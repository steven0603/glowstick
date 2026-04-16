"""
Yahoo Finance 資料抓取模組
所有個股、匯率、TAIEX 資料皆透過 yfinance 取得。
"""

from __future__ import annotations
import warnings
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

import database as db
from config import USD_TWD_TICKER, BENCHMARK_TICKER, START_DATE

warnings.filterwarnings("ignore")


# ── 工具函式 ─────────────────────────────────────────────────────────────────

def _flatten(df: pd.DataFrame) -> pd.DataFrame:
    """將 yfinance 回傳的 MultiIndex columns 壓平為單層。"""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df

def _next_day(date_str: str) -> str:
    d = date.fromisoformat(date_str)
    return (d + timedelta(days=1)).isoformat()


def _date_range_for_yf(start: str, end: str) -> tuple[str, str]:
    """yfinance end 是 exclusive，需往後加一天。"""
    end_dt = date.fromisoformat(end) + timedelta(days=1)
    return start, end_dt.isoformat()


# ── 匯率 ─────────────────────────────────────────────────────────────────────

def fetch_exchange_rates(start: str, end: str) -> pd.DataFrame:
    """
    抓取 USD/TWD 收盤匯率（TWD per 1 USD）。
    回傳 DataFrame，index 為 date string，欄位為 rate。
    """
    yf_start, yf_end = _date_range_for_yf(start, end)
    try:
        df = yf.download(USD_TWD_TICKER, start=yf_start, end=yf_end,
                         auto_adjust=True, progress=False)
        if df.empty:
            return pd.DataFrame()
        df = _flatten(df)
        result = df[["Close"]].copy()
        result.index = result.index.strftime("%Y-%m-%d")
        result.columns = ["rate"]
        return result
    except Exception as e:
        print(f"[匯率] 抓取失敗：{e}")
        return pd.DataFrame()


def fetch_exchange_rate_single(date_str: str) -> float | None:
    """抓取單日匯率，若無資料則向前尋找最近一筆。"""
    # 先查 DB
    rate = db.get_exchange_rate(date_str)
    if rate:
        return rate
    # 從 yfinance 抓
    yf_start = date_str
    yf_end   = _next_day(_next_day(date_str))
    try:
        df = yf.download(USD_TWD_TICKER, start=yf_start, end=yf_end,
                         auto_adjust=True, progress=False)
        if not df.empty:
            df = _flatten(df)
            close = float(df["Close"].iloc[0])
            return close
    except Exception:
        pass
    # fallback：使用最近一筆
    last = db.get_last_exchange_rate(before_date=date_str)
    return last["rate"] if last else None


# ── 個股 ─────────────────────────────────────────────────────────────────────

def fetch_stock_history(ticker: str, start: str, end: str) -> pd.DataFrame:
    """
    抓取個股 OHLCV，回傳 DataFrame，index 為 date string。
    """
    yf_start, yf_end = _date_range_for_yf(start, end)
    try:
        df = yf.download(ticker, start=yf_start, end=yf_end,
                         auto_adjust=True, progress=False)
        if df.empty:
            return pd.DataFrame()
        df = _flatten(df)
        df.index = df.index.strftime("%Y-%m-%d")
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as e:
        print(f"[{ticker}] 抓取失敗：{e}")
        return pd.DataFrame()


def fetch_stock_single(ticker: str, date_str: str) -> dict | None:
    """抓取單日個股 OHLCV。"""
    # 先查 DB
    row = db.get_price(date_str, ticker)
    if row:
        return row
    yf_start = date_str
    yf_end   = _next_day(_next_day(date_str))
    try:
        df = yf.download(ticker, start=yf_start, end=yf_end,
                         auto_adjust=True, progress=False)
        if not df.empty:
            df = _flatten(df)
            df.columns = [c.lower() for c in df.columns]
            row_df = df.iloc[0]
            return {
                "date":   date_str,
                "ticker": ticker,
                "open":   float(row_df["open"])   if "open"   in row_df else None,
                "high":   float(row_df["high"])   if "high"   in row_df else None,
                "low":    float(row_df["low"])    if "low"    in row_df else None,
                "close":  float(row_df["close"])  if "close"  in row_df else None,
                "volume": float(row_df["volume"]) if "volume" in row_df else None,
            }
    except Exception:
        pass
    return None


