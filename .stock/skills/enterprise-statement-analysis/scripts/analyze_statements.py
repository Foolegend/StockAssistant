#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compute the book's statement-analysis metrics from fetched statements.

Usage:
    python .../analyze_statements.py --input .tmp/600519-statements.json
    python .../analyze_statements.py --input .tmp/600519-statements.json --format md

The input is either the JSON produced by fetch_statements.py or a manually
assembled file using the schema in references/data-schema.md.  Monetary values
in the output are in 亿元; ratios are percentages unless stated otherwise.

The output is deliberately a *question generator*: it computes what the
statements can support, flags factual inconsistencies, and lists data gaps.
It does not decide whether the company is a good investment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from _common import (
        COMMERCIAL_LIABILITY_KEYS,
        CONTROLLING_INVESTMENT_KEYS,
        FINANCIAL_LIABILITY_KEYS,
        NON_OPERATING_ASSET_KEYS,
        OPERATING_ASSET_KEYS,
        OTHER_OPERATING_LIABILITY_KEYS,
        RETAINED_KEYS,
        SHAREHOLDER_KEYS,
        average,
        fmt_money,
        fmt_pct,
        fmt_ratio,
        is_financial_industry,
        load_input,
        pct_change,
        ratio_pct,
        round_or_none,
        safe_div,
        sum_keys,
        to_yi,
    )
except ImportError:  # pragma: no cover - direct execution fallback
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import (  # type: ignore
        COMMERCIAL_LIABILITY_KEYS,
        CONTROLLING_INVESTMENT_KEYS,
        FINANCIAL_LIABILITY_KEYS,
        NON_OPERATING_ASSET_KEYS,
        OPERATING_ASSET_KEYS,
        OTHER_OPERATING_LIABILITY_KEYS,
        RETAINED_KEYS,
        SHAREHOLDER_KEYS,
        average,
        fmt_money,
        fmt_pct,
        fmt_ratio,
        is_financial_industry,
        load_input,
        pct_change,
        ratio_pct,
        round_or_none,
        safe_div,
        sum_keys,
        to_yi,
    )


# --------------------------------------------------------------- utilities ---
def _sum_or_zero(*values: float | None) -> float:
    return sum(value for value in values if value is not None)


def _first(*values: float | None) -> float | None:
    for value in values:
        if value is not None:
            return value
    return None


def _receivables(balance: dict[str, Any]) -> float | None:
    combined = balance.get("notes_and_accounts_receivable")
    if combined is not None:
        return combined
    return sum_keys(balance, ("notes_receivable", "accounts_receivable"))


def _commercial_receivables(balance: dict[str, Any]) -> float | None:
    """Receivables used for collection-quality ratios (includes contract assets)."""
    parts = [
        _receivables(balance),
        balance.get("receivables_financing"),
        balance.get("contract_assets"),
    ]
    present = [value for value in parts if value is not None]
    return sum(present) if present else None


def _operating_assets(balance: dict[str, Any]) -> float | None:
    parts: list[float | None] = [_receivables(balance)]
    for key in OPERATING_ASSET_KEYS:
        if key in {"notes_receivable", "accounts_receivable", "notes_and_accounts_receivable"}:
            continue
        parts.append(balance.get(key))
    present = [value for value in parts if value is not None]
    return sum(present) if present else None


def _non_operating_assets(balance: dict[str, Any]) -> float | None:
    return sum_keys(balance, NON_OPERATING_ASSET_KEYS)


def _commercial_liabilities(balance: dict[str, Any]) -> float | None:
    combined = balance.get("notes_and_accounts_payable")
    if combined is not None:
        base = combined
    else:
        base = _sum_or_zero(balance.get("notes_payable"), balance.get("accounts_payable"))
    parts = [base, balance.get("contract_liabilities"), balance.get("advance_receipts")]
    present = [value for value in parts if value is not None]
    return sum(present) if present else None


def _financial_liabilities(balance: dict[str, Any]) -> float | None:
    keys = tuple(
        key
        for key in FINANCIAL_LIABILITY_KEYS
        if key not in {"interest_payable", "dividend_payable"}
    )
    core = sum_keys(balance, keys)
    # Interest/dividend payable are often inside "其他应付款"; add only when
    # other payables are not separately available, to avoid double counting.
    if balance.get("other_payables") is None:
        extra = sum_keys(balance, ("interest_payable", "dividend_payable"))
        if extra is not None:
            core = (core or 0) + extra
    return core


def _core_profit(income: dict[str, Any]) -> tuple[float | None, list[str]]:
    missing: list[str] = []
    revenue = _first(income.get("operating_income"), income.get("total_operating_income"))
    cost = income.get("operating_cost")
    if revenue is None:
        return None, ["营业收入"]
    if cost is None:
        return None, ["营业成本"]
    parts = {
        "税金及附加": income.get("taxes_and_surcharges"),
        "销售费用": income.get("selling_expenses"),
        "管理费用": income.get("administrative_expenses"),
        "研发费用": income.get("research_expenses"),
        "利息费用": _first(income.get("interest_expense_finance"), income.get("interest_expense")),
    }
    for label, value in parts.items():
        if value is None:
            missing.append(label)
    core = revenue - cost
    for value in parts.values():
        if value is not None:
            core -= value
    return core, missing


def _misc_income(income: dict[str, Any]) -> tuple[float | None, str]:
    # "利息收入" in 营业总收入 and "利息收入" inside 财务费用 are separate
    # presentation lines; both are included in operating profit.  Sum them when
    # both exist, otherwise use whichever is available.
    interest_parts = [
        value
        for value in (income.get("interest_income"), income.get("interest_income_finance"))
        if value is not None
    ]
    interest = sum(interest_parts) if interest_parts else None
    investment = income.get("investment_income")
    fair_value = income.get("fair_value_change_income")
    present = [value for value in (interest, investment, fair_value) if value is not None]
    if not present:
        return None, "利息收入、投资收益、公允价值变动收益均缺失"
    if len(interest_parts) == 2:
        source = "利息收入（营业总收入 + 财务费用）"
    elif interest_parts:
        source = "利息收入"
    else:
        source = "财务费用中的利息收入"
    return sum(present), source


def _impairment_loss(income: dict[str, Any]) -> tuple[float | None, bool]:
    asset_loss = income.get("asset_impairment_loss")
    credit_loss = income.get("credit_impairment_loss")
    present = [value for value in (asset_loss, credit_loss) if value is not None]
    if not present:
        return None, False
    # Positive = loss, negative = reversal/gain.  Net them: a credit-impairment
    # reversal should reduce, not increase, the total impairment burden.
    return sum(present), True


def _cagr(periods: list[str], values: list[float | None]) -> float | None:
    pairs = [(period, value) for period, value in zip(periods, values) if value is not None]
    if len(pairs) < 2:
        return None
    first_period, first_value = pairs[0]
    last_period, last_value = pairs[-1]
    if first_value is None or last_value is None or first_value <= 0 or last_value <= 0:
        return None
    years = int(last_period) - int(first_period)
    if years <= 0:
        return None
    return round(((last_value / first_value) ** (1 / years) - 1) * 100, 2)


