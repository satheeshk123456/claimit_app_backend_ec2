from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    mongodb_url: str
    database_name: str = "claimit_db"
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    refresh_token_expire_days: int = 30
    static_otp: str = "123456"
    # upload_dir removed — Vercel has no persistent filesystem.
    max_file_size_mb: int = 10

    # Twilio SMS (OTP delivery)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_phone_number: str = ""
    twilio_default_country_code: str = "+91"

    # SMTP email (OTP fallback)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_name: str = "Claimit"

    class Config:
        # On Vercel, all vars come from the dashboard (env vars).
        # Locally, a .env file in this folder is also read.
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
