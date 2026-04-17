"""
資料庫管理模組 — SQLite
"""

import sqlite3
from contextlib import contextmanager
from config import DB_PATH


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """建立所有資料表。"""
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS holdings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            ticker     TEXT    NOT NULL,
            shares     REAL    NOT NULL DEFAULT 0,
            avg_cost_twd REAL  NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS cash (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            amount_twd REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS price_history (
            date   TEXT NOT NULL,
            ticker TEXT NOT NULL,
            open   REAL,
            high   REAL,
            low    REAL,
            close  REAL,
            volume REAL,
            PRIMARY KEY (date, ticker)
        );

        CREATE TABLE IF NOT EXISTS exchange_rate_history (
            date TEXT PRIMARY KEY,
            rate REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS nav_history (
            date          TEXT PRIMARY KEY,
            nav_twd       REAL NOT NULL,
            nav_usd       REAL NOT NULL,
            exchange_rate REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS taiex_history (
            date      TEXT PRIMARY KEY,
            close     REAL NOT NULL,
            close_usd REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            submitted_date  TEXT NOT NULL,
            stock_name      TEXT NOT NULL,
            ticker          TEXT NOT NULL,
            direction       TEXT NOT NULL,
            order_type      TEXT NOT NULL,
            quantity        REAL NOT NULL,
            limit_price     REAL,
            status          TEXT NOT NULL DEFAULT 'PENDING',
            execution_date  TEXT,
            execution_price REAL,
            fee_twd         REAL,
            notes           TEXT
        );

        CREATE TABLE IF NOT EXISTS trade_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            ticker     TEXT NOT NULL,
            direction  TEXT NOT NULL,
            quantity   REAL NOT NULL,
            price_twd  REAL NOT NULL,
            fee_twd    REAL NOT NULL,
            net_twd    REAL NOT NULL,
            order_id   INTEGER REFERENCES orders(id)
        );

        CREATE TABLE IF NOT EXISTS system_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)


# ── System state ────────────────────────────────────────────────────────────

def is_initialized() -> bool:
    with get_db() as conn:
        row = conn.execute(
            "SELECT value FROM system_state WHERE key='initialized'"
        ).fetchone()
    return row is not None and row["value"] == "true"


def set_initialized():
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO system_state (key,value) VALUES ('initialized','true')"
        )


# ── Holdings ────────────────────────────────────────────────────────────────

def get_holdings() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM holdings WHERE shares > 0 ORDER BY name"
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_holding(name: str, ticker: str, shares: float, avg_cost_twd: float):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO holdings (name, ticker, shares, avg_cost_twd)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                ticker=excluded.ticker,
                shares=excluded.shares,
                avg_cost_twd=excluded.avg_cost_twd
        """, (name, ticker, shares, avg_cost_twd))


def get_cash() -> float:
    with get_db() as conn:
        row = conn.execute("SELECT amount_twd FROM cash WHERE id=1").fetchone()
    return row["amount_twd"] if row else 0.0


def set_cash(amount_twd: float):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO cash (id, amount_twd) VALUES (1, ?)",
            (amount_twd,)
        )


# ── Price history ────────────────────────────────────────────────────────────

def save_price(date_str, ticker, open_p, high_p, low_p, close_p, volume=None):
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO price_history
            (date, ticker, open, high, low, close, volume)
            VALUES (?,?,?,?,?,?,?)
        """, (date_str, ticker, open_p, high_p, low_p, close_p, volume))


def get_price(date_str: str, ticker: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM price_history WHERE date=? AND ticker=?",
            (date_str, ticker)
        ).fetchone()
    return dict(row) if row else None


def get_last_price(ticker: str, before_date: str | None = None) -> dict | None:
    with get_db() as conn:
        if before_date:
            row = conn.execute(
                "SELECT * FROM price_history WHERE ticker=? AND date<=? ORDER BY date DESC LIMIT 1",
                (ticker, before_date)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM price_history WHERE ticker=? ORDER BY date DESC LIMIT 1",
                (ticker,)
            ).fetchone()
    return dict(row) if row else None


