"""新闻聚合模块：从 GDELT / NewsAPI / Bing News 抓取与品类相关的新闻。

需求 7.3：增长市场 / 下滑市场关联新闻。
本模块负责：
1. 按品类关键词构建检索式
2. 调用新闻源 API
3. 清洗、去重、按国家/地区分组
4. 兜底：数据源不可用时返回模拟数据，保证报告可生成

生产环境填入对应 API Key 即可启用真实数据。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from config.settings import settings
from core.hs_service import Category

log = logging.getLogger(__name__)

# 新闻缓存：避免重复调用 API
_CACHE_DIR = Path("/tmp/customs_news_cache")
_TTL_SECONDS = 6 * 3600  # 6 小时


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published_at: Optional[str] = None
    snippet: str = ""
    country: str = ""           # 关联国家/地区（可空）
    sentiment: str = "neutral"  # positive / neutral / negative
    _hash: str = field(default="", init=False)

    def __post_init__(self):
        h = hashlib.md5(f"{self.title}{self.url}".encode("utf-8")).hexdigest()
        self._hash = h

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published_at": self.published_at,
            "snippet": self.snippet,
            "country": self.country,
            "sentiment": self.sentiment,
        }


def _cache_get(key: str) -> Optional[list[dict]]:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _CACHE_DIR / f"{key}.json"
        if not p.exists():
            return None
        age = datetime.utcnow().timestamp() - p.stat().st_mtime
        if age > _TTL_SECONDS:
            return None
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_set(key: str, data: list[dict]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = _CACHE_DIR / f"{key}.json"
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        log.warning("新闻缓存写入失败: %s", e)


def fetch_news(category: Category, max_items: int = 20, country: Optional[str] = None) -> list[NewsItem]:
    """获取与品类相关的新闻。优先真实源，失败时兜底模拟数据。

    参数:
        category: 品类对象（含关键词、检索词）
        max_items: 最多返回条数
        country: 限定国家/地区（可选）
    返回:
        NewsItem 列表，按发布时间倒序
    """
    cache_key = f"{category.key}_{country or 'all'}_{max_items}"
    cached = _cache_get(cache_key)
    if cached:
        return [NewsItem(**d) for d in cached]

    items: list[NewsItem] = []
    if settings.GDELT_BASE_URL and settings.NEWS_FALLBACK_MOCK is False:
        items = _fetch_gdelt(category, max_items, country)

    if not items and settings.NEWSAPI_KEY:
        items = _fetch_newsapi(category, max_items, country)

    if not items and settings.BING_NEWS_KEY:
        items = _fetch_bing(category, max_items, country)

    if not items:
        # 兜底：模拟数据，保证报告流程不中断
        items = _mock_news(category, max_items)

    _cache_set(cache_key, [i.to_dict() for i in items])
    return items


def fetch_news_for_country(category: Category, country: str, max_items: int = 5) -> list[NewsItem]:
    """获取某个国家/地区 + 品类相关的增长/下滑相关新闻。"""
    return fetch_news(category, max_items=max_items, country=country)


# ============================ 数据源实现 ====================================

def _fetch_gdelt(category: Category, max_items: int, country: Optional[str]) -> list[NewsItem]:
    """GDELT Doc API：免费、无需 Key，支持中文检索。"""
    query = category.search_query_cn()
    if country:
        query = f"{query} {country}"
    params = {
        "query": query,
        "maxrecords": max_items,
        "format": "json",
        "sort": "DateDesc",
    }
    try:
        r = requests.get(settings.GDELT_BASE_URL, params=params,
                         timeout=settings.GDELT_TIMEOUT_SECONDS)
        r.raise_for_status()
        data = r.json()
        items = []
        for art in data.get("articles", []):
            items.append(NewsItem(
                title=art.get("title", ""),
                url=art.get("url", ""),
                source=art.get("source", "GDELT"),
                published_at=art.get("seendate"),
                snippet=art.get("snippet", ""),
                country=country or "",
            ))
        return items
    except Exception as e:
        log.warning("GDELT 抓取失败: %s", e)
        return []


def _fetch_newsapi(category: Category, max_items: int, country: Optional[str]) -> list[NewsItem]:
    """NewsAPI.org：需 API Key。"""
    terms = " OR ".join(f'"{t}"' for t in category.concrete_terms_en if t) or category.display_name
    if country:
        terms = f"{terms} {country}"
    params = {
        "q": terms,
        "apiKey": settings.NEWSAPI_KEY,
        "pageSize": max_items,
        "sortBy": "publishedAt",
        "language": "en",
    }
    try:
        r = requests.get("https://newsapi.org/v2/everything", params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return [NewsItem(
            title=a.get("title", ""),
            url=a.get("url", ""),
            source=a.get("source", {}).get("name", "NewsAPI"),
            published_at=a.get("publishedAt"),
            snippet=a.get("description", "") or "",
            country=country or "",
        ) for a in data.get("articles", [])]
    except Exception as e:
        log.warning("NewsAPI 抓取失败: %s", e)
        return []


def _fetch_bing(category: Category, max_items: int, country: Optional[str]) -> list[NewsItem]:
    """Bing News Search：需 API Key。"""
    terms = category.display_name
    if country:
        terms = f"{terms} {country}"
    headers = {"Ocp-Apim-Subscription-Key": settings.BING_NEWS_KEY}
    params = {"q": terms, "count": max_items, "mkt": "zh-CN"}
    try:
        r = requests.get("https://api.bing.microsoft.com/v7.0/news/search",
                         headers=headers, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        return [NewsItem(
            title=v.get("name", ""),
            url=v.get("url", ""),
            source=v.get("provider", [{}])[0].get("name", "Bing") if v.get("provider") else "Bing",
            published_at=v.get("datePublished"),
            snippet=v.get("description", "") or "",
            country=country or "",
        ) for v in data.get("value", [])]
    except Exception as e:
        log.warning("Bing News 抓取失败: %s", e)
        return []


# ============================ 兜底模拟数据 ==================================

def _mock_news(category: Category, max_items: int) -> list[NewsItem]:
    """数据源不可用时的兜底内容，结构与真实数据一致，保证报告可渲染。"""
    base = [
        ("行业整体出口延续增长态势", "中国海关数据显示，该品类近期出口保持稳健增长，主要市场表现良好。"),
        ("新兴市场成为增量重点", "东南亚、中东等新兴市场增速显著，成为出口增量的重要来源。"),
        ("欧美市场需求回暖", "欧美传统市场需求回升，订单量环比改善。"),
        ("贸易政策变动值得关注", "部分目的国调整了相关产品的进口关税与技术壁垒要求。"),
        ("供应链重构带来新机遇", "全球供应链调整背景下，中国供应链优势进一步凸显。"),
    ]
    out = []
    for i, (title, snippet) in enumerate(base[:max_items]):
        out.append(NewsItem(
            title=f"【{category.display_name}】{title}",
            url="https://example.com/mock-news",
            source="模拟数据（请配置新闻源 API Key 启用真实数据）",
            published_at=datetime.utcnow().isoformat(),
            snippet=snippet,
            country="",
            sentiment="neutral",
        ))
    return out
