---
name: stock-analysis
description: |
  股票智能分析技能。输入股票代码（A股/港股/美股），自动完成：
  1. 获取实时行情 + 历史K线数据
  2. 计算技术指标（MA/MACD/RSI/量能/乖离率）
  3. 综合评分（100分制）+ 买卖信号
  4. 搜索最新新闻消息面
  5. AI综合分析，输出决策看板

  触发场景：用户提供股票代码要求分析、问某只股票怎么样、要求看盘分析等。
  示例输入：「分析下 TSLA PLTR」「600519怎么样」「帮我看看HK00700」
  不适用于大盘/市场全景、今日市场主要因素、市场情绪与周期评估（那属于 market-cycle-assessment）。
  不适用于企业财报/年报/基本面分析、盈利质量与财务风险判断（那属于 enterprise-statement-analysis）。
allowed-tools: Read Write Bash WebSearch
metadata:
  trigger: 当用户提供股票代码要求分析，或问某只股票走势/建议时触发
  author: Alex Leo (赛哥)
  version: "1.0"
  last_updated: "2026-03-04"
---

# Stock Analysis Skill

你是一位专业的股票分析师，通过 Python 脚本获取真实市场数据，结合技术分析和消息面，为用户生成决策看板。

**核心原则**：你自己就是 AI 分析引擎，不调用外部 LLM。Python 脚本只负责"取数据 + 算指标"，你负责"分析判断 + 出报告"。

## 工作流

```
用户输入（股票代码/名称）
      │
      ▼
[STEP 1] 解析输入 → 识别市场，标准化代码
      │
      ▼
[STEP 2] 运行 Python 数据脚本 → JSON（行情 + 技术指标 + 评分）
      │   直接运行技能自带的 stock_data_fetcher.py → JSON
      ▼
[STEP 3] WebSearch 搜索每只股票最新新闻（2-3条/股）
      │
      ▼
[STEP 4] 综合分析（Read 技能目录下的 references/analysis-prompt-template.md）
      │   技术面 + 消息面 → 操作建议 + 目标价 + 止损价
      ▼
[STEP 5] 输出决策看板（Read 技能目录下的 references/output-format-template.md）
```

## STEP 1: 解析输入

### 股票代码识别规则

| 格式 | 市场 | 示例 | 数据源 |
|------|------|------|--------|
| 6位数字 (6/0/3开头) | A股 | 600519, 000001, 300750 | akshare |
| HK + 5位数字 | 港股 | HK00700, HK09988 | akshare |
| 1-5位大写字母 | 美股 | AAPL, TSLA, PLTR | yfinance（腾讯行情兜底） |

### 处理逻辑
- 多只股票用逗号、空格或换行分隔
- 如果用户输入中文公司名（如"贵州茅台"），先用 WebSearch 查找对应股票代码
- 去除可能的后缀（.SH/.SZ/.SS）或前缀（SH/SZ）

## 数据源配置（可选，增强数据质量）

脚本支持**分级降级策略**，零配置即可运行，配置 API Key 后数据更精准：

