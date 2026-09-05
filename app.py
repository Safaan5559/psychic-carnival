import asyncio
import json
import mimetypes
import os
import re
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

import tornado.escape
import tornado.ioloop
import tornado.web

import config
from builder import start_build, session_for
from db import all_rows, execute, init_db, now, one
from security import hash_password, new_id, valid_email, valid_package, valid_version, verify_password


def json_body(handler):
    try:
        return json.loads(handler.request.body or b"{}")
    except json.JSONDecodeError:
        raise tornado.web.HTTPError(400, reason="Invalid JSON")


def user_id(handler):
    value = handler.get_secure_cookie("user_id")
    return int(value.decode()) if value else None


def require_user(handler):
    uid = user_id(handler)
    if not uid:
        raise tornado.web.HTTPError(401, reason="Authentication required")
    return uid


def validate_zip_member(name):
    p = Path(name)
    return not p.is_absolute() and ".." not in p.parts and not name.startswith("/")


def scan_project(root):
    total = 0
    for p in root.rglob("*"):
        if p.is_symlink():
            raise ValueError("Symbolic links are not allowed")
        if p.is_file():
            total += p.stat().st_size
            if total > config.MAX_UPLOAD_MB * 2 * 1024 * 1024:
                raise ValueError("Expanded project is too large")
            if p.suffix.lower() in {".sh", ".bat", ".cmd", ".exe"}:
                raise ValueError("Executable shell/binary files are not accepted in uploads")
    mains = list(root.rglob("main.py"))
    if not mains:
        py = list(root.glob("*.py"))
        if len(py) == 1:
            py[0].rename(root / "main.py")
            mains = [root / "main.py"]
    if not mains:
        raise ValueError("Project must contain main.py or exactly one top-level Python file")
    if len(mains) > 1:
        raise ValueError("Project must contain one main.py entry point")
    return mains[0]


class BaseHandler(tornado.web.RequestHandler):
    def write_json(self, data, status=200):
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps(data, default=str))

    def get_template_namespace(self):
        return {"app_title": config.APP_TITLE, "current_user": user_id(self)}


class HomeHandler(BaseHandler):
    async def get(self):
        self.render("index.html")


class LoginHandler(BaseHandler):
    async def get(self):
        self.render("login.html", register=False)

    async def post(self):
        data = json_body(self)
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        row = await one("SELECT * FROM users WHERE email=?", (email,))
        if not row or not verify_password(row["password_hash"], password):
            self.write_json({"error": "Invalid email or password"}, 401)
            return
        self.set_secure_cookie("user_id", str(row["id"]), httponly=True, secure=config.COOKIE_SECURE, samesite="Lax")
        self.write_json({"ok": True})


class RegisterHandler(BaseHandler):
    async def get(self):
        self.render("login.html", register=True)

    async def post(self):
        if not config.REGISTRATION_ENABLED:
            self.write_json({"error": "Registration is disabled"}, 403)
            return
        data = json_body(self)
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""
        if not valid_email(email) or len(password) < 10 or len(password) > 256:
            self.write_json({"error": "Use a valid email and a password of 10-256 characters"}, 400)
            return
        try:
            uid = await execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)", (email, hash_password(password), now()))
        except Exception:
            self.write_json({"error": "An account with that email already exists"}, 409)
            return
        self.set_secure_cookie("user_id", str(uid), httponly=True, secure=config.COOKIE_SECURE, samesite="Lax")
        self.write_json({"ok": True})


class LogoutHandler(BaseHandler):
    async def post(self):
        self.clear_cookie("user_id")
        self.write_json({"ok": True})


