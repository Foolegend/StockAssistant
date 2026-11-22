#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot market snapshot for A-share market diagnosis.

Prints a single JSON object to stdout. Pure data collection: no LLM, no files
written, no temporary scripts needed. Every section degrades gracefully -- a
failed source is reported in ``errors`` instead of aborting the run.

Usage:
    python market_brief.py [--news-limit 30] [--no-news] [--pretty]

Data sources:
    indices / turnover   Tencent qt.gtimg.cn
    breadth              乐咕乐股 market-activity (up/down/limit counts)
    sectors              Sina newSinaHy.php (industry) / newFLJK.php (concept)
    southbound           Eastmoney datacenter RPT_MUTUAL_QUOTA
    global / commodities Sina hq.sinajs.cn
    news                 Sina 7x24 finance feed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests

socket.setdefaulttimeout(10)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT = float(os.getenv("MARKET_BRIEF_TIMEOUT", "10"))
CN_TZ = timezone(timedelta(hours=8))

A_INDICES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sh000852": "中证1000",
    "sh000016": "上证50",
    "sz399303": "国证2000",
}
GLOBAL_INDICES = {
    "int_nasdaq": "纳斯达克",
    "int_sp500": "标普500",
    "int_dji": "道琼斯",
    "int_nikkei": "日经225",
    "int_hangseng": "恒生指数",
}
COMMODITIES = {
    "hf_GC": "纽约黄金",
    "hf_CL": "纽约原油",
    "hf_SI": "纽约白银",
    "hf_ES": "标普500期货",
    "hf_NQ": "纳斯达克期货",
}


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    return session


def _num(value, default=None):
    try:
        text = str(value).strip()
        if not text or text in {"-", "--", "None", "nan"}:
            return default
        return float(text)
    except (TypeError, ValueError):
        return default


def _pct(last, prev):
    last = _num(last)
    prev = _num(prev)
    if last is None or not prev:
        return None
    return round((last / prev - 1) * 100, 2)


def _local_now() -> datetime:
    return datetime.now(CN_TZ)


# ---------------------------------------------------------------- indices ----

def fetch_indices() -> dict:
    """A-share index quotes + combined turnover (亿元)."""
    session = _session()
    response = session.get(
        "https://qt.gtimg.cn/q=" + ",".join(A_INDICES),
        timeout=TIMEOUT,
    )
    response.encoding = "gbk"
    rows = []
    for line in response.text.strip().splitlines():
        if '="' not in line:
            continue
        code = line.split("=", 1)[0].replace("v_", "").strip()
        fields = line.split('="', 1)[1].rstrip('";').split("~")
        if len(fields) < 38:
            continue
        rows.append(
            {
                "code": code,
                "name": fields[1] or A_INDICES.get(code, code),
                "price": _num(fields[3]),
                "prev_close": _num(fields[4]),
                "change": _num(fields[31]),
                "pct": _num(fields[32]),
                "high": _num(fields[33]),
                "low": _num(fields[34]),
                "amount_yi": (
                    round(_num(fields[37], 0) / 10000, 2)
                    if len(fields) > 37 and _num(fields[37]) is not None
                    else None
                ),
                "quote_time": fields[30] or None,
            }
        )

    turnover = None
    by_code = {row["code"]: row for row in rows}
    sh = by_code.get("sh000001", {}).get("amount_yi")
    sz = by_code.get("sz399001", {}).get("amount_yi")
    if sh is not None and sz is not None:
        turnover = {
            "sh_yi": sh,
            "sz_yi": sz,
            "total_yi": round(sh + sz, 2),
        }

    quote_time = by_code.get("sh000001", {}).get("quote_time") or ""
    trade_date = None
    match = re.match(r"^(\d{4})(\d{2})(\d{2})", quote_time)
    if match:
        trade_date = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

    return {"indices": rows, "turnover": turnover, "trade_date": trade_date}


# ---------------------------------------------------------------- breadth ----

