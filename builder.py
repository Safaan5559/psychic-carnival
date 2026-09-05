import asyncio
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from config import BUILD_DIR, BUILD_CPU, BUILD_MEMORY, BUILD_PIDS, BUILDER_IMAGE, MAX_BUILD_SECONDS, MAX_LOG_BYTES, MAX_CONCURRENT_BUILDS
from db import execute, one, now

POOL = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BUILDS)
SESSIONS = {}


def safe_env(value, default=""):
    return re.sub(r"[^A-Za-z0-9_.,+\- ]", "", value or default)


def emit(build_id, line):
    session = SESSIONS.get(build_id)
    if not session:
        return
    session["lines"].append(line.rstrip("\n"))
    if len(session["lines"]) > 2000:
        session["lines"] = session["lines"][-2000:]
    for q in list(session["queues"]):
        try:
            q.put_nowait(line.rstrip("\n"))
        except asyncio.QueueFull:
            pass


def build_sync(build_id, project_dir, metadata):
    work = BUILD_DIR / build_id
    out = work / "out"
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(exist_ok=True)
    log_path = work / "build.log"
    started = time.monotonic()
    cmd = [
        "docker", "run", "--rm", "--network", "none",
        "--cpus", BUILD_CPU, "--memory", BUILD_MEMORY, "--pids-limit", BUILD_PIDS,
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp:rw,nosuid,nodev",
        "-e", f"PY2APK_APP_NAME={safe_env(metadata['app_name'])}",
        "-e", f"PY2APK_PACKAGE={metadata['package_name']}",
        "-e", f"PY2APK_VERSION={metadata['version_name']}",
        "-e", f"PY2APK_VERSION_CODE={metadata['version_code']}",
        "-e", f"PY2APK_ICON={metadata.get('icon_name','')}",
        "-e", f"PY2APK_SPLASH={metadata.get('splash_name','')}",
        "-e", f"PY2APK_REQUIREMENTS={safe_env(metadata.get('requirements','python3,kivy'))}",
        "-v", f"{project_dir}:/workspace/app:rw",
        "-v", f"{out}:/workspace/out:rw",
        BUILDER_IMAGE,
    ]
    session = SESSIONS[build_id]
    try:
        execute_sync = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        with log_path.open("w", encoding="utf-8") as log:
            for line in execute_sync.stdout:
                if time.monotonic() - started > MAX_BUILD_SECONDS:
                    execute_sync.kill()
                    emit(build_id, "[Py2APK] Build timed out and was terminated.")
                    break
                if log.tell() < MAX_LOG_BYTES:
                    log.write(line)
                emit(build_id, line)
            code = execute_sync.wait(timeout=10)
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
        session["done"] = True


async def start_build(build_id, project_dir, metadata):
    await execute("UPDATE builds SET status=?,started_at=? WHERE id=?", ("building", now(), build_id))
    SESSIONS[build_id] = {"lines": [], "queues": set(), "done": False}
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(POOL, build_sync, build_id, str(project_dir), metadata)


def session_for(build_id):
    return SESSIONS.get(build_id)


def remove_session(build_id):
    SESSIONS.pop(build_id, None)
