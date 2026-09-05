# Production deployment

## 1. Server requirements

Use a Linux host with Docker Engine, at least 8 GB RAM, 4 CPU cores and enough disk for Android SDK/NDK caches. A first builder image can be several GB.

## 2. Configure

```bash
cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Put the generated value in `SESSION_SECRET`. For HTTPS set `COOKIE_SECURE=1` and put Py2APK behind a TLS reverse proxy.

## 3. Build and run

```bash
docker compose build
docker compose up -d
```

The first build image creation downloads Android SDK/NDK components and can take a while.

## 4. Reverse proxy

Expose only the reverse proxy publicly. Keep port 8080 bound to localhost or an internal Docker network when using a proxy. Forward normal HTTP requests and disable response buffering for `/api/builds/*/logs` so Server-Sent Events remain live.

## 5. Docker security

The application launches one fresh builder container per build with no network, CPU/RAM/PID limits, dropped capabilities and a temporary filesystem. The application itself needs access to the Docker socket to create those containers. A Docker socket grants powerful host control, so for an internet-facing service use a dedicated build host/VM or a restricted Docker daemon instead of sharing the main server daemon.

## 6. Upload and cleanup policy

Uploads are capped by `MAX_UPLOAD_MB`. ZIP extraction rejects traversal paths, symbolic links, excessive file counts and large expanded archives. Temporary source/build directories are removed after a build. Completed artifacts are removed after `RETENTION_DAYS`.

## 7. Android packaging

The builder uses Buildozer with python-for-android. Projects should provide a `main.py` entry point and Android-compatible requirements. Packages with native extensions need a python-for-android recipe or compatible dependency.

Debug APKs are produced by default. For Google Play distribution, add a separate controlled signing/release pipeline rather than storing signing keys in uploaded projects or the build container.

## 8. Operations

Useful commands:

```bash
docker compose ps
docker compose logs -f py2apk
docker image ls py2apk-builder
docker compose down
```

Back up `data/py2apk.sqlite3` and your artifact storage according to your retention policy.
