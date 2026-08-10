# restream1

## Next.js frontend

The primary web UI lives in `frontend/`. It uses the FastAPI REST API under
`/api/...` and replaces the earlier Streamlit MVP panel.

Local development:

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 npm run dev
```

Production build:

```bash
cd frontend
npm run build
npm run start
```

Useful environment variables:

```bash
# Leave empty in production when Nginx serves frontend and /api on the same domain.
NEXT_PUBLIC_API_BASE_URL=
NEXT_PUBLIC_OBS_SERVER_URL=rtmp://restream.medialive.ru/live
NEXT_PUBLIC_HLS_BASE_URL=https://restream.medialive.ru/srs/live
```

Password recovery requires **both** the Next.js frontend and the FastAPI
backend from the same release. If the login page shows `Not Found` after
submitting forgot-password, the production backend is still on an older build.

Update the backend on the server:

```bash
chmod +x deploy/scripts/update_backend.sh
RESTREAM_REPO_DIR=/opt/restream RESTREAM_DEPLOY_BRANCH=main ./deploy/scripts/update_backend.sh
```

Then verify:

```bash
curl -i -X POST https://restream.medialive.ru/api/auth/forgot-password \
  -H "Content-Type: application/json" \
  -d '{"identifier":"your_login_or_email"}'
```

A healthy deployment returns HTTP `200` with `{"code":0,...}` instead of `404`.

The frontend uses an HttpOnly `restream_session` cookie set by FastAPI. Useful
backend cookie variables:

```bash
RESTREAM_COOKIE_NAME=restream_session
RESTREAM_COOKIE_SECURE=true
# Optional, usually leave empty for a host-only cookie.
RESTREAM_COOKIE_DOMAIN=
```

## Plans and destination limits

Each user has:

```text
plan
max_destinations
```

Defaults:

```bash
RESTREAM_DEFAULT_CLIENT_PLAN=free
RESTREAM_DEFAULT_MAX_DESTINATIONS=1
RESTREAM_ADMIN_MAX_DESTINATIONS=99
```

Clients cannot save more active destinations than their `max_destinations`.
Admins can edit a user's plan and destination limit from the Next.js admin
panel. The SRS webhook also enforces the limit when starting FFmpeg.

## Live stream status

The client dashboard uses Server-Sent Events:

```text
GET /api/me/stream-status/events
```

The endpoint streams the same payload as `/api/me/stream-status` once per
second. The Next.js client falls back to slower polling if the SSE connection is
temporarily unavailable.

The admin dashboard uses a similar live feed:

```text
GET /api/admin/dashboard/events
```

It streams CPU/RAM/disk metrics, active SRS publishers, FFmpeg workers, and
recent exits once per second. Users and database backups are loaded separately
because they change less often.

## Database backends

SQLite remains the default for an already-running single-server MVP:

```bash
RESTREAM_DB_PATH=/opt/restream/restream.db
DATABASE_URL=
```

PostgreSQL can be enabled by setting `DATABASE_URL`:

```bash
DATABASE_URL=postgresql://restream:STRONG_DB_PASSWORD@127.0.0.1:5432/restream
```

The Python storage layer keeps SQLite compatibility and switches to PostgreSQL
when `DATABASE_URL` starts with `postgresql://` or `postgres://`.

Suggested migration order:

1. Create a fresh PostgreSQL database.
2. Start the backend once with `DATABASE_URL=...` so it creates tables.
3. Stop writes briefly or schedule a maintenance window.
4. Copy users/settings:

   ```bash
   python scripts/migrate_sqlite_to_postgres.py \
     --sqlite /opt/restream/restream.db \
     --postgres postgresql://restream:STRONG_DB_PASSWORD@127.0.0.1:5432/restream
   ```

5. Start backend with `DATABASE_URL=...`.
6. Verify login, stream settings, SRS webhooks, and admin panel.
7. Keep the old SQLite backup until the new setup is stable.

## Worker architecture (24/7)

Each enabled destination runs its own FFmpeg process:

```text
VideoCoder -> SRS -> FFmpeg(YouTube)
                  -> FFmpeg(VK)
                  -> FFmpeg(Rutube)
                  -> FFmpeg(Telegram)
                  -> FFmpeg(Custom)
```

If VK dies, YouTube/Rutube/Telegram/Custom keep running. Restart uses a rolling
window + exponential backoff. Orphan recovery adopts only processes tagged with
`RESTREAM_WORKER=1` and restores per-destination logs (`stream_key + platform`).

SRS webhooks (`POST /on_publish`, `POST /on_unpublish`) **always** require
`RESTREAM_SRS_WEBHOOK_SECRET`. Trusted IPs are never a bypass.

SRS `http_hooks` cannot set custom headers, so Compose/systemd render the shared
secret into the webhook URL query (`?token=...`). Prefer keeping SRS→backend on
localhost so the token never leaves the host. Optional nginx can instead inject
`X-Restream-Webhook-Secret` and strip the query string.

## Docker Compose

```bash
cp .env.example .env
# Set strong values for:
#   POSTGRES_PASSWORD
#   DATABASE_URL
#   RESTREAM_SRS_WEBHOOK_SECRET
#   RESTREAM_ADMIN_PASSWORD
#   NEXT_PUBLIC_OBS_SERVER_URL / NEXT_PUBLIC_HLS_BASE_URL
chmod +x deploy/scripts/*.sh
RESTREAM_SRS_BACKEND_HOOK=backend:8000 ./deploy/scripts/compose_up.sh
```

`compose_up.sh` renders `deploy/srs/srs.conf` from the template with the same
webhook secret the backend uses, then starts Compose. Do **not** run bare
`docker compose up` without rendering SRS config first.

