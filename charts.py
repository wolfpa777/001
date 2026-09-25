"""图表生成：基于 matplotlib，输出 PNG 到 data/charts/。

图表类型（对应需求文档报告内容）：
1. 市场份额饼图（Top5 + 其他）
2. 国家金额对比柱状图（Top N）
3. 增长/下滑双色柱状图
4. 机会度雷达图
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")  # 无头模式，不依赖 DISPLAY
import matplotlib.pyplot as plt
from matplotlib import font_manager

from config.settings import settings

# ---- 中文字体：优先文泉驿，回退 DejaVu ----
_FONT_SET = False


def _ensure_font() -> None:
    global _FONT_SET
    if _FONT_SET:
        return
    candidates = ["WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "Noto Sans CJK SC", "SimHei"]
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in candidates:
        if name in available:
            matplotlib.rcParams["font.sans-serif"] = [name]
            break
    matplotlib.rcParams["axes.unicode_minus"] = False
    _FONT_SET = True


# ---- 配色（沿用品牌蓝 + 预警红绿）----
C_BLUE = "#2E6BE6"
C_BLUE_L = "#6FA0F5"
C_ORANGE = "#F59E0B"
C_RED = "#E5484D"
C_GREEN = "#30A46C"
C_GRAY = "#9AA5B1"
PALETTE = ["#2E6BE6", "#F59E0B", "#30A46C", "#E5484D", "#7C4DFF",
           "#06B6D4", "#F97316", "#EC4899", "#6366F1", "#14B8A6"]


def _save(fig, hs_code: str, chart_type: str, period: str) -> str:
    """保存图表，返回绝对文件路径（供 reportlab Image 直接读取）。"""
    _ensure_font()
    settings.CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{hs_code}_{chart_type}_{period}.png"
    fpath = settings.CHARTS_DIR / fname
    fig.savefig(fpath, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return str(fpath)


# ============================ 具体图表 ======================================

def share_pie(
    items: list[tuple[str, float]],
    hs_code: str,
    period: str,
    top_n: int = 5,
) -> str:
    """市场份额饼图：Top N + 其他。items = [(国家, 金额), ...] 已按金额降序。"""
    _ensure_font()
    if not items:
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=14, color=C_GRAY)
        ax.set_axis_off()
        return _save(fig, hs_code, "share", period)

    top = items[:top_n]
    other_sum = sum(a for _, a in items[top_n:])
    labels = [c for c, _ in top]
    vals = [float(a) for _, a in top]
    if other_sum > 0:
        labels.append("其他")
        vals.append(float(other_sum))

    fig, ax = plt.subplots(figsize=(6.4, 6.4), dpi=150)
    wedges, texts, autotexts = ax.pie(
        vals,
        labels=None,
        autopct=lambda p: f"{p:.1f}%" if p >= 3 else "",
        startangle=90,
        counterclock=False,
        colors=PALETTE[:len(vals)],
        wedgeprops=dict(width=0.45, edgecolor="white", linewidth=1.5),
        pctdistance=0.78,
        textprops=dict(fontsize=10, color="white", fontweight="bold"),
    )
    ax.legend(wedges, [f"{l}" for l in labels], loc="center left",
              bbox_to_anchor=(1.0, 0.5), fontsize=10, frameon=False)
    ax.set_title(f"市场份额分布（{period}）", fontsize=13, fontweight="bold", pad=14)
    return _save(fig, hs_code, "share", period)


def amount_bar(
    items: list[tuple[str, float]],
    hs_code: str,
    period: str,
    top_n: int = 15,
) -> str:
    """国家金额对比柱状图。items = [(国家, 金额), ...] 降序。"""
    _ensure_font()
    if not items:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=14, color=C_GRAY)
        ax.set_axis_off()
        return _save(fig, hs_code, "amount", period)

    items = items[:top_n][::-1]  # 横条图，倒序让最高在最上
    countries = [c for c, _ in items]
    amounts = [float(a) / 1e6 for _, a in items]  # 转百万美元

    fig, ax = plt.subplots(figsize=(8, max(4, len(countries) * 0.42)), dpi=150)
    bars = ax.barh(countries, amounts, color=C_BLUE, height=0.66)
    for bar, amt in zip(bars, amounts):
        ax.text(bar.get_width() + max(amounts) * 0.01, bar.get_y() + bar.get_height() / 2,
                f"${amt:.1f}M", va="center", fontsize=8.5, color="#333")
    ax.set_xlabel("出口金额（百万美元）", fontsize=10)
    ax.set_title(f"出口金额 Top{len(countries)} 国家/地区（{period}）", fontsize=13, fontweight="bold", pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    ax.set_xlim(0, max(amounts) * 1.18)
    return _save(fig, hs_code, "amount", period)


def growth_bar(
    items: list[tuple[str, float, Optional[float]]],
    hs_code: str,
    period: str,
    top_n: int = 10,
) -> str:
    """增长/下滑双色柱状图。items = [(国家, 环比%, 同比%), None 的同比不展示]。

    只取环比绝对值最大的 top_n 个，正向绿色、负向红色。
    """
    _ensure_font()
    if not items:
        fig, ax = plt.subplots(figsize=(8, 5))
        ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=14, color=C_GRAY)
        ax.set_axis_off()
        return _save(fig, hs_code, "growth", period)

    # 按 |环比| 排序取 top_n
    ranked = sorted(items, key=lambda x: abs(x[1] or 0), reverse=True)[:top_n]
    countries = [c for c, _, _ in ranked]
    rates = [float(r) if r is not None else 0.0 for _, r, _ in ranked]
    colors = [C_GREEN if r >= 0 else C_RED for r in rates]

    fig, ax = plt.subplots(figsize=(8, max(4, len(countries) * 0.45)), dpi=150)
    bars = ax.barh(countries[::-1], rates[::-1], color=colors[::-1], height=0.66)
    for bar, r in zip(bars, rates[::-1]):
        x = bar.get_width()
        ax.text((x + max(map(abs, rates)) * 0.01 * (1 if x >= 0 else -1)),
                bar.get_y() + bar.get_height() / 2,
                f"{r:+.1f}%", va="center", fontsize=9,
                ha="left" if x >= 0 else "right", color="#333")
    ax.axvline(0, color="#666", linewidth=0.8)
    ax.set_xlabel("环比增速（%）", fontsize=10)
    ax.set_title(f"环比增速（{period} vs 上月）", fontsize=13, fontweight="bold", pad=12)
    ax.spines[["top", "right"]].set_visible(False)
    return _save(fig, hs_code, "growth", period)


def opportunity_radar(
    scores: list[dict],
    hs_code: str,
    period: str,
    top_n: int = 6,
) -> str:
    """机会度雷达图：展示前 N 国四维得分。scores = [{country, growth, share, stability, momentum}, ...]"""
    _ensure_font()
    if not scores:
        fig, ax = plt.subplots(figsize=(7, 7))
        ax.text(0.5, 0.5, "暂无数据", ha="center", va="center", fontsize=14, color=C_GRAY)
        ax.set_axis_off()
        return _save(fig, hs_code, "opportunity", period)

    dims = ["growth", "share", "stability", "momentum"]
    dim_labels = {"growth": "增长性", "share": "份额空间", "stability": "稳定性", "momentum": "动能"}

    fig = plt.figure(figsize=(7.2, 7.2), dpi=150)
    ax = fig.add_subplot(111, polar=True)

    n = len(dims)
    angles = [i / n * 2 * 3.14159 for i in range(n)]
    angles += angles[:1]

    for i, s in enumerate(scores[:top_n]):
        vals = [float(s.get(d, 0)) for d in dims]
        vals += vals[:1]
        ax.plot(angles, vals, linewidth=1.8, color=PALETTE[i % len(PALETTE)], label=s["country"])
        ax.fill(angles, vals, alpha=0.12, color=PALETTE[i % len(PALETTE)])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels([dim_labels[d] for d in dims], fontsize=10.5)
    ax.set_ylim(0, 100)
    ax.set_yticks([20, 40, 60, 80, 100])
    ax.set_yticklabels(["20", "40", "60", "80", "100"], fontsize=8, color=C_GRAY)
    ax.set_title(f"国家机会度四维评估（{period}）", fontsize=13, fontweight="bold", pad=22)
    ax.legend(loc="upper right", bbox_to_anchor=(1.18, 1.12), fontsize=9, frameon=False)
    return _save(fig, hs_code, "opportunity", period)
