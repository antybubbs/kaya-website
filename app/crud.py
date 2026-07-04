from sqlalchemy import select
from sqlalchemy.orm import Session
from . import models, schemas

DEFAULT_SITE_SETTINGS = {
    "site_logo_url": "/static/brand/kaya-full-logo.svg",
    "header_logo_url": "/static/brand/kaya-full-logo.svg",
    "home_hero_image_url": "/static/kaya-dashboard-screenshot.svg",
    "website_repo_url": "https://github.com/antybubbs/kaya-website",
    "app_repo_url": "https://github.com/antybubbs/kaya",
    "maintenance_enabled": "false",
    "maintenance_message": "Kaya is currently undergoing maintenance. Please check back shortly.",
    "home_content": '<span class="eyebrow">Command your self-hosted infrastructure</span>\n<h1>One private operations console for the systems you run.</h1>\n<p>Kaya brings servers, services, remote access, runbooks, licences, assets and operational history into a calm self-hosted control plane.</p>\n<div class="hero-actions">\n  <a class="button button-primary" href="{{ settings.demo_url }}">View Demo</a>\n  <a class="button button-secondary" href="/install">Get Started</a>\n  <a class="button button-ghost" href="{{ settings.github_url }}" target="_blank" rel="noreferrer">GitHub</a>\n</div>',
    "home_intro_eyebrow": "Your Infrastructure. Your Home.",
    "home_intro_title": "Welcome to Kaya.",
    "home_intro_body": "Keep asset records, access details, procedures and monitoring context close to the systems they describe.",
    "home_features": "\n".join([
        "Infrastructure dashboard|A fast overview of estate health, ownership and recent operational changes.",
        "Server and service inventory|Track hosts, VMs, containers, networks, vendors, locations and critical service notes.",
        "Remote Manager|Launch the right access path and keep operational connection details in one controlled place.",
        "Runbooks|Write Markdown procedures for incidents, maintenance windows and repeatable admin tasks.",
        "Licence management|Capture keys, vendors, renewals and compliance notes before they disappear into inboxes.",
        "Docker/VM monitoring|Follow workloads across Docker hosts and virtual machines with practical status context.",
        "Audit logs|Review who changed what, when, and why during day-to-day infrastructure work.",
        "Self-hosted deployment|Run Kaya on your own hardware with Docker and retain ownership of your operational data.",
    ]),
    "home_why_eyebrow": "Why Kaya?",
    "home_why_title": "Built for infrastructure that is personal, critical and too specific for generic tooling.",
    "home_why_body": "Kaya is for the people who know every server has a story: where it lives, what depends on it, how to reach it, and what to do when it complains at the least convenient possible moment.",
    "home_reasons": "\n".join([
        "No cloud dependency|Deploy privately, back up simply, and keep control.",
        "Operator-first records|Designed around the information you reach for during real maintenance.",
        "Small-team friendly|Useful for one careful homelabber or a compact operations team.",
    ]),
    "home_install_eyebrow": "Install",
    "home_install_title": "Start with Docker Compose.",
    "home_install_body": "Clone Kaya, configure your environment and bring it online on your own host.",
    "home_install_code": "git clone https://github.com/antybubbs/kaya.git\ncd kaya\ncp .env.example .env\ndocker compose up -d --build",
}


# Admin user operations
def get_admin_by_email(db: Session, email: str):
    return db.scalar(select(models.AdminUser).where(models.AdminUser.email == email))


def get_or_create_admin(db: Session, email: str, password_hash: str):
    admin = get_admin_by_email(db, email)
    if not admin:
        admin = models.AdminUser(email=email, password_hash=password_hash)
        db.add(admin)
        db.commit()
        db.refresh(admin)
    return admin


def get_page_by_slug(db: Session, slug: str):
    return db.scalar(select(models.Page).where(models.Page.slug == models.normalize_slug(slug)))


def _sort_pages(items):
    def sort_key(item):
        if isinstance(item, dict):
            return (item.get("page_sort") or 1000, item.get("title", "").lower())
        return (item.sort_order if item.sort_order is not None else 1000, item.title.lower())
    return sorted(items, key=sort_key)


def get_pages(db: Session, only_published: bool = True):
    return list_pages(db, only_published=only_published)


def get_descendant_ids(pages, page_id: int):
    children_by_parent = {}
    for page in pages:
        children_by_parent.setdefault(page.parent_id, []).append(page)

    descendants = set()
    stack = [page_id]
    while stack:
        current_id = stack.pop()
        for child in children_by_parent.get(current_id, []):
            if child.id not in descendants:
                descendants.add(child.id)
                stack.append(child.id)
    return descendants


def get_page_path(page, page_map=None):
    page_map = page_map or {}
    segments = []
    current = page
    seen = set()

    while current and current.id not in seen:
        seen.add(current.id)
        if current.slug:
            segments.append(models.normalize_slug(current.slug))
        current = page_map.get(current.parent_id)

    return "/".join(reversed([segment for segment in segments if segment]))


def get_page_url(page, page_map=None):
    path = get_page_path(page, page_map=page_map)
    return f"/{path}" if path else "/"


