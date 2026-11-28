#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拉取 A 股指数月度行情，落盘 CSV 并输出摘要 JSON。

数据源按可靠性降级（东方财富限流时自动切换）：

  1. 东方财富  ak.stock_zh_index_daily_em   日线，脚本内重采样为月度
  2. 新浪      ak.stock_zh_index_daily      日线，脚本内重采样为月度
  3. 腾讯      ak.stock_zh_index_daily_tx   日线，脚本内重采样为月度

用法：
    python fetch_index_data.py --index sh000001 --start 1990-01
    python fetch_index_data.py --index 上证指数,深证成指 --out-dir out
    python fetch_index_data.py --index sz399001 --refresh

输出：
    <out-dir>/<key>_monthly.csv     列：date,open,high,low,close,pct_change,source
    stdout                          摘要 JSON（点数、区间、首末值、最高/最低月）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------- 指数登记表

INDEXES = {
    "sh000001": {"code": "000001", "name": "上证指数",
                 "alias": ["上证指数", "上证综指", "沪指", "sh000001", "000001"]},
    "sz399001": {"code": "399001", "name": "深证成指",
                 "alias": ["深证成指", "深证综指", "深成指", "sz399001", "399001"]},
    "sh000300": {"code": "000300", "name": "沪深300",
                 "alias": ["沪深300", "hs300", "sh000300", "000300"]},
    "sh000905": {"code": "000905", "name": "中证500",
                 "alias": ["中证500", "zz500", "sh000905", "000905"]},
    "sh000016": {"code": "000016", "name": "上证50",
                 "alias": ["上证50", "sh000016", "000016"]},
    "sz399006": {"code": "399006", "name": "创业板指",
                 "alias": ["创业板指", "cyb", "sz399006", "399006"]},
    "sh000688": {"code": "000688", "name": "科创50",
                 "alias": ["科创50", "kc50", "sh000688", "000688"]},
}

_ALIAS_MAP = {}
for _key, _meta in INDEXES.items():
    _ALIAS_MAP[_key.lower()] = _key
    _ALIAS_MAP[_meta["code"]] = _key
    for _a in _meta["alias"]:
        _ALIAS_MAP[_a.lower()] = _key


def resolve_index(token: str) -> tuple[str, dict]:
    """把用户输入（上证指数 / 000001 / sh000001 …）解析成标准 key。"""
    key = _ALIAS_MAP.get(token.strip().lower())
    if not key:
        known = "、".join(f"{k}({v['name']})" for k, v in INDEXES.items())
        raise SystemExit(f"无法识别的指数: {token!r}；可用: {known}")
    return key, INDEXES[key]


# ---------------------------------------------------------------- 数据源

def _akshare():
    import akshare as ak  # noqa: PLC0415 - 延迟导入，便于给出友好报错
    return ak


def fetch_eastmoney(key: str, start: str, end: str) -> pd.DataFrame:
    """东方财富：日线（按请求区间）。"""
    ak = _akshare()
    df = ak.stock_zh_index_daily_em(
        symbol=key,
        start_date=start.replace("-", "") + "01",
        end_date=end.replace("-", "") + "28",
    )
    if df is None or df.empty:
        raise RuntimeError("东方财富返回空数据")
    df["date"] = pd.to_datetime(df["date"])
    df = df[["date", "open", "high", "low", "close"]].copy()
    df["source"] = "东方财富"
    return df


def fetch_daily_sina(key: str) -> pd.DataFrame:
    """新浪：日线全历史。"""
    ak = _akshare()
    df = ak.stock_zh_index_daily(symbol=key)
    if df is None or df.empty:
        raise RuntimeError("新浪返回空数据")
    df["date"] = pd.to_datetime(df["date"])
    df = df[["date", "open", "high", "low", "close"]].copy()
    df["source"] = "新浪财经"
    return df


def fetch_daily_tx(key: str, start: str, end: str) -> pd.DataFrame:
    """腾讯：日线（按请求区间）。"""
    ak = _akshare()
    df = ak.stock_zh_index_daily_tx(
        symbol=key,
        start_date=start.replace("-", "") + "01",
        end_date=end.replace("-", "") + "28",
    )
    if df is None or df.empty:
        raise RuntimeError("腾讯返回空数据")
    df["date"] = pd.to_datetime(df["date"])
    df = df[["date", "open", "high", "low", "close"]].copy()
    df["source"] = "腾讯财经"
    return df


def to_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    """日线 → 月线：开=月首日开盘，高=月内最高，低=月内最低，收=月末收盘。"""
    df = daily.dropna(subset=["date"]).sort_values("date").copy()
    df["ym"] = df["date"].dt.to_period("M")
    g = df.groupby("ym", sort=True)
    monthly = pd.DataFrame({
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
    }).reset_index()
    monthly["date"] = monthly["ym"].astype(str)          # YYYY-MM
    monthly = monthly.drop(columns=["ym"])
    for col in ("open", "high", "low", "close"):
        monthly[col] = pd.to_numeric(monthly[col], errors="coerce")
    monthly = monthly.dropna(subset=["close"]).reset_index(drop=True)
    monthly["source"] = daily["source"].iloc[0] if len(daily) else ""
    return monthly[["date", "open", "high", "low", "close", "source"]]


