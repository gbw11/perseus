"""Bounded read-only adapter to the existing loopback display feed.

No listener, browser authentication policy, new collector or MQTT client here.
The phone entry point supplies its own authenticated browser session separately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import suppress
import http.client
import re

READ_PATHS = frozenset({"/display/mobile", "/api/v1/display/presentation"})
ASSET = re.compile(r"/assets/[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\.(?:js|css|png|jpg|jpeg|webp|svg|woff2?|ttf|ico)")
MAX_BODY = 2 * 1024 * 1024


class FeedUnavailable(Exception):
    """Public error contains a fixed code only, never raw upstream details."""
    def __init__(self, code="DISPLAY_FEED_UNAVAILABLE"):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FeedResponse:
    status: int
    content_type: str
    body: bytes = field(repr=False)


def allowed_path(path):
    return path in READ_PATHS or ASSET.fullmatch(path) is not None


@dataclass(frozen=True)
class LocalFeed:
    token: str = field(repr=False)
    port: int = 8082

    def __post_init__(self):
        if (not isinstance(self.token, str) or not self.token.strip() or any(c in self.token for c in "\r\n\0")
                or type(self.port) is not int or not 1 <= self.port <= 65535):
            raise ValueError("valid local feed configuration required")

    def get(self, path, *, connect=http.client.HTTPConnection):
        if not allowed_path(path):
            raise ValueError("read path is not allowed")
        # No external URL, proxy, forwarding headers, browser cookie or redirect.
        connection = None
        try:
            connection = connect("127.0.0.1", self.port, timeout=2)
            connection.request("GET", path, headers={"Authorization": f"Bearer {self.token}"})
            response = connection.getresponse()
            if response.status in (401, 403):
                raise FeedUnavailable("DISPLAY_FEED_AUTHENTICATION_FAILED")
            if response.status not in (200, 404):
                raise FeedUnavailable()
            body = response.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                raise FeedUnavailable("DISPLAY_FEED_RESPONSE_TOO_LARGE")
            content_type = response.getheader("Content-Type", "application/octet-stream")
            # No upstream Set-Cookie/Location/WWW-Authenticate or service headers.
            return FeedResponse(response.status, content_type, body)
        except FeedUnavailable:
            raise
        except Exception:
            raise FeedUnavailable() from None
        finally:
            if connection is not None:
                with suppress(Exception):
                    connection.close()
