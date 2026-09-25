"""AI 洞察模块：调用 DeepSeek 生成报告解读文本。

需求 8.x：基于统计数据 + 新闻，生成人话版的市场解读。
本模块负责：
1. 组装提示词（数据摘要 + 新闻摘要 + 输出格式约束）
2. 调用 DeepSeek Chat API
3. 解析返回内容，按章节组织
4. 兜底：API 不可用时返回模板化文案

生产环境填入 DEEPSEEK_API_KEY 即可启用真实生成。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Optional

import requests

from config.settings import settings
from core.metrics import HSPeriodAggregate, market_score, growth_distribution

log = logging.getLogger(__name__)


@dataclass
class Insight:
    """单段洞察：标题 + 正文 + 标签。"""
    title: str
    body: str
    tag: str = "summary"  # summary / opportunity / risk / news
    references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"title": self.title, "body": self.body, "tag": self.tag,
                "references": self.references}


@dataclass
class InsightReport:
    """完整的洞察报告：概览 + 机会 + 风险 + 新闻解读。"""
    overview: str = ""
    opportunities: list[Insight] = field(default_factory=list)
    risks: list[Insight] = field(default_factory=list)
    news_summary: str = ""
    disclaimer: str = "本解读由 AI 基于海关公开统计数据自动生成，仅供参考，不构成投资建议。"

    def to_dict(self) -> dict:
        return {
            "overview": self.overview,
            "opportunities": [i.to_dict() for i in self.opportunities],
            "risks": [i.to_dict() for i in self.risks],
            "news_summary": self.news_summary,
            "disclaimer": self.disclaimer,
        }


# ============================ 主入口 ========================================

def generate_insight(
    hs_code: str,
    hs_name: str,
    cur: HSPeriodAggregate,
    prev: Optional[HSPeriodAggregate],
    prev_year: Optional[HSPeriodAggregate],
    news: Optional[list] = None,
    focus_countries: Optional[list[str]] = None,
) -> InsightReport:
    """生成 AI 洞察报告。有 Key 走真实 API，否则返回模板文案。"""
    payload = _build_payload(hs_code, hs_name, cur, prev, prev_year, news, focus_countries)
    if settings.DEEPSEEK_API_KEY:
        try:
            text = _call_deepseek(payload)
            return _parse_response(text, cur)
        except Exception as e:
            log.warning("DeepSeek 调用失败，使用模板兜底: %s", e)
    return _template_insight(hs_code, hs_name, cur, prev, news)


# ============================ 提示词组装 =====================================

def _build_payload(hs_code, hs_name, cur, prev, prev_year, news, focus_countries) -> dict:
    """组装发送给大模型的上下文。"""
    top = cur.top_countries(10)
    market_lines = "\n".join(
        f"  {i+1}. {c}: ${float(a)/1e6:.2f}M ({s:.1f}%)" for i, (c, a, s) in enumerate(top)
    )
    mom = cur.growth_mom(prev) if prev else None
    yoy = cur.growth_yoy(prev_year) if prev_year else None

    news_lines = ""
    if news:
        news_lines = "\n".join(f"  - {n.title if hasattr(n,'title') else n.get('title','')}"
                               for n in news[:5])

    prompt = f"""你是一名资深外贸分析师。请基于以下海关出口数据，为该品类的出口企业撰写一份简明、专业、可直接用于决策参考的市场解读（中文）。

【品类】{hs_name}（HS {hs_code}）
【数据期】{cur.period}
【全球出口总额】${float(cur.world_amount)/1e6:.2f} M
【贸易伙伴数】{len(cur.partner_amounts)}
【环比增速】{f"{mom:+.2f}%" if mom is not None else "无上月数据"}
【同比增速】{f"{yoy:+.2f}%" if yoy is not None else "无同期数据"}
【集中度 CR5 / CR10】{cur.concentration(5):.1f}% / {cur.concentration(10):.1f}%

【主要市场 Top10】
{market_lines}
"""

    if focus_countries:
        prompt += f"\n【重点关注国家】{', '.join(focus_countries)}"

    if news_lines:
        prompt += f"\n\n【近期相关新闻】\n{news_lines}"

    prompt += """

