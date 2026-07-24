from pathlib import Path
from uuid import uuid4
import re
import base64
import json
import time
import ipaddress
from urllib.parse import urlparse
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import URLError, HTTPError

import bleach
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown import markdown
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import crud, models, schemas
from .auth import (
    login_user,
    logout_user,
    require_admin,
    verify_password,
    get_password_hash,
    get_or_create_admin_user,
    setup_2fa,
    verify_2fa_setup,
    verify_2fa_token,
)
from .config import settings
from .database import SessionLocal, init_db
from .security import TOTP2FA, PasswordValidator

BASE_DIR = Path(__file__).resolve().parent
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"}
GITHUB_API_BASE = "https://api.github.com/repos"
_version_cache = {}


env = Environment(
    loader=FileSystemLoader(str(BASE_DIR / "templates")),
    autoescape=select_autoescape(["html", "xml"]),
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_nav_items(db):
    pages = crud.list_pages(db, only_published=True, navigation_only=True)
    page_map = {page.id: page for page in crud.get_pages(db, only_published=True)}
    items = [
        {
            "title": page.title,
            "href": crud.get_page_url(page, page_map),
            "sort_order": page.sort_order,
            "external": False,
        }
        for page in pages
    ]
    external_links = crud.get_site_settings(db).get("external_nav_links", "")
    try:
        items.extend(parse_external_nav_links(external_links))
    except ValueError:
        # Invalid stored settings should not break the public website.
        pass
    return sorted(items, key=lambda item: (item["sort_order"], item["title"].lower()))


def has_admin_users(db) -> bool:
    return db.scalar(select(models.AdminUser.id)) is not None


def parse_ip_networks(value: str):
    networks = []
    for item in re.split(r"[\s,]+", value or ""):
        if item:
            networks.append(ipaddress.ip_network(item, strict=False))
    return networks


def parse_external_nav_links(value: str):
    links = []
    for line_number, raw_line in enumerate((value or "").splitlines(), start=1):
        if not raw_line.strip():
            continue
        parts = [part.strip() for part in raw_line.split("|")]
        if len(parts) not in (2, 3) or not parts[0] or not parts[1]:
            raise ValueError(f"Navigation link on line {line_number} must use Label | URL | Order.")
        parsed = urlparse(parts[1])
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"Navigation link on line {line_number} must use a full http:// or https:// URL.")
        try:
            sort_order = int(parts[2]) if len(parts) == 3 and parts[2] else 100
        except ValueError:
            raise ValueError(f"Navigation link order on line {line_number} must be a whole number.")
        links.append({
            "title": parts[0],
            "href": parts[1],
            "sort_order": sort_order,
            "external": True,
        })
    return links


def get_client_ip(request: Request) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    peer_value = request.client.host if request.client else ""
    try:
        peer_ip = ipaddress.ip_address(peer_value)
    except ValueError:
        return None

    try:
        trusted_proxies = parse_ip_networks(settings.trusted_proxy_ips)
    except ValueError:
        trusted_proxies = []

    if any(peer_ip in network for network in trusted_proxies):
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            try:
                chain = [ipaddress.ip_address(item.strip()) for item in forwarded.split(",")]
            except ValueError:
                return None
            current = peer_ip
            for candidate in reversed(chain):
                if not any(current in network for network in trusted_proxies):
                    break
                current = candidate
            return current
        real_ip = request.headers.get("X-Real-IP", "").strip()
        if real_ip:
            try:
                return ipaddress.ip_address(real_ip)
            except ValueError:
                return None
    return peer_ip


def ip_is_allowed(client_ip, allowlist: str) -> bool:
    if not (allowlist or "").strip():
        return True
    if client_ip is None:
        return False
    try:
        return any(client_ip in network for network in parse_ip_networks(allowlist))
    except ValueError:
        return False


def parse_github_repo(repo_url: str) -> tuple[str, str] | None:
    try:
        parsed = urlparse(repo_url)
        if parsed.netloc.lower() != "github.com":
            return None
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) < 2:
            return None
        return parts[0], parts[1]
    except Exception:
        return None


