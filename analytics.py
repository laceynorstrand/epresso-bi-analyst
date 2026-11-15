"""Business logic. Keep Streamlit code OUT of this file so every function can be
tested on its own and reused by the assistant."""
from __future__ import annotations

import calendar
import io
from dataclasses import asdict, dataclass, field

import pandas as pd

EXCEL_EXT = ('.xlsx', '.xlsm', '.xls')
TEXT_EXT = ('.csv', '.txt', '.tsv')


# ---------------------------------------------------------------- loading
def excel_sheet_names(file_bytes: bytes) -> list[str]:
    return pd.ExcelFile(io.BytesIO(file_bytes)).sheet_names


def load_table(file_bytes: bytes, filename: str, sheet_name=0) -> pd.DataFrame:
    """Read an uploaded CSV / TXT / TSV / Excel file into a DataFrame."""
    name = filename.lower()
    buffer = io.BytesIO(file_bytes)
    if name.endswith(EXCEL_EXT):
        return pd.read_excel(buffer, sheet_name=sheet_name)
    if name.endswith(TEXT_EXT):
        return pd.read_csv(buffer, sep=None, engine='python')  # sniffs the delimiter
    raise ValueError(f'Unsupported file type: {filename}')


def profile_table(df: pd.DataFrame) -> dict:
    """Basic profile: size, duplicates, missing values, data types."""
    missing = df.isna().sum()
    return {
        'rows': len(df),
        'columns': df.shape[1],
        'duplicate_rows': int(df.duplicated().sum()),
        'missing': pd.DataFrame({
            'column': missing.index,
            'missing': missing.values,
            'pct_missing': (missing.values / max(len(df), 1) * 100).round(2),
        }),
        'dtypes': pd.DataFrame({'column': df.columns, 'dtype': df.dtypes.astype(str).values}),
    }


# ---------------------------------------------------------- tool contract
@dataclass
class ToolResult:
    """Every analytical function returns this. The assistant may only narrate it.
    Keep `value` to plain Python types (numbers, strings, dicts, lists)."""
    tool: str
    value: object
    definition: str
    scope: dict
    rows_used: int
    caveats: list = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _error(tool: str, message: str, scope: dict | None = None) -> ToolResult:
    return ToolResult(tool=tool, value=None, definition='', scope=scope or {},
                      rows_used=0, caveats=[], error=message)


def _apply_filters(df: pd.DataFrame, filters: dict | None):
    """Validate filters against the LOADED data, then apply them."""
    for col, wanted in (filters or {}).items():
        if col not in df.columns:
            return df, f'Unknown column: {col}'
        wanted_list = list(wanted) if isinstance(wanted, (list, tuple, set)) else [wanted]
        valid = set(df[col].dropna().unique())
        bad = [w for w in wanted_list if w not in valid]
        if bad:
            examples = sorted(map(str, valid))[:20]
            return df, f'{bad} not found in column {col}. Valid values include: {examples}'
        df = df[df[col].isin(wanted_list)]
    return df, None


# ------------------------------------------------------ analytical tools
def get_total(df: pd.DataFrame, value_col: str, filters: dict | None = None) -> ToolResult:
    """Sum of one numeric column, optionally filtered. EXAMPLE tool: replace the
    generic column names with your own definition of revenue."""
    name = 'get_total'
    if value_col not in df.columns:
        return _error(name, f'Unknown column: {value_col}')
    subset, err = _apply_filters(df, filters)
    if err:
        return _error(name, err, {'filters': filters})
    values = pd.to_numeric(subset[value_col], errors='coerce')
    n_bad = int(values.isna().sum())
    caveats = ['This is a sum of the chosen column. Revenue is not profit.']
    if n_bad:
        caveats.append(f'{n_bad} row(s) had a missing or non-numeric {value_col} and were excluded.')
    return ToolResult(tool=name, value=float(values.sum()), definition=f'Sum of {value_col}',
                      scope={'filters': filters or 'none (all rows)'},
                      rows_used=int(values.notna().sum()), caveats=caveats)


