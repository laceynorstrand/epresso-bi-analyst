"""E-Presso BI Analyst. Calculations live in analytics.py, the assistant in agent.py."""
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import agent
import analytics

MAX_QUESTIONS = 15  # per-session limit for the assistant
DATA_DIR = Path(__file__).parent / 'data'

st.set_page_config(page_title='E-Presso BI Analyst', layout='wide')
st.title('E-Presso BI Analyst')
st.caption('Interactive business intelligence dashboard and analytical assistant for E-Presso.')


@st.cache_data(show_spinner=False)
def cached_load(file_bytes: bytes, filename: str, sheet_name):
    # The cache is keyed on the file CONTENTS, so a modified file is never
    # confused with the original one.
    return analytics.load_table(file_bytes, filename, sheet_name)


@st.cache_data(show_spinner=False)
def load_bundled():
    """Read the sample E-Presso files stored in the repo's data/ folder."""
    found = {}
    if DATA_DIR.exists():
        for path in sorted(DATA_DIR.iterdir()):
            if path.suffix.lower() in analytics.TEXT_EXT + analytics.EXCEL_EXT:
                try:
                    table = analytics.load_table(path.read_bytes(), path.name, 0)
                except Exception:
                    continue
                kind = analytics.identify_table(table)
                if kind and kind not in found:
                    found[kind] = (path.name, table)
    return found


# ------------------------------------------------------------------ data
bundled = load_bundled()
with st.sidebar:
    st.header('Data')
    st.caption('The app starts with the bundled E-Presso sample data. Upload files to replace '
               'any table (for example an updated Sales file). Tables are recognized by their columns.')
    uploaded_files = st.file_uploader(
        'Upload E-Presso files (CSV or Excel)',
        type=['csv', 'txt', 'tsv', 'xlsx', 'xls'],
        accept_multiple_files=True,
    )

tables = {kind: table for kind, (_, table) in bundled.items()}
source = {kind: f'bundled sample ({fname})' for kind, (fname, _) in bundled.items()}
unrecognized = []
for f in uploaded_files or []:
    try:
        table = cached_load(f.getvalue(), f.name, 0)
    except Exception as exc:  # show a readable message instead of a traceback
        st.sidebar.error(f'Could not read {f.name}: {exc}')
        continue
    kind = analytics.identify_table(table)
    if kind is None:
        unrecognized.append(f.name)
    else:
        tables[kind] = table
        source[kind] = f'UPLOADED ({f.name})'

st.sidebar.write('**Tables in use:**')
for kind, table in tables.items():
    st.sidebar.write(f'{kind}: {len(table):,} rows, from {source[kind]}')
for name in unrecognized:
    st.sidebar.warning(f'Not recognized: {name}')

if 'sales' not in tables:
    st.info('No Sales data found. Upload the Sales file in the sidebar to begin.')
    st.stop()

df, model_report = analytics.build_model(tables)
if df is None:
    st.error(model_report['problems'][0])
    st.stop()

tab_data, tab_dash, tab_assistant = st.tabs(['Data and profile', 'Dashboard', 'Assistant'])

# --------------------------------------------------------- data and profile
with tab_data:
    choice = st.selectbox('Table to inspect', list(tables.keys()))
    profile = analytics.profile_table(tables[choice])
    c1, c2, c3 = st.columns(3)
    c1.metric('Rows', f"{profile['rows']:,}")
    c2.metric('Columns', profile['columns'])
    c3.metric('Duplicate rows', profile['duplicate_rows'])
    st.subheader('Preview')
    st.dataframe(tables[choice].head(50))
    left, right = st.columns(2)
    left.subheader('Missing values')
    left.dataframe(profile['missing'])
    right.subheader('Data types')
    right.dataframe(profile['dtypes'])

    st.subheader('Table relationships and grain')
    st.markdown(
        '- **Sales** (one row = one product\'s revenue for one month at one location) is the main table.\n'
        '- Sales `Store ID` links to Stores `ID`; `Product ID` to Products; `Category ID` to Categories.\n'
        '- **Survey** (one row = one satisfaction score for one store in one year) links through '
        '`StoreYear` = `Survey ID`. It is yearly while Sales is monthly, so it is not joined into the revenue '
        'table, to avoid repeating revenue.')

    st.subheader('Join checks')
    st.caption('After each join, rows and revenue should NOT change. If they do, a join duplicated revenue.')
    if model_report['steps']:
        st.dataframe(pd.DataFrame(model_report['steps']))
    if model_report['problems']:
        for problem in model_report['problems']:
            st.warning(problem)
    else:
        st.success('No data problems found in the joins.')

    quality = analytics.get_data_quality_report(tables, df)
    with st.expander('Data quality report (all tables)'):
        st.json(quality.value)

