"""R18 — SSRF guard for tenant-supplied URLs."""
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

import httpx
import pytest

from cronsentinel import netguard
from cronsentinel.config import settings


@pytest.fixture(autouse=True)
def saas_mode(monkeypatch):
    monkeypatch.setattr(settings, "outbound_allow_private", False)


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",     # AWS/GCP/Azure metadata
    "http://[::ffff:169.254.169.254]/",             # same, IPv4-mapped
    "http://127.0.0.1:6379/", "http://[::1]/", "http://0.0.0.0/",
    "http://10.0.0.5/", "http://172.16.3.4/", "http://192.168.1.1/", "http://[fd00::1]/",
    "http://100.64.1.1/",                           # CGNAT — not in Python's is_private table
    "http://localhost:8080/",
    "file:///etc/passwd", "gopher://x/", "ftp://example.com/",
])
def test_config_time_rejects(url):
    with pytest.raises(netguard.BlockedDestination):
        netguard.check_url(url, resolve=False)


def test_config_time_rejects_names_that_resolve_only_to_private_space(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("10.1.2.3", 443))])
    with pytest.raises(netguard.BlockedDestination, match="resolves only"):
        netguard.check_url("https://hooks.innocent-looking.example/x")


def test_config_time_accepts_public(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 443))])
    netguard.check_url("https://hooks.example.com/services/T0/B0/xyz")
    netguard.check_url("https://93.184.216.34/hook", resolve=False)


# --- R30: check_nats_url / resolve_nats_host — same policy as check_url, adapted to nats/tls and
# port 4222. Not parametrized off test_config_time_rejects: that list mixes in http-only schemes
# (file://, gopher://, ftp://) that check_nats_url would reject for a different reason (wrong
# scheme, not address class), so asserting the SAME exception type there wouldn't prove much.

@pytest.mark.parametrize("url", [
    "nats://169.254.169.254:4222/",
    "nats://127.0.0.1:4222/", "tls://[::1]:4222/",
    "nats://10.0.0.5:4222/", "nats://192.168.1.1:4222/",
    "nats://localhost:4222/",
    "http://example.com:4222/", "ftp://example.com/",   # right host, wrong scheme
])
def test_nats_config_time_rejects(url):
    with pytest.raises(netguard.BlockedDestination):
        netguard.check_nats_url(url)


def test_nats_config_time_rejects_names_that_resolve_only_to_private_space(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("10.1.2.3", 4222))])
    with pytest.raises(netguard.BlockedDestination, match="resolves only"):
        netguard.check_nats_url("nats://broker.innocent-looking.example/")


def test_nats_config_time_accepts_public(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 4222))])
    netguard.check_nats_url("nats://broker.example.com:4222/")


def test_nats_config_time_defaults_the_port_to_4222(monkeypatch):
    seen = {}
    def fake_getaddrinfo(host, port, **k):
        seen["port"] = port
        return [(0, 0, 0, "", ("93.184.216.34", port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    netguard.check_nats_url("nats://broker.example.com/")   # no :port in the URL
    assert seen["port"] == 4222


def test_resolve_nats_host_swaps_in_the_validated_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 4222))])
    resolved = netguard.resolve_nats_host("nats://broker.example.com:4222/")
    assert "93.184.216.34" in resolved and "broker.example.com" not in resolved


def test_resolve_nats_host_brackets_an_ipv6_address(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("2606:4700:4700::1111", 4222))])
    resolved = netguard.resolve_nats_host("nats://broker.example.com:4222/events")
    parsed = urlparse(resolved)
    assert parsed.hostname == "2606:4700:4700::1111"
    assert parsed.port == 4222
    assert parsed.path == "/events"


def test_resolve_nats_host_does_not_invent_a_password(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 4222))])
    resolved = netguard.resolve_nats_host("nats://token@broker.example.com:4222/")
    parsed = urlparse(resolved)
    assert parsed.username == "token"
    assert parsed.password is None
    assert parsed.hostname == "93.184.216.34"


def test_resolve_nats_host_keeps_reserved_characters_in_the_password(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("93.184.216.34", 4222))])
    resolved = netguard.resolve_nats_host("nats://user:p%40ss:word@broker.example.com:4222/events")
    parsed = urlparse(resolved)
    assert parsed.username == "user"
    assert "%3A" in resolved
    assert parsed.hostname == "93.184.216.34"
    assert parsed.port == 4222
    assert parsed.path == "/events"


def test_resolve_nats_host_blocks_a_name_that_resolves_only_to_private_space(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("10.1.2.3", 4222))])
    with pytest.raises(netguard.BlockedDestination):
        netguard.resolve_nats_host("nats://broker.innocent-looking.example:4222/")