def _read_json(url: str):
    req = UrlRequest(url, headers={"User-Agent": "kaya-website"})
    with urlopen(req, timeout=settings.github_api_timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_latest_repo_version(owner: str, repo: str) -> str | None:
    release_url = f"{GITHUB_API_BASE}/{owner}/{repo}/releases/latest"
    tags_url = f"{GITHUB_API_BASE}/{owner}/{repo}/tags?per_page=1"

    try:
        release = _read_json(release_url)
        tag = (release or {}).get("tag_name")
        if tag:
            return str(tag)
    except HTTPError as exc:
        if exc.code != 404:
            return None
    except (URLError, TimeoutError, ValueError):
        return None

    try:
        tags = _read_json(tags_url)
        if isinstance(tags, list) and tags:
            name = tags[0].get("name")
            if name:
                return str(name)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None
    return None


def get_release_repo_urls(site_config: dict | None = None) -> dict[str, str]:
    return {
        "website": (site_config or {}).get("website_repo_url") or settings.website_github_url,
        "app": (site_config or {}).get("app_repo_url") or settings.github_url,
    }


def get_release_versions(repo_urls: dict[str, str] | None = None) -> dict[str, str]:
    repo_urls = repo_urls or get_release_repo_urls()
    repos = {
        "website": parse_github_repo(repo_urls.get("website", "")),
        "app": parse_github_repo(repo_urls.get("app", "")),
    }
    now = time.time()
    versions = {
        "website": settings.kaya_version,
        "app": settings.kaya_version,
    }

    for key, repo in repos.items():
        if not repo:
            continue
        owner, name = repo
        cache_key = f"{owner}/{name}"
        cache_entry = _version_cache.get(cache_key)
        if cache_entry and now - cache_entry["ts"] < settings.github_version_cache_seconds:
            versions[key] = cache_entry["version"]
            continue

        latest = fetch_latest_repo_version(owner, name)
        if latest:
            _version_cache[cache_key] = {"version": latest, "ts": now}
            versions[key] = latest
        elif cache_entry:
            versions[key] = cache_entry["version"]

    return versions


def common_context(db=None, **context):
    if db is not None:
        context.setdefault("nav_items", get_nav_items(db))
        context.setdefault("site_config", crud.get_site_settings(db))
    context.setdefault("settings", settings)
    context.setdefault("release_versions", get_release_versions(get_release_repo_urls(context.get("site_config"))))
    request = context.get("request")
    if request is not None:
        context.setdefault("active_path", request.url.path)
        context.setdefault(
            "canonical_url",
            f"{str(settings.base_url).rstrip('/')}{request.url.path}",
        )
    return context


def is_docs_slug(slug: str) -> bool:
    clean = models.normalize_slug(slug)
    return clean == "documentation" or clean.startswith("documentation/")


def build_docs_sidebar(pages):
    page_map = {page.id: page for page in pages}
    root = {
        "key": "documentation",
        "title": "Documentation",
        "url": "/documentation",
        "page": None,
        "page_sort": None,
        "children": {},
    }

    for page in pages:
        page_path = models.normalize_slug(crud.get_page_path(page, page_map))
        if not is_docs_slug(page_path):
            continue

        if page_path == "documentation":
            root["page"] = page
            root["title"] = page.title
            root["page_sort"] = page.sort_order
            continue

        rel = page_path[len("documentation/"):]
        parts = [part for part in rel.split("/") if part]
        node = root
        acc = "documentation"
        for part in parts:
            acc = f"{acc}/{part}"
            if part not in node["children"]:
                node["children"][part] = {
                    "key": acc,
                    "title": part.replace("-", " ").title(),
                    "url": f"/{acc}",
                    "page": None,
                    "page_sort": None,
                    "children": {},
                }
            node = node["children"][part]

        node["page"] = page
        node["title"] = page.title
        node["url"] = f"/{page_path}"
        node["page_sort"] = page.sort_order

    def to_list(node):
        children = [to_list(child) for child in node["children"].values()]
        children.sort(key=lambda item: ((item["page_sort"] if item["page_sort"] is not None else 10**9), item["title"].lower()))
        node["children"] = children
        return node

    return to_list(root)


def render_template(template_name: str, **context):
    response_status = context.pop("status_code", status.HTTP_200_OK)
    if "site_config" not in context:
        with SessionLocal() as db:
            context["site_config"] = crud.get_site_settings(db)
    context.setdefault("release_versions", get_release_versions(get_release_repo_urls(context.get("site_config"))))
    template = env.get_template(template_name)
    return HTMLResponse(template.render(**context), status_code=response_status)


def sanitize_html(content: str) -> str:
    allowed_tags = bleach.sanitizer.ALLOWED_TAGS | {
        "p", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "code", "img", "table", "thead",
        "tbody", "tr", "th", "td", "ul", "ol", "li", "strong", "em", "blockquote", "hr", "br"
    }
    allowed_attrs = {
        "a": ["href", "title", "rel", "target"],
        "img": ["src", "alt", "title"],
        "code": ["class"],
        "th": ["align"],
        "td": ["align"],
    }
    raw_html = markdown(content or "", extensions=["fenced_code", "tables", "sane_lists"])
    return bleach.clean(raw_html, tags=allowed_tags, attributes=allowed_attrs, protocols=["http", "https", "mailto"], strip=True)


def strip_legacy_home_actions(content: str) -> str:
    """Remove the action markup used before buttons had dedicated settings."""
    # Older installations stored these actions inside home_content. They are
    # now rendered from dedicated settings so existing databases do not show
    # a duplicate set of buttons.
    return re.sub(
        r"""<div\s+class=(["'])hero-actions\1[^>]*>.*?</div\s*>""",
        "",
        content or "",
        flags=re.IGNORECASE | re.DOTALL,
    ).strip()


def sanitize_home_content(content: str) -> str:
    """Render the CMS-owned homepage hero without evaluating arbitrary Jinja."""
    replacements = {
        "{{ settings.demo_url }}": settings.demo_url,
        "{{ settings.github_url }}": settings.github_url,
    }
    rendered = strip_legacy_home_actions(content)
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, str(value))

    allowed_tags = {"a", "br", "div", "em", "h1", "p", "span", "strong"}
    allowed_attrs = {
        "a": ["class", "href", "rel", "target"],
        "div": ["class"],
        "span": ["class"],
    }
    return bleach.clean(
        rendered,
        tags=allowed_tags,
        attributes=allowed_attrs,
        protocols=["http", "https", "mailto"],
        strip=True,
    )


def sanitize_action_url(value: str) -> str:
    """Allow ordinary web links and local paths in CMS-managed buttons."""
    value = (value or "").strip()
    if value.startswith(("/", "#")) and not value.startswith("//"):
        return value
    if urlparse(value).scheme.lower() in {"http", "https", "mailto"}:
        return value
    return ""


def parse_homepage_items(value: str, fallback: str = ""):
    items = []
    source = value or fallback or ""
    for line in source.splitlines():
        if not line.strip():
            continue
        title, separator, body = line.partition("|")
        items.append({
            "title": title.strip(),
            "body": body.strip() if separator else "",
        })
    return items


def page_payload(title, slug, meta_description, content, order, nav=True):
    return {
        "title": title,
        "slug": slug,
        "meta_description": meta_description,
        "content": content.strip(),
        "published": True,
        "show_in_navigation": nav,
        "sort_order": order,
    }


