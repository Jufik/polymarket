"""Lightweight HTTP introspection server for a running LiveRunner.

Starts an aiohttp server inside the strategy process, exposing
live state: pool, candidate markets, positions, budget, etc.

Usage (from strategy CLI):
    server = IntrospectServer(runner, port=8002)
    await server.start()
    ...
    await server.stop()
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import structlog
from aiohttp import web

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)


def _lookup_questions(condition_ids: list[str]) -> dict[str, str]:
    """Look up market questions from ClickHouse (sync, for run_in_executor)."""
    if not condition_ids:
        return {}
    try:
        import clickhouse_connect

        ch = clickhouse_connect.get_client(
            host="192.168.0.148",
            port=18123,
            database="polymarket",
        )
        placeholders = ", ".join(f"'{cid}'" for cid in condition_ids)
        rows = ch.query(
            f"SELECT condition_id, question FROM markets WHERE condition_id IN ({placeholders})"
        )
        return {str(r[0]): str(r[1]) for r in rows.result_rows}
    except Exception:
        return {}


class IntrospectServer:
    def __init__(self, runner: Any, port: int = 8002) -> None:
        self._runner = runner
        self._port = port
        self._start_time = time.time()
        self._app = web.Application()
        self._app.router.add_get("/status", self._handle_status)
        self._app.router.add_get("/pool", self._handle_pool)
        self._app.router.add_get("/pool/{provider}", self._handle_pool_detail)
        self._app.router.add_get("/candidates", self._handle_candidates)
        self._app.router.add_get("/signaled", self._handle_signaled)
        self._app.router.add_get("/positions", self._handle_positions)
        self._app.router.add_get("/budget", self._handle_budget)
        self._runner_site: web.TCPSite | None = None
        self._runner_obj: web.AppRunner | None = None

    async def start(self) -> None:
        self._runner_obj = web.AppRunner(self._app)
        await self._runner_obj.setup()
        try:
            self._runner_site = web.TCPSite(
                self._runner_obj,
                "0.0.0.0",
                self._port,
                reuse_address=True,
            )
            await self._runner_site.start()
            logger.warning("introspect.started", port=self._port)
        except OSError as e:
            logger.warning("introspect.port_unavailable", port=self._port, error=str(e))

    async def stop(self) -> None:
        if self._runner_obj:
            await self._runner_obj.cleanup()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_strategy_internals(self) -> list[dict[str, Any]]:
        results = []
        for strategy, config in self._runner.strategies:
            info: dict[str, Any] = {
                "name": strategy.name,
                "enabled": config.enabled,
                "mode": config.mode.value if hasattr(config.mode, "value") else str(config.mode),
            }
            if hasattr(strategy, "_market_state"):
                info["market_state_count"] = len(strategy._market_state)
            if hasattr(strategy, "_signaled"):
                info["signaled_count"] = len(strategy._signaled)
            if hasattr(strategy, "_n_threshold"):
                info["n_threshold"] = strategy._n_threshold
            if hasattr(strategy, "_direction_filter"):
                info["direction_filter"] = strategy._direction_filter
            if hasattr(strategy, "_max_price"):
                info["max_price"] = strategy._max_price
            if hasattr(strategy, "_max_entry_price"):
                info["max_entry_price"] = strategy._max_entry_price
            if hasattr(strategy, "_size_usd"):
                info["size_usd"] = strategy._size_usd
            results.append(info)
        return results

    def _get_provider_summary(self) -> list[dict[str, Any]]:
        results = []
        for provider in self._runner.providers:
            info: dict[str, Any] = {"name": provider.name}
            if hasattr(provider, "_tag"):
                info["tag"] = provider._tag
            if hasattr(provider, "_k"):
                info["k"] = provider._k
            if hasattr(provider, "_pool"):
                info["pool_size"] = len(provider._pool)
            if hasattr(provider, "_tag_markets"):
                info["tag_markets"] = len(provider._tag_markets)
            if hasattr(provider, "_gambling_markets"):
                info["gambling_excluded"] = len(provider._gambling_markets)
            if hasattr(provider, "_initialized"):
                info["initialized"] = provider._initialized
            results.append(info)
        return results

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_status(self, _request: web.Request) -> web.Response:
        r = self._runner
        uptime_s = time.time() - self._start_time
        uptime_h = uptime_s / 3600
        data = {
            "uptime_s": round(uptime_s),
            "uptime_h": round(uptime_h, 2),
            "trades_processed": r._trades_processed,
            "intents_submitted": r._intents_submitted,
            "drops_dedup": r._drops_dedup,
            "drops_stale": r._drops_stale,
            "unique_markets": len(r._unique_markets),
            "strategies": self._get_strategy_internals(),
            "providers": self._get_provider_summary(),
        }
        return web.json_response(data)

    async def _handle_pool(self, _request: web.Request) -> web.Response:
        results = []
        for provider in self._runner.providers:
            info: dict[str, Any] = {"name": provider.name}
            if hasattr(provider, "_tag"):
                info["tag"] = provider._tag
            if hasattr(provider, "_pool"):
                pool = provider._pool
                info["pool_size"] = len(pool)
                info["traders"] = sorted(f"{addr[:8]}...{addr[-6:]}" for addr in pool)
            results.append(info)
        return web.json_response(results)

    async def _handle_pool_detail(self, request: web.Request) -> web.Response:
        name = request.match_info["provider"]
        for provider in self._runner.providers:
            if provider.name == name:
                pool = getattr(provider, "_pool", frozenset())
                return web.json_response(
                    {
                        "name": provider.name,
                        "tag": getattr(provider, "_tag", None),
                        "pool_size": len(pool),
                        "traders": sorted(pool),
                    }
                )
        return web.json_response({"error": f"Provider {name!r} not found"}, status=404)

    async def _handle_candidates(self, request: web.Request) -> web.Response:
        filter_strategy = request.query.get("strategy")
        candidates = []
        for strategy, _config in self._runner.strategies:
            if filter_strategy and strategy.name != filter_strategy:
                continue
            if not hasattr(strategy, "_market_state"):
                continue
            n_threshold = getattr(strategy, "_n_threshold", 0)
            signaled = getattr(strategy, "_signaled", set())
            direction_filter = getattr(strategy, "_direction_filter", None)
            for cid, traders in strategy._market_state.items():
                trader_dirs: dict[str, str] = {}
                yes_usd = 0.0
                no_usd = 0.0
                for trader, dirs in traders.items():
                    y = dirs.get("YES", 0)
                    n = dirs.get("NO", 0)
                    if y > 0 or n > 0:
                        trader_dirs[trader] = "YES" if y >= n else "NO"
                        yes_usd += y
                        no_usd += n
                n_traders = len(trader_dirs)
                consensus_dir = "YES" if yes_usd >= no_usd else "NO"
                market = self._runner.ctx._markets.get(cid)
                question = market.question if market else None
                candidates.append(
                    {
                        "strategy": strategy.name,
                        "condition_id": cid,
                        "question": question,
                        "n_traders": n_traders,
                        "n_threshold": n_threshold,
                        "distance": n_threshold - n_traders,
                        "consensus_direction": consensus_dir,
                        "direction_filter": direction_filter,
                        "would_pass_filter": direction_filter is None
                        or consensus_dir == direction_filter,
                        "yes_usd": round(yes_usd, 2),
                        "no_usd": round(no_usd, 2),
                        "signaled": cid in signaled,
                        "traders": {f"{t[:8]}...{t[-6:]}": d for t, d in trader_dirs.items()},
                    }
                )
        missing_cids = list({c["condition_id"] for c in candidates if c["question"] is None})
        if missing_cids:
            questions = await asyncio.get_event_loop().run_in_executor(
                None, _lookup_questions, missing_cids
            )
            for c in candidates:
                if c["question"] is None:
                    c["question"] = questions.get(c["condition_id"])
        candidates.sort(key=lambda c: (c["signaled"], c["distance"], -c["n_traders"]))
        return web.json_response({"count": len(candidates), "candidates": candidates})

    async def _handle_signaled(self, _request: web.Request) -> web.Response:
        results = []
        for strategy, _config in self._runner.strategies:
            signaled = getattr(strategy, "_signaled", set())
            for cid in signaled:
                market = self._runner.ctx._markets.get(cid)
                results.append(
                    {
                        "strategy": strategy.name,
                        "condition_id": cid,
                        "question": market.question if market else None,
                    }
                )
        missing_cids = list({r["condition_id"] for r in results if r["question"] is None})
        if missing_cids:
            questions = await asyncio.get_event_loop().run_in_executor(
                None, _lookup_questions, missing_cids
            )
            for r in results:
                if r["question"] is None:
                    r["question"] = questions.get(r["condition_id"])
        return web.json_response(results)

    async def _handle_positions(self, _request: web.Request) -> web.Response:
        positions = self._runner.ctx.get_all_positions()
        results = []
        for cid, pos in positions.items():
            if pos.qty_yes <= 0 and pos.qty_no <= 0:
                continue
            market = self._runner.ctx._markets.get(cid)
            results.append(
                {
                    "condition_id": cid,
                    "question": market.question if market else None,
                    "strategy": pos.strategy,
                    "qty_yes": round(pos.qty_yes, 4),
                    "qty_no": round(pos.qty_no, 4),
                    "avg_entry_yes": round(pos.avg_entry_yes, 4),
                    "avg_entry_no": round(pos.avg_entry_no, 4),
                    "cost_basis": round(pos.cost_basis, 4),
                    "realized_pnl": round(pos.realized_pnl, 4),
                }
            )
        return web.json_response(results)

    async def _handle_budget(self, _request: web.Request) -> web.Response:
        gw = self._runner.gateway
        budgets = getattr(gw, "_strategy_budgets", {})
        spent = getattr(gw, "_strategy_spent", {})
        results = []
        for name, cap in budgets.items():
            used = spent.get(name, 0.0)
            results.append(
                {
                    "strategy": name,
                    "budget_cap": round(cap, 2),
                    "spent": round(used, 2),
                    "remaining": round(cap - used, 2),
                    "utilization_pct": round(used / cap * 100, 2) if cap > 0 else 0,
                }
            )
        return web.json_response(results)
