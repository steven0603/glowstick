"""
交易系統模組
支援市價單（隔日開盤成交）與現價單（隔日觸價成交）。
"""

from __future__ import annotations
from datetime import date, timedelta

import database as db
import data_fetcher as fetcher
from config import BUY_FEE_RATE, SELL_FEE_RATE, SELL_TAX_RATE


# ── 費用計算 ──────────────────────────────────────────────────────────────────

def calc_buy_fee(price: float, quantity: float) -> float:
    """買進手續費。"""
    return price * quantity * BUY_FEE_RATE


def calc_sell_fee(price: float, quantity: float) -> float:
    """賣出手續費 + 交易稅。"""
    return price * quantity * (SELL_FEE_RATE + SELL_TAX_RATE)


def calc_buy_total(price: float, quantity: float) -> float:
    """買進總支出（含手續費）。"""
    return price * quantity * (1 + BUY_FEE_RATE)


def calc_sell_net(price: float, quantity: float) -> float:
    """賣出淨收入（扣手續費及交易稅）。"""
    return price * quantity * (1 - SELL_FEE_RATE - SELL_TAX_RATE)


# ── 委託下單 ──────────────────────────────────────────────────────────────────

def submit_market_order(
    stock_name: str,
    ticker: str,
    direction: str,   # "BUY" or "SELL"
    quantity: float,
    submitted_date: str | None = None,
) -> int:
    """
    送出市價單。
    成交時間：次一個有交易的交易日，成交價為開盤價。
    """
    today = submitted_date or date.today().isoformat()
    order_id = db.create_order(
        submitted_date=today,
        stock_name=stock_name,
        ticker=ticker,
        direction=direction.upper(),
        order_type="MARKET",
        quantity=quantity,
        notes="市價單",
    )
    print(f"✓ 已送出市價{'買進' if direction.upper()=='BUY' else '賣出'}委託 "
          f"#{order_id}  {stock_name}({ticker})  {quantity:.4f} 股")
    return order_id


def submit_limit_order(
    stock_name: str,
    ticker: str,
    direction: str,
    quantity: float,
    limit_price: float,
    submitted_date: str | None = None,
) -> int:
    """
    送出現價單。
    若隔日價格範圍涵蓋 limit_price 則成交；否則自動取消。
    """
    today = submitted_date or date.today().isoformat()
    order_id = db.create_order(
        submitted_date=today,
        stock_name=stock_name,
        ticker=ticker,
        direction=direction.upper(),
        order_type="LIMIT",
        quantity=quantity,
        limit_price=limit_price,
        notes=f"現價單 @ {limit_price:.2f}",
    )
    print(f"✓ 已送出現價{'買進' if direction.upper()=='BUY' else '賣出'}委託 "
          f"#{order_id}  {stock_name}({ticker})  {quantity:.4f} 股 @ {limit_price:.2f}")
    return order_id


# ── 執行委託 ──────────────────────────────────────────────────────────────────

def _execute_trade(order: dict, execution_price: float, execution_date: str):
    """
    執行一筆已成交的委託：
    - 更新持倉
    - 更新現金
    - 記錄 trade_log
    - 更新 order 狀態
    """
    ticker    = order["ticker"]
    name      = order["stock_name"]
    direction = order["direction"]
    qty       = order["quantity"]

    if direction == "BUY":
        fee     = calc_buy_fee(execution_price, qty)
        total   = calc_buy_total(execution_price, qty)
        net_twd = -total  # 現金流出

        # 更新持倉（加權平均成本）
        holdings = {h["ticker"]: h for h in db.get_holdings()}
        if ticker in holdings:
            old = holdings[ticker]
            new_shares = old["shares"] + qty
            new_cost   = (old["shares"] * old["avg_cost_twd"] + qty * execution_price) / new_shares
        else:
            new_shares = qty
            new_cost   = execution_price
        db.upsert_holding(name, ticker, new_shares, new_cost)

        # 扣現金
        cash = db.get_cash()
        db.set_cash(cash + net_twd)  # net_twd 為負

    else:  # SELL
        fee     = calc_sell_fee(execution_price, qty)
        net_twd = calc_sell_net(execution_price, qty)  # 現金流入

        # 減少持倉
        holdings = {h["ticker"]: h for h in db.get_holdings()}
        if ticker not in holdings:
            print(f"  [!] 無持倉 {name}({ticker})，無法賣出")
            db.update_order(order["id"], "CANCELLED")
            return
        old = holdings[ticker]
        new_shares = old["shares"] - qty
        if new_shares < -1e-6:
            print(f"  [!] {name} 持股不足（持有={old['shares']:.4f}，賣出={qty:.4f}），取消委託")
            db.update_order(order["id"], "CANCELLED")
            return
        db.upsert_holding(name, ticker, max(0.0, new_shares), old["avg_cost_twd"])

        # 加現金
        cash = db.get_cash()
        db.set_cash(cash + net_twd)

    # 記錄
    db.save_trade(execution_date, name, ticker, direction, qty,
                  execution_price, abs(fee), net_twd, order["id"])
    db.update_order(order["id"], "EXECUTED", execution_date, execution_price, abs(fee))

    arrow = "↑買入" if direction == "BUY" else "↓賣出"
    print(f"  {arrow} {name}({ticker})  {qty:.4f}股  @{execution_price:.2f}  "
          f"費用={abs(fee):,.0f}TWD")


