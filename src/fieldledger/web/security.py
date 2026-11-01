"""Pure-ASGI security middleware (docs/api-contract.md, section 2).

* Host header allow-list (DNS-rebinding defence).
* Every POST/PUT/DELETE under /api needs ``X-FieldLedger-Client: ui`` and, when an Origin header
  is present, an Origin equal to the request's own origin. CORS is not enabled.
* Upload body limits enforced while the body streams in.
* Security response headers; API responses are never cached.
"""

from __future__ import annotations

import json
from typing import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

from .apierrors import ApiError

Scope = dict
Receive = Callable[[], Awaitable[dict]]
Send = Callable[[dict], Awaitable[None]]

CLIENT_HEADER = b"x-fieldledger-client"
LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", ""})
UNSAFE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
SECURITY_HEADERS = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"content-security-policy", CSP.encode("ascii")),
)


def hostname_of(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):  # IPv6 literal, optional port
        end = host.find("]")
        return host[1:end] if end != -1 else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


async def _send_error(send: Send, err: ApiError) -> None:
    body = json.dumps(err.body()).encode("utf-8")
    await send({"type": "http.response.start", "status": err.status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"), *SECURITY_HEADERS]})
    await send({"type": "http.response.body", "body": body})


class GuardMiddleware:
    def __init__(self, app, configured_host: str = "127.0.0.1") -> None:
        self.app = app
        host = configured_host.strip().lower().strip("[]")
        self.any_host = host in WILDCARD_HOSTS
        self.allowed = set(LOOPBACK_NAMES) | {host}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        host_header = headers.get(b"host", b"").decode("latin-1")
        if not self.any_host and hostname_of(host_header) not in self.allowed:
            await _send_error(send, ApiError("forbidden", "request rejected"))
            return
        path: str = scope.get("path", "")
        if path.startswith("/api") and scope["method"] in UNSAFE_METHODS:
            if headers.get(CLIENT_HEADER) != b"ui":
                await _send_error(send, ApiError("forbidden", "request rejected"))
                return
            origin = headers.get(b"origin")
            if origin is not None:
                netloc = urlsplit(origin.decode("latin-1")).netloc.lower()
                if not netloc or netloc != host_header.strip().lower():
                    await _send_error(send, ApiError("forbidden", "request rejected"))
                    return

        is_api = path.startswith("/api")

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, _ in message.get("headers", [])}
                extra = [h for h in SECURITY_HEADERS if h[0] not in existing]
                if is_api and b"cache-control" not in existing:
                    extra.append((b"cache-control", b"no-store"))
                message = {**message, "headers": [*message.get("headers", []), *extra]}
            await send(message)

        await self.app(scope, receive, send_wrapper)


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Reject over-long upload bodies early (Content-Length) and while streaming."""

    def __init__(self, app, limits: dict[str, tuple[int, str]]) -> None:
        # limits: exact path -> (max body bytes, error code)
        self.app = app
        self.limits = limits

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "POST" or scope.get("path") not in self.limits:
            await self.app(scope, receive, send)
            return
        limit, code = self.limits[scope["path"]]
        err = ApiError(code, "the upload is larger than the allowed limit")
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _send_error(send, err)
            return
        seen = 0

        async def limited_receive() -> dict:
            nonlocal seen
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    raise _TooLarge
            return message

        started = False

        async def tracking_send(message: dict) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await _send_error(send, err)