def test_self_hosted_mode_allows_a_private_nats_target(monkeypatch):
    monkeypatch.setattr(settings, "outbound_allow_private", True)
    netguard.check_nats_url("nats://10.0.0.5:4222/")
    netguard.check_nats_url("nats://localhost:4222/")


@pytest.mark.parametrize("header", ["Host", "metadata-flavor", "Metadata", "X-aws-ec2-metadata-token", "Transfer-Encoding"])
def test_dangerous_headers_rejected(header):
    with pytest.raises(netguard.BlockedDestination):
        netguard.check_headers({header: "x"})


def test_ordinary_headers_allowed():
    netguard.check_headers({"Authorization": "Bearer t", "X-Team": "sre"})


# --- send time: the guard must hold at connect, not just at validation ------------------------

class _Echo(BaseHTTPRequestHandler):
    hits = 0
    def do_POST(self):  # noqa: N802
        _Echo.hits += 1
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
    def log_message(self, *a): pass


@pytest.fixture
def local_server():
    srv = HTTPServer(("127.0.0.1", 0), _Echo)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Echo.hits = 0
    yield srv.server_port
    srv.shutdown()


def test_send_time_blocks_a_name_that_rebinds_to_loopback(local_server, monkeypatch):
    """DNS rebinding: the name is fine when saved, then resolves to an internal address when sent.
    check_url at save time cannot catch this; only the connect-time check can."""
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", local_server))]
                        if host == "rebind.example" else real(host, *a, **k))
    with pytest.raises(httpx.ConnectError, match="blocked"):
        netguard.post(f"http://rebind.example:{local_server}/", content=b"{}")
    assert _Echo.hits == 0, "request reached the internal server"


def test_guard_hook_is_actually_installed(local_server):
    """If a future httpx/httpcore stops honouring ConnectionPool._network_backend, the guard would
    silently become a no-op. This fails instead: a plain name resolving to loopback must be blocked
    by the transport itself, with no help from check_url."""
    with netguard.client() as c:
        with pytest.raises(httpx.ConnectError, match="blocked"):
            c.post(f"http://localhost:{local_server}/", content=b"{}")
    assert _Echo.hits == 0


def test_self_hosted_mode_allows_internal(local_server, monkeypatch):
    monkeypatch.setattr(settings, "outbound_allow_private", True)
    r = netguard.post(f"http://127.0.0.1:{local_server}/", content=b"{}")
    assert r.status_code == 200 and _Echo.hits == 1


def test_redirects_are_not_followed(monkeypatch):
    """A public endpoint answering 302 → metadata service must not be chased."""
    class _Redirect(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.send_response(302); self.send_header("Location", "http://169.254.169.254/"); self.end_headers()
        def log_message(self, *a): pass
    srv = HTTPServer(("127.0.0.1", 0), _Redirect)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(settings, "outbound_allow_private", True)   # let us reach the local redirector
    try:
        r = netguard.post(f"http://127.0.0.1:{srv.server_port}/", content=b"{}")
        assert r.status_code == 302
    finally:
        srv.shutdown()


def test_failure_descriptions_leak_nothing():
    msgs = {netguard.describe_failure(e) for e in (
        httpx.ConnectError("[Errno 111] Connection refused to 10.0.3.7:6379"),
        httpx.ConnectTimeout("timed out connecting to 10.0.3.7"),
        netguard.BlockedDestination("URL points at a private address"),
    )}
    assert msgs == {"connection failed", "timed out", "destination not allowed"}
    assert all("10.0" not in m for m in msgs)
    assert netguard.tenant_error(RuntimeError("nats destination not implemented")) == "nats destination not implemented"


def test_tls_verifies_against_the_hostname_not_the_ip(tmp_path, monkeypatch):
    """The guard connects to the validated IP; certificate verification and SNI must still use the
    URL's hostname, or every HTTPS webhook would fail (or worse, verification would be skipped)."""
    import ssl, subprocess
    key, crt = tmp_path / "k.pem", tmp_path / "c.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", key, "-out", crt, "-days", "1",
                    "-subj", "/CN=hooks.public.example", "-addext", "subjectAltName=DNS:hooks.public.example"],
                   check=True, capture_output=True)
    srv = HTTPServer(("127.0.0.1", 0), _Echo)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(crt, key)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_port
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]
                        if host in ("hooks.public.example", "wrong-name.example") else real(host, *a, **k))
    monkeypatch.setattr(settings, "outbound_allow_private", True)   # the test server is on loopback
    trust = ssl.create_default_context(cafile=str(crt))
    try:
        with netguard.client(verify=trust) as c:
            assert c.post(f"https://hooks.public.example:{port}/", content=b"{}").status_code == 200
            # Same IP, different name: must fail verification — proves the name, not the IP, is checked.
            with pytest.raises(httpx.ConnectError, match="(?i)certificate|hostname"):
                c.post(f"https://wrong-name.example:{port}/", content=b"{}")
    finally:
        srv.shutdown()
