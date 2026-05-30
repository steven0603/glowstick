"""
GlowStick X 投資競賽記錄系統
互動式 CLI 主程式
"""

from __future__ import annotations
import os
import sys
from datetime import date, timedelta

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm, IntPrompt, FloatPrompt
from rich.text import Text
from rich import box

import database as db
import data_fetcher as fetcher
import portfolio as pf
import trading as tr
import performance as perf
import reporter as rep
from config import (
    FUND_NAME, INITIAL_PORTFOLIO, START_DATE, END_DATE,
    REPORTS_DIR, INITIAL_CAPITAL_USD,
)


def _parse_date(s: str) -> str:
    """將使用者輸入的日期正規化為 YYYY-MM-DD。
    接受 YYYYMMDD、YYYY-MM-DD、YYYY/MM/DD 等格式。"""
    s = s.strip().replace("/", "-")
    if len(s) == 8 and "-" not in s:          # 20260323 → 2026-03-23
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    date.fromisoformat(s)                      # 驗證格式，不合法會 raise ValueError
    return s

console = Console()


# ── 頂欄 ─────────────────────────────────────────────────────────────────────

def print_header():
    today = date.today().isoformat()
    console.print(Panel(
        f"[bold cyan]{FUND_NAME}[/bold cyan]\n"
        f"[dim]空間叉叉投資信託(股)公司  ·  競賽期間：{START_DATE} ～ {END_DATE}[/dim]\n"
        f"[dim]今日：{today}[/dim]",
        box=box.DOUBLE_EDGE,
        expand=False,
    ))


def do_auto_catchup():
    """
    啟動時自動補齊缺漏日期的價格、匯率、TAIEX 及 NAV。
    從最後一筆 NAV 的隔天補到今日，完全自動，無需手動操作。
    """
    from datetime import timedelta
    today = date.today().isoformat()

    navs = db.get_nav_history(start_date=START_DATE)
    last_nav_date = navs[-1]["date"] if navs else None

    if last_nav_date and last_nav_date >= today:
        console.print("[dim]資料已是最新，無需補齊。[/dim]")
        return

    if last_nav_date:
        start_catchup = (date.fromisoformat(last_nav_date) + timedelta(days=1)).isoformat()
    else:
        start_catchup = START_DATE

    console.print(f"\n[bold yellow]自動補齊 {start_catchup} ～ {today} 的資料...[/bold yellow]")

    # 取得所有曾持有過的標的（當前持倉 + 歷史交易），確保補齊時不漏掉已賣出股票的歷史價格
    tickers = list({h["ticker"] for h in db.get_holdings()} |
                   {t["ticker"] for t in db.get_trade_history()})
    if not tickers:
        console.print("[red]尚無持股資料，請先執行初始化（選項 0）。[/red]")
        return

    # 批次抓取：匯率、TAIEX、所有個股
    fetcher.bulk_load_history(tickers, start_catchup, today)

    # 逐日處理委託 → 計算並儲存 NAV
    current = date.fromisoformat(start_catchup)
    end_dt  = date.fromisoformat(today)
    updated = 0

    while current <= end_dt:
        d = current.isoformat()
        tr.process_pending_orders(d)
        nav = pf.save_nav_for_date(d)
        if nav:
            updated += 1
        current += timedelta(days=1)

    console.print(f"[bold green]✓ 補齊完成，共更新 {updated} 個交易日淨值。[/bold green]\n")


def print_menu():
    console.print("\n[bold yellow]═══ 功能選單 ═══[/bold yellow]")
    console.print(" [cyan]1[/cyan]  更新今日價格 & 計算淨值")
    console.print(" [cyan]2[/cyan]  查看投資組合（持股表格）")
    console.print(" [cyan]3[/cyan]  查看基金 & TAIEX 歷史績效總表")
    console.print(" [cyan]4[/cyan]  生成每日 Markdown 紀錄")
    console.print(" [cyan]5[/cyan]  繪製報酬率折線圖")
    console.print(" [cyan]6[/cyan]  計算 Alpha（完整過程）")
    console.print(" [cyan]7[/cyan]  下委託單（買/賣）")
    console.print(" [cyan]8[/cyan]  查看待執行委託")
    console.print(" [cyan]9[/cyan]  查看交易紀錄")
    console.print(" [cyan]b[/cyan]  更新歷史匯率（台銀即期買賣均價 backfill）")
    console.print(" [cyan]0[/cyan]  重新初始化系統（重算歷史）")
    console.print(" [cyan]q[/cyan]  離開")
    console.print()


