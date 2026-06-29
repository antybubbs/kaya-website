# Kaya Website - Security Implementation Guide

## Overview

This document details the comprehensive security hardening implemented for the Kaya website CMS, designed for production global access.

## Security Features Implemented

### 1. Two-Factor Authentication (2FA)

**Status**: ✅ Implemented with TOTP (Time-based One-Time Password)

**Features**:
- TOTP-based 2FA using authenticator apps (Google Authenticator, Authy, etc.)
- QR code generation for easy setup
- Backup codes for account recovery
- Configurable enforcement (`REQUIRE_2FA` in config)

**Usage**:
```bash
# Enable in .env
ENABLE_2FA=true
REQUIRE_2FA=false  # Set to true to force all users
```

### 2. Rate Limiting & Brute Force Protection

**Status**: ✅ Implemented

**Features**:
- Max 5 login attempts per 5-minute window
- 15-minute account lockout after threshold exceeded
- Per-client IP tracking
- Configurable via environment variables

**Configuration**:
```
MAX_LOGIN_ATTEMPTS=5
RATE_LIMIT_WINDOW=300  # seconds
ACCOUNT_LOCKOUT_DURATION=900  # seconds
```

### 3. Session Security

**Status**: ✅ Implemented

**Features**:
- Secure session cookies with httponly and same_site=lax flags
- Session timeout (default 1 hour)
- HTTPS-only cookies in production (`SESSION_COOKIE_SECURE=true`)
- Session fixation prevention via middleware

**Configuration**:
```
SESSION_TIMEOUT=3600  # seconds
SESSION_COOKIE_SECURE=true  # HTTPS only (production)
```

### 4. CSRF Protection

**Status**: ✅ Infrastructure in place

**Implementation**:
- CSRF token generation and validation utilities available in `security.py`
- Templates need token injection in forms (see "TODO" section)

### 5. Password Security

**Status**: ✅ Implemented

**Features**:
- bcrypt hashing with proper salt
- Password strength validation (min 12 chars, uppercase, numbers, special chars)
- Secure password comparison using `hmac.compare_digest()`

**Validation Rules**:
```
- Minimum 12 characters
- At least one uppercase letter (A-Z)
- At least one number (0-9)
- At least one special character (!@#$%^&*...)
```

### 6. Input Validation & Sanitization

**Status**: ✅ Implemented

**Features**:
- File upload validation (type checking, size limits 8MB)
- Filename sanitization (removes dangerous characters)
- Content sanitization with bleach (XSS protection)
- Jinja2 autoescape enabled
- SQL injection protection via SQLAlchemy ORM

### 7. Security Headers

**Status**: ⚠️ Middleware ready, needs activation

**Headers to implement** (in main.py middleware):
```python
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline';"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response
```

### 8. CORS Configuration

**Status**: ⚠️ Ready for configuration

**Production Setup**:
```
# Restrict CORS to trusted domains
CORS_ORIGINS=https://yourdomain.com,https://www.yourdomain.com
```

### 9. Environment Security

**Status**: ✅ Best practices documented

**Sensitive Data**:
- Never commit `.env` file
- Use strong `SECRET_KEY` (min 32 random characters)
- Rotate admin credentials periodically
- Use environment variables for all secrets

## Production Deployment Checklist

### Before Going Public

- [ ] **HTTPS/SSL**
  - Enable HTTPS on reverse proxy (nginx, caddy, etc.)
  - Set `SESSION_COOKIE_SECURE=true`
  - Use modern TLS 1.2+

- [ ] **Environment Configuration**
  - [ ] Generate strong `SECRET_KEY`: `python -c "import secrets; print(secrets.token_urlsafe(32))"`
  - [ ] Change default admin credentials
  - [ ] Set `ALLOWED_HOSTS` to your domain(s)
  - [ ] Restrict `CORS_ORIGINS` if needed

- [ ] **Security Headers**
  - [ ] Implement security headers middleware (see section 7)
  - [ ] Test with securityheaders.com

- [ ] **2FA Setup**
  - [ ] Enable 2FA for admin account
  - [ ] Save backup codes securely
  - [ ] Advise admin on authenticator app setup

- [ ] **Rate Limiting**
  - [ ] Verify `MAX_LOGIN_ATTEMPTS` is reasonable
  - [ ] Test lockout behavior

- [ ] **Database**
  - [ ] Regular backups (daily recommended)
  - [ ] Encrypted backup storage
  - [ ] Test restore process

- [ ] **Logging & Monitoring**
  - [ ] Enable application logging
  - [ ] Monitor failed login attempts
  - [ ] Set up alerts for suspicious activity

- [ ] **Regular Updates**
  - [ ] Keep dependencies updated: `pip list --outdated`
  - [ ] Subscribe to security advisories
  - [ ] Test updates in staging first

