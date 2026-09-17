"""LLM adapter. Default: OpenAI-compatible endpoint (vLLM / Ollama / llama.cpp server) — self-hosted (D12).
Set COPILOT_BASE_URL, COPILOT_MODEL, COPILOT_API_KEY. Anthropic-compatible adapter behind COPILOT_PROVIDER=anthropic."""
import json, time

import httpx

from ..config import settings

SYSTEM = """You are CronSentinel Copilot, an SRE assistant for scheduled-job operations.
You only reason from the telemetry JSON provided. Never invent log lines, metrics or hosts. If evidence is insufficient, say so and lower confidence.
Never propose destructive commands; suggested commands are for investigation only and are never executed by you.
Respond with ONLY a JSON object with keys:
summary (string, 1-2 sentences), root_cause (string), evidence (array of short strings quoting specific numbers/lines from the telemetry),
affected_resources (array of strings), confidence (number 0-1), remediation (array of strings, ordered), relevant_logs (array of strings),
investigate_commands (array of strings), what_changed (array of strings, or empty)."""

SCHEMA_KEYS = ["summary", "root_cause", "evidence", "affected_resources", "confidence", "remediation", "relevant_logs", "investigate_commands", "what_changed"]


def _parse(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"): t = t.strip("`").split("\n", 1)[1] if "\n" in t else t
    if t.lower().startswith("json"): t = t[4:]
    try:
        d = json.loads(t[t.index("{"):t.rindex("}") + 1])
    except Exception:
        d = {"summary": text[:500], "root_cause": "unparsed model output", "confidence": 0.2}
    for k in SCHEMA_KEYS: d.setdefault(k, [] if k not in ("summary", "root_cause", "confidence") else ("" if k != "confidence" else 0.3))
    try: d["confidence"] = max(0.0, min(1.0, float(d["confidence"])))
    except Exception: d["confidence"] = 0.3
    return d


def ask(question: str, ctx: dict) -> tuple[dict, str, int]:
    user = f"Question: {question}\n\nTelemetry (JSON):\n{json.dumps(ctx)[:settings.copilot_max_context_chars]}"
    t0 = time.perf_counter()
    if settings.copilot_provider == "anthropic":
        r = httpx.post(f"{settings.copilot_base_url}/v1/messages", headers={"x-api-key": settings.copilot_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                       json={"model": settings.copilot_model, "max_tokens": 1500, "system": SYSTEM, "messages": [{"role": "user", "content": user}]}, timeout=settings.copilot_timeout_s)
        r.raise_for_status(); out = "".join(b.get("text", "") for b in r.json()["content"])
    else:  # openai-compatible
        r = httpx.post(f"{settings.copilot_base_url}/v1/chat/completions", headers={"Authorization": f"Bearer {settings.copilot_api_key}", "content-type": "application/json"},
                       json={"model": settings.copilot_model, "temperature": 0.1, "max_tokens": 1500, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]}, timeout=settings.copilot_timeout_s)
        r.raise_for_status(); out = r.json()["choices"][0]["message"]["content"]
    return _parse(out), settings.copilot_model, int((time.perf_counter() - t0) * 1000)
