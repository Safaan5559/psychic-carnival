import asyncio
import io
import json
import os
import re
import shutil
import zipfile
from pathlib import Path

from PIL import Image
import tornado.escape
import tornado.web

import config
from builder import start_build, session_for
from cleanup import cleanup_expired
from db import all_rows, execute, init_db, now, one
from security import hash_password, new_id, valid_email, valid_package, valid_version, verify_password


def json_body(handler):
    try:
        return json.loads(handler.request.body or b"{}")
    except json.JSONDecodeError:
        raise tornado.web.HTTPError(400, reason="Invalid JSON")


def uid(handler):
    value = handler.get_secure_cookie("user_id")
    return int(value.decode()) if value else None


def require_user(handler):
    value = uid(handler)
    if not value:
        raise tornado.web.HTTPError(401, reason="Authentication required")
    return value


def safe_zip_name(name):
    p = Path(name)
    return not p.is_absolute() and not name.startswith("/") and ".." not in p.parts


def scan_project(root):
    total = 0
    mains = []
    for p in root.rglob("*"):
        if p.is_symlink():
            raise ValueError("Symbolic links are not allowed")
        if p.is_file():
            total += p.stat().st_size
            if total > 100 * 1024 * 1024:
                raise ValueError("Expanded project exceeds 100 MB")
            if p.suffix.lower() in {".sh", ".bat", ".cmd", ".exe"}:
                raise ValueError("Executable shell files are not accepted")
            if p.name == "main.py":
                mains.append(p)
    if not mains:
        top = list(root.glob("*.py"))
        if len(top) == 1:
            top[0].rename(root / "main.py")
            mains = [root / "main.py"]
    if len(mains) != 1:
        raise ValueError("Project must contain exactly one main.py entry point")


def verify_image(body):
    try:
        with Image.open(io.BytesIO(body)) as image:
            image.verify()
    except Exception as exc:
        raise ValueError("Invalid image upload") from exc


class Base(tornado.web.RequestHandler):
    def write_json(self, data, status=200):
        self.set_status(status)
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps(data, default=str))


class Home(Base):
    async def get(self):
        if not uid(self):
            self.redirect("/login")
            return
        self.render("index.html")


class Login(Base):
    async def get(self):
        self.render("login.html", register=False)

    async def post(self):
        d = json_body(self)
        email = (d.get("email") or "").strip().lower()
        password = d.get("password") or ""
        row = await one("SELECT * FROM users WHERE email=?", (email,))
        if not row or not verify_password(row["password_hash"], password):
            self.write_json({"error": "Invalid email or password"}, 401)
            return
        self.set_secure_cookie("user_id", str(row["id"]), httponly=True, secure=config.COOKIE_SECURE, samesite="Lax")
        self.write_json({"ok": True})


class Register(Base):
    async def get(self):
        self.render("login.html", register=True)

    async def post(self):
        if not config.REGISTRATION_ENABLED:
            self.write_json({"error": "Registration is disabled"}, 403)
            return
        d = json_body(self)
        email = (d.get("email") or "").strip().lower()
        password = d.get("password") or ""
        if not valid_email(email) or not 10 <= len(password) <= 256:
            self.write_json({"error": "Use a valid email and a 10-256 character password"}, 400)
            return
        try:
            user = await execute("INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)", (email, hash_password(password), now()))
        except Exception:
            self.write_json({"error": "Account already exists"}, 409)
            return
        self.set_secure_cookie("user_id", str(user), httponly=True, secure=config.COOKIE_SECURE, samesite="Lax")
        self.write_json({"ok": True})


class Logout(Base):
    async def post(self):
        self.clear_cookie("user_id")
        self.write_json({"ok": True})


