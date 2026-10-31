# Copyright 2026 fanzipei. SPDX-License-Identifier: Apache-2.0
"""Bounded publisher RSS/Atom collection; news proxy settings apply to every feed."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import json
from pathlib import Path
import re
import threading
import time
import xml.etree.ElementTree as ET
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from .core import article_digest, clip_utf8, safe_web_url
from .news_network import connection_routes, proxy_handler

RSS_ID_BASE = 1 << 49
FEEDS = (
    ("rthk-world", "香港电台", "politics", "https://rthk.hk/rthk/news/rss/c_expressnews_cinternational.xml"),
    ("rthk-finance", "香港电台", "investment", "https://rthk.hk/rthk/news/rss/c_expressnews_cfinance.xml"),
    ("bbc-world", "BBC", "politics", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("guardian-world", "卫报", "politics", "https://www.theguardian.com/world/rss"),
    ("cnbc-finance", "CNBC", "investment", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("cnbc-investing", "CNBC", "investment", "https://www.cnbc.com/id/15839069/device/rss/rss.html"),
    ("fed-monetary", "美联储", "investment", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("ars", "Ars Technica", "technology", "https://feeds.arstechnica.com/arstechnica/index"),
    ("techcrunch", "TechCrunch", "technology", "https://techcrunch.com/feed/"),
    ("qbitai", "量子位", "technology", "https://www.qbitai.com/feed"),
    ("aljazeera", "Al Jazeera", "politics", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("france24", "France 24", "politics", "https://www.france24.com/en/rss"),
    ("npr-world", "NPR", "politics", "https://feeds.npr.org/1004/rss.xml"),
    ("ecb", "欧洲央行", "investment", "https://www.ecb.europa.eu/rss/press.html"),
    ("nasa", "NASA", "technology", "https://www.nasa.gov/feed/"),
    ("mit", "MIT News", "technology", "https://news.mit.edu/rss/feed"),
    ("sciencedaily", "ScienceDaily", "technology", "https://www.sciencedaily.com/rss/top/science.xml"),
)


def atomic_json(path, value):
    import os
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def timestamp(value):
    try:
        date = parsedate_to_datetime(value)
    except (ValueError, TypeError, OverflowError):
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError, AttributeError):
            return 0
    try:
        return int(date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp())
    except (ValueError, OverflowError, OSError):
        return 0


def parse_feed(raw, feed, now):
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("RSS 包含不支持的实体声明。")
    root = ET.fromstring(raw)
    atom = "{http://www.w3.org/2005/Atom}"
    items = root.findall("./channel/item") or root.findall(atom + "entry")
    key, label, category, _ = feed
    result = []
    for item in items[:60]:
        title = item.findtext("title") or item.findtext(atom + "title", "")
        title = " ".join(html.unescape(re.sub(r"<[^>]*>", " ", title)).split())
        link = item.findtext("link", "")
        if not link:
            link = next((entry.get("href", "") for entry in item.findall(atom + "link")
                         if entry.get("rel", "alternate") == "alternate"), "")
        link = safe_web_url(link.strip())
        published = timestamp(item.findtext("pubDate") or item.findtext(atom + "published")
                              or item.findtext(atom + "updated"))
        if not title or not link or (published and not now - 48 * 3600 <= published <= now + 600):
            continue
        identifier = RSS_ID_BASE + int(article_digest(link)[:12], 16)
        from urllib.parse import urlsplit
        result.append({"id": identifier, "source": "rss", "feed": key, "publisher": label,
                       "category": category, "title": clip_utf8(title, 480), "url": link,
                       "domain": clip_utf8(urlsplit(link).hostname or "", 96),
                       "time": published or int(now), "score": 0, "comments": 0})
    return result


class FeedCollector:
    def __init__(self, directory):
        self.path = Path(directory) / "news-feed-cache.json"
        self.lock = threading.Lock()
        self.cache = json.loads(self.path.read_text(encoding="utf8")) if self.path.exists() else {}

    def fetch_one(self, feed, config, now):
        key, _, _, url = feed
        cached = self.cache.get(key, {})
        headers = {"User-Agent": "DeskCompanion/3.0 (personal RSS reader)",
                   "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}
        for field, header in (("etag", "If-None-Match"), ("modified", "If-Modified-Since")):
            if cached.get(field):
                headers[header] = cached[field]
        error = None
        for route in connection_routes(config):
            try:
                opener = build_opener(proxy_handler(route, config))
                with opener.open(Request(url, headers=headers), timeout=12) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
                    if len(raw) > 2 * 1024 * 1024:
                        raise ValueError("RSS 内容过大。")
                    items = parse_feed(raw, feed, now)
                    value = {"etag": response.headers.get("ETag", ""),
                             "modified": response.headers.get("Last-Modified", ""), "items": items}
                with self.lock:
                    self.cache[key] = value
                return items, None
            except HTTPError as exc:
                status = exc.code
                exc.close()
                if status == 304:
                    return cached.get("items", []), None
                error = f"HTTP {status}"
            except Exception as exc:
                error = type(exc).__name__
        return cached.get("items", []), error

    def collect(self, config, now=None):
        now = time.time() if now is None else now
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda feed: self.fetch_one(feed, config, now), FEEDS))
        candidates, seen, status = [], set(), []
        for feed, (items, error) in zip(FEEDS, results):
            fresh = [item for item in items if now - 48 * 3600 <= item["time"] <= now + 600]
            status.append({"feed": feed[0], "items": len(fresh), "error": error})
            for item in sorted(fresh, key=lambda story: story["time"], reverse=True)[:20]:
                digest = article_digest(item["url"])
                if digest not in seen:
                    seen.add(digest)
                    candidates.append(item)
        atomic_json(self.path, self.cache)
        return candidates, status
