#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由指数月度 CSV 生成「面积走势图 PNG」+「含原生 AreaChart 的 Excel」。

用法：
    python build_index_chart.py --csv data/sh000001_monthly.csv \
        --name 上证指数 --out-dir out [--markers 1993-03,1998-03,...] [--log]

产出：
    <out-dir>/<name>_走势图.png
    <out-dir>/<name>_月度数据.xlsx
    stdout：产物验收摘要 JSON（点数、首末、最高、每个标注点取值）
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from openpyxl import Workbook  # noqa: E402
from openpyxl.chart import AreaChart, Reference  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402

CJK_CANDIDATES = [
    "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Zen Hei",
    "Microsoft YaHei", "SimHei", "Droid Sans Fallback",
]


def setup_cjk_font() -> str | None:
    """挑一个可用的中文字体，返回字体名；找不到返回 None。"""
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in CJK_CANDIDATES:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name]
            plt.rcParams["font.family"] = "sans-serif"
            plt.rcParams["axes.unicode_minus"] = False
            return name
    plt.rcParams["axes.unicode_minus"] = False
    return None


def parse_markers(raw: str | None) -> list[str]:
    """'1993-03, 1998-03' -> ['1993-03', '1998-03']，同时兼容 1993-3。"""
    if not raw:
        return []
    out = []
    for token in raw.replace("，", ",").split(","):
        token = token.strip()
        if not token:
            continue
        y, _, m = token.partition("-")
        out.append(f"{int(y):04d}-{int(m):02d}")
    return out


def marker_value(monthly: pd.DataFrame, ym: str) -> tuple[float | None, str]:
    """返回 (收盘值, 说明)。值不在数据范围内时返回 (None, 说明)。"""
    hit = monthly[monthly["date"] == ym]
    if hit.empty:
        first, last = monthly["date"].iloc[0], monthly["date"].iloc[-1]
        if ym < first:
            return None, f"早于数据起点 {first}"
        if ym > last:
            return None, f"晚于数据终点 {last}"
        return None, "该月无数据"
    return round(float(hit["close"].iloc[0]), 2), ""


# ---------------------------------------------------------------- PNG

def render_png(monthly: pd.DataFrame, name: str, out_path: Path,
               markers: list[str], log_scale: bool,
               title: str | None) -> dict:
    x = pd.to_datetime(monthly["date"] + "-01")
    y = monthly["close"].astype(float).to_numpy()

    fig, ax = plt.subplots(figsize=(14, 8), dpi=150)
    ax.fill_between(x, y, alpha=0.28, color="#c0392b", label="_nolegend_")
    ax.plot(x, y, color="#c0392b", linewidth=1.4, label=f"{name} 月度收盘点位")

    marker_info = []
    for i, ym in enumerate(markers):
        value, note = marker_value(monthly, ym)
        if value is None:
            marker_info.append({"ym": ym, "close": None, "note": note})
            continue
        stamp = pd.Timestamp(ym + "-01")
        ax.axvline(stamp, color="#d62728", linestyle="--", linewidth=1.0, alpha=0.75,
                   zorder=2)
        ax.scatter([stamp], [value], s=42, color="#d62728", zorder=4,
                   edgecolors="white", linewidths=0.8)
        offset = 8 if i % 2 == 0 else -12
        va = "bottom" if i % 2 == 0 else "top"
        ax.annotate(f"{ym}\n{value:,.0f}", xy=(stamp, value),
                    xytext=(7, offset), textcoords="offset points",
                    fontsize=9, color="#8b0000", va=va, ha="left", zorder=5,
                    bbox=dict(boxstyle="round,pad=0.25", fc="white",
                              ec="#d62728", alpha=0.85, lw=0.7))
        marker_info.append({"ym": ym, "close": value, "note": note})

    ax.set_title(title or f"{name} 月度收盘点位走势（{monthly['date'].iloc[0]} ~ "
                           f"{monthly['date'].iloc[-1]}，共 {len(monthly)} 个月）",
                 fontsize=15, pad=14)
    ax.set_xlabel("")
    ax.set_ylabel("收盘点位（点）")
    ax.grid(True, which="major", axis="both", color="#dcdcdc", linewidth=0.7)
    ax.set_axisbelow(True)

    locator = mdates.YearLocator(2)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.set_xlim(x.iloc[0], x.iloc[-1])
    if log_scale:
        ax.set_yscale("log")
    else:
        ax.set_ylim(bottom=max(0, y.min() * 0.85), top=y.max() * 1.12)

    leg = ax.legend(loc="upper left", frameon=True, framealpha=0.9, fontsize=10)
    leg.set_zorder(6)

    for label in ax.get_xticklabels():
        label.set_rotation(0)
        label.set_ha("center")

    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    return {"markers": marker_info}


