"""Network helpers for S20 hardening: the client address behind trusted reverse proxies (login
rate limit) and the webhook target check (no requests to internal addresses: SSRF)."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from functools import cache
from urllib.parse import urlsplit

from fastapi import Request

from app.config import settings

Network = ipaddress.IPv4Network | ipaddress.IPv6Network
Address = ipaddress.IPv4Address | ipaddress.IPv6Address


def parse_networks(spec: str) -> tuple[Network, ...]:
    """`10.0.0.1, 172.16.0.0/12` -> networks; a bare address is a /32 (/128)."""
    return tuple(
        ipaddress.ip_network(p.strip(), strict=False) for p in spec.split(",") if p.strip()
    )


@cache
def _trusted(spec: str) -> tuple[Network, ...]:
    return parse_networks(spec)


def _address(value: str) -> Address | None:
    try:
        return ipaddress.ip_address(value.strip().strip("[]"))
    except ValueError:
        return None


def _is_trusted(value: str, trusted: Iterable[Network]) -> bool:
    ip = _address(value)
    return ip is not None and any(ip in net for net in trusted)


def client_ip(peer: str, forwarded_for: str | None, trusted: Iterable[Network]) -> str:
    """The client as the nearest trusted proxy saw it. Only when the TCP peer is a trusted
    proxy is X-Forwarded-For read, right to left, skipping further trusted proxies: entries
    left of the first untrusted one were written by the client and are never believed."""
    trusted = tuple(trusted)
    if not forwarded_for or not _is_trusted(peer, trusted):
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _is_trusted(hop, trusted):
            return hop
    return hops[0] if hops else peer


def request_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    return client_ip(
        peer, request.headers.get("x-forwarded-for"), _trusted(settings.trusted_proxies)
    )


# --- webhook targets (SSRF) --------------------------------------------------------------------


def is_public(ip: Address) -> bool:
    """False for private, loopback, link-local (incl. cloud metadata 169.254.169.254),
    multicast, reserved and unspecified addresses, and IPv4 mapped into IPv6 from those."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


INTERNAL_NAMES = ("localhost", "localhost.localdomain")


def literal_target_refusal(url: str) -> str | None:
    """Why `url` may not be a webhook target without a DNS lookup, or None: a host that is an
    internal IP literal or a localhost name."""
    host = (urlsplit(url).hostname or "").rstrip(".").lower()
    if host in INTERNAL_NAMES or host.endswith(".localhost"):
        return f"{host} is a local address."
    ip = _address(host)
    if ip is not None and not is_public(ip):
        return f"{host} is a private, loopback or link-local address."
    return None


Resolver = Callable[[str, int], Awaitable[list[str]]]


async def resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to (empty if it does not resolve)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return [str(info[4][0]) for info in infos]


async def target_refusal(url: str, resolver: Resolver | None = None) -> str | None:
    """Why a webhook may not be POSTed to `url` now, or None. Checks the literal host, then
    every address it resolves to; a name that does not resolve is left to the HTTP client
    (it cannot connect either). A name re-pointed between this check and the connection (DNS
    rebinding) is not caught; redirects are never followed."""
    if refusal := literal_target_refusal(url):
        return refusal
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    for value in await (resolver or resolve)(host, port):
        ip = _address(value.split("%")[0])
        if ip is not None and not is_public(ip):
            return f"{host} resolves to {ip}, a private, loopback or link-local address."
    return None


def private_targets_allowed() -> bool:
    """Only a dev hub with WEBHOOK_ALLOW_PRIVATE_TARGETS=true may call internal addresses."""
    return settings.is_dev and settings.webhook_allow_private_targets