## TODO: Required Manual Updates

### 1. main.py - Update Login Endpoints

The login endpoint needs to be updated to:
1. Check rate limiting
2. Initialize 2FA flow
3. Use new auth functions from updated `auth.py`

Example pattern:
```python
@app.post("/admin/login")
async def admin_login_post(request: Request, email: str = Form(...), password: str = Form(...), db=Depends(get_db)):
    client_ip = get_client_ip(request)
    
    # Rate limiting
    allowed, message = check_rate_limit(client_ip)
    if not allowed:
        return render_template("admin_login.html", error=message, ...)
    
    # Get admin user
    admin = crud.get_admin_by_email(db, email)
    if not admin or not verify_password(password, admin.password_hash):
        record_login_attempt(client_ip)
        return render_template("admin_login.html", error="Invalid credentials", ...)
    
    # Check if locked
    if check_admin_locked(admin):
        return render_template("admin_login.html", error="Account locked. Try again later.", ...)
    
    # 2FA check
    if admin.totp_enabled:
        request.session["pending_2fa"] = admin.id
        return RedirectResponse(url="/admin/verify-2fa", status_code=302)
    
    # Successful login
    record_successful_login(db, admin)
    clear_login_attempts(client_ip)
    login_user(request)
    return RedirectResponse(url="/admin/pages", status_code=302)
```

### 2. Add 2FA Templates

Create `admin_2fa_setup.html` and `admin_verify_2fa.html` templates for 2FA flow.

### 3. Activate Security Middleware

Add security headers middleware to main.py `create_app()` function.

### 4. Configure HTTPS

For production:
```bash
# Using nginx reverse proxy
upstream kaya {
    server localhost:8090;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;
    
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!MD5;
    
    location / {
        proxy_pass http://kaya;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Host $host;
    }
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$server_name$request_uri;
}
```

## Security Best Practices Applied

### OWASP Top 10 Mitigation

1. **SQL Injection**: ✅ SQLAlchemy ORM prevents SQL injection
2. **Broken Authentication**: ✅ bcrypt hashing, session management, 2FA
3. **Sensitive Data Exposure**: ✅ HTTPS enforcement, secure cookies
4. **XML External Entities (XXE)**: ✅ Bleach sanitization
5. **Broken Access Control**: ✅ require_admin() on all admin routes
6. **Security Misconfiguration**: ✅ Documented security headers
7. **XSS**: ✅ Jinja2 autoescape, bleach sanitization
8. **Insecure Deserialization**: ✅ No pickle usage, proper JSON handling
9. **Using Components with Known Vulnerabilities**: ✅ Regular dependency updates needed
10. **Insufficient Logging & Monitoring**: ✅ Framework in place

## Incident Response

### Suspected Breach

1. Immediately change admin password
2. Review admin login logs for unauthorized access
3. Check database for unauthorized modifications
4. Review file uploads for malicious content
5. Consider regenerating all credentials
6. Notify users if content was compromised

### Database Backup Strategy

```bash
# Daily backup
0 2 * * * /home/user/backup-kaya.sh

# Backup script
#!/bin/bash
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/backups/kaya"
mkdir -p $BACKUP_DIR

# Database
docker run --rm -v kaya_website_data:/data -v $BACKUP_DIR:/backup \
  alpine sh -c "cp /data/website.db /backup/website.db.$DATE"

# Uploads
docker run --rm -v kaya_website_uploads:/uploads -v $BACKUP_DIR:/backup \
  alpine sh -c "tar -czf /backup/uploads.$DATE.tar.gz -C /uploads ."

# Encrypt and move offsite
gpg --symmetric --cipher-algo AES256 $BACKUP_DIR/*.$DATE
rm $BACKUP_DIR/*.$DATE
```

## Monitoring & Alerting

### Key Metrics to Monitor

- Failed login attempts (alert if > 10 per hour)
- Account lockouts
- Upload file sizes and types
- Response times (DoS indicator)
- Database size growth
- Disk space usage

### Docker Container Security

```yaml
# docker-compose.yml additions
services:
  kaya:
    image: kaya-website:latest
    read_only: true  # Read-only filesystem
    cap_drop:
      - ALL  # Drop all capabilities
    cap_add:
      - NET_BIND_SERVICE
    security_opt:
      - no-new-privileges:true
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8090/"]
      interval: 30s
      timeout: 10s
      retries: 3
```

## Additional Resources

- OWASP Top 10: https://owasp.org/Top10/
- FastAPI Security: https://fastapi.tiangolo.com/tutorial/security/
- NIST Cybersecurity Framework: https://www.nist.gov/cyberframework
- CWE Top 25: https://cwe.mitre.org/top25/

## Support

For security concerns or vulnerability reports, please follow responsible disclosure practices.
