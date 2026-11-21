#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared field maps and helpers for enterprise-statement-analysis.

Keep this file next to fetch_statements.py and analyze_statements.py.  It has
no third-party dependencies, so it can also be imported by a small ad-hoc
script when the user supplies statements manually.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------- field maps --
# normalized key -> candidate keys in Eastmoney F10 annual statements.
BALANCE_MAP: dict[str, tuple[str, ...]] = {
    "total_assets": ("TOTAL_ASSETS",),
    "monetary_funds": ("MONETARYFUNDS",),
    "notes_receivable": ("NOTE_RECE",),
    "accounts_receivable": ("ACCOUNTS_RECE",),
    "notes_and_accounts_receivable": ("NOTE_ACCOUNTS_RECE",),
    "receivables_financing": ("RECEIVABLES_FINANCING",),
    "contract_assets": ("CONTRACT_ASSET",),
    "prepayments": ("PREPAYMENT",),
    "other_receivables": ("TOTAL_OTHER_RECE", "OTHER_RECE"),
    "inventory": ("INVENTORY",),
    "current_assets": ("TOTAL_CURRENT_ASSETS",),
    "fixed_assets": ("FIXED_ASSET",),
    "construction_in_progress": ("CIP",),
    "construction_materials": ("PROJECT_MATERIAL",),
    "productive_biological_assets": ("PRODUCTIVE_BIOLOGY_ASSET",),
    "oil_gas_assets": ("OIL_GAS_ASSET",),
    "intangible_assets": ("INTANGIBLE_ASSET",),
    "development_expenditure": ("DEVELOP_EXPENSE",),
    "goodwill": ("GOODWILL",),
    "long_term_equity_investment": ("LONG_EQUITY_INVEST",),
    "other_equity_investments": ("OTHER_EQUITY_INVEST",),
    "other_noncurrent_financial_assets": ("OTHER_NONCURRENT_FINASSET",),
    "investment_property": ("INVEST_REALESTATE",),
    "right_of_use_assets": ("USERIGHT_ASSET",),
    "long_term_prepaid_expenses": ("LONG_PREPAID_EXPENSE",),
    "deferred_tax_assets": ("DEFER_TAX_ASSET",),
    "other_current_assets": ("OTHER_CURRENT_ASSET",),
    "other_noncurrent_assets": ("OTHER_NONCURRENT_ASSET",),
    "noncurrent_assets_due_within_one_year": ("NONCURRENT_ASSET_1YEAR",),
    "long_term_receivables": ("LONG_RECE",),
    "trade_financial_assets": ("TRADE_FINASSET", "FVTPL_FINASSET"),
    "derivative_financial_assets": ("DERIVE_FINASSET",),
    "debt_investments": ("CREDITOR_INVEST", "HOLD_MATURITY_INVEST"),
    "other_debt_investments": ("OTHER_CREDITOR_INVEST",),
    "lend_fund": ("LEND_FUND",),
    "loan_advance": ("LOAN_ADVANCE",),
    "cash_deposit_pbc": ("CASH_DEPOSIT_PBC",),
    "deposit_interbank": ("DEPOSIT_INTERBANK",),
    "buy_resale_finasset": ("BUY_RESALE_FINASSET",),
    "precious_metal": ("PRECIOUS_METAL",),
    "fin_fund": ("FIN_FUND",),
    "interest_receivable": ("INTEREST_RECE",),
    "dividend_receivable": ("DIVIDEND_RECE",),
    "holdsale_asset": ("HOLDSALE_ASSET",),
    "subsidy_receivable": ("SUBSIDY_RECE",),
    "export_refund_receivable": ("EXPORT_REFUND_RECE",),
    "total_liabilities": ("TOTAL_LIABILITIES",),
    "short_term_loans": ("SHORT_LOAN",),
    "trading_financial_liabilities": ("TRADE_FINLIAB", "FVTPL_FINLIAB"),
    "notes_payable": ("NOTE_PAYABLE",),
    "accounts_payable": ("ACCOUNTS_PAYABLE",),
    "notes_and_accounts_payable": ("NOTE_ACCOUNTS_PAYABLE",),
    "contract_liabilities": ("CONTRACT_LIAB",),
    "advance_receipts": ("ADVANCE_RECEIVABLES",),
    "payroll_payable": ("STAFF_SALARY_PAYABLE",),
    "taxes_payable": ("TAX_PAYABLE",),
    "other_payables": ("TOTAL_OTHER_PAYABLE", "OTHER_PAYABLE"),
    "current_liabilities": ("TOTAL_CURRENT_LIAB",),
    "noncurrent_liabilities_due_within_one_year": ("NONCURRENT_LIAB_1YEAR",),
    "long_term_loans": ("LONG_LOAN",),
    "bonds_payable": ("BOND_PAYABLE",),
    "short_bond_payable": ("SHORT_BOND_PAYABLE",),
    "lease_liabilities": ("LEASE_LIAB",),
    "long_term_payables": ("LONG_PAYABLE",),
    "interest_payable": ("INTEREST_PAYABLE",),
    "dividend_payable": ("DIVIDEND_PAYABLE",),
    "accept_deposit_interbank": ("ACCEPT_DEPOSIT_INTERBANK",),
    "other_current_liab": ("OTHER_CURRENT_LIAB",),
    "general_risk_reserve": ("GENERAL_RISK_RESERVE",),
    "total_equity": ("TOTAL_EQUITY",),
    "share_capital": ("SHARE_CAPITAL",),
    "capital_reserve": ("CAPITAL_RESERVE",),
    "surplus_reserve": ("SURPLUS_RESERVE",),
    "undistributed_profit": ("UNASSIGN_RPOFIT",),
    "other_comprehensive_income": ("OTHER_COMPRE_INCOME",),
    "parent_equity": ("TOTAL_PARENT_EQUITY",),
    "minority_equity": ("MINORITY_EQUITY",),
    "total_liab_equity": ("TOTAL_LIAB_EQUITY",),
}

