# Deploying to the cloud

One container serves both the frontend (student/faculty KYC portals and the admin
panel, rendered by `register.py`) and the backend (face recognition, MySQL access).
It needs a MySQL 8 database. Instructions below use Railway; any Docker host works
(Render, Fly.io, Cloud Run) as long as you provide the same environment variables
and a MySQL database.

## Railway

1. Railway dashboard -> **New Project** -> **Deploy from GitHub repo** -> pick this repo.
   `railway.json` and the `Dockerfile` are picked up automatically.
2. In the same project: **New** -> **Database** -> **MySQL**.
3. On the app service, set these variables (references to the MySQL service):

   | Variable | Value |
   |---|---|
   | `MYSQL_HOST` | `${{MySQL.MYSQLHOST}}` |
   | `MYSQL_PORT` | `${{MySQL.MYSQLPORT}}` |
   | `MYSQL_USER` | `${{MySQL.MYSQLUSER}}` |
   | `MYSQL_PASSWORD` | `${{MySQL.MYSQLPASSWORD}}` |
   | `MYSQL_DATABASE` | `${{MySQL.MYSQLDATABASE}}` |
   | `ADMIN_ID` | `ADMIN` (or your choice) |
   | `ADMIN_PASSWORD` | a long random secret (**required**) |

4. Service **Settings -> Networking -> Generate Domain**. The platform provides HTTPS,
   which phone browsers require for camera access.
5. Open `https://<your-domain>/` (hub), `/student`, `/teacher`, `/admin`.

The schema is created automatically on first start.

## Behaviour in the cloud (`CLOUD_MODE=1`, set in the Dockerfile)

- The built-in default admin password is disabled. If `ADMIN_PASSWORD` is unset or a
  placeholder, admin login is refused.
- `ALLOW_OPEN_REGISTRATION=0`: only IDs/passcodes pre-authorised by the admin can
  enroll. Create them from the desktop app's credentials screen, pointing the desktop
  app's `MYSQL_*` at the same cloud database (the Railway MySQL public/TCP proxy
  address), or insert into `authorized_credentials` yourself.
- Runs a single worker. KYC and admin sessions are kept in memory, so do not scale to
  more than one replica, and admins must log in again after a redeploy.

## Not hosted

- The desktop app (`main.py`, live classroom cameras) stays on-prem; it talks to the same
  MySQL database.
- `backend/api/app.py` (REST API) has no authentication, so it is deliberately not exposed.
- `frontend/admin/` and `static/` call endpoints (`/api/stats`, `/api/timetable/upload`)
  that no server currently implements, so they are not served.

## Local container test

```bash
docker build -t smart-attendance .
docker run --rm -p 8000:8000 -e PORT=8000 -e ADMIN_PASSWORD=choose-one \
  -e MYSQL_HOST=host.docker.internal -e MYSQL_USER=root -e MYSQL_PASSWORD=... smart-attendance
```