def get_all_prices_on_date(date_str: str) -> dict[str, dict]:
    """回傳 {ticker: row} 該日所有已存價格。"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM price_history WHERE date=?", (date_str,)
        ).fetchall()
    return {r["ticker"]: dict(r) for r in rows}


def get_price_series(ticker: str, start_date: str, end_date: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM price_history WHERE ticker=? AND date>=? AND date<=? ORDER BY date",
            (ticker, start_date, end_date)
        ).fetchall()
    return [dict(r) for r in rows]


# ── Exchange rate ────────────────────────────────────────────────────────────

def save_exchange_rate(date_str: str, rate: float):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO exchange_rate_history (date, rate) VALUES (?,?)",
            (date_str, rate)
        )


def get_exchange_rate(date_str: str) -> float | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT rate FROM exchange_rate_history WHERE date=?", (date_str,)
        ).fetchone()
    return row["rate"] if row else None


def get_last_exchange_rate(before_date: str | None = None) -> dict | None:
    with get_db() as conn:
        if before_date:
            row = conn.execute(
                "SELECT * FROM exchange_rate_history WHERE date<=? ORDER BY date DESC LIMIT 1",
                (before_date,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM exchange_rate_history ORDER BY date DESC LIMIT 1"
            ).fetchone()
    return dict(row) if row else None


def get_exchange_rate_series(start_date: str, end_date: str) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM exchange_rate_history WHERE date>=? AND date<=? ORDER BY date",
            (start_date, end_date)
        ).fetchall()
    return [dict(r) for r in rows]


# ── NAV history ──────────────────────────────────────────────────────────────

def save_nav(date_str: str, nav_twd: float, nav_usd: float, exchange_rate: float):
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO nav_history (date, nav_twd, nav_usd, exchange_rate)
            VALUES (?,?,?,?)
        """, (date_str, nav_twd, nav_usd, exchange_rate))


def get_nav(date_str: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM nav_history WHERE date=?", (date_str,)
        ).fetchone()
    return dict(row) if row else None


def get_nav_history(start_date: str | None = None) -> list[dict]:
    with get_db() as conn:
        if start_date:
            rows = conn.execute(
                "SELECT * FROM nav_history WHERE date>=? ORDER BY date", (start_date,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM nav_history ORDER BY date"
            ).fetchall()
    return [dict(r) for r in rows]


# ── TAIEX ────────────────────────────────────────────────────────────────────

def save_taiex(date_str: str, close: float, close_usd: float):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO taiex_history (date, close, close_usd) VALUES (?,?,?)",
            (date_str, close, close_usd)
        )


def get_taiex_history(start_date: str | None = None) -> list[dict]:
    with get_db() as conn:
        if start_date:
            rows = conn.execute(
                "SELECT * FROM taiex_history WHERE date>=? ORDER BY date", (start_date,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM taiex_history ORDER BY date").fetchall()
    return [dict(r) for r in rows]


# ── Orders ───────────────────────────────────────────────────────────────────

def create_order(submitted_date, stock_name, ticker, direction,
                 order_type, quantity, limit_price=None, notes=None) -> int:
    with get_db() as conn:
        cur = conn.execute("""
            INSERT INTO orders
            (submitted_date, stock_name, ticker, direction, order_type,
             quantity, limit_price, status, notes)
            VALUES (?,?,?,?,?,?,?,'PENDING',?)
        """, (submitted_date, stock_name, ticker, direction, order_type,
              quantity, limit_price, notes))
    return cur.lastrowid


def get_pending_orders() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE status='PENDING' ORDER BY submitted_date, id"
        ).fetchall()
    return [dict(r) for r in rows]


def update_order(order_id: int, status: str,
                 execution_date: str | None = None,
                 execution_price: float | None = None,
                 fee_twd: float | None = None,
                 notes: str | None = None):
    with get_db() as conn:
        conn.execute("""
            UPDATE orders SET status=?, execution_date=?, execution_price=?, fee_twd=?,
                notes=COALESCE(?, notes)
            WHERE id=?
        """, (status, execution_date, execution_price, fee_twd, notes, order_id))


def get_all_orders() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY submitted_date DESC, id DESC").fetchall()
    return [dict(r) for r in rows]


# ── Trade log ────────────────────────────────────────────────────────────────

def save_trade(date_str, stock_name, ticker, direction,
               quantity, price_twd, fee_twd, net_twd, order_id=None):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO trade_log
            (date, stock_name, ticker, direction, quantity, price_twd, fee_twd, net_twd, order_id)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (date_str, stock_name, ticker, direction, quantity,
              price_twd, fee_twd, net_twd, order_id))


def get_trade_history() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM trade_log ORDER BY date, id").fetchall()
    return [dict(r) for r in rows]