INCOME_MAP: dict[str, tuple[str, ...]] = {
    "total_operating_income": ("TOTAL_OPERATE_INCOME",),
    "operating_income": ("OPERATE_INCOME",),
    "interest_income": ("INTEREST_INCOME",),
    "interest_expense": ("INTEREST_EXPENSE",),
    "operating_cost": ("OPERATE_COST",),
    "taxes_and_surcharges": ("OPERATE_TAX_ADD",),
    "selling_expenses": ("SALE_EXPENSE",),
    "administrative_expenses": ("MANAGE_EXPENSE",),
    "research_expenses": ("RESEARCH_EXPENSE",),
    "financial_expenses": ("FINANCE_EXPENSE",),
    "interest_expense_finance": ("FE_INTEREST_EXPENSE",),
    "interest_income_finance": ("FE_INTEREST_INCOME",),
    "other_income": ("OTHER_INCOME",),
    "investment_income": ("INVEST_INCOME",),
    "fair_value_change_income": ("FAIRVALUE_CHANGE_INCOME",),
    "asset_disposal_income": ("ASSET_DISPOSAL_INCOME",),
    "asset_impairment_loss": ("ASSET_IMPAIRMENT_LOSS",),
    "credit_impairment_loss": ("CREDIT_IMPAIRMENT_LOSS",),
    "asset_impairment_income": ("ASSET_IMPAIRMENT_INCOME",),
    "credit_impairment_income": ("CREDIT_IMPAIRMENT_INCOME",),
    "operating_profit": ("OPERATE_PROFIT",),
    "total_profit": ("TOTAL_PROFIT",),
    "income_tax": ("INCOME_TAX",),
    "net_profit": ("NETPROFIT",),
    "parent_net_profit": ("PARENT_NETPROFIT",),
    "minority_interest": ("MINORITY_INTEREST",),
    "basic_eps": ("BASIC_EPS",),
}

