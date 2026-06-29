"""
Security module with rate limiting, CSRF protection, and 2FA support.
"""
import hmac
import hashlib
import base64
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict
from threading import Lock
import pyotp
import qrcode
from io import BytesIO

# Rate limiting - simple in-memory store (consider using Redis for production)
rate_limit_store = defaultdict(list)
rate_limit_lock = Lock()

MAX_LOGIN_ATTEMPTS = 5
RATE_LIMIT_WINDOW = 300  # 5 minutes
BAN_DURATION = 900  # 15 minutes


def get_client_ip(request) -> str:
    """Extract real client IP from request, considering proxies."""
    if x_forwarded_for := request.headers.get("X-Forwarded-For"):
        return x_forwarded_for.split(",")[0].strip()
    if x_real_ip := request.headers.get("X-Real-IP"):
        return x_real_ip
    return request.client.host if request.client else "unknown"


def check_rate_limit(client_id: str, action: str = "login") -> tuple[bool, str]:
    """
    Check if client has exceeded rate limits.
    Returns (allowed, message)
    """
    with rate_limit_lock:
        key = f"{action}:{client_id}"
        now = time.time()
        
        # Clean old attempts
        if key in rate_limit_store:
            rate_limit_store[key] = [
                ts for ts in rate_limit_store[key] 
                if now - ts < RATE_LIMIT_WINDOW + BAN_DURATION
            ]
        
        attempts = rate_limit_store[key]
        
        # Check if currently banned
        if len(attempts) > 0:
            oldest = attempts[0]
            if now - oldest < BAN_DURATION and len(attempts) >= MAX_LOGIN_ATTEMPTS:
                return False, f"Too many attempts. Try again in {int(BAN_DURATION - (now - oldest))} seconds."
        
        # Check if within window
        recent_attempts = len([ts for ts in attempts if now - ts < RATE_LIMIT_WINDOW])
        if recent_attempts >= MAX_LOGIN_ATTEMPTS:
            return False, f"Too many attempts. Try again later."
        
        return True, ""


def record_login_attempt(client_id: str):
    """Record a failed login attempt."""
    with rate_limit_lock:
        key = f"login:{client_id}"
        rate_limit_store[key].append(time.time())


def clear_login_attempts(client_id: str):
    """Clear login attempts after successful login."""
    with rate_limit_lock:
        key = f"login:{client_id}"
        rate_limit_store[key] = []


def generate_csrf_token() -> str:
    """Generate a CSRF token."""
    return base64.b64encode(os.urandom(32)).decode()


def verify_csrf_token(token: str, session_token: str) -> bool:
    """Verify CSRF token matches session token."""
    if not token or not session_token:
        return False
    return hmac.compare_digest(token, session_token)


class TOTP2FA:
    """Two-Factor Authentication using Time-based One-Time Passwords (TOTP)."""
    
    @staticmethod
    def generate_secret() -> str:
        """Generate a new 2FA secret."""
        return pyotp.random_base32()
    
    @staticmethod
    def get_totp(secret: str) -> pyotp.TOTP:
        """Get TOTP instance from secret."""
        return pyotp.TOTP(secret)
    
    @staticmethod
    def verify_token(secret: str, token: str, window: int = 1) -> bool:
        """Verify a TOTP token. window=1 allows ±1 time period for clock drift."""
        if not secret or not token:
            return False
        try:
            totp = pyotp.TOTP(secret)
            return totp.verify(token, valid_window=window)
        except Exception:
            return False
    
    @staticmethod
    def get_provisioning_uri(secret: str, name: str, issuer: str = "Kaya") -> str:
        """Get provisioning URI for QR code generation."""
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(name=name, issuer_name=issuer)
    
    @staticmethod
    def get_qr_code(provisioning_uri: str) -> bytes:
        """Generate QR code as PNG bytes."""
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(provisioning_uri)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = BytesIO()
        img.save(buffer)
        buffer.seek(0)
        return buffer.getvalue()
    
    @staticmethod
    def get_backup_codes(count: int = 10) -> list[str]:
        """Generate backup codes for account recovery."""
        return [base64.b32encode(os.urandom(6)).decode()[:8] for _ in range(count)]


class PasswordValidator:
    """Validate password strength."""
    
    MIN_LENGTH = 12
    REQUIRE_UPPERCASE = True
    REQUIRE_NUMBERS = True
    REQUIRE_SPECIAL = True
    
    @staticmethod
    def validate(password: str) -> tuple[bool, str]:
        """
        Validate password strength.
        Returns (is_valid, message)
        """
        if len(password) < PasswordValidator.MIN_LENGTH:
            return False, f"Password must be at least {PasswordValidator.MIN_LENGTH} characters long."
        
        if PasswordValidator.REQUIRE_UPPERCASE and not any(c.isupper() for c in password):
            return False, "Password must contain at least one uppercase letter."
        
        if PasswordValidator.REQUIRE_NUMBERS and not any(c.isdigit() for c in password):
            return False, "Password must contain at least one number."
        
        if PasswordValidator.REQUIRE_SPECIAL and not any(c in "!@#$%^&*()_+-=[]{}|;:,.<>?" for c in password):
            return False, "Password must contain at least one special character."
        
        return True, ""


def sanitize_filename(filename: str) -> str:
    """Remove dangerous characters from filename."""
    import re
    # Allow only alphanumeric, dash, underscore, and dot
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "-", filename)
    # Remove leading/trailing dots and dashes
    sanitized = sanitized.strip(".-")
    return sanitized or "file"