class UploadHandler(BaseHandler):
    async def post(self):
        uid = require_user(self)
        files = self.request.files.get("project") or []
        if not files:
            self.write_json({"error": "Project file is required"}, 400); return
        project = files[0]
        filename = Path(project["filename"]).name
        ext = Path(filename).suffix.lower()
        if ext not in {".py", ".zip"}:
            self.write_json({"error": "Only .py and .zip files are accepted"}, 400); return
        if len(project["body"]) > config.MAX_UPLOAD_MB * 1024 * 1024:
            self.write_json({"error": "Upload exceeds the size limit"}, 413); return
        build_id = new_id()
        root = config.UPLOAD_DIR / build_id
        root.mkdir(parents=True)
        try:
            if ext == ".py":
                (root / "main.py").write_bytes(project["body"])
            else:
                with zipfile.ZipFile(io_bytes := __import__('io').BytesIO(project["body"])) as zf:
                    if len(zf.infolist()) > 5000:
                        raise ValueError("ZIP contains too many files")
                    for info in zf.infolist():
                        if not validate_zip_member(info.filename):
                            raise ValueError("Unsafe ZIP path")
                        target = root / info.filename
                        if info.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(info) as src, target.open("wb") as dst:
                                shutil.copyfileobj(src, dst, length=1024 * 1024)
            main = scan_project(root)
            for key in ("icon", "splash"):
                upload = (self.request.files.get(key) or [None])[0]
                if upload:
                    if len(upload["body"]) > 2 * 1024 * 1024:
                        raise ValueError(f"{key} is too large")
                    suffix = Path(upload["filename"]).suffix.lower()
                    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                        raise ValueError(f"{key} must be an image")
                    (root / f"__py2apk_{key}{suffix}").write_bytes(upload["body"])
            data = {k: self.get_body_argument(k, default="") for k in ("app_name", "package_name", "version_name", "version_code", "requirements")}
            data["app_name"] = data["app_name"] or Path(filename).stem.replace("_", " ").title()
            data["package_name"] = data["package_name"] or f"com.py2apk.{re.sub(r'[^a-z0-9]+','',Path(filename).stem.lower())[:24] or 'app'}"
            data["version_name"] = data["version_name"] or "1.0.0"
            data["version_code"] = int(data["version_code"] or "1")
            if not valid_package(data["package_name"]) or not valid_version(data["version_name"]):
                raise ValueError("Invalid package or version")
            if data["version_code"] < 1 or data["version_code"] > 2147483647:
                raise ValueError("Invalid version code")
            if len(data["app_name"]) > 50:
                raise ValueError("App name is too long")
            await execute("INSERT INTO builds(id,user_id,filename,status,created_at,package_name,app_name,version_name,version_code) VALUES(?,?,?,?,?,?,?,?,?)", (build_id, uid, filename, "queued", now(), data["package_name"], data["app_name"], data["version_name"], data["version_code"]))
            meta = {
                "app_name": data["app_name"], "package_name": data["package_name"], "version_name": data["version_name"], "version_code": data["version_code"],
                "requirements": data["requirements"] or "python3,kivy",
                "icon_name": next((p.name for p in root.glob("__py2apk_icon.*")), ""),
                "splash_name": next((p.name for p in root.glob("__py2apk_splash.*")), ""),
            }
            (root / "__py2apk_meta.json").write_text(json.dumps(meta), encoding="utf-8")
            self.write_json({"build_id": build_id, "status": "queued"}, 201)
        except Exception as exc:
            shutil.rmtree(root, ignore_errors=True)
            self.write_json({"error": str(exc)}, 400)


class BuildStartHandler(BaseHandler):
    async def post(self, build_id):
        uid = require_user(self)
        row = await one("SELECT * FROM builds WHERE id=? AND user_id=?", (build_id, uid))
        if not row:
            self.write_json({"error": "Build not found"}, 404); return
        if row["status"] not in {"queued", "failed"}:
            self.write_json({"error": "Build cannot be started from its current state"}, 409); return
        root = config.UPLOAD_DIR / build_id
        if not root.exists():
            self.write_json({"error": "Build source is no longer available"}, 410); return
        meta_path = root / "__py2apk_meta.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        asyncio.create_task(start_build(build_id, root, {"app_name": row["app_name"], "package_name": row["package_name"], "version_name": row["version_name"], "version_code": row["version_code"], **meta}))
        self.write_json({"ok": True, "status": "building"})


class BuildStatusHandler(BaseHandler):
    async def get(self, build_id):
        uid = require_user(self)
        row = await one("SELECT * FROM builds WHERE id=? AND user_id=?", (build_id, uid))
        if not row: self.write_json({"error": "Not found"}, 404); return
        data = dict(row)
        data["download_url"] = f"/api/builds/{build_id}/download" if row["status"] == "success" else None
        self.write_json(data)


class LogStreamHandler(BaseHandler):
    async def get(self, build_id):
        uid = require_user(self)
        row = await one("SELECT id FROM builds WHERE id=? AND user_id=?", (build_id, uid))
        if not row: raise tornado.web.HTTPError(404)
        self.set_header("Content-Type", "text/event-stream")
        self.set_header("Cache-Control", "no-cache")
        self.set_header("X-Accel-Buffering", "no")
        session = session_for(build_id)
        if not session:
            self.write("data: Build is queued or has already been cleaned up.\n\n"); await self.flush(); return
        q = asyncio.Queue(maxsize=200)
        session["queues"].add(q)
        try:
            for line in session["lines"]:
                self.write("data: " + tornado.escape.json_encode({"line": line}) + "\n\n")
            await self.flush()
            while True:
                line = await q.get()
                self.write("data: " + tornado.escape.json_encode({"line": line}) + "\n\n")
                await self.flush()
                if session["done"] and q.empty(): break
        except (tornado.web.Finish, ConnectionError, asyncio.CancelledError):
            pass
        finally:
            session["queues"].discard(q)