# ----------------------------------------------------------------- analysis --
def _analyze_financial_period(
    period: str,
    block: dict[str, Any],
    previous: dict[str, Any] | None,
) -> dict[str, Any]:
    """Reduced output for banks/insurers/brokers.

    The book's core-profit and operating/non-operating asset split do not map
    onto financial statements; showing those numbers would be misleading.
    """
    balance = block.get("balance") or {}
    income = block.get("income") or {}
    cashflow = block.get("cashflow") or {}
    prev_balance = (previous or {}).get("balance") or {}
    prev_income = (previous or {}).get("income") or {}
    revenue = _first(income.get("operating_income"), income.get("total_operating_income"))
    net_profit = income.get("net_profit")
    parent_net_profit = income.get("parent_net_profit")
    total_assets = balance.get("total_assets") or balance.get("total_liab_equity")
    total_liabilities = balance.get("total_liabilities")
    total_equity = balance.get("total_equity")
    parent_equity = balance.get("parent_equity")
    avg_total_assets = average(total_assets, prev_balance.get("total_assets") or prev_balance.get("total_liab_equity"))
    avg_total_equity = average(total_equity, prev_balance.get("total_equity"))
    avg_parent_equity = average(parent_equity, prev_balance.get("parent_equity"))
    roe = safe_div(parent_net_profit, avg_parent_equity)
    if roe is None:
        roe = safe_div(net_profit, avg_total_equity)
    roa = safe_div(net_profit, avg_total_assets)
    return {
        "period": period,
        "is_financial": True,
        "revenue_yi": to_yi(revenue),
        "total_operating_income_yi": to_yi(income.get("total_operating_income")),
        "growth": {
            "revenue_yoy_pct": pct_change(
                revenue,
                prev_income.get("operating_income") or prev_income.get("total_operating_income"),
            )
            if previous
            else None,
            "net_profit_yoy_pct": pct_change(net_profit, prev_income.get("net_profit")) if previous else None,
            "total_assets_yoy_pct": pct_change(
                total_assets,
                prev_balance.get("total_assets") or prev_balance.get("total_liab_equity"),
            )
            if previous
            else None,
        },
        "profit_structure": {
            "operating_profit_yi": to_yi(income.get("operating_profit")),
            "net_profit_yi": to_yi(net_profit),
            "parent_net_profit_yi": to_yi(parent_net_profit),
            "minority_interest_yi": to_yi(income.get("minority_interest")),
        },
        "assets": {"total_assets_yi": to_yi(total_assets)},
        "capital": {
            "total_liabilities_yi": to_yi(total_liabilities),
            "total_equity_yi": to_yi(total_equity),
            "debt_ratio_pct": ratio_pct(total_liabilities, total_assets),
        },
        "cashflow": {
            "operating_cash_flow_yi": to_yi(cashflow.get("operating_cash_flow")),
            "cash_change_yi": to_yi(cashflow.get("cash_change")),
        },
        "quality": {
            "roe_pct": None if roe is None else round(roe * 100, 2),
            "roa_pct": None if roa is None else round(roa * 100, 2),
            "net_margin_pct": ratio_pct(net_profit, revenue),
        },
        "data_notes": {"financial_industry": True},
    }


