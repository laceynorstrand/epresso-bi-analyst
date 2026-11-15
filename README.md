# E-Presso BI Analyst

An interactive business intelligence dashboard and analytical assistant for the fictional company E-Presso, built with Python, pandas, Plotly and Streamlit.

**Live app:** https://epreapp-bi-analyst-niyvuax5xoakv47wqzvnkc.streamlit.app/

## What the app does

- Loads the E-Presso sales, stores, products, categories and survey data and profiles each table (row counts, duplicates, missing values, data types).
- Joins the tables safely and checks after every join that rows and total revenue did not change.
- Calculates total revenue and other metrics from the **currently loaded data** (nothing is hardcoded).
- Shows four interactive charts with filters for year, product category and sales channel. All KPIs and charts recalculate when a filter changes.
- Accepts updated files in the sidebar. Tables are recognized by their column names, so a modified Sales file replaces the bundled one without any code change.
- Includes an analytical assistant that answers business questions by calling approved Python functions and shows a tool-call log with every answer.

## How to use it

1. Open the live app. It starts with the sample data stored in the `data/` folder.
2. Use the **Dashboard** tab filters to explore revenue.
3. To test updated data, upload a changed file in the sidebar (for example a modified Sales.csv). The sidebar shows which tables are bundled and which are uploaded.
4. Use the **Assistant** tab to ask a business question. Each answer shows its scope, calculated findings, interpretation, limitations and the tool-call log (function name, arguments, raw result). Sessions are limited to 15 questions.

## Files

| File | Purpose |
|---|---|
| `streamlit_app.py` | The web app: data loading, dashboard, assistant chat |
| `analytics.py` | Data model, joins, merge check, and every analytical function |
| `agent.py` | Question router, approved-function dispatcher, answer formatting |
| `data/` | Sample E-Presso files used when nothing is uploaded |
| `requirements.txt` | Python packages the hosted app needs |

## Data model and grain

| Table | One row = | Key |
|---|---|---|
| Sales | One product's revenue for one month at one location (25,200 rows, Jan 2025 to Dec 2029) | Sales ID |
| Stores | One sales location. SH00 is the Website, SH01 to SH20 are brick-and-mortar stores | ID |
| Products | One product | Product ID |
| Categories | One product category | Category ID |
| Survey Results | One satisfaction and interest score for one store in one year | Survey ID |

Relationships:

- Sales `Store ID` to Stores `ID`
- Sales `Product ID` to Products `Product ID`
- Sales `Category ID` to Categories `Category ID`
- Sales `StoreYear` to Survey `Survey ID`

The survey is yearly while Sales is monthly, so it is **not** joined into the revenue table. Joining it would repeat survey rows and risk inflating revenue. The assistant uses it separately for satisfaction questions.

## Revenue definition

Revenue is the sum of the `Revenue` column in Sales. It is **not profit**. The data contains no costs, so the app cannot measure profitability.

## Analytical functions (the only calculations the assistant can use)

| Function | Answers |
|---|---|
| `get_revenue_by_channel(year)` | Website vs. brick-and-mortar revenue and share |
| `get_revenue_by_year(channel)` | Revenue per year and change from the prior year |
| `get_channel_growth()` | Channel revenue growth, first to last year |
| `get_store_trend()` | Revenue change per location, first to last year |
| `get_category_performance(year)` | Revenue and share by product category |
| `get_monthly_revenue_share(year)` | Each month as a percentage of the annual total |
| `get_satisfaction_association()` | Correlation of satisfaction and revenue (association only) |
| `get_data_quality_report()` | Row counts, duplicates and missing values per table |

## Assistant design and boundaries

- **Mode:** rules-based assistant. A keyword router matches the question to one approved function. It is **not** a language-model agent. (Update this section if a Gemini mode is added.)
- It runs only the named functions above. It never executes generated Python or SQL, never uses `eval` or `exec`, and cannot modify the data.
- Every number in an answer comes from a tool result. Answers separate calculated findings from interpretation and list limitations.
- Questions about profit, closing a store or other decisions get the revenue evidence plus a statement of what extra information is needed.
- Unsupported or data-changing requests are declined with examples of supported questions.
- The assistant answers about the full loaded dataset. Dashboard filters do not change its answers, and each answer says so.

## Validation summary

- Total revenue 2025 to 2029: **$42,174,440**, matching the Power BI report, an Excel column sum, and an independent pandas calculation.
- Website revenue $27,218,880 and brick-and-mortar revenue $14,955,560 matched Power BI.
- Modified-data test: adding $1,000 to one Sales row (S0020250101, 6,780 to 7,780) raised total revenue to exactly $42,175,440 with no code change.

## Limitations

- Revenue is not profit. No cost, lease or staffing data is available.
- Survey data has no scores for the Website and five blank store-year rows, so satisfaction conclusions cover physical stores only.
- Correlation between satisfaction and revenue is not causation.
- Growth comparisons use only the first and last year.
- The assistant understands a limited set of question types.

## Development

Built with AI coding assistance (Claude) for planning, code generation and debugging. All calculations were verified against Power BI, Excel and independent pandas checks.

Team members and contributions: *(add names and roles here)*