# ── TAIEX ────────────────────────────────────────────────────────────────────

def fetch_taiex_history(start: str, end: str) -> pd.DataFrame:
    yf_start, yf_end = _date_range_for_yf(start, end)
    try:
        df = yf.download(BENCHMARK_TICKER, start=yf_start, end=yf_end,
                         auto_adjust=True, progress=False)
        if df.empty:
            return pd.DataFrame()
        df = _flatten(df)
        result = df[["Close"]].copy()
        result.index = result.index.strftime("%Y-%m-%d")
        result.columns = ["close"]
        return result
    except Exception as e:
        print(f"[TAIEX] 抓取失敗：{e}")
        return pd.DataFrame()


# ── 批次更新 DB ───────────────────────────────────────────────────────────────

def bulk_load_history(tickers: list[str], start: str, end: str):
    """
    批次抓取所有 ticker 的歷史 OHLCV 並存入 DB。
    同時抓取匯率及 TAIEX。
    """
    today_str = date.today().isoformat()
    effective_end = min(end, today_str)

    print("  → 抓取台美匯率...")
    fx_df = fetch_exchange_rates(start, effective_end)
    for d_str, row in fx_df.iterrows():
        db.save_exchange_rate(d_str, float(row["rate"]))

    print("  → 抓取 TAIEX...")
    taiex_df = fetch_taiex_history(start, effective_end)
    for d_str, row in taiex_df.iterrows():
        rate = db.get_exchange_rate(d_str)
        if rate is None:
            last = db.get_last_exchange_rate(before_date=d_str)
            rate = last["rate"] if last else 30.0
        close_usd = float(row["close"]) / rate
        db.save_taiex(d_str, float(row["close"]), close_usd)

    print(f"  → 抓取 {len(tickers)} 檔個股歷史資料...")
    for ticker in tickers:
        df = fetch_stock_history(ticker, start, effective_end)
        if df.empty:
            print(f"    [!] {ticker} 無資料")
            continue
        for d_str, row in df.iterrows():
            db.save_price(
                d_str, ticker,
                float(row.get("open", 0)) if pd.notna(row.get("open")) else None,
                float(row.get("high", 0)) if pd.notna(row.get("high")) else None,
                float(row.get("low",  0)) if pd.notna(row.get("low"))  else None,
                float(row.get("close",0)) if pd.notna(row.get("close")) else None,
                float(row.get("volume",0)) if pd.notna(row.get("volume")) else None,
            )
        print(f"    ✓ {ticker} ({len(df)} 筆)")


def update_today(tickers: list[str], target_date: str | None = None):
    """
    更新指定日期（預設今日）的所有資料並存入 DB。
    回傳 dict: {ticker: close_price, ...}, exchange_rate, taiex_close
    """
    today = target_date or date.today().isoformat()
    result = {"date": today, "prices": {}, "exchange_rate": None, "taiex": None}

    # 匯率
    fx_df = fetch_exchange_rates(today, today)
    if not fx_df.empty:
        rate = float(fx_df["rate"].iloc[0])
        db.save_exchange_rate(today, rate)
        result["exchange_rate"] = rate
    else:
        last = db.get_last_exchange_rate(before_date=today)
        result["exchange_rate"] = last["rate"] if last else None

    # TAIEX
    taiex_df = fetch_taiex_history(today, today)
    if not taiex_df.empty:
        close = float(taiex_df["close"].iloc[0])
        close_usd = close / result["exchange_rate"] if result["exchange_rate"] else 0
        db.save_taiex(today, close, close_usd)
        result["taiex"] = close
    else:
        last_taiex = db.get_taiex_history()
        result["taiex"] = last_taiex[-1]["close"] if last_taiex else None

    # 個股
    for ticker in tickers:
        row = fetch_stock_single(ticker, today)
        if row:
            db.save_price(today, ticker, row.get("open"), row.get("high"),
                          row.get("low"), row.get("close"), row.get("volume"))
            result["prices"][ticker] = row.get("close")
        else:
            # Forward fill
            last = db.get_last_price(ticker, before_date=today)
            result["prices"][ticker] = last["close"] if last else None

    return result
