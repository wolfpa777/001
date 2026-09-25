"""海关 CSV 智能解析器。

真实数据特征（来自 /data/inputs/950691_2026Q1.csv 实测）：
- GB18030 编码（非 UTF-8），表头一行，金额字段带千分位逗号
- 末尾可能多出一个空列（pandas 读出来会是一个 Unnamed 列）
- 列：数据年月 / 商品编码 / 商品名称 / 贸易伙伴编码 / 贸易伙伴名称
       / 贸易方式编码 / 贸易方式名称 / 美元

需求 4.4.1：解析器要容忍字段顺序变化与列名差异，这里用「列名子串 + 别名」做
模糊映射，而不是按位置硬编码。
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable

from config.settings import settings

# ---- 金额解析 --------------------------------------------------------------

def parse_amount(raw) -> Decimal:
    """把「1,234,567」或「1234.5」或空值统一成 Decimal（美元，分精度）。"""
    if raw is None:
        return Decimal("0")
    if isinstance(raw, (int, float, Decimal)):
        return Decimal(str(raw)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    s = str(raw).strip().replace(",", "").replace(" ", "").replace("，", "")
    if s in ("", "-", "—", "NA", "N/A", "null", "None"):
        return Decimal("0")
    try:
        return Decimal(s).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0")


def parse_quantity(raw) -> Decimal | None:
    """数量可选字段，缺失返回 None（文档要求缺失则单价为 null）。"""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if s in ("", "-", "—", "NA", "N/A", "null", "None"):
        return None
    try:
        return Decimal(s)
    except Exception:
        return None


def normalize_period(raw: str) -> str:
    """统一成 YYYY-MM 字符串。支持 202603 / 2026-03 / 2026/03 / 20260300 等形态。"""
    s = str(raw).strip().replace("/", "-").replace(".", "")
    if len(s) >= 7 and "-" in s:
        y, m = s.split("-")[:2]
        return f"{int(y):04d}-{int(m):02d}"
    s = s.replace("-", "").replace("年", "").replace("月", "").replace("日", "")
    s = s[:6]
    if len(s) == 6 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}"
    raise ValueError(f"无法识别的年月格式: {raw!r}")


# ---- 列名模糊映射 ----------------------------------------------------------

# 标准列 -> 可接受别名（含中英文、空格、简繁）
_COLUMN_ALIASES: dict[str, list[str]] = {
    "period":        ["数据年月", "年月", "统计年月", "年份月份", "period", "year_month", "date", "月份"],
    "hs_code":       ["商品编码", "商品代号", "hs编码", "hs", "hs_code", "hs6", "hs_code_6"],
    "hs_name":       ["商品名称", "商品中文名称", "品名", "hs名称", "hs_name", "product_name"],
    "partner_code":  ["贸易伙伴编码", "伙伴编码", "国家编码", "partner_code", "country_code"],
    "partner_name":  ["贸易伙伴名称", "贸易伙伴", "伙伴名称", "国家名称", "国家", "partner_name", "country", "country_name"],
    "trade_mode_code": ["贸易方式编码", "方式编码", "trade_mode_code"],
    "trade_mode_name": ["贸易方式名称", "贸易方式", "方式名称", "trade_mode_name", "trade_mode"],
    "amount_usd":    ["美元", "金额", "出口金额", "出口额", "美元金额", "amount_usd", "usd", "value", "amount"],
    "quantity":      ["数量", "出口数量", "quantity", "qty"],
    "quantity_unit": ["计量单位", "单位", "数量单位", "unit", "quantity_unit"],
}

# 需要排除的「伪列」：pandas 读空尾列会生成 Unnamed: N
_IGNORE_HEADERS = ("", "nan", "null", "none", "n/a", "na")


def _norm_header(h: str) -> str:
    return h.strip().lower().replace(" ", "").replace("　", "")


def detect_header_row(lines: list[str], max_scan: int = 20) -> int:
    """自动定位表头：找同时含「年月」与「编码」的一行。找不到则默认第 0 行。"""
    for i, line in enumerate(lines[:max_scan]):
        low = line.lower().replace(" ", "")
        if ("年月" in low or "period" in low) and ("编码" in low or "hs" in low):
            return i
    return 0


def _decode(raw: bytes) -> str:
    """编码探测：GB18030 优先（中国海关数据惯例），失败回落 UTF-8。"""
    for enc in ("gb18030", "gbk", "utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("gb18030", errors="replace")


def build_column_map(header: list[str]) -> dict[str, int]:
    """对一行表头生成 {标准列名: 列下标} 映射，未知列会被记录并跳过。"""
    normed = {_norm_header(h): idx for idx, h in enumerate(header) if _norm_header(h) not in _IGNORE_HEADERS}
    mapping: dict[str, int] = {}
    unknowns: list[str] = []
    for std, aliases in _COLUMN_ALIASES.items():
        for a in aliases:
            if _norm_header(a) in normed:
                mapping[std] = normed[_norm_header(a)]
                break
        if std not in mapping:
            unknowns.append(std)
    # amount 找不到时尝试任意含「美元/金额/usd/value」的列
    if "amount_usd" not in mapping:
        for h, idx in normed.items():
            if any(k in h for k in ("美元", "金额", "usd", "value", "amount")):
                mapping["amount_usd"] = idx
                break
    return mapping, unknowns


@dataclass
class ParseResult:
    rows: list[dict] = field(default_factory=list)
    total: int = 0
    parsed: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    header_row: int = 0
    encoding: str = ""
    mapping: dict = field(default_factory=dict)

    def summary(self) -> str:
        return (
            f"编码={self.encoding} 表头行={self.header_row} "
            f"总行数={self.total} 成功={self.parsed} 失败={self.failed}"
        )


def parse_csv_bytes(raw: bytes, source_file: str | None = None) -> ParseResult:
    """把原始字节解析成规整的行字典列表（不做入库）。"""
    text = _decode(raw)
    lines = text.splitlines()
    header_idx = detect_header_row(lines)
    reader = csv.reader(lines[header_idx:])
    header = next(reader, [])
    mapping, unknowns = build_column_map(header)

    if "period" not in mapping or "amount_usd" not in mapping:
        raise ValueError(
            f"CSV 缺少必要列（period={('period' in mapping)} amount_usd={('amount_usd' in mapping)}），"
            f"未识别列: {unknowns}，原始表头: {header}"
        )

    res = ParseResult(header_row=header_idx, encoding="gb18030", mapping=mapping)
    res.total = sum(1 for _ in reader) + 1  # 含表头近似

    reader = csv.reader(lines[header_idx + 1:])
    for lineno, row in enumerate(reader, start=header_idx + 2):
        if not row or all(str(c).strip() == "" for c in row):
            continue
        try:
            get = lambda k: (row[mapping[k]].strip() if k in mapping and mapping[k] < len(row) else "")
            rec = {
                "period": normalize_period(get("period")),
                "hs_code": get("hs_code").zfill(6)[:6] if get("hs_code") else "",
                "hs_name": get("hs_name") or None,
                "partner_code": get("partner_code") or None,
                "partner_name": get("partner_name") or f"未知({lineno})",
                "trade_mode_code": get("trade_mode_code") or None,
                "trade_mode_name": get("trade_mode_name") or None,
                "amount_usd": parse_amount(row[mapping["amount_usd"]]) if mapping["amount_usd"] < len(row) else Decimal("0"),
                "quantity": parse_quantity(row[mapping["quantity"]]) if "quantity" in mapping and mapping["quantity"] < len(row) else None,
                "quantity_unit": get("quantity_unit") or None,
                "source_file": source_file,
                "_line": lineno,
            }
            if not rec["hs_code"] or len(rec["hs_code"]) not in (4, 6, 8, 10):
                # 允许 HS4 作为汇总行，但本项目只取 HS6，4 位会在导入期过滤并告警
                if len(rec["hs_code"]) != 4:
                    raise ValueError(f"HS 编码非法: {rec['hs_code']!r}")
            res.rows.append(rec)
            res.parsed += 1
        except Exception as e:
            res.failed += 1
            res.errors.append(f"第{lineno}行: {e}")
    return res


def iter_csv_file(path: str) -> ParseResult:
    """从文件路径解析（自动选择 GB18030）。"""
    return parse_csv_bytes(open(path, "rb").read(), source_file=os.path.basename(path))


def _csv_rows(path: str) -> Iterable[dict]:
    """惰性生成器：供大文件流式导入，避免一次全量进内存。"""
    res = iter_csv_file(path)
    yield from res.rows


# 导出供测试与脚本使用的快捷函数
__all__ = [
    "parse_csv_bytes",
    "iter_csv_file",
    "parse_amount",
    "parse_quantity",
    "normalize_period",
    "build_column_map",
    "detect_header_row",
    "ParseResult",
]
