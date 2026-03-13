"""
거래 기록 SQLite 저장소
- 모든 거래를 로컬 DB에 기록하여 추후 분석 가능
"""
import sqlite3
import os
from datetime import datetime
from typing import Optional

from config.settings import DB_PATH
from utils.logger import setup_logger

logger = setup_logger("storage")


def get_db() -> sqlite3.Connection:
    """SQLite 연결 (자동 테이블 생성)"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _init_tables(conn)
    return conn


def _init_tables(conn: sqlite3.Connection):
    """테이블 초기화"""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,           -- 'long' / 'short'
            action TEXT NOT NULL,         -- 'open' / 'close'
            price REAL NOT NULL,
            quantity REAL NOT NULL,
            leverage INTEGER DEFAULT 1,
            pnl REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            fee REAL DEFAULT 0,
            signal_grade TEXT,            -- 'gold' / 'silver' / 'bronze'
            strategy TEXT,                -- 전략명
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            total_pnl REAL DEFAULT 0,
            total_pnl_pct REAL DEFAULT 0,
            trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            balance REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS bot_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        );
    """)
    conn.commit()


def record_trade(
    symbol: str, side: str, action: str, price: float, quantity: float,
    leverage: int = 1, pnl: float = 0, pnl_pct: float = 0, fee: float = 0,
    signal_grade: str = "", strategy: str = "", note: str = ""
):
    """거래 기록 저장"""
    conn = get_db()
    conn.execute(
        """INSERT INTO trades
        (timestamp, symbol, side, action, price, quantity, leverage, pnl, pnl_pct, fee, signal_grade, strategy, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (datetime.utcnow().isoformat(), symbol, side, action, price, quantity,
         leverage, pnl, pnl_pct, fee, signal_grade, strategy, note)
    )
    conn.commit()
    conn.close()


def update_daily_summary(date: str, total_pnl: float, total_pnl_pct: float,
                         trade_count: int, win_count: int, loss_count: int, balance: float):
    """일일 요약 저장/갱신"""
    conn = get_db()
    conn.execute(
        """INSERT OR REPLACE INTO daily_summary
        (date, total_pnl, total_pnl_pct, trade_count, win_count, loss_count, balance)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (date, total_pnl, total_pnl_pct, trade_count, win_count, loss_count, balance)
    )
    conn.commit()
    conn.close()


def get_recent_trades(hours: int = 6) -> list[dict]:
    """최근 N시간 거래 내역 조회"""
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM trades
        WHERE timestamp >= datetime('now', ?)
        ORDER BY timestamp DESC""",
        (f"-{hours} hours",)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_daily_stats(date_str: str) -> Optional[dict]:
    """특정 일자 요약 조회"""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM daily_summary WHERE date = ?", (date_str,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_trade_stats_since(hours: int = 24) -> dict:
    """최근 N시간 거래 통계"""
    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM trades
        WHERE timestamp >= datetime('now', ?) AND action = 'close'
        ORDER BY timestamp DESC""",
        (f"-{hours} hours",)
    ).fetchall()
    conn.close()

    trades = [dict(r) for r in rows]
    if not trades:
        return {
            "count": 0, "wins": 0, "losses": 0,
            "total_pnl": 0, "win_rate": 0,
            "best": 0, "worst": 0, "trades": [],
        }

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    return {
        "count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "total_pnl": sum(pnls),
        "win_rate": len(wins) / len(trades) * 100 if trades else 0,
        "best": max(pnls) if pnls else 0,
        "worst": min(pnls) if pnls else 0,
        "trades": trades,
    }


def get_state(key: str) -> Optional[str]:
    """봇 상태 조회"""
    conn = get_db()
    row = conn.execute("SELECT value FROM bot_state WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else None


def set_state(key: str, value: str):
    """봇 상태 저장"""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO bot_state (key, value, updated_at) VALUES (?, ?, ?)",
        (key, value, datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()