class Upload(Base):
    async def post(self):
        user = require_user(self)
        files = self.request.files.get("project") or []
        if not files:
            self.write_json({"error": "Project file is required"}, 400)
            return
        f = files[0]
        filename = Path(f["filename"]).name
        ext = Path(filename).suffix.lower()
        if ext not in {".py", ".zip"}:
            self.write_json({"error": "Only .py and .zip files are accepted"}, 400)
            return
        if len(f["body"]) > config.MAX_UPLOAD_MB * 1024 * 1024:
            self.write_json({"error": "Upload exceeds the size limit"}, 413)
            return
        build_id = new_id()
        root = config.UPLOAD_DIR / build_id
        root.mkdir(parents=True)
        try:
            if ext == ".py":
                (root / "main.py").write_bytes(f["body"])
            else:
                with zipfile.ZipFile(io.BytesIO(f["body"])) as z:
                    if len(z.infolist()) > 5000:
                        raise ValueError("ZIP contains too many files")
                    expanded = 0
                    for info in z.infolist():
                        if not safe_zip_name(info.filename):
                            raise ValueError("Unsafe ZIP path")
                        expanded += info.file_size
                        if expanded > 100 * 1024 * 1024:
                            raise ValueError("Expanded ZIP exceeds 100 MB")
                        target = root / info.filename
                        if info.is_dir():
                            target.mkdir(parents=True, exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True, exist_ok=True)
                            with z.open(info) as src, target.open("wb") as dst:
                                shutil.copyfileobj(src, dst, 1024 * 1024)
            scan_project(root)
            for key in ("icon", "splash"):
                item = (self.request.files.get(key) or [None])[0]
                if item:
                    if len(item["body"]) > 2 * 1024 * 1024:
                        raise ValueError(f"{key} is too large")
                    suffix = Path(item["filename"]).suffix.lower()
                    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                        raise ValueError(f"{key} must be an image")
                    verify_image(item["body"])
                    (root / f"__py2apk_{key}{suffix}").write_bytes(item["body"])
            app_name = (self.get_body_argument("app_name", "") or Path(filename).stem.replace("_", " ").title()).strip()
            package = (self.get_body_argument("package_name", "") or f"com.py2apk.{re.sub(r'[^a-z0-9]+','',Path(filename).stem.lower())[:24] or 'app'}").strip()
            version = (self.get_body_argument("version_name", "") or "1.0.0").strip()
            code = int(self.get_body_argument("version_code", "1"))
            requirements = self.get_body_argument("requirements", "") or "python3,kivy"
            if len(app_name) > 50 or not valid_package(package) or not valid_version(version) or not 1 <= code <= 2147483647:
                raise ValueError("Invalid app metadata")
            if not re.fullmatch(r"[A-Za-z0-9_.,+\- ]+", requirements):
                raise ValueError("Invalid requirements list")
            await execute("INSERT INTO builds(id,user_id,filename,status,created_at,package_name,app_name,version_name,version_code) VALUES(?,?,?,?,?,?,?,?,?)", (build_id, user, filename, "queued", now(), package, app_name, version, code))
            meta = {"app_name": app_name, "package_name": package, "version_name": version, "version_code": code, "requirements": requirements, "icon_name": next((p.name for p in root.glob("__py2apk_icon.*")), ""), "splash_name": next((p.name for p in root.glob("__py2apk_splash.*")), "")}
            (root / "__py2apk_meta.json").write_text(json.dumps(meta), encoding="utf-8")
            self.write_json({"build_id": build_id, "status": "queued"}, 201)
        except Exception as exc:
            shutil.rmtree(root, ignore_errors=True)
            self.write_json({"error": str(exc)}, 400)


async def get_build(handler, build_id):
    user = require_user(handler)
    row = await one("SELECT * FROM builds WHERE id=? AND user_id=?", (build_id, user))
    if not row:
        raise tornado.web.HTTPError(404, reason="Build not found")
    return row


class Start(Base):
    async def post(self, build_id):
        row = await get_build(self, build_id)
        if row["status"] not in {"queued", "failed"}:
            self.write_json({"error": "Build is not startable"}, 409)
            return
        root = config.UPLOAD_DIR / build_id
        if not root.exists():
            self.write_json({"error": "Build source has expired"}, 410)
            return
        meta = json.loads((root / "__py2apk_meta.json").read_text())
        asyncio.create_task(start_build(build_id, root, meta))
        self.write_json({"ok": True, "status": "building"})


class Status(Base):
    async def get(self, build_id):
        row = await get_build(self, build_id)
        data = dict(row)
        data["download_url"] = f"/api/builds/{build_id}/download" if row["status"] == "success" else None
        self.write_json(data)

    async def delete(self, build_id):
        row = await get_build(self, build_id)
        for value in (row["apk_path"], row["log_path"]):
            if value:
                Path(value).unlink(missing_ok=True)
        shutil.rmtree(config.UPLOAD_DIR / build_id, ignore_errors=True)
        shutil.rmtree(config.BUILD_DIR / build_id, ignore_errors=True)
        await execute("DELETE FROM builds WHERE id=?", (build_id,))
        self.write_json({"ok": True})


