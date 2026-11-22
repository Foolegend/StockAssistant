---
name: /stock-report
description: 生成单只股票的快速研究报告，汇总行情、技术面与结论。
argument-hint: <股票代码或公司名称>
arguments: [symbol]
when_to_use: 当用户希望一次获得某只股票的行情、技术面和结论摘要时。
allowed-tools: Read Write Bash WebSearch
agent: investment-analyst
skills: [stock-analysis]
mcp_server_ids: [quant-tools]
user-invocable: true
enabled: true
---

针对 $ARGUMENTS 生成一份结构化研究简报，依次包含：

1. 核心结论（一句话 + 关键理由）
2. 行情与技术面（趋势、量能、关键指标）
3. 风险与反证
4. 需要继续核实的事项

如果用户没有提供股票代码或公司名称，先询问目标标的，不要自行猜测。
