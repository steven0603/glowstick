"""
報告生成模組
- Markdown 每日紀錄
- 投資組合表格（Rich）
- TAIEX 表格
- 報酬率折線圖
- Alpha 完整計算展示
"""

from __future__ import annotations
import os
from datetime import date

import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib import rcParams
from tabulate import tabulate
from rich.console import Console
from rich.table import Table
from rich import box
from rich.text import Text

import database as db
import portfolio as pf
import performance as perf
from config import (
    FUND_NAME, BENCHMARK_NAME, INITIAL_CAPITAL_USD,
    START_DATE, REPORTS_DIR,
)

console = Console()

# 設定字型（支援中文）
rcParams["font.family"] = ["Arial Unicode MS", "PingFang TC",
                            "Microsoft JhengHei", "sans-serif"]
rcParams["axes.unicode_minus"] = False


# ── 工具 ─────────────────────────────────────────────────────────────────────

def _ticker_short(ticker: str | None) -> str:
    """2330.TW → 2330，3491.TWO → 3491"""
    if ticker is None:
        return ""
    return ticker.replace(".TWO", "").replace(".TW", "")


def _pct(v: float | None, decimals: int = 2) -> str:
    if v is None:
        return "-"
    return f"{v*100:+.{decimals}f}%"


def _fmt_num(v: float | None, decimals: int = 0) -> str:
    if v is None:
        return "-"
    return f"{v:,.{decimals}f}"


def ensure_reports_dir():
    os.makedirs(REPORTS_DIR, exist_ok=True)


# ── Markdown 每日紀錄 ─────────────────────────────────────────────────────────

def generate_daily_markdown(date_str: str) -> str:
    """
    生成競賽格式的每日投資紀錄 Markdown。
    格式：M/D 價格更新：
          股票名稱 ticker ( 收盤價 )、...、現金
          台幣美元匯率：XX.XX
    """
    nav_info = pf.calculate_nav(date_str)
    if nav_info is None:
        return f"# {date_str} — 無資料\n"

    d = date.fromisoformat(date_str)
    date_label = f"{d.month}/{d.day}"

    items = []
    for item in nav_info["holdings_detail"]:
        if item["name"] == "現金":
            items.append("現金")
        else:
            short = _ticker_short(item["ticker"])
            close = item["close_twd"]
            items.append(f"{item['name']} {short} ( {close:.2f} )")

    price_line = "、".join(items)

    rate = nav_info["exchange_rate"]
    nav_usd = nav_info["nav_usd"]
    nav_twd = nav_info["nav_twd"]

    # 計算報酬率
    ret = (nav_usd - INITIAL_CAPITAL_USD) / INITIAL_CAPITAL_USD

    md = f"""{date_label} 價格更新：
{price_line}

台幣美元匯率：{rate:.2f}

> 基金淨值：USD {nav_usd:,.2f}（TWD {nav_twd:,.0f}）｜累積報酬率：{ret*100:+.2f}%
"""
    return md


