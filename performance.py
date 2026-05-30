"""
績效計算模組
嚴格依據競賽規則計算 Alpha、Beta 及各項報酬率。
"""

from __future__ import annotations
import math

import database as db
from config import START_DATE, RISK_FREE_RATE_DAILY, INITIAL_CAPITAL_USD


# ── 基礎報酬序列 ──────────────────────────────────────────────────────────────

def get_fund_returns() -> list[dict]:
    """
    回傳基金每日報酬率序列（美元計價）。
    r_p,t = (p_t - p_{t-1}) / p_{t-1}
    首個交易日以 INITIAL_CAPITAL_USD 為 t-1 基準。
    """
    navs = db.get_nav_history(start_date=START_DATE)
    if not navs:
        return []
    returns = []
    for i in range(len(navs)):
        p_t   = navs[i]["nav_usd"]
        p_tm1 = INITIAL_CAPITAL_USD if i == 0 else navs[i-1]["nav_usd"]
        if p_tm1 == 0:
            continue
        returns.append({
            "date":    navs[i]["date"],
            "r_p":     (p_t - p_tm1) / p_tm1,
            "nav_usd": p_t,
        })
    return returns


def get_benchmark_returns() -> list[dict]:
    """
    回傳 TAIEX 每日美元報酬率序列。
    r_m,t = (taiex_usd_t - taiex_usd_{t-1}) / taiex_usd_{t-1}
    首個交易日以競賽前一交易日（START_DATE 前）的 close_usd 為 t-1 基準。
    """
    rows = db.get_taiex_history(start_date=START_DATE)
    if not rows:
        return []
    taiex_before = db.get_last_taiex_before(START_DATE)
    returns = []
    for i in range(len(rows)):
        p_t   = rows[i]["close_usd"]
        p_tm1 = taiex_before["close_usd"] if i == 0 else rows[i-1]["close_usd"]
        if not p_tm1 or p_tm1 == 0:
            continue
        returns.append({
            "date":      rows[i]["date"],
            "r_m":       (p_t - p_tm1) / p_tm1,
            "taiex":     rows[i]["close"],
            "taiex_usd": p_t,
        })
    return returns


def _align_returns(fund_rets: list[dict], bench_rets: list[dict]) -> list[tuple]:
    """
    對齊兩個報酬率序列（取共同日期）。
    回傳 [(date, r_p, r_m), ...]
    """
    bench_map = {r["date"]: r["r_m"] for r in bench_rets}
    aligned = []
    for r in fund_rets:
        d = r["date"]
        if d in bench_map:
            aligned.append((d, r["r_p"], bench_map[d]))
    return aligned


# ── Alpha / Beta 計算 ─────────────────────────────────────────────────────────

def calculate_alpha_full() -> dict:
    """
    依競賽規則計算 Alpha，回傳完整計算過程。

    公式：
        r̄_p = Σr_{p,t} / T
        r̄_m = Σr_{m,t} / T
        β_p = Σ(r_{p,t}-r̄_p)(r_{m,t}-r̄_m) / Σ(r_{m,t}-r̄_m)²
        α_p = r̄_p - [r_f + β_p(r̄_m - r_f)]
    """
    fund_rets  = get_fund_returns()
    bench_rets = get_benchmark_returns()
    aligned    = _align_returns(fund_rets, bench_rets)

    if len(aligned) < 2:
        return {"error": "資料不足（需至少 2 個共同交易日）", "T": len(aligned)}

    T = len(aligned)
    dates = [a[0] for a in aligned]
    rp    = [a[1] for a in aligned]
    rm    = [a[2] for a in aligned]

    # 平均報酬
    r_bar_p = sum(rp) / T
    r_bar_m = sum(rm) / T

    # Beta 分子/分母
    numerator   = sum((rp[i] - r_bar_p) * (rm[i] - r_bar_m) for i in range(T))
    denominator = sum((rm[i] - r_bar_m) ** 2                 for i in range(T))

    if denominator == 0:
        return {"error": "Beta 分母為 0（基準指數報酬率無變化）", "T": T}

    beta = numerator / denominator

    # Alpha
    rf    = RISK_FREE_RATE_DAILY
    alpha = r_bar_p - (rf + beta * (r_bar_m - rf))

    # 期間累積報酬
    nav_start = db.get_nav_history(start_date=START_DATE)
    p0        = INITIAL_CAPITAL_USD
    p_last    = nav_start[-1]["nav_usd"] if nav_start else p0
    total_return_fund  = (p_last - p0) / p0

    taiex_rows = db.get_taiex_history(start_date=START_DATE)
    taiex_before_row = db.get_last_taiex_before(START_DATE)
    t0_usd = (taiex_before_row["close_usd"] if taiex_before_row
              else (taiex_rows[0]["close_usd"] if taiex_rows else None))
    tN_usd = taiex_rows[-1]["close_usd"] if taiex_rows else None
    total_return_bench = ((tN_usd - t0_usd) / t0_usd) if (t0_usd and t0_usd != 0) else None

    return {
        "T":                 T,
        "start_date":        dates[0]  if dates else None,
        "end_date":          dates[-1] if dates else None,
        "r_bar_p":           r_bar_p,
        "r_bar_m":           r_bar_m,
        "rf":                rf,
        "beta_numerator":    numerator,
        "beta_denominator":  denominator,
        "beta":              beta,
        "alpha":             alpha,
        "alpha_annualized":  alpha * 252,  # 參考值
        "total_return_fund": total_return_fund,
        "total_return_bench": total_return_bench,
        "daily_pairs":       list(zip(dates, rp, rm)),
    }


