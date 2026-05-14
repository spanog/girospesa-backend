from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_url: str
    supabase_service_role_key: str
    supabase_jwt_secret: str
    database_url: str = ""
    db_dsn: str = ""
    admin_email: str = ""
    admin_password: str = ""

    llm_provider: str = "gemini"
    google_api_key: str = ""
    gemini_model: str = "gemma-4-31b-it"
    geocoding_provider: str = "nominatim"

    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_mailto: str = "mailto:admin@girospesa.it"

    # Shared secret for the Supabase Database Webhook → /push/notify-favorites
    webhook_secret: str = ""

    frontend_url: str = "http://localhost:3000"

    # Product fuzzy deduplication thresholds (pre-upsert similarity check)
    product_name_similarity_threshold: float = 0.85   # rapidfuzz partial_ratio / 100
    product_brand_similarity_threshold: float = 0.90  # rapidfuzz ratio / 100 (after diacritic normalization)



settings = Settings()  # type: ignore[call-arg]