def seed_default_pages(db):
    if db.scalar(select(models.Page)):
        return

    pages = [
        page_payload(
            "Home", "",
            "Kaya is a self-hosted infrastructure management platform for servers, services, assets, remote access, runbooks, licences and operational knowledge.",
            "", 0, False,
        ),
        page_payload(
            "Features", "features",
            "Explore Kaya features for inventory, remote access, runbooks, licences, Docker and VM monitoring, and audit trails.",
            """
## Kaya feature map

Kaya keeps the practical operational facts of a small estate in one private place: assets, servers, services, remote access paths, licences, runbooks and audits.

| Area | What Kaya helps you track |
| --- | --- |
| Infrastructure dashboard | Health, ownership, service status and recent changes |
| Server and service inventory | Physical hosts, VMs, Docker workloads and service metadata |
| Remote Manager | RDP, SSH and operational access details with a single launch surface |
| Runbooks | Markdown procedures attached to the systems that need them |
| Licence management | Keys, renewal dates, vendors and compliance notes |
| Audit logs | A timeline of day-to-day operational changes |
""", 10,
        ),
        page_payload(
            "Screenshots", "screenshots",
            "Preview the Kaya dashboard, inventory, runbooks and remote management workflows.",
            """
## Product screenshots

Upload real Kaya screenshots in the admin media library, then embed them in this page with Markdown:

```markdown
![Kaya dashboard](/uploads/your-screenshot.png)
```

The seeded homepage includes a polished dashboard mockup so the public site feels complete before real screenshots are added.
""", 20,
        ),
        page_payload(
            "Demo", "demo",
            "Try the Kaya demo or follow the project on GitHub.",
            """
## Demo

A public demo link can be configured with `DEMO_URL`. Until a hosted demo is available, use the GitHub repository to run Kaya locally and explore the product in your own environment.

[Open Kaya on GitHub](https://github.com/antybubbs/kaya)
""", 30,
        ),
        page_payload(
            "Install", "install",
            "Install Kaya with Docker Compose for private self-hosted operations management.",
            """
## Install Kaya

Kaya is designed for Docker-first deployment on your own host.

```bash
git clone https://github.com/antybubbs/kaya.git
cd kaya
cp .env.example .env
docker compose up -d --build
```

Keep your `.env`, database and uploaded assets backed up before updates.
""", 40,
        ),
        page_payload(
            "Documentation", "documentation",
            "Read Kaya documentation, install notes and operational guidance.",
            """
## Documentation

Use this page as the public documentation hub for Kaya. Add install guides, screenshots, release notes, reverse proxy examples and upgrade notes from the admin editor.

Useful starting points:

- Quick install
- Backup and restore
- Remote Manager setup
- Docker and VM monitoring
- Runbook conventions
""", 50,
        ),
        page_payload(
            "Roadmap", "roadmap",
            "Roadmap for Kaya self-hosted infrastructure management features.",
            """
## Roadmap

Kaya is moving toward a deeper infrastructure operations console for homelabs and small teams.

- Richer topology and dependency mapping
- Improved remote access flows
- More detailed Docker and VM telemetry
- Better release and update workflows
- Additional import and export options
""", 60,
        ),
        page_payload(
            "About Kaya", "about-kaya",
            "Learn more about Kaya, the self-hosted infrastructure platform for homelabs and small teams.",
            """
## About Kaya

Kaya is built for people who run real infrastructure without wanting a heavyweight enterprise platform. It gives homelabs and small infrastructure teams a calm, private place to manage services, assets, runbooks, licences and daily operational knowledge.
""", 70,
        ),
        page_payload(
            "Contact", "contact",
            "Contact Kaya and find the project on GitHub.",
            """
## Contact

Kaya is developed in the open on GitHub.

[GitHub repository](https://github.com/antybubbs/kaya)
""", 80,
        ),
    ]

    for item in pages:
        crud.create_page(db, schemas.PageCreate(**item))

    crud.create_post(db, schemas.PostCreate(
        title="Kaya website is ready for self-hosted publishing",
        slug="kaya-website-self-hosted-publishing",
        excerpt="A seeded update post for the standalone Kaya marketing site, with editable Markdown content and persistent uploads.",
        content="""
## A public home for Kaya

This website is a separate Docker-hosted project for presenting Kaya to users, contributors and self-hosting operators. It includes editable pages, Markdown rendering, image uploads and a simple admin area backed by SQLite.

Use the admin editor to turn this seeded post into your first real update.
""".strip(),
        published=True,
    ))


async def save_upload(upload: UploadFile, db):
    if not upload.filename:
        return None
    if upload.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Only image uploads are supported.")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", upload.filename).strip(".-") or "upload"
    unique_name = f"{uuid4().hex}-{safe_name}"
    upload_path = settings.uploads_dir / unique_name
    content = await upload.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Uploads must be 8MB or smaller.")
    upload_path.write_bytes(content)
    return crud.create_upload(db, unique_name, upload.filename, upload.content_type, len(content))


def build_page_form(title, slug, meta_description, content, published, show_in_navigation, sort_order, parent_id=None):
    return schemas.PageCreate(
        parent_id=parent_id,
        title=title,
        slug=slug,
        meta_description=meta_description,
        content=content,
        published=published,
        show_in_navigation=show_in_navigation,
        sort_order=sort_order,
    )


def build_post_form(title, slug, excerpt, content, published):
    return schemas.PostCreate(title=title, slug=slug, excerpt=excerpt, content=content, published=published)