# ---------------------------------------------------------------- Excel

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(name="Arial", size=11, bold=True, color="FFFFFF")
TITLE_FONT = Font(name="Arial", size=14, bold=True, color="1F4E79")
ZEBRA_FILL = PatternFill("solid", fgColor="F2F7FB")
THIN = Side(style="thin", color="B7C9D6")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def build_excel(monthly: pd.DataFrame, name: str, out_path: Path,
                markers: list[str], marker_info: list[dict],
                source: str) -> None:
    wb = Workbook()

    # --- 工作表 1：月度数据
    ws = wb.active
    ws.title = "月度数据"
    n = len(monthly)
    last_row = 3 + n

    ws["A1"] = f"{name} 月度数据（{monthly['date'].iloc[0]} ~ {monthly['date'].iloc[-1]}）"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = (f"数据来源：{source or '未知'}｜共 {n} 个月度数据点｜"
                "月涨跌幅为公式列（=（本月收盘-上月收盘）/上月收盘×100），打开表格自动重算"
                "；图表为 Excel 原生 AreaChart，引用本表数据，改数值后曲线联动更新。")
    ws["A2"].font = Font(name="Arial", size=9, italic=True, color="666666")

    headers = ["年月", "年份", "月份", "月开盘", "月最高", "月最低", "月末收盘",
               "月涨跌幅(%)", "数据来源"]
    for col, head in enumerate(headers, start=1):
        cell = ws.cell(row=3, column=col, value=head)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = BORDER

    for i, row in enumerate(monthly.itertuples(index=False), start=4):
        ym = str(row.date)
        year, _, month = ym.partition("-")
        values = [ym, int(year), int(month), float(row.open), float(row.high),
                  float(row.low), float(row.close)]
        for col, value in enumerate(values, start=1):
            cell = ws.cell(row=i, column=col, value=value)
            cell.border = BORDER
            cell.font = Font(name="Arial", size=10)
            if col >= 4:
                cell.number_format = "#,##0.00"
            if i % 2 == 0:
                cell.fill = ZEBRA_FILL
        # 月涨跌幅：首行留空，其余用公式，引用上月收盘
        pct = ws.cell(row=i, column=8)
        if i == 4:
            pct.value = None
        else:
            pct.value = f"=IFERROR((G{i}-G{i-1})/G{i-1}*100,\"\")"
        pct.number_format = "0.00"
        pct.border = BORDER
        pct.font = Font(name="Arial", size=10)
        if i % 2 == 0:
            pct.fill = ZEBRA_FILL
        src = ws.cell(row=i, column=9, value=row.source)
        src.border = BORDER
        src.font = Font(name="Arial", size=9, color="777777")
        if i % 2 == 0:
            src.fill = ZEBRA_FILL

    widths = [11, 8, 8, 12, 12, 12, 13, 13, 16]
    for col, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A4"

    # --- 工作表 2：图表（原生 AreaChart）
    cs = wb.create_sheet("图表")
    cs["A1"] = f"{name} 月度收盘点位面积图（Excel 原生图表，数据源：月度数据!G4:G{last_row}）"
    cs["A1"].font = TITLE_FONT
    chart = AreaChart()
    chart.title = f"{name} 月度收盘点位走势"
    chart.style = 2
    chart.y_axis.title = "收盘点位（点）"
    chart.x_axis.title = "年月"
    chart.height = 11
    chart.width = 26
    data = Reference(ws, min_col=7, min_row=3, max_row=last_row)
    cats = Reference(ws, min_col=1, min_row=4, max_row=last_row)
    chart.add_data(data, titles_from_data=True)
    chart.set_categories(cats)
    chart.x_axis.tickLblSkip = 24
    chart.x_axis.tickMarkSkip = 24
    cs.add_chart(chart, "A3")

    # --- 工作表 3：关键时点
    ms = wb.create_sheet("关键时点")
    ms["A1"] = "关键时点标注"
    ms["A1"].font = TITLE_FONT
    ms["A2"] = "时间点、对应收盘值（与月度数据表一致）；超出数据范围的时间点会注明。"
    ms["A2"].font = Font(name="Arial", size=9, italic=True, color="666666")
    for col, head in enumerate(["时间点", "当月收盘", "说明"], start=1):
        cell = ms.cell(row=3, column=col, value=head)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center")
        cell.border = BORDER
    for i, info in enumerate(marker_info, start=4):
        ms.cell(row=i, column=1, value=info["ym"]).border = BORDER
        c = ms.cell(row=i, column=2, value=info["close"] if info["close"] is not None else "—")
        c.number_format = "#,##0.00"
        c.border = BORDER
        note = ms.cell(row=i, column=3, value=info["note"] or "落在数据范围内")
        note.border = BORDER
        if i % 2 == 0:
            for col in (1, 2, 3):
                ms.cell(row=i, column=col).fill = ZEBRA_FILL
    for col, width in enumerate([14, 14, 34], start=1):
        ms.column_dimensions[get_column_letter(col)].width = width

    wb.save(out_path)


