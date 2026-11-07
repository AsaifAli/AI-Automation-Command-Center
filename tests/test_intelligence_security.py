
import logging
import socket
from contextlib import contextmanager

import pytest

from app.core.config import Settings
from app.integrations import intelligence as intelligence_module
from app.integrations.intelligence import IntelligenceSource, UnsafeFeedURL


def make_source():
    return IntelligenceSource(
        Settings(
            demo_mode=True,
            intelligence_timeout_seconds=2,
            max_intelligence_items=25,
        )
    )


def test_rejects_private_and_loopback_ip_literals():
    source = make_source()

    urls = [
        "http://127.0.0.1/feed.xml",
        "http://10.0.0.5/feed.xml",
        "http://192.168.1.10/feed.xml",
        "http://[::1]/feed.xml",
        "http://169.254.169.254/latest/meta-data/",
    ]

    for url in urls:
        with pytest.raises(UnsafeFeedURL):
            source._validate_source_url(url)


def test_rejects_invalid_schemes_credentials_and_ports():
    source = make_source()

    urls = [
        "file:///etc/passwd",
        "ftp://example.com/feed.xml",
        "https://user:password@example.com/feed.xml",
        "https://example.com:8443/feed.xml",
        "http://localhost/feed.xml",
        "http://service.internal/feed.xml",
    ]

    for url in urls:
        with pytest.raises(UnsafeFeedURL):
            source._validate_source_url(url)


def test_rejects_hostname_resolving_to_private_ip(monkeypatch):
    source = make_source()

    def private_dns(*args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("10.20.30.40", 443),
            )
        ]

    monkeypatch.setattr(
        intelligence_module.socket,
        "getaddrinfo",
        private_dns,
    )

    with pytest.raises(UnsafeFeedURL):
        source._validate_source_url(
            "https://feeds.example.com/rss.xml"
        )


def test_rejects_mixed_public_and_private_dns_answers(monkeypatch):
    source = make_source()

    def mixed_dns(*args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            ),
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("127.0.0.1", 443),
            ),
        ]

    monkeypatch.setattr(
        intelligence_module.socket,
        "getaddrinfo",
        mixed_dns,
    )

    with pytest.raises(UnsafeFeedURL):
        source._validate_source_url(
            "https://feeds.example.com/rss.xml"
        )


def test_redirects_are_not_followed_and_urls_are_not_logged(
    monkeypatch,
    caplog,
):
    source = make_source()

    requested_url = (
        "https://feeds.example.com/rss.xml?token=secret-value"
    )
    client_options = {}

    def public_dns(*args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            )
        ]

    class FakeResponse:
        status_code = 302
        headers = {
            "location": "http://127.0.0.1/private"
        }

        def raise_for_status(self):
            raise AssertionError(
                "Redirect should be rejected before status validation"
            )

        def iter_bytes(self):
            raise AssertionError(
                "Redirect response body should not be read"
            )

    class FakeClient:
        def __init__(self, **kwargs):
            client_options.update(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        @contextmanager
        def stream(self, *args, **kwargs):
            yield FakeResponse()

    monkeypatch.setattr(
        intelligence_module.socket,
        "getaddrinfo",
        public_dns,
    )
    monkeypatch.setattr(
        intelligence_module.httpx,
        "Client",
        FakeClient,
    )

    with caplog.at_level(logging.WARNING):
        result = source.collect([
            {
                "name": "Test feed",
                "url": requested_url,
            }
        ])

    assert result == []
    assert client_options["follow_redirects"] is False
    assert client_options["trust_env"] is False
    assert "secret-value" not in caplog.text
    assert requested_url not in caplog.text
    assert "UnsafeFeedURL" in caplog.text


def test_rejects_oversized_response(monkeypatch, caplog):
    source = make_source()

    def public_dns(*args, **kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", 443),
            )
        ]

    class FakeResponse:
        status_code = 200
        headers = {"content-length": "1000001"}

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            raise AssertionError(
                "Oversized body should not be read"
            )

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        @contextmanager
        def stream(self, *args, **kwargs):
            yield FakeResponse()

    monkeypatch.setattr(
        intelligence_module.socket,
        "getaddrinfo",
        public_dns,
    )
    monkeypatch.setattr(
        intelligence_module.httpx,
        "Client",
        FakeClient,
    )

    with caplog.at_level(logging.WARNING):
        result = source.collect([
            {
                "name": "Test feed",
                "url": "https://feeds.example.com/rss.xml",
            }
        ])

    assert result == []
    assert "FeedTooLarge" in caplog.text


def test_atom_entry_uses_link_href_attribute():
    feed = b'''<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>Example update</title>
        <link href="https://example.com/article/1" />
        <updated>2026-11-06T10:00:00Z</updated>
      </entry>
    </feed>'''

    items = IntelligenceSource._parse_feed(
        feed,
        "Example Feed",
        "https://example.com/feed.xml",
    )

    assert len(items) == 1
    assert items[0]["title"] == "Example update"
    assert items[0]["url"] == "https://example.com/article/1"
    assert items[0]["published"] == "2026-11-06T10:00:00Z"
