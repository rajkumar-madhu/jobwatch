from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://cronsentinel:cronsentinel@localhost:5432/cronsentinel"
    redis_url: str = "redis://localhost:6379/0"
    nats_url: str = "nats://localhost:4222"
    nats_stream: str = "CS_EXEC"
    keycloak_issuer: str = "http://localhost:8080/realms/cronsentinel"
    keycloak_audience: str = "cronsentinel-web"
    keycloak_client_id: str = "cronsentinel-web"
    keycloak_client_secret: str = ""
    api_public_url: str = "http://localhost:8000"
    web_public_url: str = "http://localhost:3000"
    session_ttl_s: int = 12 * 3600
    cookie_secure: bool = False
    copilot_provider: str = "openai"          # openai-compatible (vLLM/Ollama) | anthropic
    copilot_base_url: str = ""                # e.g. http://vllm:8000 or http://ollama:11434
    copilot_model: str = "qwen2.5:14b"
    copilot_api_key: str = "none"
    copilot_timeout_s: int = 60
    copilot_max_context_chars: int = 60_000
    copilot_rate_per_min: int = 20
    # R16 data boundary: an endpoint outside the private network is refused unless this is set, and
    # when it is set, hostnames/pods/nodes/IPs are pseudonymised before leaving. See copilot/egress.py.
    copilot_allow_external: bool = False
    # R18 SSRF guard: false = tenant webhooks may only reach public addresses (multi-tenant SaaS).
    # Set true for single-tenant self-hosted installs that post to internal services. netguard.py.
    outbound_allow_private: bool = False
    copilot_internal_suffixes: str = ".svc,.cluster.local,.internal,.local,.lan"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    secret_encryption_key: str = "change-me"
    ingest_rate_per_min: int = 600
    api_rate_per_min: int = 1200
    reconciler_interval_s: int = 30
    max_log_bytes_per_execution: int = 256 * 1024
    log_level: str = "INFO"


settings = Settings()