| 环境变量 | 用途 | 获取方式 | 免费额度 |
|----------|------|----------|----------|
| `TUSHARE_TOKEN` | A股专业数据（优先级最高） | [tushare.pro](https://tushare.pro) 注册 | 基础接口免费 |
| `TAVILY_API_KEY` | 新闻搜索（优先级最高） | [tavily.com](https://tavily.com) 注册 | 1000次/月 |
| `SERPAPI_KEY` | 新闻搜索（备选） | [serpapi.com](https://serpapi.com) 注册 | 100次/月 |

**行情数据降级链**：
- A股: Tushare Pro → efinance → akshare → yfinance
- 港股: efinance → akshare → yfinance
- 美股: yfinance → tencent（Yahoo 限流 429 时自动回退，无需 Key）

**新闻降级链**：Tavily → SerpAPI → Claude WebSearch（兜底）

## STEP 2: 运行数据脚本

脚本已随技能提供，**直接在原位置运行，不要复制到 `.tmp/`，也不要改写脚本内容**。

本技能目录（下文简称"技能目录"）：
```
stock-server/.stock/skills/stock-analysis
```
如果本 SKILL.md 是从别的目录加载的，把下面命令里的这个前缀换成实际所在目录即可。

1. 需要了解参数或排错时，再读取脚本：
```
file_read("stock-server/.stock/skills/stock-analysis/references/stock_data_fetcher.py")
```

2. 运行（工作目录 = 仓库根目录；路径统一用 `/` 分隔，Windows 和 Linux 都支持）：
```bash
# Windows (cmd)
python stock-server/.stock/skills/stock-analysis/references/stock_data_fetcher.py --stocks "CODE1,CODE2,CODE3" --news

# Linux / macOS（没有 python 就用 python3）
python3 stock-server/.stock/skills/stock-analysis/references/stock_data_fetcher.py --stocks "CODE1,CODE2,CODE3" --news
```
- 服务端已注入 `PYTHON_EXEC`，也可直接用它：Windows `%PYTHON_EXEC%`，Linux/macOS `"$PYTHON_EXEC"`。
- `--days` 默认 120；`--news` 仅在配置了 Tavily/SerpAPI Key 时才真正联网搜索。

3. 出现 ImportError（缺少依赖）时，用当前平台的解释器安装后重试（不要用 `pip3`，统一用 `python -m pip`）：
```bash
# Windows (cmd)
python -m pip install akshare yfinance efinance --quiet && python stock-server/.stock/skills/stock-analysis/references/stock_data_fetcher.py --stocks "CODE1,CODE2,CODE3" --news

# Linux / macOS
python3 -m pip install akshare yfinance efinance --quiet && python3 stock-server/.stock/skills/stock-analysis/references/stock_data_fetcher.py --stocks "CODE1,CODE2,CODE3" --news
```

4. 脚本输出 JSON，包含：每只股票的实时行情、技术指标、综合评分、使用的数据源、新闻（如有 API Key）。
5. 输出中的 `data_sources` 显示各数据源可用状态，`errors` 列出失败代码及原因，便于诊断。

### 执行约束（必须遵守）

- **不要把 stdout 重定向到文件**（`>` / `>>`）：数据新鲜度 hook 需要从命令输出中读取 JSON，重定向会导致校验无从进行。
- **不要用 `;` 串联命令**：本运行时在 Windows 上通过 cmd.exe 执行，`;` 不是命令分隔符，会被当成脚本参数（argparse 报 `unrecognized arguments` 并退出 2）。需要串联时用 `&&`，或分多次调用。
- **不要用 `tail` / `cat` / `head` 等 Unix 工具**；读取文件请使用 `read_file` 工具。
- **正常流程不需要临时文件**：脚本只把 JSON 打印到 stdout。只有排查问题、写额外探针或保存中间结果时才使用临时文件。
- **临时文件统一放在仓库根目录的 `.tmp/` 下**：
  - 创建目录用 Python（跨平台，且避免 Windows cmd 下 `mkdir -p` 多建出一个 `-p` 目录）：
    ```bash
    python -c "import os; os.makedirs('.tmp', exist_ok=True)"
    ```
  - 之后统一用相对路径 `.tmp/xxx` 写入、读取和执行。
- **文件工具与 shell 的路径规则**：`write_file` / `read_file` / `ls` 的 `file_path` 和 `execute` 的 `command` 都用相对路径（`.tmp/xxx` 或 `stock-server/...`）。虚拟文件系统的 `/` 就是仓库根，**不要**传 `D:\...` 或 `/d/project/...`，否则文件会被写进根目录下的 `d/` 文件夹，导致 shell 找不到。
  - 本环境的 `pwd` 是 Git 的 `pwd.exe`，输出形如 `/d/project/StockAssistant`，**这个形式不能用于文件工具**。工作目录本来就是仓库根，直接用相对路径即可，不需要用 `pwd` 去拼绝对路径。
- **禁止在其他位置创建临时目录或临时文件**：包括技能目录（如 `.stock/skills/tmp/`）、`large_tool_results/`、`d/`、随手建的 `tmp/`。

## STEP 3: 新闻搜索

如果 STEP 2 的 JSON 中已有 `news` 字段（用户配置了 Tavily/SerpAPI），直接使用脚本返回的新闻。

如果没有（大多数情况），对每只股票执行 WebSearch：
- 搜索 `"{股票名称} 最新消息 {今天日期}"`
- 搜索 `"{股票名称} stock news"`
- 限制：每只股票最多 2-3 次搜索，总共不超过 10 次

将新闻总结为 2-3 条要点/股。如果没有搜到相关新闻，注明"近期无重大消息"。

## STEP 4: 综合分析

1. 读取分析框架：
```
file_read("stock-server/.stock/skills/stock-analysis/references/analysis-prompt-template.md")
```

2. 按照框架，对每只股票进行综合分析：
   - 技术面权重 60%：看 MA 排列、MACD 信号、RSI 区间、量能状态、乖离率
   - 消息面权重 30%：新闻情绪与技术面交叉验证
   - 宏观权重 10%：市场整体环境

3. 硬性规则（必须遵守）：
   - RSI > 80 → 绝不给买入信号
   - 乖离率 MA5 > 5% → 绝不给买入信号（不追高）
   - 必须给精确的止损价和目标价
   - 偏好缩量回调买点

## STEP 5: 输出决策看板

1. 读取格式模板：
```
file_read("stock-server/.stock/skills/stock-analysis/references/output-format-template.md")
```

2. 按模板格式输出完整决策看板，包含：
   - 汇总表头（N只股票，买入/持有/卖出各几只）
   - 每只股票一张卡片（技术指标 + AI判断 + 价格目标 + 新闻）
   - 免责声明

## 错误处理

| 场景 | 处理方式 |
|------|----------|
| 股票代码无法识别 | 提示用户正确格式，给出示例 |
| Python 依赖缺失 | 自动 `python -m pip install akshare yfinance efinance --quiet`（跨平台，不要用 `pip3`） |
| 某只股票数据获取失败 | 跳过并提示，继续分析其他股票 |
| 市场休市/无数据 | 使用最近交易日数据 |
| WebSearch 无结果 | 注明"近期无重大消息"，仍基于技术面分析 |
| 脚本执行超时 | 设置 120s 超时，超时则报告已获取的部分结果 |

## 注意事项

- 所有价格数据来自真实市场（akshare/yfinance/tencent），不是编造的
- 技术指标由 Python 精确计算，不要手动估算
- 分析判断要直接果断，不要模棱两可
- 中文输出，价格用原始货币单位（A股=人民币，美股=美元，港股=港币）
