from pathlib import Path
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    website_port: int = 8090
    base_url: AnyHttpUrl = "http://localhost:8090"
    secret_key: str
    admin_email: str
    admin_password: str
    allowed_hosts: str = "*"
    session_cookie_secure: bool = False
    github_url: str = "https://github.com/antybubbs/kaya"
    demo_url: str = "/demo"
    kaya_version: str = "v0.19.1"
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