CASHFLOW_MAP: dict[str, tuple[str, ...]] = {
    "sales_cash": ("SALES_SERVICES",),
    "purchases_cash": ("BUY_SERVICES",),
    "staff_cash": ("PAY_STAFF_CASH",),
    "taxes_paid": ("PAY_ALL_TAX",),
    "operating_cash_flow": ("NETCASH_OPERATE",),
    "total_operating_inflow": ("TOTAL_OPERATE_INFLOW",),
    "total_operating_outflow": ("TOTAL_OPERATE_OUTFLOW",),
    "capex": ("CONSTRUCT_LONG_ASSET",),
    "external_investment_cash": ("INVEST_PAY_CASH",),
    "acquire_subsidiary_cash": ("OBTAIN_SUBSIDIARY_OTHER",),
    "investment_income_cash": ("RECEIVE_INVEST_INCOME",),
    "disposal_long_assets_cash": ("DISPOSAL_LONG_ASSET",),
    "investing_cash_flow": ("NETCASH_INVEST",),
    "total_investing_inflow": ("TOTAL_INVEST_INFLOW",),
    "total_investing_outflow": ("TOTAL_INVEST_OUTFLOW",),
    "equity_financing_cash": ("ACCEPT_INVEST_CASH",),
    "subsidiary_equity_financing_cash": ("SUBSIDIARY_ACCEPT_INVEST",),
    "borrowings_cash": ("RECEIVE_LOAN_CASH",),
    "bonds_cash": ("ISSUE_BOND",),
    "debt_repayment_cash": ("PAY_DEBT_CASH",),
    "dividends_interest_paid": ("ASSIGN_DIVIDEND_PORFIT",),
    "financing_cash_flow": ("NETCASH_FINANCE",),
    "total_financing_inflow": ("TOTAL_FINANCE_INFLOW",),
    "total_financing_outflow": ("TOTAL_FINANCE_OUTFLOW",),
    "cash_change": ("CCE_ADD",),
    "fixed_asset_depreciation": ("FA_IR_DEPR", "OILGAS_BIOLOGY_DEPR"),
    "intangible_amortization": ("IA_AMORTIZE",),
    "long_term_prepaid_amortization": ("LPE_AMORTIZE",),
    "right_of_use_asset_amortization": ("USERIGHT_ASSET_AMORTIZE",),
    "inventory_decrease": ("INVENTORY_REDUCE",),
    "operating_receivables_decrease": ("OPERATE_RECE_REDUCE",),
    "operating_payables_increase": ("OPERATE_PAYABLE_ADD",),
}

