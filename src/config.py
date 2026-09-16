from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """Application settings and environment configuration."""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Server Settings
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Twilio Telephony Credentials
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_PHONE_NUMBER: Optional[str] = None

    # AI Model Keys
    GEMINI_API_KEY: Optional[str] = None
    DEEPGRAM_API_KEY: Optional[str] = None
    ELEVENLABS_API_KEY: Optional[str] = None
    ELEVENLABS_VOICE_ID: str = "21m00Tcm4TlvDq8ikWAM"  # Default clean voice
    ELEVENLABS_MODEL_ID: str = "eleven_turbo_v2"

    # Latency & Streaming Settings
    MAX_LATENCY_THRESHOLD_MS: int = 1000
    VAD_SILENCE_TIMEOUT_MS: int = 400
    MANDATORY_DISCLOSURE: str = (
        "Hi, thank you for calling. I am Vani, an automated AI assistant. "
        "How can I help you today?"
    )

    # Telephony Audio Constants (Twilio Media Streams)
    TWILIO_SAMPLE_RATE: int = 8000
    TWILIO_CHANNELS: int = 1

    # Telegram Admin Bot Settings
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_RATE_LIMIT_CALLS: int = 10
    TELEGRAM_RATE_LIMIT_WINDOW_SEC: int = 60
    DEFAULT_BUSINESS_ID: str = "biz_default"
    DEFAULT_ESCALATION_NUMBER: Optional[str] = None

    # Database & Multi-Tenant Vector Settings
    DATABASE_URL: str = "sqlite+aiosqlite:///./vani.db"
    MAX_CHUNKS_PER_BUSINESS: int = 10000
    EMBEDDING_DIMENSION: int = 768


settings = Settings()