def build_page_tree(pages):
    page_map = {page.id: page for page in pages}
    nodes = {}
    roots = []

    for page in pages:
        if not page.slug:
            continue
        nodes[page.id] = {
            "id": page.id,
            "title": page.title,
            "slug": page.slug,
            "url": get_page_url(page, page_map),
            "page_sort": page.sort_order,
            "parent_id": page.parent_id,
            "children": [],
        }

    for node in nodes.values():
        parent_id = node["parent_id"]
        if parent_id and parent_id in nodes:
            nodes[parent_id]["children"].append(node)
        else:
            roots.append(node)

    def sort_nodes(items):
        for item in items:
            item["children"] = sort_nodes(_sort_pages(item["children"]))
        return items

    return sort_nodes(_sort_pages(roots))


def build_page_parent_options(pages, exclude_page_id: int | None = None):
    excluded_ids = set()
    if exclude_page_id is not None:
        excluded_ids.add(exclude_page_id)
        excluded_ids.update(get_descendant_ids(pages, exclude_page_id))

    tree = build_page_tree([page for page in pages if page.id not in excluded_ids])
    options = []

    def walk(items, depth=0):
        for item in items:
            options.append({
                "id": item["id"],
                "title": item["title"],
                "depth": depth,
                "url": item["url"],
            })
            walk(item["children"], depth + 1)

    walk(tree)
    return options


def get_page_by_path(db: Session, path: str, only_published: bool = True):
    clean_path = models.normalize_slug(path)
    pages = get_pages(db, only_published=only_published)

    if not clean_path:
        return next((page for page in pages if not page.slug), None)

    page_map = {page.id: page for page in pages}
    segments = [segment for segment in clean_path.split("/") if segment]

    def descendants_for(parent_id):
        return _sort_pages([page for page in pages if page.parent_id == parent_id])

    roots = descendants_for(None)
    for root in roots:
        if models.normalize_slug(root.slug) != segments[0]:
            continue

        current = root
        matched = True
        for segment in segments[1:]:
            children = descendants_for(current.id)
            next_page = next((page for page in children if models.normalize_slug(page.slug) == segment), None)
            if not next_page:
                matched = False
                break
            current = next_page

        if matched:
            return current

    return next((page for page in pages if models.normalize_slug(page.slug) == clean_path), None)


def list_pages(db: Session, only_published: bool = True, navigation_only: bool = False):
    query = select(models.Page)
    if only_published:
        query = query.where(models.Page.published.is_(True))
    if navigation_only:
        query = query.where(models.Page.show_in_navigation.is_(True))
    query = query.order_by(models.Page.sort_order, models.Page.title)
    return db.scalars(query).all()


def create_page(db: Session, page_in: schemas.PageCreate):
    page = models.Page(**page_in.model_dump())
    db.add(page)
    db.commit()
    db.refresh(page)
    return page


def update_page(db: Session, page: models.Page, page_in: schemas.PageUpdate):
    for field, value in page_in.model_dump().items():
        setattr(page, field, value)
    db.add(page)
    db.commit()
    db.refresh(page)
    return page


def delete_page(db: Session, page: models.Page):
    children = db.scalars(select(models.Page).where(models.Page.parent_id == page.id)).all()
    for child in children:
        child.parent_id = page.parent_id
    db.delete(page)
    db.commit()


def get_post_by_slug(db: Session, slug: str):
    return db.scalar(select(models.Post).where(models.Post.slug == models.normalize_slug(slug)))


def list_posts(db: Session, only_published: bool = True):
    query = select(models.Post)
    if only_published:
        query = query.where(models.Post.published.is_(True))
    query = query.order_by(models.Post.published_at.desc(), models.Post.title)
    return db.scalars(query).all()


def create_post(db: Session, post_in: schemas.PostCreate):
    post = models.Post(**post_in.model_dump())
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def update_post(db: Session, post: models.Post, post_in: schemas.PostUpdate):
    for field, value in post_in.model_dump().items():
        setattr(post, field, value)
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


def delete_post(db: Session, post: models.Post):
    db.delete(post)
    db.commit()


def create_upload(db: Session, filename: str, original_filename: str, content_type: str | None, size: int):
    upload = models.Upload(
        filename=filename,
        original_filename=original_filename,
        content_type=content_type,
        size=size,
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    return upload


def list_uploads(db: Session):
    return db.scalars(select(models.Upload).order_by(models.Upload.created_at.desc())).all()


def set_site_setting(db: Session, key: str, value: str | None):
    setting = db.get(models.SiteSetting, key)
    if setting is None:
        setting = models.SiteSetting(key=key, value=value)
    else:
        setting.value = value
    db.add(setting)
    db.commit()
    db.refresh(setting)
    return setting


def get_site_settings(db: Session):
    values = dict(DEFAULT_SITE_SETTINGS)
    rows = db.scalars(select(models.SiteSetting)).all()
    for row in rows:
        values[row.key] = row.value or ""
    values["maintenance_enabled"] = str(values.get("maintenance_enabled", "false")).lower() == "true"
    return values


def seed_site_settings(db: Session):
    for key, value in DEFAULT_SITE_SETTINGS.items():
        if db.get(models.SiteSetting, key) is None:
            db.add(models.SiteSetting(key=key, value=value))
    db.commit()
