import json
import os
import shutil
import uuid
import zipfile
from pathlib import Path

import tornado.web
import tornado.ioloop
import tornado.httpserver

from builder import start_build
from config import DATA_DIR, MAX_UPLOAD_MB, BASE_URL
from db import execute, init_db, one

PROJECTS = DATA_DIR / "projects"
PROJECTS.mkdir(parents=True, exist_ok=True)
MAX_BYTES = MAX_UPLOAD_MB * 1024 * 1024


def safe_zip_extract(data: bytes, destination: Path):
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        total = 0
        for info in zf.infolist():
            name = Path(info.filename)
            if name.is_absolute() or ".." in name.parts or info.filename.startswith("/"):
                raise ValueError("Unsafe ZIP path")
            if info.is_dir():
                continue
            total += info.file_size
            if total > MAX_BYTES * 4:
                raise ValueError("Expanded project is too large")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=1024 * 1024)


class Health(tornado.web.RequestHandler):
    async def get(self):
        self.set_header("Content-Type", "application/json")
        self.write({"ok": True, "service": "Py2APK Android builder API"})


class Builds(tornado.web.RequestHandler):
    async def post(self):
        if "file" not in self.request.files:
            self.set_status(400); self.write({"error": "file is required"}); return
        upload = self.request.files["file"][0]
        body = upload["body"]
        if len(body) > MAX_BYTES:
            self.set_status(413); self.write({"error": "project is too large"}); return

        build_id = uuid.uuid4().hex
        project = PROJECTS / build_id
        project.mkdir(parents=True)
        filename = Path(upload.get("filename", "project.py")).name
        try:
            if filename.lower().endswith(".zip"):
                safe_zip_extract(body, project)
            elif filename.lower().endswith(".py"):
                (project / "main.py").write_bytes(body)
            else:
                raise ValueError("Only .py and .zip projects are supported")

            if not (project / "main.py").exists():
                candidates = list(project.rglob("*.py"))
                if candidates:
                    shutil.copy2(candidates[0], project / "main.py")
                else:
                    raise ValueError("Project must contain a Python entry file")

            def arg(name, default):
                return self.get_body_argument(name, default).strip()

            app_name = arg("app_name", "Python App")[:64]
            package_name = arg("package_name", "com.example.pythonapp")
            version_name = arg("version_name", "1.0.0")[:32]
            version_code = int(arg("version_code", "1"))
            requirements = arg("requirements", "python3,kivy")[:512]
            if not package_name.replace(".", "").replace("_", "").isalnum() or not package_name.startswith("com."):
                raise ValueError("Use a valid package name such as com.example.app")
            if version_code < 1:
                raise ValueError("Version code must be positive")

            await execute("INSERT INTO builds(id,user_id,filename,status,created_at,package_name,app_name,version_name,version_code) VALUES(?,?,?,?,?,?,?,?,?)",
                          (build_id, None, filename, "queued", __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(), package_name, app_name, version_name, version_code))
            self.set_status(202)
            self.write({"id": build_id, "status": "queued", "message": "Build queued"})
            tornado.ioloop.IOLoop.current().spawn_callback(start_build, build_id, project, {
                "app_name": app_name, "package_name": package_name, "version_name": version_name,
                "version_code": version_code, "requirements": requirements
            })
        except Exception as exc:
            shutil.rmtree(project, ignore_errors=True)
            self.set_status(400); self.write({"error": str(exc)})


class BuildStatus(tornado.web.RequestHandler):
    async def get(self, build_id):
        row = await one("SELECT id,status,package_name,app_name,version_name,version_code,error,apk_path,finished_at,duration_seconds FROM builds WHERE id=?", (build_id,))
        if not row:
            self.set_status(404); self.write({"error": "build not found"}); return
        status = row["status"]
        mapped = "completed" if status == "success" else status
        response = {
            "id": row["id"], "status": mapped, "progress": 100 if mapped == "completed" else (0 if mapped == "queued" else 50),
            "message": "APK ready" if mapped == "completed" else (row["error"] or mapped),
            "package_name": row["package_name"], "app_name": row["app_name"],
            "version_name": row["version_name"], "version_code": row["version_code"],
            "duration_seconds": row["duration_seconds"]
        }
        if mapped == "completed": response["apk_url"] = f"{BASE_URL}/v1/builds/{build_id}/apk"
        self.write(response)


class APK(tornado.web.RequestHandler):
    async def get(self, build_id):
        row = await one("SELECT apk_path,app_name FROM builds WHERE id=? AND status='success'", (build_id,))
        if not row or not row["apk_path"] or not Path(row["apk_path"]).is_file():
            self.set_status(404); self.write({"error": "APK not available"}); return
        path = Path(row["apk_path"])
        self.set_header("Content-Type", "application/vnd.android.package-archive")
        self.set_header("Content-Disposition", f'attachment; filename="{row["app_name"]}.apk"')
        self.set_header("Content-Length", str(path.stat().st_size))
        with path.open("rb") as f:
            while chunk := f.read(1024 * 1024):
                self.write(chunk)
        await self.flush()


def make_app():
    return tornado.web.Application([
        (r"/health", Health),
        (r"/v1/builds", Builds),
        (r"/v1/builds/([A-Za-z0-9_-]+)", BuildStatus),
        (r"/v1/builds/([A-Za-z0-9_-]+)/apk", APK),
    ], max_body_size=MAX_BYTES + 1024 * 1024)


async def main():
    await init_db()
    server = tornado.httpserver.HTTPServer(make_app())
    server.listen(int(os.getenv("PORT", "8080")), address=os.getenv("HOST", "0.0.0.0"))
    print("Py2APK headless Android builder API listening on port 8080", flush=True)
    await tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    tornado.ioloop.IOLoop.current().run_sync(main)