请严格按以下 JSON 结构输出，不要输出 JSON 之外的任何内容：
{
  "overview": "一段 150 字以内的整体市场概览，说清规模、趋势与格局",
  "opportunities": [
    {"title": "机会点标题", "body": "具体说明，含国家/金额/增速", "tag": "opportunity"}
  ],
  "risks": [
    {"title": "风险点标题", "body": "具体说明", "tag": "risk"}
  ],
  "news_summary": "结合新闻的政策/事件解读，100 字以内"
}
机会和风险各给出 2-3 条，内容必须基于数据，不要编造具体数字。"""

    return {
        "model": settings.DEEPSEEK_MODEL_DEFAULT,
        "messages": [
            {"role": "system", "content": "你是资深外贸数据分析师，擅长将海关统计与市场动态结合，输出结构化、可落地的商业洞察。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.5,
        "response_format": {"type": "json_object"},
        "max_tokens": 1500,
    }


# ============================ API 调用 =======================================

def _call_deepseek(payload: dict) -> str:
    url = f"{settings.DEEPSEEK_BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    r = requests.post(url, headers=headers, json=payload,
                      timeout=settings.DEEPSEEK_TIMEOUT_SECONDS)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def _parse_response(text: str, cur: HSPeriodAggregate) -> InsightReport:
    """解析大模型返回的 JSON，容错处理。"""
    try:
        obj = json.loads(text)
    except Exception:
        log.warning("AI 返回非 JSON，使用原文")
        return InsightReport(overview=text[:500])

    out = InsightReport(overview=(obj.get("overview") or "")[:600])
    for item in obj.get("opportunities", [])[:3]:
        out.opportunities.append(Insight(
            title=item.get("title", "")[:60],
            body=item.get("body", "")[:400],
            tag="opportunity",
        ))
    for item in obj.get("risks", [])[:3]:
        out.risks.append(Insight(
            title=item.get("title", "")[:60],
            body=item.get("body", "")[:400],
            tag="risk",
        ))
    out.news_summary = (obj.get("news_summary") or "")[:400]
    return out


# ============================ 模板兜底 =======================================

def _template_insight(hs_code: str, hs_name: str, cur: HSPeriodAggregate,
                      prev: Optional[HSPeriodAggregate],
                      news: Optional[list] = None) -> InsightReport:
    """无 AI Key 时的模板化文案，保证报告可读。"""
    top = cur.top_countries(3)
    leaders = "、".join(c for c, _, _ in top[:3])
    mom = cur.growth_mom(prev) if prev else None
    trend = f"环比{mom:+.1f}%" if mom is not None else "缺乏对比数据"
    cr10 = cur.concentration(10)

    concentration = "高度集中" if cr10 > 60 else ("较为集中" if cr10 > 40 else "相对分散")

    out = InsightReport()
    out.overview = (
        f"{hs_name}（HS {hs_code}）本期全球出口总额 "
        f"${float(cur.world_amount)/1e6:.1f}M，{trend}。"
        f"主要出口市场为 {leaders}，市场格局{concentration}（CR10={cr10:.1f}%）。"
    )

    # 机会：规模大且份额低的作为增量空间
    scores = market_score(cur, prev, top_n=30)
    for s in scores[:2]:
        out.opportunities.append(Insight(
            title=f"关注 {s['country']} 的增量空间",
            body=f"该市场本期出口 ${s['amount_usd']/1e6:.2f}M，"
                 f"占全球 {s['share_pct']}%，份额空间评分 {s['score_share']:.0f}/100，值得评估拓展。",
            tag="opportunity",
        ))

    # 风险：集中度
    if cr10 > 60:
        out.risks.append(Insight(
            title="单一市场依赖度偏高",
            body=f"前 10 国合计占比 {cr10:.1f}%，其中美国市场占比最大，"
                 f"需关注贸易政策与汇率波动带来的集中度风险。",
            tag="risk",
        ))

    if news:
        titles = [n.title if hasattr(n, "title") else n.get("title", "") for n in news[:3]]
        out.news_summary = "近期相关动态：" + "；".join(t for t in titles if t)[:200]
    else:
        out.news_summary = "（未配置新闻源与 AI 服务，此处为模板内容，请配置 DEEPSEEK_API_KEY 与新闻源 Key 启用智能解读）"

    return out