# ---------------------------------------------------------------- dashboard
with tab_dash:
    st.subheader('Filters')
    f1, f2, f3 = st.columns(3)

    years = sorted(int(y) for y in df['Year'].dropna().unique())
    sel_years = f1.multiselect('Year', years, default=years)

    cats = sorted(df['Category'].dropna().unique()) if 'Category' in df.columns else []
    sel_cats = f2.multiselect('Product category', cats, default=cats) if cats else []

    types = sorted(df['StoreType'].dropna().unique()) if 'StoreType' in df.columns else []
    sel_types = f3.multiselect('Sales channel', types, default=types) if types else []

    # Apply filters. A filter only removes rows when something is deselected.
    mask = df['Year'].isin(sel_years)
    if cats and len(sel_cats) < len(cats):
        mask &= df['Category'].isin(sel_cats)
    if types and len(sel_types) < len(types):
        mask &= df['StoreType'].isin(sel_types)
    filtered = df[mask]

    if len(filtered) < len(df):
        st.caption(f'Showing a FILTERED subset: {len(filtered):,} of {len(df):,} sales rows.')
    else:
        st.caption('Showing ALL data (no filters removing rows).')

    if filtered.empty:
        st.warning('No rows match these filters. Select at least one value in each filter.')
    else:
        total = analytics.get_total(filtered, 'Revenue')
        k1, k2, k3 = st.columns(3)
        k1.metric('Total revenue', f'${total.value:,.0f}')
        k2.metric('Sales rows used', f'{total.rows_used:,}')
        k3.metric('Period', f"{filtered['Date'].min():%b %Y} to {filtered['Date'].max():%b %Y}")

        by_channel = analytics.get_revenue_by_channel(filtered)
        by_year_tool = analytics.get_revenue_by_year(filtered)
        k4, k5 = st.columns(2)
        if not by_channel.error and 'Website' in by_channel.value:
            k4.metric('Website share of revenue', f"{by_channel.value['Website']['share_pct']:.1f}%")
        growth = None if by_year_tool.error else by_year_tool.value['first_to_last_year_pct']
        if growth is not None:
            k5.metric('Revenue change, first to last year shown', f'{growth:+.1f}%')
        st.caption(f'{total.definition}. Revenue is not profit. Metrics above use the filtered data.')

        def to_frame(result, label, numeric_sort=False):
            if result.error:
                st.error(result.error)
                return None
            items = list(result.value.items())
            if numeric_sort:
                items.sort(key=lambda kv: int(kv[0]))
            return pd.DataFrame(items, columns=[label, 'Revenue'])

        c1, c2 = st.columns(2)
        by_year = to_frame(analytics.group_total(filtered, 'Revenue', 'Year'), 'Year', True)
        if by_year is not None:
            fig = px.bar(by_year, x='Year', y='Revenue', title='Revenue by year ($)')
            fig.update_xaxes(type='category')
            c1.plotly_chart(fig, use_container_width=True)

        by_month = to_frame(analytics.group_total(filtered, 'Revenue', 'Month'), 'Month', True)
        if by_month is not None:
            fig = px.line(by_month, x='Month', y='Revenue', markers=True,
                          title='Revenue by month, summed across selected years ($)')
            fig.update_xaxes(dtick=1)
            c2.plotly_chart(fig, use_container_width=True)

        c3, c4 = st.columns(2)
        if types:
            by_type = to_frame(analytics.group_total(filtered, 'Revenue', 'StoreType'), 'Sales channel')
            if by_type is not None:
                c3.plotly_chart(px.bar(by_type, x='Sales channel', y='Revenue',
                                       title='Revenue by sales channel ($)'),
                                use_container_width=True)
        if cats:
            by_cat = to_frame(analytics.group_total(filtered, 'Revenue', 'Category'), 'Category')
            if by_cat is not None:
                c4.plotly_chart(px.bar(by_cat, x='Category', y='Revenue',
                                       title='Revenue by product category ($)'),
                                use_container_width=True)

# ---------------------------------------------------------------- assistant
with tab_assistant:
    st.caption('Mode: RULES-BASED assistant. It matches your question to an approved analytical function, '
               'runs it on the currently loaded data, and reports the result. It is not a language model. '
               'It answers about the full dataset; dashboard filters do not apply here.')
    st.session_state.setdefault('n_questions', 0)
    st.session_state.setdefault('history', [])
    st.caption(f"Questions used this session: {st.session_state['n_questions']} of {MAX_QUESTIONS}")
    for item in st.session_state['history']:
        with st.chat_message('user'):
            st.write(item['question'])
        with st.chat_message('assistant'):
            ans = item['answer']
            st.markdown(f"**Scope:** {ans['scope']}")
            st.markdown(f"**Findings (calculated):**\n\n{ans['findings']}")
            if ans['interpretation']:
                st.markdown(f"**Interpretation (not a calculated fact):** {ans['interpretation']}")
            if ans.get('limitations'):
                st.markdown('**Limitations:**\n\n' + '\n'.join(f'- {c}' for c in ans['limitations']))
            with st.expander('Tool-call log'):
                st.json(ans['tool_log'])
    question = st.chat_input('Ask a question about the loaded data')
    if question:
        if st.session_state['n_questions'] >= MAX_QUESTIONS:
            st.warning('Question limit reached for this session. Reload the page to start over.')
        else:
            st.session_state['n_questions'] += 1
            answer = agent.answer_question(question, df, mode='rules', tables=tables)
            st.session_state['history'].append({'question': question, 'answer': answer})
            st.rerun()