# Chinese labels commonly copied from an annual report / 巨潮 / 同花顺.
CHINESE_ALIASES: dict[str, str] = {
    "资产总计": "total_assets",
    "资产合计": "total_assets",
    "货币资金": "monetary_funds",
    "应收票据": "notes_receivable",
    "应收账款": "accounts_receivable",
    "应收票据及应收账款": "notes_and_accounts_receivable",
    "应收款项融资": "receivables_financing",
    "合同资产": "contract_assets",
    "预付款项": "prepayments",
    "预付账款": "prepayments",
    "其他应收款": "other_receivables",
    "存货": "inventory",
    "流动资产合计": "current_assets",
    "固定资产": "fixed_assets",
    "固定资产净额": "fixed_assets",
    "在建工程": "construction_in_progress",
    "工程物资": "construction_materials",
    "生产性生物资产": "productive_biological_assets",
    "油气资产": "oil_gas_assets",
    "无形资产": "intangible_assets",
    "开发支出": "development_expenditure",
    "商誉": "goodwill",
    "长期股权投资": "long_term_equity_investment",
    "其他权益工具投资": "other_equity_investments",
    "其他非流动金融资产": "other_noncurrent_financial_assets",
    "投资性房地产": "investment_property",
    "使用权资产": "right_of_use_assets",
    "长期待摊费用": "long_term_prepaid_expenses",
    "递延所得税资产": "deferred_tax_assets",
    "其他流动资产": "other_current_assets",
    "其他非流动资产": "other_noncurrent_assets",
    "一年内到期的非流动资产": "noncurrent_assets_due_within_one_year",
    "长期应收款": "long_term_receivables",
    "交易性金融资产": "trade_financial_assets",
    "衍生金融资产": "derivative_financial_assets",
    "债权投资": "debt_investments",
    "其他债权投资": "other_debt_investments",
    "拆出资金": "lend_fund",
    "发放贷款及垫款": "loan_advance",
    "发放贷款和垫款": "loan_advance",
    "存放中央银行款项": "cash_deposit_pbc",
    "存放同业款项": "deposit_interbank",
    "买入返售金融资产": "buy_resale_finasset",
    "贵金属": "precious_metal",
    "融出资金": "fin_fund",
    "应收利息": "interest_receivable",
    "应收股利": "dividend_receivable",
    "持有待售资产": "holdsale_asset",
    "应收补贴款": "subsidy_receivable",
    "应收出口退税": "export_refund_receivable",
    "负债合计": "total_liabilities",
    "短期借款": "short_term_loans",
    "交易性金融负债": "trading_financial_liabilities",
    "应付票据": "notes_payable",
    "应付账款": "accounts_payable",
    "应付票据及应付账款": "notes_and_accounts_payable",
    "合同负债": "contract_liabilities",
    "预收款项": "advance_receipts",
    "应付职工薪酬": "payroll_payable",
    "应交税费": "taxes_payable",
    "其他应付款": "other_payables",
    "流动负债合计": "current_liabilities",
    "一年内到期的非流动负债": "noncurrent_liabilities_due_within_one_year",
    "长期借款": "long_term_loans",
    "应付债券": "bonds_payable",
    "应付短期债券": "short_bond_payable",
    "租赁负债": "lease_liabilities",
    "长期应付款": "long_term_payables",
    "应付利息": "interest_payable",
    "应付股利": "dividend_payable",
    "同业存放款项": "accept_deposit_interbank",
    "其他流动负债": "other_current_liab",
    "一般风险准备": "general_risk_reserve",
    "所有者权益合计": "total_equity",
    "股东权益合计": "total_equity",
    "实收资本": "share_capital",
    "股本": "share_capital",
    "资本公积": "capital_reserve",
    "盈余公积": "surplus_reserve",
    "未分配利润": "undistributed_profit",
    "其他综合收益": "other_comprehensive_income",
    "归属于母公司所有者权益": "parent_equity",
    "归属于母公司股东权益": "parent_equity",
    "少数股东权益": "minority_equity",
    "负债和所有者权益总计": "total_liab_equity",
    "负债和股东权益总计": "total_liab_equity",
    "营业总收入": "total_operating_income",
    "营业收入": "operating_income",
    "利息收入": "interest_income",
    "利息支出": "interest_expense",
    "营业成本": "operating_cost",
    "税金及附加": "taxes_and_surcharges",
    "销售费用": "selling_expenses",
    "管理费用": "administrative_expenses",
    "研发费用": "research_expenses",
    "财务费用": "financial_expenses",
    "利息费用": "interest_expense_finance",
    "其他收益": "other_income",
    "投资收益": "investment_income",
    "公允价值变动收益": "fair_value_change_income",
    "公允价值变动损益": "fair_value_change_income",
    "资产处置收益": "asset_disposal_income",
    "资产减值损失": "asset_impairment_loss",
    "信用减值损失": "credit_impairment_loss",
    "营业利润": "operating_profit",
    "利润总额": "total_profit",
    "所得税费用": "income_tax",
    "净利润": "net_profit",
    "归属于母公司股东的净利润": "parent_net_profit",
    "归属于母公司所有者的净利润": "parent_net_profit",
    "少数股东损益": "minority_interest",
    "基本每股收益": "basic_eps",
    "销售商品、提供劳务收到的现金": "sales_cash",
    "购买商品、接受劳务支付的现金": "purchases_cash",
    "支付给职工以及为职工支付的现金": "staff_cash",
    "支付的各项税费": "taxes_paid",
    "经营活动产生的现金流量净额": "operating_cash_flow",
    "经营活动现金流量净额": "operating_cash_flow",
    "购建固定资产、无形资产和其他长期资产支付的现金": "capex",
    "投资支付的现金": "external_investment_cash",
    "取得子公司及其他营业单位支付的现金净额": "acquire_subsidiary_cash",
    "取得投资收益收到的现金": "investment_income_cash",
    "处置固定资产、无形资产和其他长期资产收回的现金净额": "disposal_long_assets_cash",
    "投资活动产生的现金流量净额": "investing_cash_flow",
    "吸收投资收到的现金": "equity_financing_cash",
    "子公司吸收少数股东投资收到的现金": "subsidiary_equity_financing_cash",
    "取得借款收到的现金": "borrowings_cash",
    "发行债券收到的现金": "bonds_cash",
    "偿还债务支付的现金": "debt_repayment_cash",
    "分配股利、利润或偿付利息支付的现金": "dividends_interest_paid",
    "筹资活动产生的现金流量净额": "financing_cash_flow",
    "现金及现金等价物净增加额": "cash_change",
    "固定资产折旧、油气资产折耗、生产性生物资产折旧": "fixed_asset_depreciation",
    "无形资产摊销": "intangible_amortization",
    "长期待摊费用摊销": "long_term_prepaid_amortization",
    "使用权资产摊销": "right_of_use_asset_amortization",
    "存货的减少": "inventory_decrease",
    "经营性应收项目的减少": "operating_receivables_decrease",
    "经营性应付项目的增加": "operating_payables_increase",
}

ENGLISH_KEYS: set[str] = set(BALANCE_MAP) | set(INCOME_MAP) | set(CASHFLOW_MAP)