# ── 初始化 ────────────────────────────────────────────────────────────────────

def do_initialize():
    """完整初始化：建立 DB、抓歷史資料、計算歷史 NAV。"""
    console.print("\n[bold yellow]初始化系統...[/bold yellow]")

    db.init_db()

    # 批次抓取歷史資料
    tickers = [v["ticker"] for v in INITIAL_PORTFOLIO.values()]
    today   = date.today().isoformat()

    console.print(f"\n[bold]抓取歷史資料 {START_DATE} ～ {today}[/bold]")
    fetcher.bulk_load_history(tickers, START_DATE, today)

    # 初始化持倉（以 3/23 開盤價計算股數）
    pf.initialize_portfolio()

    # 重建歷史淨值
    pf.rebuild_nav_history(start=START_DATE)

    console.print("[bold green]✓ 初始化完成！[/bold green]\n")


# ── 每日更新 ──────────────────────────────────────────────────────────────────

def do_daily_update():
    raw    = Prompt.ask("更新日期（Enter = 今日）",
                        default=date.today().isoformat())
    try:
        target = _parse_date(raw)
    except ValueError:
        console.print(f"[red]日期格式錯誤：{raw}，請輸入 YYYY-MM-DD 或 YYYYMMDD[/red]")
        return
    console.print(f"\n[bold]抓取 {target} 資料...[/bold]")

    # 以 Trade Replay 取得該日正確持倉標的，確保不漏掉任何持股
    nav_pre = pf.calculate_nav(target)
    if nav_pre:
        tickers = [item["ticker"] for item in nav_pre["holdings_detail"] if item["ticker"]]
    else:
        tickers = [h["ticker"] for h in db.get_holdings()]
    result  = fetcher.update_today(tickers, target_date=target)

    # 處理當日待執行委託
    tr.process_pending_orders(target)

    # 計算並儲存淨值
    nav_info = pf.save_nav_for_date(target)

    if nav_info:
        rate = nav_info["exchange_rate"]
        usd  = nav_info["nav_usd"]
        twd  = nav_info["nav_twd"]

        # 計算報酬率
        p0 = INITIAL_CAPITAL_USD
        ret = (usd - p0) / p0

        console.print(f"\n  匯率：1 USD = [bold]{rate:.4f}[/bold] TWD")
        console.print(f"  TAIEX：[bold]{result.get('taiex', '-')}[/bold]")
        console.print(f"  基金淨值：[bold cyan]USD {usd:,.2f}[/bold cyan]（TWD {twd:,.0f}）")
        ret_color = "green" if ret >= 0 else "red"
        console.print(f"  累積報酬率：[bold {ret_color}]{ret*100:+.2f}%[/bold {ret_color}]")
    else:
        console.print("[red]淨值計算失敗，請確認當日資料是否存在。[/red]")


# ── 投資組合 ──────────────────────────────────────────────────────────────────

def do_portfolio():
    navs = db.get_nav_history(start_date=START_DATE)
    last_date = navs[-1]["date"] if navs else date.today().isoformat()
    raw = Prompt.ask("查詢日期（Enter = 最新）", default=last_date)
    try:
        target = _parse_date(raw)
    except ValueError:
        console.print(f"[red]日期格式錯誤：{raw}，請輸入 YYYY-MM-DD 或 YYYYMMDD[/red]")
        return
    rep.print_portfolio_table(target)


# ── TAIEX ─────────────────────────────────────────────────────────────────────

def do_taiex():
    rep.print_history_table()


# ── 每日 Markdown ─────────────────────────────────────────────────────────────

