"""报告生成服务：组装指标 -> 生成图表 -> 输出 PDF（ReportLab）。

报告内容结构：
  1. 封面：品类名称 / HS 编码 / 数据期 / 生成时间
  2. 核心指标概览：全球总额、同比、环比、国家数、CR10
  3. 市场结构：Top 国家金额 + 市场份额饼图 + 柱状图
  4. 增长榜：环比 / 同比双色图
  5. 排名变化：升跌榜
  6. 机会度：雷达图 + 说明
  7. 附录：数据来源与口径说明
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Image,
    Table, TableStyle, KeepTogether,
)

from config.settings import settings
from core import metrics as M
from core.charts import share_pie, amount_bar, growth_bar, opportunity_radar
from core.metrics import HSPeriodAggregate, aggregate_hs
from core.models import ExportRecord

log = logging.getLogger(__name__)

# ---- 中文字体注册 ----
_FONT_READY = False


def _register_fonts() -> bool:
    """尝试注册中文字体。返回 True 表示有可用中文字体。"""
    global _FONT_READY
    if _FONT_READY:
        return True
    candidates = [
        ("WenQuanYi Micro Hei", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        ("WenQuanYi Zen Hei", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        ("Noto Sans CJK SC", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    for name, path in candidates:
        if Path(path).exists():
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                _FONT_READY = True
                return True
            except Exception as e:
                log.warning("字体注册失败 %s: %s", path, e)
                continue
    # 没有中文字体也返回 True，用内置 Helvetica 兜底（中文会显示为方块，但英文正常）
    _FONT_READY = True
    return False


_FONT_NAME = "WenQuanYi Micro Hei"
_FONT_BOLD = "WenQuanYi Micro Hei"


@dataclass
class ReportInput:
    """报告输入：HS 编码 + 数据期。"""
    hs_code: str
    period: str
    hs_name: Optional[str] = None
    focus_countries: list[str] = field(default_factory=list)


@dataclass
class ReportResult:
    """报告生成结果。"""
    file_path: str
    file_url: str
    file_size: int
    period: str
    hs_code: str


# ============================ 数据准备 ======================================

def _load_records_for_period(period: str) -> list[ExportRecord]:
    """从海关库加载指定月份的全部明细。生产环境用 repository，此处直接读引擎。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    from config.database_binds import _BIND_TO_URL, BIND_TRADE
    from core.models import ExportRecord as ER

    url = _BIND_TO_URL[BIND_TRADE]
    eng = create_engine(url, future=True,
                        connect_args={"check_same_thread": False, "timeout": 30},
                        poolclass=StaticPool)
    out: list[ER] = []
    with eng.begin() as c:
        rows = c.execute(
            text("select period, hs_code, hs_name, partner_name, amount_usd "
                 "from exports where period=:p and hs_code=:hs"),
            {"p": period, "hs": None},
        ).fetchall()
    return out


def prepare_aggregates(hs_code: str, period: str) -> dict:
    """加载数据并聚合当月与对比期。返回 dict 供后续渲染。"""
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    from config.database_binds import _BIND_TO_URL, BIND_TRADE

    url = _BIND_TO_URL[BIND_TRADE]
    eng = create_engine(url, future=True,
                        connect_args={"check_same_thread": False, "timeout": 30},
                        poolclass=StaticPool)

    def _fetch(p: str) -> list[ExportRecord]:
        with eng.begin() as c:
            rows = c.execute(
                text("select period, hs_code, hs_name, partner_name, amount_usd "
                     "from exports where period=:p"),
                {"p": p},
            ).fetchall()
        return [ExportRecord(period=r[0], hs_code=r[1], hs_name=r[2], partner_name=r[3],
                             amount_usd=r[4]) for r in rows]

    cur = aggregate_hs(_fetch(period), hs_code, period)
    prev_p = M.period_add(period, -1)
    prev = aggregate_hs(_fetch(prev_p), hs_code, prev_p) if _fetch(prev_p) else None
    return {"current": cur, "prev": prev}


# ============================ 样式 ==========================================