def analyze_period(
    period: str,
    block: dict[str, Any],
    previous: dict[str, Any] | None,
    meta: dict[str, Any],
    is_financial: bool,
) -> dict[str, Any]:
    if is_financial:
        return _analyze_financial_period(period, block, previous)
    balance = block.get("balance") or {}
    income = block.get("income") or {}
    cashflow = block.get("cashflow") or {}
    prev_balance = (previous or {}).get("balance") or {}
    prev_income = (previous or {}).get("income") or {}
    prev_cashflow = (previous or {}).get("cashflow") or {}

    revenue = _first(income.get("operating_income"), income.get("total_operating_income"))
    total_operating_income = income.get("total_operating_income")
    operating_cost = income.get("operating_cost")
    core_profit, core_missing = _core_profit(income)
    other_income = income.get("other_income")
    misc_income, misc_source = _misc_income(income)
    impairment_loss, impairment_present = _impairment_loss(income)
    operating_profit = income.get("operating_profit")
    asset_disposal = income.get("asset_disposal_income")

    gross_profit = None
    if revenue is not None and operating_cost is not None:
        gross_profit = revenue - operating_cost

    # Profit structure ("三支柱两减值").
    profit_structure: dict[str, Any] = {
        "core_profit_yi": to_yi(core_profit),
        "other_income_yi": to_yi(other_income),
        "misc_income_yi": to_yi(misc_income),
        "asset_disposal_income_yi": to_yi(asset_disposal),
        "asset_impairment_loss_yi": to_yi(income.get("asset_impairment_loss")),
        "credit_impairment_loss_yi": to_yi(income.get("credit_impairment_loss")),
        "impairment_loss_yi": to_yi(impairment_loss),
        "operating_profit_yi": to_yi(operating_profit),
        "net_profit_yi": to_yi(income.get("net_profit")),
        "parent_net_profit_yi": to_yi(income.get("parent_net_profit")),
        "minority_interest_yi": to_yi(income.get("minority_interest")),
    }
    residual = None
    if operating_profit is not None:
        known = _sum_or_zero(
            core_profit,
            other_income,
            misc_income,
            asset_disposal,
            -impairment_loss if impairment_loss is not None else None,
        )
        residual = operating_profit - known
    profit_structure["residual_yi"] = to_yi(residual)
    if operating_profit not in (None, 0):
        profit_structure["core_profit_share_pct"] = ratio_pct(core_profit, operating_profit)
        profit_structure["other_income_share_pct"] = ratio_pct(other_income, operating_profit)
        profit_structure["misc_income_share_pct"] = ratio_pct(misc_income, operating_profit)
        profit_structure["impairment_share_pct"] = ratio_pct(impairment_loss, operating_profit)
        profit_structure["residual_share_pct"] = ratio_pct(residual, operating_profit)

    # Assets and returns.
    operating_assets = _operating_assets(balance)
    non_operating_assets = _non_operating_assets(balance)
    total_assets = balance.get("total_assets") or balance.get("total_liab_equity")
    unclassified_assets = None
    if total_assets is not None:
        unclassified_assets = total_assets - _sum_or_zero(operating_assets, non_operating_assets)
    prev_operating_assets = _operating_assets(prev_balance) if prev_balance else None
    prev_non_operating_assets = _non_operating_assets(prev_balance) if prev_balance else None
    avg_operating_assets = average(operating_assets, prev_operating_assets)
    avg_non_operating_assets = average(non_operating_assets, prev_non_operating_assets)
    avg_total_assets = average(total_assets, prev_balance.get("total_assets") or prev_balance.get("total_liab_equity"))

    core_plus_other = None
    if core_profit is not None:
        core_plus_other = core_profit + _sum_or_zero(other_income)
    operating_asset_return = safe_div(core_plus_other, avg_operating_assets)
    non_operating_asset_return = safe_div(misc_income, avg_non_operating_assets)
    operating_asset_turnover = safe_div(revenue, avg_operating_assets)
    total_asset_turnover = safe_div(revenue, avg_total_assets)

    assets: dict[str, Any] = {
        "total_assets_yi": to_yi(total_assets),
        "operating_assets_yi": to_yi(operating_assets),
        "non_operating_assets_yi": to_yi(non_operating_assets),
        "unclassified_assets_yi": to_yi(unclassified_assets),
        "operating_assets_share_pct": ratio_pct(operating_assets, total_assets),
        "non_operating_assets_share_pct": ratio_pct(non_operating_assets, total_assets),
        "avg_operating_assets_yi": to_yi(avg_operating_assets),
        "avg_non_operating_assets_yi": to_yi(avg_non_operating_assets),
        "operating_asset_return_pct": None if operating_asset_return is None else round(operating_asset_return * 100, 2),
        "non_operating_asset_return_pct": None if non_operating_asset_return is None else round(non_operating_asset_return * 100, 2),
        "operating_asset_turnover": round_or_none(operating_asset_turnover, 2),
        "total_asset_turnover": round_or_none(total_asset_turnover, 2),
    }

    # Capital sources ("四度").
    commercial_liabilities = _commercial_liabilities(balance)
    other_operating_liabilities = sum_keys(balance, OTHER_OPERATING_LIABILITY_KEYS)
    financial_liabilities = _financial_liabilities(balance)
    shareholder_contribution = sum_keys(balance, SHAREHOLDER_KEYS)
    retained_earnings = sum_keys(balance, RETAINED_KEYS)
    total_equity = balance.get("total_equity")
    total_liabilities = balance.get("total_liabilities")
    other_sources = None
    if total_assets is not None:
        other_sources = total_assets - _sum_or_zero(
            commercial_liabilities,
            other_operating_liabilities,
            financial_liabilities,
            shareholder_contribution,
            retained_earnings,
            balance.get("minority_equity"),
        )
    monetary_funds = balance.get("monetary_funds")
    net_debt = None
    if financial_liabilities is not None:
        net_debt = financial_liabilities - _sum_or_zero(monetary_funds)
    interest_expense = _first(income.get("interest_expense_finance"), income.get("interest_expense"))
    interest_coverage = None
    total_profit = income.get("total_profit")
    if interest_expense not in (None, 0) and total_profit is not None:
        interest_coverage = (total_profit + interest_expense) / interest_expense

    capital: dict[str, Any] = {
        "commercial_liabilities_yi": to_yi(commercial_liabilities),
        "other_operating_liabilities_yi": to_yi(other_operating_liabilities),
        "financial_liabilities_yi": to_yi(financial_liabilities),
        "shareholder_contribution_yi": to_yi(shareholder_contribution),
        "retained_earnings_yi": to_yi(retained_earnings),
        "other_sources_yi": to_yi(other_sources),
        "total_liabilities_yi": to_yi(total_liabilities),
        "total_equity_yi": to_yi(total_equity),
        "commercial_debt_share_pct": ratio_pct(commercial_liabilities, total_assets),
        "financial_debt_share_pct": ratio_pct(financial_liabilities, total_assets),
        "shareholder_contribution_share_pct": ratio_pct(shareholder_contribution, total_assets),
        "retained_earnings_share_pct": ratio_pct(retained_earnings, total_assets),
        "debt_ratio_pct": ratio_pct(total_liabilities, total_assets),
        "financial_debt_ratio_pct": ratio_pct(financial_liabilities, total_assets),
        "net_debt_yi": to_yi(net_debt),
        "net_debt_to_equity_pct": ratio_pct(net_debt, total_equity),
        "interest_coverage": round_or_none(interest_coverage, 2),
    }

    # Cash flow and quality.
    ocf = cashflow.get("operating_cash_flow")
    core_profit_plus_other = core_plus_other
    cash_conversion = safe_div(ocf, core_profit_plus_other if core_profit_plus_other and core_profit_plus_other > 0 else None)
    da = sum_keys(
        cashflow,
        (
            "fixed_asset_depreciation",
            "intangible_amortization",
            "long_term_prepaid_amortization",
            "right_of_use_asset_amortization",
        ),
    )
    capex = cashflow.get("capex")
    external_investment = cashflow.get("external_investment_cash")
    acquire_subsidiary = cashflow.get("acquire_subsidiary_cash")
    investment_parts = [capex, external_investment, acquire_subsidiary]
    present_investment = [value for value in investment_parts if value is not None]
    total_investment_outflow = sum(present_investment) if present_investment else None
    free_cash_flow = None if ocf is None or capex is None else ocf - capex
    capex_to_ocf = safe_div(capex, ocf)
    investment_outflow_to_ocf = safe_div(total_investment_outflow, ocf)
    dividends_interest_paid = cashflow.get("dividends_interest_paid")
    ocf_after_da = None if ocf is None or da is None else ocf - da
    ocf_after_interest = None if ocf is None or interest_expense is None else ocf - interest_expense
    ocf_after_dividends = None if ocf is None or dividends_interest_paid is None else ocf - dividends_interest_paid
    inflow_parts = [
        cashflow.get("equity_financing_cash"),
        cashflow.get("borrowings_cash"),
        cashflow.get("bonds_cash"),
    ]
    present_inflow = [value for value in inflow_parts if value is not None]
    financing_inflow = sum(present_inflow) if present_inflow else None
    outflow_parts = [
        cashflow.get("debt_repayment_cash"),
        cashflow.get("dividends_interest_paid"),
    ]
    present_outflow = [value for value in outflow_parts if value is not None]
    financing_outflow = sum(present_outflow) if present_outflow else None
    net_financing = None
    if financing_inflow is not None or financing_outflow is not None:
        net_financing = _sum_or_zero(financing_inflow) - _sum_or_zero(financing_outflow)

    cashflow_metrics: dict[str, Any] = {
        "operating_cash_flow_yi": to_yi(ocf),
        "core_profit_plus_other_income_yi": to_yi(core_profit_plus_other),
        "cash_conversion": round_or_none(cash_conversion, 2),
        "depreciation_amortization_yi": to_yi(da),
        "ocf_after_da_yi": to_yi(ocf_after_da),
        "ocf_after_interest_yi": to_yi(ocf_after_interest),
        "ocf_after_dividends_interest_yi": to_yi(ocf_after_dividends),
        "capex_yi": to_yi(capex),
        "external_investment_cash_yi": to_yi(external_investment),
        "acquire_subsidiary_cash_yi": to_yi(acquire_subsidiary),
        "total_investment_outflow_yi": to_yi(total_investment_outflow),
        "free_cash_flow_yi": to_yi(free_cash_flow),
        "capex_to_ocf": round_or_none(capex_to_ocf, 2),
        "investment_outflow_to_ocf": round_or_none(investment_outflow_to_ocf, 2),
        "equity_financing_cash_yi": to_yi(cashflow.get("equity_financing_cash")),
        "borrowings_cash_yi": to_yi(cashflow.get("borrowings_cash")),
        "bonds_cash_yi": to_yi(cashflow.get("bonds_cash")),
        "debt_repayment_cash_yi": to_yi(cashflow.get("debt_repayment_cash")),
        "dividends_interest_paid_yi": to_yi(dividends_interest_paid),
        "financing_inflow_yi": to_yi(financing_inflow),
        "financing_outflow_yi": to_yi(financing_outflow),
        "net_financing_yi": to_yi(net_financing),
        "cash_change_yi": to_yi(cashflow.get("cash_change")),
    }

    receivables = _commercial_receivables(balance)
    prev_receivables = _commercial_receivables(prev_balance) if prev_balance else None
    inventory = balance.get("inventory")
    prev_inventory = prev_balance.get("inventory") if prev_balance else None
    avg_inventory = average(inventory, prev_inventory)
    inventory_turnover = safe_div(operating_cost, avg_inventory)
    fixed_assets = balance.get("fixed_assets")
    avg_fixed_assets = average(fixed_assets, prev_balance.get("fixed_assets") if prev_balance else None)
    fixed_asset_turnover = safe_div(revenue, avg_fixed_assets)
    short_term_debt = _sum_or_zero(
        balance.get("short_term_loans"),
        balance.get("noncurrent_liabilities_due_within_one_year"),
        balance.get("short_bond_payable"),
    )
    parent_net_profit = income.get("parent_net_profit")
    net_profit = income.get("net_profit")
    parent_equity = balance.get("parent_equity")
    avg_parent_equity = average(parent_equity, prev_balance.get("parent_equity") if prev_balance else None)
    avg_total_equity = average(total_equity, prev_balance.get("total_equity") if prev_balance else None)
    roe = safe_div(parent_net_profit, avg_parent_equity)
    if roe is None:
        roe = safe_div(net_profit, avg_total_equity)
    roa = safe_div(net_profit, avg_total_assets)

    quality: dict[str, Any] = {
        "receivables_yi": to_yi(receivables),
        "receivables_to_revenue_pct": ratio_pct(receivables, revenue),
        "receivables_growth_pct": pct_change(receivables, prev_receivables) if prev_receivables is not None else None,
        "inventory_yi": to_yi(inventory),
        "inventory_growth_pct": pct_change(inventory, prev_inventory) if prev_inventory is not None else None,
        "inventory_to_cogs_pct": ratio_pct(inventory, operating_cost),
        "inventory_turnover": round_or_none(inventory_turnover, 2),
        "inventory_days": None if inventory_turnover in (None, 0) else round(365 / inventory_turnover, 1),
        "goodwill_yi": to_yi(balance.get("goodwill")),
        "goodwill_to_equity_pct": ratio_pct(
            balance.get("goodwill"),
            balance.get("parent_equity") or total_equity,
        ),
        "goodwill_to_assets_pct": ratio_pct(balance.get("goodwill"), total_assets),
        "other_receivables_yi": to_yi(balance.get("other_receivables")),
        "other_receivables_to_assets_pct": ratio_pct(balance.get("other_receivables"), total_assets),
        "other_receivables_to_revenue_pct": ratio_pct(balance.get("other_receivables"), revenue),
        "construction_in_progress_yi": to_yi(balance.get("construction_in_progress")),
        "cip_to_fixed_assets_pct": ratio_pct(balance.get("construction_in_progress"), fixed_assets),
        "cip_to_assets_pct": ratio_pct(balance.get("construction_in_progress"), total_assets),
        "monetary_funds_yi": to_yi(monetary_funds),
        "monetary_funds_to_assets_pct": ratio_pct(monetary_funds, total_assets),
        "monetary_funds_to_short_term_debt": round_or_none(safe_div(monetary_funds, short_term_debt), 2),
        "fixed_asset_turnover_net": round_or_none(fixed_asset_turnover, 2),
        "roe_pct": None if roe is None else round(roe * 100, 2),
        "roa_pct": None if roa is None else round(roa * 100, 2),
        "gross_margin_pct": ratio_pct(gross_profit, revenue),
        "core_margin_pct": ratio_pct(core_profit, revenue),
        "net_margin_pct": ratio_pct(net_profit, revenue),
        "parent_net_margin_pct": ratio_pct(parent_net_profit, revenue),
        "selling_expense_ratio_pct": ratio_pct(income.get("selling_expenses"), revenue),
        "admin_expense_ratio_pct": ratio_pct(income.get("administrative_expenses"), revenue),
        "research_expense_ratio_pct": ratio_pct(income.get("research_expenses"), revenue),
        "financial_expense_ratio_pct": ratio_pct(income.get("financial_expenses"), revenue),
    }

    growth = {
        "revenue_yoy_pct": pct_change(revenue, prev_income.get("operating_income") or prev_income.get("total_operating_income")) if previous else None,
        "core_profit_yoy_pct": pct_change(core_profit, _core_profit(prev_income)[0]) if previous else None,
        "net_profit_yoy_pct": pct_change(net_profit, prev_income.get("net_profit")) if previous else None,
        "parent_net_profit_yoy_pct": pct_change(parent_net_profit, prev_income.get("parent_net_profit")) if previous else None,
        "operating_cash_flow_yoy_pct": pct_change(ocf, prev_cashflow.get("operating_cash_flow")) if previous else None,
        "total_assets_yoy_pct": pct_change(total_assets, prev_balance.get("total_assets") or prev_balance.get("total_liab_equity")) if previous else None,
    }

    return {
        "period": period,
        "is_financial": is_financial,
        "revenue_yi": to_yi(revenue),
        "total_operating_income_yi": to_yi(total_operating_income),
        "gross_profit_yi": to_yi(gross_profit),
        "growth": growth,
        "profit_structure": profit_structure,
        "assets": assets,
        "capital": capital,
        "cashflow": cashflow_metrics,
        "quality": quality,
        "data_notes": {
            "core_profit_missing_components": core_missing,
            "misc_income_source": misc_source,
            "impairment_fields_present": impairment_present,
        },
    }


