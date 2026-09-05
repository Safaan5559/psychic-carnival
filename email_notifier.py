import asyncio
import os
import smtplib
from email.message import EmailMessage

from db import all_rows, execute, init_db, now

SMTP_HOST=os.getenv("SMTP_HOST","")
SMTP_PORT=int(os.getenv("SMTP_PORT","587"))
SMTP_USER=os.getenv("SMTP_USER","")
SMTP_PASSWORD=os.getenv("SMTP_PASSWORD","")
SMTP_FROM=os.getenv("SMTP_FROM",SMTP_USER)
BASE_URL=os.getenv("BASE_URL","http://localhost:8080").rstrip("/")


def send_email(to, app_name, status, build_id, error=None):
    if not SMTP_HOST or not SMTP_FROM:
        return False
    msg=EmailMessage()
    msg["Subject"]=f"Py2APK build {status}: {app_name}"
    msg["From"]=SMTP_FROM
    msg["To"]=to
    body=f"Your Py2APK build for {app_name} is {status}.\n\nBuild ID: {build_id}\nDashboard: {BASE_URL}/build/{build_id}\n"
    if error: body += f"\nError: {error}\n"
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST,SMTP_PORT,timeout=20) as server:
        server.starttls()
        if SMTP_USER: server.login(SMTP_USER,SMTP_PASSWORD)
        server.send_message(msg)
    return True

async def loop():
    await init_db()
    while True:
        rows=await all_rows("SELECT builds.*, users.email FROM builds JOIN users ON users.id=builds.user_id WHERE builds.finished_at IS NOT NULL AND builds.notified_at IS NULL LIMIT 20")
        for row in rows:
            try:
                if send_email(row["email"],row["app_name"],row["status"],row["id"],row["error"]):
                    await execute("UPDATE builds SET notified_at=? WHERE id=?",(now(),row["id"]))
            except Exception as exc:
                print(f"notification error for {row['id']}: {exc}",flush=True)
        await asyncio.sleep(30)

if __name__=="__main__": asyncio.run(loop())