class DownloadHandler(BaseHandler):
    async def get(self, build_id):
        uid = require_user(self)
        row = await one("SELECT * FROM builds WHERE id=? AND user_id=?", (build_id, uid))
        if not row or row["status"] != "success": raise tornado.web.HTTPError(404)
        path = Path(row["apk_path"])
        if not path.is_file(): raise tornado.web.HTTPError(404)
        self.set_header("Content-Type", "application/vnd.android.package-archive")
        self.set_header("Content-Disposition", f'attachment; filename="{row["app_name"]}.apk"')
        self.set_header("Content-Length", str(path.stat().st_size))
        with path.open("rb") as f: self.write(f.read())


class HistoryHandler(BaseHandler):
    async def get(self):
        uid = require_user(self)
        page = max(1, int(self.get_query_argument("page", "1")))
        q = self.get_query_argument("q", "").strip()
        limit = 20; offset = (page - 1) * limit
        like = f"%{q}%"
        rows = await all_rows("SELECT * FROM builds WHERE user_id=? AND (filename LIKE ? OR app_name LIKE ? OR package_name LIKE ?) ORDER BY created_at DESC LIMIT ? OFFSET ?", (uid, like, like, like, limit, offset))
        self.write_json({"items": [dict(r) for r in rows], "page": page, "has_more": len(rows) == limit})

    async def render_page(self):
        self.render("history.html")


class DeleteHandler(BaseHandler):
    async def delete(self, build_id):
        uid = require_user(self)
        row = await one("SELECT * FROM builds WHERE id=? AND user_id=?", (build_id, uid))
        if not row: self.write_json({"error": "Not found"}, 404); return
        if row["apk_path"]: Path(row["apk_path"]).unlink(missing_ok=True)
        if row["log_path"]: Path(row["log_path"]).unlink(missing_ok=True)
        shutil.rmtree(config.UPLOAD_DIR / build_id, ignore_errors=True)
        shutil.rmtree(config.BUILD_DIR / build_id, ignore_errors=True)
        await execute("DELETE FROM builds WHERE id=?", (build_id,))
        self.write_json({"ok": True})


class RetryHandler(BaseHandler):
    async def post(self, build_id):
        uid = require_user(self)
        row = await one("SELECT * FROM builds WHERE id=? AND user_id=?", (build_id, uid))
        if not row: self.write_json({"error": "Not found"}, 404); return
        if row["status"] not in {"failed", "success"}: self.write_json({"error": "Only completed builds can be retried"}, 409); return
        new = new_id(); src = config.UPLOAD_DIR / build_id
        if not src.exists(): self.write_json({"error": "Source expired"}, 410); return
        shutil.copytree(src, config.UPLOAD_DIR / new)
        await execute("INSERT INTO builds(id,user_id,filename,status,created_at,package_name,app_name,version_name,version_code) VALUES(?,?,?,?,?,?,?,?,?)", (new, uid, row["filename"], "queued", now(), row["package_name"], row["app_name"], row["version_name"], row["version_code"]))
        self.write_json({"build_id": new, "status": "queued"}, 201)


class PageHandler(BaseHandler):
    def initialize(self, template_name): self.template_name = template_name
    async def get(self):
        require_user(self); self.render(self.template_name)


class HealthHandler(BaseHandler):
    async def get(self): self.write_json({"ok": True, "service": "py2apk"})


def make_app():
    settings = {
        "cookie_secret": config.SESSION_SECRET,
        "template_path": str(Path(__file__).parent / "templates"),
        "static_path": str(Path(__file__).parent / "static"),
        "debug": False,
        "max_body_size": config.MAX_UPLOAD_MB * 1024 * 1024 + 5 * 1024 * 1024,
        "xsrf_cookies": False,
    }
    return tornado.web.Application([
        (r"/", HomeHandler), (r"/login", LoginHandler), (r"/register", RegisterHandler), (r"/logout", LogoutHandler),
        (r"/upload", PageHandler, {"template_name": "index.html"}),
        (r"/history", PageHandler, {"template_name": "history.html"}),
        (r"/settings", PageHandler, {"template_name": "settings.html"}),
        (r"/build/(.*)", PageHandler, {"template_name": "build.html"}),
        (r"/api/upload", UploadHandler), (r"/api/builds/([^/]+)/start", BuildStartHandler),
        (r"/api/builds/([^/]+)", BuildStatusHandler), (r"/api/builds/([^/]+)/logs", LogStreamHandler),
        (r"/api/builds/([^/]+)/download", DownloadHandler), (r"/api/builds/([^/]+)", DeleteHandler),
        (r"/api/builds/([^/]+)/retry", RetryHandler), (r"/api/history", HistoryHandler),
        (r"/health", HealthHandler), (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": settings["static_path"]}),
    ], **settings)


async def main():
    await init_db()
    app = make_app(); app.listen(int(os.getenv("PORT", "8080")), address=os.getenv("HOST", "0.0.0.0"))
    print("Py2APK listening on port 8080", flush=True)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
