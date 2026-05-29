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

import os, sys, json, argparse, warnings
from datetime import datetime, timedelta
warnings.filterwarnings("ignore")

_AVAILABLE_SOURCES = {}

def _check_source(name):
    if name not in _AVAILABLE_SOURCES:
        try:
            __import__(name)
            _AVAILABLE_SOURCES[name] = True
        except ImportError:
            _AVAILABLE_SOURCES[name] = False
    return _AVAILABLE_SOURCES[name]

def _log(msg):
    print(f"[INFO] {msg}", file=sys.stderr)

def classify_stock(code: str):
    code = code.strip()
    upper = code.upper()
    if upper.startswith("HK") and upper[2:].isdigit():
        return ("cn_hk", upper[2:], upper)
    if upper.isdigit() and len(upper) == 6:
        return ("cn_a", upper, upper)
    if upper.isalpha() and 1 <= len(upper) <= 5:
        return ("us", upper, upper)
    if "." in upper:
        base, suffix = upper.rsplit(".", 1)
        if suffix in ("SH", "SZ", "SS") and base.isdigit():
            return ("cn_a", base, base)
    if upper[:2] in ("SH", "SZ") and upper[2:].isdigit():
        return ("cn_a", upper[2:], upper[2:])
    return ("unknown", code, code)

def to_yfinance_code(code: str, market: str):
    if market == "cn_hk":
        num = code.lstrip("0") or "0"
        return f"{num.zfill(4)}.HK"
    if market == "us":
        return code
    if code.startswith(("600", "601", "603", "688")):
        return f"{code}.SS"
    if code.startswith(("51", "52", "56", "58")):
        return f"{code}.SS"
    return f"{code}.SZ"

def _safe_float(v):
    try:
        return float(v)
    except Exception:
        return None

def _df_to_ohlcv(df, days):
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

def _fetch_tushare_a(code: str, days: int):
    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise EnvironmentError("TUSHARE_TOKEN not set")
    import tushare as ts
    pro = ts.pro_api(token)
    ts_code = f"{code}.SH" if code.startswith(("600", "601", "603", "688")) else f"{code}.SZ"
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days*2)).strftime("%Y%m%d")
    df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        raise ValueError(f"Tushare returned no data for {code}")
    col_map = {"trade_date": "date", "open": "open", "close": "close", "high": "high", "low": "low", "vol": "volume", "amount": "amount", "pct_chg": "pct_chg"}
    df = df.rename(columns=col_map)
    df["date"] = df["date"].apply(lambda x: f"{x[:4]}-{x[4:6]}-{x[6:]}" if len(str(x)) == 8 else x)
    _log(f"[{code}] Using Tushare Pro (premium)")
    return _df_to_ohlcv(df, days), "tushare"

def _fetch_efinance_a(code: str, days: int):
    import efinance as ef
    df = ef.stock.get_quote_history(code)
    if df is None or df.empty:
        raise ValueError(f"efinance returned no data for {code}")
    col_map = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount", "涨跌幅": "pct_chg"}
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using efinance (free)")
    return _df_to_ohlcv(df, days), "efinance"

def _fetch_efinance_hk(code: str, days: int):
    import efinance as ef
    df = ef.stock.get_quote_history(code, stock_type="hk")
    if df is None or df.empty:
        raise ValueError(f"efinance returned no data for HK{code}")
    col_map = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount", "涨跌幅": "pct_chg"}
    df = df.rename(columns=col_map)
    _log(f"[HK{code}] Using efinance (free)")
    return _df_to_ohlcv(df, days), "efinance"

def _fetch_akshare_a(code: str, days: int):
    import akshare as ak
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days*2)).strftime("%Y%m%d")
    try:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date, adjust="qfq")
    except Exception:
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        raise ValueError(f"akshare returned no data for {code}")
    col_map = {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume", "成交额": "amount", "涨跌幅": "pct_chg"}
    df = df.rename(columns=col_map)
    _log(f"[{code}] Using akshare (free)")
    return _df_to_ohlcv(df, days), "akshare"

def _fetch_yfinance(code: str, days: int):
    import yfinance as yf
    ticker = yf.Ticker(code)
    hist = ticker.history(period=f"{days}d")
    if hist.empty:
        raise ValueError(f"yfinance returned no data for {code}")
    df = hist.reset_index()
    df.rename(columns={"Date": "date", "Open": "open", "Close": "close", "High": "high", "Low": "low", "Volume": "volume"}, inplace=True)
    df["pct_chg"] = df["close"].pct_change()*100
    _log(f"[{code}] Using yfinance (fallback)")
    return _df_to_ohlcv(df, days), "yfinance"

def fetch_stock(code: str, days: int = 5):
    market, norm_code, _ = classify_stock(code)
    if market == "unknown":
        raise ValueError(f"Unrecognized stock code {code}")
    # Try sources based on market
    if market == "cn_a":
        # Tushare
        if _check_source("tushare"):
            try:
                return _fetch_tushare_a(norm_code, days)
            except Exception as e:
                _log(str(e))
        if _check_source("efinance"):
            try:
                return _fetch_efinance_a(norm_code, days)
            except Exception as e:
                _log(str(e))
        if _check_source("akshare"):
            try:
                return _fetch_akshare_a(norm_code, days)
            except Exception as e:
                _log(str(e))
        if _check_source("yfinance"):
            return _fetch_yfinance(to_yfinance_code(norm_code, market), days)
    if market == "cn_hk":
        if _check_source("efinance"):
            try:
                return _fetch_efinance_hk(norm_code, days)
            except Exception as e:
                _log(str(e))
        if _check_source("akshare"):
            try:
                return _fetch_akshare_a(norm_code, days)
            except Exception as e:
                _log(str(e))
        if _check_source("yfinance"):
            return _fetch_yfinance(to_yfinance_code(norm_code, market), days)
    if market == "us":
        if _check_source("yfinance"):
            return _fetch_yfinance(to_yfinance_code(norm_code, market), days)
    raise RuntimeError("No data source available")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stocks", required=True, help="Comma-separated stock codes")
    parser.add_argument("--days", type=int, default=5, help="Days of history")
    parser.add_argument("--news", action="store_true", help="Include news (not implemented here)")
    args = parser.parse_args()
    stocks = [s.strip() for s in args.stocks.split(",") if s.strip()]
    result = {}
    for s in stocks:
        try:
            ohlcv, source = fetch_stock(s, args.days)
            result[s] = {"source": source, "data": ohlcv}
        except Exception as e:
            result[s] = {"error": str(e)}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
