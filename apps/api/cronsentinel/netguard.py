"""R18 — outbound request guard for tenant-supplied URLs (SSRF).

Every URL a tenant controls — webhook channels, Slack/Teams/Discord incoming-webhook URLs, signal
destinations — is fetched from inside the cluster by the notifier, the outbound exporter, or (for
"send test") the API itself. Before R18 nothing checked where those URLs pointed, so a tenant could
aim one at the cloud metadata service (169.254.169.254), Keycloak's admin API, Redis-over-HTTP, or
any other internal service, and the platform would POST to it with its own network position.

Two layers:

1. `check_url()` at configuration time — clear 400 for obviously bad targets (scheme, literal
   private IPs, names that resolve only to private space).
2. `post()` at send time — the check that actually matters. It resolves the name itself, rejects
   the request if any usable address is not public, and then connects to *that validated address*.
   Checking at config time alone is useless against DNS rebinding: the name can resolve publicly
   when saved and to 169.254.169.254 when sent. TLS still verifies against the original hostname,
   because httpcore passes the URL host as server_hostname to start_tls.

Redirects are not followed (a public URL could 302 to the metadata service), and proxy environment
variables are ignored (the guard would otherwise validate the proxy, not the target).

Policy: `OUTBOUND_ALLOW_PRIVATE=true` disables the private-address check for single-tenant
self-hosted installs, where webhooks to internal services are the normal case. Default is false —
the multi-tenant SaaS posture. Scheme, redirect and header rules apply in both modes.

NOTE: relies on httpcore's `ConnectionPool._network_backend` (private attribute, httpcore 1.0.x).
tests/test_netguard.py fails loudly if that hook stops being honoured, rather than the guard
silently becoming a no-op.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpcore
import httpx

from .config import settings

# Headers a tenant must not set on outbound requests: they alter routing/framing, or unlock cloud
# metadata endpoints that deliberately require them (GCP/Azure) — the difference between a blind
# SSRF and a credential leak.
FORBIDDEN_HEADERS = {
    "host", "content-length", "transfer-encoding", "connection", "upgrade", "te", "trailer",
    "proxy-authorization", "proxy-connection", "keep-alive",
    "metadata-flavor", "metadata", "x-aws-ec2-metadata-token", "x-aws-ec2-metadata-token-ttl-seconds",
    "x-google-metadata-request",
}

_CGNAT = ipaddress.ip_network("100.64.0.0/10")  # not is_private in Python's table, but never public


class BlockedDestination(ValueError):
    """The URL points somewhere the platform will not send tenant traffic."""


def _public(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped  # ::ffff:169.254.169.254 is still the metadata service
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified)


def _allowed(ip: ipaddress._BaseAddress) -> bool:
    return settings.outbound_allow_private or _public(ip)


def _resolve(host: str, port: int) -> list[str]:
    try:
        return list(dict.fromkeys(ai[4][0] for ai in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except socket.gaierror as e:
        raise BlockedDestination(f"cannot resolve {host}") from e


def check_headers(headers: dict | None) -> None:
    bad = sorted(h for h in (headers or {}) if h.lower() in FORBIDDEN_HEADERS)
    if bad:
        raise BlockedDestination(f"headers not allowed on outbound requests: {bad}")


def check_url(url: str, *, resolve: bool = True) -> None:
    """Configuration-time validation. Raises BlockedDestination with a message safe to return."""
    u = urlparse(url)
    if u.scheme not in ("http", "https"):
        raise BlockedDestination("URL must be http or https")
    if not u.hostname:
        raise BlockedDestination("URL has no host")
    try:
        literal = ipaddress.ip_address(u.hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _allowed(literal):
            raise BlockedDestination("URL points at a private, loopback or link-local address")
        return
    if u.hostname.lower() in ("localhost", "localhost.localdomain") and not settings.outbound_allow_private:
        raise BlockedDestination("URL points at localhost")
    if resolve:
        addrs = _resolve(u.hostname, u.port or (443 if u.scheme == "https" else 80))
        if not any(_allowed(ipaddress.ip_address(a)) for a in addrs):
            raise BlockedDestination("URL resolves only to private, loopback or link-local addresses")


def check_nats_url(url: str) -> None:
    """R30: the tenant's own NATS server. Same policy as check_url (no private/loopback/link-local
    targets unless OUTBOUND_ALLOW_PRIVATE), adapted for the nats/tls scheme and NATS's default port
    4222 instead of 80/443. Configuration-time only — the actual connect goes through
    resolve_nats_host() below for the DNS-rebinding-safe step, the same split check_url/client() use."""
    u = urlparse(url)
    if u.scheme not in ("nats", "tls"):
        raise BlockedDestination("NATS URL must use the nats or tls scheme")
    if not u.hostname:
        raise BlockedDestination("URL has no host")
    try:
        literal = ipaddress.ip_address(u.hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _allowed(literal):
            raise BlockedDestination("URL points at a private, loopback or link-local address")
        return
    if u.hostname.lower() in ("localhost", "localhost.localdomain") and not settings.outbound_allow_private:
        raise BlockedDestination("URL points at localhost")
    addrs = _resolve(u.hostname, u.port or 4222)
    if not any(_allowed(ipaddress.ip_address(a)) for a in addrs):
        raise BlockedDestination("URL resolves only to private, loopback or link-local addresses")


def resolve_nats_host(url: str) -> str:
    """R30: connect-time step for a NATS destination — resolve now, right before connecting, and
    hand nats.py the validated address instead of the original hostname. Same DNS-rebinding defence
    as _GuardedBackend gives the HTTP path: validating a hostname and then letting the client re-
    resolve it at connect time leaves a window where the name can change in between."""
    u = urlparse(url)
    port = u.port or 4222
    addrs = [a for a in _resolve(u.hostname, port) if _allowed(ipaddress.ip_address(a))]
    if not addrs:
        raise BlockedDestination(f"{u.hostname} has no permitted address")
    userinfo = f"{u.username}:{u.password}@" if u.username else ""
    return u._replace(netloc=f"{userinfo}{addrs[0]}:{port}").geturl()


class _GuardedBackend(httpcore.SyncBackend):
    """Resolve, validate, then connect to the validated address — the DNS-rebinding-safe step."""

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addrs = [a for a in _resolve(host, port) if _allowed(ipaddress.ip_address(a))]
        if not addrs:
            raise httpcore.ConnectError(f"blocked: {host} has no permitted address")
        return super().connect_tcp(addrs[0], port, timeout=timeout, local_address=local_address, socket_options=socket_options)


def client(timeout: float = 10, verify=True) -> httpx.Client:
    transport = httpx.HTTPTransport(retries=0, verify=verify)
    transport._pool._network_backend = _GuardedBackend()  # see module NOTE
    return httpx.Client(transport=transport, follow_redirects=False, trust_env=False, timeout=timeout)


def post(url: str, *, headers: dict | None = None, timeout: float = 10, **kw) -> httpx.Response:
    """The only way tenant-supplied URLs should be fetched."""
    check_url(url, resolve=False)  # scheme + literal IPs; resolution is enforced at connect
    check_headers(headers)
    with client(timeout) as c:
        return c.post(url, headers=headers, **kw)


def describe_failure(exc: Exception) -> str:
    """A category, never the raw exception: its text leaks internal addresses and lets a caller
    tell open from closed from filtered ports."""
    if isinstance(exc, BlockedDestination) or "blocked:" in str(exc):
        return "destination not allowed"
    if isinstance(exc, httpx.HTTPStatusError):
        # The destination answered: its status code is the tenant's own endpoint talking, safe to show.
        return f"HTTP {exc.response.status_code} from destination"
    if isinstance(exc, httpx.TimeoutException):
        return "timed out"
    if isinstance(exc, httpx.HTTPError):
        return "connection failed"
    return "request failed"


def tenant_error(exc: Exception) -> str:
    """What to store where a tenant can read it (delivery log, ledger, disabled_reason).
    Network-layer failures become categories; our own RuntimeErrors ("nats destination not
    implemented") are written by us and stay as they are."""
    if isinstance(exc, (httpx.HTTPError, httpcore.ConnectError, BlockedDestination)) or "blocked:" in str(exc):
        return describe_failure(exc)
    return str(exc)[:200]