def _styles() -> dict:
    base = getSampleStyleSheet()
    font = _FONT_NAME
    return {
        "title": ParagraphStyle("title", parent=base["Title"], fontName=font, fontSize=22,
                                leading=30, spaceAfter=6, alignment=TA_CENTER, textColor=colors.HexColor("#1A2B4A")),
        "subtitle": ParagraphStyle("subtitle", parent=base["Normal"], fontName=font, fontSize=12,
                                   leading=18, alignment=TA_CENTER, textColor=colors.HexColor("#5A6B85"),
                                   spaceAfter=24),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName=font, fontSize=15, leading=22,
                             textColor=colors.HexColor("#1A2B4A"), spaceBefore=14, spaceAfter=8),
        "h3": ParagraphStyle("h3", parent=base["Heading3"], fontName=font, fontSize=12, leading=17,
                             textColor=colors.HexColor("#2E6BE6"), spaceBefore=8, spaceAfter=5),
        "body": ParagraphStyle("body", parent=base["Normal"], fontName=font, fontSize=9.5, leading=15,
                               textColor=colors.HexColor("#333333")),
        "small": ParagraphStyle("small", parent=base["Normal"], fontName=font, fontSize=8, leading=12,
                                textColor=colors.HexColor("#777777")),
        "note": ParagraphStyle("note", parent=base["Normal"], fontName=font, fontSize=8.5, leading=13,
                               textColor=colors.HexColor("#5A6B85")),
    }


def _mk_story() -> list:
    return []


# ============================ 渲染组件 ======================================

