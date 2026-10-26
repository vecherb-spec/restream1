# Аудит Restream MVP перед beta

Дата: 26 октября 2026  
Цель: подготовить MVP restream-платформы к beta-тесту церквями до воскресенья, 1 ноября 2026.

## 1. Что это за проект

Проект — MVP restream-платформы: один входящий RTMP-поток из OBS/vMix приходит в SRS, backend FastAPI принимает SRS webhook `on_publish`, на каждую активную площадку запускается отдельный FFmpeg worker, а Next.js frontend дает личный кабинет и админку.

Текущая архитектура **не транскодирует видео**. FFmpeg запускается с `-c copy`, то есть делает relay/remux потока в RTMP/RTMPS destination. Это снижает CPU-нагрузку, но требует, чтобы входящий кодек/битрейт подходил целевым площадкам.

Реализовано:

- auth: login/register/logout, HttpOnly cookie, password reset;
- личный кабинет клиента: stream key, RTMP server, выбор площадок, HLS preview, логи;
- admin dashboard: users, limits, backups, active streams/workers;
- площадки: YouTube, VK, Rutube, Telegram RTMP, custom RTMP;
- profiles площадок и masked secrets;
- Telegram notifications;
- SQLite/PostgreSQL support;
- SRS webhooks, FFmpeg auto-restart, per-destination status;
- Docker Compose и systemd-oriented deploy scripts.

Не реализовано или не готово для обещания beta-тестерам:

- SRT ingest;
- billing/pricing;
- multi-server scaling;
- автоматический e2e RTMP fan-out тест в CI;
- полноценный wizard/инструкция для волонтеров;
- SLA/мониторинг уровня production.

## 2. Найденные проблемы по severity

### Critical

| Проблема | Статус | Комментарий |
|---|---|---|
| `origin/main` фактически пустой, рабочий MVP живет в PR-ветках | remaining | До beta нужно выбрать production-ветку и не деплоить пустой `main`. |
| Backend Docker image не копировал `telegram_notify.py` | fixed | Без этого backend container падал бы при import `main.py`. |
| Next.js имел direct critical npm audit finding | fixed | Обновлено до Next `16.4.3`. |
| Dashboard был на `/`, а нужен публичный landing | fixed | Landing добавлен на `/`, кабинет перенесен на `/dashboard`. |
| Password reset вел на старый root route | fixed | Ссылки теперь `/dashboard?reset_token=...`. |
| Deploy scripts убивали процессы через broad `pgrep -f` | fixed | Заменено на PID-file helpers для backend/frontend manual fallback. |
| Backend принимал `.env.example` placeholder `REPLACE_WITH...` как webhook secret | fixed | Startup теперь отвергает такой secret. |
| Fresh Docker deploy ломается при bare `docker compose up` без render SRS config | remaining | Нужно использовать `deploy/scripts/compose_up.sh`; это критично для инструкции деплоя. |

### Important

| Проблема | Статус | Комментарий |
|---|---|---|
| SRS hook в Docker должен быть `backend:8000`, не `127.0.0.1:8000` | remaining/docs | `compose_up.sh` выставляет правильно; bare compose легко сломать. |
| Non-systemd deploy path раньше не стартовал frontend | fixed | Добавлен `frontend_process.sh`, `deploy.sh` и `recover_500.sh` стартуют frontend manual mode. |
| `RESTREAM_COOKIE_SECURE=true` мешает plain HTTP local testing | remaining | Для локального HTTP ставить `RESTREAM_COOKIE_SECURE=false`; production через HTTPS оставлять `true`. |
| Host/systemd path не ставит SRS service | remaining | Для нового сервера проще Docker Compose; если systemd — отдельно установить SRS/nginx. |
| SRS webhook secret передается в query string | remaining | SRS http_hooks не умеет header; держать SRS→backend внутри localhost/docker network, логировать аккуратно. |
| Открытая self-service registration | remaining | Для beta лучше добавить invite/admin gate или временно закрыть регистрацию. |
| Tests требуют `pytest`, но `requirements.txt` не содержит test deps | remaining | Для dev/CI добавить отдельный test requirements или документировать venv setup. |
| `npm audit --omit=dev` показывает 1 moderate transitive vulnerability | remaining | Остался `baseline-browser-mapping`; не blocker, но проверить перед release. |
| Не выполнен Docker/Compose/RTMP fan-out smoke в этой VM | remaining | Docker CLI отсутствует; обязательно прогнать на новом сервере. |
| Бренд/домен еще не выбран | fixed/configured | Добавлены `NEXT_PUBLIC_PRODUCT_NAME`, `NEXT_PUBLIC_CONTACT_EMAIL`, `NEXT_PUBLIC_SITE_DOMAIN`. |

