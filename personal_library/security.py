"""URL admission and DNS-pinned, credential-free public requests."""

import ipaddress
from dataclasses import dataclass
import re
import socket
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit

import httpx


class PipelineError(Exception):
    """Only fixed machine codes may cross the reporting boundary."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


SECRET = re.compile(
    r"token|secret|password|passwd|credential|signature|authorization|"
    r"api[-_]?key|access[-_]?key|session|jwt|^auth$|^key$|^sig$|^code$",
    re.I,
)
SECRET_VALUE = re.compile(r"^(?:Bearer\s|eyJ[\w-]+\.[\w-]+\.|sk-[A-Za-z0-9]{12,})", re.I)


def canonical_url(value: str) -> str:
    try:
        if len(value) > 8192 or re.search(r"[\x00-\x20\x7f\\]", value):
            raise ValueError()
        parts = urlsplit(value)
        host = (parts.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
        if parts.scheme.lower() not in {"http", "https"} or not host:
            raise ValueError()
        if parts.username is not None or parts.password is not None:
            raise ValueError()
        if "%" in host or host == "localhost" or host.endswith(
            (".localhost", ".local", ".internal", ".home", ".lan")
        ) or "." not in host and ":" not in host:
            raise ValueError()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError()
        # Do not reorder/re-encode meaningful queries: repeated parameters and
        # order can be significant to the publisher.
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if any(SECRET.search(unquote(key)) or SECRET_VALUE.search(value) for key, value in pairs):
            raise ValueError()
        port = parts.port
        if port is not None and port not in {80, 443}:
            raise ValueError()
        authority = f"[{host}]" if ":" in host else host
        if port and port != (443 if parts.scheme.lower() == "https" else 80):
            authority += f":{port}"
        return urlunsplit((parts.scheme.lower(), authority, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError):
        raise PipelineError("unsafe_url") from None


def public_addresses(url: str, resolver=socket.getaddrinfo) -> list[str]:
    parts = urlsplit(canonical_url(url))
    try:
        addresses = sorted({
            row[4][0] for row in resolver(
                parts.hostname, parts.port or (443 if parts.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        })
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise PipelineError("unsafe_address")
        return addresses
    except (OSError, ValueError):
        raise PipelineError("dns_failed") from None


class PublicHTTP:
    def __init__(self, *, transport=None, resolver=socket.getaddrinfo, max_bytes=8_000_000):
        self.transport = transport
        self.resolver = resolver
        self.max_bytes = max_bytes

    def validate(self, url: str) -> str:
        url = canonical_url(url)
        public_addresses(url, self.resolver)
        return url

    def get(self, url: str, *, redirects=True) -> tuple[str, str, str]:
        response = self.request(url, redirects=redirects)
        return response.text, response.headers.get("content-type", ""), response.url

    def request(self, url: str, *, redirects=True, accepted=(200,)):
        for _ in range(6):
            url = canonical_url(url)
            addresses = public_addresses(url, self.resolver)
            parts = urlsplit(url)
            ip = addresses[0]
            authority = f"[{ip}]" if ":" in ip else ip
            if parts.port:
                authority += f":{parts.port}"
            pinned = urlunsplit((parts.scheme, authority, parts.path, parts.query, ""))
            try:
                # Connect to the admitted IP, keeping TLS SNI/certificate checks
                # and Host bound to the original hostname. No ambient proxy,
                # netrc, authentication, or cookies survive between requests.
                with httpx.Client(
                    transport=self.transport, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(45, connect=15),
                ) as client:
                    with client.stream(
                        "GET", pinned,
                        headers={"Host": parts.netloc, "User-Agent": "personal-library/0.1"},
                        extensions={"sni_hostname": parts.hostname},
                    ) as response:
                        if 300 <= response.status_code < 400 and response.status_code not in accepted:
                            if not redirects or not response.headers.get("location"):
                                raise PipelineError("redirect_rejected")
                            url = canonical_url(urljoin(url, response.headers["location"]))
                            continue
                        if response.status_code not in accepted:
                            raise PipelineError(f"http_{response.status_code}")
                        chunks = []
                        size = 0
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > self.max_bytes:
                                raise PipelineError("document_too_large")
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        text = body.decode(response.encoding or "utf-8", errors="replace")
                        return PublicResponse(text, dict(response.headers), url, response.status_code)
            except httpx.HTTPError:
                raise PipelineError("fetch_transport_failed") from None
        raise PipelineError("too_many_redirects")


@dataclass
class PublicResponse:
    text: str
    headers: dict[str, str]
    url: str
    status: int
