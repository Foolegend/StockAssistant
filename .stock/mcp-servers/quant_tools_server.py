#!/usr/bin/env python3
"""quant-tools: a dependency-free MCP server for deterministic stock math.

Why this file implements the protocol itself instead of using the official
``mcp`` SDK: the backend launches MCP servers as ``python <script>`` and the
child only inherits ``PATH``, not the project virtualenv activation.  A system
interpreter may therefore be picked, and it will not have ``mcp`` installed.
Standard library only means any Python 3.9+ can host this server.

Transport: newline-delimited JSON-RPC 2.0 over stdin/stdout.
Never write diagnostics to stdout - stdout carries the protocol.  Use stderr.

Exposed tools:
  - normalize_symbol      CN/HK/US ticker normalization
  - technical_indicators  MA/MACD/RSI/Bollinger/ATR/returns/volatility
  - position_plan         position sizing from equity, entry, stop
  - valuation_snapshot    PE/PB/PEG/yields/Graham number

All tools are pure functions over their arguments: no network, no files.
"""

from __future__ import annotations

import json
import math
import re
import statistics
import sys
from typing import Any, Callable

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "quant-tools"
SERVER_VERSION = "1.0.0"
TRADING_DAYS_PER_YEAR = 252


class ToolInputError(ValueError):
    """Invalid tool arguments, reported back to the model as a tool error."""


# ---------------------------------------------------------------------------
# Argument helpers
# ---------------------------------------------------------------------------


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    value = float(value)
    return round(value, digits) if math.isfinite(value) else None


