"""Tests for ClickHouse -> DuckDB SQL translation."""

from polymarket_pipeline.exploration.duckdb_source import translate_ch_to_duckdb


def test_strip_schema_prefix():
    sql = "SELECT * FROM polymarket.trades_raw"
    assert "polymarket." not in translate_ch_to_duckdb(sql)
    assert "trades_raw" in translate_ch_to_duckdb(sql)


def test_strip_final():
    sql = "SELECT * FROM trades_raw FINAL WHERE x = 1"
    result = translate_ch_to_duckdb(sql)
    assert "FINAL" not in result
    assert "trades_raw" in result


def test_count_empty_parens():
    sql = "SELECT count() FROM t"
    assert "count(*)" in translate_ch_to_duckdb(sql)


def test_arg_max():
    sql = "SELECT argMax(price, timestamp) FROM t"
    assert "arg_max(price, timestamp)" in translate_ch_to_duckdb(sql)


def test_stddev_pop():
    sql = "SELECT stddevPop(pnl) FROM t"
    assert "stddev_pop(pnl)" in translate_ch_to_duckdb(sql)


def test_multi_if():
    sql = "SELECT multiIf(x >= 0.95, 1, x <= 0.05, 0, -1) FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "CASE WHEN x >= 0.95 THEN 1 WHEN x <= 0.05 THEN 0 ELSE -1 END" in result


def test_sum_if():
    sql = "SELECT sumIf(amount, side = 'BUY') FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "SUM(CASE WHEN side = 'BUY' THEN amount END)" in result


def test_count_if():
    sql = "SELECT countIf(fee > 0) FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "COUNT(CASE WHEN fee > 0 THEN 1 END)" in result


def test_quantile():
    sql = "SELECT quantile(0.25)(pnl) FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "quantile_cont(pnl, 0.25)" in result


def test_quantile_multiple():
    sql = "SELECT quantile(0.25)(pnl) AS p25, quantile(0.75)(pnl) AS p75 FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "quantile_cont(pnl, 0.25)" in result
    assert "quantile_cont(pnl, 0.75)" in result


def test_int_div():
    sql = "SELECT intDiv(a, b) FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "CAST(FLOOR((a) / (b)) AS BIGINT)" in result


def test_to_unix_timestamp():
    sql = "SELECT toUnixTimestamp(timestamp) FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "CAST(epoch(timestamp) AS BIGINT)" in result


def test_to_date_time():
    sql = "SELECT toDateTime('2025-08-01') FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "CAST('2025-08-01' AS TIMESTAMP)" in result


def test_to_float32():
    sql = "SELECT toFloat32(0.0) AS fee FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "CAST(0.0 AS FLOAT)" in result


def test_to_uint8():
    sql = "SELECT toUInt8(x) FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "CAST(x AS TINYINT)" in result


def test_if_function():
    sql = "SELECT if(side = 'BUY', 'SELL', 'BUY') FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "CASE WHEN side = 'BUY' THEN 'SELL' ELSE 'BUY' END" in result


def test_nested_multi_if_with_arg_max():
    """Tests the actual resolved_markets_cte pattern."""
    sql = """SELECT multiIf(
        argMax(t.price, t.timestamp) >= 0.95, 1,
        argMax(t.price, t.timestamp) <= 0.05, 0,
        -1
    ) AS resolution_value FROM t"""
    result = translate_ch_to_duckdb(sql)
    assert "arg_max(t.price, t.timestamp)" in result
    assert "CASE WHEN" in result
    assert "ELSE -1 END" in result


def test_complex_sum_if():
    """Tests the positions_cte pattern with expressions in sumIf."""
    sql = "SELECT sumIf(tt.price * tt.amount_usd, tt.trader_side = 'BUY') FROM t"
    result = translate_ch_to_duckdb(sql)
    assert "SUM(CASE WHEN tt.trader_side = 'BUY' THEN tt.price * tt.amount_usd END)" in result


def test_full_query_translation():
    """Smoke test: translate a query resembling compute_trader_aggregates."""
    sql = """
        SELECT
            trader,
            sum(pnl) AS estimated_pnl,
            avg(is_win) AS win_rate_by_market,
            avg(pnl) / greatest(stddevPop(pnl), 0.001) AS sharpe,
            count() AS resolved_markets,
            quantile(0.25)(pnl) AS pnl_p25,
            quantile(0.50)(pnl) AS pnl_p50,
            quantile(0.75)(pnl) AS pnl_p75,
            stddevPop(pnl) AS pnl_stddev
        FROM polymarket.trades_raw FINAL
        GROUP BY trader
    """
    result = translate_ch_to_duckdb(sql)
    assert "polymarket." not in result
    assert "FINAL" not in result
    assert "count(*)" in result
    assert "stddev_pop(pnl)" in result
    assert "quantile_cont(pnl, 0.25)" in result
    assert "quantile_cont(pnl, 0.50)" in result
    assert "quantile_cont(pnl, 0.75)" in result


def test_temporal_consistency_pattern():
    """Tests the complex temporal consistency SQL pattern."""
    sql = """
        toUInt8(
            least(
                intDiv(
                    toUnixTimestamp(min(tt.timestamp))
                        - toUnixTimestamp(toDateTime('2025-08-01')),
                    greatest(
                        (toUnixTimestamp(now())
                            - toUnixTimestamp(toDateTime('2025-08-01')))
                        / 4,
                        1
                    )
                ),
                3
            )
        ) AS sub_period
    """
    result = translate_ch_to_duckdb(sql)
    assert "CAST(" in result
    assert "AS TINYINT)" in result
    assert "CAST(FLOOR(" in result
    assert "CAST(epoch(" in result
    assert "AS TIMESTAMP)" in result
