import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
import config
from db import all_rows, execute

async def cleanup_expired():
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.RETENTION_DAYS)).isoformat()
    rows = await all_rows("SELECT id,apk_path,log_path FROM builds WHERE finished_at IS NOT NULL AND finished_at < ?", (cutoff,))
    for row in rows:
        if row["apk_path"]: Path(row["apk_path"]).unlink(missing_ok=True)
        if row["log_path"]: Path(row["log_path"]).unlink(missing_ok=True)
        shutil.rmtree(config.UPLOAD_DIR / row["id"], ignore_errors=True)
        shutil.rmtree(config.BUILD_DIR / row["id"], ignore_errors=True)
        await execute("DELETE FROM builds WHERE id=?", (row["id"],))