Published ports (production-oriented):

```text
127.0.0.1:8000 -> backend API (nginx only)
127.0.0.1:3000 -> frontend (nginx only)
1935           -> SRS RTMP ingest (OBS/encoder)
127.0.0.1:8080 -> SRS HTTP/HLS (nginx only)
```

## Production deploy (systemd)

```bash
chmod +x deploy/scripts/*.sh
# .env must contain a strong RESTREAM_SRS_WEBHOOK_SECRET (not change_me*)
RESTREAM_REPO_DIR=/opt/restream RESTREAM_DEPLOY_BRANCH=cursor/restream-mvp-3225 ./deploy/scripts/deploy.sh
./deploy/scripts/install_systemd.sh   # creates user `restream`, installs units
```

Services run as non-root user `restream`, listen on localhost, and should be
exposed through nginx TLS.

Required backend secrets in `/opt/restream/.env`:

```bash
RESTREAM_ADMIN_USERNAME=admin
RESTREAM_ADMIN_PASSWORD=strong_password_here
RESTREAM_SRS_WEBHOOK_SECRET=long_random_secret
RESTREAM_SRS_TRUSTED_IPS=127.0.0.1,::1
```

`RESTREAM_ADMIN_PASSWORD` is used only when the admin user does not exist yet.
Change the default admin password after the first deploy if the database was
created earlier.

## Production healthchecks and backups

Scripts for a systemd-based production host live in `deploy/scripts/`:

```bash
chmod +x deploy/scripts/*.sh

# Full deploy: git pull, build frontend, restart services
deploy/scripts/deploy.sh

# Check API, frontend, SRS, and optional systemd units
deploy/scripts/healthcheck.sh

# Restart failed services if healthcheck fails
deploy/scripts/recover_services.sh

# Daily PostgreSQL backup (requires DATABASE_URL)
deploy/scripts/backup_postgres.sh
```

Example systemd timer for daily PostgreSQL backups:

```ini
[Unit]
Description=Daily Restream PostgreSQL backup

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

Install log rotation for application and FFmpeg logs:

```bash
sudo cp deploy/logrotate/restream /etc/logrotate.d/restream
```

## HLS preview in the client cabinet

The client dashboard renders a browser preview through HLS. Browsers cannot play
RTMP directly, so SRS must expose HLS over HTTP/HTTPS.

Default preview URL generated by the app:

```text
https://restream.medialive.ru/srs/live/{stream_key}.m3u8
```

You can override it with environment variables:

```bash
NEXT_PUBLIC_HLS_BASE_URL=https://restream.medialive.ru/srs/live
```

Example SRS fragments:

```conf
http_server {
    enabled         on;
    listen          8080;
    dir             ./objs/nginx/html;
}

vhost __defaultVhost__ {
    http_hooks {
        enabled         on;
        on_publish      http://127.0.0.1:8000/on_publish?token=YOUR_SRS_WEBHOOK_SECRET;
        on_unpublish    http://127.0.0.1:8000/on_unpublish?token=YOUR_SRS_WEBHOOK_SECRET;
    }

    hls {
        enabled         on;
        hls_path        ./objs/nginx/html;
        hls_fragment    3;
        hls_window      30;
    }
}
```

Example Nginx location for `restream.medialive.ru`:

```nginx
location /srs/ {
    proxy_pass http://127.0.0.1:8080/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
}

# FastAPI (do not expose :8000 publicly)
location /api/ {
    proxy_pass http://127.0.0.1:8000/api/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}

location /on_publish {
    proxy_pass http://127.0.0.1:8000/on_publish;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}

location /on_unpublish {
    proxy_pass http://127.0.0.1:8000/on_unpublish;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}

# Next.js
location / {
    proxy_pass http://127.0.0.1:3000/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

## Tests

Unit / API tests (SQLite):

```bash
RESTREAM_ALLOW_INSECURE_DEFAULTS=true \
RESTREAM_SRS_WEBHOOK_SECRET=unit-test-srs-secret-value \
pytest -q
```

PostgreSQL integration tests (real Postgres via Compose overlay):

```bash
cd /opt/restream   # or your clone path

# If git pull complains about deploy/srs/srs.conf (rendered secret file):
cp deploy/srs/srs.conf /tmp/srs.conf.bak
git checkout -- deploy/srs/srs.conf 2>/dev/null || true
git pull origin cursor/restream-mvp-3225
# restore/re-render production SRS config afterwards:
# cp /tmp/srs.conf.bak deploy/srs/srs.conf
# or: ./deploy/scripts/render_srs_conf.sh

apt-get install -y python3-venv docker-compose-v2   # once
./deploy/scripts/run_postgres_tests.sh
```

The script creates `.venv-tests` automatically (PEP 668 safe) and supports both
`docker compose` and legacy `docker-compose`.

Do **not** run bare `docker` / bare `pytest`.

## Troubleshooting

| Symptom | Check |
|---------|--------|
| `on_publish` HTTP 403 | Backend and SRS must share the same `RESTREAM_SRS_WEBHOOK_SECRET`; re-run `render_srs_conf.sh` / `compose_up.sh` |
| Backend refuses to start | Secret missing/`change_me*`; set a strong secret or (dev only) `RESTREAM_ALLOW_INSECURE_DEFAULTS=true` |
| One destination down | Expected with per-destination workers; only that FFmpeg restarts |
| Stuck after backend restart | Orphan recovery adopts tagged workers; check logs for `FFmpeg worker recovery` |
| Auth brute-force | Rate limit on login/register/forgot/reset; tune `RESTREAM_AUTH_RATE_*` |