# ── 每日報酬率彙整表 ──────────────────────────────────────────────────────────

def get_returns_table() -> list[dict]:
    """
    回傳完整每日報酬率表格，包含基金 NAV、TAIEX、各日報酬。
    供 reporter 使用。
    """
    fund_rets  = get_fund_returns()
    bench_rets = get_benchmark_returns()

    bench_map = {r["date"]: r for r in bench_rets}
    navs      = {n["date"]: n for n in db.get_nav_history(start_date=START_DATE)}

    # 初始 NAV（用於計算累積報酬）
    nav_rows  = db.get_nav_history(start_date=START_DATE)
    p0_usd    = INITIAL_CAPITAL_USD

    taiex_rows   = db.get_taiex_history(start_date=START_DATE)
    taiex_before = db.get_last_taiex_before(START_DATE)
    t0_usd       = (taiex_before["close_usd"] if taiex_before
                    else (taiex_rows[0]["close_usd"] if taiex_rows else None))

    rows = []
    for r in fund_rets:
        d   = r["date"]
        bm  = bench_map.get(d, {})
        nav = navs.get(d, {})

        cumul_fund  = (r["nav_usd"] - p0_usd) / p0_usd if p0_usd else None
        taiex_usd   = bm.get("taiex_usd")
        cumul_bench = ((taiex_usd - t0_usd) / t0_usd) if (taiex_usd and t0_usd) else None

        rows.append({
            "date":          d,
            "nav_usd":       r["nav_usd"],
            "r_p":           r["r_p"],
            "cumul_fund":    cumul_fund,
            "taiex":         bm.get("taiex"),
            "r_m":           bm.get("r_m"),
            "cumul_bench":   cumul_bench,
            "exchange_rate": nav.get("exchange_rate"),
        })
    return rows


# ── TAIEX 報酬表（供 reporter 使用）─────────────────────────────────────────

def get_taiex_return_table() -> list[dict]:
    """回傳 TAIEX 從起始日至今的每日收盤及累積報酬（報酬以 USD 計）。"""
    rows = db.get_taiex_history(start_date=START_DATE)
    if not rows:
        return []

    taiex_before = db.get_last_taiex_before(START_DATE)
    t0_usd = taiex_before["close_usd"] if taiex_before else rows[0]["close_usd"]

    result = []
    for i, r in enumerate(rows):
        prev_usd = t0_usd if i == 0 else rows[i-1]["close_usd"]
        daily = (r["close_usd"] - prev_usd) / prev_usd if prev_usd else 0.0
        cumul = (r["close_usd"] - t0_usd) / t0_usd if t0_usd else 0
        result.append({
            "date":            r["date"],
            "taiex_close":     r["close"],
            "taiex_close_usd": r["close_usd"],
            "daily_return":    daily,
            "cumul_return":    cumul,
        })
    return result
