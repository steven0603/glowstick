"""
投資組合管理模組
負責初始化持股、計算淨值、檢查投資限制。
"""

from __future__ import annotations
import json
from datetime import date

import database as db
import data_fetcher as fetcher
from config import (
    INITIAL_PORTFOLIO, CASH_INITIAL_WEIGHT,
    INITIAL_CAPITAL_USD, START_DATE,
    BUY_FEE_RATE, MAX_CASH_RATIO, MAX_SINGLE_ASSET_RATIO,
)


# ── 初始化 ────────────────────────────────────────────────────────────────────

def initialize_portfolio():
    """
    以 START_DATE 開盤價買入初始持股。
    全數 $1億USD 先換成 TWD，再依權重分配。
    """
    print(f"\n初始化投資組合（{START_DATE}）...")

    # 1. 取得初始換匯匯率（以開盤日匯率）
    fx = fetcher.fetch_exchange_rate_single(START_DATE)
    if fx is None:
        raise RuntimeError("無法取得 2026-03-23 匯率，請確認網路連線。")
    print(f"  換匯匯率：1 USD = {fx:.4f} TWD")

    total_twd = INITIAL_CAPITAL_USD * fx
    print(f"  初始台幣資金：{total_twd:,.0f} TWD")

    # 2. 儲存匯率
    db.save_exchange_rate(START_DATE, fx)

    # 3. 逐一計算各股買入股數
    tickers = [v["ticker"] for v in INITIAL_PORTFOLIO.values()]
    fetcher.bulk_load_history(tickers, START_DATE, START_DATE)

    cash_twd = total_twd * CASH_INITIAL_WEIGHT
    print(f"\n  初始現金（10%）：{cash_twd:,.0f} TWD")

    for name, info in INITIAL_PORTFOLIO.items():
        ticker = info["ticker"]
        weight = info["weight"]
        alloc_twd = total_twd * weight

        row = db.get_price(START_DATE, ticker)
        if row is None or row.get("open") is None:
            print(f"  [!] {name} ({ticker}) 無開盤價，跳過")
            cash_twd += alloc_twd
            continue

        open_price = row["open"]
        # 買入股數（含手續費）
        shares = alloc_twd / (open_price * (1 + BUY_FEE_RATE))
        actual_cost = shares * open_price * (1 + BUY_FEE_RATE)
        leftover = alloc_twd - actual_cost
        cash_twd += leftover

        db.upsert_holding(name, ticker, shares, open_price)
        print(f"  ✓ {name:8s} {weight*100:.1f}%  開盤價={open_price:.2f}  股數={shares:,.4f}")

    db.set_cash(cash_twd)
    print(f"\n  最終現金餘額：{cash_twd:,.0f} TWD")
    db.set_initialized()
    print("初始化完成。\n")


# ── 歷史持倉重建 ──────────────────────────────────────────────────────────────

def _get_holdings_and_cash_at_date(date_str: str) -> tuple[list[dict], float]:
    """
    依 date_str 重建當時的持倉與現金（從 initial_snapshot 重播交易紀錄）。
    回傳 (holdings_list, cash_twd)。
    """
    with db.get_db() as conn:
        snap_row = conn.execute(
            "SELECT value FROM system_state WHERE key='initial_snapshot'"
        ).fetchone()

    if not snap_row:
        # 無快照則退回目前持倉
        return db.get_holdings(), db.get_cash()

    initial = json.loads(snap_row["value"])
    holdings_dict: dict[str, dict] = {t: dict(h) for t, h in initial["holdings"].items()}
    cash_twd: float = initial["cash_twd"]

    all_trades = sorted(db.get_trade_history(), key=lambda x: x["date"])
    for trade in all_trades:
        if trade["date"] > date_str:
            break
        ticker = trade["ticker"]
        qty    = trade["quantity"]
        price  = trade["price_twd"]
        net    = trade["net_twd"]
        if trade["direction"] == "BUY":
            if ticker in holdings_dict:
                old = holdings_dict[ticker]
                ns  = old["shares"] + qty
                nc  = (old["shares"] * old["avg_cost_twd"] + qty * price) / ns
                holdings_dict[ticker] = {**old, "shares": ns, "avg_cost_twd": nc}
            else:
                holdings_dict[ticker] = {
                    "name": trade["stock_name"], "ticker": ticker,
                    "shares": qty, "avg_cost_twd": price,
                }
            cash_twd += net
        else:
            if ticker in holdings_dict:
                holdings_dict[ticker]["shares"] = max(0.0, holdings_dict[ticker]["shares"] - qty)
            cash_twd += net

    holdings_list = [
        {"name": h["name"], "ticker": h["ticker"],
         "shares": h["shares"], "avg_cost_twd": h["avg_cost_twd"]}
        for h in holdings_dict.values() if h["shares"] > 0
    ]
    return holdings_list, cash_twd