def _signals(
    metrics: dict[str, dict[str, Any]],
    latest: str,
    periods: list[str],
    meta: dict[str, Any],
) -> list[dict[str, Any]]:
    latest_m = metrics[latest]
    signals: list[dict[str, Any]] = []
    quality = latest_m["quality"]
    capital = latest_m["capital"]
    cash = latest_m["cashflow"]
    profit = latest_m["profit_structure"]
    assets = latest_m["assets"]
    industry = str(meta.get("industry") or "")
    if meta.get("is_financial") or is_financial_industry(industry, meta.get("company_type")):
        return [
            {
                "level": "info",
                "category": "data",
                "id": "financial_industry",
                "message": "金融行业：核心利润、经营资产分类和通用八看信号不适用",
                "evidence": "请改用净息差、不良率、拨备覆盖率、资本充足率等行业专用指标；通用报表仅作辅助。",
                "period": latest,
            }
        ]

    def add(level: str, category: str, signal_id: str, message: str, evidence: str) -> None:
        signals.append(
            {
                "level": level,
                "category": category,
                "id": signal_id,
                "message": message,
                "evidence": evidence,
                "period": latest,
            }
        )

    core = profit.get("core_profit_yi")
    if core is not None and core <= 0:
        add("high", "profit_quality", "core_profit_negative", "营业收入未带来正的核心利润", f"{latest} 核心利润 {core:,.2f} 亿元")
    if quality.get("core_margin_pct") is not None and quality["core_margin_pct"] < 0:
        add("high", "profit_quality", "core_margin_negative", "核心利润率为负，主营经营亏损", f"{latest} 核心利润率 {quality['core_margin_pct']:.2f}%")

    inventory_turnover = quality.get("inventory_turnover")
    # The book's 1.2~1.5x benchmark is explicitly conditioned on inventory
    # turnover above ~2x per year.  For slow-cycle businesses (liquor aging,
    # real estate, construction), judge cash conversion over the operating
    # cycle instead of applying the benchmark mechanically.
    fast_cycle = inventory_turnover is None or inventory_turnover >= 2
    conversion = cash.get("cash_conversion")
    if conversion is not None and fast_cycle:
        if conversion < 0.5:
            add("high", "cash_quality", "cash_conversion_low", "核心利润获现率显著偏低", f"{latest} 经营现金流/（核心利润+其他收益）= {conversion:.2f}")
        elif conversion < 0.8:
            add("medium", "cash_quality", "cash_conversion_weak", "核心利润获现率偏低，利润含金量需核查", f"{latest} 获现率 {conversion:.2f}")
        elif conversion > 2.5:
            add("info", "cash_quality", "cash_conversion_high", "获现率很高，可能受预收/应付或营运资本变动驱动", f"{latest} 获现率 {conversion:.2f}")
    elif conversion is not None and not fast_cycle and conversion < 0.3:
        add("medium", "cash_quality", "cash_conversion_very_low", "获现率很低；但存货周转低于2次，应按经营周期判断", f"{latest} 获现率 {conversion:.2f}，存货周转 {inventory_turnover:.2f} 次")
    elif (core is None or core <= 0) and cash.get("operating_cash_flow_yi") is not None:
        add("info", "cash_quality", "cash_conversion_na", "核心利润不可计算或为负，获现率不适用", f"{latest} 经营现金流 {cash['operating_cash_flow_yi']:,.2f} 亿元")

    if cash.get("operating_cash_flow_yi") is not None and cash["operating_cash_flow_yi"] < 0 and profit.get("net_profit_yi", 0) and profit["net_profit_yi"] > 0:
        add("high", "cash_quality", "profit_without_cash", "净利润为正但经营现金流为负", f"{latest} 净利润 {profit['net_profit_yi']:,.2f} 亿元，经营现金流 {cash['operating_cash_flow_yi']:,.2f} 亿元")

    gap = None
    if quality.get("receivables_growth_pct") is not None and latest_m["growth"].get("revenue_yoy_pct") is not None:
        gap = quality["receivables_growth_pct"] - latest_m["growth"]["revenue_yoy_pct"]
    if gap is not None and gap > 20:
        add("high" if gap > 40 else "medium", "asset_quality", "receivables_outgrow_revenue", "应收增速显著高于收入增速，回款质量恶化", f"{latest} 应收增速 {quality['receivables_growth_pct']:.2f}%，收入增速 {latest_m['growth']['revenue_yoy_pct']:.2f}%")

    if quality.get("goodwill_to_equity_pct") is not None and quality["goodwill_to_equity_pct"] > 30:
        add("high" if quality["goodwill_to_equity_pct"] > 50 else "medium", "asset_quality", "goodwill_high", "商誉占净资产比重较高，关注减值风险", f"{latest} 商誉/净资产 {quality['goodwill_to_equity_pct']:.2f}%")
    if quality.get("other_receivables_to_assets_pct") is not None and quality["other_receivables_to_assets_pct"] > 5:
        add("high" if quality["other_receivables_to_assets_pct"] > 10 else "medium", "asset_quality", "other_receivables_high", "其他应收款占比偏高，核查关联方资金占用", f"{latest} 其他应收款/总资产 {quality['other_receivables_to_assets_pct']:.2f}%")
    if inventory_turnover is not None and inventory_turnover < 2:
        slow_cycle_industry = any(
            word in industry for word in ("白酒", "酒", "房地产", "建筑", "工程", "船舶", "航空")
        )
        if slow_cycle_industry:
            add("info", "asset_quality", "inventory_turnover_low", "存货周转低；该行业可能属于长周期/陈化模式，需结合行业判断", f"{latest} 存货周转率 {inventory_turnover:.2f} 次/年")
        else:
            add("medium", "asset_quality", "inventory_turnover_low", "存货周转慢，关注积压与跌价风险", f"{latest} 存货周转率 {inventory_turnover:.2f} 次/年")
    if quality.get("cip_to_fixed_assets_pct") is not None and quality["cip_to_fixed_assets_pct"] > 50:
        add("medium", "asset_quality", "cip_high", "在建工程占固定资产比重高，关注转固与产能消化", f"{latest} 在建工程/固定资产 {quality['cip_to_fixed_assets_pct']:.2f}%")

    non_op_share = assets.get("non_operating_assets_share_pct")
    non_op_return = assets.get("non_operating_asset_return_pct")
    if non_op_share is not None and non_op_share > 40 and non_op_return is not None and non_op_return < 5:
        add(
            "info",
            "asset_quality",
            "non_operating_assets_low_return",
            "非经营资产占比高但报酬率低，关注资金使用效率与战略配置",
            f"{latest} 非经营资产占比 {non_op_share:.2f}%，非经营资产报酬率 {non_op_return:.2f}%",
        )

    if capital.get("financial_debt_ratio_pct") is not None and capital["financial_debt_ratio_pct"] > 30:
        add("high" if capital["financial_debt_ratio_pct"] > 50 else "medium", "financial_risk", "financial_debt_high", "有息负债率偏高", f"{latest} 金融性负债/总资产 {capital['financial_debt_ratio_pct']:.2f}%")
    if capital.get("debt_ratio_pct") is not None and capital["debt_ratio_pct"] > 70:
        add("medium", "financial_risk", "debt_ratio_high", "资产负债率超过 70%（需区分经营性负债与金融性负债）", f"{latest} 资产负债率 {capital['debt_ratio_pct']:.2f}%，金融性负债率 {capital.get('financial_debt_ratio_pct') or 0:.2f}%")
    if capital.get("interest_coverage") is not None and capital["interest_coverage"] < 2:
        add("high" if capital["interest_coverage"] < 1 else "medium", "financial_risk", "interest_coverage_low", "利息保障倍数偏低", f"{latest} 利息保障倍数 {capital['interest_coverage']:.2f}")

    if cash.get("capex_to_ocf") is not None and cash["capex_to_ocf"] > 1.5 and (cash.get("net_financing_yi") or 0) > 0:
        add("medium", "investment", "capex_above_ocf", "资本开支超过经营现金流且依赖融资", f"{latest} 资本开支/经营现金流 {cash['capex_to_ocf']:.2f}，净融资 {cash.get('net_financing_yi') or 0:,.2f} 亿元")

    if profit.get("impairment_share_pct") is not None and profit["impairment_share_pct"] > 30:
        add("high" if profit["impairment_share_pct"] > 50 else "medium", "profit_quality", "impairment_large", "减值损失对营业利润冲击较大", f"{latest} 减值/营业利润 {profit['impairment_share_pct']:.2f}%")
    if profit.get("other_income_share_pct") is not None and profit["other_income_share_pct"] > 30:
        add("medium", "profit_quality", "subsidy_dependence", "其他收益（多为政府补贴）占营业利润比重高", f"{latest} 其他收益/营业利润 {profit['other_income_share_pct']:.2f}%")
    if profit.get("misc_income_share_pct") is not None and profit["misc_income_share_pct"] > 30:
        add("medium", "profit_quality", "misc_income_dependence", "杂项收益（利息、投资、公允价值变动）占营业利润比重高", f"{latest} 杂项收益/营业利润 {profit['misc_income_share_pct']:.2f}%")

    residual_share = profit.get("residual_share_pct")
    if residual_share is not None and abs(residual_share) > 10:
        add(
            "medium" if abs(residual_share) > 20 else "info",
            "profit_quality",
            "profit_structure_residual",
            "营业利润残差较大，三支柱两减值未能完整解释营业利润",
            f"{latest} 残差 {profit.get('residual_yi') or 0:,.2f} 亿元，占营业利润 {residual_share:.2f}%；"
            "核查其他业务利润、利息收入列示口径和未分类项目",
        )

    if quality.get("monetary_funds_to_short_term_debt") is not None and quality["monetary_funds_to_short_term_debt"] > 3 and (capital.get("financial_debt_ratio_pct") or 0) > 20:
        add("info", "financial_risk", "cash_and_debt_both_high", "货币资金充裕同时有息负债较高，核查受限资金与融资必要性", f"{latest} 货币资金/短期有息负债 {quality['monetary_funds_to_short_term_debt']:.2f}，有息负债率 {capital['financial_debt_ratio_pct']:.2f}%")

    # Multi-period trends.
    if len(periods) >= 3:
        recent = [metrics[p]["growth"].get("revenue_yoy_pct") for p in periods[-3:]]
        if all(value is not None and value < 0 for value in recent):
            add("medium", "growth", "revenue_decline_3y", "营业收入连续三年下滑", f"{periods[-3]}~{latest} 收入同比均为负")
        recent_core = [metrics[p]["quality"].get("core_margin_pct") for p in periods[-3:]]
        if all(value is not None for value in recent_core) and recent_core[0] > recent_core[1] > recent_core[2]:
            add("medium", "profit_quality", "core_margin_decline_3y", "核心利润率连续三年下降", f"{periods[-3]}~{latest} 核心利润率持续走低")

    return signals


