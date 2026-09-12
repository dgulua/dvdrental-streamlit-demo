# DVD Rental PostgreSQL → Databricks Streamlit Demo

This app visualizes the Week 3–4 teaching pipeline:

1. PostgreSQL operational tables
2. Databricks Bronze raw tables
3. Databricks typed staging tables
4. Star schema dimensions + `fact_rental`
5. Analytical charts

## Setup

```bash
cd streamlit_dw_demo
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in PostgreSQL and Databricks connection details.

Run:

```bash
streamlit run app.py
```

## Important

The dashboard is deliberately read-only. Your existing SQL scripts remain responsible for extraction, Bronze ingestion, staging transformation, dimension loading, and fact loading. This makes the app suitable for demonstrating the architecture without letting a dashboard accidentally rebuild the warehouse.
