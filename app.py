import os
from contextlib import closing

import pandas as pd
import streamlit as st
from databricks import sql as dbsql

st.set_page_config(page_title="DVD Rental Data Warehouse Flow", page_icon="🎬", layout="wide")

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
DBX_CATALOG = st.secrets.get("databricks", {}).get("catalog", "")
DBX_SCHEMA = st.secrets.get("databricks", {}).get("schema", "")


def dbx_table(name: str) -> str:
    """Return a safely composed catalog.schema.table name from trusted secrets."""
    parts = [p for p in [DBX_CATALOG, DBX_SCHEMA, name] if p]
    return ".".join(f"`{p}`" for p in parts)


# -----------------------------------------------------------------------------
# Connections
# -----------------------------------------------------------------------------
@st.cache_resource
def get_databricks_connection():
    cfg = st.secrets["databricks"]
    return dbsql.connect(
        server_hostname=cfg["server_hostname"],
        http_path=cfg["http_path"],
        access_token=cfg["token"],
    )


def pg_query(query: str) -> pd.DataFrame:
    conn = st.connection("postgresql", type="sql")
    return conn.query(query, ttl=0)


def dbx_query(query: str) -> pd.DataFrame:
    conn = get_databricks_connection()
    with conn.cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description] if cursor.description else []
    return pd.DataFrame(rows, columns=columns)


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
SOURCE_TABLES = ["customer", "staff", "film", "inventory", "rental", "payment"]
BRONZE_TABLES = [f"bronze_{t}" for t in SOURCE_TABLES]
STAGING_TABLES = [f"stg_{t}" for t in SOURCE_TABLES]
DIM_TABLES = ["dim_customer", "dim_staff", "dim_film", "dim_date"]
FACT_TABLES = ["fact_rental"]


def source_counts() -> pd.DataFrame:
    pieces = [f"select '{t}' as table_name, count(*) as row_count from {t}" for t in SOURCE_TABLES]
    return pg_query(" union all ".join(pieces))


def dbx_counts(names: list[str]) -> pd.DataFrame:
    pieces = [
        f"select '{t}' as table_name, count(*) as row_count from {dbx_table(t)}"
        for t in names
    ]
    return dbx_query(" union all ".join(pieces))


def sample_pg(table: str) -> pd.DataFrame:
    return pg_query(f"select * from {table} limit 5")


def sample_dbx(table: str) -> pd.DataFrame:
    return dbx_query(f"select * from {dbx_table(table)} limit 5")


def stage_card(title: str, subtitle: str, metric: str | int | None = None):
    with st.container(border=True):
        st.subheader(title)
        st.caption(subtitle)
        if metric is not None:
            st.metric("Rows", metric)


# -----------------------------------------------------------------------------
# Header + flow diagram
# -----------------------------------------------------------------------------
st.title("🎬 DVD Rental: PostgreSQL → Databricks Warehouse")
st.write(
    "A live teaching dashboard that follows the same pipeline as the Week 3–4 scripts: "
    "operational PostgreSQL → CSV/raw Bronze → typed Staging → star schema → analytics."
)

st.graphviz_chart(
    """
    digraph G {
      rankdir=LR;
      node [shape=box, style="rounded,filled", fillcolor="white"];
      pg [label="PostgreSQL\nOperational DB"];
      csv [label="CSV Extract\n\\copy"];
      bronze [label="Databricks Bronze\nRaw tables"];
      staging [label="Databricks Staging\nTyped tables"];
      dims [label="Dimensions\ndim_customer | dim_staff\ndim_film | dim_date"];
      fact [label="Fact\nfact_rental"];
      bi [label="Streamlit\nAnalytics"];
      pg -> csv -> bronze -> staging -> dims -> fact -> bi;
      staging -> fact;
    }
    """,
    use_container_width=True,
)

if st.button("🔄 Refresh live data", type="primary"):
    st.cache_data.clear()
    st.cache_resource.clear()
    st.rerun()

# -----------------------------------------------------------------------------
# Main tabs
# -----------------------------------------------------------------------------
tab_pg, tab_bronze, tab_staging, tab_star, tab_analytics = st.tabs(
    [
        "1 · PostgreSQL",
        "2 · Bronze",
        "3 · Staging",
        "4 · Star Schema",
        "5 · Analytics",
    ]
)

with tab_pg:
    st.header("1. Operational PostgreSQL")
    st.write(
        "This is the normalized source system. The dashboard only reads it; your Week 3 `\\copy` commands perform the extraction."
    )
    try:
        counts = source_counts()
        cols = st.columns(3)
        for i, row in counts.iterrows():
            with cols[i % 3]:
                stage_card(str(row["table_name"]), "Operational source table", int(row["row_count"]))

        selected = st.selectbox("Inspect PostgreSQL table", SOURCE_TABLES, key="pg_table")
        st.dataframe(sample_pg(selected), use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"PostgreSQL connection/query failed: {e}")

with tab_bronze:
    st.header("2. Databricks Bronze")
    st.write(
        "The six CSV extracts land here as raw ingestion tables. Bronze preserves the source shape before warehouse transformations."
    )
    try:
        counts = dbx_counts(BRONZE_TABLES)
        st.dataframe(counts, use_container_width=True, hide_index=True)

        selected = st.selectbox("Inspect Bronze table", BRONZE_TABLES, key="bronze_table")
        st.dataframe(sample_dbx(selected), use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"Databricks Bronze query failed: {e}")