def _cover(story: list, inp: ReportInput, styles: dict) -> None:
    story.append(Spacer(1, 5 * cm))
    story.append(Paragraph("出口商品国际市场观测报告", styles["title"]))
    story.append(Spacer(1, 0.6 * cm))
    if inp.hs_name:
        story.append(Paragraph(inp.hs_name, ParagraphStyle("hsname", parent=styles["subtitle"],
                     fontSize=16, textColor=colors.HexColor("#2E6BE6"))))
    story.append(Paragraph(f"HS 编码：{inp.hs_code}", styles["subtitle"]))
    story.append(Paragraph(f"数据期：{inp.period}", styles["subtitle"]))
    story.append(Spacer(1, 1.5 * cm))
    story.append(Paragraph(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}", styles["small"]))
    story.append(Paragraph("数据来源：中国海关统计月报", styles["small"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("本报告的同比为与上年同月比较，环比为与上月比较，金额单位均为美元。",
                           styles["note"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph("声明：本报告基于海关公开统计数据自动生成，仅供参考，不构成投资建议。",
                           styles["note"]))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph("© 出口商品国际市场观测报告工具", styles["small"]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(Paragraph(f"页码：第 1 页", styles["small"]))


def _kpis(story: list, cur: HSPeriodAggregate, prev: Optional[HSPeriodAggregate],
          styles: dict) -> None:
    story.append(Paragraph("一、核心指标概览", styles["h2"]))
    story.append(Paragraph("本节给出本期全球出口总额、增速、贸易伙伴集中度等关键指标。",
                           styles["body"]))

    mom = cur.growth_mom(prev) if prev else None
    cr10 = cur.concentration(10)
    cr5 = cur.concentration(5)

    rows = [[
        Paragraph("指标", styles["body"]),
        Paragraph("数值", styles["body"]),
        Paragraph("解读", styles["body"]),
    ]]
    rows.append([
        Paragraph("全球出口总额", styles["body"]),
        Paragraph(f"${float(cur.world_amount)/1e6:.1f} M", styles["body"]),
        Paragraph("本期全部贸易伙伴出口金额合计", styles["body"]),
    ])
    rows.append([
        Paragraph("贸易伙伴数", styles["body"]),
        Paragraph(str(len(cur.partner_amounts)), styles["body"]),
        Paragraph("有出口记录的国家/地区数量", styles["body"]),
    ])
    rows.append([
        Paragraph("环比增速", styles["body"]),
        Paragraph(f"{mom:+.2f}%" if mom is not None else "—", styles["body"]),
        Paragraph("与上月相比" if mom is not None else "无上月数据", styles["body"]),
    ])
    rows.append([
        Paragraph("CR5", styles["body"]),
        Paragraph(f"{cr5:.1f}%", styles["body"]),
        Paragraph("前 5 国份额，越高越集中", styles["body"]),
    ])
    rows.append([
        Paragraph("CR10", styles["body"]),
        Paragraph(f"{cr10:.1f}%", styles["body"]),
        Paragraph("前 10 国份额，衡量集中度", styles["body"]),
    ])

    t = Table(rows, colWidths=[4.2 * cm, 4 * cm, 8.8 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E6BE6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FC")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDE3EC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(Spacer(1, 0.3 * cm))
    story.append(t)


def _section_structure(story: list, cur: HSPeriodAggregate, inp: ReportInput,
                       styles: dict) -> None:
    story.append(Paragraph("二、市场结构", styles["h2"]))
    story.append(Paragraph("本节展示主要贸易伙伴的出口金额与市场份额分布。", styles["body"]))
    story.append(Spacer(1, 0.3 * cm))

    top = cur.top_countries(15)
    table_rows = [[
        Paragraph("排名", styles["body"]),
        Paragraph("国家/地区", styles["body"]),
        Paragraph("出口金额(美元)", styles["body"]),
        Paragraph("市场份额", styles["body"]),
    ]]
    for i, (c, amt, share) in enumerate(top, 1):
        table_rows.append([
            Paragraph(str(i), styles["body"]),
            Paragraph(c, styles["body"]),
            Paragraph(f"{float(amt):,.0f}", styles["body"]),
            Paragraph(f"{share:.2f}%", styles["body"]),
        ])
    t = Table(table_rows, colWidths=[1.6 * cm, 5.5 * cm, 6 * cm, 3.9 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E6BE6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FC")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDE3EC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (3, -1), "RIGHT"),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.5 * cm))

    # 图表
    pie_items = [(c, float(amt)) for c, amt, _ in cur.top_countries(5)]
    pie_url = share_pie(pie_items, inp.hs_code, inp.period, top_n=5)
    story.append(Image(pie_url, width=11 * cm, height=11 * cm, hAlign="CENTER"))
    story.append(Spacer(1, 0.3 * cm))

    bar_items = [(c, float(amt)) for c, amt, _ in cur.top_countries(15)]
    bar_url = amount_bar(bar_items, inp.hs_code, inp.period, top_n=15)
    story.append(Image(bar_url, width=16 * cm, height=8.4 * cm, hAlign="CENTER"))


def _section_growth(story: list, cur: HSPeriodAggregate, prev: Optional[HSPeriodAggregate],
                    inp: ReportInput, styles: dict) -> None:
    story.append(Paragraph("三、增长与下滑", styles["h2"]))
    story.append(Paragraph("本节对比上月，标出增速最快与下滑最明显的贸易伙伴。",
                           styles["body"]))
    if prev is None:
        story.append(Paragraph("无上月数据，无法计算环比。", styles["note"]))
        return

    items = []
    for c, amt in cur.partner_amounts.items():
        prev_amt = prev.partner_amounts.get(c, Decimal(0))
        if prev_amt == 0:
            continue
        mom = float((amt - prev_amt) / prev_amt * 100)
        items.append((c, mom, None))
    # 过滤掉绝对金额过小的扰动项（占全球 < 0.02%），只看有规模的市场
    min_amt = cur.world_amount * Decimal("0.0002")
    items = [i for i in items if cur.partner_amounts.get(i[0], 0) >= min_amt]
    items.sort(key=lambda x: abs(x[1]), reverse=True)

    story.append(Spacer(1, 0.3 * cm))
    bar_url = growth_bar(items, inp.hs_code, inp.period, top_n=10)
    story.append(Image(bar_url, width=16 * cm, height=8.4 * cm, hAlign="CENTER"))

    story.append(Spacer(1, 0.3 * cm))
    # 增长榜 / 下滑榜 表格（同样过滤小额市场）
    ups = [i for i in items if i[1] > 0][:5]
    downs = [i for i in items if i[1] < 0][:5]
    rows = [[Paragraph("类型", styles["body"]),
             Paragraph("国家/地区", styles["body"]),
             Paragraph("本期金额", styles["body"]),
             Paragraph("上期金额", styles["body"]),
             Paragraph("环比", styles["body"])]]
    for tag, lst in (("↑ 增长", ups), ("↓ 下滑", downs)):
        for c, r, _ in lst:
            amt = cur.partner_amounts[c]
            prev_amt = prev.partner_amounts.get(c, Decimal(0))
            rows.append([
                Paragraph(tag, styles["body"]),
                Paragraph(c, styles["body"]),
                Paragraph(f"${float(amt)/1e6:.2f}M", styles["body"]),
                Paragraph(f"${float(prev_amt)/1e6:.2f}M", styles["body"]),
                Paragraph(f"{r:+.1f}%", styles["body"]),
            ])
    t = Table(rows, colWidths=[1.6 * cm, 4.5 * cm, 3.5 * cm, 3.5 * cm, 3.9 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E6BE6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FC")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDE3EC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
    ]))
    story.append(t)


def _section_opportunity(story: list, cur: HSPeriodAggregate, prev: Optional[HSPeriodAggregate],
                         inp: ReportInput, styles: dict) -> None:
    story.append(Paragraph("四、国家机会度评估", styles["h2"]))
    story.append(Paragraph("从增长性、份额空间、稳定性、动能四个维度打分（满分 100），"
                           "识别值得重点关注的新兴市场。", styles["body"]))
    story.append(Spacer(1, 0.3 * cm))

    scores = M.market_score(cur, prev)
    top = scores[:6]
    radar = [{
        "country": s["country"],
        "growth": s["score_growth"],
        "share": s["score_share"],
        "stability": s["score_share"],   # 份额空间兼作稳定性基准
        "momentum": s["score_scale"],    # 规模增速兼作动能
    } for s in top]
    radar_url = opportunity_radar(radar, inp.hs_code, inp.period, top_n=6)
    story.append(Image(radar_url, width=13 * cm, height=13 * cm, hAlign="CENTER"))

    story.append(Spacer(1, 0.3 * cm))
    rows = [[Paragraph("排名", styles["body"]),
             Paragraph("国家/地区", styles["body"]),
             Paragraph("综合分", styles["body"]),
             Paragraph("增长", styles["body"]),
             Paragraph("份额", styles["body"]),
             Paragraph("稳定", styles["body"]),
             Paragraph("动能", styles["body"])]]
    for i, s in enumerate(top, 1):
        rows.append([
            Paragraph(str(i), styles["body"]),
            Paragraph(s["country"], styles["body"]),
            Paragraph(f"{s['score_composite']:.0f}", styles["body"]),
            Paragraph(f"{s['score_growth']:.0f}", styles["body"]),
            Paragraph(f"{s['score_share']:.0f}", styles["body"]),
            Paragraph(f"{s['score_share']:.0f}", styles["body"]),
            Paragraph(f"{s['score_scale']:.0f}", styles["body"]),
        ])
    t = Table(rows, colWidths=[1.5 * cm, 4.2 * cm, 2.2 * cm, 2 * cm, 2 * cm, 2 * cm, 2 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), _FONT_NAME),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2E6BE6")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FC")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDE3EC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
    ]))
    story.append(t)


def _section_appendix(story: list, styles: dict) -> None:
    story.append(Paragraph("附录：数据口径与说明", styles["h2"]))
    notes = [
        "数据来源：中国海关统计月报，按月度汇总。",
        "金额单位：美元（USD），保留两位小数。",
        "同比：与上年同月比较；环比：与上月比较。",
        "市场份额：单个贸易伙伴出口金额 ÷ 全球出口总额。",
        "CRn：前 n 个贸易伙伴金额合计 ÷ 全球出口总额，衡量集中度。",
        "国家机会度评分仅基于历史贸易数据，不含政策、汇率等非数据因素。",
        "本报告为自动化生成，可能存在数据缺失或延迟，请结合其他信息综合判断。",
    ]
    for n in notes:
        story.append(Paragraph(f"• {n}", styles["note"]))
        story.append(Spacer(1, 0.15 * cm))


# ============================ 主入口 ========================================

def generate_report(inp: ReportInput) -> ReportResult:
    """生成报告 PDF，返回结果。"""
    _register_fonts()
    aggs = prepare_aggregates(inp.hs_code, inp.period)
    cur: HSPeriodAggregate = aggs["current"]
    prev: Optional[HSPeriodAggregate] = aggs["prev"]

    styles = _styles()
    story = _mk_story()

    _cover(story, inp, styles)
    story.append(Spacer(1, 1 * cm))

    _kpis(story, cur, prev, styles)
    story.append(Spacer(1, 0.6 * cm))

    _section_structure(story, cur, inp, styles)
    story.append(Spacer(1, 0.6 * cm))

    _section_growth(story, cur, prev, inp, styles)
    story.append(Spacer(1, 0.6 * cm))

    _section_opportunity(story, cur, prev, inp, styles)
    story.append(Spacer(1, 0.6 * cm))

    _section_appendix(story, styles)

    # ---- 输出 ----
    settings.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"report_{inp.hs_code}_{inp.period}.pdf"
    fpath = settings.REPORTS_DIR / fname

    doc = BaseDocTemplate(
        str(fpath), pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame])])
    doc.build(story)

    size = fpath.stat().st_size
    return ReportResult(
        file_path=str(fpath),
        file_url=f"/data/reports/{fname}",
        file_size=size,
        period=inp.period,
        hs_code=inp.hs_code,
    )