# ------------------------------------------------------------- asset groups --
OPERATING_ASSET_KEYS = (
    "notes_receivable",
    "accounts_receivable",
    "notes_and_accounts_receivable",
    "receivables_financing",
    "contract_assets",
    "prepayments",
    "inventory",
    "fixed_assets",
    "construction_in_progress",
    "construction_materials",
    "productive_biological_assets",
    "oil_gas_assets",
    "intangible_assets",
    "development_expenditure",
    "goodwill",
    "right_of_use_assets",
    "long_term_prepaid_expenses",
    "other_current_assets",
    "other_noncurrent_assets",
    "subsidy_receivable",
    "export_refund_receivable",
)

NON_OPERATING_ASSET_KEYS = (
    "monetary_funds",
    "trade_financial_assets",
    "derivative_financial_assets",
    "debt_investments",
    "other_debt_investments",
    "long_term_equity_investment",
    "other_equity_investments",
    "other_noncurrent_financial_assets",
    "investment_property",
    "other_receivables",
    "long_term_receivables",
    "noncurrent_assets_due_within_one_year",
    "lend_fund",
    "loan_advance",
    "cash_deposit_pbc",
    "deposit_interbank",
    "buy_resale_finasset",
    "precious_metal",
    "fin_fund",
    "interest_receivable",
    "dividend_receivable",
    "holdsale_asset",
)

COMMERCIAL_LIABILITY_KEYS = (
    "notes_and_accounts_payable",
    "notes_payable",
    "accounts_payable",
    "contract_liabilities",
    "advance_receipts",
)

OTHER_OPERATING_LIABILITY_KEYS = (
    "payroll_payable",
    "taxes_payable",
    "other_payables",
    "other_current_liab",
)

FINANCIAL_LIABILITY_KEYS = (
    "short_term_loans",
    "trading_financial_liabilities",
    "noncurrent_liabilities_due_within_one_year",
    "long_term_loans",
    "bonds_payable",
    "short_bond_payable",
    "lease_liabilities",
    "long_term_payables",
    "interest_payable",
    "dividend_payable",
    "accept_deposit_interbank",
)

SHAREHOLDER_KEYS = ("share_capital", "capital_reserve")
RETAINED_KEYS = ("surplus_reserve", "undistributed_profit")

# Keys where a parent-minus-consolidated difference can reveal resources the
# parent supplied to subsidiaries.  See references/eight-views.md (§控制性投资).
CONTROLLING_INVESTMENT_KEYS = (
    "long_term_equity_investment",
    "other_receivables",
    "prepayments",
    "other_current_assets",
    "noncurrent_assets_due_within_one_year",
    "other_noncurrent_assets",
)


# ------------------------------------------------------------------ helpers --
def num(value: Any) -> float | None:
    """Parse a number from a statement value; return None when unavailable."""
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and math.isnan(value):
            return None
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "").replace(" ", "")
    if not text or text in {"-", "--", "—", "None", "nan", "NaN", "null"}:
        return None
    multiplier = 1.0
    if text.endswith("亿"):
        multiplier, text = 1e8, text[:-1]
    elif text.endswith("万"):
        multiplier, text = 1e4, text[:-1]
    elif text.endswith("元"):
        text = text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def pick(row: dict[str, Any], keys: Iterable[str]) -> float | None:
    """Return the first non-empty numeric value among ``keys``."""
    for key in keys:
        if key in row:
            value = num(row.get(key))
            if value is not None:
                return value
    return None


def sum_keys(row: dict[str, Any], keys: Iterable[str]) -> float | None:
    """Sum non-empty values; return None only when every item is missing."""
    values = [num(row.get(key)) for key in keys]
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present)


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


def average(current: float | None, previous: float | None) -> float | None:
    if current is None:
        return None
    if previous is None:
        return current
    return (current + previous) / 2.0


def to_yi(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value / 1e8, 2)


