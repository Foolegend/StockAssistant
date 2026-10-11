#!/usr/bin/env python3
"""
Stock Data Fetcher + Technical Indicator Calculator
Outputs structured JSON for Claude Code analysis.
No AI/LLM calls -- pure data + math.

Data source priority (graceful degradation):
  A-share: Tushare Pro (if TUSHARE_TOKEN set) > efinance > akshare > yfinance
  HK:      efinance > akshare > yfinance
  US:      yfinance (primary)

News search priority (via --news flag):
  Tavily (if TAVILY_API_KEY set) > SerpAPI (if SERPAPI_KEY set) > skip (use WebSearch in Claude)

Usage:
    python3 stock_data_fetcher.py --stocks "600519,TSLA,HK00700" [--days 120] [--news]

Environment variables (optional, for enhanced data):
    TUSHARE_TOKEN    - Tushare Pro token (free signup at tushare.pro)
    TAVILY_API_KEY   - Tavily API key (1000 free calls/month)
    SERPAPI_KEY       - SerpAPI key (100 free calls/month)
"""

import os
import sys
import json
import argparse
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")

# Data source availability detection
_AVAILABLE_SOURCES = {}

def _check_source(name):
    """Lazy-check if a data source library is importable."""
    if name not in _AVAILABLE_SOURCES:
        try:
            __import__(name)
            _AVAILABLE_SOURCES[name] = True
        except ImportError:
            _AVAILABLE_SOURCES[name] = False
    return _AVAILABLE_SOURCES[name]

def _log(msg):
    """Log to stderr so it doesn't pollute JSON stdout."""
    print(f"[INFO] {msg}", file=sys.stderr)

# ============================================================
# SECTION 1: Stock Code Parser
# ============================================================

def classify_stock(code: str) -> tuple:
    """
    Returns (market, normalized_code, display_code)
    market: 'cn_a', 'cn_hk', 'us'
    """
    code = code.strip()
    upper = code.upper()

    # 港股: HK00700 -> ('cn_hk', '00700', 'HK00700')
    if upper.startswith("HK") and upper[2:].isdigit():
        return ("cn_hk", upper[2:], upper)

    # A股: 600519 -> ('cn_a', '600519', '600519')
    if upper.isdigit() and len(upper) == 6:
        return ("cn_a", upper, upper)

    # 美股: TSLA -> ('us', 'TSLA', 'TSLA')
    if upper.isalpha() and 1 <= len(upper) <= 5:
        return ("us", upper, upper)

    # 带后缀的A股: 600519.SH -> strip
    if "." in upper:
        base, suffix = upper.rsplit(".", 1)
        if suffix in ("SH", "SZ", "SS") and base.isdigit():
            return ("cn_a", base, base)

    # 带前缀的A股: SH600519 -> strip
    if upper[:2] in ("SH", "SZ") and upper[2:].isdigit():
        return ("cn_a", upper[2:], upper[2:])

    return ("unknown", code, code)

def to_yfinance_code(code: str, market: str) -> str:
    """Convert to Yahoo Finance ticker format."""
    if market == "cn_hk":
        num = code.lstrip("0") or "0"
        return f"{num.zfill(4)}.HK"
    if market == "us":
        return code
    # A股
    if code.startswith(("600", "601", "603", "688")):
        return f"{code}.SS"
    if code.startswith(("51", "52", "56", "58")):
        return f"{code}.SS"
    return f"{code}.SZ"

# ============================================================
# SECTION 2: Data Fetchers (with graceful degradation)
# ============================================================

def _df_to_ohlcv(df, days):
    """Convert a normalized DataFrame to OHLCV list."""
    import pandas as pd
    for c in ["open", "close", "high", "low", "volume", "amount", "pct_chg"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").tail(days).reset_index(drop=True)
    ohlcv = []
    for _, row in df.iterrows():
        ohlcv.append({
            "date": str(row.get("date", "")),
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
            "volume": _safe_float(row.get("volume")),
            "amount": _safe_float(row.get("amount")),
            "pct_chg": _safe_float(row.get("pct_chg")),
        })
    return ohlcv

def _safe_float(v):
    try:
        return float(v)
    except Exception:
        return None

# --- Tushare Pro (Priority 0, needs TUSHARE_TOKEN) ---

def _fetch_tushare_a(code: str, days: int):
    """Fetch A-share via Tushare Pro. Returns (ohlcv, source) or raises."""
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise EnvironmentError("TUSHARE_TOKEN not set")
    import tushare as ts
    pro = ts.pro_api(token)
    ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "688")) else f"{code}.SZ"
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        raise ValueError(f"Tushare returned no data for {code}")
    col_map = {
        "trade_date": "date", "open": "open", "close": "close",
        "high": "high", "low": "low", "vol": "volume",
        "amount": "amount", "pct_chg": "pct_chg",
    }
    df = df.rename(columns=col_map)
    df["date"] = df["date"].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(str(x)) == 8 else x)
    _log(f"[{code}] Using Tushare Pro (premium)")
    return _df_to_ohlcv(df, days), "tushare"

# --- efinance (Priority 1, free) ---

def _fetch_efinance_a(code: str, days: int):
    """Fetch A-share via efinance (EastMoney). Returns (ohlcv, source) or raise."""
    import efinance as ef
    df = ef.stock.get_quote_history(code)
    if df is None or df.empty:
        raise ValueError(f"efinance returned no data for {code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using efinance (free)")
    return _df_to_ohlcv(df, days), "efinance"

