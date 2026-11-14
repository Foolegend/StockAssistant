import importlib
for lib in ["akshare", "yfinance", "efinance", "tushare", "pandas", "requests"]:
    try:
        m = importlib.import_module(lib)
        print(f"{lib}: OK {getattr(m, '__version__', '')}")
    except Exception as e:
        print(f"{lib}: MISSING ({e})")