class Retry(Base):
    async def post(self, build_id):
        row = await get_build(self, build_id)
        if row["status"] not in {"failed", "success"}:
            self.write_json({"error": "Only completed builds can be retried"}, 409)
            return
        source = config.UPLOAD_DIR / build_id
        if not source.exists():
            self.write_json({"error": "Source expired"}, 410)
            return
        new = new_id()
        shutil.copytree(source, config.UPLOAD_DIR / new)
        await execute("INSERT INTO builds(id,user_id,filename,status,created_at,package_name,app_name,version_name,version_code) VALUES(?,?,?,?,?,?,?,?,?)", (new, row["user_id"], row["filename"], "queued", now(), row["package_name"], row["app_name"], row["version_name"], row["version_code"]))
        self.write_json({"build_id": new, "status": "queued"}, 201)


class Logs(Base):
    async def get(self, build_id):
        await get_build(self, build_id)
        self.set_header("Content-Type", "text/event-stream")
        self.set_header("Cache-Control", "no-cache")
        self.set_header("X-Accel-Buffering", "no")
        session = session_for(build_id)
        if not session:
            self.write("data: " + tornado.escape.json_encode({"line": "Build is queued or logs have expired."}) + "\n\n")
            await self.flush()
            return
        queue = asyncio.Queue(maxsize=200)
        session["queues"].add(queue)
        try:
            for line in session["lines"]:
                self.write("data: " + tornado.escape.json_encode({"line": line}) + "\n\n")
            await self.flush()
            while True:
                line = await queue.get()
                self.write("data: " + tornado.escape.json_encode({"line": line}) + "\n\n")
                await self.flush()
                if session["done"] and queue.empty():
                    break
        except (ConnectionError, asyncio.CancelledError, tornado.web.Finish):
            pass
        finally:
            session["queues"].discard(queue)


class Download(Base):
    async def get(self, build_id):
        row = await get_build(self, build_id)
        if row["status"] != "success" or not row["apk_path"]:
            raise tornado.web.HTTPError(404)
        path = Path(row["apk_path"])
        if not path.is_file():
            raise tornado.web.HTTPError(404)
        self.set_header("Content-Type", "application/vnd.android.package-archive")
        self.set_header("Content-Disposition", f'attachment; filename="{row["app_name"]}.apk"')
        self.set_header("Content-Length", str(path.stat().st_size))
        self.write(path.read_bytes())


class History(Base):
    async def get(self):
        user = require_user(self)
        page = max(1, int(self.get_query_argument("page", "1")))
        query = self.get_query_argument("q", "").strip()
        limit, offset = 20, (page - 1) * 20
        like = f"%{query}%"
        rows = await all_rows("SELECT * FROM builds WHERE user_id=? AND (filename LIKE ? OR app_name LIKE ? OR package_name LIKE ?) ORDER BY created_at DESC LIMIT ? OFFSET ?", (user, like, like, like, limit, offset))
        self.write_json({"items": [dict(r) for r in rows], "page": page, "has_more": len(rows) == limit})


class Page(Base):
    def initialize(self, template_name):
        self.template_name = template_name

    async def get(self):
        require_user(self)
        self.render(self.template_name)


class Health(Base):
    async def get(self):
        self.write_json({"ok": True, "service": "py2apk"})


def make_app():
    root = Path(__file__).parent
    return tornado.web.Application([
        (r"/", Home), (r"/login", Login), (r"/register", Register), (r"/logout", Logout),
        (r"/upload", Page, {"template_name": "index.html"}), (r"/history", Page, {"template_name": "history.html"}),
        (r"/settings", Page, {"template_name": "settings.html"}), (r"/build/([^/]+)", Page, {"template_name": "build.html"}),
        (r"/api/upload", Upload), (r"/api/builds/([^/]+)/start", Start), (r"/api/builds/([^/]+)/retry", Retry),
        (r"/api/builds/([^/]+)/logs", Logs), (r"/api/builds/([^/]+)/download", Download), (r"/api/builds/([^/]+)", Status),
        (r"/api/history", History), (r"/health", Health),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": str(root / "static")}),
    ], cookie_secret=config.SESSION_SECRET, template_path=str(root / "templates"), debug=False,
       max_body_size=config.MAX_UPLOAD_MB * 1024 * 1024 + 5 * 1024 * 1024, xsrf_cookies=False)


async def main():
    await init_db()
    await cleanup_expired()
    app = make_app()
    app.listen(int(os.getenv("PORT", "8080")), address=os.getenv("HOST", "0.0.0.0"))

    async def periodic_cleanup():
        while True:
            await asyncio.sleep(21600)
            try:
                await cleanup_expired()
            except Exception as exc:
                print(f"cleanup error: {exc}", flush=True)

    asyncio.create_task(periodic_cleanup())
    print("Py2APK listening on 8080", flush=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