def do_markdown():
    navs = db.get_nav_history(start_date=START_DATE)
    last_date = navs[-1]["date"] if navs else date.today().isoformat()
    raw = Prompt.ask("生成日期（Enter = 最新）", default=last_date)
    try:
        target = _parse_date(raw)
    except ValueError:
        console.print(f"[red]日期格式錯誤：{raw}，請輸入 YYYY-MM-DD 或 YYYYMMDD[/red]")
        return

    md = rep.generate_daily_markdown(target)
    console.print("\n[bold yellow]══ Markdown 預覽 ══[/bold yellow]")
    console.print(md)

    if Confirm.ask("是否儲存到 reports/ 目錄？"):
        path = rep.save_daily_markdown(target)
        console.print(f"[green]✓ 已儲存：{path}[/green]")


# ── 折線圖 ────────────────────────────────────────────────────────────────────

def do_plot():
    rep.ensure_reports_dir()
    today = date.today().isoformat()
    path  = os.path.join(REPORTS_DIR, f"returns_{today}.png")
    result = rep.plot_returns(save_path=path)
    if result:
        console.print(f"[green]✓ 圖表已儲存：{result}[/green]")
        # 嘗試開啟
        if sys.platform == "darwin":
            os.system(f"open '{result}'")


# ── Alpha ────────────────────────────────────────────────────────────────────

def do_alpha():
    n1_str = Prompt.ask("競賽總組數（若不知可直接 Enter）", default="")
    n1 = int(n1_str) if n1_str.strip().isdigit() else None
    rep.print_alpha_calculation(n1=n1)


# ── 下委託單 ──────────────────────────────────────────────────────────────────

def do_submit_order():
    console.print("\n[bold yellow]── 下委託單 ──[/bold yellow]")

    # 顯示目前持股供選擇
    holdings = db.get_holdings()
    console.print("\n目前持股：")
    for i, h in enumerate(holdings, 1):
        short = h["ticker"].replace(".TW", "")
        console.print(f"  {i}. {h['name']} ({short})  持有 {h['shares']:,.4f} 股")

    console.print("\n  ─ 或輸入新標的 ─")

    ticker_input = Prompt.ask("Ticker（例如 2330.TW）或按上方編號選擇")

    # 允許用數字選擇
    if ticker_input.strip().isdigit():
        idx = int(ticker_input) - 1
        if 0 <= idx < len(holdings):
            h       = holdings[idx]
            ticker  = h["ticker"]
            name    = h["name"]
        else:
            console.print("[red]無效編號[/red]")
            return
    else:
        ticker = ticker_input.strip()
        name   = Prompt.ask("股票名稱（中文）")

    direction = Prompt.ask("方向", choices=["BUY", "SELL"], default="BUY")
    qty       = FloatPrompt.ask("股數（張×1000 或任意小數）")

    order_type = Prompt.ask("委託類型", choices=["MARKET", "LIMIT"], default="MARKET")

    if order_type == "MARKET":
        tr.submit_market_order(name, ticker, direction, qty)
    else:
        limit_price = FloatPrompt.ask("現價（TWD）")
        tr.submit_limit_order(name, ticker, direction, qty, limit_price)

    console.print("[green]委託已送出，將於次一交易日處理。[/green]")


# ── 待執行委託 ────────────────────────────────────────────────────────────────

def do_pending_orders():
    orders = db.get_pending_orders()
    if not orders:
        console.print("[dim]目前無待執行委託。[/dim]")
        return

    from rich.table import Table
    t = Table(title="待執行委託", box=box.ROUNDED)
    t.add_column("ID",     style="dim")
    t.add_column("送出日期")
    t.add_column("方向",   style="bold")
    t.add_column("類型")
    t.add_column("標的")
    t.add_column("股數",   justify="right")
    t.add_column("限價",   justify="right")

    for o in orders:
        color = "green" if o["direction"] == "BUY" else "red"
        t.add_row(
            str(o["id"]),
            o["submitted_date"],
            Text(o["direction"], style=color),
            o["order_type"],
            f"{o['stock_name']}({o['ticker'].replace('.TW','')})",
            f"{o['quantity']:,.4f}",
            f"{o['limit_price']:.2f}" if o["limit_price"] else "-",
        )
    console.print(t)


