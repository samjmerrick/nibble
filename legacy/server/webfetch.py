"""Fetch a public web page for Claude: its readable text, title, image and schema.org JSON-LD.

Only public http(s) addresses on standard ports are fetched, and anything that resolves to a
private, loopback or link-local address is refused (redirects included), so the tool can't be
used to reach other services on the home network.

Nibble adds site adapters for pages that need special handling (a JavaScript shell with a
JSON API behind it, say). An adapter returns the same shape as fetch_page.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Callable

MAX_BYTES = 4 * 1024 * 1024
MAX_JSONLD = 200_000  # characters of JSON-LD returned, all blobs together
USER_AGENT = "Mozilla/5.0 (compatible; personal-db/1.0)"

# (name, match(url) -> bool, fetch(url) -> result dict)
_adapters: list[tuple[str, Callable[[str], bool], Callable[[str], dict[str, Any]]]] = []


class BlockedAddress(ValueError):
    pass


def add_adapter(name: str, match: Callable[[str], bool], fetch: Callable[[str], dict[str, Any]]) -> None:
    _adapters.append((name, match, fetch))


def _check_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise BlockedAddress(f"cannot resolve {host}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global or ip.is_multicast:
            raise BlockedAddress(f"{host} is not a public address")


def check_url(url: str) -> str:
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise BlockedAddress("only public http(s) links can be fetched")
    if p.port not in (None, 80, 443):
        raise BlockedAddress("only standard ports can be fetched")
    _check_host(p.hostname)
    return url


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirect)


def get(url: str, accept: str = "text/html,application/xhtml+xml") -> tuple[str, Any, bytes]:
    """GET a public URL safely. Returns (final url, headers, body up to MAX_BYTES)."""
    check_url(url)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": accept, "Accept-Language": "en-GB,en;q=0.8"})
    with _opener.open(req, timeout=20) as resp:
        return resp.geturl(), resp.headers, resp.read(MAX_BYTES + 1)[:MAX_BYTES]


def get_json(url: str) -> Any:
    _, headers, raw = get(url, "application/json")
    return json.loads(raw.decode(headers.get_content_charset() or "utf-8", errors="replace"))


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "aside"}
    BLOCK = {"p", "li", "br", "div", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0
        self.jsonld: list[str] = []
        self._in_ld = False
        self.title = ""
        self._in_title = False
        self.og_image = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_ld = True
            self.jsonld.append("")
            return
        if tag == "meta" and a.get("property") == "og:image" and not self.og_image:
            self.og_image = a.get("content")
        if tag == "title":
            self._in_title = True
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if self._in_ld and tag == "script":
            self._in_ld = False
            return
        if tag == "title":
            self._in_title = False
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._in_ld:
            self.jsonld[-1] += data
        elif self._in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def parse_page(page: str, url: str, max_text: int = 15000) -> dict[str, Any]:
    """Readable text, title, image and JSON-LD objects from a page's HTML."""
    parser = _TextExtractor()
    parser.feed(page)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.out))
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    jsonld, size = [], 0
    for blob in parser.jsonld:
        try:
            obj = json.loads(blob.strip())
        except (ValueError, TypeError):
            continue
        size += len(blob)
        if size > MAX_JSONLD:
            break
        jsonld.append(obj)
    return {
        "url": url,
        "title": html.unescape(parser.title).strip(),
        "image": parser.og_image,
        "jsonld": jsonld,
        "text": text[:max_text],
        "text_truncated": len(text) > max_text,
    }


def fetch_page(url: str, max_text: int = 15000) -> dict[str, Any]:
    """Fetch a public page (through a site adapter when one matches)."""
    check_url(url)
    for name, match, fetch in _adapters:
        if match(url):
            out = fetch(url)
            out.setdefault("source", name)
            return out
    final_url, headers, raw = get(url)
    ctype = headers.get("Content-Type", "")
    if "html" not in ctype and "xml" not in ctype:
        raise ValueError(f"that link is not a web page ({ctype or 'unknown type'})")
    return parse_page(raw.decode(headers.get_content_charset() or "utf-8", errors="replace"), final_url, max_text)
