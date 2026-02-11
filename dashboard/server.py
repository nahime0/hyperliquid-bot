"""Dashboard web server — live view of the trading bot.

Reads the same SQLite DB as the bot plus a JSON status file for live
in-memory state.  Pushes updates to the browser via WebSocket every 2s.

Usage:
    .venv/bin/python -m dashboard.server                # default :8080
    .venv/bin/python -m dashboard.server --port 9090
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from aiohttp import web, WSMsgType

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_TEMPLATE_DIR = _HERE / "templates"
_DB_PATH = _PROJECT_ROOT / "data" / "trading_bot.db"
_STATUS_PATH = _PROJECT_ROOT / "data" / "bot_status.json"

BROADCAST_INTERVAL = 2  # seconds


# ── Data aggregator ──────────────────────────────────────────────


class DashboardState:
    """Reads DB + status JSON and produces a single dict for the frontend."""

    def __init__(self, db_path: Path, status_path: Path) -> None:
        self._db_path = db_path
        self._status_path = status_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        if not self._db_path.exists():
            return
        self._db = await aiosqlite.connect(
            str(self._db_path),
            uri=True,
        )
        self._db.row_factory = aiosqlite.Row
        # Enable WAL for concurrent reads
        await self._db.execute("PRAGMA journal_mode=WAL")

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def gather(self) -> dict[str, Any]:
        """Collect everything the frontend needs."""
        data: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "db_connected": self._db is not None,
        }

        # Live status file
        data["bot_status"] = self._read_status_file()

        if not self._db:
            return data

        # Run all DB queries concurrently
        (
            data["balance"],
            data["equity_curve"],
            data["ai_decisions"],
            data["ai_costs"],
            data["trades"],
            data["trade_stats"],
            data["open_orders"],
            data["positions"],
        ) = await asyncio.gather(
            self._q_balance(),
            self._q_equity_curve(),
            self._q_ai_decisions(),
            self._q_ai_costs(),
            self._q_trades(),
            self._q_trade_stats(),
            self._q_open_orders(),
            self._q_positions(),
        )

        return data

    # ── Status file ──────────────────────────────────────────

    def _read_status_file(self) -> dict[str, Any] | None:
        try:
            if self._status_path.exists():
                text = self._status_path.read_text()
                return json.loads(text) if text.strip() else None
        except (json.JSONDecodeError, OSError):
            pass
        return None

    # ── SQL queries ──────────────────────────────────────────

    async def _q_balance(self) -> dict[str, Any] | None:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM balance_snapshots ORDER BY id DESC LIMIT 1"
        )
        row = await cur.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["positions"] = json.loads(d.get("positions", "{}"))
        return d

    async def _q_equity_curve(self) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute(
            "SELECT timestamp, total_usdc, peak_balance FROM balance_snapshots ORDER BY id"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _q_ai_decisions(self) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM ai_decisions ORDER BY id DESC LIMIT 50"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _q_ai_costs(self) -> dict[str, Any]:
        assert self._db
        # Per-tier breakdown
        cur = await self._db.execute(
            "SELECT tier, COUNT(*) as cnt, COALESCE(SUM(cost_usd), 0) as total "
            "FROM ai_decisions GROUP BY tier"
        )
        rows = await cur.fetchall()
        tiers = {r["tier"] or "unknown": {"count": r["cnt"], "cost": r["total"]} for r in rows}

        # Today's cost
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cur2 = await self._db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(cost_usd), 0) as total "
            "FROM ai_decisions WHERE timestamp >= ?",
            (today,),
        )
        today_row = await cur2.fetchone()

        # Grand total
        cur3 = await self._db.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(cost_usd), 0) as total FROM ai_decisions"
        )
        grand = await cur3.fetchone()

        return {
            "tiers": tiers,
            "today": {"count": today_row["cnt"], "cost": today_row["total"]} if today_row else {},
            "total": {"count": grand["cnt"], "cost": grand["total"]} if grand else {},
        }

    async def _q_trades(self) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT 50"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _q_trade_stats(self) -> dict[str, Any]:
        assert self._db
        cur = await self._db.execute(
            "SELECT pnl FROM trades WHERE pnl IS NOT NULL ORDER BY id DESC LIMIT 100"
        )
        rows = await cur.fetchall()
        pnls = [r["pnl"] for r in rows]
        if not pnls:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "avg_win": 0, "avg_loss": 0,
                "profit_factor": 0, "consecutive_losses": 0,
                "total_pnl": 0,
            }
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        gross_win = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0

        # Consecutive losses from most recent
        consec = 0
        for p in pnls:
            if p <= 0:
                consec += 1
            else:
                break

        return {
            "total_trades": len(pnls),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(pnls) * 100, 1) if pnls else 0,
            "avg_win": round(gross_win / len(wins), 4) if wins else 0,
            "avg_loss": round(gross_loss / len(losses), 4) if losses else 0,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else 0,
            "consecutive_losses": consec,
            "total_pnl": round(sum(pnls), 4),
        }

    async def _q_open_orders(self) -> list[dict[str, Any]]:
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM orders WHERE status IN ('NEW', 'PARTIALLY_FILLED') ORDER BY id DESC"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _q_positions(self) -> list[dict[str, Any]]:
        """Get open perpetual positions with direction/leverage info."""
        assert self._db
        cur = await self._db.execute(
            "SELECT * FROM positions WHERE status = 'OPEN' ORDER BY id"
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ── Web handlers ─────────────────────────────────────────────────


async def index_handler(request: web.Request) -> web.Response:
    html = (_TEMPLATE_DIR / "index.html").read_text()
    return web.Response(text=html, content_type="text/html")


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    request.app["ws_clients"].add(ws)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.ERROR:
                break
    finally:
        request.app["ws_clients"].discard(ws)
    return ws


async def broadcast_loop(app: web.Application) -> None:
    """Background task: gather state and push to all WS clients every N seconds."""
    state: DashboardState = app["state"]
    await state.connect()

    try:
        while True:
            await asyncio.sleep(BROADCAST_INTERVAL)
            clients = app["ws_clients"]
            if not clients:
                continue

            try:
                data = await state.gather()
                payload = json.dumps(data, default=str)
            except Exception as exc:
                payload = json.dumps({"error": str(exc)})

            dead: set[web.WebSocketResponse] = set()
            for ws in clients:
                try:
                    await ws.send_str(payload)
                except Exception:
                    dead.add(ws)
            clients -= dead
    except asyncio.CancelledError:
        pass
    finally:
        await state.close()


async def on_startup(app: web.Application) -> None:
    app["broadcast_task"] = asyncio.create_task(broadcast_loop(app))


async def on_cleanup(app: web.Application) -> None:
    task = app.get("broadcast_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    # Close all WS connections
    for ws in set(app["ws_clients"]):
        await ws.close()


def create_app(db_path: Path | None = None, status_path: Path | None = None) -> web.Application:
    app = web.Application()
    app["ws_clients"] = set()
    app["state"] = DashboardState(
        db_path=db_path or _DB_PATH,
        status_path=status_path or _STATUS_PATH,
    )

    app.router.add_get("/", index_handler)
    app.router.add_get("/ws", ws_handler)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading Bot Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    parser.add_argument("--port", type=int, default=8080, help="Port")
    parser.add_argument("--db", type=str, default=None, help="Path to SQLite DB")
    parser.add_argument("--status", type=str, default=None, help="Path to bot_status.json")
    args = parser.parse_args()

    app = create_app(
        db_path=Path(args.db) if args.db else None,
        status_path=Path(args.status) if args.status else None,
    )
    print(f"Dashboard running at http://{args.host}:{args.port}")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
