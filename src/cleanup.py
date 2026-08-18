"""Prune rendered artifacts (audio/video/thumbnails) once they're older than
the configured retention window so finished Shorts only live on YouTube.

Config (config/config.json):
    "cleanup": {
        "enabled": true,
        "retention_days": 7
    }

Called at the start of every pipeline run (cron fires 3x/day) and on demand
from the dashboard's Settings tab.
"""

import logging
import time
from pathlib import Path

log = logging.getLogger("cleanup")

OUTPUT_DIRS = ("audio_dir", "video_dir", "thumbnail_dir")


def cleanup_old_outputs(cfg: dict, paths: dict) -> int:
    cleanup = cfg.get("cleanup") or {}
    if not cleanup.get("enabled", True):
        log.info("Cleanup disabled, skipping.")
        return 0
    try:
        retention_days = float(cleanup.get("retention_days", 7))
    except (TypeError, ValueError):
        retention_days = 7.0
    cutoff = time.time() - retention_days * 86400
    deleted = 0
    freed = 0
    for key in OUTPUT_DIRS:
        d = Path(paths.get(key, ""))
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if not f.is_file():
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_mtime < cutoff:
                try:
                    freed += st.st_size
                    f.unlink(missing_ok=True)
                    log.info("Deleted %s (%d days old)", f.name, retention_days)
                    deleted += 1
                except OSError as e:
                    log.warning("Could not delete %s: %s", f, e)
    if deleted:
        log.info("Cleanup removed %d file(s), freed %.1f MB", deleted, freed / 1e6)
    return deleted