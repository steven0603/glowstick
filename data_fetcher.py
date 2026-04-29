"""
資料抓取模組
- 匯率：台灣銀行即期買賣均價（主要），yfinance（fallback）
- 個股 / TAIEX：yfinance
"""

from __future__ import annotations
import warnings
from datetime import date, timedelta

import pandas as pd
import yfinance as yf
import requests
from bs4 import BeautifulSoup

import database as db
from config import USD_TWD_TICKER, BENCHMARK_TICKER, START_DATE

warnings.filterwarnings("ignore")

# ── 台銀匯率快取（每日只抓一次）────────────────────────────────────────────────
_bot_rates_cache: dict[str, float] | None = None
_bot_cache_date: str | None = None


def _fetch_bot_rates_all() -> dict[str, float]:
    """
    從台銀網站抓取 USD/TWD 即期匯率，回傳 {date_iso: (買入+賣出)/2}。
    同一天內只打一次網路請求。
    """
    global _bot_rates_cache, _bot_cache_date
    today = date.today().isoformat()
    if _bot_rates_cache is not None and _bot_cache_date == today:
        return _bot_rates_cache

    try:
        url = "https://rate.bot.com.tw/xrt/quote/ltm/USD"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        result: dict[str, float] = {}
        for row in soup.find("table").find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 6:
                continue
            try:
                # cells[0]=日期, [4]=即期買入, [5]=即期賣出
                d = cells[0].get_text(strip=True).replace("/", "-")
                rate = (float(cells[4].get_text(strip=True)) +
                        float(cells[5].get_text(strip=True))) / 2
                result[d] = round(rate, 4)
            except (ValueError, AttributeError):
                continue
        if result:
            _bot_rates_cache = result
            _bot_cache_date = today
        return result
    except Exception as e:
        print(f"[台銀匯率] 抓取失敗：{e}")
        return {}


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
    抓取 USD/TWD 匯率（台銀即期買賣均價）。
    優先台銀，失敗則 fallback 至 yfinance。
    """
    bot = _fetch_bot_rates_all()
    if bot:
        filtered = {d: r for d, r in bot.items() if start <= d <= end}
        if filtered:
            df = pd.DataFrame.from_dict(filtered, orient="index", columns=["rate"])
            df.index.name = None
            return df.sort_index()

    # Fallback: yfinance
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
        print(f"[匯率] yfinance 抓取失敗：{e}")
        return pd.DataFrame()


def fetch_exchange_rate_single(date_str: str) -> float | None:
    """抓取單日匯率，若無資料則向前尋找最近一筆。"""
    # 先查 DB
    rate = db.get_exchange_rate(date_str)
    if rate:
        return rate
    # 台銀
    bot = _fetch_bot_rates_all()
    if date_str in bot:
        return bot[date_str]
    # Fallback: yfinance
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
    # 最後 fallback：使用最近一筆
    last = db.get_last_exchange_rate(before_date=date_str)
    return last["rate"] if last else None


def backfill_bot_exchange_rates() -> int:
    """
    用台銀即期買賣均價覆蓋 DB 中所有歷史匯率，
    並同步重算 taiex_history.close_usd 與 nav_history.nav_usd。
    回傳更新筆數。
    """
    bot = _fetch_bot_rates_all()
    if not bot:
        print("[台銀匯率] 無法取得資料，取消 backfill")
        return 0

    print(f"  → 寫入 {len(bot)} 筆台銀匯率...")
    for d, r in sorted(bot.items()):
        db.save_exchange_rate(d, r)

    def _get_rate(d: str) -> float | None:
        if d in bot:
            return bot[d]
        candidates = [k for k in bot if k <= d]
        return bot[max(candidates)] if candidates else None

    print("  → 重算 TAIEX close_usd...")
    for row in db.get_taiex_history():
        r = _get_rate(row["date"])
        if r:
            db.save_taiex(row["date"], row["close"], row["close"] / r)

    print("  → 重算 NAV nav_usd...")
    for row in db.get_nav_history():
        r = _get_rate(row["date"])
        if r:
            db.save_nav(row["date"], row["nav_twd"], row["nav_twd"] / r, r)

    print(f"  ✓ 台銀匯率 backfill 完成（{len(bot)} 筆）")
    return len(bot)


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
            actual_date = df.index[0].strftime("%Y-%m-%d")
            if actual_date != date_str:
                return None
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
    個股採批次下載、永遠覆蓋 DB（不跳過已存在資料）。
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

    # 個股：批次一次性抓取，永遠覆蓋 DB（不使用快取）
    if tickers:
        yf_start, yf_end = _date_range_for_yf(today, today)
        fetched: dict[str, dict] = {}
        try:
            raw = yf.download(tickers, start=yf_start, end=yf_end,
                              auto_adjust=True, progress=False)
            if not raw.empty:
                date_strs = raw.index.strftime("%Y-%m-%d").tolist()
                if today in date_strs:
                    row = raw.iloc[date_strs.index(today)]
                    is_multi = isinstance(raw.columns, pd.MultiIndex)
                    for t in tickers:
                        try:
                            c = row[("Close",  t)] if is_multi else row["Close"]
                            o = row[("Open",   t)] if is_multi else row["Open"]
                            h = row[("High",   t)] if is_multi else row["High"]
                            l = row[("Low",    t)] if is_multi else row["Low"]
                            v = row[("Volume", t)] if is_multi else row.get("Volume")
                            if pd.notna(c):
                                fetched[t] = {
                                    "open":   float(o) if pd.notna(o) else None,
                                    "high":   float(h) if pd.notna(h) else None,
                                    "low":    float(l) if pd.notna(l) else None,
                                    "close":  float(c),
                                    "volume": float(v) if v is not None and pd.notna(v) else None,
                                }
                        except (KeyError, TypeError):
                            pass
        except Exception as e:
            print(f"  [!] 批次抓取個股失敗：{e}")

        for t in tickers:
            if t in fetched:
                p = fetched[t]
                db.save_price(today, t, p["open"], p["high"],
                              p["low"], p["close"], p["volume"])
                result["prices"][t] = p["close"]
            else:
                # 無當日資料（假日 / 無交易），沿用最近一筆
                last = db.get_last_price(t, before_date=today)
                result["prices"][t] = last["close"] if last else None

    return result
