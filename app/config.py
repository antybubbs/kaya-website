from pathlib import Path
import secrets
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


def load_or_create_secret_key(secret_file: Path) -> str:
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    if secret_file.exists():
        value = secret_file.read_text(encoding="utf-8").strip()
        if value:
            return value
    value = secrets.token_urlsafe(48)
    secret_file.write_text(value, encoding="utf-8")
    return value


class Settings(BaseSettings):
    website_port: int = 8090
    base_url: AnyHttpUrl = "http://localhost:8090"
    secret_key_file: Path = BASE_DIR / "data" / ".session_secret"
    secret_key: str = load_or_create_secret_key(secret_key_file)
    admin_email: str = ""
    admin_password: str = ""
    allowed_hosts: str = "*"
    session_cookie_secure: bool = False
    github_url: str = "https://github.com/antybubbs/kaya"
    website_github_url: str = "https://github.com/antybubbs/kaya-website"
    demo_url: str = "https://demo.kaya-app.uk/"
    kaya_version: str = "v0.19.1"
    github_api_timeout_seconds: int = 4
    github_version_cache_seconds: int = 900
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'website.db'}"
    uploads_dir: Path = BASE_DIR / "uploads"
    
    # Security settings
    enable_2fa: bool = True
    require_2fa: bool = False  # Force 2FA for all admins
    max_login_attempts: int = 5
    login_attempt_window: int = 300  # seconds
    account_lockout_duration: int = 900  # seconds
    session_timeout: int = 3600  # seconds (1 hour)
    cors_origins: str = "*"  # Restrict in production
    
    class Config:
        env_file = BASE_DIR / ".env"
        env_file_encoding = "utf-8"


settings = Settings()
