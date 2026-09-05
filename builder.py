import asyncio
import os
import re
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from config import BUILD_DIR, BUILD_CPU, BUILD_MEMORY, BUILD_PIDS, BUILDER_IMAGE, MAX_BUILD_SECONDS, MAX_LOG_BYTES, MAX_CONCURRENT_BUILDS
from db import execute, now

POOL = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BUILDS)
SESSIONS = {}
HOST_DATA_DIR = Path(os.getenv("HOST_DATA_DIR", "/srv/py2apk/data"))


def safe_env(value, default=""):
    return re.sub(r"[^A-Za-z0-9_.,+\- ]", "", value or default)


def host_path(container_path):
    path = Path(container_path)
    try:
        relative = path.relative_to(Path("/app/data"))
        return str(HOST_DATA_DIR / relative)
    except ValueError:
        raise RuntimeError("Build path is outside the configured data directory")


def emit(build_id, line):
    session = SESSIONS.get(build_id)
    if not session:
        return
    session["lines"].append(line.rstrip("\n"))
    session["lines"] = session["lines"][-2000:]
    for queue in list(session["queues"]):
        try:
            queue.put_nowait(line.rstrip("\n"))
        except asyncio.QueueFull:
            pass


def build_sync(build_id, project_dir, metadata):
    work = BUILD_DIR / build_id
    out = work / "out"
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(exist_ok=True)
    log_path = work / "build.log"
    started = time.monotonic()
    host_project = host_path(project_dir)
    host_out = host_path(out)
    cmd = [
        "docker", "run", "--rm", "--network", "none", "--cpus", BUILD_CPU,
        "--memory", BUILD_MEMORY, "--pids-limit", BUILD_PIDS, "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--tmpfs", "/tmp:rw,nosuid,nodev",
        "-e", f"PY2APK_APP_NAME={safe_env(metadata['app_name'])}",
        "-e", f"PY2APK_PACKAGE={metadata['package_name']}",
        "-e", f"PY2APK_VERSION={metadata['version_name']}",
        "-e", f"PY2APK_VERSION_CODE={metadata['version_code']}",
        "-e", f"PY2APK_ICON={metadata.get('icon_name','')}",
        "-e", f"PY2APK_SPLASH={metadata.get('splash_name','')}",
        "-e", f"PY2APK_REQUIREMENTS={safe_env(metadata.get('requirements','python3,kivy'))}",
        "--mount", f"type=bind,src={host_project},dst=/workspace/app",
        "--mount", f"type=bind,src={host_out},dst=/workspace/out",
        BUILDER_IMAGE,
    ]
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        with log_path.open("w", encoding="utf-8") as log:
            for line in process.stdout:
                if time.monotonic() - started > MAX_BUILD_SECONDS:
                    process.kill()
                    emit(build_id, "[Py2APK] Build timed out and was terminated.")
                    break
                if log.tell() < MAX_LOG_BYTES:
                    log.write(line)
                emit(build_id, line)
        code = process.wait(timeout=10)
        if code != 0:
            raise RuntimeError(f"builder exited with code {code}")
        apks = sorted(out.rglob("*.apk"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not apks:
            raise RuntimeError("Build finished without producing an APK")
        final_dir = BUILD_DIR / "artifacts"
        final_dir.mkdir(exist_ok=True)
        final_apk = final_dir / f"{build_id}.apk"
        shutil.copy2(apks[0], final_apk)
        duration = round(time.monotonic() - started, 2)
        asyncio.run(execute("UPDATE builds SET status=?,finished_at=?,duration_seconds=?,apk_path=?,log_path=? WHERE id=?", ("success", now(), duration, str(final_apk), str(log_path), build_id)))
        emit(build_id, f"[Py2APK] Build complete in {duration}s. APK: {final_apk.name}")
    except Exception as exc:
        duration = round(time.monotonic() - started, 2)
        asyncio.run(execute("UPDATE builds SET status=?,finished_at=?,duration_seconds=?,log_path=?,error=? WHERE id=?", ("failed", now(), duration, str(log_path), str(exc), build_id)))
        emit(build_id, f"[Py2APK] ERROR: {exc}")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(project_dir, ignore_errors=True)
        session = SESSIONS.get(build_id)
        if session:
            session["done"] = True


async def start_build(build_id, project_dir, metadata):
    await execute("UPDATE builds SET status=?,started_at=? WHERE id=?", ("building", now(), build_id))
    SESSIONS[build_id] = {"lines": [], "queues": set(), "done": False}
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(POOL, build_sync, build_id, str(project_dir), metadata)


def session_for(build_id):
    return SESSIONS.get(build_id)
