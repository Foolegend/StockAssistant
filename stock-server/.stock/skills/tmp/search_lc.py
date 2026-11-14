# -*- coding: utf-8 -*-
"""Search for A-share stocks whose name contains 浪潮 / 浪潮."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import akshare as ak

try:
    df = ak.stock_info_a_code_name()
    m = df[df["name"].str.contains("浪潮", na=False)]
    print("=== 名称含'浪潮'的A股 ===")
    print(m.to_string())
except Exception as e:
    print("stock_info_a_code_name failed:", type(e).__name__, e)

try:
    spot = ak.stock_zh_a_spot_em()
    m2 = spot[spot["名称"].str.contains("浪潮", na=False)]
    print("\n=== 实时行情 spot_em 中含'浪潮' ===")
    cols = [c for c in ["代码","名称","最新价","涨跌幅","今开","最高","最低","昨收","成交量","成交额","换手率","市盈率-动态","市净率","总市值","流通市值","量比","振幅"] if c in m2.columns]
    print(m2[cols].to_string())
except Exception as e:
    print("stock_zh_a_spot_em failed:", type(e).__name__, e)