def group_total(df: pd.DataFrame, value_col: str, group_col: str,
                filters: dict | None = None) -> ToolResult:
    """Sum of one numeric column by group (store, channel, year, ...)."""
    name = 'group_total'
    for col in (value_col, group_col):
        if col not in df.columns:
            return _error(name, f'Unknown column: {col}')
    subset, err = _apply_filters(df, filters)
    if err:
        return _error(name, err, {'filters': filters})
    work = subset.assign(**{value_col: pd.to_numeric(subset[value_col], errors='coerce')})
    n_bad = int(work[value_col].isna().sum())
    n_nogroup = int(work[group_col].isna().sum())
    grouped = work.dropna(subset=[value_col, group_col]).groupby(group_col)[value_col].sum()
    caveats = ['Sums of the chosen column. Revenue is not profit.']
    if n_bad:
        caveats.append(f'{n_bad} row(s) with missing or non-numeric {value_col} were excluded.')
    if n_nogroup:
        caveats.append(f'{n_nogroup} row(s) with a missing {group_col} were excluded.')
    return ToolResult(tool=name, value={str(k): float(v) for k, v in grouped.items()},
                      definition=f'Sum of {value_col} by {group_col}',
                      scope={'filters': filters or 'none (all rows)'},
                      rows_used=int(len(work) - n_bad - n_nogroup), caveats=caveats)


def data_quality_report(df: pd.DataFrame) -> ToolResult:
    """Row count, duplicate rows, and missing values per column."""
    missing = {c: int(n) for c, n in df.isna().sum().items() if n > 0}
    caveats = []
    if missing:
        caveats.append('Missing values may limit conclusions drawn from those columns.')
    return ToolResult(tool='data_quality_report',
                      value={'rows': len(df), 'duplicate_rows': int(df.duplicated().sum()),
                             'columns_with_missing': missing},
                      definition='Counts of duplicate rows and missing values in the loaded table',
                      scope={'filters': 'none (full table)'}, rows_used=len(df), caveats=caveats)


# ------------------------------------------------------------ merge check
def check_merge(left: pd.DataFrame, right: pd.DataFrame, on, value_col: str, how: str = 'left'):
    """Merge, and report whether the join changed the row count or the revenue sum.
    `value_col` must be a numeric column in `left`. Returns (merged, report)."""
    before_rows = len(left)
    before_sum = float(pd.to_numeric(left[value_col], errors='coerce').sum())
    merged = left.merge(right, on=on, how=how)
    after_rows = len(merged)
    after_sum = float(pd.to_numeric(merged[value_col], errors='coerce').sum())
    fan_out = after_rows > before_rows or abs(after_sum - before_sum) > 1e-9
    report = {
        'rows_before': before_rows, 'rows_after': after_rows,
        'sum_before': before_sum, 'sum_after': after_sum,
        'changed': fan_out,
        'warning': ('The join changed the row count or the total. Check the grain and keys '
                    'of both tables before using this result.') if fan_out else '',
    }
    return merged, report


# ------------------------------------------------ E-Presso data model
# Each table is recognized by the columns it has (not by its file name),
# so an updated file with a different name still works.
TABLE_SIGNATURES = {
    'sales': {'Sales ID', 'Date', 'Store ID', 'Revenue'},
    'stores': {'ID', 'StoreType'},
    'products': {'Product ID', 'Product'},
    'categories': {'Category ID', 'Category'},
    'survey': {'Survey ID', 'Satisfaction'},
}


def identify_table(df: pd.DataFrame):
    """Return 'sales', 'stores', ... based on the columns, or None."""
    cols = set(df.columns)
    for name, needed in TABLE_SIGNATURES.items():
        if needed.issubset(cols):
            return name
    return None


