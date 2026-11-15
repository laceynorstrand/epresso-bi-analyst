"""E-Presso analytical assistant.

HOW IT WORKS (rules-based mode)
  1. route()        reads the question and picks ONE approved analytical function.
  2. run_tool()     runs that function on the data currently loaded in the app.
  3. a formatter    turns the function's raw result into plain English.
Every number in an answer comes from a tool result. The assistant never runs
generated code and never changes the data.
"""
from __future__ import annotations

import re

import analytics

# ----------------------------------------------------------- approved tools
# The ONLY functions the assistant may call, and the arguments each may receive.
ALLOWED_ARGS = {
    'get_revenue_by_channel': {'year'},
    'get_revenue_by_year': {'channel'},
    'get_channel_growth': set(),
    'get_store_trend': set(),
    'get_category_performance': {'year'},
    'get_monthly_revenue_share': {'year'},
    'get_satisfaction_association': set(),
    'get_data_quality_report': set(),
}

SUPPORTED_QUESTIONS = [
    'How much revenue did the website generate in 2028?',
    'Which sales channel experienced the greatest revenue growth?',
    'Which store experienced the greatest percentage revenue decline?',
    'How does revenue differ by year?',
    'Which product category earns the most revenue?',
    'Which months are the busiest, as a share of annual revenue?',
    'Is customer satisfaction related to revenue?',
    'Are there missing values or duplicates in the data?',
]


def run_tool(name: str, args: dict, df, tables: dict | None):
    """Run one approved tool. Anything not on the approved list is refused."""
    if name not in ALLOWED_ARGS:
        return analytics._error(name, f'{name} is not an approved analytical function.')
    unexpected = set(args) - ALLOWED_ARGS[name]
    if unexpected:
        return analytics._error(name, f'Argument(s) {sorted(unexpected)} are not allowed for {name}.')
    if name == 'get_satisfaction_association':
        return analytics.get_satisfaction_association(df, tables or {})
    if name == 'get_data_quality_report':
        return analytics.get_data_quality_report(tables or {}, df)
    return getattr(analytics, name)(df, **args)


# ------------------------------------------------------------------ router
def _has(q: str, *words: str) -> bool:
    return any(w in q for w in words)


def route(question: str, df) -> dict:
    """Pick a tool for the question. Returns {'tool', 'args', 'topic'} or {'tool': None}."""
    q = question.lower()
    if re.search(r'\b(delete|remove|drop|erase|overwrite|modify|edit|update|insert|rewrite|change the data|'
                 r'set the|add a row|add row|run code|execute|python|sql)\b', q):
        return {'tool': None, 'args': {}, 'topic': 'readonly'}
    years = [int(y) for y in re.findall(r'\b(20[2-3]\d)\b', q)]
    year = years[0] if years else None
    cities = []
    if 'City' in df.columns:
        cities = [str(c).lower() for c in df['City'].dropna().unique()]
    named_city = any(c in q for c in cities)

    if _has(q, 'profit', 'margin', 'expense', 'lease', 'overhead', 'worth keeping', 'shut down',
            'close ', 'closing', 'closure', 'should e-presso', 'should we'):
        if _has(q, 'store', 'location', 'close', 'closing', 'closure', 'shut') or named_city:
            return {'tool': 'get_store_trend', 'args': {}, 'topic': 'judgment'}
        return {'tool': 'get_revenue_by_channel', 'args': {}, 'topic': 'judgment'}

    if _has(q, 'satisf', 'survey', 'customer'):
        causal = _has(q, 'cause', 'causal', 'because', 'drive', 'driving', 'lead to', 'leads to',
                      'impact', 'effect', 'result in', 'increase revenue')
        return {'tool': 'get_satisfaction_association', 'args': {},
                'topic': 'causation' if causal else 'association'}

    if _has(q, 'missing', 'duplicate', 'data quality', 'quality', 'blank', 'null'):
        return {'tool': 'get_data_quality_report', 'args': {}, 'topic': 'quality'}

    if _has(q, 'month', 'season', 'peak', 'busiest', 'holiday'):
        return {'tool': 'get_monthly_revenue_share', 'args': {'year': year} if year else {},
                'topic': 'monthly'}

    channel_words = _has(q, 'channel', 'website', 'online', 'brick', 'mortar')
    if channel_words and _has(q, 'grew', 'growth', 'grow', 'fastest', 'increase', 'declin',
                              'decreas', 'drop'):
        return {'tool': 'get_channel_growth', 'args': {}, 'topic': 'channel_growth'}

    if _has(q, 'store', 'location', 'city', 'declin', 'decreas') or named_city:
        return {'tool': 'get_store_trend', 'args': {}, 'topic': 'store'}

    if _has(q, 'category', 'categories', 'product', 'bean', 'syrup', 'frother', 'espresso machine',
            'accessor'):
        return {'tool': 'get_category_performance', 'args': {'year': year} if year else {},
                'topic': 'category'}

    if channel_words:
        return {'tool': 'get_revenue_by_channel', 'args': {'year': year} if year else {},
                'topic': 'channel'}

    if _has(q, 'year', 'annual', 'trend', 'total', 'revenue', 'sales', 'overall', 'how much'):
        return {'tool': 'get_revenue_by_year', 'args': {}, 'topic': 'year'}

    return {'tool': None, 'args': {}, 'topic': 'unsupported'}


