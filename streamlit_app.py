"""Main app. Keep calculations in analytics.py and the assistant in agent.py."""
import pandas as pd
import streamlit as st

import agent
import analytics

MAX_QUESTIONS = 15  # per-session limit for the assistant

st.set_page_config(page_title='E-Presso BI Analyst', layout='wide')
st.title('E-Presso BI Analyst')
st.caption('Interactive business intelligence dashboard and analytical assistant for E-Presso.')


@st.cache_data(show_spinner=False)
def cached_load(file_bytes: bytes, filename: str, sheet_name):
    # The cache is keyed on the file CONTENTS, so a modified file is never
    # confused with the original one.
    return analytics.load_table(file_bytes, filename, sheet_name)


with st.sidebar:
    st.header('Data')
    uploaded_files = st.file_uploader(
        'Upload all E-Presso files (CSV or Excel)',
        type=['csv', 'txt', 'tsv', 'xlsx', 'xls'],
        accept_multiple_files=True,
    )

if not uploaded_files:
    st.info('Upload your E-Presso files in the sidebar to begin.')
    st.stop()

# Read every file and work out which table it is from its columns.
tables = {}
unrecognized = []
for f in uploaded_files:
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

st.sidebar.write('**Recognized tables:**')
for kind, table in tables.items():
    st.sidebar.write(f'{kind}: {len(table):,} rows')
for name in unrecognized:
    st.sidebar.warning(f'Not recognized: {name}')

df, model_report = analytics.build_model(tables)
if df is None:
    st.error(model_report['problems'][0])
    st.stop()

tab_data, tab_dash, tab_assistant = st.tabs(['Data and profile', 'Dashboard', 'Assistant'])

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

    st.subheader('Join checks')
    st.caption('After each join, rows and revenue should NOT change. If they do, a join duplicated revenue.')
    if model_report['steps']:
        st.dataframe(pd.DataFrame(model_report['steps']))
    for problem in model_report['problems']:
        st.warning(problem)

with tab_dash:
    total = analytics.get_total(df, 'Revenue')
    if total.error:
        st.error(total.error)
    else:
        k1, k2, k3 = st.columns(3)
        k1.metric('Total revenue', f'${total.value:,.0f}')
        k2.metric('Sales rows used', f'{total.rows_used:,}')
        k3.metric('Period', f"{df['Date'].min():%b %Y} to {df['Date'].max():%b %Y}")
        st.caption(f'{total.definition}. Revenue is not profit.')
    st.info('Charts and filters come in Lesson 3.')

with tab_assistant:
    st.caption('Mode: rules-based starter. Describe what you actually built here.')
    st.session_state.setdefault('n_questions', 0)
    st.session_state.setdefault('history', [])
    for item in st.session_state['history']:
        with st.chat_message('user'):
            st.write(item['question'])
        with st.chat_message('assistant'):
            st.markdown(f"**Scope:** {item['answer']['scope']}")
            st.markdown(f"**Findings:** {item['answer']['findings']}")
            if item['answer']['interpretation']:
                st.markdown(f"**Interpretation:** {item['answer']['interpretation']}")
            with st.expander('Tool-call log'):
                st.json(item['answer']['tool_log'])
    question = st.chat_input('Ask a question about the loaded data')
    if question:
        if st.session_state['n_questions'] >= MAX_QUESTIONS:
            st.warning('Question limit reached for this session. Reload the page to start over.')
        else:
            st.session_state['n_questions'] += 1
            answer = agent.answer_question(question, df, mode='rules')
            st.session_state['history'].append({'question': question, 'answer': answer})
            st.rerun()