def build_model(tables: dict):
    """Join Sales (the main table) to its lookup tables one at a time, checking
    after each join that revenue was not duplicated. The survey is NOT joined
    here: it is yearly while Sales is monthly, so joining would repeat revenue.
    Returns (model, report)."""
    if 'sales' not in tables:
        return None, {'steps': [], 'problems': ['No Sales table found. Please upload the Sales file.']}

    model = tables['sales'].copy()
    model['Revenue'] = pd.to_numeric(model['Revenue'], errors='coerce')
    model['Date'] = pd.to_datetime(model['Date'], errors='coerce')
    model['Year'] = model['Date'].dt.year
    model['Month'] = model['Date'].dt.month

    steps, problems = [], []
    bad_revenue = int(model['Revenue'].isna().sum())
    bad_dates = int(model['Date'].isna().sum())
    if bad_revenue:
        problems.append(f'{bad_revenue} Sales row(s) have a missing or non-numeric Revenue.')
    if bad_dates:
        problems.append(f'{bad_dates} Sales row(s) have a missing or invalid Date.')

    # (table name, key in Sales, key in lookup table, a label column to test matches)
    joins = [
        ('stores', 'Store ID', 'ID', 'StoreType'),
        ('products', 'Product ID', 'Product ID', 'Product'),
        ('categories', 'Category ID', 'Category ID', 'Category'),
    ]
    for name, sales_key, lookup_key, label_col in joins:
        if name not in tables:
            problems.append(f'No {name} table uploaded, so its columns are missing.')
            continue
        lookup = tables[name].rename(columns={lookup_key: sales_key})
        dup_keys = int(lookup[sales_key].duplicated().sum())
        if dup_keys:
            problems.append(f'{name}: {dup_keys} duplicate key(s). Joining would duplicate '
                            f'revenue, so this table was skipped.')
            continue
        model, report = check_merge(model, lookup, on=sales_key, value_col='Revenue')
        step = {'table': name, **report}
        step['unmatched_rows'] = int(model[label_col].isna().sum())
        steps.append(step)
        if step['unmatched_rows']:
            problems.append(f"{step['unmatched_rows']} Sales row(s) had no match in {name}.")
        if report['changed']:
            problems.append(f'{name}: {report["warning"]}')
    return model, {'steps': steps, 'problems': problems}


# =====================================================================
# E-Presso analytical tools (the ONLY calculations the assistant may use)
# Every tool takes the joined model `df` from build_model and returns a ToolResult.
# =====================================================================
def _need(df: pd.DataFrame, tool: str, columns: list):
    """Return an error ToolResult if the loaded data lacks a needed column."""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        return _error(tool, f'The loaded data is missing column(s) {missing}. '
                            f'Upload the file(s) that contain them.')
    return None


def _years(df: pd.DataFrame) -> list:
    return sorted(int(y) for y in df['Year'].dropna().unique())


def _pct(new, old):
    """Percent change from old to new, or None when old is zero or missing."""
    if old is None or pd.isna(old) or old == 0:
        return None
    return round(float((new - old) / old * 100), 2)


def get_revenue_by_channel(df: pd.DataFrame, year: int | None = None) -> ToolResult:
    """Website vs. Brick & Mortar revenue, optionally for one year."""
    name = 'get_revenue_by_channel'
    err = _need(df, name, ['Revenue', 'StoreType', 'Year'])
    if err:
        return err
    subset = df
    if year is not None:
        year = int(year)
        subset = df[df['Year'] == year]
        if subset.empty:
            return _error(name, f'No sales rows found for {year}. Years in the data: {_years(df)}',
                          {'year': year})
    clean = subset.dropna(subset=['Revenue', 'StoreType'])
    by = clean.groupby('StoreType')['Revenue'].sum()
    total = float(by.sum())
    value = {str(k): {'revenue': float(v),
                      'share_pct': round(float(v) / total * 100, 2) if total else None}
             for k, v in by.items()}
    caveats = ['Revenue is not profit. No cost data is available.']
    excluded = len(subset) - len(clean)
    if excluded:
        caveats.append(f'{excluded} row(s) with missing revenue or sales channel were excluded.')
    return ToolResult(tool=name, value=value,
                      definition='Sum of Revenue by StoreType. share_pct = channel revenue / total revenue in scope.',
                      scope={'year': year if year is not None else 'all years',
                             'years_in_data': _years(df)},
                      rows_used=len(clean), caveats=caveats)