def round_or_none(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def ratio_pct(numerator: float | None, denominator: float | None) -> float | None:
    value = safe_div(numerator, denominator)
    return None if value is None else round(value * 100.0, 2)


# --------------------------------------------------------------- normalize ---
def _normalize_row(row: dict[str, Any], field_map: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for target, candidates in field_map.items():
        result[target] = pick(row, candidates)
    return result


def normalize_balance(row: dict[str, Any]) -> dict[str, Any]:
    result = _normalize_row(row, BALANCE_MAP)
    # Keep the original "total assets" denominator when available.
    if result.get("total_assets") is None:
        result["total_assets"] = result.get("total_liab_equity")
    return result


def normalize_income(row: dict[str, Any]) -> dict[str, Any]:
    result = _normalize_row(row, INCOME_MAP)
    # Convert signed impairment lines to positive loss amounts, if needed.
    for loss_key, income_key in (
        ("asset_impairment_loss", "asset_impairment_income"),
        ("credit_impairment_loss", "credit_impairment_income"),
    ):
        if result.get(loss_key) is None and result.get(income_key) is not None:
            result[loss_key] = -result[income_key]
    return result


def normalize_cashflow(row: dict[str, Any]) -> dict[str, Any]:
    result = _normalize_row(row, CASHFLOW_MAP)
    return result


def normalize_statement(row: dict[str, Any], kind: str) -> dict[str, Any]:
    if kind == "balance":
        return normalize_balance(row)
    if kind == "income":
        return normalize_income(row)
    if kind == "cashflow":
        return normalize_cashflow(row)
    raise ValueError(f"unknown statement kind: {kind}")


def _alias_key(key: str) -> str:
    key = str(key).strip()
    key = key.replace("　", " ")
    key = re.sub(r"\s+", "", key)
    # Annual-report tables often append units or prefixes, e.g.
    # "营业收入(元)", "其中：营业收入", "减：营业成本".
    key = re.sub(r"[（(][^）)]*[)）]", "", key)
    key = re.sub(r"^(其中|加|减)[:：]", "", key)
    return CHINESE_ALIASES.get(key, key)


def normalize_manual_statement(raw: dict[str, Any], kind: str) -> dict[str, Any]:
    """Normalize a user-supplied dict that may use Chinese or English keys."""
    if not isinstance(raw, dict):
        return {}
    renamed: dict[str, Any] = {}
    for key, value in raw.items():
        target = _alias_key(key)
        if target in ENGLISH_KEYS or target in {"report_date", "report_type"}:
            if target not in renamed or renamed[target] is None:
                renamed[target] = value

    result: dict[str, Any] = {key: None for key in ENGLISH_KEYS}
    for key in ENGLISH_KEYS:
        if key in renamed:
            result[key] = num(renamed.get(key))

    # A fetch_statements.py file already uses normalized English keys.  If the
    # input also used Eastmoney field names, map those and fill any gaps.
    for key, value in normalize_statement(raw, kind).items():
        if result.get(key) is None:
            result[key] = value

    for loss_key, income_key in (
        ("asset_impairment_loss", "asset_impairment_income"),
        ("credit_impairment_loss", "credit_impairment_income"),
    ):
        if result.get(loss_key) is None and result.get(income_key) is not None:
            result[loss_key] = -result[income_key]
    return result


def load_input(path: str | Path) -> dict[str, Any]:
    """Load a fetch_statements.py output or a manually assembled JSON file."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")

    # A single period may be supplied as {balance, income, cashflow} directly.
    if "statements" not in payload and any(k in payload for k in ("balance", "income", "cashflow")):
        payload = {
            "meta": payload.get("meta", {}),
            "statements": {str(payload.get("period", payload.get("year", "latest"))): payload},
        }

    statements = payload.get("statements") or {}
    if not isinstance(statements, dict) or not statements:
        raise ValueError("input has no 'statements' object")

    normalized: dict[str, dict[str, Any]] = {}
    for period, block in statements.items():
        if not isinstance(block, dict):
            continue
        normalized[str(period)] = {
            kind: normalize_manual_statement(block.get(kind) or {}, kind)
            for kind in ("balance", "income", "cashflow")
        }
    payload["statements"] = normalized

    parent = payload.get("parent_statements")
    if isinstance(parent, dict) and parent:
        payload["parent_statements"] = {
            str(period): {
                kind: normalize_manual_statement(block.get(kind) or {}, kind)
                for kind in ("balance", "income", "cashflow")
            }
            for period, block in parent.items()
            if isinstance(block, dict)
        }
    else:
        payload["parent_statements"] = None
    return payload


# ------------------------------------------------------------------ display --
def fmt_money(value_yi: float | None, digits: int = 2) -> str:
    if value_yi is None:
        return "—"
    return f"{value_yi:,.{digits}f}"


def fmt_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}%"


def fmt_ratio(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def is_financial_industry(industry: str | None, company_type: int | None = None) -> bool:
    if company_type in (1, 2, 3):
        return True
    text = str(industry or "")
    return any(word in text for word in ("银行", "保险", "证券", "多元金融", "信托", "期货"))