# ── 交易紀錄 ─────────────────────────────────────────────────────────────────

def do_trade_history():
    trades = db.get_trade_history()
    if not trades:
        console.print("[dim]尚無交易紀錄。[/dim]")
        return

    from rich.table import Table
    from rich.console import Console as RichConsole
    wide = RichConsole(width=220)
    t = Table(title="交易紀錄", box=box.ROUNDED)
    t.add_column("日期",        style="dim",  no_wrap=True)
    t.add_column("方向",        style="bold", no_wrap=True)
    t.add_column("標的",                      no_wrap=True)
    t.add_column("股數",        justify="right", no_wrap=True)
    t.add_column("成交價",      justify="right", no_wrap=True)
    t.add_column("手續費(TWD)", justify="right", no_wrap=True)
    t.add_column("淨額(TWD)",   justify="right", no_wrap=True)

    for tr_row in trades:
        color = "green" if tr_row["direction"] == "BUY" else "red"
        ticker_short = tr_row["ticker"].replace(".TWO", "").replace(".TW", "")
        t.add_row(
            tr_row["date"],
            Text(tr_row["direction"], style=color),
            f"{tr_row['stock_name']}({ticker_short})",
            f"{tr_row['quantity']:,.2f}",
            f"{tr_row['price_twd']:,.2f}",
            f"{tr_row['fee_twd']:,.0f}",
            f"{tr_row['net_twd']:+,.0f}",
        )
    wide.print(t)


# ── 台銀匯率 backfill ─────────────────────────────────────────────────────────

def do_bot_backfill():
    console.print("\n[bold yellow]── 台銀匯率 Backfill ──[/bold yellow]")
    console.print("將以台銀即期買賣均價覆蓋所有歷史匯率，並重算 TAIEX / NAV 的美元欄位。")
    if not Confirm.ask("確定執行？"):
        return
    n = fetcher.backfill_bot_exchange_rates()
    if n:
        console.print(f"[bold green]✓ 完成，共更新 {n} 筆匯率。[/bold green]")
    else:
        console.print("[red]取消或台銀資料無法取得。[/red]")


# ── 啟動快照 ──────────────────────────────────────────────────────────────────