def fetch_breadth() -> dict:
    """Up/down/limit-up/limit-down counts from Legu market activity page."""
    session = _session()
    response = session.get(
        "https://legulegu.com/stockdata/market-activity",
        timeout=TIMEOUT,
    )
    response.encoding = "utf-8"
    html = response.text

    match = re.search(
        r"(\d{4}-\d{2}-\d{2})\s*[，,]\s*A股赚钱效益分布[，,]\s*其中"
        r"(\d+)家涨停[，,]\s*(\d+)家跌停[，,]\s*(\d+)家上涨[，,]\s*(\d+)家下跌",
        html,
    )
    if not match:
        raise ValueError("legu page no longer contains the activity summary")

    activity = re.search(
        r'metric-label">活跃度</span>\s*<span class="metric-value">([\d.]+%)',
        html,
    )
    stat_time = re.search(
        r'market-activity-time">([^<]+)</span>',
        html,
    )

    return {
        "breadth": {
            "trade_date": match.group(1),
            "limit_up": int(match.group(2)),
            "limit_down": int(match.group(3)),
            "up": int(match.group(4)),
            "down": int(match.group(5)),
            "activity": activity.group(1) if activity else None,
            "stat_time": stat_time.group(1).strip() if stat_time else None,
            "source": "legulegu.com",
        }
    }


# ---------------------------------------------------------------- sectors ----

def _parse_sina_sectors(text: str) -> list[dict]:
    rows = []
    for key, value in re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', text):
        fields = value.split(",")
        if len(fields) < 9:
            continue
        rows.append(
            {
                "code": key,
                "name": fields[1],
                "count": int(_num(fields[2], 0) or 0),
                "pct": _num(fields[5]),
                "amount_yi": (
                    round(_num(fields[7], 0) / 1e8, 2)
                    if _num(fields[7]) is not None
                    else None
                ),
                "leader": fields[12] if len(fields) > 12 else None,
                "leader_pct": _num(fields[9]) if len(fields) > 9 else None,
            }
        )
    return [row for row in rows if row["pct"] is not None]


def fetch_sectors(top_n: int = 6) -> dict:
    session = _session()
    session.headers.update({"Referer": "https://finance.sina.com.cn/"})
    result = {}
    sources = {
        "industry": "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php",
        "concept": "https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=class",
    }
    for kind, url in sources.items():
        response = session.get(url, timeout=TIMEOUT)
        response.encoding = "gbk"
        rows = _parse_sina_sectors(response.text)
        if not rows:
            raise ValueError(f"no {kind} sector rows parsed")
        rows.sort(key=lambda item: item["pct"], reverse=True)
        result[kind] = {
            "top": rows[:top_n],
            "bottom": list(reversed(rows[-top_n:])),
        }
    return {"sectors": result}


# ------------------------------------------------------------- southbound ---

def fetch_hsgt() -> dict:
    """Southbound net buy (northbound daily net flow is no longer disclosed)."""
    session = _session()
    params = {
        "reportName": "RPT_MUTUAL_QUOTA",
        "columns": (
            "TRADE_DATE,MUTUAL_TYPE,BOARD_TYPE,MUTUAL_TYPE_NAME,FUNDS_DIRECTION,"
            "INDEX_CODE,INDEX_NAME,BOARD_CODE"
        ),
        "quoteColumns": (
            "status~07~BOARD_CODE,dayNetAmtIn~07~BOARD_CODE,"
            "dayAmtRemain~07~BOARD_CODE,dayAmtThreshold~07~BOARD_CODE,"
            "f104~07~BOARD_CODE,f105~07~BOARD_CODE,f106~07~BOARD_CODE,"
            "f3~03~INDEX_CODE~INDEX_f3,netBuyAmt~07~BOARD_CODE"
        ),
        "quoteType": "0",
        "pageNumber": "1",
        "pageSize": "2000",
        "sortTypes": "1",
        "sortColumns": "MUTUAL_TYPE",
        "source": "WEB",
        "client": "WEB",
    }
    response = session.get(
        "https://datacenter-web.eastmoney.com/api/data/v1/get",
        params=params,
        timeout=TIMEOUT,
    )
    payload = response.json()
    rows = (payload.get("result") or {}).get("data") or []
    if not rows:
        raise ValueError("empty RPT_MUTUAL_QUOTA response")

    items = []
    for row in rows:
        net_buy = _num(row.get("netBuyAmt"))
        items.append(
            {
                "board": row.get("MUTUAL_TYPE_NAME"),
                "direction": row.get("FUNDS_DIRECTION"),
                "trade_date": str(row.get("TRADE_DATE") or "")[:10],
                "net_buy_yi": round(net_buy / 10000, 2) if net_buy is not None else None,
                "index": row.get("INDEX_NAME"),
                "index_pct": _num(row.get("INDEX_f3")),
                "up": _num(row.get("f104")),
                "down": _num(row.get("f105")),
                "flat": _num(row.get("f106")),
            }
        )

    south = [item for item in items if "南向" in str(item["direction"])]
    south_total = sum(
        item["net_buy_yi"] or 0 for item in south
    )
    return {
        "hsgt": {
            "trade_date": items[0]["trade_date"] if items else None,
            "southbound_net_buy_yi": round(south_total, 2),
            "southbound": south,
            "northbound_note": (
                "北向资金当日净买入自2024年起停止逐日披露，"
                "接口返回0表示未披露，不代表真实净买入为0。"
            ),
            "boards": items,
        }
    }