def process_pending_orders(processing_date: str):
    """
    處理所有 PENDING 委託。
    - 市價單：以 processing_date 開盤價成交（僅在 submitted_date 隔日之後執行）
    - 現價單：若 processing_date 的 [low, high] 含 limit_price 則成交，否則取消
              現價單只有在 submitted_date 的隔一個交易日才有機會成交；未觸價當天即取消
    """
    orders = db.get_pending_orders()
    if not orders:
        return

    print(f"\n處理待執行委託（{processing_date}）...")
    executed = 0
    cancelled = 0

    for order in orders:
        # 核心規則：委託必須在送出日的「下一個交易日」才能執行，當天不得成交
        if order["submitted_date"] >= processing_date:
            continue

        ticker = order["ticker"]
        row = db.get_price(processing_date, ticker)

        if row is None:
            # 嘗試抓取
            fetched = fetcher.fetch_stock_single(ticker, processing_date)
            if fetched:
                db.save_price(processing_date, ticker,
                              fetched.get("open"), fetched.get("high"),
                              fetched.get("low"), fetched.get("close"),
                              fetched.get("volume"))
                row = db.get_price(processing_date, ticker)

        if row is None:
            print(f"  [!] #{order['id']} {order['stock_name']} 當日無行情，跳過")
            continue

        if order["order_type"] == "MARKET":
            exec_price = row.get("open")
            if exec_price is None:
                # open 欄位缺失時重新從 yfinance 抓取，確保用開盤價而非收盤價
                refetched = fetcher.fetch_stock_single(ticker, processing_date)
                if refetched:
                    exec_price = refetched.get("open")
                    if exec_price:
                        db.save_price(processing_date, ticker,
                                      refetched.get("open"), refetched.get("high"),
                                      refetched.get("low"), refetched.get("close"),
                                      refetched.get("volume"))
            if exec_price:
                _execute_trade(order, exec_price, processing_date)
                executed += 1
            else:
                print(f"  [!] #{order['id']} 市價單無開盤價，跳過")

        elif order["order_type"] == "LIMIT":
            limit_price = order["limit_price"]
            low   = row.get("low")  or row.get("close")
            high  = row.get("high") or row.get("close")
            direction = order["direction"]

            # 買進現價單：成交條件 = 股價跌至或低於限價
            # 賣出現價單：成交條件 = 股價漲至或高於限價
            triggered = False
            if direction == "BUY"  and low  is not None and low  <= limit_price:
                triggered = True
            if direction == "SELL" and high is not None and high >= limit_price:
                triggered = True

            if triggered:
                _execute_trade(order, limit_price, processing_date)
                executed += 1
            else:
                db.update_order(order["id"], "CANCELLED",
                                notes=f"當日區間 [{low:.2f},{high:.2f}] 未觸價")
                print(f"  ✗ #{order['id']} 現價單取消 {order['stock_name']} "
                      f"限價={limit_price:.2f}  當日範圍=[{low:.2f},{high:.2f}]")
                cancelled += 1

    print(f"  成交 {executed} 筆，取消 {cancelled} 筆\n")


# ── 查詢工具 ──────────────────────────────────────────────────────────────────

def get_pending_summary() -> list[dict]:
    return db.get_pending_orders()


def get_trade_history() -> list[dict]:
    return db.get_trade_history()
