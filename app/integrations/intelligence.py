
import ipaddress
import logging
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import Settings

logger = logging.getLogger(__name__)

MAX_FEED_BYTES = 1_000_000
MAX_URL_LENGTH = 2_048
ATOM_NS = "{http://www.w3.org/2005/Atom}"

BLOCKED_HOST_SUFFIXES = (
    ".localhost", ".local", ".internal", ".test",
    ".invalid", ".lan", ".home.arpa", ".onion",
)


class UnsafeFeedURL(ValueError):
    """Raised when a feed URL is not safe to fetch."""


class FeedTooLarge(ValueError):
    """Raised when a feed exceeds the response-size limit."""


class IntelligenceSource:
    """RSS/Atom adapter with SSRF and response-size protections."""

    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _validate_source_url(url: str) -> None:
        if not url or len(url) > MAX_URL_LENGTH:
            raise UnsafeFeedURL("Feed URL is empty or too long")

        try:
            parsed = urlsplit(url)
            scheme = parsed.scheme.lower()
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as exc:
            raise UnsafeFeedURL("Feed URL is malformed") from exc

        if scheme not in {"http", "https"}:
            raise UnsafeFeedURL("Only HTTP and HTTPS feed URLs are allowed")

        if not hostname or parsed.username is not None or parsed.password is not None:
            raise UnsafeFeedURL("Feed URL must contain a hostname and no credentials")

        if parsed.fragment:
            raise UnsafeFeedURL("Feed URL fragments are not allowed")

        hostname = hostname.rstrip(".").lower()

        if not hostname or "%" in hostname:
            raise UnsafeFeedURL("Feed hostname is invalid")

        if hostname == "localhost" or hostname.endswith(BLOCKED_HOST_SUFFIXES):
            raise UnsafeFeedURL("Local or non-public hostnames are not allowed")

        expected_port = 443 if scheme == "https" else 80

        if port not in {None, expected_port}:
            raise UnsafeFeedURL("Only standard HTTP(S) ports are allowed")

        try:
            literal_ip = ipaddress.ip_address(hostname)
            addresses = {literal_ip}
        except ValueError:
            try:
                records = socket.getaddrinfo(
                    hostname,
                    expected_port,
                    type=socket.SOCK_STREAM,
                )
            except OSError as exc:
                raise UnsafeFeedURL("Feed hostname could not be resolved") from exc

            addresses = set()

            for record in records:
                address_text = record[4][0].split("%", 1)[0]

                try:
                    addresses.add(ipaddress.ip_address(address_text))
                except ValueError as exc:
                    raise UnsafeFeedURL(
                        "Feed hostname resolved to an invalid address"
                    ) from exc

        if not addresses:
            raise UnsafeFeedURL("Feed hostname did not resolve to an address")

        if any(not address.is_global for address in addresses):
            raise UnsafeFeedURL(
                "Feed hostname must resolve only to public IP addresses"
            )

    def _fetch_feed(self, url: str) -> bytes:
        self._validate_source_url(url)

        timeout = httpx.Timeout(
            self.settings.intelligence_timeout_seconds
        )

        with httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "GET",
                url,
                headers={
                    "Accept": (
                        "application/rss+xml, application/atom+xml, "
                        "application/xml, text/xml"
                    )
                },
            ) as response:

                if 300 <= response.status_code < 400:
                    raise UnsafeFeedURL("Feed redirects are not allowed")

                response.raise_for_status()

                content_length = response.headers.get("content-length")

                if content_length:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise ValueError(
                            "Feed response has an invalid Content-Length"
                        ) from exc

                    if declared_size > MAX_FEED_BYTES:
                        raise FeedTooLarge(
                            "Feed response exceeds the size limit"
                        )

                chunks = []
                total_bytes = 0

                for chunk in response.iter_bytes():
                    total_bytes += len(chunk)

                    if total_bytes > MAX_FEED_BYTES:
                        raise FeedTooLarge(
                            "Feed response exceeds the size limit"
                        )

                    chunks.append(chunk)

                return b"".join(chunks)

    def collect(
        self,
        sources: list[dict[str, str]],
    ) -> list[dict[str, Any]]:

        items: list[dict[str, Any]] = []

        for source in sources[:10]:
            url = source.get("url", "").strip()

            if not url:
                continue

            try:
                body = self._fetch_feed(url)
                source_name = source.get("name", "").strip() or "Feed"

                items.extend(
                    self._parse_feed(body, source_name, url)
                )

            except Exception as exc:
                # Never log URLs, query strings, credentials or response bodies.
                logger.warning(
                    "intelligence_source_failed error_type=%s",
                    type(exc).__name__,
                )

            if len(items) >= self.settings.max_intelligence_items:
                break

        return items[:self.settings.max_intelligence_items]

    @staticmethod
    def _parse_feed(
        body: bytes | str,
        source_name: str,
        url: str,
    ) -> list[dict[str, Any]]:

        root = ET.fromstring(body)

        feed_items = (
            root.findall(".//item")
            + root.findall(f".//{ATOM_NS}entry")
        )

        results = []

        for item in feed_items:
            title = (
                item.findtext("title")
                or item.findtext(f"{ATOM_NS}title")
                or "Untitled"
            )

            rss_link = item.find("link")

            if rss_link is not None and rss_link.text:
                link = rss_link.text.strip()
            else:
                atom_link = item.find(f"{ATOM_NS}link")
                link = (
                    atom_link.attrib.get("href", url)
                    if atom_link is not None
                    else url
                )

            published = (
                item.findtext("pubDate")
                or item.findtext(f"{ATOM_NS}published")
                or item.findtext(f"{ATOM_NS}updated")
            )

            results.append({
                "source": source_name,
                "title": title.strip(),
                "url": link,
                "published": published,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            })

        return results