def _as_number(
    args: dict[str, Any],
    key: str,
    *,
    required: bool = True,
    default: float | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    if key not in args or args[key] is None:
        if required:
            raise ToolInputError(f"'{key}' is required")
        return default
    value = args[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolInputError(f"'{key}' must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise ToolInputError(f"'{key}' must be a finite number")
    if minimum is not None and value < minimum:
        raise ToolInputError(f"'{key}' must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ToolInputError(f"'{key}' must be <= {maximum}")
    return value


def _as_series(
    args: dict[str, Any],
    key: str,
    *,
    required: bool,
    minimum: float | None = None,
) -> list[float] | None:
    if key not in args or args[key] is None:
        if required:
            raise ToolInputError(f"'{key}' is required")
        return None
    values = args[key]
    if not isinstance(values, list):
        raise ToolInputError(f"'{key}' must be an array of numbers")
    if not values:
        raise ToolInputError(f"'{key}' must not be empty")
    series: list[float] = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolInputError(f"'{key}[{index}]' must be a number")
        value = float(value)
        if not math.isfinite(value):
            raise ToolInputError(f"'{key}[{index}]' must be a finite number")
        if minimum is not None and value < minimum:
            raise ToolInputError(f"'{key}[{index}]' must be >= {minimum}")
        series.append(value)
    return series


# ---------------------------------------------------------------------------
# Tool 1: symbol normalization
# ---------------------------------------------------------------------------

_HK_PREFIX_RE = re.compile(r"^HK(\d{1,5})$")
_HK_SUFFIX_RE = re.compile(r"^(\d{1,5})\.HK$")
_CN_SUFFIX_RE = re.compile(r"^(\d{6})\.(SH|SS|SZ|BJ)$")
_CN_PREFIX_RE = re.compile(r"^(SH|SZ|BJ)(\d{6})$")
_US_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

_MARKET_NAMES = {
    "cn_a": "A-share",
    "cn_hk": "Hong Kong",
    "us": "US",
}


def _cn_exchange(code: str) -> str:
    """Infer the mainland exchange from a 6-digit code."""
    if code.startswith(("4", "8")) or code.startswith("920"):
        return "BJ"
    if code.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def normalize_symbol(args: dict[str, Any]) -> dict[str, Any]:
    raw = args.get("code")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolInputError("'code' must be a non-empty string")
    requested = str(args.get("market") or "auto").strip().lower()
    if requested == "hk":
        requested = "cn_hk"
    if requested not in ("auto", "cn_a", "cn_hk", "us"):
        raise ToolInputError("'market' must be one of: auto, cn_a, cn_hk, us")

    text = re.sub(r"\s+", "", raw).upper()
    market, symbol, exchange = _parse_symbol(text, requested)

    if market == "cn_a":
        yahoo_exchange = {"SH": "SS", "SZ": "SZ", "BJ": "BJ"}[exchange]
        display = f"{exchange}{symbol}"
        yahoo = f"{symbol}.{yahoo_exchange}"
    elif market == "cn_hk":
        display = f"HK{symbol}"
        yahoo = f"{symbol.lstrip('0').zfill(4)}.HK"
    else:
        display = symbol
        yahoo = symbol

    return {
        "input": raw,
        "market": market,
        "market_name": _MARKET_NAMES[market],
        "symbol": symbol,
        "display": display,
        "yahoo": yahoo,
        "exchange": exchange,
    }


def _parse_symbol(text: str, requested: str) -> tuple[str, str, str | None]:
    if requested in ("auto", "cn_hk"):
        match = _HK_PREFIX_RE.match(text) or _HK_SUFFIX_RE.match(text)
        if match:
            return "cn_hk", match.group(1).zfill(5), None
        if requested == "cn_hk" and text.isdigit() and len(text) <= 5:
            return "cn_hk", text.zfill(5), None
        if requested == "auto" and text.isdigit() and len(text) == 5:
            return "cn_hk", text.zfill(5), None

    if requested in ("auto", "cn_a"):
        match = _CN_SUFFIX_RE.match(text)
        if match:
            suffix = "SH" if match.group(2) in ("SH", "SS") else match.group(2)
            return "cn_a", match.group(1), suffix
        match = _CN_PREFIX_RE.match(text)
        if match:
            return "cn_a", match.group(2), match.group(1)
        if text.isdigit() and len(text) == 6:
            return "cn_a", text, _cn_exchange(text)

    if requested == "us" or requested == "auto":
        if _US_TICKER_RE.match(text):
            return "us", text, None

    raise ToolInputError(
        f"cannot recognize '{text}' as market '{requested}'; "
        "use e.g. 600519, 00700, HK00700, 000001.SZ, or AAPL"
    )


# ---------------------------------------------------------------------------
# Tool 2: technical indicators
# ---------------------------------------------------------------------------


def _sma_last(values: list[float], period: int) -> float | None:
    if period <= 0 or len(values) < period:
        return None
    return sum(values[-period:]) / period


def _ema_series(values: list[float], period: int) -> list[float | None]:
    if period <= 0 or len(values) < period:
        return [None] * len(values)
    multiplier = 2.0 / (period + 1)
    series: list[float | None] = [None] * (period - 1)
    ema = sum(values[:period]) / period
    series.append(ema)
    for value in values[period:]:
        ema = (value - ema) * multiplier + ema
        series.append(ema)
    return series


def _macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, Any]:
    empty = {"DIF": None, "DEA": None, "hist": None, "signal": "insufficient_data"}
    if len(closes) < slow + signal:
        return empty

    fast_series = _ema_series(closes, fast)
    slow_series = _ema_series(closes, slow)
    dif_series = [
        fast_value - slow_value
        if fast_value is not None and slow_value is not None
        else None
        for fast_value, slow_value in zip(fast_series, slow_series)
    ]
    valid_dif = [value for value in dif_series if value is not None]
    if len(valid_dif) < signal:
        return empty

    dea_series = _ema_series(valid_dif, signal)
    current_dif = valid_dif[-1]
    current_dea = dea_series[-1]
    if current_dea is None:
        return empty

    label = "neutral"
    if len(valid_dif) >= 2 and len(dea_series) >= 2:
        previous_dif = valid_dif[-2]
        previous_dea = dea_series[-2]
        if previous_dea is not None:
            previous_gap = previous_dif - previous_dea
            current_gap = current_dif - current_dea
            if previous_gap <= 0 < current_gap:
                label = "golden_cross_above_zero" if current_dif > 0 else "golden_cross"
            elif previous_gap >= 0 > current_gap:
                label = "death_cross"
            elif current_dif > 0 and current_dea > 0:
                label = "bullish"
            elif current_dif < 0 and current_dea < 0:
                label = "bearish"
        if previous_dif < 0 <= current_dif:
            label = "crossing_above_zero"
        elif previous_dif > 0 >= current_dif:
            label = "crossing_below_zero"

    return {
        "DIF": _round(current_dif),
        "DEA": _round(current_dea),
        "hist": _round((current_dif - current_dea) * 2),
        "signal": label,
    }


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(0.0, delta) for delta in deltas]
    losses = [max(0.0, -delta) for delta in deltas]
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period
    for index in range(period, len(deltas)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
    if average_loss == 0:
        return 100.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def _rsi_zone(rsi: float | None) -> str:
    if rsi is None:
        return "insufficient_data"
    if rsi >= 80:
        return "overbought"
    if rsi >= 60:
        return "strong"
    if rsi >= 40:
        return "neutral"
    if rsi >= 20:
        return "weak"
    return "oversold"


def _ma_alignment(ma: dict[str, float | None]) -> dict[str, str]:
    ma5, ma10, ma20 = ma.get("MA5"), ma.get("MA10"), ma.get("MA20")
    if ma5 is None or ma10 is None or ma20 is None:
        return {"alignment": "insufficient_data"}

    if ma5 > ma10 > ma20:
        spread = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0.0
        alignment = "bullish"
        detail = "strong_bullish" if spread > 5 else "bullish"
    elif ma5 < ma10 < ma20:
        spread = (ma20 - ma5) / ma20 * 100 if ma20 > 0 else 0.0
        alignment = "bearish"
        detail = "strong_bearish" if spread > 5 else "bearish"
    elif ma5 > ma10 >= ma20:
        alignment = detail = "weak_bullish"
    elif ma5 < ma10 <= ma20:
        alignment = detail = "weak_bearish"
    else:
        alignment = detail = "consolidation"
    return {"alignment": alignment, "alignment_detail": detail}


def _max_drawdown_pct(closes: list[float]) -> float | None:
    if len(closes) < 2:
        return None
    peak = closes[0]
    worst = 0.0
    for close in closes:
        peak = max(peak, close)
        if peak > 0:
            worst = max(worst, (peak - close) / peak * 100)
    return worst


def _atr(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> float | None:
    if len(closes) < period + 1:
        return None
    true_ranges = []
    for index in range(1, len(closes)):
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            )
        )
    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = (atr * (period - 1) + true_range) / period
    return atr


def technical_indicators(args: dict[str, Any]) -> dict[str, Any]:
    closes = _as_series(args, "closes", required=True, minimum=0.0000001)
    assert closes is not None
    if len(closes) < 2:
        raise ToolInputError("'closes' needs at least 2 values")

    highs = _as_series(args, "highs", required=False)
    lows = _as_series(args, "lows", required=False)
    volumes = _as_series(args, "volumes", required=False, minimum=0.0)
    if (highs is None) != (lows is None):
        raise ToolInputError("'highs' and 'lows' must be provided together")
    for name, series in (("highs", highs), ("lows", lows), ("volumes", volumes)):
        if series is not None and len(series) != len(closes):
            raise ToolInputError(
                f"'{name}' must contain {len(closes)} values to match 'closes'"
            )

    raw_periods = args.get("ma_periods") or [5, 10, 20, 60]
    if not isinstance(raw_periods, list) or not raw_periods:
        raise ToolInputError("'ma_periods' must be a non-empty array of integers")
    periods: list[int] = []
    for value in raw_periods:
        if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 250:
            raise ToolInputError("each entry in 'ma_periods' must be an integer between 2 and 250")
        periods.append(value)

    ma: dict[str, Any] = {}
    for period in dict.fromkeys(periods):
        ma[f"MA{period}"] = _round(_sma_last(closes, period))
    ma.update(_ma_alignment(ma))

    rsi14 = _rsi(closes, 14)
    macd = _macd(closes)

    bollinger: dict[str, Any] = {"period": 20, "multiplier": 2.0}
    if len(closes) >= 20:
        window = closes[-20:]
        middle = sum(window) / 20
        deviation = statistics.pstdev(window)
        upper = middle + 2 * deviation
        lower = middle - 2 * deviation
        last = closes[-1]
        bollinger.update(
            {
                "middle": _round(middle),
                "upper": _round(upper),
                "lower": _round(lower),
                "percent_b": _round(
                    (last - lower) / (upper - lower) * 100 if upper > lower else None, 2
                ),
                "bandwidth_pct": _round(
                    (upper - lower) / middle * 100 if middle > 0 else None, 2
                ),
            }
        )
    else:
        bollinger.update(
            {"middle": None, "upper": None, "lower": None, "percent_b": None, "bandwidth_pct": None}
        )

    returns_pct: dict[str, float | None] = {}
    for horizon in (5, 20, 60):
        if len(closes) > horizon:
            returns_pct[f"{horizon}d"] = _round((closes[-1] / closes[-1 - horizon] - 1) * 100, 2)
        else:
            returns_pct[f"{horizon}d"] = None

    annualized_volatility_pct = None
    if len(closes) >= 21:
        daily_returns = [
            closes[index] / closes[index - 1] - 1 for index in range(len(closes) - 20, len(closes))
        ]
        annualized_volatility_pct = _round(
            statistics.stdev(daily_returns) * math.sqrt(TRADING_DAYS_PER_YEAR) * 100, 2
        )

    atr_value = _atr(highs, lows, closes) if highs is not None and lows is not None else None

    volume: dict[str, Any] | None = None
    if volumes is not None and len(volumes) >= 20:
        average_5 = sum(volumes[-5:]) / 5
        average_20 = sum(volumes[-20:]) / 20
        ratio = average_5 / average_20 if average_20 > 0 else None
        if ratio is None:
            trend = "insufficient_data"
        elif ratio >= 1.3:
            trend = "expanding"
        elif ratio <= 0.7:
            trend = "shrinking"
        else:
            trend = "normal"
        volume = {"vol_ratio_5_20": _round(ratio, 2), "trend": trend}

    return {
        "bars": len(closes),
        "last_close": _round(closes[-1]),
        "ma": ma,
        "macd": macd,
        "rsi": {"RSI14": _round(rsi14, 2), "zone": _rsi_zone(rsi14)},
        "bollinger": bollinger,
        "returns_pct": returns_pct,
        "annualized_volatility_pct": annualized_volatility_pct,
        "max_drawdown_pct": _round(_max_drawdown_pct(closes), 2),
        "atr": {
            "ATR14": _round(atr_value),
            "atr_pct_of_price": _round(
                atr_value / closes[-1] * 100 if atr_value is not None else None, 2
            ),
        }
        if atr_value is not None
        else None,
        "volume": volume,
    }


# ---------------------------------------------------------------------------
# Tool 3: position sizing
# ---------------------------------------------------------------------------

_DEFAULT_LOT_SIZES = {"cn_a": 100, "cn_hk": 100, "us": 1}


def position_plan(args: dict[str, Any]) -> dict[str, Any]:
    equity = _as_number(args, "equity", minimum=0.0000001)
    entry = _as_number(args, "entry", minimum=0.0000001)
    stop = _as_number(args, "stop", minimum=0.0000001)
    target = _as_number(args, "target", required=False, minimum=0.0000001)
    risk_pct = _as_number(args, "risk_pct", required=False, default=1.0, minimum=0.0001, maximum=100)
    max_position_pct = _as_number(
        args, "max_position_pct", required=False, default=30.0, minimum=0.0001, maximum=100
    )
    market = str(args.get("market") or "cn_a").strip().lower()
    if market == "hk":
        market = "cn_hk"
    if market not in _DEFAULT_LOT_SIZES:
        raise ToolInputError("'market' must be one of: cn_a, cn_hk, us")
    assert equity is not None and entry is not None and stop is not None
    assert risk_pct is not None and max_position_pct is not None

    raw_lot = args.get("lot_size")
    if raw_lot is None:
        lot_size = _DEFAULT_LOT_SIZES[market]
        lot_assumed = True
    else:
        if isinstance(raw_lot, bool) or not isinstance(raw_lot, int) or raw_lot <= 0:
            raise ToolInputError("'lot_size' must be a positive integer")
        lot_size = raw_lot
        lot_assumed = False

    if stop == entry:
        raise ToolInputError("'stop' must differ from 'entry'")
    direction = "long" if stop < entry else "short"
    per_share_risk = abs(entry - stop)
    risk_budget = equity * risk_pct / 100
    raw_shares = risk_budget / per_share_risk
    shares_by_risk = int(raw_shares // lot_size) * lot_size
    shares_by_value = int((equity * max_position_pct / 100) / entry // lot_size) * lot_size
    shares = min(shares_by_risk, shares_by_value)

    warnings: list[str] = []
    if lot_assumed and market != "us":
        warnings.append(
            f"lot_size not supplied; assumed {lot_size} for market '{market}'. "
            "Pass lot_size when the board lot is known."
        )
    if shares_by_value < shares_by_risk:
        warnings.append(
            f"size capped by max_position_pct={max_position_pct:g}% of equity"
        )
    if shares <= 0:
        minimum_capital = per_share_risk * lot_size / (risk_pct / 100)
        warnings.append(
            "risk budget or position cap is too small for one lot; "
            f"one lot ({lot_size} shares) needs about {_round(minimum_capital, 2)} equity "
            f"at risk_pct={risk_pct:g}%"
        )

    position_value = shares * entry
    actual_risk = shares * per_share_risk
    take_profit = {}
    for multiple in (1, 2, 3):
        if direction == "long":
            price = entry + multiple * per_share_risk
        else:
            price = entry - multiple * per_share_risk
        if price > 0:
            take_profit[f"{multiple}R"] = _round(price)

    result: dict[str, Any] = {
        "direction": direction,
        "market": market,
        "lot_size": lot_size,
        "lots": shares // lot_size,
        "shares": shares,
        "entry": _round(entry),
        "stop": _round(stop),
        "target": _round(target),
        "stop_distance_pct": _round(per_share_risk / entry * 100, 2),
        "risk_budget": _round(risk_budget, 2),
        "position_value": _round(position_value, 2),
        "position_pct_of_equity": _round(position_value / equity * 100, 2),
        "actual_risk_amount": _round(actual_risk, 2),
        "actual_risk_pct_of_equity": _round(actual_risk / equity * 100, 2),
        "take_profit_1r_2r_3r": take_profit,
        "risk_reward_ratio": None,
        "expected_profit_at_target": None,
        "warnings": warnings,
    }
    if target is not None:
        reward_per_share = abs(target - entry)
        result["risk_reward_ratio"] = _round(reward_per_share / per_share_risk, 2)
        result["expected_profit_at_target"] = _round(reward_per_share * shares, 2)
    return result


# ---------------------------------------------------------------------------
# Tool 4: valuation snapshot
# ---------------------------------------------------------------------------


def valuation_snapshot(args: dict[str, Any]) -> dict[str, Any]:
    price = _as_number(args, "price", minimum=0.0000001)
    eps = _as_number(args, "eps", required=False)
    bvps = _as_number(args, "bvps", required=False)
    growth_pct = _as_number(args, "earnings_growth_pct", required=False)
    dividend_per_share = _as_number(args, "dividend_per_share", required=False, minimum=0.0)
    fcf_per_share = _as_number(args, "free_cash_flow_per_share", required=False)
    assert price is not None

    if eps is None and bvps is None:
        raise ToolInputError("provide at least one of 'eps' or 'bvps'")

    notes: list[str] = []
    pe = None
    earnings_yield_pct = None
    if eps is not None:
        if eps > 0:
            pe = price / eps
            earnings_yield_pct = eps / price * 100
        else:
            notes.append("eps <= 0: PE and earnings yield are not meaningful")

    pb = price / bvps if bvps is not None and bvps > 0 else None
    if bvps is not None and bvps <= 0:
        notes.append("bvps <= 0: PB is not meaningful")

    peg = None
    if pe is not None and growth_pct is not None:
        if growth_pct > 0:
            peg = pe / growth_pct
        else:
            notes.append("earnings_growth_pct <= 0: PEG is not meaningful")

    graham = None
    if eps is not None and bvps is not None and eps > 0 and bvps > 0:
        graham = math.sqrt(22.5 * eps * bvps)

    return {
        "price": _round(price),
        "eps": _round(eps),
        "bvps": _round(bvps),
        "pe": _round(pe, 2),
        "pb": _round(pb, 2),
        "peg": _round(peg, 2),
        "earnings_yield_pct": _round(earnings_yield_pct, 2),
        "dividend_yield_pct": _round(
            dividend_per_share / price * 100 if dividend_per_share is not None else None, 2
        ),
        "fcf_yield_pct": _round(fcf_per_share / price * 100 if fcf_per_share is not None else None, 2),
        "graham_number": _round(graham, 2),
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


def _tool(
    name: str,
    description: str,
    properties: dict[str, Any],
    required: list[str],
    handler: ToolHandler,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "handler": handler,
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "normalize_symbol",
        "Normalize a stock code (股票代码) across A-share, Hong Kong and US markets. "
        "Accepts 600519, 000001.SZ, SH600519, 00700, HK00700, 0700.HK, AAPL. "
        "Returns the market, canonical symbol, display code and Yahoo Finance ticker.",
        {
            "code": {"type": "string", "description": "Raw ticker as written by the user."},
            "market": {
                "type": "string",
                "enum": ["auto", "cn_a", "cn_hk", "us"],
                "default": "auto",
                "description": "Force a market instead of inferring it from the code.",
            },
        },
        ["code"],
        normalize_symbol,
    ),
    _tool(
        "technical_indicators",
        "Compute technical indicators (技术指标) from a chronological price series: "
        "SMA and alignment, MACD (DIF/DEA/hist), RSI14, Bollinger(20,2), ATR14, "
        "5/20/60-bar returns, annualized volatility, max drawdown and 5/20 volume ratio. "
        "Use when you already have OHLCV data; this tool does not fetch quotes.",
        {
            "closes": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Close prices, oldest first. The last value is 'now'.",
            },
            "highs": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Highs aligned with closes; required for ATR.",
            },
            "lows": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Lows aligned with closes; required for ATR.",
            },
            "volumes": {
                "type": "array",
                "items": {"type": "number"},
                "description": "Volumes aligned with closes; enables the volume ratio.",
            },
            "ma_periods": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "SMA periods. Defaults to [5, 10, 20, 60]; MA5/10/20 also drive alignment.",
            },
        },
        ["closes"],
        technical_indicators,
    ),
    _tool(
        "position_plan",
        "Size a position (仓位/止损) from account equity, entry, stop and optional target. "
        "Supports long and short, whole lots (A-share 100 shares by default), a position "
        "value cap, R-multiple take-profit levels and risk/reward. Deterministic arithmetic only.",
        {
            "equity": {"type": "number", "description": "Total account equity in account currency."},
            "entry": {"type": "number", "description": "Planned entry price."},
            "stop": {"type": "number", "description": "Stop-loss price; below entry means long, above means short."},
            "target": {"type": "number", "description": "Optional take-profit price."},
            "risk_pct": {
                "type": "number",
                "default": 1.0,
                "description": "Percent of equity to risk (default 1.0).",
            },
            "max_position_pct": {
                "type": "number",
                "default": 30.0,
                "description": "Cap on position value as a percent of equity (default 30).",
            },
            "market": {
                "type": "string",
                "enum": ["cn_a", "cn_hk", "us"],
                "default": "cn_a",
                "description": "Drives the default lot size: cn_a/cn_hk 100, us 1.",
            },
            "lot_size": {
                "type": "integer",
                "description": "Override the board lot; pass it when the HK board lot is known.",
            },
        },
        ["equity", "entry", "stop"],
        position_plan,
    ),
    _tool(
        "valuation_snapshot",
        "Compute valuation ratios (估值) from price and per-share fundamentals: "
        "PE, PB, PEG, earnings yield, dividend yield, FCF yield and Graham number. "
        "All inputs are per-share values supplied by the caller.",
        {
            "price": {"type": "number", "description": "Current share price."},
            "eps": {"type": "number", "description": "Trailing-twelve-month EPS."},
            "bvps": {"type": "number", "description": "Book value per share."},
            "earnings_growth_pct": {
                "type": "number",
                "description": "Expected annual EPS growth in percent, used for PEG.",
            },
            "dividend_per_share": {"type": "number", "description": "Annual dividend per share."},
            "free_cash_flow_per_share": {
                "type": "number",
                "description": "Free cash flow per share.",
            },
        },
        ["price"],
        valuation_snapshot,
    ),
]

_TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def _tool_listing() -> list[dict[str, Any]]:
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["inputSchema"],
        }
        for tool in TOOLS
    ]


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    try:
        name = params.get("name")
        if not isinstance(name, str):
            raise ToolInputError("'name' must be a string")
        tool = _TOOLS_BY_NAME.get(name)
        if tool is None:
            raise ToolInputError(f"unknown tool '{name}'")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ToolInputError("'arguments' must be an object")
        payload = tool["handler"](arguments)
    except ToolInputError as error:
        return {
            "content": [{"type": "text", "text": f"Invalid arguments: {error}"}],
            "isError": True,
        }
    except Exception as error:  # unexpected: keep the session alive
        print(f"[{SERVER_NAME}] tool '{name}' failed: {error!r}", file=sys.stderr, flush=True)
        return {
            "content": [{"type": "text", "text": f"Tool '{name}' failed: {error}"}],
            "isError": True,
        }
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}
        ],
        "isError": False,
    }


# ---------------------------------------------------------------------------
# JSON-RPC over stdio
# ---------------------------------------------------------------------------


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _send_error(message_id: Any, code: int, message: str) -> None:
    _send(
        {
            "jsonrpc": "2.0",
            "id": message_id,
            "error": {"code": code, "message": message},
        }
    )


def _handle(message: dict[str, Any]) -> None:
    method = message.get("method")
    message_id = message.get("id")
    is_notification = "id" not in message

    # Responses to server-initiated requests: this server sends none.
    if method is None:
        return

    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": _tool_listing()}
        elif method == "tools/call":
            params = message.get("params") or {}
            if not isinstance(params, dict):
                raise ToolInputError("'params' must be an object")
            result = _call_tool(params)
        elif is_notification:
            return
        else:
            _send_error(message_id, -32601, f"Method not found: {method}")
            return
    except Exception as error:
        if is_notification:
            return
        _send_error(message_id, -32603, f"Internal error: {error}")
        return

    if is_notification:
        return
    _send({"jsonrpc": "2.0", "id": message_id, "result": result})


def main() -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as error:
            print(f"[{SERVER_NAME}] ignoring malformed JSON: {error}", file=sys.stderr, flush=True)
            continue
        if isinstance(message, dict):
            _handle(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