def create_app():
    app = FastAPI(title="Kaya Website")
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        https_only=settings.session_cookie_secure,
        same_site="lax",
    )
    if settings.allowed_hosts and settings.allowed_hosts != "*":
        hosts = [host.strip() for host in settings.allowed_hosts.split(",") if host.strip()]
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    app.mount("/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")

    @app.middleware("http")
    async def admin_ip_allowlist_middleware(request: Request, call_next):
        if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
            with SessionLocal() as db:
                allowlist = crud.get_site_settings(db).get("admin_allowed_ips", "")
            if not ip_is_allowed(get_client_ip(request), allowlist):
                # A plain 403 avoids revealing whether the admin login exists.
                return HTMLResponse("Forbidden", status_code=status.HTTP_403_FORBIDDEN)
        return await call_next(request)

    @app.middleware("http")
    async def maintenance_mode_middleware(request: Request, call_next):
        path = request.url.path
        if path.startswith(("/admin", "/static", "/uploads")):
            return await call_next(request)
        with SessionLocal() as db:
            site_config = crud.get_site_settings(db)
            if site_config.get("maintenance_enabled"):
                return render_template(
                    "maintenance.html",
                    title="Maintenance",
                    nav_items=[],
                    site_config=site_config,
                    settings=settings,
                )
        return await call_next(request)

    @app.on_event("startup")
    def startup_event():
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        Path(settings.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
        init_db()
        with SessionLocal() as db:
            crud.seed_site_settings(db)
            seed_default_pages(db)

    @app.get("/", response_class=HTMLResponse)
    def public_home(request: Request, db=Depends(get_db)):
        posts = crud.list_posts(db, only_published=True)[:3]
        site_config = crud.get_site_settings(db)
        home_actions = [
            {
                "label": site_config.get("home_primary_button_label", ""),
                "url": sanitize_action_url(site_config.get("home_primary_button_url", "")),
                "class_name": "button-primary",
            },
            {
                "label": site_config.get("home_secondary_button_label", ""),
                "url": sanitize_action_url(site_config.get("home_secondary_button_url", "")),
                "class_name": "button-secondary",
            },
            {
                "label": site_config.get("home_tertiary_button_label", ""),
                "url": sanitize_action_url(site_config.get("home_tertiary_button_url", "")),
                "class_name": "button-ghost",
            },
        ]
        return render_template(
            "home.html",
            **common_context(
                db,
                request=request,
                title="Kaya",
                posts=posts,
                site_config=site_config,
                home_content_html=sanitize_home_content(site_config.get("home_content", "")),
                home_actions=home_actions,
                home_features=parse_homepage_items(site_config.get("home_features", "")),
                home_reasons=parse_homepage_items(site_config.get("home_reasons", "")),
            ),
        )

    @app.get("/blog", response_class=HTMLResponse)
    def public_blog(request: Request, db=Depends(get_db)):
        posts = crud.list_posts(db, only_published=True)
        return render_template("blog.html", **common_context(db, request=request, title="Kaya updates", posts=posts, meta_description="Kaya project updates, releases and self-hosted infrastructure notes."))

    @app.get("/blog/{slug}", response_class=HTMLResponse)
    def public_post(request: Request, slug: str, db=Depends(get_db)):
        post = crud.get_post_by_slug(db, slug)
        if not post or not post.published:
            raise HTTPException(status_code=404, detail="Post not found")
        return render_template(
            "post.html",
            **common_context(db, request=request, title=post.title, meta_description=post.excerpt, post=post, post_html=sanitize_html(post.content or "")),
        )

    @app.get("/admin", response_class=HTMLResponse)
    def admin_login(request: Request, db=Depends(get_db)):
        if not has_admin_users(db):
            return RedirectResponse(url="/admin/setup", status_code=status.HTTP_302_FOUND)
        if request.session.get("admin_authenticated"):
            return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)
        return render_template("admin_login.html", title="Admin login", nav_items=[], error=None, settings=settings)

    @app.get("/admin/setup", response_class=HTMLResponse)
    def admin_setup(request: Request, db=Depends(get_db)):
        if has_admin_users(db):
            return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
        return render_template(
            "admin_setup.html",
            title="Admin setup",
            nav_items=[],
            error=None,
            message="Create the first admin account to finish setup.",
            settings=settings,
        )

    @app.post("/admin/setup")
    async def admin_setup_post(
        request: Request,
        email: str = Form(...),
        password: str = Form(...),
        confirm_password: str = Form(...),
        db=Depends(get_db),
    ):
        if has_admin_users(db):
            return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

        if password != confirm_password:
            return render_template(
                "admin_setup.html",
                title="Admin setup",
                nav_items=[],
                error="Passwords do not match",
                message=None,
                settings=settings,
            )

        valid, validation_error = PasswordValidator.validate(password)
        if not valid:
            return render_template(
                "admin_setup.html",
                title="Admin setup",
                nav_items=[],
                error=validation_error,
                message=None,
                settings=settings,
            )

        admin = models.AdminUser(
            email=email.strip().lower(),
            password_hash=get_password_hash(password),
        )
        db.add(admin)
        try:
            db.commit()
            db.refresh(admin)
        except IntegrityError:
            db.rollback()
            return render_template(
                "admin_setup.html",
                title="Admin setup",
                nav_items=[],
                error="That email is already in use.",
                message=None,
                settings=settings,
            )

        login_user(request, admin.email)
        return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)

    @app.post("/admin/login")
    async def admin_login_post(request: Request, email: str = Form(...), password: str = Form(...), db=Depends(get_db)):
        if not has_admin_users(db):
            return RedirectResponse(url="/admin/setup", status_code=status.HTTP_302_FOUND)

        admin = crud.get_admin_by_email(db, email)
        if admin and verify_password(password, admin.password_hash):
            login_user(request, email)
            return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)

        return render_template("admin_login.html", title="Admin login", nav_items=[], error="Invalid credentials", settings=settings)

    @app.get("/admin/logout")
    def admin_logout(request: Request):
        logout_user(request)
        return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/pages", response_class=HTMLResponse)
    def admin_pages(request: Request, db=Depends(get_db)):
        require_admin(request)
        pages = crud.get_pages(db, only_published=False)
        return render_template("admin_pages.html", title="Pages", pages=pages, nav_items=[], settings=settings)

    @app.get("/admin/pages/new", response_class=HTMLResponse)
    def admin_new_page(request: Request, db=Depends(get_db)):
        require_admin(request)
        pages = crud.get_pages(db, only_published=False)
        return render_template("admin_edit_page.html", title="Create page", page=None, uploads=crud.list_uploads(db), parent_options=crud.build_page_parent_options(pages), form_action="/admin/pages/new", nav_items=[], message=None, settings=settings)

    @app.post("/admin/pages/new")
    async def admin_create_page(request: Request, title: str = Form(...), slug: str = Form(""), meta_description: str = Form(""), content: str = Form(""), published: bool = Form(False), show_in_navigation: bool = Form(False), sort_order: int = Form(100), parent_id: int | None = Form(None), image: UploadFile | None = File(None), db=Depends(get_db)):
        require_admin(request)
        if parent_id is not None and db.get(models.Page, parent_id) is None:
            pages = crud.get_pages(db, only_published=False)
            return render_template("admin_edit_page.html", title="Create page", page=None, uploads=crud.list_uploads(db), parent_options=crud.build_page_parent_options(pages), form_action="/admin/pages/new", nav_items=[], message=None, error="Selected parent page was not found.", settings=settings)

        page_in = build_page_form(title, slug, meta_description, content, published, show_in_navigation, sort_order, parent_id=parent_id)
        try:
            crud.create_page(db, page_in)
            if image and image.filename:
                await save_upload(image, db)
        except IntegrityError:
            db.rollback()
            pages = crud.get_pages(db, only_published=False)
            return render_template("admin_edit_page.html", title="Create page", page=None, uploads=crud.list_uploads(db), parent_options=crud.build_page_parent_options(pages), form_action="/admin/pages/new", nav_items=[], message="A page with that slug already exists.", settings=settings)
        return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/pages/{page_id}/edit", response_class=HTMLResponse)
    def admin_edit_page(request: Request, page_id: int, db=Depends(get_db)):
        require_admin(request)
        page = db.get(models.Page, page_id)
        if not page:
            raise HTTPException(status_code=404, detail="Not found")
        pages = crud.get_pages(db, only_published=False)
        return render_template("admin_edit_page.html", title="Edit page", page=page, uploads=crud.list_uploads(db), parent_options=crud.build_page_parent_options(pages, exclude_page_id=page.id), form_action=f"/admin/pages/{page_id}/edit", nav_items=[], message=None, settings=settings)

    @app.post("/admin/pages/{page_id}/edit")
    async def admin_update_page(request: Request, page_id: int, title: str = Form(...), slug: str = Form(""), meta_description: str = Form(""), content: str = Form(""), published: bool = Form(False), show_in_navigation: bool = Form(False), sort_order: int = Form(100), parent_id: int | None = Form(None), image: UploadFile | None = File(None), db=Depends(get_db)):
        require_admin(request)
        page = db.get(models.Page, page_id)
        if not page:
            raise HTTPException(status_code=404, detail="Not found")
        all_pages = crud.get_pages(db, only_published=False)
        excluded_ids = crud.get_descendant_ids(all_pages, page.id)
        if parent_id == page.id or (parent_id is not None and parent_id in excluded_ids):
            return render_template("admin_edit_page.html", title="Edit page", page=page, uploads=crud.list_uploads(db), parent_options=crud.build_page_parent_options(all_pages, exclude_page_id=page.id), form_action=f"/admin/pages/{page_id}/edit", nav_items=[], message=None, error="Please choose a different parent page.", settings=settings)
        if parent_id is not None and db.get(models.Page, parent_id) is None:
            return render_template("admin_edit_page.html", title="Edit page", page=page, uploads=crud.list_uploads(db), parent_options=crud.build_page_parent_options(all_pages, exclude_page_id=page.id), form_action=f"/admin/pages/{page_id}/edit", nav_items=[], message=None, error="Selected parent page was not found.", settings=settings)

        page_in = schemas.PageUpdate(**build_page_form(title, slug, meta_description, content, published, show_in_navigation, sort_order, parent_id=parent_id).model_dump())
        try:
            crud.update_page(db, page, page_in)
            if image and image.filename:
                await save_upload(image, db)
        except IntegrityError:
            db.rollback()
            return render_template("admin_edit_page.html", title="Edit page", page=page, uploads=crud.list_uploads(db), parent_options=crud.build_page_parent_options(all_pages, exclude_page_id=page.id), form_action=f"/admin/pages/{page_id}/edit", nav_items=[], message="A page with that slug already exists.", settings=settings)
        return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)

    @app.post("/admin/pages/{page_id}/delete")
    def admin_delete_page(request: Request, page_id: int, db=Depends(get_db)):
        require_admin(request)
        page = db.get(models.Page, page_id)
        if not page:
            raise HTTPException(status_code=404, detail="Not found")
        crud.delete_page(db, page)
        return RedirectResponse(url="/admin/pages", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/posts", response_class=HTMLResponse)
    def admin_posts(request: Request, db=Depends(get_db)):
        require_admin(request)
        return render_template("admin_posts.html", title="Updates", posts=crud.list_posts(db, only_published=False), nav_items=[], settings=settings)

    @app.get("/admin/posts/new", response_class=HTMLResponse)
    def admin_new_post(request: Request, db=Depends(get_db)):
        require_admin(request)
        return render_template("admin_edit_post.html", title="Create update", post=None, uploads=crud.list_uploads(db), form_action="/admin/posts/new", nav_items=[], message=None, settings=settings)

    @app.post("/admin/posts/new")
    async def admin_create_post(request: Request, title: str = Form(...), slug: str = Form(...), excerpt: str = Form(""), content: str = Form(""), published: bool = Form(False), image: UploadFile | None = File(None), db=Depends(get_db)):
        require_admin(request)
        try:
            crud.create_post(db, build_post_form(title, slug, excerpt, content, published))
            if image and image.filename:
                await save_upload(image, db)
        except IntegrityError:
            db.rollback()
            return render_template("admin_edit_post.html", title="Create update", post=None, uploads=crud.list_uploads(db), form_action="/admin/posts/new", nav_items=[], message="A post with that slug already exists.", settings=settings)
        return RedirectResponse(url="/admin/posts", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/posts/{post_id}/edit", response_class=HTMLResponse)
    def admin_edit_post(request: Request, post_id: int, db=Depends(get_db)):
        require_admin(request)
        post = db.get(models.Post, post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Not found")
        return render_template("admin_edit_post.html", title="Edit update", post=post, uploads=crud.list_uploads(db), form_action=f"/admin/posts/{post_id}/edit", nav_items=[], message=None, settings=settings)

    @app.post("/admin/posts/{post_id}/edit")
    async def admin_update_post(request: Request, post_id: int, title: str = Form(...), slug: str = Form(...), excerpt: str = Form(""), content: str = Form(""), published: bool = Form(False), image: UploadFile | None = File(None), db=Depends(get_db)):
        require_admin(request)
        post = db.get(models.Post, post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Not found")
        try:
            crud.update_post(db, post, schemas.PostUpdate(**build_post_form(title, slug, excerpt, content, published).model_dump()))
            if image and image.filename:
                await save_upload(image, db)
        except IntegrityError:
            db.rollback()
            return render_template("admin_edit_post.html", title="Edit update", post=post, uploads=crud.list_uploads(db), form_action=f"/admin/posts/{post_id}/edit", nav_items=[], message="A post with that slug already exists.", settings=settings)
        return RedirectResponse(url="/admin/posts", status_code=status.HTTP_302_FOUND)

    @app.post("/admin/posts/{post_id}/delete")
    def admin_delete_post(request: Request, post_id: int, db=Depends(get_db)):
        require_admin(request)
        post = db.get(models.Post, post_id)
        if not post:
            raise HTTPException(status_code=404, detail="Not found")
        crud.delete_post(db, post)
        return RedirectResponse(url="/admin/posts", status_code=status.HTTP_302_FOUND)

    @app.get("/admin/uploads", response_class=HTMLResponse)
    def admin_uploads(request: Request, db=Depends(get_db)):
        require_admin(request)
        return render_template("admin_uploads.html", title="Uploads", uploads=crud.list_uploads(db), nav_items=[], message=None, settings=settings)

    @app.post("/admin/uploads", response_class=HTMLResponse)
    async def admin_upload_create(request: Request, image: UploadFile = File(...), db=Depends(get_db)):
        require_admin(request)
        await save_upload(image, db)
        return RedirectResponse(url="/admin/uploads", status_code=status.HTTP_302_FOUND)

    @app.post("/admin/uploads/{upload_id}/delete")
    def admin_upload_delete(
        request: Request,
        upload_id: int,
        return_to: str = Form("uploads"),
        db=Depends(get_db),
    ):
        require_admin(request)
        upload = db.get(models.Upload, upload_id)
        if not upload:
            raise HTTPException(status_code=404, detail="Upload not found")

        uploads_root = settings.uploads_dir.resolve()
        upload_path = (uploads_root / upload.filename).resolve()
        if upload_path.parent != uploads_root:
            raise HTTPException(status_code=400, detail="Invalid upload path")
        try:
            upload_path.unlink(missing_ok=True)
        except OSError:
            raise HTTPException(status_code=500, detail="The upload file could not be deleted")

        crud.delete_upload(db, upload)
        destination = "/admin/settings?upload_deleted=true" if return_to == "settings" else "/admin/uploads"
        return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/admin/settings", response_class=HTMLResponse)
    def admin_site_settings(request: Request, upload_deleted: bool = False, db=Depends(get_db)):
        require_admin(request)
        client_ip = get_client_ip(request)
        return render_template(
            "admin_settings.html",
            title="Site settings",
            nav_items=[],
            site_config=crud.get_site_settings(db),
            uploads=crud.list_uploads(db),
            message="Upload deleted." if upload_deleted else None,
            error=None,
            detected_client_ip=str(client_ip) if client_ip else "Unknown",
            detected_client_ip_is_private=bool(client_ip and client_ip.is_private),
            settings=settings,
        )

    @app.get("/admin/homepage", response_class=HTMLResponse)
    def admin_homepage(request: Request, db=Depends(get_db)):
        require_admin(request)
        site_config = crud.get_site_settings(db)
        site_config["home_content"] = strip_legacy_home_actions(site_config.get("home_content", ""))
        return render_template(
            "admin_homepage.html",
            title="Homepage",
            nav_items=[],
            site_config=site_config,
            uploads=crud.list_uploads(db),
            message=None,
            settings=settings,
        )

    @app.post("/admin/homepage", response_class=HTMLResponse)
    async def admin_homepage_update(
        request: Request,
        home_content: str = Form(""),
        home_hero_image_url: str = Form(""),
        home_primary_button_label: str = Form(""),
        home_primary_button_url: str = Form(""),
        home_secondary_button_label: str = Form(""),
        home_secondary_button_url: str = Form(""),
        home_tertiary_button_label: str = Form(""),
        home_tertiary_button_url: str = Form(""),
        home_intro_eyebrow: str = Form(""),
        home_intro_title: str = Form(""),
        home_intro_body: str = Form(""),
        home_features: str = Form(""),
        home_why_eyebrow: str = Form(""),
        home_why_title: str = Form(""),
        home_why_body: str = Form(""),
        home_reasons: str = Form(""),
        home_install_eyebrow: str = Form(""),
        home_install_title: str = Form(""),
        home_install_body: str = Form(""),
        home_install_code: str = Form(""),
        home_hero_image: UploadFile | None = File(None),
        db=Depends(get_db),
    ):
        require_admin(request)
        if home_hero_image and home_hero_image.filename:
            uploaded_home_hero = await save_upload(home_hero_image, db)
            home_hero_image_url = f"/uploads/{uploaded_home_hero.filename}"
        crud.set_site_setting(db, "home_content", strip_legacy_home_actions(home_content))
        crud.set_site_setting(db, "home_hero_image_url", home_hero_image_url or "/static/kaya-dashboard-screenshot.svg")
        crud.set_site_setting(db, "home_primary_button_label", home_primary_button_label.strip())
        crud.set_site_setting(db, "home_primary_button_url", home_primary_button_url.strip())
        crud.set_site_setting(db, "home_secondary_button_label", home_secondary_button_label.strip())
        crud.set_site_setting(db, "home_secondary_button_url", home_secondary_button_url.strip())
        crud.set_site_setting(db, "home_tertiary_button_label", home_tertiary_button_label.strip())
        crud.set_site_setting(db, "home_tertiary_button_url", home_tertiary_button_url.strip())
        crud.set_site_setting(db, "home_intro_eyebrow", home_intro_eyebrow)
        crud.set_site_setting(db, "home_intro_title", home_intro_title)
        crud.set_site_setting(db, "home_intro_body", home_intro_body)
        crud.set_site_setting(db, "home_features", home_features)
        crud.set_site_setting(db, "home_why_eyebrow", home_why_eyebrow)
        crud.set_site_setting(db, "home_why_title", home_why_title)
        crud.set_site_setting(db, "home_why_body", home_why_body)
        crud.set_site_setting(db, "home_reasons", home_reasons)
        crud.set_site_setting(db, "home_install_eyebrow", home_install_eyebrow)
        crud.set_site_setting(db, "home_install_title", home_install_title)
        crud.set_site_setting(db, "home_install_body", home_install_body)
        crud.set_site_setting(db, "home_install_code", home_install_code)
        return render_template(
            "admin_homepage.html",
            title="Homepage",
            nav_items=[],
            site_config=crud.get_site_settings(db),
            uploads=crud.list_uploads(db),
            message="Homepage saved.",
            settings=settings,
        )

    @app.post("/admin/settings", response_class=HTMLResponse)
    async def admin_site_settings_update(
        request: Request,
        site_logo_url: str = Form(""),
        header_logo_url: str = Form(""),
        website_repo_url: str = Form(""),
        app_repo_url: str = Form(""),
        external_nav_links: str = Form(""),
        admin_allowed_ips: str = Form(""),
        confirm_current_ip_lockout: bool = Form(False),
        maintenance_enabled: bool = Form(False),
        maintenance_message: str = Form(""),
        logo_image: UploadFile | None = File(None),
        header_logo_image: UploadFile | None = File(None),
        db=Depends(get_db),
    ):
        require_admin(request)
        client_ip = get_client_ip(request)
        try:
            parse_ip_networks(admin_allowed_ips)
        except ValueError:
            return render_template(
                "admin_settings.html",
                title="Site settings",
                nav_items=[],
                site_config={
                    **crud.get_site_settings(db),
                    "admin_allowed_ips": admin_allowed_ips,
                    "external_nav_links": external_nav_links,
                },
                uploads=crud.list_uploads(db),
                message=None,
                error="The admin allowlist contains an invalid IP address or network.",
                detected_client_ip=str(client_ip) if client_ip else "Unknown",
                detected_client_ip_is_private=bool(client_ip and client_ip.is_private),
                settings=settings,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        try:
            parse_external_nav_links(external_nav_links)
        except ValueError as exc:
            return render_template(
                "admin_settings.html",
                title="Site settings",
                nav_items=[],
                site_config={
                    **crud.get_site_settings(db),
                    "admin_allowed_ips": admin_allowed_ips,
                    "external_nav_links": external_nav_links,
                },
                uploads=crud.list_uploads(db),
                message=None,
                error=str(exc),
                detected_client_ip=str(client_ip) if client_ip else "Unknown",
                detected_client_ip_is_private=bool(client_ip and not client_ip.is_global),
                settings=settings,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if (
            admin_allowed_ips.strip()
            and not ip_is_allowed(client_ip, admin_allowed_ips)
            and not confirm_current_ip_lockout
        ):
            return render_template(
                "admin_settings.html",
                title="Site settings",
                nav_items=[],
                site_config={
                    **crud.get_site_settings(db),
                    "admin_allowed_ips": admin_allowed_ips,
                    "external_nav_links": external_nav_links,
                },
                uploads=crud.list_uploads(db),
                message=None,
                error=f"Settings not saved: the allowlist does not include the address used by your current connection ({client_ip or 'Unknown'}). Add it, or confirm below that you intend to block this connection.",
                detected_client_ip=str(client_ip) if client_ip else "Unknown",
                detected_client_ip_is_private=bool(client_ip and client_ip.is_private),
                settings=settings,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if logo_image and logo_image.filename:
            uploaded_logo = await save_upload(logo_image, db)
            site_logo_url = f"/uploads/{uploaded_logo.filename}"
        if header_logo_image and header_logo_image.filename:
            uploaded_header_logo = await save_upload(header_logo_image, db)
            header_logo_url = f"/uploads/{uploaded_header_logo.filename}"
        crud.set_site_setting(db, "site_logo_url", site_logo_url or "/static/brand/kaya-full-logo.svg")
        crud.set_site_setting(db, "header_logo_url", header_logo_url or "/static/brand/kaya-full-logo.svg")
        crud.set_site_setting(db, "website_repo_url", website_repo_url or settings.website_github_url)
        crud.set_site_setting(db, "app_repo_url", app_repo_url or settings.github_url)
        crud.set_site_setting(db, "external_nav_links", external_nav_links.strip())
        crud.set_site_setting(db, "admin_allowed_ips", admin_allowed_ips.strip())
        crud.set_site_setting(db, "maintenance_enabled", "true" if maintenance_enabled else "false")
        crud.set_site_setting(db, "maintenance_message", maintenance_message or "Kaya is currently undergoing maintenance. Please check back shortly.")
        return render_template(
            "admin_settings.html",
            title="Site settings",
            nav_items=[],
            site_config=crud.get_site_settings(db),
            uploads=crud.list_uploads(db),
            message="Settings saved.",
            error=None,
            detected_client_ip=str(client_ip) if client_ip else "Unknown",
            detected_client_ip_is_private=bool(client_ip and client_ip.is_private),
            settings=settings,
        )

    @app.get("/admin/user-settings", response_class=HTMLResponse)
    def admin_user_settings(request: Request, db=Depends(get_db)):
        require_admin(request)
        
        admin = get_or_create_admin_user(db)
        if not admin:
            return RedirectResponse(url="/admin/setup", status_code=status.HTTP_302_FOUND)
        
        return render_template(
            "admin_user_settings.html",
            title="User Settings",
            nav_items=[],
            two_fa_enabled=admin.totp_enabled if admin else False,
            show_2fa_setup=False,
            message=None,
            error=None,
            settings=settings,
        )

    @app.post("/admin/user-settings", response_class=HTMLResponse)
    async def admin_user_settings_update(
        request: Request,
        section: str = Form(...),
        current_password: str = Form(""),
        new_password: str = Form(""),
        confirm_password: str = Form(""),
        password: str = Form(""),
        token: str = Form(""),
        db=Depends(get_db),
    ):
        require_admin(request)

        admin = get_or_create_admin_user(db)
        if not admin:
            return RedirectResponse(url="/admin/setup", status_code=status.HTTP_302_FOUND)
        
        message = None
        error = None
        show_2fa_setup = False
        qr_code = None
        totp_secret = None
        backup_codes = None

        if section == "password":
            # Verify current password
            if not verify_password(current_password, admin.password_hash):
                error = "Current password is incorrect"
            elif new_password != confirm_password:
                error = "Passwords do not match"
            elif not new_password:
                error = "New password cannot be empty"
            else:
                # Validate password strength
                is_valid, error_msg = PasswordValidator.validate(new_password)
                if not is_valid:
                    error = error_msg
                else:
                    # Update password
                    admin.password_hash = get_password_hash(new_password)
                    db.commit()
                    message = "Password updated successfully"

        elif section == "setup-2fa":
            if admin.totp_enabled:
                error = "2FA is already enabled"
            else:
                # Generate new secret
                provisioning_uri, codes = setup_2fa(db, admin)
                qr_code_bytes = TOTP2FA.get_qr_code(provisioning_uri)
                qr_code = base64.b64encode(qr_code_bytes).decode()
                totp_secret = admin.totp_secret
                backup_codes = codes
                show_2fa_setup = True
                request.session["2fa_setup_in_progress"] = True

        elif section == "verify-2fa":
            if not request.session.get("2fa_setup_in_progress"):
                error = "2FA setup not initiated"
            elif not admin.totp_secret:
                error = "2FA secret not found"
            elif not token:
                error = "Please enter a 6-digit code"
            elif not verify_2fa_setup(db, admin, token):
                error = "Invalid code. Please try again"
            else:
                request.session.pop("2fa_setup_in_progress", None)
                message = "Two-Factor Authentication enabled successfully!"
                # Re-fetch admin to get updated 2FA status
                db.refresh(admin)

        elif section == "disable-2fa":
            if not verify_password(password, admin.password_hash):
                error = "Password is incorrect"
            elif not admin.totp_enabled:
                error = "2FA is not currently enabled"
            else:
                admin.totp_enabled = False
                admin.totp_secret = None
                admin.backup_codes = None
                db.commit()
                message = "Two-Factor Authentication disabled"

        return render_template(
            "admin_user_settings.html",
            title="User Settings",
            nav_items=[],
            two_fa_enabled=admin.totp_enabled if admin else False,
            show_2fa_setup=show_2fa_setup,
            qr_code=qr_code,
            totp_secret=totp_secret,
            backup_codes=backup_codes,
            message=message,
            error=error,
            settings=settings,
        )

    @app.get("/{slug:path}", response_class=HTMLResponse)
    def public_page(request: Request, slug: str, db=Depends(get_db)):
        clean_slug = models.normalize_slug(slug)
        page = crud.get_page_by_path(db, clean_slug)
        if not page or not page.published:
            raise HTTPException(status_code=404, detail="Page not found")

        pages = crud.get_pages(db, only_published=True)
        page_map = {item.id: item for item in pages}
        current_path = crud.get_page_path(page, page_map)
        if is_docs_slug(current_path):
            return render_template(
                "docs_page.html",
                **common_context(
                    db,
                    request=request,
                    title=page.title,
                    meta_description=page.meta_description,
                    page=page,
                    page_html=sanitize_html(page.content or ""),
                    docs_sidebar=build_docs_sidebar(pages),
                    current_slug=current_path,
                ),
            )

        has_children = any(item.parent_id == page.id for item in pages)
        if page.parent_id is not None or has_children:
            return render_template(
                "page_hierarchy.html",
                **common_context(
                    db,
                    request=request,
                    title=page.title,
                    meta_description=page.meta_description,
                    page=page,
                    page_html=sanitize_html(page.content or ""),
                    page_tree=crud.build_page_tree(pages),
                    current_path=current_path,
                ),
            )

        return render_template("page.html", **common_context(db, request=request, title=page.title, meta_description=page.meta_description, page=page, page_html=sanitize_html(page.content or "")))

    return app


app = create_app()


