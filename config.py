"""
GlowStick X 證券投資信託基金 — 系統設定
"""

FUND_NAME          = "GlowStick X 證券投資信託基金"
MANAGEMENT_COMPANY = "空間叉叉投資信託(股)公司"
INITIAL_CAPITAL_USD = 100_000_000   # 初始資金：1億美元

START_DATE = "2026-03-23"   # 首個交易日（3/22 午夜會議後次一交易日）
END_DATE   = "2026-05-29"   # 結算日

# 績效基準
BENCHMARK_TICKER = "^TWII"
BENCHMARK_NAME   = "台灣加權股價指數"

# 台美匯率 ticker（Yahoo Finance: TWD per 1 USD）
USD_TWD_TICKER = "TWD=X"

# 無風險日利率 0.003%
RISK_FREE_RATE_DAILY = 0.00003

# ── 手續費 ──────────────────────────────────────────
BUY_FEE_RATE   = 0.001   # 買進手續費 0.1%
SELL_FEE_RATE  = 0.001   # 賣出手續費 0.1%
SELL_TAX_RATE  = 0.003   # 賣出交易稅 0.3%

# ── 投資限制 ────────────────────────────────────────
MAX_CASH_RATIO          = 0.30   # 現金上限 30%
MAX_SINGLE_ASSET_RATIO  = 0.30   # 單一資產上限 30%
MIN_ASSET_TYPES         = 10
MAX_ASSET_TYPES         = 20

# ── 初始持股配置 ────────────────────────────────────
INITIAL_PORTFOLIO = {
    "台積電":    {"ticker": "2330.TW", "weight": 0.200},
    "昇達科":    {"ticker": "3491.TWO", "weight": 0.135},
    "穩懋":      {"ticker": "3105.TWO", "weight": 0.090},
    "華通":      {"ticker": "2313.TW", "weight": 0.080},
    "金像電":    {"ticker": "2368.TW", "weight": 0.060},
    "台光電":    {"ticker": "2383.TW", "weight": 0.060},
    "台達電":    {"ticker": "2308.TW", "weight": 0.060},
    "奇鋐":      {"ticker": "3017.TW", "weight": 0.060},
    "兆赫":      {"ticker": "2485.TW", "weight": 0.030},
    "聯合再生":  {"ticker": "3576.TW", "weight": 0.030},
    "建準":      {"ticker": "2421.TW", "weight": 0.030},
    "同欣電":    {"ticker": "6271.TW", "weight": 0.030},
    "訊芯-KY":   {"ticker": "6451.TW", "weight": 0.020},
    "台揚":      {"ticker": "2314.TW", "weight": 0.015},
}
# 現金保留 10%（不在持股表，計算時自動處理）
CASH_INITIAL_WEIGHT = 0.10

DB_PATH     = "portfolio.db"
REPORTS_DIR = "reports"