def _parent_analysis(data: dict[str, Any], periods: list[str]) -> dict[str, Any] | None:
    parent = data.get("parent_statements")
    if not parent:
        return None
    detailed: dict[str, Any] = {}
    for period in periods:
        p_block = parent.get(period)
        c_block = (data.get("statements") or {}).get(period)
        if not p_block or not c_block:
            continue
        p_balance = p_block.get("balance") or {}
        c_balance = c_block.get("balance") or {}
        p_income = p_block.get("income") or {}
        c_income = c_block.get("income") or {}

        p_assets = p_balance.get("total_assets") or p_balance.get("total_liab_equity")
        c_assets = c_balance.get("total_assets") or c_balance.get("total_liab_equity")
        expansion_effect = None
        if p_assets is not None and c_assets is not None:
            expansion_effect = c_assets - p_assets

        details = {}
        controlling_investment = 0.0
        for key in CONTROLLING_INVESTMENT_KEYS:
            p_value = p_balance.get(key)
            c_value = c_balance.get(key)
            if p_value is None or c_value is None:
                continue
            diff = p_value - c_value
            if diff > 0:
                details[key] = to_yi(diff)
                controlling_investment += diff

        p_core, _ = _core_profit(p_income)
        c_core, _ = _core_profit(c_income)
        p_net = p_income.get("net_profit")
        c_net = c_income.get("net_profit")
        p_investment_income = p_income.get("investment_income")
        c_investment_income = c_income.get("investment_income")

        p_operating = _operating_assets(p_balance)
        p_non_operating = _non_operating_assets(p_balance)
        p_cash = p_balance.get("monetary_funds")
        parent_operating_ex_cash = None
        parent_investment = None
        if p_operating is not None:
            parent_operating_ex_cash = p_operating - _sum_or_zero(p_cash)
        if p_non_operating is not None:
            parent_investment = p_non_operating - _sum_or_zero(p_cash)

        detailed[period] = {
            "parent_total_assets_yi": to_yi(p_assets),
            "consolidated_total_assets_yi": to_yi(c_assets),
            "expansion_effect_yi": to_yi(expansion_effect),
            "controlling_investment_yi": to_yi(controlling_investment),
            "controlling_investment_detail_yi": details,
            "expansion_multiple": round_or_none(safe_div(expansion_effect, controlling_investment), 2),
            "parent_core_profit_yi": to_yi(p_core),
            "consolidated_core_profit_yi": to_yi(c_core),
            "parent_net_profit_yi": to_yi(p_net),
            "consolidated_net_profit_yi": to_yi(c_net),
            "parent_minus_consolidated_net_profit_yi": None if p_net is None or c_net is None else to_yi(p_net - c_net),
            "parent_investment_income_yi": to_yi(p_investment_income),
            "consolidated_investment_income_yi": to_yi(c_investment_income),
            "parent_minus_consolidated_investment_income_yi": None
            if p_investment_income is None or c_investment_income is None
            else to_yi(p_investment_income - c_investment_income),
            "parent_operating_assets_ex_cash_yi": to_yi(parent_operating_ex_cash),
            "parent_investment_assets_yi": to_yi(parent_investment),
            "parent_monetary_funds_yi": to_yi(p_cash),
            "parent_operating_share_pct": ratio_pct(parent_operating_ex_cash, _sum_or_zero(parent_operating_ex_cash, parent_investment, p_cash)),
            "parent_investment_share_pct": ratio_pct(parent_investment, _sum_or_zero(parent_operating_ex_cash, parent_investment, p_cash)),
        }
    if not detailed:
        return None
    latest_period = max(detailed)
    return {"periods": detailed, "latest": latest_period, "latest_metrics": detailed[latest_period]}


