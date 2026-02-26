import marimo

__generated_with = "0.20.2"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import polars as pl
    import plotly.express as px
    import plotly.graph_objects as go
    import clickhouse_connect
    import time as time_mod
    from datetime import datetime




    return clickhouse_connect, mo


@app.cell
def _(clickhouse_connect, mo):
    CH_HOST = "192.168.0.148"
    ch = clickhouse_connect.get_client(
        host=CH_HOST, port=18123, database="polymarket"
    )
    ver = ch.query("SELECT version()").first_row[0]
    mo.md(f"Connected to ClickHouse **{ver}** at `{CH_HOST}`")
    return (ch,)


@app.cell
def _(ch, mo):
    _df = mo.sql(
        f"""
        SELECT * FROM
        """,
        engine=ch
    )
    return


if __name__ == "__main__":
    app.run()