### Minor

| Проблема | Статус | Комментарий |
|---|---|---|
| README смешивает историю веток и production deploy | remaining | После выбора production-ветки обновить README. |
| FastAPI `@app.on_event("startup")` deprecated warning | remaining | Не blocker; позже перейти на lifespan. |
| `database.py` делает `init_db()` на import | remaining | Терпимо для MVP, но лучше убрать side-effect позже. |
| `requests` в `requirements.txt` не используется | remaining | Можно удалить после проверки. |
| Admin API возвращает stream keys администратору | accepted | ОК для admin role, но помнить при API review. |

## 3. Секреты в публичном репозитории

Реальных секретов, токенов, приватных ключей или production passwords в публичном repo не найдено.

Найдены только placeholders:

- `REPLACE_WITH_STRONG_DB_PASSWORD`;
- `REPLACE_WITH_STRONG_ADMIN_PASSWORD`;
- `REPLACE_WITH_STRONG_SRS_WEBHOOK_SECRET`;
- test passwords / test database URLs в test docs.

Ротация из-за committed secrets не требуется. Но при переносе на новый сервер обязательно сгенерировать новые значения:

- `POSTGRES_PASSWORD`;
- `DATABASE_URL`;
- `RESTREAM_SRS_WEBHOOK_SECRET`;
- `RESTREAM_ADMIN_PASSWORD`;
- `RESTREAM_TELEGRAM_BOT_TOKEN`, если будет использоваться Telegram.

## 4. Что обязательно сделать до воскресенья, 1 ноября

1. Выбрать production-ветку. Не использовать текущий пустой `main`, пока MVP не слит туда.
2. Поднять новый сервер по Docker Compose сценарию ниже.
3. Заполнить `.env` реальными secrets и финальными domain/name settings.
4. Настроить nginx + TLS.
5. Прогнать реальный OBS/vMix smoke:
   - 1 поток 1080p на 3 площадки;
   - 1 поток 1080p на 5 площадок;
   - остановка/перезапуск входящего потока;
   - отключение одной destination и проверка, что остальные продолжают работать.
6. Прогнать короткий load smoke: минимум 3-5 параллельных входящих потоков, если нет времени на полный 10-20.
7. Проверить reboot сервера: Docker containers поднялись, SRS webhook работает, frontend доступен.
8. Настроить backups, logrotate, basic monitoring disk/RAM/network.
9. Подготовить короткую инструкцию для beta churches: OBS/vMix server, stream key, Start/Stop площадок, что делать при красном статусе.
10. Решить, нужна ли закрытая регистрация/invite на beta.

## 5. Что можно оставить после beta

- SRT ingest.
- Adaptive bitrate / transcoding.
- Multi-node routing.
- Billing/pricing UI.
- Автоматические e2e RTMP tests в CI.
- Полная observability stack: Prometheus/Grafana/Loki/Sentry.
- Перенос startup hook FastAPI на lifespan.
- Улучшение onboarding wizard.

## 6. Server sizing для 10-20 church streams x 3-5 destinations

### Важно: relay, не transcode

Сейчас FFmpeg использует `-c copy`. Это значит:

- CPU тратится в основном на процессы, mux/network I/O и watchdog;
- качество/битрейт не меняются;
- если площадка требует другой codec/profile/bitrate, текущая система это не исправит;
- главный ресурс — **исходящий bandwidth**.

### Формула bandwidth

Примерная сеть:

```text
total Mbps = concurrent_streams × stream_bitrate_mbps × (1 input + destination_count)
```

Примеры:

| Сценарий | Расчет | Итого |
|---|---:|---:|
| 10 потоков, 4 Mbps, 3 площадки | 10 × 4 × 4 | ~160 Mbps |
| 10 потоков, 6 Mbps, 5 площадок | 10 × 6 × 6 | ~360 Mbps |
| 20 потоков, 4 Mbps, 3 площадки | 20 × 4 × 4 | ~320 Mbps |
| 20 потоков, 6 Mbps, 5 площадок | 20 × 6 × 6 | ~720 Mbps |

Добавить запас 30-50% на overhead, HLS preview, retries, network bursts.

### Рекомендации

Минимально для beta 10 потоков:

- 8 vCPU;
- 16 GB RAM;
- NVMe 100-200 GB;
- 1 Gbps uplink, лучше unmetered;
- Ubuntu 24.04 LTS.

Рекомендовано для 20 потоков x 3-5 destinations:

- 16 vCPU;
- 32 GB RAM;
- NVMe 200+ GB;
- 2 Gbps uplink или гарантированный 1 Gbps с запасом по провайдеру;
- traffic allowance не меньше 10-20 TB/month, лучше unmetered.

Если позже появится transcoding:

- считать уже не destinations, а transcode profiles;
- 1080p software transcode может требовать 1-3 vCPU на поток;
- для 20 потоков нужен отдельный sizing, возможно GPU/NVENC или multi-node.

## 7. One-shot deploy на fresh Ubuntu server

Ниже Docker Compose путь. Он предпочтителен для нового dedicated server, потому что поднимает Postgres, backend, frontend и SRS согласованно.

### 7.1. Установка пакетов

```bash
apt update
apt install -y git curl ca-certificates gnupg nginx certbot python3-certbot-nginx
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

### 7.2. Код и env

```bash
git clone https://github.com/vecherb-spec/restream1 /opt/restream
cd /opt/restream
git fetch origin
git checkout cursor/test-ready-audit-7bc3
cp .env.example .env
chmod 600 .env
```

В `.env` обязательно заменить:

```bash
PUBLIC_DOMAIN=your-domain.example
NEXT_PUBLIC_PRODUCT_NAME="Your Product Name"
NEXT_PUBLIC_CONTACT_EMAIL=hello@your-domain.example
NEXT_PUBLIC_SITE_DOMAIN=your-domain.example
NEXT_PUBLIC_OBS_SERVER_URL=rtmp://your-domain.example/live
NEXT_PUBLIC_HLS_BASE_URL=https://your-domain.example/srs/live

POSTGRES_PASSWORD=<strong-random>
DATABASE_URL=postgresql://restream:<same-password>@postgres:5432/restream
RESTREAM_ADMIN_PASSWORD=<strong-random>
RESTREAM_SRS_WEBHOOK_SECRET=<strong-random-32+chars>
RESTREAM_PUBLIC_URL=https://your-domain.example
RESTREAM_COOKIE_SECURE=true
RESTREAM_CORS_ORIGINS=https://your-domain.example
RESTREAM_SRS_BACKEND_HOOK=backend:8000
```

Генерация secrets:

```bash
openssl rand -base64 32
```

### 7.3. Запуск Compose

Не запускать bare `docker compose up` на fresh clone. Сначала нужно render SRS config:

```bash
chmod +x deploy/scripts/*.sh
RESTREAM_SRS_BACKEND_HOOK=backend:8000 ./deploy/scripts/compose_up.sh
docker compose ps
```

Проверки:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -I http://127.0.0.1:3000/
```

### 7.4. Nginx reverse proxy

Создать `/etc/nginx/sites-available/restream`:

```nginx
server {
    listen 80;
    server_name your-domain.example;

    client_max_body_size 50m;

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

    location /srs/ {
        proxy_pass http://127.0.0.1:8080/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_buffering off;
    }

    location / {
        proxy_pass http://127.0.0.1:3000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable:

```bash
ln -s /etc/nginx/sites-available/restream /etc/nginx/sites-enabled/restream
nginx -t
systemctl reload nginx
```

### 7.5. TLS

```bash
certbot --nginx -d your-domain.example
certbot renew --dry-run
```

После TLS:

```bash
curl -fsS https://your-domain.example/
curl -fsS https://your-domain.example/api/me || true
```

`/api/me` без login должен вернуть auth error, не 500.

### 7.6. RTMP smoke

RTMP ingest открыт на `1935`:

```text
Server: rtmp://your-domain.example/live
Stream key: из личного кабинета /dashboard
```

FFmpeg local smoke:

```bash
ffmpeg -re -f lavfi -i testsrc=size=1280x720:rate=30 \
  -f lavfi -i sine=frequency=1000:sample_rate=48000 \
  -c:v libx264 -preset veryfast -b:v 2500k -maxrate 2500k -bufsize 5000k \
  -c:a aac -b:a 128k -f flv \
  rtmp://your-domain.example/live/<stream_key>
```

Проверить:

- dashboard показывает входящий поток;
- активные destination получают поток;
- `/srs/live/<stream_key>.m3u8` доступен через HTTPS;
- остановка ffmpeg вызывает `on_unpublish` и workers останавливаются.

## 8. Итоговый статус

MVP стал ближе к beta: исправлены явные blockers в packaging, public landing, route split, reset link, Next security, deploy process safety и placeholder secrets.

Перед передачей церквям остается главное: поднять clean dedicated server, прогнать настоящий RTMP fan-out под нагрузкой и закрыть production-ветку/домен/бренд.