# ── 淨值計算 ──────────────────────────────────────────────────────────────────

def calculate_nav(date_str: str) -> dict | None:
    """
    計算指定日期的基金淨值（台幣及美元）。
    回傳 {"nav_twd", "nav_usd", "exchange_rate", "holdings_detail"}
    或 None（若資料不足）。
    """
    holdings, cash_twd = _get_holdings_and_cash_at_date(date_str)

    # 取匯率
    rate = db.get_exchange_rate(date_str)
    if rate is None:
        last = db.get_last_exchange_rate(before_date=date_str)
        rate = last["rate"] if last else None
    if rate is None:
        return None

    total_stock_twd = 0.0
    detail = []

    for h in holdings:
        ticker = h["ticker"]
        shares = h["shares"]

        row = db.get_price(date_str, ticker)
        if row is None or row.get("close") is None:
            # forward fill
            last_p = db.get_last_price(ticker, before_date=date_str)
            close = last_p["close"] if last_p else 0.0
        else:
            close = row["close"]

        value_twd = shares * close
        total_stock_twd += value_twd
        detail.append({
            "name":       h["name"],
            "ticker":     ticker,
            "shares":     shares,
            "close_twd":  close,
            "value_twd":  value_twd,
            "value_usd":  value_twd / rate,
        })

    nav_twd = total_stock_twd + cash_twd
    nav_usd = nav_twd / rate

    # 加入現金欄位
    detail.append({
        "name":      "現金",
        "ticker":    None,
        "shares":    None,
        "close_twd": None,
        "value_twd": cash_twd,
        "value_usd": cash_twd / rate,
    })

    return {
        "nav_twd":        nav_twd,
        "nav_usd":        nav_usd,
        "exchange_rate":  rate,
        "holdings_detail": detail,
    }


def save_nav_for_date(date_str: str) -> dict | None:
    """計算並儲存指定日期淨值。"""
    nav_info = calculate_nav(date_str)
    if nav_info:
        db.save_nav(date_str, nav_info["nav_twd"],
                    nav_info["nav_usd"], nav_info["exchange_rate"])
    return nav_info


def rebuild_nav_history(start: str | None = None):
    """
    重建從 start 到今日的所有歷史淨值。
    用於初始化時批次計算。
    """
    from datetime import timedelta
    start = start or START_DATE
    today = date.today().isoformat()
    current = date.fromisoformat(start)
    end     = date.fromisoformat(today)

    print(f"  重建歷史淨值 {start} ~ {today}...")
    count = 0
    while current <= end:
        d = current.isoformat()
        # 只計算有匯率資料的日子
        rate = db.get_exchange_rate(d)
        if rate:
            nav = calculate_nav(d)
            if nav:
                db.save_nav(d, nav["nav_twd"], nav["nav_usd"], nav["exchange_rate"])
                count += 1
        current += timedelta(days=1)
    print(f"  → 共計算 {count} 筆淨值")


# ── 投資組合持股比例 ───────────────────────────────────────────────────────────

def get_portfolio_weights(date_str: str) -> dict[str, float]:
    """回傳各標的佔總淨值比例。"""
    nav_info = calculate_nav(date_str)
    if not nav_info:
        return {}
    total = nav_info["nav_twd"]
    if total == 0:
        return {}
    return {
        item["name"]: item["value_twd"] / total
        for item in nav_info["holdings_detail"]
    }


# ── 投資限制檢查 ──────────────────────────────────────────────────────────────

def check_constraints(date_str: str) -> list[str]:
    """
    檢查是否違反公開說明書投資限制。
    回傳違規說明清單（空清單表示無違規）。
    """
    nav_info = calculate_nav(date_str)
    if not nav_info:
        return []

    total    = nav_info["nav_twd"]
    detail   = nav_info["holdings_detail"]
    warnings = []

    # 現金上限 30%
    cash_item = next((x for x in detail if x["name"] == "現金"), None)
    if cash_item:
        cash_ratio = cash_item["value_twd"] / total
        if cash_ratio > MAX_CASH_RATIO:
            warnings.append(
                f"❌ 現金比例 {cash_ratio:.1%} 超過上限 {MAX_CASH_RATIO:.0%}"
            )

    # 單一資產上限 30%
    stock_items = [x for x in detail if x["name"] != "現金"]
    for item in stock_items:
        ratio = item["value_twd"] / total
        if ratio > MAX_SINGLE_ASSET_RATIO:
            warnings.append(
                f"❌ {item['name']} 比例 {ratio:.1%} 超過單一資產上限 {MAX_SINGLE_ASSET_RATIO:.0%}"
            )

    # 資產種類 10~20 種
    n = len(stock_items)
    if n < 10:
        warnings.append(f"❌ 持股種類 {n} 種，低於最低要求 10 種")
    if n > 20:
        warnings.append(f"❌ 持股種類 {n} 種，超過上限 20 種")

    return warnings
