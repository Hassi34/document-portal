from __future__ import annotations

import hashlib
import json
import os
import tarfile
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

import boto3
from botocore.config import Config as BotoConfig

from .logging import get_logger

log = get_logger("core")


def _client():
    return boto3.client("s3", config=BotoConfig(retries={"max_attempts": 5, "mode": "standard"}))


def _iter_files(dirs: Iterable[Path]):
    """Yield (root_dir, file_path) pairs for all files under provided roots.

    root_dir is always one of the include_dirs entries (resolved). The file_path
    may equal root_dir when the include directory itself is a single file.
    """
    for root in dirs:
        if not root.exists():
            continue
        if root.is_file():
            yield root, root
        else:
            for p in root.rglob("*"):
                if p.is_file():
                    yield root, p


def _rel(base: Path, p: Path) -> str:
    try:
        return str(p.relative_to(base))
    except Exception:
        return p.name


def _hash_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path: Path) -> Dict[str, Dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_manifest(path: Path, data: Dict[str, Dict]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(path)


def run_backup_once(
    bucket: str,
    prefix: str,
    include_dirs: Iterable[str],
    manifest_path: Optional[str] = None,
    incremental: bool = True,
    archive: bool = False,
) -> int:
    start = time.time()
    raw_dirs = [Path(d) for d in include_dirs]

    mapped: list[Path] = []
    seen: set[Path] = set()
    for original in raw_dirs:
        resolved = original.resolve()
        if resolved.exists():
            mapped_path = resolved
            log.info("dir_present", extra={"path": str(mapped_path)})
        else:
            # Attempt fallback mapping for local dev: if absolute like /app/data -> ./data
            fallback_candidates: list[Path] = []
            if original.is_absolute():
                name = original.name
                fallback_candidates.append(Path.cwd() / name)  # ./data
                # If path like /app/data/logs we attempt relative chain
                try:
                    parts = list(original.parts)
                    if len(parts) > 2:
                        fallback_candidates.append(Path.cwd() / Path(*parts[2:]))
                except Exception:
                    pass
            # Also consider original relative form if user typed absolute but created relative
            if not original.is_absolute():
                fallback_candidates.append(resolved)
            mapped_path = None
            for cand in fallback_candidates:
                try:
                    if cand.exists():
                        mapped_path = cand.resolve()
                        log.info(
                            "dir_mapped",
                            extra={
                                "original": str(original),
                                "mapped_to": str(mapped_path),
                            },
                        )
                        break
                except Exception:
                    continue
            if mapped_path is None:
                # Create only if truly needed (avoid silently creating empty dirs that hide mapping issues)
                try:
                    resolved.mkdir(parents=True, exist_ok=True)
                    mapped_path = resolved
                    log.info("dir_created", extra={"path": str(mapped_path)})
                except Exception as e:  # noqa: BLE001
                    log.error("dir_create_failed", extra={"path": str(resolved), "error": str(e)})
                    continue
        if mapped_path and mapped_path not in seen:
            seen.add(mapped_path)
            mapped.append(mapped_path)
    dirs = mapped
    prefix = prefix.rstrip("/") + "/" if prefix else ""
    s3 = _client()
    manifest_file = Path(manifest_path) if manifest_path else None
    manifest = load_manifest(manifest_file) if (manifest_file and incremental and not archive) else {}

    # Pre-scan files for counting
    all_files = list(_iter_files(dirs))
    log.info(
        "scan_summary",
        extra={
            "dirs": len(dirs),
            "files_found": len(all_files),
            "bucket": bucket,
            "prefix": prefix,
            "mode": "archive" if archive else ("incremental" if incremental else "full"),
        },
    )
    if not all_files:
        log.warning(
            "scan_empty",
            extra={
                "dirs": [str(d) for d in dirs],
                "bucket": bucket,
                "prefix": prefix,
            },
        )

    if archive:
        ts = time.strftime("%Y%m%d_%H%M%S")
        archive_name = f"{prefix}snapshot_{ts}.tar.gz"
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = Path(tmp.name)
        with tarfile.open(tmp_path, "w:gz") as tar:
            for d in dirs:
                if d.exists():
                    tar.add(str(d), arcname=d.name)
        size = tmp_path.stat().st_size
        s3.upload_file(str(tmp_path), bucket, archive_name)
        tmp_path.unlink(missing_ok=True)
        log.info(
            "backup_archive_uploaded",
            extra={
                "key": archive_name,
                "size": size,
                "duration_ms": int((time.time() - start) * 1000),
                "files_included": len(all_files),
            },
        )
        return 1

    uploaded = 0
    new_manifest: Dict[str, Dict] = {}
    for root, p in all_files:
        # Compute relative key fragment: <root_basename>/<relative_path_inside_root>
        if p == root:
            rel = root.name  # single file include
        else:
            try:
                rel_inside = p.relative_to(root).as_posix()
            except Exception:
                rel_inside = p.name
            rel = f"{root.name}/{rel_inside}" if rel_inside else root.name
        stat = p.stat()
        entry = {"mtime": int(stat.st_mtime), "size": stat.st_size}
        need_upload = True
        if incremental and rel in manifest:
            old = manifest[rel]
            if old.get("mtime") == entry["mtime"] and old.get("size") == entry["size"]:
                need_upload = False
        if need_upload and incremental:
            entry["sha256"] = _hash_file(p)
        if need_upload:
            key = f"{prefix}{rel}".replace("\\", "/")
            try:
                s3.upload_file(str(p), bucket, key)
                uploaded += 1
                log.info("file_uploaded", extra={"key": key, "size": stat.st_size})
            except Exception as e:  # noqa: BLE001
                log.error("file_upload_failed", extra={"path": str(p), "error": str(e)})
        new_manifest[rel] = entry

    if manifest_file and incremental:
        save_manifest(manifest_file, new_manifest)

    log.info(
        "backup_completed",
        extra={
            "uploaded": uploaded,
            "total_tracked": len(new_manifest),
            "total_files_scanned": len(all_files),
            "duration_ms": int((time.time() - start) * 1000),
            "bucket": bucket,
            "prefix": prefix,
        },
    )
    return uploaded


class PeriodicRunner:
    def __init__(self, *args, interval: int, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.interval = interval
        self._stop = threading.Event()

    def start(self):  # pragma: no cover
        while not self._stop.is_set():
            try:
                run_backup_once(*self.args, **self.kwargs)
            except Exception as e:  # noqa: BLE001
                log.error("periodic_run_failed", extra={"error": str(e)})
            if self._stop.wait(self.interval):
                break

    def stop(self):  # pragma: no cover
        self._stop.set()