def _show_startup_snapshot():
    """開啟時顯示最新淨值、各股收盤及功能選單。"""
    if not db.is_initialized():
        return

    navs = db.get_nav_history(start_date=START_DATE)
    if not navs:
        return

    last = navs[-1]
    p0   = INITIAL_CAPITAL_USD
    ret  = (last["nav_usd"] - p0) / p0

    from rich.table import Table
    from rich.columns import Columns

    # ── 淨值摘要面板 ──
    ret_color = "green" if ret >= 0 else "red"
    console.print(Panel(
        f"[bold]最新淨值[/bold]  [cyan]{last['date']}[/cyan]\n"
        f"USD [bold cyan]{last['nav_usd']:,.2f}[/bold cyan]   "
        f"TWD {last['nav_twd']:,.0f}\n"
        f"累積報酬  [{ret_color}][bold]{ret*100:+.2f}%[/bold][/{ret_color}]   "
        f"匯率 {last['exchange_rate']:.4f} TWD/USD",
        title="GlowStick X",
        border_style="cyan",
        expand=False,
    ))

    # ── 持倉快照表格 ──
    import json
    try:
        with db.get_db() as conn:
            snap_row = conn.execute(
                "SELECT value FROM system_state WHERE key='initial_snapshot'"
            ).fetchone()
        if not snap_row:
            return
        initial    = json.loads(snap_row["value"])
        all_trades = sorted(db.get_trade_history(), key=lambda x: x["date"])

        holdings_dict = {t: dict(h) for t, h in initial["holdings"].items()}
        cash_twd = initial["cash_twd"]
        date_str = last["date"]

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
                    nc  = (old["shares"]*old["avg_cost_twd"] + qty*price) / ns
                    holdings_dict[ticker] = {**old, "shares": ns, "avg_cost_twd": nc}
                else:
                    holdings_dict[ticker] = {"name": trade["stock_name"], "ticker": ticker,
                                              "shares": qty, "avg_cost_twd": price}
                cash_twd += net
            else:
                if ticker in holdings_dict:
                    holdings_dict[ticker]["shares"] = max(0, holdings_dict[ticker]["shares"] - qty)
                cash_twd += net

        rate = last["exchange_rate"]
        detail = []
        total_stock_twd = 0
        for ticker, h in holdings_dict.items():
            if h["shares"] <= 0:
                continue
            # forward fill：若當日無收盤價，沿用最近一筆
            row = db.get_price(date_str, ticker)
            if row and row.get("close"):
                close = row["close"]
            else:
                last_p = db.get_last_price(ticker, before_date=date_str)
                close = last_p["close"] if last_p else 0
            val_twd = h["shares"] * close
            total_stock_twd += val_twd
            pnl_pct = (close - h["avg_cost_twd"]) / h["avg_cost_twd"] * 100 if h["avg_cost_twd"] else 0
            detail.append({"name": h["name"], "ticker": ticker,
                           "close": close, "value_twd": val_twd,
                           "value_usd": val_twd/rate, "pnl_pct": pnl_pct})

        nav_total = total_stock_twd + cash_twd
        detail.sort(key=lambda x: -x["value_twd"])

        short = lambda t: t.replace(".TWO","").replace(".TW","")
        t = Table(title=f"持股明細  {date_str}", box=box.SIMPLE_HEAVY,
                  show_footer=True, title_style="bold")
        t.add_column("標的",    style="bold", footer="現金")
        t.add_column("Ticker",  style="dim")
        t.add_column("收盤價",  justify="right")
        t.add_column("損益%",   justify="right")
        t.add_column("比重",    justify="right",
                     footer=f"{cash_twd/nav_total*100:.1f}%")
        t.add_column("美元股權(USD)", justify="right",
                     footer=f"{cash_twd/rate:,.0f}")

        for item in detail:
            w = item["value_twd"] / nav_total * 100
            pct_style = "green" if item["pnl_pct"] >= 0 else "red"
            t.add_row(
                item["name"],
                short(item["ticker"]),
                f"{item['close']:,.2f}",
                Text(f"{item['pnl_pct']:+.1f}%", style=pct_style),
                f"{w:.1f}%",
                f"{item['value_usd']:,.0f}",
            )
        console.print(t)

    except Exception:
        pass  # 快照失敗不中斷主流程

    console.rule()


# ── 主迴圈 ────────────────────────────────────────────────────────────────────

def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    db.init_db()
    print_header()

    if not db.is_initialized():
        console.print("\n[bold yellow]首次執行，開始初始化系統...[/bold yellow]")
        if Confirm.ask("是否立即初始化（將從 Yahoo Finance 抓取歷史資料）？"):
            do_initialize()
        else:
            console.print("[dim]請手動選擇 '0' 進行初始化後再使用其他功能。[/dim]")
    else:
        # 將初始 3/23 買入記錄補入 trade_log（只跑一次）
        pf.add_initial_trades_to_log()
        # 每次開啟自動補齊到今日
        do_auto_catchup()

    # 開啟時先顯示最新淨值快照
    _show_startup_snapshot()

    while True:
        print_menu()
        choice = Prompt.ask("[bold]請選擇功能[/bold]",
                            choices=["0","1","2","3","4","5","6","7","8","9","b","q"],
                            default="1")

        if choice == "q":
            console.print("[dim]再見！[/dim]")
            break
        elif choice == "0":
            if Confirm.ask("[red]確定重新初始化？這將重算所有歷史資料[/red]"):
                do_initialize()
        elif choice == "1":
            do_daily_update()
        elif choice == "2":
            do_portfolio()
        elif choice == "3":
            do_taiex()
        elif choice == "4":
            do_markdown()
        elif choice == "5":
            do_plot()
        elif choice == "6":
            do_alpha()
        elif choice == "7":
            do_submit_order()
        elif choice == "8":
            do_pending_orders()
        elif choice == "9":
            do_trade_history()
        elif choice == "b":
            do_bot_backfill()


if __name__ == "__main__":
    main()