def get_revenue_by_year(df: pd.DataFrame, channel: str | None = None) -> ToolResult:
    """Revenue for each year, with the change from the prior year."""
    name = 'get_revenue_by_year'
    needed = ['Revenue', 'Year'] + (['StoreType'] if channel else [])
    err = _need(df, name, needed)
    if err:
        return err
    subset = df
    if channel:
        valid = sorted(map(str, df['StoreType'].dropna().unique()))
        if channel not in valid:
            return _error(name, f'Unknown channel {channel!r}. Valid channels: {valid}')
        subset = df[df['StoreType'] == channel]
    clean = subset.dropna(subset=['Revenue', 'Year'])
    by = clean.groupby('Year')['Revenue'].sum().sort_index()
    rows, prior = [], None
    for y, v in by.items():
        rows.append({'year': int(y), 'revenue': float(v), 'change_vs_prior_year_pct': _pct(v, prior)})
        prior = v
    first_last = _pct(by.iloc[-1], by.iloc[0]) if len(by) > 1 else None
    return ToolResult(tool=name, value={'by_year': rows, 'first_to_last_year_pct': first_last},
                      definition='Sum of Revenue by calendar year of the sale date.',
                      scope={'channel': channel or 'all channels'},
                      rows_used=len(clean),
                      caveats=['Revenue is not profit. No cost data is available.'])


def get_channel_growth(df: pd.DataFrame) -> ToolResult:
    """Revenue growth for each sales channel from the first to the last year."""
    name = 'get_channel_growth'
    err = _need(df, name, ['Revenue', 'StoreType', 'Year'])
    if err:
        return err
    clean = df.dropna(subset=['Revenue', 'StoreType', 'Year'])
    pivot = clean.groupby(['StoreType', 'Year'])['Revenue'].sum().unstack('Year').fillna(0)
    years = sorted(int(y) for y in pivot.columns)
    if len(years) < 2:
        return _error(name, 'At least two years of data are needed to measure growth.')
    first, last = years[0], years[-1]
    channels = {}
    for ch, row in pivot.iterrows():
        a, b = float(row[first]), float(row[last])
        channels[str(ch)] = {'first_year_revenue': a, 'last_year_revenue': b,
                             'dollar_change': b - a, 'pct_change': _pct(b, a)}
    with_pct = {k: v for k, v in channels.items() if v['pct_change'] is not None}
    best_pct = max(with_pct, key=lambda k: with_pct[k]['pct_change']) if with_pct else None
    best_dollar = max(channels, key=lambda k: channels[k]['dollar_change'])
    return ToolResult(tool=name,
                      value={'first_year': first, 'last_year': last, 'channels': channels,
                             'greatest_pct_growth': best_pct, 'greatest_dollar_growth': best_dollar},
                      definition='Change in annual revenue per channel between the first and last year in the data.',
                      scope={'years': f'{first} to {last}', 'channel': 'all channels'},
                      rows_used=len(clean),
                      caveats=['Growth compares only two years and ignores the years between them.',
                               'Revenue is not profit. No cost data is available.'])