def analyze(data: dict[str, Any]) -> dict[str, Any]:
    meta = data.get("meta") or {}
    statements = data.get("statements") or {}
    periods = sorted(statements)
    if not periods:
        raise ValueError("no statements to analyze")
    is_financial = bool(meta.get("is_financial")) or is_financial_industry(meta.get("industry"), meta.get("company_type"))
    metrics: dict[str, dict[str, Any]] = {}
    for index, period in enumerate(periods):
        previous = statements.get(periods[index - 1]) if index > 0 else None
        metrics[period] = analyze_period(period, statements[period], previous, meta, is_financial)

    latest = periods[-1]
    parent_analysis = _parent_analysis(data, periods)
    signals = _signals(metrics, latest, periods, meta)

    # Add parent-specific signals.
    if parent_analysis:
        parent_latest = parent_analysis["latest_metrics"]
        multiple = parent_latest.get("expansion_multiple")
        controlling = parent_latest.get("controlling_investment_yi") or 0
        if multiple is not None and controlling > 0 and multiple < 1:
            signals.append(
                {
                    "level": "medium",
                    "category": "strategy",
                    "id": "controlling_investment_weak_expansion",
                    "message": "控制性投资撬动的增量资产小于投入资源，扩张效应偏弱",
                    "evidence": f"{latest} 控制性投资 {controlling:,.2f} 亿元，撬动增量资产 {parent_latest.get('expansion_effect_yi') or 0:,.2f} 亿元，倍数 {multiple:.2f}",
                    "period": latest,
                }
            )
        p_net = parent_latest.get("parent_net_profit_yi")
        c_net = parent_latest.get("consolidated_net_profit_yi")
        if p_net is not None and c_net is not None and c_net < 0 < p_net:
            signals.append(
                {
                    "level": "high",
                    "category": "group",
                    "id": "subsidiaries_drag_group",
                    "message": "母公司盈利但合并报表亏损，子公司整体拖累集团",
                    "evidence": f"{latest} 母公司净利润 {p_net:,.2f} 亿元，合并净利润 {c_net:,.2f} 亿元",
                    "period": latest,
                }
            )
        p_inv = parent_latest.get("parent_investment_income_yi")
        c_inv = parent_latest.get("consolidated_investment_income_yi")
        if p_inv is not None and c_inv is not None and p_inv - c_inv > max(1.0, abs(c_inv)):
            signals.append(
                {
                    "level": "info",
                    "category": "group",
                    "id": "parent_income_from_subsidiaries",
                    "message": "母公司投资收益显著高于合并投资收益，可能包含子公司现金分红",
                    "evidence": f"{latest} 母公司投资收益 {p_inv:,.2f} 亿元，合并投资收益 {c_inv:,.2f} 亿元",
                    "period": latest,
                }
            )

    data_gaps: list[str] = []
    caveats: list[str] = []
    if is_financial:
        data_gaps.append("金融行业：核心利润、经营资产/非经营资产分类、存货周转等通用指标不适用；应使用行业专用指标。")
    if not parent_analysis:
        data_gaps.append("缺少母公司报表：无法计算控制性投资占用资源、扩张效应、母公司资产结构与分红能力；需从年报母公司报表补充。")
    if len(periods) >= 1:
        data_gaps.append("第一期的平均余额只能使用期末数，周转率与报酬率略偏高或偏低；至少保留两期数据更可靠。")
    if any(metrics[p]["quality"].get("fixed_asset_turnover_net") is not None for p in periods):
        caveats.append("固定资产周转率按报表净值计算；书中强调应使用固定资产原值（见附注），否则折旧会系统性抬高周转率。")
    caveats.append("核心利润获现率 1.2~1.5 倍是书中的经验参考，受存货周转、行业周期和预收/应付模式影响，不能当作普适阈值。")
    latest_inventory_turnover = metrics[latest]["quality"].get("inventory_turnover")
    if latest_inventory_turnover is not None and latest_inventory_turnover < 2:
        caveats.append(
            f"最新期存货周转率 {latest_inventory_turnover:.2f} 次/年（低于 2 次），"
            "1.2~1.5 倍获现率经验值不直接适用，应按经营周期、预收模式和行业惯例判断。"
        )
    caveats.append("货币资金默认归入非经营资产；若企业现金规模很小，归入经营资产对结构比例影响不大。")
    # Extremely large year-on-year swings in a single line item, while revenue
    # is stable, often indicate a reclassification or an incomplete data feed
    # rather than a real business change.  Flag it instead of trend-reading it.
    for period in periods[1:]:
        period_quality = metrics[period]["quality"]
        revenue_yoy = metrics[period]["growth"].get("revenue_yoy_pct")
        if revenue_yoy is None:
            continue
        for label, key in (
            ("应收", "receivables_growth_pct"),
            ("存货", "inventory_growth_pct"),
        ):
            item_growth = period_quality.get(key)
            if item_growth is not None and abs(item_growth) > 80 and abs(revenue_yoy) < 20:
                caveats.append(
                    f"{period} {label}同比 {item_growth:.1f}%，收入同比 {revenue_yoy:.1f}%；"
                    "年度间异常波动，可能为科目重分类、合并范围变化或数据源问题，需核对年报附注。"
                )
    caveats.append("比率和信号只用于提出问题；需结合附注、分部报告、同业和审计意见再下结论。")

    if not is_financial:
        for period in periods:
            notes = metrics[period]["data_notes"]
            missing = notes.get("core_profit_missing_components") or []
            if missing:
                data_gaps.append(f"{period} 核心利润计算缺少：{', '.join(missing)}（按 0 处理，可能高估核心利润）。")
            if not notes.get("impairment_fields_present"):
                data_gaps.append(f"{period} 未取得资产/信用减值损失字段，利润结构中的减值项可能不完整。")
            if metrics[period]["cashflow"].get("depreciation_amortization_yi") is None:
                data_gaps.append(f"{period} 未取得折旧摊销补充资料，无法计算经营现金流对折旧摊销的覆盖。")

    questions = _questions(metrics, latest, signals, parent_analysis, is_financial)

    return {
        "meta": meta,
        "periods": periods,
        "latest_period": latest,
        "metrics": metrics,
        "latest": metrics[latest],
        "parent_analysis": parent_analysis,
        "signals": signals,
        "data_gaps": _dedupe(data_gaps),
        "caveats": caveats,
        "questions": questions,
        "disclaimer": "本输出是对公开财务数据的结构化计算与问题提示，不构成投资建议；事实、推断和假设需在报告中分开表述。",
    }


