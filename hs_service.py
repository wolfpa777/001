"""品类映射与 HS 主数据服务。

需求 7.1.1 / 7.1.2：
- 用户口语词（如「健身器材」）必须映射到 HS6 编码，检索词只使用 HS 标准品名 + concrete 词汇
- 系统提供 10 个高频品类默认模板，未收录的品类允许管理员在后台新增映射
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from config.settings import settings

# ---- 品类映射（默认 10 个高频品类，见 config/hs_table.json） ------------------

@dataclass
class Category:
    key: str
    display_name: str
    hs6_codes: list[str]
    concrete_terms_cn: list[str]
    concrete_terms_en: list[str]
    exclude_terms: list[str]
    default_countries: list[str]

    def search_query_cn(self) -> str:
        """用于 GDELT 等中文新闻源的检索词：标准品名优先，concrete 词汇兜底。"""
        terms = [t for t in self.concrete_terms_cn if t]
        return " OR ".join(f'"{t}"' for t in terms) if terms else self.display_name

    def search_query_en(self) -> str:
        terms = [t for t in self.concrete_terms_en if t]
        return " OR ".join(f'"{t}"' for t in terms) if terms else self.display_name

    def all_search_terms(self) -> list[str]:
        return list(dict.fromkeys(self.concrete_terms_cn + self.concrete_terms_en))


def load_categories() -> list[Category]:
    path = settings.HS_TABLE_PATH
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Category(**c) for c in data.get("categories", [])]


_CATEGORIES: list[Category] | None = None


def get_categories(force: bool = False) -> list[Category]:
    global _CATEGORIES
    if _CATEGORIES is None or force:
        _CATEGORIES = load_categories()
    return _CATEGORIES


def find_category(key_or_name: str) -> Category | None:
    """按 key 或 display_name 精确查找。"""
    s = (key_or_name or "").strip()
    for c in get_categories():
        if c.key == s or c.display_name == s:
            return c
    return None


def match_category(text: str) -> Category | None:
    """从用户自由输入中匹配品类（支持口语）。命中关键词越多越优先。"""
    text = (text or "").strip().lower()
    if not text:
        return None
    best: Category | None = None
    best_score = 0
    for c in get_categories():
        score = 0
        for kw in (c.display_name, c.key, *c.concrete_terms_cn, *c.concrete_terms_en):
            if kw and kw.lower() in text:
                score += 2 if len(kw) >= 3 else 1
        if score > best_score:
            best_score, best = score, c
    return best if best_score > 0 else None


def search_categories(keyword: str) -> list[Category]:
    if not keyword:
        return get_categories()
    kw = keyword.lower()
    return [c for c in get_categories() if kw in c.display_name.lower() or kw in c.key.lower()]


# ---- HS6 主数据（海关统计商品目录） -----------------------------------------

@dataclass
class HSCode:
    code: str
    name: str
    en_name: str | None
    unit: str | None
    chapter: str | None
    note: str | None


def load_hs_master() -> list[HSCode]:
    path = settings.HS_MASTER_PATH
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [HSCode(**i) for i in data.get("items", [])]


def hs_master_lookup(code: str) -> HSCode | None:
    code = (code or "").strip()
    for item in load_hs_master():
        if item.code == code:
            return item
    return None


def hs_name(code: str) -> str | None:
    item = hs_master_lookup(code)
    return item.name if item else None


# ---- 校验：HS 编码必须 6 位且为数字 -----------------------------------------

_HS6_RE = re.compile(r"^\d{6}$")


def validate_hs6(code: str) -> str | None:
    """返回 None 表示合法，否则返回错误说明。"""
    if not code:
        return "HS 编码不能为空"
    s = code.strip()
    if not _HS6_RE.match(s):
        return f"HS 编码必须为 6 位数字，当前值：{code!r}"
    return None