def get_store_trend(df: pd.DataFrame) -> ToolResult:
    """Revenue change per location (store or website) from the first to the last year."""
    name = 'get_store_trend'
    err = _need(df, name, ['Revenue', 'Year', 'Store ID'])
    if err:
        return err
    d = df.dropna(subset=['Revenue', 'Year']).copy()
    city = d['City'].astype('object').where(d['City'].notna(), 'Website') if 'City' in d.columns else 'Store'
    d['Location'] = d['Store ID'].astype(str) + ' - ' + city
    pivot = d.groupby(['Location', 'Year'])['Revenue'].sum().unstack('Year').fillna(0)
    years = sorted(int(y) for y in pivot.columns)
    if len(years) < 2:
        return _error(name, 'At least two years of data are needed to measure a trend.')
    first, last = years[0], years[-1]
    rows = []
    for loc, row in pivot.iterrows():
        a, b = float(row[first]), float(row[last])
        rows.append({'location': str(loc), 'first_year_revenue': a, 'last_year_revenue': b,
                     'dollar_change': b - a, 'pct_change': _pct(b, a)})
    rows.sort(key=lambda r: (r['pct_change'] is None, r['pct_change']))
    declined = [r['location'] for r in rows if r['pct_change'] is not None and r['pct_change'] < 0]
    return ToolResult(tool=name,
                      value={'first_year': first, 'last_year': last,
                             'locations_sorted_lowest_growth_first': rows,
                             'locations_that_declined': declined},
                      definition='Change in annual revenue per location between the first and last year, sorted from lowest to highest percent change.',
                      scope={'years': f'{first} to {last}', 'locations': len(rows)},
                      rows_used=len(d),
                      caveats=['Compares only the first and last year.',
                               'Revenue trends do not show store profitability. Costs, leases and staffing are not in the data.'])


def get_category_performance(df: pd.DataFrame, year: int | None = None) -> ToolResult:
    """Revenue and share of total by product category."""
    name = 'get_category_performance'
    err = _need(df, name, ['Revenue', 'Category', 'Year'])
    if err:
        return err
    subset = df
    if year is not None:
        year = int(year)
        subset = df[df['Year'] == year]
        if subset.empty:
            return _error(name, f'No sales rows found for {year}. Years in the data: {_years(df)}',
                          {'year': year})
    clean = subset.dropna(subset=['Revenue', 'Category'])
    by = clean.groupby('Category')['Revenue'].sum().sort_values(ascending=False)
    total = float(by.sum())
    value = {str(k): {'revenue': float(v),
                      'share_pct': round(float(v) / total * 100, 2) if total else None}
             for k, v in by.items()}
    return ToolResult(tool=name, value=value,
                      definition='Sum of Revenue by product category. share_pct = category revenue / total revenue in scope.',
                      scope={'year': year if year is not None else 'all years'},
                      rows_used=len(clean),
                      caveats=['Revenue is not profit. Categories may have very different costs and margins.'])


def get_monthly_revenue_share(df: pd.DataFrame, year: int | None = None) -> ToolResult:
    """Each month's revenue as a percentage of the annual total."""
    name = 'get_monthly_revenue_share'
    err = _need(df, name, ['Revenue', 'Year', 'Month'])
    if err:
        return err
    d = df.dropna(subset=['Revenue', 'Year', 'Month'])
    if year is not None:
        year = int(year)
        d = d[d['Year'] == year]
        if d.empty:
            return _error(name, f'No sales rows found for {year}. Years in the data: {_years(df)}',
                          {'year': year})
        by = d.groupby('Month')['Revenue'].sum()
        share = by / by.sum() * 100
        basis = f'Each month as a percentage of {year} total revenue.'
        extra = {}
    else:
        per = d.groupby(['Year', 'Month'])['Revenue'].sum().unstack('Month').fillna(0)
        share = per.div(per.sum(axis=1), axis=0).mean(axis=0) * 100
        grand = d.groupby('Month')['Revenue'].sum()
        grand_share = grand / grand.sum() * 100
        basis = ("Each month as a percentage of ITS OWN YEAR's revenue, averaged across the years.")
        extra = {'pct_of_five_year_total': {calendar.month_name[int(m)]: round(float(s), 2)
                                           for m, s in grand_share.items()}}
    share_named = {calendar.month_name[int(m)]: round(float(s), 2) for m, s in share.items()}
    peak = max(share_named, key=share_named.get)
    low = min(share_named, key=share_named.get)
    value = {'basis': basis, 'share_pct': share_named, 'peak_month': peak, 'lowest_month': low, **extra}
    return ToolResult(tool=name, value=value, definition=basis,
                      scope={'year': year if year is not None else 'all years'},
                      rows_used=len(d),
                      caveats=['Monthly share shows seasonality, not profitability.'])


