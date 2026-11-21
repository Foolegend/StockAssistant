#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fetch A-share annual financial statements from Eastmoney F10.

The script prints a short summary and writes one normalized JSON file.  Run it
from the repository root:

    python stock-server/.stock/skills/enterprise-statement-analysis/scripts/fetch_statements.py \
        --code 600519 --years 6 --out .tmp/600519-statements.json

Only annual reports are fetched by default.  Consolidated statements are
always available; parent-company statements are not exposed by the public F10
endpoint, so controlling-investment analysis needs a --parent file assembled
from the annual report (see references/data-schema.md).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests

try:  # works both when executed directly and when imported as a module
    from _common import (
        is_financial_industry,
        normalize_balance,
        normalize_cashflow,
        normalize_income,
    )
except ImportError:  # pragma: no cover - direct execution fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import (  # type: ignore
        is_financial_industry,
        normalize_balance,
        normalize_cashflow,
        normalize_income,
    )

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
CN_TZ = timezone(timedelta(hours=8))
F10 = "https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/"
STATEMENT_ENDPOINTS = {
    "balance": "zcfzbAjaxNew",
    "income": "lrbAjaxNew",
    "cashflow": "xjllbAjaxNew",
}
DATE_ENDPOINT = "zcfzbDateAjaxNew"


class FetchError(RuntimeError):
    pass


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://emweb.securities.eastmoney.com/"})
    return session