with tab_staging:
    st.header("3. Typed Staging")
    st.write(
        "Staging keeps essentially the same business entities but fixes data types. The best validation is that each Bronze row count matches its Staging counterpart."
    )
    try:
        bronze = dbx_counts(BRONZE_TABLES).copy()
        staging = dbx_counts(STAGING_TABLES).copy()
        bronze["entity"] = bronze["table_name"].str.replace("bronze_", "", regex=False)
        staging["entity"] = staging["table_name"].str.replace("stg_", "", regex=False)
        compare = bronze[["entity", "row_count"]].rename(columns={"row_count": "bronze_rows"}).merge(
            staging[["entity", "row_count"]].rename(columns={"row_count": "staging_rows"}),
            on="entity",
        )
        compare["match"] = compare["bronze_rows"] == compare["staging_rows"]
        st.dataframe(compare, use_container_width=True, hide_index=True)

        selected = st.selectbox("Inspect Staging table", STAGING_TABLES, key="staging_table")
        st.dataframe(sample_dbx(selected), use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"Databricks Staging query failed: {e}")

with tab_star:
    st.header("4. Warehouse Star Schema")
    st.write(
        "Here the operational entities are reorganized for analytics: descriptive dimensions surround one transaction-grain fact table."
    )

    st.graphviz_chart(
        """
        digraph STAR {
          rankdir=TB;
          node [shape=box, style="rounded,filled", fillcolor="white"];
          fact [label="fact_rental\n1 row = 1 rental", shape=box3d];
          customer [label="dim_customer"];
          staff [label="dim_staff"];
          film [label="dim_film"];
          date [label="dim_date"];
          customer -> fact [label="customer_id"];
          staff -> fact [label="staff_id"];
          film -> fact [label="film_id"];
          date -> fact [label="date_id"];
        }
        """,
        use_container_width=True,
    )

    try:
        counts = dbx_counts(DIM_TABLES + FACT_TABLES)
        st.dataframe(counts, use_container_width=True, hide_index=True)

        fact_count = int(counts.loc[counts.table_name == "fact_rental", "row_count"].iloc[0])
        stg_rental_count = int(dbx_counts(["stg_rental"])["row_count"].iloc[0])
        no_payment = dbx_query(
            f"select count(*) as n from {dbx_table('fact_rental')} where amount is null"
        )["n"].iloc[0]

        c1, c2, c3 = st.columns(3)
        c1.metric("Staging rentals", stg_rental_count)
        c2.metric("Fact rows", fact_count, delta=fact_count - stg_rental_count)
        c3.metric("Rentals without payment", int(no_payment))

        selected = st.selectbox(
            "Inspect warehouse table", DIM_TABLES + FACT_TABLES, key="warehouse_table"
        )
        st.dataframe(sample_dbx(selected), use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"Warehouse query failed: {e}")

with tab_analytics:
    st.header("5. Business Analytics")
    st.write(
        "These queries are powered by the star schema, not by the normalized operational PostgreSQL tables."
    )
    try:
        revenue_film = dbx_query(
            f"""
            select f.title,
                   count(*) as times_rented,
                   sum(fr.amount) as total_revenue
            from {dbx_table('fact_rental')} fr
            join {dbx_table('dim_film')} f on fr.film_id = f.film_id
            group by f.title
            order by total_revenue desc
            limit 10
            """
        )

        revenue_staff = dbx_query(
            f"""
            select concat(s.first_name, ' ', s.last_name) as staff,
                   sum(fr.amount) as total_revenue
            from {dbx_table('fact_rental')} fr
            join {dbx_table('dim_staff')} s on fr.staff_id = s.staff_id
            group by s.first_name, s.last_name
            order by total_revenue desc
            """
        )

        monthly = dbx_query(
            f"""
            select d.year, d.month, sum(fr.amount) as monthly_revenue
            from {dbx_table('fact_rental')} fr
            join {dbx_table('dim_date')} d on fr.date_id = d.date_id
            group by d.year, d.month
            order by d.year, d.month
            """
        )
        monthly["period"] = monthly["year"].astype(str) + "-" + monthly["month"].astype(str).str.zfill(2)

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Top films by revenue")
            st.bar_chart(revenue_film.set_index("title")["total_revenue"])
            st.dataframe(revenue_film, use_container_width=True, hide_index=True)
        with c2:
            st.subheader("Revenue by staff")
            st.bar_chart(revenue_staff.set_index("staff")["total_revenue"])
            st.dataframe(revenue_staff, use_container_width=True, hide_index=True)

        st.subheader("Monthly revenue")
        st.line_chart(monthly.set_index("period")["monthly_revenue"])
        st.dataframe(monthly, use_container_width=True, hide_index=True)
    except Exception as e:
        st.error(f"Analytics query failed: {e}")

st.divider()
st.caption(
    "Teaching point: Streamlit is the presentation layer. PostgreSQL remains the operational source; Databricks contains the analytical pipeline and warehouse."
)
