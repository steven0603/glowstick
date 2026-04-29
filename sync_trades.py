"""
交易同步腳本 — 一鍵補齊 3/23 之後的所有交易並重算 NAV

使用方式（在 glowstick/ 目錄下執行）：
    python3 sync_trades.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database as db
import portfolio as pf
from config import BUY_FEE_RATE, SELL_FEE_RATE, SELL_TAX_RATE
from datetime import date, timedelta

# ── 需要補齊的交易清單 ─────────────────────────────────────────────────────────
# 格式：(submitted_date, execution_date, stock_name, ticker, direction, order_type, quantity, price_twd)

MISSING_ORDERS = [
    # 3/30 加碼買入
    ("2026-03-28", "2026-03-30", "萬泰科", "6190.TWO", "BUY",  "MARKET", 1_178_942.0, 53.60),
    ("2026-03-28", "2026-03-30", "啟碁",   "6285.TW",  "BUY",  "MARKET",   205_659.0, 186.00),
    ("2026-03-28", "2026-03-30", "訊芯-KY","6451.TW",  "BUY",  "MARKET",   263_428.0, 347.00),
    # 4/13 賣出台揚
    ("2026-04-13", "2026-04-13", "台揚",   "2314.TW",  "SELL", "MARKET", 2_658_425.0,  12.30),
    # 4/20 換股
    ("2026-04-19", "2026-04-20", "建準",   "2421.TW",  "SELL", "MARKET",   695_429.0, 147.00),
    ("2026-04-19", "2026-04-20", "同欣電", "6271.TW",  "SELL", "MARKET",   639_795.0, 187.00),
    ("2026-04-19", "2026-04-20", "奇鋐",   "3017.TW",  "SELL", "MARKET",    50_350.0, 2410.00),
    ("2026-04-19", "2026-04-20", "尖點",   "8021.TW",  "BUY",  "MARKET",   278_508.0, 402.00),
    ("2026-04-19", "2026-04-20", "雙鴻",   "3324.TWO", "BUY",  "MARKET",   116_700.0, 1040.00),
    # 4/27 買入順德
    ("2026-04-26", "2026-04-27", "順德",   "2351.TW",  "BUY",  "MARKET",   278_000.0, 150.00),
]


def _calc_fee(direction, price, qty):
    if direction == "BUY":
        return price * qty * BUY_FEE_RATE
    else:
        return price * qty * (SELL_FEE_RATE + SELL_TAX_RATE)


def _calc_net(direction, price, qty, fee):
    if direction == "BUY":
        return -(price * qty + fee)
    else:
        return price * qty - fee


def main():
    db.init_db()

    # 已存在的 trade_log 日期範圍
    existing = {(t["date"], t["ticker"], t["direction"]) for t in db.get_trade_history()}

    inserted = 0
    for sub_date, exec_date, name, ticker, direction, order_type, qty, price in MISSING_ORDERS:
        key = (exec_date, ticker, direction)
        if key in existing:
            print(f"  [跳過] {exec_date} {direction} {name} 已存在")
            continue

        fee = _calc_fee(direction, price, qty)
        net = _calc_net(direction, price, qty, fee)

        # 寫入 orders
        with db.get_db() as conn:
            cur = conn.execute("""
                INSERT INTO orders
                (submitted_date, stock_name, ticker, direction, order_type,
                 quantity, status, execution_date, execution_price, fee_twd, notes)
                VALUES (?,?,?,?,?,?,'EXECUTED',?,?,?,?)
            """, (sub_date, name, ticker, direction, order_type,
                  qty, exec_date, price, abs(fee), "sync_trades 補入"))
            order_id = cur.lastrowid

        # 寫入 trade_log
        db.save_trade(exec_date, name, ticker, direction, qty, price, abs(fee), net, order_id)

        print(f"  ✓ {exec_date} {direction:4s} {name}({ticker})  {qty:,.0f}股 @{price:.2f}  "
              f"費={abs(fee):,.0f}  淨={net:+,.0f}")
        inserted += 1

    print(f"\n共補入 {inserted} 筆交易\n")

    # ── 重建 holdings & cash ──────────────────────────────────────────────────
    print("重建持倉與現金...")
    today = date.today().isoformat()
    holdings, cash = pf._get_holdings_and_cash_at_date(today)

    # 清空 holdings 再重寫
    with db.get_db() as conn:
        conn.execute("DELETE FROM holdings")
    for h in holdings:
        db.upsert_holding(h["name"], h["ticker"], h["shares"], h["avg_cost_twd"])
    db.set_cash(cash)
    print(f"  現金：{cash:,.0f} TWD")
    print(f"  持倉：{len(holdings)} 檔")

    # ── 重算所有受影響日期的 NAV ──────────────────────────────────────────────
    print("\n重算 NAV（2026-03-30 起）...")
    start = date.fromisoformat("2026-03-30")
    end   = date.fromisoformat(today)
    cur   = start
    count = 0
    while cur <= end:
        d   = cur.isoformat()
        nav = pf.save_nav_for_date(d)
        if nav:
            count += 1
        cur += timedelta(days=1)
    print(f"  → 重算 {count} 筆 NAV\n")

    print("✅ 同步完成！請重新啟動 python3 main.py 確認持倉。")


if __name__ == "__main__":
    main()
