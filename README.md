# Kaya Website

A standalone Docker-hosted marketing website for [Kaya](https://github.com/antybubbs/kaya), built with FastAPI, Jinja templates, SQLite and Markdown editing. This repository is separate from the Kaya app and is intended for `https://github.com/antybubbs/kaya-website`.

## What it includes

- Public marketing pages for Kaya
- Dark, app-like Kaya product styling
- Editable CMS pages stored in SQLite
- Blog/update posts at `/blog`
- Admin login at `/admin`
- Markdown editor fields for pages and updates
- Image upload manager with persistent uploads
- Safe public Markdown rendering with HTML sanitization
- Responsive desktop, tablet and mobile layouts
- Docker Compose deployment on port `8090`

## Quick start

```bash
docker compose up -d --build
```

Open `http://localhost:8090`.

On first visit to `/admin`, you will be redirected to a setup screen to create the initial admin account.

The session secret is generated automatically on first boot and persisted in the data volume.

## Configuration

No environment variables are required for a standard install.

Optional environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `WEBSITE_PORT` | `8090` | Uvicorn bind port inside the container |
| `BASE_URL` | `http://localhost:8090` | Public site URL |
| `SECRET_KEY` | auto-generated at first run | Session signing secret |
| `ADMIN_EMAIL` | empty | Optional bootstrap admin email |
| `ADMIN_PASSWORD` | empty | Optional bootstrap admin password |
| `ALLOWED_HOSTS` | `*` | Comma-separated trusted hosts |
| `SESSION_COOKIE_SECURE` | `false` | Set `true` behind HTTPS |
| `GITHUB_URL` | Kaya GitHub repo | Header/footer/project links |
| `DEMO_URL` | `/demo` | Demo CTA target |
| `KAYA_VERSION` | `v0.19.1` | Version text shown in the UI |

## Content editing

Visit `/admin` after deployment. The admin area lets you:

- Create, edit and delete public pages
- Set title, slug, meta description, publish status, navigation visibility and sort order
- Create update/blog posts
- Upload images and embed them in Markdown with paths like `/uploads/image.png`

Seed content is created automatically on first startup so the site looks complete immediately.

## Storage

Docker volumes:

- `kaya_website_data` mounted at `/app/data` for SQLite
- `kaya_website_uploads` mounted at `/app/uploads` for images

## Updating

```bash
git pull
docker compose up -d --build
```

The SQLite database and uploads remain in Docker volumes.

## Install from GHCR (Main or Dev Branch)

The publish workflow tags images by branch name, so pushing branch `dev2.0.0` publishes:

```text
ghcr.io/antybubbs/kaya-website:dev2.0.0
```

Example `docker-compose.yml` service using GHCR image tags:

```yaml
services:
  website:
    image: ghcr.io/antybubbs/kaya-website:dev2.0.0
    ports:
      - "8090:8090"
    volumes:
      - kaya_website_data:/app/data
      - kaya_website_uploads:/app/uploads
    restart: unless-stopped

volumes:
  kaya_website_data:
  kaya_website_uploads:
```

Then deploy a dev branch image:

```bash
docker compose pull
docker compose up -d
```

## Backup

Back up the database:

```bash
docker run --rm -v kaya_website_data:/data -v ${PWD}:/backup alpine \
  sh -c "cp /data/website.db /backup/kaya-website.db"
```

Back up uploads:

```bash
docker run --rm -v kaya_website_uploads:/uploads -v ${PWD}:/backup alpine \
  sh -c "tar -czf /backup/kaya-website-uploads.tar.gz -C /uploads ."
```

Restore by copying the database back into `/app/data/website.db` and extracting uploads into `/app/uploads` while the container is stopped.

## Reverse proxy

Example Nginx site:

```nginx
server {
  listen 80;
  server_name kaya.example.com;

  location / {
    proxy_pass http://127.0.0.1:8090;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

For HTTPS, terminate TLS at your reverse proxy and set:

```env
BASE_URL=https://kaya.example.com
SESSION_COOKIE_SECURE=true
ALLOWED_HOSTS=kaya.example.com
```

## Development

Install dependencies locally:

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8090
```

## Notes

This website references Kaya branding and product concepts, but it does not import or modify the Kaya application codebase.