# --------------------------------------------------------------- formatting
def _money(x) -> str:
    return f'${x:,.0f}'


def _pct(x) -> str:
    return 'n/a' if x is None else f'{x:+.1f}%'


def _fmt_channel(res, q):
    v = res.value
    year = res.scope.get('year')
    where = f'in {year}' if year != 'all years' else 'across all years'
    lines = [f'- {ch}: {_money(d["revenue"])} ({d["share_pct"]:.1f}% of revenue {where})'
             for ch, d in v.items()]
    lead = ''
    for ch, d in v.items():
        key = 'website' if ch.lower() == 'website' else 'brick'
        if key in q.lower():
            lead = f'{ch} revenue {where} was {_money(d["revenue"])}.\n\n'
    top = max(v, key=lambda k: v[k]['revenue'])
    interp = f'{top} brought in the larger share of revenue {where}. Revenue is not profit, so this does not show which channel is more profitable.'
    return lead + '\n'.join(lines), interp


def _fmt_year(res, q):
    rows = res.value['by_year']
    lines = [f'- {r["year"]}: {_money(r["revenue"])} ({_pct(r["change_vs_prior_year_pct"])} vs. prior year)'
             if r['change_vs_prior_year_pct'] is not None else f'- {r["year"]}: {_money(r["revenue"])}'
             for r in rows]
    fl = res.value['first_to_last_year_pct']
    tail = f'\n\nFirst year to last year: {_pct(fl)}.' if fl is not None else ''
    interp = ''
    if fl is not None:
        interp = ('Revenue rose over the period.' if fl > 0 else 'Revenue fell over the period.') + \
                 ' This shows direction only, not the reasons or profitability.'
    return '\n'.join(lines) + tail, interp


def _fmt_channel_growth(res, q):
    v = res.value
    lines = [f'- {ch}: {_money(d["first_year_revenue"])} in {v["first_year"]} to '
             f'{_money(d["last_year_revenue"])} in {v["last_year"]} '
             f'({_pct(d["pct_change"])}, {"+" if d["dollar_change"] >= 0 else "-"}{_money(abs(d["dollar_change"]))})'
             for ch, d in v['channels'].items()]
    head = (f'Greatest percentage growth: {v["greatest_pct_growth"]}. '
            f'Greatest dollar growth: {v["greatest_dollar_growth"]}.')
    interp = ''
    if v['greatest_pct_growth'] != v['greatest_dollar_growth']:
        interp = 'The channel with the biggest percentage growth is not the one that added the most dollars, so the answer depends on which measure matters.'
    else:
        interp = 'The same channel leads on both percentage and dollar growth.'
    return head + '\n\n' + '\n'.join(lines), interp


def _fmt_store(res, q):
    v = res.value
    rows = v['locations_sorted_lowest_growth_first']
    ql = q.lower()
    named = [r for r in rows if any(part and part.strip().lower() in ql
                                    for part in [r['location'].split(' - ')[-1]])]
    if named:
        lines = [f'- {r["location"]}: {_money(r["first_year_revenue"])} in {v["first_year"]} to '
                 f'{_money(r["last_year_revenue"])} in {v["last_year"]} ({_pct(r["pct_change"])})'
                 for r in named]
        lines.append(f'\nFor comparison, the median location changed by about '
                     f'{_pct(sorted(r["pct_change"] for r in rows if r["pct_change"] is not None)[len(rows) // 2])}.')
        return '\n'.join(lines), ''
    declined = v['locations_that_declined']
    if declined:
        worst = rows[0]
        head = (f'Greatest percentage decline: {worst["location"]} ({_pct(worst["pct_change"])}), from '
                f'{_money(worst["first_year_revenue"])} in {v["first_year"]} to '
                f'{_money(worst["last_year_revenue"])} in {v["last_year"]}.\n'
                f'{len(declined)} location(s) declined: {", ".join(declined)}.')
    else:
        low = rows[0]
        head = (f'No location declined between {v["first_year"]} and {v["last_year"]}. '
                f'The smallest change was {low["location"]} ({_pct(low["pct_change"])}).')
    lines = [f'- {r["location"]}: {_pct(r["pct_change"])}' for r in rows[:5]]
    return head + '\n\nLowest five by percentage change:\n' + '\n'.join(lines), ''


def _fmt_category(res, q):
    lines = [f'- {c}: {_money(d["revenue"])} ({d["share_pct"]:.1f}%)' for c, d in res.value.items()]
    top = next(iter(res.value))
    return '\n'.join(lines), f'{top} is the largest category by revenue. Revenue is not profit, and categories can have very different margins.'


def _fmt_monthly(res, q):
    v = res.value
    ranked = sorted(v['share_pct'].items(), key=lambda kv: kv[1], reverse=True)
    lines = [f'- {m}: {p:.1f}%' for m, p in ranked]
    head = f'{v["basis"]}\nPeak month: {v["peak_month"]}. Lowest month: {v["lowest_month"]}.'
    interp = f'{v["peak_month"]} is the strongest month, which suggests seasonality. This is a revenue pattern, not a profit pattern.'
    return head + '\n\n' + '\n'.join(lines), interp


