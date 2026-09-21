"""R16 — what the Copilot is allowed to send, and to where.

The default Copilot backend is a self-hosted OpenAI-compatible server (vLLM / Ollama), and for that
case the telemetry context is fine to send as-is (secrets are still redacted — see context.py).
Nothing stopped an operator pointing COPILOT_BASE_URL at a public API, though, and then every
hostname, pod, node, IP and incident signal in the context left the building. That is the data
boundary this product is built around, so it is enforced here rather than left to configuration
discipline:

  * An endpoint is "internal" if its host is loopback, an RFC 1918 / ULA literal, a single-label
    name (a Kubernetes Service like `vllm`), or ends in a configured internal suffix
    (.svc, .cluster.local, .internal, .local, .lan by default).
  * External endpoints are REFUSED unless COPILOT_ALLOW_EXTERNAL=true.
  * When external is allowed, topology is pseudonymised before sending (host-1, pod-2, 10.x → ip-1)
    and mapped back in the answer, so the model can still correlate "the same host" across jobs
    without ever seeing its name.

Deliberately NOT done: DNS resolution to decide internal/external. Resolving at request time is
slow, and a name that resolves to a private address today can be repointed; the decision is made
on the configured name alone, and anything ambiguous counts as external.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

from ..config import settings


def is_internal(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
        # link-local is excluded on purpose: 169.254.169.254 is the cloud metadata service.
        return (ip.is_loopback or ip.is_private) and not ip.is_link_local
    except ValueError:
        pass
    if host == "localhost" or "." not in host:
        return True
    suffixes = [s.strip().lower() for s in settings.copilot_internal_suffixes.split(",") if s.strip()]
    return any(host == s.lstrip(".") or host.endswith(s if s.startswith(".") else "." + s) for s in suffixes)


class EgressRefused(Exception):
    pass


def check() -> bool:
    """Returns True when the endpoint is external (so the caller must pseudonymise).
    Raises EgressRefused when it is external and not explicitly allowed."""
    external = not is_internal(settings.copilot_base_url)
    if external and not settings.copilot_allow_external:
        raise EgressRefused(
            "Copilot endpoint is outside the private network and COPILOT_ALLOW_EXTERNAL is not set. "
            "Job telemetry (hostnames, pods, logs) will not be sent to a third party by default."
        )
    return external


# ---------------------------------------------------------------------------
# Pseudonymisation — only applied for external endpoints.
# ---------------------------------------------------------------------------

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# Keys whose values are topology names. Matched anywhere in the context tree.
_TOPOLOGY_KEYS = {"host": "host", "hostname": "host", "pod": "pod", "node": "node", "object_name": "k8s-obj", "cluster": "cluster"}


class Pseudonymiser:
    def __init__(self) -> None:
        self.forward: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    def _token(self, kind: str, value: str) -> str:
        if value not in self.forward:
            self.counters[kind] = self.counters.get(kind, 0) + 1
            self.forward[value] = f"{kind}-{self.counters[kind]}"
        return self.forward[value]

    def _walk(self, node, key: str | None = None):
        if isinstance(node, dict):
            return {k: self._walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [self._walk(v, key) for v in node]
        if isinstance(node, str):
            if key in _TOPOLOGY_KEYS and node:
                return self._token(_TOPOLOGY_KEYS[key], node)
            # Known names can also appear inside free text (stderr, failure reasons, commands).
            out = _IPV4.sub(lambda m: self._token("ip", m.group(0)), node)
            for real, tok in sorted(self.forward.items(), key=lambda kv: -len(kv[0])):
                if len(real) >= 3:
                    out = out.replace(real, tok)
            return out
        return node

    def apply(self, ctx: dict) -> dict:
        # Two passes: the first collects names from topology keys, the second also replaces those
        # names wherever they occur in free text elsewhere in the tree.
        self._walk(ctx)
        return self._walk(ctx)

    def restore(self, node):
        """Map tokens in the model's answer back to real names for the (authorised) user."""
        reverse = {v: k for k, v in self.forward.items()}
        if not reverse:
            return node
        pat = re.compile("|".join(re.escape(t) for t in sorted(reverse, key=len, reverse=True)))
        def fix(x):
            if isinstance(x, str): return pat.sub(lambda m: reverse[m.group(0)], x)
            if isinstance(x, list): return [fix(v) for v in x]
            if isinstance(x, dict): return {k: fix(v) for k, v in x.items()}
            return x
        return fix(node)