# --- akshare (Priority 2, free) ---

def _fetch_akshare_a(code: str, days: int):
    """Fetch A-share via akshare."""
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start_date, end_date=end_date, adjust="qfq")
    except Exception:
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start_date, end_date=end_date, adjust="hfq")
    if df is None or df.empty:
        raise ValueError(f"akshare returned no data for {code}")
    col_map = {
        "日期": "date", "开盘": "open", "收盘": "close",
        "最高": "high", "最低": "low", "成交量": "volume",
        "成交额": "amount", "涨跌幅": "pct_chg",
    }
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using akshare (free)")
    return _df_to_ohlcv(df, days), "akshare"

# --- yfinance fallback ---

def _fetch_yfinance(code: str, market: str, days: int):
    import yfinance as yf
    ticker = to_yfinance_code(code, market)
    data = yf.download(ticker, period=f"{days}d", interval="1d", progress=False)
    if data.empty:
        raise ValueError(f"yfinance returned no data for {ticker}")
    data = data.reset_index()
    data.rename(columns={"Date": "date", "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}, inplace=True)
    data["pct_chg"] = data["close"].pct_change()*100
    _log(f"[{code}] Using yfinance (fallback)")
    return _df_to_ohlcv(data, days), "yfinance"

def fetch_stock_data(code: str, days: int = 120):
    market, norm_code, _ = classify_stock(code)
    if market == "cn_a":
        # try in order
        for fetcher in [_fetch_tushare_a, _fetch_efinance_a, _fetch_akshare_a, lambda c,d: _fetch_yfinance(c, market, d)]:
            try:
                return fetcher(norm_code, days)
            except Exception as e:
                _log(f"[{code}] {fetcher.__name__} failed: {e}")
        raise RuntimeError(f"All data sources failed for {code}")
    elif market == "cn_hk":
        for fetcher in [_fetch_efinance_hk, lambda c,d: _fetch_yfinance(c, market, d)]:
            try:
                return fetcher(norm_code, days)
            except Exception as e:
                _log(f"[HK{code}] {fetcher.__name__} failed: {e}")
        raise RuntimeError(f"All data sources failed for HK{code}")
    else:
        # US stocks via yfinance
        return _fetch_yfinance(norm_code, market, days)

# ============================================================
# SECTION 3: Technical Indicators
# ============================================================

def calculate_indicators(ohlcv):
    import pandas as pd, numpy as np
    df = pd.DataFrame(ohlcv)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    # MA
    df['MA5'] = df['close'].rolling(window=5).mean()
    df['MA20'] = df['close'].rolling(window=20).mean()
    # MACD
    ema12 = df['close'].ewm(span=12, adjust=False).mean()
    ema26 = df['close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    # RSI
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -1*delta.clip(upper=0)
    roll_up = up.ewm(com=13, adjust=False).mean()
    roll_down = down.ewm(com=13, adjust=False).mean()
    rs = roll_up/roll_down
    df['RSI'] = 100 - (100/(1+rs))
    # Volume MA
    df['VOL_MA5'] = df['volume'].rolling(window=5).mean()
    # Bias (乖离率) relative to MA20
    df['BIAS'] = (df['close'] - df['MA20'])/df['MA20']*100
    # Latest values
    latest = df.iloc[-1]
    indicators = {
        'close': latest['close'],
        'MA5': latest['MA5'],
        'MA20': latest['MA20'],
        'MACD': latest['MACD'],
        'MACD_Signal': latest['Signal'],
        'RSI': latest['RSI'],
        'VOL': latest['volume'],
        'VOL_MA5': latest['VOL_MA5'],
        'BIAS': latest['BIAS'],
    }
    return indicators

# ============================================================
# SECTION 4: Scoring
# ============================================================

def compute_score(indicators):
    score = 0
    # Simple heuristic: MA5 > MA20 adds points, MACD > Signal adds, RSI 30-70 neutral, BIAS within +/-5 neutral
    if indicators['MA5'] and indicators['MA20'] and indicators['MA5'] > indicators['MA20']:
        score += 10
    if indicators['MACD'] and indicators['MACD_Signal'] and indicators['MACD'] > indicators['MACD_Signal']:
        score += 10
    if indicators['RSI']:
        if indicators['RSI'] < 30:
            score += 5
        elif indicators['RSI'] > 70:
            score -= 5
    if indicators['BIAS'] is not None:
        if abs(indicators['BIAS']) < 5:
            score += 5
        elif indicators['BIAS'] > 5:
            score -= 5
    # Volume: if current > VOL_MA5, add
    if indicators['VOL'] and indicators['VOL_MA5'] and indicators['VOL'] > indicators['VOL_MA5']:
        score += 5
    # Clamp 0-100
    return max(0, min(100, score))

# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--stocks', required=True, help='Comma separated stock codes')
    parser.add_argument('--days', type=int, default=120, help='Historical days')
    parser.add_argument('--news', action='store_true', help='Fetch news if API keys present')
    args = parser.parse_args()
    result = {}
    for code in [c.strip() for c in args.stocks.split(',') if c.strip()]:
        try:
            ohlcv, source = fetch_stock_data(code, args.days)
            indicators = calculate_indicators(ohlcv)
            score = compute_score(indicators)
            result[code] = {
                'source': source,
                'latest_price': indicators['close'],
                'indicators': indicators,
                'score': score,
                'ohlcv': ohlcv,
            }
        except Exception as e:
            result[code] = {'error': str(e)}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