def save_daily_markdown(date_str: str) -> str:
    ensure_reports_dir()
    md = generate_daily_markdown(date_str)
    path = os.path.join(REPORTS_DIR, f"daily_{date_str}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


# ── 投資組合表格 ──────────────────────────────────────────────────────────────

def print_portfolio_table(date_str: str):
    """以 Rich 表格印出當日持股詳情。"""
    nav_info = pf.calculate_nav(date_str)
    if nav_info is None:
        console.print(f"[red]無法取得 {date_str} 資料[/red]")
        return

    total_twd = nav_info["nav_twd"]
    total_usd = nav_info["nav_usd"]
    rate      = nav_info["exchange_rate"]

    d = date.fromisoformat(date_str)
    title = f"{FUND_NAME}  [{d.month}/{d.day}]"

    t = Table(title=title, box=box.ROUNDED, show_footer=True,
              title_style="bold cyan")
    t.add_column("標的",     style="bold",     footer="合計")
    t.add_column("Ticker",   style="dim")
    t.add_column("收盤價\n(TWD)", justify="right")
    t.add_column("持股比例",  justify="right")
    t.add_column("台幣股權\n(TWD)",    justify="right", footer=f"{total_twd:,.0f}")
    t.add_column(f"美元股權\n(USD)\n匯率={rate:.2f}", justify="right",
                 footer=f"{total_usd:,.2f}")

    for item in nav_info["holdings_detail"]:
        weight = item["value_twd"] / total_twd if total_twd else 0
        t.add_row(
            item["name"],
            _ticker_short(item["ticker"]),
            f"{item['close_twd']:.2f}" if item["close_twd"] else "-",
            f"{weight*100:.2f}%",
            f"{item['value_twd']:,.0f}",
            f"{item['value_usd']:,.2f}",
        )

    console.print(t)

    # 投資限制檢查
    warnings = pf.check_constraints(date_str)
    if warnings:
        console.print("\n[bold red]⚠ 投資限制違規：[/bold red]")
        for w in warnings:
            console.print(f"  {w}")
    else:
        console.print("\n[green]✓ 所有投資限制均符合規定[/green]")


# ── TAIEX 表格 ────────────────────────────────────────────────────────────────

def print_taiex_table():
    """印出 TAIEX 歷史表格（報酬以 USD 計）。"""
    rows = perf.get_taiex_return_table()
    if not rows:
        console.print("[red]無 TAIEX 資料[/red]")
        return

    t = Table(title=f"{BENCHMARK_NAME}  {START_DATE} 起（報酬以 USD 計）", box=box.ROUNDED)
    t.add_column("日期",           style="dim")
    t.add_column("收盤指數\n(TWD)", justify="right")
    t.add_column("收盤\n(USD)",     justify="right", style="dim")
    t.add_column("單日報酬\n(USD)", justify="right")
    t.add_column("累積報酬\n(USD)", justify="right")

    for r in rows:
        daily = r["daily_return"]
        cumul = r["cumul_return"]
        daily_style = "green" if daily >= 0 else "red"
        cumul_style = "green" if cumul >= 0 else "red"
        taiex_usd = r.get("taiex_close_usd")
        t.add_row(
            r["date"],
            f"{r['taiex_close']:,.2f}",
            f"{taiex_usd:,.2f}" if taiex_usd else "—",
            Text(_pct(daily), style=daily_style),
            Text(_pct(cumul), style=cumul_style),
        )

    console.print(t)


# ── 歷史績效總表 ──────────────────────────────────────────────────────────────

def print_history_table():
    """
    印出從 START_DATE 到今日的基金 & TAIEX 逐日歷史總表。
    欄位：日期 | 基金NAV(USD) | 基金日報酬 | 基金累積報酬 | TAIEX | TAIEX日報酬 | TAIEX累積報酬 | 匯率
    每次呼叫皆從 DB 取最新資料，自動涵蓋到今日已更新的最後一筆。
    """
    nav_rows   = db.get_nav_history(start_date=START_DATE)
    taiex_rows = db.get_taiex_history(start_date=START_DATE)

    if not nav_rows:
        console.print("[red]無基金淨值資料，請先執行選項 1 更新資料。[/red]")
        return

    # 建立 TAIEX lookup
    taiex_map = {r["date"]: r for r in taiex_rows}

    # 基金累積報酬以初始資金為基準（而非首日 NAV）
    p0 = INITIAL_CAPITAL_USD
    # TAIEX 累積報酬以競賽開始前一個交易日收盤（USD）為基準
    taiex_before = db.get_last_taiex_before(START_DATE)
    t0_usd = (taiex_before["close_usd"] if taiex_before
              else (taiex_rows[0]["close_usd"] if taiex_rows else None))

    last_date = nav_rows[-1]["date"]
    title = (f"基金 & TAIEX 歷史績效總表  "
             f"{START_DATE} ～ {last_date}  "
             f"（共 {len(nav_rows)} 日）")

    t = Table(title=title, box=box.SIMPLE_HEAVY, title_style="bold cyan")
    t.add_column("日期",              style="dim",    no_wrap=True)
    t.add_column("基金 NAV\n(USD)",   justify="right")
    t.add_column("基金\n日報酬",       justify="right")
    t.add_column("基金\n累積報酬",     justify="right")
    t.add_column("TAIEX\n(TWD)",      justify="right")
    t.add_column("TAIEX\n(USD)",      justify="right", style="dim")
    t.add_column("TAIEX\n日報酬(USD)", justify="right")
    t.add_column("TAIEX\n累積(USD)",   justify="right")
    t.add_column("匯率\nTWD/USD",      justify="right", style="dim")

    # 用排序後的 TAIEX 日期清單找前一個交易日，避免 nav_rows 含週末而 taiex_map 無週末資料
    taiex_dates_sorted = sorted(taiex_map.keys())

    for i, nav in enumerate(nav_rows):
        d = nav["date"]

        # 基金報酬：首日用 INITIAL_CAPITAL_USD 為基準
        cumul_fund = (nav["nav_usd"] - p0) / p0
        if i == 0:
            daily_fund = (nav["nav_usd"] - INITIAL_CAPITAL_USD) / INITIAL_CAPITAL_USD
        else:
            prev_nav = nav_rows[i - 1]["nav_usd"]
            daily_fund = (nav["nav_usd"] - prev_nav) / prev_nav if prev_nav else None

        # TAIEX：用 close_usd 計算報酬，TWD 點數僅供參考
        tx = taiex_map.get(d)
        taiex_twd    = tx["close"]     if tx else None
        taiex_usd    = tx["close_usd"] if tx else None
        cumul_bench  = ((taiex_usd - t0_usd) / t0_usd) if (taiex_usd and t0_usd) else None
        prev_taiex_date = next((td for td in reversed(taiex_dates_sorted) if td < d), None)
        prev_tx = taiex_map.get(prev_taiex_date) if prev_taiex_date else taiex_before
        if taiex_usd is None or prev_tx is None:
            daily_bench = None
        else:
            prev_usd = prev_tx["close_usd"]
            daily_bench = (taiex_usd - prev_usd) / prev_usd if prev_usd else None

        # 顏色
        def _colored(val):
            if val is None:
                return Text("—", style="dim")
            style = "green" if val >= 0 else "red"
            return Text(f"{val*100:+.2f}%", style=style)

        t.add_row(
            d,
            f"{nav['nav_usd']:>15,.0f}",
            _colored(daily_fund),
            _colored(cumul_fund),
            f"{taiex_twd:,.2f}" if taiex_twd else "—",
            f"{taiex_usd:,.2f}" if taiex_usd else "—",
            _colored(daily_bench),
            _colored(cumul_bench),
            f"{nav['exchange_rate']:.4f}",
        )

    console.print(t)

    # 摘要列
    last_nav  = nav_rows[-1]["nav_usd"]
    total_ret = (last_nav - p0) / p0
    last_tx   = taiex_map.get(nav_rows[-1]["date"])
    bench_ret = ((last_tx["close_usd"] - t0_usd) / t0_usd
                 if (last_tx and t0_usd) else None)

    ret_color = "green" if total_ret >= 0 else "red"
    console.print(
        f"\n  基金累積報酬  [{ret_color}][bold]{total_ret*100:+.2f}%[/bold][/{ret_color}]"
        + (f"   vs   TAIEX [{('green' if bench_ret>=0 else 'red')}]{bench_ret*100:+.2f}%[/{'green' if bench_ret>=0 else 'red'}]"
           if bench_ret is not None else "")
    )


# ── 報酬率折線圖 ──────────────────────────────────────────────────────────────

def plot_returns(save_path: str | None = None) -> str:
    """
    繪製基金 vs TAIEX 累積報酬率走勢圖。
    """
    returns_table = perf.get_returns_table()
    if len(returns_table) < 2:
        console.print("[red]資料不足，無法繪圖[/red]")
        return ""

    import pandas as pd
    df = pd.DataFrame(returns_table)
    df["date"] = pd.to_datetime(df["date"])
    df = df.dropna(subset=["cumul_fund"])

    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    # 基金曲線
    ax.plot(df["date"], df["cumul_fund"] * 100,
            color="#00d4ff", linewidth=2, label=FUND_NAME, zorder=5)
    ax.fill_between(df["date"], df["cumul_fund"] * 100, 0,
                    alpha=0.15, color="#00d4ff")

    # TAIEX 曲線
    bench_df = df.dropna(subset=["cumul_bench"])
    if not bench_df.empty:
        ax.plot(bench_df["date"], bench_df["cumul_bench"] * 100,
                color="#ff6b6b", linewidth=2, linestyle="--",
                label=BENCHMARK_NAME, zorder=4)

    ax.axhline(y=0, color="white", linewidth=0.5, alpha=0.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.xticks(rotation=45, color="white")
    plt.yticks(color="white")
    ax.set_xlabel("日期", color="white")
    ax.set_ylabel("累積報酬率 (%)", color="white")
    ax.set_title(f"{FUND_NAME}  vs  {BENCHMARK_NAME}\n累積報酬率走勢", color="white",
                 fontsize=14, pad=15)
    ax.tick_params(colors="white")
    for spine in ax.spines.values():
        spine.set_edgecolor("#444466")
    ax.legend(facecolor="#1a1a2e", labelcolor="white", framealpha=0.8)
    ax.grid(axis="y", color="#444466", alpha=0.4)

    plt.tight_layout()

    if save_path is None:
        ensure_reports_dir()
        today = date.today().isoformat()
        save_path = os.path.join(REPORTS_DIR, f"returns_{today}.png")

    plt.savefig(save_path, dpi=150, bbox_inches="tight",
                facecolor=fig.get_facecolor())
    plt.close()
    return save_path


# ── Alpha 計算展示 ────────────────────────────────────────────────────────────

def print_alpha_calculation(n1: int | None = None):
    """
    完整展示 Alpha 計算過程。
    n1：參賽組數（若提供，計算評分）。
    """
    result = perf.calculate_alpha_full()

    if "error" in result:
        console.print(f"[red]計算失敗：{result['error']}[/red]")
        return

    console.rule("[bold cyan]Alpha 計算完整過程[/bold cyan]")

    T      = result["T"]
    r_p    = result["r_bar_p"]
    r_m    = result["r_bar_m"]
    rf     = result["rf"]
    beta   = result["beta"]
    alpha  = result["alpha"]
    beta_n = result["beta_numerator"]
    beta_d = result["beta_denominator"]

    console.print(f"\n[bold]競賽期間：[/bold] {result['start_date']} ～ {result['end_date']}")
    console.print(f"[bold]共同交易日數 T = {T}[/bold]\n")

    # 每日報酬率表
    console.print("[bold yellow]─ 每日報酬率（基金 vs 基準）─[/bold yellow]")
    pairs = result["daily_pairs"]
    tbl_rows = []
    for d, rp_i, rm_i in pairs:
        tbl_rows.append([d, f"{rp_i*100:+.4f}%", f"{rm_i*100:+.4f}%",
                         f"{(rp_i-r_p)*100:.4f}%", f"{(rm_i-r_m)*100:.4f}%",
                         f"{(rp_i-r_p)*(rm_i-r_m)*1e6:.4f}",
                         f"{(rm_i-r_m)**2*1e6:.4f}"])
    headers = ["日期", "r_p,t", "r_m,t",
               "r_p,t−r̄_p", "r_m,t−r̄_m",
               "(r_p,t−r̄_p)(r_m,t−r̄_m)×10⁶", "(r_m,t−r̄_m)²×10⁶"]
    console.print(tabulate(tbl_rows, headers=headers, tablefmt="rounded_outline"))

    # 計算步驟
    console.rule()
    console.print(f"\n[bold cyan]步驟 1：平均報酬率[/bold cyan]")
    console.print(f"  r̄_p = Σr_{{p,t}} / T = {r_p*100:.6f}%  (日均)")
    console.print(f"  r̄_m = Σr_{{m,t}} / T = {r_m*100:.6f}%  (日均)")

    console.print(f"\n[bold cyan]步驟 2：Beta（系統性風險）[/bold cyan]")
    console.print(f"  分子 Σ(r_{{p,t}}-r̄_p)(r_{{m,t}}-r̄_m) = {beta_n:.10f}")
    console.print(f"  分母 Σ(r_{{m,t}}-r̄_m)²               = {beta_d:.10f}")
    console.print(f"  β_p = {beta_n:.10f} / {beta_d:.10f}")
    console.print(f"      = [bold green]{beta:.6f}[/bold green]")

    console.print(f"\n[bold cyan]步驟 3：Alpha（超額報酬）[/bold cyan]")
    console.print(f"  r_f = {rf*100:.4f}%（日利率）")
    console.print(f"  α_p = r̄_p − [r_f + β_p(r̄_m − r_f)]")
    console.print(f"      = {r_p*100:.6f}% − [{rf*100:.4f}% + {beta:.6f} × ({r_m*100:.6f}% − {rf*100:.4f}%)]")
    console.print(f"      = {r_p*100:.6f}% − {(rf + beta*(r_m-rf))*100:.6f}%")

    alpha_color = "green" if alpha >= 0 else "red"
    console.print(f"      = [bold {alpha_color}]{alpha*100:+.6f}%（日均）[/bold {alpha_color}]")
    console.print(f"      ≈ [bold {alpha_color}]{alpha*252*100:+.2f}% 年化（參考值）[/bold {alpha_color}]")

    # 累積績效
    console.print(f"\n[bold cyan]步驟 4：累積績效[/bold cyan]")
    tr_f = result.get("total_return_fund")
    tr_b = result.get("total_return_bench")
    if tr_f is not None:
        console.print(f"  基金累積報酬：{tr_f*100:+.2f}%")
    if tr_b is not None:
        console.print(f"  TAIEX 累積報酬：{tr_b*100:+.2f}%")

    # 評分（如提供組數）
    if n1 and n1 > 0:
        console.print(f"\n[bold cyan]評分公式[/bold cyan]")
        console.print(f"  共 {n1} 組參賽，若排第 n 名 → 分數 = 20 − 10 × (n−1)/{n1}")
        console.print(f"  第 1 名 = {20:.1f} 分，末名 = {20 - 10*(n1-1)/n1:.1f} 分")

    console.rule()
