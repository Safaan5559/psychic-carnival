# Py2APK

A production-oriented Python-to-Android APK builder with a Tornado web UI, SQLite metadata store, and isolated Docker builds using Buildozer/python-for-Android.

> This repository was created in `Safaan5559/psychic-carnival` because the connected GitHub account currently does not expose a repository-creation action. You can rename the repository to `Py2APK` in GitHub settings without changing the code.

## Features

- Python `.py` and `.zip` uploads
- Drag-and-drop responsive dashboard
- Custom app name, package, version name/code, icon and optional splash
- Background build queue
- Real-time Server-Sent Events build logs
- Docker-isolated builds with CPU/RAM/PID/time limits
- SQLite build history and statistics
- Password authentication with secure password hashing
- Search and pagination
- Retry/delete/cleanup flows
- Downloadable APK and logs
- Docker Compose deployment
- Environment-based configuration

## Packaging engine

Builds use Buildozer on top of python-for-android. Android SDK/NDK/JDK dependencies live in a dedicated builder image; uploaded code is mounted only into the build container.

## Quick start

```bash
cp .env.example .env
mkdir -p data storage

docker compose build
docker compose up -d
```

Open `http://localhost:8080` and register a local account.

## Important production security note

The web container needs access to the Docker Engine to create short-lived build containers. A mounted Docker socket is effectively host-level Docker access. For a public deployment, run the build worker against a dedicated Docker daemon/VM or replace the socket connection with a tightly isolated build service.

See `docs/DEPLOYMENT.md` for the production checklist.