def add_pct_change(monthly: pd.DataFrame) -> pd.DataFrame:
    prev = monthly["close"].shift(1)
    monthly = monthly.copy()
    monthly["pct_change"] = ((monthly["close"] / prev - 1.0) * 100).round(2)
    cols = [c for c in ("date", "open", "high", "low", "close", "pct_change", "source")
            if c in monthly.columns]
    return monthly[cols]


def fetch_with_fallback(key: str, start: str, end: str, refresh: bool,
                        cache_dir: Path) -> tuple[pd.DataFrame, str]:
    """依次尝试各数据源，返回 (monthly_df, source)。"""
    cache = cache_dir / f"{key}_monthly.csv"
    if cache.exists() and not refresh:
        try:
            monthly = pd.read_csv(cache, dtype={"date": str})
            if not monthly.empty:
                cached_start = str(monthly["date"].iloc[0])[:7]
                cached_end = str(monthly["date"].iloc[-1])[:7]
                covers = cached_start <= start[:7] and cached_end >= end[:7]
                if covers:
                    print(f"[cache] 复用 {cache}", file=sys.stderr)
                    return monthly, str(monthly["source"].iloc[0])
                print(f"[cache] 缓存区间 {cached_start}~{cached_end} 不覆盖请求区间，"
                      "重新抓取", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001 - 缓存损坏则重新抓取
            print(f"[cache] 缓存不可用，重新抓取: {exc}", file=sys.stderr)

    attempts = [
        ("东方财富", lambda: fetch_eastmoney(key, start, end)),
        ("新浪财经", lambda: fetch_daily_sina(key)),
        ("腾讯财经", lambda: fetch_daily_tx(key, start, end)),
    ]
    errors = []
    for label, fn in attempts:
        try:
            raw = fn()
            monthly = to_monthly(raw)
            if monthly.empty:
                raise RuntimeError("月度数据为空")
            monthly["date"] = monthly["date"].astype(str).str.slice(0, 7)
            cache_dir.mkdir(parents=True, exist_ok=True)
            monthly.to_csv(cache, index=False)
            return monthly, label
        except Exception as exc:  # noqa: BLE001 - 逐个降级
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            print(f"[warn] {label} 取数失败 -> {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            time.sleep(1.5)
    raise SystemExit("所有数据源均失败：\n  " + "\n  ".join(errors))


# ---------------------------------------------------------------- 主流程

def build_summary(key: str, monthly: pd.DataFrame, source: str,
                  csv_path: Path) -> dict:
    first = monthly.iloc[0]
    last = monthly.iloc[-1]
    max_row = monthly.loc[monthly["close"].idxmax()]
    min_row = monthly.loc[monthly["close"].idxmin()]
    return {
        "index": key,
        "name": INDEXES[key]["name"],
        "source": source,
        "points": int(len(monthly)),
        "start": str(first["date"]),
        "end": str(last["date"]),
        "first_close": round(float(first["close"]), 2),
        "last_close": round(float(last["close"]), 2),
        "max_close": {"date": str(max_row["date"]),
                      "value": round(float(max_row["close"]), 2)},
        "min_close": {"date": str(min_row["date"]),
                      "value": round(float(min_row["close"]), 2)},
        "csv": str(csv_path),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="拉取指数月度行情")
    ap.add_argument("--index", required=True,
                    help="指数标识，逗号分隔。支持：sh000001 / 000001 / 上证指数 / sz399001 / 深证成指 …")
    ap.add_argument("--start", default="1990-01", help="起始月份 YYYY-MM")
    ap.add_argument("--end", default="2099-12", help="结束月份 YYYY-MM")
    ap.add_argument("--out-dir", default="data", help="CSV 输出目录")
    ap.add_argument("--cache-dir", default=None, help="原始日线缓存目录（默认 <out-dir>/_cache）")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存重新抓取")
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    cache_dir = Path(args.cache_dir) if args.cache_dir else out_dir / "_cache"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 结束月份不晚于当月，避免把未来日期当成"数据缺口"
    today_month = pd.Timestamp.today().strftime("%Y-%m")
    end_eff = min(args.end, today_month)

    summaries = []
    for token in args.index.split(","):
        token = token.strip()
        if not token:
            continue
        key, meta = resolve_index(token)
        monthly, source = fetch_with_fallback(key, args.start, end_eff,
                                              args.refresh, cache_dir)
        monthly = monthly[
            (monthly["date"] >= args.start) & (monthly["date"] <= args.end)
        ].reset_index(drop=True)
        monthly = add_pct_change(monthly)
        csv_path = out_dir / f"{key}_monthly.csv"
        monthly.to_csv(csv_path, index=False)
        summary = build_summary(key, monthly, source, csv_path)
        summary["label"] = meta["name"]
        summaries.append(summary)
        print(f"[ok] {meta['name']}({key}) {len(monthly)} 个月 <- {source} -> {csv_path}",
              file=sys.stderr)

    print(json.dumps(summaries if len(summaries) > 1 else summaries[0],
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