def _questions(
    metrics: dict[str, dict[str, Any]],
    latest: str,
    signals: list[dict[str, Any]],
    parent_analysis: dict[str, Any] | None,
    is_financial: bool,
) -> list[str]:
    questions = [
        "应收账款与合同资产的账龄、坏账准备、前五大客户及回款安排如何？",
        "存货的构成、库龄、跌价准备和周转天数如何？是否存在为毛利而积压？",
        "商誉与无形资产的减值测试假设、并购业绩承诺完成情况如何？",
        "其他应收款的对象、账龄和商业实质是什么？是否存在关联方资金占用或违规担保？",
        "货币资金是否存在受限（保证金、冻结、募集资金专户）？存贷双高的原因是什么？",
        "在建工程的进度、预计转固时间和产能消化安排如何？",
        "审计意见类型和关键审计事项是什么？是否涉及收入确认、应收账款、存货或持续经营？",
        "分部报告、关联交易、政府补助和重大诉讼是否改变了上述判断？",
    ]
    ids = {signal["id"] for signal in signals}
    if "receivables_outgrow_revenue" in ids:
        questions.insert(0, "应收增速显著高于收入：核查是否放宽信用政策、提前确认收入或存在关联方赊销。")
    if "goodwill_high" in ids:
        questions.insert(0, "商誉占净资产较高：核查被并购企业实际业绩与业绩承诺、商誉减值测试关键假设。")
    if "other_receivables_high" in ids:
        questions.insert(0, "其他应收款占比高：核查资金流向、是否流向控股股东/关联方、是否计提足额减值。")
    if "cash_conversion_low" in ids or "cash_conversion_weak" in ids:
        questions.insert(0, "利润有、现金少：核查应收、存货、预付款和合同资产的增加，以及收入确认政策。")
    if parent_analysis is None:
        questions.insert(0, "补充母公司资产负债表、利润表和现金流量表，以判断控制性投资扩张效应和分红能力。")
    if is_financial:
        questions.insert(0, "金融企业：补充净息差、不良率、拨备覆盖率、资本充足率等行业指标，通用八看框架仅作辅助。")
    return _dedupe(questions)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# --------------------------------------------------------------- rendering ---