# ------------------------------------------------------ global/commodities --

def _sina_lines(codes: list[str]) -> dict[str, list[str]]:
    session = _session()
    session.headers.update({"Referer": "https://finance.sina.com.cn"})
    response = session.get(
        "https://hq.sinajs.cn/list=" + ",".join(codes),
        timeout=TIMEOUT,
    )
    response.encoding = "gbk"
    result = {}
    for line in response.text.strip().splitlines():
        match = re.search(r'hq_str_([^=]+)="([^"]*)"', line)
        if match and match.group(2):
            result[match.group(1)] = match.group(2).split(",")
    return result


def fetch_global() -> dict:
    data = _sina_lines(list(GLOBAL_INDICES) + list(COMMODITIES))
    global_rows = []
    for code, label in GLOBAL_INDICES.items():
        fields = data.get(code)
        if not fields or len(fields) < 4:
            continue
        global_rows.append(
            {
                "code": code,
                "name": fields[0] or label,
                "price": _num(fields[1]),
                "change": _num(fields[2]),
                "pct": _num(fields[3]),
            }
        )

    commodity_rows = []
    for code, label in COMMODITIES.items():
        fields = data.get(code)
        if not fields or len(fields) < 8:
            continue
        prev = _num(fields[7])
        commodity_rows.append(
            {
                "code": code,
                "name": fields[13] if len(fields) > 13 and fields[13] else label,
                "price": _num(fields[0]),
                "pct": _pct(fields[0], prev),
                "quote_time": fields[6] if len(fields) > 6 else None,
            }
        )

    return {"global": global_rows, "commodities": commodity_rows}


def fetch_usd_cnh() -> dict:
    data = _sina_lines(["fx_susdcnh"])
    fields = data.get("fx_susdcnh")
    if not fields or len(fields) < 11:
        raise ValueError("no fx_susdcnh data")
    return {
        "fx": {
            "usd_cnh": _num(fields[8]),
            "pct": _num(fields[10]),
            "quote_time": fields[0],
        }
    }


# ------------------------------------------------------------------ news ----

def _fetch_news_page(page: int) -> list[dict] | None:
    try:
        session = _session()
        session.headers.update({"Referer": "https://finance.sina.com.cn/"})
        response = session.get(
            "https://zhibo.sina.com.cn/api/zhibo/feed",
            params={
                "page": page,
                "page_size": 100,
                "zhibo_id": 152,
                "tag_id": 0,
                "dire": "f",
                "dpc": 1,
            },
            timeout=TIMEOUT,
        )
        payload = response.json()
        feed = ((payload.get("result") or {}).get("data") or {}).get("feed") or {}
        return feed.get("list") or []
    except Exception:  # noqa: BLE001 - one bad page must not drop the feed
        return None


