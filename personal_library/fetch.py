"""Reader-first extraction. Authentication and challenge pages are failures."""

from dataclasses import dataclass
from html import unescape
import json
import re
import unicodedata
from urllib.parse import quote, urljoin, urlsplit

import trafilatura

from .security import PipelineError, PublicHTTP, canonical_url


@dataclass
class Document:
    text: str
    method: str
    final_url: str
    reader_error: str | None = None


BLOCKED = re.compile(
    r"access denied|verify (?:that )?you are human|checking your browser|"
    r"just a moment|captcha|sign in to continue|log in to continue|"
    r"authentication required|page not found|404 not found|403 forbidden|"
    r"enable javascript and cookies(?: to continue)?|robot check|"
    r"请先登录|登录后查看|访问受限|人机验证",
    re.I,
)
CHALLENGE_NOTICE = re.compile(
    r"(?:please )?(?:verify|verifying) (?:that )?you are human"
    r"(?:[.! ]+this may take a few seconds)?|"
    r"checking (?:your browser|if the site connection is secure)"
    r"(?:[.! ]+this may take a few seconds)?",
    re.I,
)


def challenge_marker(value: str) -> bool:
    # Match an entire title/standalone opening notice, not a phrase mentioned
    # inside prose about captchas or access-control engineering.
    value = " ".join(unicodedata.normalize("NFKC", unescape(value)).split())
    value = value.strip(" \t.!?:;。！？：；…#*-_")
    return bool(BLOCKED.fullmatch(value) or CHALLENGE_NOTICE.fullmatch(value))


def check_content(text: str) -> None:
    envelope = text.split("Markdown Content:", 1)
    header = envelope[0] if len(envelope) == 2 else ""
    body = envelope[-1].strip()
    if len(body) < 100:
        raise PipelineError("empty_or_short_document")
    title = re.search(r"(?m)^Title:[ \t]*(.*)", header)
    first_line = body.splitlines()[0].strip("# *") if body else ""
    if (title and challenge_marker(title.group(1))) or challenge_marker(first_line):
        raise PipelineError("blocked_or_error_document")
    if re.search(r"(?im)^Warning:.*(?:error|[45]\d\d|captcha|blocked)", header):
        raise PipelineError("reader_error_document")


class Fetcher:
    def __init__(self, http: PublicHTTP | None = None):
        self.http = http or PublicHTTP()

    def fetch(self, url: str) -> Document:
        url = self.http.validate(url)
        reader_error = None
        try:
            text, mime, _ = self.http.get("https://r.jina.ai/" + url, redirects=False)
            if "text/plain" not in mime and "text/markdown" not in mime:
                raise PipelineError("reader_invalid_type")
            if "Markdown Content:" not in text:
                raise PipelineError("reader_invalid_envelope")
            check_content(text)
            # A reader response can disclose a redirect; reject private targets.
            source = re.search(r"(?m)^URL Source:\s*(\S+)", text)
            if source:
                self.http.validate(source.group(1))
            return Document(text, "jina", url)
        except PipelineError as error:
            reader_error = error.code
        try:
            html, mime, final_url = self.http.get(url)
            if "text/html" not in mime and "application/xhtml+xml" not in mime:
                raise PipelineError("direct_unsupported_type")
            title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
            if (title and challenge_marker(title.group(1))) or re.search(
                r"<input\b[^>]*\btype\s*=\s*['\"]?password", html, re.I
            ):
                raise PipelineError("blocked_or_error_document")
            text = trafilatura.extract(
                html, output_format="markdown", include_comments=False,
                include_tables=True, include_links=True,
            )
            if not text:
                raise PipelineError("extraction_empty")
            check_content(text)
            return Document(text, "direct", final_url, reader_error)
        except PipelineError as error:
            # Preserve both safe failure codes; never persist response bodies.
            raise PipelineError(f"reader:{reader_error};direct:{error.code}") from None

    def wayback(self, url: str) -> dict:
        """Explicit opt-in public submission; distinguish an existing snapshot."""
        try:
            url = self.http.validate(url)
        except PipelineError:
            return {"status": "unavailable", "error": "wayback_url_unavailable"}
        try:
            response = self.http.request(
                "https://web.archive.org/save/" + url, redirects=False,
                accepted=(200, 202, 301, 302, 303, 307, 308),
            )
            location = response.headers.get("content-location") or response.headers.get("location")
            if location:
                snapshot = self._snapshot(urljoin("https://web.archive.org", location), url)
                return {"status": "saved", "url": snapshot}
            if response.status == 202:
                return {"status": "submitted"}
        except PipelineError:
            pass
        # Failure to save must not be confused with availability of an old copy.
        try:
            text, _, _ = self.http.get(
                "https://archive.org/wayback/available?url=" + quote(url, safe=""),
                redirects=False,
            )
            closest = json.loads(text).get("archived_snapshots", {}).get("closest", {})
            if not closest.get("available") or str(closest.get("status")) != "200":
                return {"status": "unavailable"}
            snapshot = self._snapshot(closest["url"], url)
            return {"status": "existing", "url": snapshot}
        except (PipelineError, ValueError, TypeError, KeyError, AttributeError):
            return {"status": "unavailable", "error": "wayback_unavailable"}

    def _snapshot(self, value: str, original: str) -> str:
        snapshot = self.http.validate(value)
        parts = urlsplit(snapshot)
        match = re.fullmatch(r"/web/\d{14}(?:[a-z_]+)?/(https?://.+)", parts.path)
        if parts.hostname != "web.archive.org" or not match:
            raise PipelineError("invalid_snapshot")
        embedded = match.group(1) + ("?" + parts.query if parts.query else "")
        if canonical_url(embedded) != original:
            raise PipelineError("invalid_snapshot")
        return snapshot
