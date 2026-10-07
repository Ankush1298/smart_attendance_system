# Deploying to the cloud (Railway)

Two services from this one repo/Dockerfile, plus a MySQL 8 database:

| Service | `SERVICE` var | What it serves |
|---|---|---|
| **app** | *(unset)* | Admin / teacher / student web UI, REST API, attendance scheduler (`server.py`) |
| **register** | `register` | Face-registration portal for phones (`/student`, `/teacher`) |

Both get HTTPS from the platform, which phone browsers require for camera access.

## Steps

1. Railway -> **New Project** -> **Deploy from GitHub repo** -> this repo (`railway.json` + `Dockerfile` are picked up). This is the **app** service.
2. **New -> Database -> MySQL** in the same project.
3. On **app**, set variables:

   | Variable | Value |
   |---|---|
   | `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | `${{MySQL.MYSQLHOST}}` / `${{MySQL.MYSQLPORT}}` / `${{MySQL.MYSQLUSER}}` / `${{MySQL.MYSQLPASSWORD}}` / `${{MySQL.MYSQLDATABASE}}` |
   | `ADMIN_ID`, `ADMIN_PASSWORD` | your admin login (**required**, long and random) |
   | `APP_SECRET_KEY` | `python -c "import secrets;print(secrets.token_hex(32))"` (otherwise everyone is signed out on each restart) |
   | `APP_TIMEZONE` | e.g. `Asia/Kolkata` (timetable times are wall-clock in this zone) |
   | `REGISTRATION_PUBLIC_URL` | the **register** service's public URL, e.g. `https://xyz.up.railway.app` |

   Generate a domain under **Settings -> Networking**.
4. **New -> GitHub Repo** (same repo) for the second service -> name it **register**. Give it the same `MYSQL_*`, `ADMIN_ID`, `ADMIN_PASSWORD` variables, plus `SERVICE=register`. Generate a domain and put it in the app's `REGISTRATION_PUBLIC_URL`.
5. Open the app URL and sign in with `ADMIN_ID` / `ADMIN_PASSWORD`. Create student/teacher logins under Accounts; QR codes there point people to the register service. The schema is created automatically on first start.

## Notes

- Keep **one replica** per service: sessions, login throttling and the scheduler are in memory.
- `CLOUD_MODE=1`, `ALLOW_OPEN_REGISTRATION=0` and `DISABLE_REGISTRATION=1` (the in-process portal on port 5050, replaced by the register service) are set in the Dockerfile.
- Classroom cameras (USB / LAN RTSP) are not reachable from the cloud. Cameras and live attendance only work in the cloud for sources the server can reach over the internet; otherwise run the scheduler on-site against the same cloud database.
- The face model is baked into the image (`buffalo_sc`, ~15 MB); allow roughly 1 GB RAM per service.

## Local container test

```bash
docker build -t smart-attendance .
docker run --rm -p 8000:8000 -e PORT=8000 -e ADMIN_PASSWORD=choose-one \
  -e MYSQL_HOST=host.docker.internal -e MYSQL_USER=root -e MYSQL_PASSWORD=... smart-attendance
# registration portal: add  -e SERVICE=register
```