def get_satisfaction_association(df: pd.DataFrame, tables: dict) -> ToolResult:
    """Correlation between store-year customer satisfaction and store-year revenue."""
    name = 'get_satisfaction_association'
    survey = tables.get('survey') if tables else None
    if survey is None:
        return _error(name, 'No survey table is loaded, so satisfaction cannot be analyzed.')
    err = _need(df, name, ['Revenue', 'StoreYear'])
    if err:
        return err
    rev = df.groupby('StoreYear')['Revenue'].sum().rename('Revenue').reset_index()
    merged = survey.merge(rev, left_on='Survey ID', right_on='StoreYear', how='inner')
    merged['Satisfaction'] = pd.to_numeric(merged['Satisfaction'], errors='coerce')
    usable = merged.dropna(subset=['Satisfaction', 'Revenue'])
    n_missing = int(merged['Satisfaction'].isna().sum())
    if len(usable) < 8:
        return _error(name, f'Only {len(usable)} store-years have both satisfaction and revenue. '
                            f'That is too few for a reliable comparison.')
    r = float(usable['Satisfaction'].corr(usable['Revenue']))
    value = {'store_years_compared': int(len(usable)),
             'store_years_missing_satisfaction': n_missing,
             'correlation': round(r, 3),
             'average_satisfaction': round(float(usable['Satisfaction'].mean()), 2)}
    return ToolResult(tool=name, value=value,
                      definition='Pearson correlation between yearly satisfaction score and yearly revenue, one point per store-year.',
                      scope={'unit': 'store-years with a satisfaction score'},
                      rows_used=int(len(usable)),
                      caveats=['Correlation is an association. It does not show that satisfaction causes revenue, or the reverse.',
                               'Larger stores may differ in both satisfaction and revenue, which this simple comparison does not control for.',
                               'The Website has no satisfaction scores, so it is not included.',
                               'Survey responses may not represent all customers.'])


def get_data_quality_report(tables: dict, df: pd.DataFrame | None = None) -> ToolResult:
    """Rows, duplicates and missing values for every loaded table."""
    name = 'get_data_quality_report'
    if not tables:
        return _error(name, 'No tables are loaded.')
    value = {}
    for kind, t in tables.items():
        missing = {c: int(n) for c, n in t.isna().sum().items() if n > 0}
        value[kind] = {'rows': int(len(t)), 'duplicate_rows': int(t.duplicated().sum()),
                       'columns_with_missing_values': missing}
    caveats = []
    if 'sales' in tables and 'Sales ID' in tables['sales'].columns:
        dup_ids = int(tables['sales']['Sales ID'].duplicated().sum())
        value['sales']['duplicate_sales_ids'] = dup_ids
    if df is not None and 'Revenue' in df.columns:
        value['sales']['missing_or_non_numeric_revenue'] = int(df['Revenue'].isna().sum())
    if any(v['columns_with_missing_values'] for v in value.values()):
        caveats.append('Missing values limit conclusions drawn from those columns.')
    return ToolResult(tool=name, value=value,
                      definition='Row counts, duplicate rows and missing values for each loaded table.',
                      scope={'tables': list(tables)},
                      rows_used=sum(v['rows'] for v in value.values()), caveats=caveats)
