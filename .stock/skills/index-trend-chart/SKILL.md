---
name: index-trend-chart
description: |
  指数走势可视化技能。输入指数（上证指数 / 深证成指 / 沪深300 / 中证500 / 创业板指 / 科创50 / 上证50），
  自动完成：
  1. 拉取该指数的月度行情（开/高/低/收 + 月涨跌幅），数据源自动降级
  2. 生成面积走势图 PNG（横轴每两年一个年份标签，可指定关键时点做红色虚线标注）
  3. 生成含 Excel 原生 AreaChart 的月度数据表（改源数据曲线联动更新）

  触发场景：用户要求「画上证指数走势」「深证成指也画一张」「把指数图标注某个时间点」
  「按月统计指数并绘图」「生成指数历史走势图」等。
  示例输入：「上证指数和深证成指各画一张月线走势，标注 1993/1998/2003/2008/2013/2018/2023 的 3 月」
  不适用于个股走势分析（那属于 stock-analysis）、市场周期评估（market-cycle-assessment）。
allowed-tools: Read Write Bash
metadata:
  trigger: 当用户要求绘制/生成指数走势图、指数月度统计图、指数图上标注时间点时触发
  author: StockAssistant
  version: "1.0"
  last_updated: "2026-11-27"
---

# Index Trend Chart Skill

生成指数（大盘）长期走势面积图 + 月度数据 Excel。**脚本负责取数与画图，你负责解读与汇报。**

## 工作流

```
用户输入（指数名 / 标注时间点 / 起止区间）
      │
      ▼
[STEP 1] 解析输入 → 指数代码、标注点、起止区间
      │
      ▼
[STEP 2] fetch_index_data.py → 月度 CSV + 摘要 JSON
      │   东方财富 →（限流时）新浪 → 腾讯
      ▼
[STEP 3] build_index_chart.py → PNG 面积图 + Excel（原生 AreaChart）
      │   标注点：红色虚线 + 圆点 + 当月收盘标签
      ▼
[STEP 4] 验收：点数一致、首末值/最高点与数据一致、标注点全部落在曲线上
      │
      ▼
[STEP 5] 按 references/output-template.md 输出解读
```

## STEP 1：解析输入

| 用户说法 | 指数 key | 名称 | 代码 |
|---------|---------|------|------|
| 上证指数 / 上证综指 / 沪指 | `sh000001` | 上证指数 | 000001.SH |
| 深证成指 / 深成指 / 深证综指 | `sz399001` | 深证成指 | 399001.SZ |
| 沪深300 | `sh000300` | 沪深300 | 000300.SH |
| 中证500 | `sh000905` | 中证500 | 000905.SH |
| 上证50 | `sh000016` | 上证50 | 000016.SH |
| 创业板指 | `sz399006` | 创业板指 | 399006.SZ |
| 科创50 | `sh000688` | 科创50 | 000688.SH |

- **标注点**：用户给的"1993-03、1998-03"这类时间点，转成 `--markers 1993-03,1998-03`。
  也接受 `1993-3`、`1993/03`。
- **区间**：用户没指定就用全部历史（上证 1990-12 至今，深证 1991-04 至今）。
- **对数坐标**：用户说"早期看不清/低点段"时加 `--log`。
- **多指数**：一次做多个指数时，逐个跑一遍（脚本本身也支持逗号分隔批量取数）。

## STEP 2：取数

```bash
python .stock/skills/index-trend-chart/scripts/fetch_index_data.py \
  --index 上证指数 --start 1990-01 --out-dir out/index-charts/上证指数
```

输出 `sh000001_monthly.csv`（列 `date,open,high,low,close,pct_change,source`）和摘要 JSON。

**数据源降级链**：东方财富（直接月度，最快）→ 新浪（日线重采样）→ 腾讯（日线重采样）。
东方财富限流时脚本会自动切源并在 stderr 提示；摘要 JSON 里的 `source` 字段是**实际使用的数据源**，
汇报时必须按它写，不要一律写"东方财富"。

> 缓存：月度 CSV 缓存在 `<out-dir>/_cache/`，重复出图不会反复请求行情服务器；
> 需要最新数据时加 `--refresh`，或删掉缓存目录。

## STEP 3：出图 + 出 Excel

```bash
python .stock/skills/index-trend-chart/scripts/build_index_chart.py \
  --csv out/index-charts/上证指数/sh000001_monthly.csv \
  --name 上证指数 --out-dir out/index-charts/上证指数 \
  --markers 1993-03,1998-03,2003-03,2008-03,2013-03,2018-03,2023-03
```

产物：

| 文件 | 内容 |
|------|------|
| `<name>_走势图.png` | 面积走势图。横轴每 2 年一个年份标签，纵轴收盘点位，浅网格；标注点 = 红色虚线 + 圆点 + 当月收盘值标签（白底红框） |
| `<name>_月度数据.xlsx` | 三个工作表：`月度数据`（年月/年份/月份/开/高/低/收盘/月涨跌幅公式/来源）、`图表`（Excel 原生 AreaChart，引用月度数据表，改数值曲线联动）、`关键时点`（标注时间点及取值） |

参数：`--log`（对数纵轴）、`--title`（自定义标题）。区间过滤在取数阶段完成
（`fetch_index_data.py --start/--end`），出图脚本直接用取好的月度 CSV。

> **Python 环境**：终端会把仓库 `.venv` 放在 PATH 最前面，直接用 `python` 即可。
> 若当前 `python` 缺 matplotlib 等依赖，改用自带启动器（自动挑选依赖齐全的解释器）：
> `bash .stock/skills/index-trend-chart/scripts/run.sh fetch_index_data.py --index 上证指数`
> 缺依赖时它会打印安装命令：`python -m pip install akshare matplotlib openpyxl`。

## STEP 4：验收（必做）

出图后必须核对，不符就修：

1. **点数一致**：PNG/xlsx 的数据点数 == 摘要 JSON 的 `points`。
2. **首末与极值**：摘要里的 `first_close`/`last_close`/`max_close` 与图中曲线一致
   （典型：上证 2007-10 收盘 5,954.77 为历史最高月；深证成指 2007-10 约 19,531）。
3. **标注点**：摘要 JSON 里 `markers[].close` 每个都不为 null。若某点 `close` 为 null，
   说明它落在数据区间之外 —— **必须在回复里如实说明**，并说明该点只画了时间刻度还是干脆没画。
4. **图面**：Read 一下 PNG，确认中文正常显示（不是方块）、标签没有互相压住、曲线完整。

## STEP 5：汇报

按 `references/output-template.md` 的结构输出：数据范围与来源 → 交付文件 → 关键时点取值表 → 图上能看出的走势特征 → 不足说明（如某标注点超出数据范围）。数值必须来自脚本输出，不要凭印象写。

## 常见坑

| 现象 | 原因 / 处理 |
|------|------------|
| `Push2RateLimitError` | 东方财富限流；脚本自动降级到新浪/腾讯，等待几分钟再试或直接用降级源 |
| 中文显示为方块 | 缺中文字体；脚本会自动挑 `Noto Sans CJK SC` 等，找不到时摘要里的 `cjk_font` 会提示 |
| Excel 里月涨跌幅列是公式，打开才显示数值 | 未安装 LibreOffice 时无法预写缓存值；Excel/WPS 打开时自动重算，属正常 |
| 标注点不在数据区间 | 脚本会在 `markers[].note` 写明原因，如实转述，不要编造数值 |
| 1990-12（上证首月）/1991-04（深证首月） | 指数发布当月只有部分交易日，是正常起点 |