def _fmt_satisfaction(res, q, topic):
    v = res.value
    r = v['correlation']
    strength = 'strong' if abs(r) >= 0.6 else 'moderate' if abs(r) >= 0.3 else 'weak'
    direction = 'positive' if r > 0 else 'negative'
    findings = (f'Across {v["store_years_compared"]} store-years with satisfaction scores, the correlation '
                f'between satisfaction and revenue is {r:+.3f} ({strength} {direction}). '
                f'Average satisfaction was {v["average_satisfaction"]}. '
                f'{v["store_years_missing_satisfaction"]} store-year(s) had no satisfaction score.')
    interp = ('This is an association, not proof of cause. Satisfaction may not drive revenue; revenue or store '
              'size may influence satisfaction, or a third factor may affect both.')
    if topic == 'causation':
        interp = 'This data cannot show that higher satisfaction CAUSES higher revenue. ' + interp
    return findings, interp


def _fmt_quality(res, q):
    lines = []
    for kind, d in res.value.items():
        miss = d['columns_with_missing_values']
        miss_text = ', '.join(f'{c} ({n})' for c, n in miss.items()) if miss else 'none'
        extra = ''
        if 'duplicate_sales_ids' in d:
            extra += f'; duplicate Sales IDs: {d["duplicate_sales_ids"]}'
        if 'missing_or_non_numeric_revenue' in d:
            extra += f'; missing or non-numeric revenue: {d["missing_or_non_numeric_revenue"]}'
        lines.append(f'- {kind}: {d["rows"]:,} rows, {d["duplicate_rows"]} duplicate rows, '
                     f'missing values: {miss_text}{extra}')
    return '\n'.join(lines), ''


def _fmt_judgment(res, q):
    if res.tool == 'get_store_trend':
        findings, _ = _fmt_store(res, q)
    else:
        findings, _ = _fmt_channel(res, q)
    interp = ('Revenue alone cannot answer this question. To decide, management would also need operating '
              'expenses, store-level profit, lease obligations and customer behavior, none of which are in '
              'this data. A revenue decline is a reason to investigate, not by itself a reason to act.')
    return findings, interp


# ------------------------------------------------------------------- answer
def _scope_text(df) -> str:
    years = analytics._years(df) if 'Year' in df.columns else []
    span = f'{years[0]} to {years[-1]}' if years else 'all dates'
    return (f'Full loaded dataset ({span}, {len(df):,} sales rows). '
            f'Dashboard filters do NOT change the assistant\'s answers.')


def answer_question(question: str, df, mode: str = 'rules', tables: dict | None = None) -> dict:
    """Return {'scope','findings','interpretation','limitations','tool_log', ...}."""
    answer = {'question': question, 'mode': 'rules', 'scope': _scope_text(df), 'findings': '',
              'interpretation': '', 'limitations': [], 'tool_log': [], 'supported': True}
    plan = route(question, df)
    if plan['topic'] == 'readonly':
        answer['supported'] = False
        answer['findings'] = ('I am a read-only assistant. I can only run my approved calculation functions on '
                              'the loaded data. I cannot change, delete or add data, and I cannot run code or SQL.')
        return answer
    if plan['tool'] is None:
        answer['supported'] = False
        answer['findings'] = ('I can only answer questions that one of my approved analytical functions can '
                              'calculate, and I could not match this question to one. Try a question like:\n'
                              + '\n'.join(f'- {s}' for s in SUPPORTED_QUESTIONS))
        return answer

    result = run_tool(plan['tool'], plan['args'], df, tables)
    answer['tool_log'].append({'function': plan['tool'], 'arguments': plan['args'],
                               'result': result.to_dict()})
    if result.error:
        answer['findings'] = f'The calculation could not be completed: {result.error}'
        return answer

    topic = plan['topic']
    if topic in ('channel',):
        findings, interp = _fmt_channel(result, question)
    elif topic == 'year':
        findings, interp = _fmt_year(result, question)
    elif topic == 'channel_growth':
        findings, interp = _fmt_channel_growth(result, question)
    elif topic == 'store':
        findings, interp = _fmt_store(result, question)
    elif topic == 'category':
        findings, interp = _fmt_category(result, question)
    elif topic == 'monthly':
        findings, interp = _fmt_monthly(result, question)
    elif topic in ('association', 'causation'):
        findings, interp = _fmt_satisfaction(result, question, topic)
    elif topic == 'quality':
        findings, interp = _fmt_quality(result, question)
    else:  # judgment
        findings, interp = _fmt_judgment(result, question)

    if topic == 'judgment':
        answer['findings'] = ('I cannot answer that from revenue data alone (profit, cost and decision questions '
                              'need information this data does not contain). Here is the revenue evidence the '
                              'data does contain:\n\n' + findings)
    else:
        answer['findings'] = findings
    answer['interpretation'] = interp
    answer['limitations'] = list(result.caveats)
    return answer
