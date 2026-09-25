"""出口指标计算。

口径严格遵循需求 4.5 / 7.2 / 7.3：
- 默认取 HS6 汇总行；HS6 行金额应已包含其全部 HS8 子目，禁止把 HS6 与 HS8 重复累加
- 同比增速 = (本期 - 上年同期) / |上年同期|；基数缺失或为 0 时返回 None（图表留空，不画 0）
- 环比 = (本月 - 上月) / |上月|；上月缺失返回 None
- 市场份额 = 该国金额 / 全球总额；集中度 = CR10
- 均价仅在连续 ≥ UNIT_PRICE_MIN_MONTHS 月计量单位一致时计算，否则 null
- 需求 5.3：排名支持 T+N（取报告期往后 N 期数据，应对数据发布延迟）
- 排名变化 = 上报告期排名 - 本报告期排名（正=上升）
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Optional

from config.settings import settings
from core.models import ExportRecord

ZERO = Decimal("0")
NINF = float("-inf")


def safe_div(a: Decimal, b: Decimal) -> float | None:
    """除法：分母为 0 或空返回 None，供前端决定是否渲染。"""
    if b is None or b == ZERO:
        return None
    try:
        return float(a / b)
    except Exception:
        return None


def safe_pct(numer: Decimal, denom: Decimal) -> float | None:
    """百分比：(a - b)/|b|，b 为 0 返回 None。"""
    if denom is None or denom == ZERO:
        return None
    return float((numer - denom) / abs(denom)) * 100.0


def market_share(world: Decimal, amount: Decimal) -> float | None:
    """市场份额 = 子市场 / 全球总额。"""
    return safe_div(amount, world)


# ---- 同比/环比周期工具 -----------------------------------------------------

def period_add(period: str, months: int) -> str:
    """YYYY-MM ± N 个月。"""
    y, m = int(period[:4]), int(period[5:7])
    total = y * 12 + (m - 1) + months
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def period_range(start: str, end: str) -> list[str]:
    out, cur = [], start
    while cur <= end:
        out.append(cur)
        cur = period_add(cur, 1)
    return out


def latest_period_on(asof: date | None = None) -> str:
    """截至某日的最新数据期：海关通常在次月中旬发布上月数据，
    因此「本月 20 号前」最新为两个月前，20 号及以后最新为上个月。"""
    asof = asof or date.today()
    y, m = asof.year, asof.month
    if asof.day < settings.DATA_READINESS_CHECK_DAY:  # 20 号前
        m -= 2
    else:
        m -= 1
    if m <= 0:
        y -= 1
        m += 12
    return f"{y:04d}-{m:02d}"


def same_period_last_year(period: str) -> str:
    return period_add(period, -12)


# ---- 汇总查询：从明细聚合到「某 HS 某期全球总额 / 分国金额」 --------------------

@dataclass
class HSPeriodAggregate:
    """一个 HS 编码在某个数据期的全球汇总。"""
    hs_code: str
    period: str
    world_amount: Decimal = ZERO           # 全球出口总额
    partner_amounts: dict[str, Decimal] = field(default_factory=dict)  # 国家 -> 金额
    months_covered: int = 0               # 该期内实际有数据的月数（T+N 用）

    def market_share(self, country: str) -> float | None:
        return safe_div(self.partner_amounts.get(country, ZERO), self.world_amount)

    def growth_yoy(self, prev_year: "HSPeriodAggregate | None") -> float | None:
        if prev_year is None or prev_year.world_amount == ZERO:
            return None
        return safe_pct(self.world_amount, prev_year.world_amount)

    def growth_mom(self, prev_month: "HSPeriodAggregate | None") -> float | None:
        if prev_month is None or prev_month.world_amount == ZERO:
            return None
        return safe_pct(self.world_amount, prev_month.world_amount)

    def top_countries(self, n: int, focus: list[str] | None = None) -> list[tuple[str, Decimal, float]]:
        """返回 [(国家, 金额, 份额%)] 按金额降序。focus 非空时优先保留关注国家。"""
        items = sorted(self.partner_amounts.items(), key=lambda kv: kv[1], reverse=True)
        if focus:
            focus_set = {c for c in focus}
            head = [(c, a, self.market_share(c) or 0.0) for c, a in items if c in focus_set][:n]
            seen = {c for c, _, _ in head}
            tail = [(c, a, self.market_share(c) or 0.0) for c, a in items if c not in seen][: n - len(head)]
            return head + tail
        return [(c, a, (self.market_share(c) or 0.0) * 100.0) for c, a in items[:n]]

    def concentration(self, n: int = 10) -> float:
        """CRn：前 n 国金额合计 / 全球总额。"""
        top = sum(a for _, a in sorted(self.partner_amounts.items(), key=lambda kv: kv[1], reverse=True)[:n])
        return (safe_div(top, self.world_amount) or 0.0) * 100.0


def aggregate_hs(
    records: Iterable[ExportRecord],
    hs_code: str,
    period: str,
) -> HSPeriodAggregate:
    """把一组明细行（已过滤 HS 与期）聚合成 HSPeriodAggregate。"""
    agg = HSPeriodAggregate(hs_code=hs_code, period=period)
    for r in records:
        amt = r.amount_usd or ZERO
        agg.world_amount += amt
        pname = r.partner_name or "未知"
        agg.partner_amounts[pname] = agg.partner_amounts.get(pname, ZERO) + amt
    return agg


# ---- 均价计算：单位一致校验 ------------------------------------------------

@dataclass
class UnitPriceSeries:
    hs_code: str
    period: str
    unit: str | None = None
    avg_price: Decimal | None = None       # 美元/计量单位
    months_consistent: int = 0
    note: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def compute_unit_price(
    records: Iterable[ExportRecord],
    hs_code: str,
    period: str,
    min_months: int | None = None,
) -> UnitPriceSeries:
    """需求 7.3：均价 = 期间总额 / 期间总数量；仅当连续 N 月计量单位一致时计算。"""
    min_months = min_months or settings.UNIT_PRICE_MIN_MONTHS
    recs = [r for r in records if (r.quantity or ZERO) > 0 and r.quantity_unit]
    result = UnitPriceSeries(hs_code=hs_code, period=period)
    if not recs:
        result.note = "无数量/单位数据，无法计算均价"
        return result
    units = {r.quantity_unit for r in recs}
    if len(units) > 1:
        result.note = f"计量单位不统一（{sorted(units)}），跳过均价计算"
        return result
    # 检查连续性
    months_with_qty = sorted({r.period for r in recs})
    if len(months_with_qty) < min_months:
        result.note = f"仅 {len(months_with_qty)} 个月有数量数据，不足 {min_months} 个月"
        return result
    total_amt = sum(r.amount_usd or ZERO for r in recs)
    total_qty = sum(r.quantity or ZERO for r in recs)
    if total_qty <= 0:
        result.note = "总数量为零"
        return result
    result.unit = next(iter(units))
    result.avg_price = (total_amt / total_qty).quantize(Decimal("0.0001"))
    result.months_consistent = len(months_with_qty)
    return result


# ---- 国家/市场雷达得分 ----------------------------------------------------

def market_score(
    current: HSPeriodAggregate,
    prev_year: HSPeriodAggregate | None,
    share_weight: float = 0.40,
    growth_weight: float = 0.35,
    scale_weight: float = 0.25,
    top_n: int = 30,
) -> list[dict]:
    """需求 7.4.2：国家机会度评分。

    返回 top_n 个国家（按规模降序）的雷达六维得分。得分做 0-100 线性归一，
    便于前端同口径绘图；缺失值计 0 而不是丢弃。
    """
    if current.world_amount <= 0:
        return []
    items = sorted(current.partner_amounts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    countries = [c for c, _ in items]

    def _norm(values: dict[str, float], reverse: bool = True) -> dict[str, float]:
        if not values:
            return {}
        vs = list(values.values())
        lo, hi = min(vs), max(vs)
        if hi == lo:
            return {k: 50.0 for k in values}
        span = (hi - lo) or 1
        out = {}
        for k, v in values.items():
            t = (v - lo) / span
            out[k] = (100.0 - t * 100.0) if reverse else (t * 100.0)
        return out

    share_v = {c: float(amt) / float(current.world_amount) * 100.0 for c, amt in items}
    share_s = _norm(share_v, reverse=False)

    yoy_v: dict[str, float] = {}
    for c in countries:
        cur = current.partner_amounts.get(c, ZERO)
        prv = prev_year.partner_amounts.get(c, ZERO) if prev_year else ZERO
        g = safe_pct(cur, prv)
        if g is not None:
            yoy_v[c] = g
    yoy_s = _norm(yoy_v, reverse=False) if yoy_v else {c: 0.0 for c in countries}

    scale_s = _norm({c: float(amt) for c, amt in items}, reverse=False)

    rows: list[dict] = []
    for c, amt in items:
        sh = share_s.get(c, 0.0)
        yo = yoy_s.get(c, 0.0)
        sc = scale_s.get(c, 0.0)
        composite = sh * share_weight + yo * growth_weight + sc * scale_weight
        rows.append({
            "country": c,
            "amount_usd": float(amt),
            "share_pct": round(float(amt) / float(current.world_amount) * 100.0, 2),
            "yoy_pct": round(yoy_v.get(c, 0.0), 2),
            "score_share": round(sh, 1),
            "score_growth": round(yo, 1),
            "score_scale": round(sc, 1),
            "score_composite": round(composite, 1),
        })
    return rows


def rank_changes(
    current: HSPeriodAggregate,
    previous: HSPeriodAggregate | None,
    top_n: int = 15,
) -> list[dict]:
    """需求 7.4.3：排名升降榜。变化 = 上报告期排名 - 本报告期排名，正=上升。"""
    if previous is None:
        return []
    cur_sorted = [c for c, _ in sorted(current.partner_amounts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]]
    prev_sorted = [c for c, _ in sorted(previous.partner_amounts.items(), key=lambda kv: kv[1], reverse=True)[:top_n]]
    prev_rank = {c: i + 1 for i, c in enumerate(prev_sorted)}
    rows = []
    for i, c in enumerate(cur_sorted, start=1):
        old = prev_rank.get(c)
        rows.append({
            "country": c,
            "current_rank": i,
            "previous_rank": old,
            "change": (old - i) if old else None,
            "amount_usd": float(current.partner_amounts[c]),
        })
    return rows


def growth_distribution(
    current: HSPeriodAggregate,
    prev_year: HSPeriodAggregate | None,
) -> dict:
    """需求 7.3：增长市场 vs 下滑市场分桶。"""
    if prev_year is None:
        return {"up": [], "down": [], "flat": []}
    up, down, flat = [], [], []
    for c, amt in current.partner_amounts.items():
        g = safe_pct(amt, prev_year.partner_amounts.get(c, ZERO))
        if g is None:
            continue
        entry = {"country": c, "amount_usd": float(amt), "yoy_pct": round(g, 2)}
        if g >= settings.NEWS_GROWTH_UP_THRESHOLD:
            up.append(entry)
        elif g <= settings.NEWS_GROWTH_DOWN_THRESHOLD:
            down.append(entry)
        else:
            flat.append(entry)
    for lst in (up, down, flat):
        lst.sort(key=lambda x: x["amount_usd"], reverse=True)
    return {
        "up": up,
        "down": down,
        "flat": flat,
        "up_count": len(up),
        "down_count": len(down),
        "flat_count": len(flat),
    }
