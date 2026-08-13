import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)


class IntelligenceSource:
    """Optional RSS/Atom adapter used by competitor intelligence."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def collect(self, sources: list[dict[str, str]]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for source in sources[:10]:
            url = source.get("url", "").strip()
            if not url:
                continue
            try:
                response = httpx.get(url, timeout=self.settings.intelligence_timeout_seconds, follow_redirects=True)
                response.raise_for_status()
                items.extend(self._parse_feed(response.text, source.get("name", url), url))
            except Exception as exc:
                logger.warning("intelligence_source_failed source=%s error=%s", url, exc)
        return items[: self.settings.max_intelligence_items]

    @staticmethod
    def _parse_feed(body: str, source_name: str, url: str) -> list[dict[str, Any]]:
        root = ET.fromstring(body)
        results = []
        for item in root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            title = item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or "Untitled"
            link = item.findtext("link") or url
            if link is None:
                atom_link = item.find("{http://www.w3.org/2005/Atom}link")
                link = atom_link.attrib.get("href", url) if atom_link is not None else url
            published = item.findtext("pubDate") or item.findtext("{http://www.w3.org/2005/Atom}updated")
            results.append(
                {
                    "source": source_name,
                    "title": title.strip(),
                    "url": link,
                    "published": published,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        return results
