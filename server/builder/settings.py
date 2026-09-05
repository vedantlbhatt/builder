from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration, read once.

    Note APP_DATABASE_URL is separate from DATABASE_URL and is NOT optional in
    production. Railway's DATABASE_URL is a superuser role, and superusers bypass row
    level security unconditionally — every policy in the schema becomes a silent no-op,
    and the isolation tests still pass because they run as the same superuser. The API
    must connect as `builder_app`, which is NOSUPERUSER NOBYPASSRLS. See boot.py, which
    refuses to start otherwise.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Connection used by the API for all request handling. Must be a NOBYPASSRLS role.
    app_database_url: str = "postgresql+psycopg://builder_app@localhost/builder"

    # Owner connection, used only by migrations.
    database_url: str = "postgresql+psycopg://postgres@localhost/builder"

    # Background jobs that legitimately need to see every row.
    worker_database_url: str = ""

    redis_url: str = ""

    # Public base URL. Baked into share links and the privacy page's verification command.
    base_url: str = "http://localhost:8000"

    # Sign in with Apple. The App IDs must be GROUPED under the primary in the SIWA pane,
    # or Apple scopes `sub` per App ID and the same human gets two accounts.
    apple_team_id: str = ""
    apple_primary_bundle_id: str = "com.vedantlbhatt.Builder"
    apple_service_id: str = ""
    apple_key_id: str = ""
    apple_private_key: str = ""

    # Google Sign-In. Comma-separated because the iOS, Android and web OAuth clients are
    # DISTINCT client ids and each one is the `aud` of the tokens it issues; a single value
    # would 401 every platform but one.
    google_client_ids: str = ""

    # Ed25519 signing keys for access tokens. Two of them, always: rotation with a single
    # key 401s every in-flight token at the moment of the swap.
    jwt_private_key: str = ""
    jwt_private_key_next: str = ""
    access_token_ttl_seconds: int = 900

    # APNs, for session-complete pushes to the phone.
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_private_key: str = ""
    apns_topic: str = "com.vedantlbhatt.Builder"
    apns_use_sandbox: bool = True

    environment: str = "development"
    posthog_api_key: str = ""
    sentry_dsn: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def google_client_id_list(self) -> list[str]:
        return [c.strip() for c in self.google_client_ids.split(",") if c.strip()]


@lru_cache
def settings() -> Settings:
    return Settings()