def _get_json(session: requests.Session, url: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = session.get(url, params=params, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except ValueError as error:
            last_error = FetchError(f"接口返回的不是 JSON: {url}")
        except Exception as error:  # noqa: BLE001 - retry transient network errors
            last_error = error
        if attempt == 0:
            time.sleep(0.6)
    raise FetchError(str(last_error) if last_error else f"请求失败: {url}")


def normalize_code(value: str) -> str:
    text = str(value or "").strip().upper().replace(" ", "")
    text = text.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    text = re.sub(r"^(SH|SZ|BJ)", "", text)
    if re.fullmatch(r"\d{6}", text):
        return text
    raise FetchError(f"无法识别股票代码: {value!r}；请使用 6 位代码，例如 600519")


def market_of(code: str) -> str:
    if code.startswith(("6", "9")):
        return "SH"
    if code.startswith(("0", "2", "3")):
        return "SZ"
    if code.startswith(("4", "8")):
        return "BJ"
    raise FetchError(f"不支持的股票代码: {code}")


def em_code(code: str) -> str:
    return f"{market_of(code)}{code}"


def secid(code: str) -> str:
    return f"{'1' if market_of(code) == 'SH' else '0'}.{code}"


def resolve_name(name: str, timeout: float) -> str:
    """Resolve a Chinese company name through Tencent's suggest endpoint."""
    session = _session()
    response = session.get(
        "https://smartbox.gtimg.cn/s3/",
        params={"q": name, "t": "all"},
        timeout=timeout,
    )
    response.raise_for_status()
    response.encoding = "gbk"
    text = response.text
    match = re.search(r'v_hint="([^"]+)"', text)
    if not match:
        raise FetchError(f"没有找到公司: {name}")
    for item in match.group(1).split("^"):
        fields = item.split("~")
        if len(fields) >= 5 and fields[0] in {"sh", "sz", "bj"} and fields[4].startswith("GP-A"):
            return fields[1]
    raise FetchError(f"没有找到 A 股公司: {name}")


def fetch_profile(code: str, timeout: float) -> dict[str, Any]:
    session = _session()
    try:
        payload = _get_json(
            session,
            "https://push2.eastmoney.com/api/qt/stock/get",
            {"secid": secid(code), "fields": "f57,f58,f127,f116,f117"},
            timeout,
        )
        data = payload.get("data") or {}
        return {
            "code": data.get("f57") or code,
            "name": data.get("f58") or "",
            "industry": data.get("f127") or "",
            "market_cap": data.get("f116"),
        }
    except Exception as error:  # noqa: BLE001 - profile is optional
        return {"code": code, "name": "", "industry": "", "market_cap": None, "warning": str(error)[:200]}


def detect_company_type(code: str, timeout: float) -> int:
    """Read Eastmoney's template id: 1=证券, 2=保险, 3=银行, 4=通用."""
    session = _session()
    url = F10 + "Index"
    try:
        response = session.get(url, params={"type": "web", "code": em_code(code).lower()}, timeout=timeout)
        response.raise_for_status()
        match = re.search(r'hidctype[^>]*value=["\']?(\d+)', response.text)
        if match:
            return int(match.group(1))
    except Exception:  # noqa: BLE001 - fall through to probing
        pass
    for candidate in (4, 3, 1, 2):
        try:
            payload = _get_json(
                session,
                F10 + DATE_ENDPOINT,
                {"companyType": candidate, "reportDateType": "1", "code": em_code(code)},
                timeout,
            )
            if payload.get("data"):
                return candidate
        except Exception:  # noqa: BLE001 - try next template
            continue
    return 4


def fetch_report_dates(code: str, company_type: int, years: int, timeout: float) -> list[str]:
    session = _session()
    payload = _get_json(
        session,
        F10 + DATE_ENDPOINT,
        {"companyType": company_type, "reportDateType": "1", "code": em_code(code)},
        timeout,
    )
    rows = payload.get("data") or []
    dates = []
    for row in rows:
        raw = str(row.get("REPORT_DATE") or "")[:10]
        if raw.endswith("12-31"):
            dates.append(raw)
    dates = sorted(set(dates), reverse=True)[: max(1, years)]
    if not dates:
        raise FetchError(f"没有找到 {code} 的年度报告数据")
    return dates


def _batches(items: list[str], size: int = 5) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def fetch_statement_rows(
    code: str,
    company_type: int,
    kind: str,
    dates: list[str],
    timeout: float,
    warnings: list[str],
) -> dict[str, dict[str, Any]]:
    session = _session()
    endpoint = STATEMENT_ENDPOINTS[kind]
    rows_by_date: dict[str, dict[str, Any]] = {}
    for batch in _batches(dates):
        params = {
            "companyType": company_type,
            "reportDateType": "1",
            "reportType": "1",
            "dates": ",".join(batch),
            "code": em_code(code),
        }
        try:
            payload = _get_json(session, F10 + endpoint, params, timeout)
            rows = payload.get("data") or []
        except Exception as error:  # noqa: BLE001 - one failed batch should not abort
            warnings.append(f"{kind} {batch[0]}~{batch[-1]} 获取失败: {str(error)[:160]}")
            continue
        if not rows:
            warnings.append(f"{kind} {batch[0]}~{batch[-1]} 返回空数据")
        for row in rows:
            raw_date = str(row.get("REPORT_DATE") or "")[:10]
            if raw_date:
                rows_by_date[raw_date] = row
    return rows_by_date


def normalize_statement_row(row: dict[str, Any], kind: str) -> dict[str, Any]:
    if kind == "balance":
        return normalize_balance(row)
    if kind == "income":
        return normalize_income(row)
    if kind == "cashflow":
        return normalize_cashflow(row)
    raise ValueError(kind)


def fetch_all(code: str, years: int, timeout: float) -> dict[str, Any]:
    warnings: list[str] = []
    profile = fetch_profile(code, timeout)
    if profile.get("warning"):
        warnings.append(f"公司资料获取失败: {profile.pop('warning')}")
    company_type = detect_company_type(code, timeout)
    dates = fetch_report_dates(code, company_type, years, timeout)

    raw: dict[str, dict[str, dict[str, Any]]] = {date: {} for date in dates}
    for kind in ("balance", "income", "cashflow"):
        rows = fetch_statement_rows(code, company_type, kind, dates, timeout, warnings)
        for date in dates:
            row = rows.get(date)
            if row:
                raw[date][kind] = normalize_statement_row(row, kind)

    statements: dict[str, Any] = {}
    empty_periods: list[str] = []
    for date in dates:
        block = raw.get(date) or {}
        if not block.get("balance") and not block.get("income") and not block.get("cashflow"):
            empty_periods.append(date[:4])
            continue
        statements[date[:4]] = {
            "report_date": date,
            "balance": block.get("balance") or {},
            "income": block.get("income") or {},
            "cashflow": block.get("cashflow") or {},
        }

    if not statements:
        raise FetchError(f"{code} 没有可用报表数据")

    name = profile.get("name") or code
    industry = profile.get("industry") or ""
    is_financial = is_financial_industry(industry, company_type)
    if is_financial:
        warnings.append("金融行业模板：核心利润、经营资产分类等通用指标不适用，脚本仅输出通用财务数据。")

    return {
        "meta": {
            "code": code,
            "name": name,
            "industry": industry,
            "market": market_of(code),
            "company_type": company_type,
            "is_financial": is_financial,
            "currency": "CNY",
            "unit": "元",
            "source": "东方财富 F10 年度报告（合并报表）",
            "fetched_at": datetime.now(CN_TZ).isoformat(timespec="seconds"),
        },
        "periods": sorted(statements, reverse=True),
        "statements": statements,
        "parent_statements": None,
        "warnings": warnings,
        "empty_periods": empty_periods,
    }


def _load_parent(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise FetchError("--parent 文件必须是 JSON 对象")
    if "parent_statements" in payload and isinstance(payload["parent_statements"], dict):
        return payload["parent_statements"]
    if "statements" in payload and isinstance(payload["statements"], dict):
        return payload["statements"]
    if any(key in payload for key in ("balance", "income", "cashflow")):
        period = str(payload.get("period") or payload.get("year") or "latest")
        return {period: payload}
    raise FetchError("--parent 文件缺少 balance/income/cashflow 或 statements")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description="Fetch A-share annual statements")
    parser.add_argument("--code", help="6-digit A-share code, e.g. 600519")
    parser.add_argument("--name", help="Chinese company name, e.g. 贵州茅台")
    parser.add_argument("--years", type=int, default=6, help="number of annual reports (default 6)")
    parser.add_argument("--out", help="output JSON path (default .tmp/<code>-statements.json)")
    parser.add_argument("--parent", help="optional parent-statement JSON assembled from the annual report")
    parser.add_argument("--stdout", action="store_true", help="print the full JSON instead of writing a file")
    parser.add_argument("--pretty", action="store_true", help="pretty-print JSON")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds")
    args = parser.parse_args()

    if not args.code and not args.name:
        parser.error("必须提供 --code 或 --name")
    try:
        code = normalize_code(args.code) if args.code else resolve_name(args.name, args.timeout)
        data = fetch_all(code, max(1, min(args.years, 10)), args.timeout)
        if args.parent:
            data["parent_statements"] = _load_parent(args.parent)

        if args.stdout:
            print(
                json.dumps(
                    data,
                    ensure_ascii=False,
                    indent=2 if args.pretty else None,
                    separators=None if args.pretty else (",", ":"),
                )
            )
            return 0

        out_path = Path(args.out or f".tmp/{code}-statements.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1, separators=(",", ":")),
            encoding="utf-8",
        )
        summary = {
            "ok": True,
            "code": code,
            "name": data["meta"]["name"],
            "industry": data["meta"]["industry"],
            "company_type": data["meta"]["company_type"],
            "is_financial": data["meta"]["is_financial"],
            "periods": data["periods"],
            "out": str(out_path).replace("\\", "/"),
            "warnings": data["warnings"],
        }
        print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
        return 0
    except Exception as error:  # noqa: BLE001 - keep the CLI friendly
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