def fetch_news(limit: int = 40, pages: int = 30) -> dict:
    """Sina 7x24 finance flash news covering today (plus overnight tail)."""
    now = _local_now()
    items: list[dict] = []
    seen: set[str] = set()
    failed_pages = 0

    with ThreadPoolExecutor(max_workers=8) as pool:
        batches = pool.map(_fetch_news_page, range(1, pages + 1))

    for batch in batches:
        if batch is None:
            failed_pages += 1
            continue
        for item in batch:
            raw_text = (item.get("rich_text") or item.get("text") or "").strip()
            if not raw_text:
                continue
            text = re.sub(r"<[^>]+>", "", raw_text)
            text = re.sub(r"\s+", " ", text).strip()
            dedupe_key = str(item.get("id") or text[:60])
            if not text or dedupe_key in seen:
                continue
            try:
                raw_ts = item.get("create_time")
                if isinstance(raw_ts, str) and not raw_ts.strip().isdigit():
                    created = datetime.strptime(
                        raw_ts.strip(), "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=CN_TZ)
                else:
                    created = datetime.fromtimestamp(int(raw_ts), CN_TZ)
            except (TypeError, ValueError, OSError):
                continue
            if created.date() != now.date() and created < now - timedelta(hours=12):
                continue
            seen.add(dedupe_key)
            items.append(
                {
                    "time": created.strftime("%m-%d %H:%M"),
                    "text": text[:220],
                    "_ts": created,
                }
            )

    items.sort(key=lambda item: item["_ts"])
    total = len(items)

    if total <= limit or limit <= 0:
        selected = items
    else:
        # Even sampling keeps morning catalysts from being crowded out by the
        # most recent hour's news feed.
        step = (total - 1) / (limit - 1)
        selected = [items[round(index * step)] for index in range(limit)]
        selected = list({item["_ts"]: item for item in selected}.values())
        selected.sort(key=lambda item: item["_ts"])

    for item in items:
        item.pop("_ts", None)

    window = "暂无"
    if items:
        window = f"{items[0]['time']} ~ {items[-1]['time']}"

    return {
        "news": selected,
        "news_window": window,
        "news_total_collected": total,
        "news_pages_failed": failed_pages,
    }


# ------------------------------------------------------------------ main ----

def collect(news_limit: int, with_news: bool) -> dict:
    now = _local_now()
    output: dict = {
        "generated_at": now.isoformat(timespec="seconds"),
        "trade_date": None,
        "indices": [],
        "turnover": None,
        "breadth": None,
        "sectors": None,
        "hsgt": None,
        "global": [],
        "commodities": [],
        "fx": None,
        "news": [],
        "sources": {
            "indices": "qt.gtimg.cn",
            "breadth": "legulegu.com",
            "sectors": "vip.stock.finance.sina.com.cn",
            "hsgt": "datacenter-web.eastmoney.com",
            "global": "hq.sinajs.cn",
            "news": "zhibo.sina.com.cn",
        },
        "errors": [],
    }

    tasks = {
        "indices": fetch_indices,
        "breadth": fetch_breadth,
        "sectors": fetch_sectors,
        "hsgt": fetch_hsgt,
        "global": fetch_global,
        "fx": fetch_usd_cnh,
    }
    if with_news:
        tasks["news"] = lambda: fetch_news(news_limit)

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        futures = {pool.submit(fn): name for name, fn in tasks.items()}
        for future in as_completed(futures, timeout=45):
            name = futures[future]
            try:
                output.update(future.result())
            except Exception as error:  # noqa: BLE001 - report, never abort
                output["errors"].append(
                    {"section": name, "error": f"{type(error).__name__}: {error}"[:300]}
                )

    if output.get("trade_date") is None and isinstance(output.get("breadth"), dict):
        output["trade_date"] = output["breadth"].get("trade_date")
    if output.get("trade_date") is None and isinstance(output.get("hsgt"), dict):
        output["trade_date"] = output["hsgt"].get("trade_date")
    return output


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="One-shot A-share market snapshot")
    parser.add_argument("--news-limit", type=int, default=40, help="max news items")
    parser.add_argument("--no-news", action="store_true", help="skip news collection")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    args = parser.parse_args()

    result = collect(max(0, args.news_limit), not args.no_news)
    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=1 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