def render_markdown(result: dict[str, Any]) -> str:
    meta = result.get("meta") or {}
    periods = result["periods"]
    latest = result["latest"]
    lines: list[str] = []
    lines.append(f"# {meta.get('name') or meta.get('code') or '企业'} 财务分析摘要")
    lines.append("")
    lines.append(f"- 代码：{meta.get('code', '—')}　行业：{meta.get('industry') or '—'}　数据源：{meta.get('source', '—')}")
    lines.append(f"- 期间：{', '.join(periods)}　最新期：{result['latest_period']}")
    lines.append("")

    def row(label: str, key_path: tuple[str, ...], fmt: str = "money") -> str:
        values = []
        for period in periods:
            node: Any = result["metrics"][period]
            for key in key_path:
                node = (node or {}).get(key) if isinstance(node, dict) else None
            if fmt == "money":
                values.append(fmt_money(node))
            elif fmt == "pct":
                values.append(fmt_pct(node))
            else:
                values.append(fmt_ratio(node))
        return f"| {label} | " + " | ".join(values) + " |"

    lines.append("## 规模与效益")
    lines.append("")
    lines.append("| 指标（亿元） | " + " | ".join(periods) + " |")
    lines.append("|---|" + "|".join(["---:"] * len(periods)) + "|")
    lines.append(row("营业收入", ("revenue_yi",)))
    lines.append(row("毛利", ("gross_profit_yi",)))
    lines.append(row("核心利润", ("profit_structure", "core_profit_yi")))
    lines.append(row("营业利润", ("profit_structure", "operating_profit_yi")))
    lines.append(row("净利润", ("profit_structure", "net_profit_yi")))
    lines.append(row("归母净利润", ("profit_structure", "parent_net_profit_yi")))
    lines.append(row("经营现金流净额", ("cashflow", "operating_cash_flow_yi")))
    lines.append("")
    lines.append("| 比率 | " + " | ".join(periods) + " |")
    lines.append("|---|" + "|".join(["---:"] * len(periods)) + "|")
    lines.append(row("毛利率", ("quality", "gross_margin_pct"), "pct"))
    lines.append(row("核心利润率", ("quality", "core_margin_pct"), "pct"))
    lines.append(row("净利率", ("quality", "net_margin_pct"), "pct"))
    lines.append(row("ROE", ("quality", "roe_pct"), "pct"))
    lines.append(row("核心利润获现率", ("cashflow", "cash_conversion"), "ratio"))
    lines.append("")

    lines.append("## 结构与质量（最新期）")
    lines.append("")
    profit = latest["profit_structure"]
    assets = latest["assets"]
    capital = latest["capital"]
    cash = latest["cashflow"]
    quality = latest["quality"]
    if latest.get("is_financial"):
        lines.append("- 金融行业：核心利润、经营资产/非经营资产分类、存货周转等通用指标不适用；"
                     "请使用净息差、不良率、拨备覆盖率、资本充足率等专用指标。")
    else:
        lines.append(f"- 利润结构：核心利润 {fmt_money(profit.get('core_profit_yi'))} 亿元，"
                     f"其他收益 {fmt_money(profit.get('other_income_yi'))} 亿元，"
                     f"杂项收益 {fmt_money(profit.get('misc_income_yi'))} 亿元，"
                     f"减值损失 {fmt_money(profit.get('impairment_loss_yi'))} 亿元；"
                     f"核心利润占营业利润 {fmt_pct(profit.get('core_profit_share_pct'))}。")
        lines.append(f"- 资产结构：经营资产 {fmt_money(assets.get('operating_assets_yi'))} 亿元"
                     f"（{fmt_pct(assets.get('operating_assets_share_pct'))}），"
                     f"非经营资产 {fmt_money(assets.get('non_operating_assets_yi'))} 亿元"
                     f"（{fmt_pct(assets.get('non_operating_assets_share_pct'))}）；"
                     f"经营资产报酬率 {fmt_pct(assets.get('operating_asset_return_pct'))}。")
        lines.append(f"- 资本来源：商业债务依存度 {fmt_pct(capital.get('commercial_debt_share_pct'))}，"
                     f"金融债务依存度 {fmt_pct(capital.get('financial_debt_share_pct'))}，"
                     f"股东贡献度 {fmt_pct(capital.get('shareholder_contribution_share_pct'))}，"
                     f"利润积累度 {fmt_pct(capital.get('retained_earnings_share_pct'))}。")
        lines.append(f"- 现金流：经营现金流 {fmt_money(cash.get('operating_cash_flow_yi'))} 亿元，"
                     f"获现率 {fmt_ratio(cash.get('cash_conversion'))}，"
                     f"资本开支/经营现金流 {fmt_ratio(cash.get('capex_to_ocf'))}，"
                     f"自由现金流 {fmt_money(cash.get('free_cash_flow_yi'))} 亿元。")
        lines.append(f"- 资产质量：应收/收入 {fmt_pct(quality.get('receivables_to_revenue_pct'))}，"
                     f"存货周转 {fmt_ratio(quality.get('inventory_turnover'))} 次，"
                     f"商誉/净资产 {fmt_pct(quality.get('goodwill_to_equity_pct'))}，"
                     f"其他应收款/总资产 {fmt_pct(quality.get('other_receivables_to_assets_pct'))}。")
    lines.append("")

    if result.get("parent_analysis"):
        parent = result["parent_analysis"]["latest_metrics"]
        lines.append("## 母公司/集团（最新期）")
        lines.append("")
        lines.append(f"- 控制性投资占用资源：{fmt_money(parent.get('controlling_investment_yi'))} 亿元；"
                     f"合并资产较母公司增量：{fmt_money(parent.get('expansion_effect_yi'))} 亿元；"
                     f"扩张倍数：{fmt_ratio(parent.get('expansion_multiple'))}。")
        lines.append(f"- 母公司净利润 {fmt_money(parent.get('parent_net_profit_yi'))} 亿元，"
                     f"合并净利润 {fmt_money(parent.get('consolidated_net_profit_yi'))} 亿元；"
                     f"母公司投资收益 {fmt_money(parent.get('parent_investment_income_yi'))} 亿元，"
                     f"合并投资收益 {fmt_money(parent.get('consolidated_investment_income_yi'))} 亿元。")
        lines.append("")

    lines.append("## 信号与待核实事项")
    lines.append("")
    for signal in result["signals"]:
        lines.append(f"- [{signal['level']}] {signal['message']}：{signal['evidence']}")
    if not result["signals"]:
        lines.append("- 未触发脚本内置信号；仍需结合附注和行业判断。")
    lines.append("")
    for question in result["questions"]:
        lines.append(f"- {question}")
    lines.append("")
    if result["data_gaps"]:
        lines.append("## 数据缺口")
        lines.append("")
        for gap in result["data_gaps"]:
            lines.append(f"- {gap}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description="Analyze normalized financial statements")
    parser.add_argument("--input", required=True, help="JSON from fetch_statements.py or a manual file")
    parser.add_argument("--format", choices=("json", "md"), default="json")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument(
        "--brief",
        action="store_true",
        help="JSON only: omit per-period metrics and keep latest/signals/gaps/questions",
    )
    parser.add_argument("--out", help="optional output path; otherwise print to stdout")
    args = parser.parse_args()

    try:
        data = load_input(args.input)
        result = analyze(data)
        if args.format == "md":
            output = render_markdown(result)
        else:
            payload = result
            if args.brief:
                payload = {
                    "meta": result["meta"],
                    "periods": result["periods"],
                    "latest_period": result["latest_period"],
                    "latest": result["latest"],
                    "parent_analysis": (
                        {
                            "latest": result["parent_analysis"]["latest"],
                            "latest_metrics": result["parent_analysis"]["latest_metrics"],
                        }
                        if result.get("parent_analysis")
                        else None
                    ),
                    "signals": result["signals"],
                    "data_gaps": result["data_gaps"],
                    "caveats": result["caveats"],
                    "questions": result["questions"],
                    "disclaimer": result["disclaimer"],
                }
            output = json.dumps(
                payload,
                ensure_ascii=False,
                indent=2 if args.pretty else None,
                separators=None if args.pretty else (",", ":"),
            )
        if args.out:
            path = Path(args.out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output, encoding="utf-8")
            print(json.dumps({"ok": True, "out": str(path).replace("\\", "/")}, ensure_ascii=False))
        else:
            print(output)
        return 0
    except Exception as error:  # noqa: BLE001 - keep the CLI friendly
        print(json.dumps({"ok": False, "error": f"{type(error).__name__}: {error}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