def maybe_recalc(path: Path) -> str | None:
    """有 LibreOffice 时重算公式；没有则返回提示。"""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return "未检测到 LibreOffice，公式列将在 Excel/WPS 打开时自动重算"
    tmp = path.parent / "_recalc"
    tmp.mkdir(exist_ok=True)
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "xlsx", "--outdir", str(tmp),
             str(path)],
            check=True, capture_output=True, timeout=180,
        )
        produced = tmp / path.name
        if produced.exists():
            shutil.move(str(produced), str(path))
    except Exception as exc:  # noqa: BLE001
        return f"LibreOffice 重算失败（不影响交付）: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return None


# ---------------------------------------------------------------- 主流程

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成指数走势面积图与 Excel")
    ap.add_argument("--csv", required=True, help="fetch_index_data.py 产出的月度 CSV")
    ap.add_argument("--name", required=True, help="指数显示名，如 上证指数")
    ap.add_argument("--out-dir", default="out", help="产物输出目录")
    ap.add_argument("--markers", default="", help="标注时间点，逗号分隔，如 1993-03,1998-03")
    ap.add_argument("--log", action="store_true", help="纵轴用对数刻度")
    ap.add_argument("--title", default=None, help="自定义图表标题")
    args = ap.parse_args(argv)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        raise SystemExit(f"找不到数据文件: {csv_path}")
    monthly = pd.read_csv(csv_path, dtype={"date": str})
    monthly = monthly.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    monthly["close"] = pd.to_numeric(monthly["close"], errors="coerce")
    monthly = monthly.dropna(subset=["close"]).reset_index(drop=True)
    if monthly.empty:
        raise SystemExit(f"数据文件没有可用数据: {csv_path}")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    font = setup_cjk_font()

    markers = parse_markers(args.markers)
    png_path = out_dir / f"{args.name}_走势图.png"
    xlsx_path = out_dir / f"{args.name}_月度数据.xlsx"

    info = render_png(monthly, args.name, png_path, markers, args.log, args.title)
    source = str(monthly["source"].iloc[0]) if "source" in monthly.columns else ""
    build_excel(monthly, args.name, xlsx_path, markers, info["markers"], source)
    recalc_note = maybe_recalc(xlsx_path)

    first = monthly.iloc[0]
    last = monthly.iloc[-1]
    max_row = monthly.loc[monthly["close"].idxmax()]
    min_row = monthly.loc[monthly["close"].idxmin()]
    summary = {
        "name": args.name,
        "source": source,
        "points": int(len(monthly)),
        "start": str(first["date"]),
        "end": str(last["date"]),
        "first_close": round(float(first["close"]), 2),
        "last_close": round(float(last["close"]), 2),
        "max_close": {"date": str(max_row["date"]),
                      "value": round(float(max_row["close"]), 2)},
        "min_close": {"date": str(min_row["date"]),
                      "value": round(float(min_row["close"]), 2)},
        "markers": info["markers"],
        "png": str(png_path),
        "xlsx": str(xlsx_path),
        "cjk_font": font or "未找到中文字体（图中中文可能显示为方块）",
        "note": recalc_note,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
