"""Network helpers for S20 hardening: the client address behind trusted reverse proxies (login
rate limit) and the webhook target check (no requests to internal addresses: SSRF)."""

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from functools import cache
from urllib.parse import urlsplit

import httpx
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


def forwarded_for(request: Request) -> str | None:
    """Every X-Forwarded-For line, joined in order: a client that sends its own extra line
    must not be able to hide the hops the proxy appended in another (RFC 9110 §5.3)."""
    lines = request.headers.getlist("x-forwarded-for")
    return ",".join(lines) if lines else None


def request_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    return client_ip(peer, forwarded_for(request), _trusted(settings.trusted_proxies))


# --- webhook targets (SSRF) --------------------------------------------------------------------


# NAT64 prefixes: a NAT64 gateway turns 64:ff9b::a00:1 into 10.0.0.1, so they can reach any
# IPv4 address, internal ones included. Python counts the well-known prefix as global.
NAT64 = (
    ipaddress.IPv6Network("64:ff9b::/96"),  # RFC 6052, well-known prefix
    ipaddress.IPv6Network("64:ff9b:1::/48"),  # RFC 8215, local-use prefix
)


def is_public(ip: Address) -> bool:
    """False for private, loopback, link-local (incl. cloud metadata 169.254.169.254),
    multicast, reserved and unspecified addresses, IPv4 mapped into IPv6 from those, and the
    NAT64 prefixes."""
    if isinstance(ip, ipaddress.IPv6Address):
        if any(ip in net for net in NAT64):
            return False
        if ip.ipv4_mapped is not None:
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


@dataclass(frozen=True)
class Target:
    """A webhook target checked once: `ip` is the address the request must connect to, or
    `refusal` says why it may not be called."""

    ip: Address | None = None
    refusal: str | None = None


async def check_target(url: str, resolver: Resolver | None = None) -> Target:
    """Resolve `url`'s host once and check every address it resolves to; the delivery then
    connects to the checked address (`pin`), so a name re-pointed after the check (DNS
    rebinding) is never looked up again. Redirects are never followed."""
    if refusal := literal_target_refusal(url):
        return Target(refusal=refusal)
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    ips: list[Address] = []
    for value in await (resolver or resolve)(host, port):
        ip = _address(value.split("%")[0])
        if ip is None:
            continue
        if not is_public(ip):
            return Target(
                refusal=f"{host} resolves to {ip}, a private, loopback or link-local address."
            )
        ips.append(ip)
    if not ips:
        return Target(refusal=f"{host} does not resolve.")
    return Target(ip=ips[0])


async def target_refusal(url: str, resolver: Resolver | None = None) -> str | None:
    """Why a webhook may not be POSTed to `url` now, or None."""
    return (await check_target(url, resolver)).refusal


def pin(request: httpx.Request, ip: Address) -> httpx.Request:
    """`request` sent to `ip` instead of a fresh lookup of its host: the URL carries the
    checked address, while the Host header (set from the original URL) and the TLS SNI and
    certificate check (`sni_hostname`) keep the original name. `Connection: close`, so a
    pooled connection made for one name is never reused for another name on the same IP."""
    host = request.url.raw_host.decode("ascii")  # IDNA-encoded, as TLS expects
    request.url = request.url.copy_with(host=str(ip))
    request.extensions = {**request.extensions, "sni_hostname": host}
    request.headers["Connection"] = "close"
    return request


def private_targets_allowed() -> bool:
    """Only a dev hub with WEBHOOK_ALLOW_PRIVATE_TARGETS=true may call internal addresses."""
    return settings.is_dev and settings.webhook_allow_private_targets
