from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

from .backup_core import PeriodicRunner, run_backup_once
from .env import load_env
from .logging import get_logger

 # logger will be initialized in main after env variables are loaded


def _load_config_file(explicit: Path | None) -> Dict[str, Any]:
    """Load configuration YAML/JSON.

    Precedence:
    1. Explicit --config path (if provided)
    2. BACKUP_CONFIG_PATH env var
    3. Local default 'backup_config.yaml'
    4. Fallback search up one level (monorepo root)
    """
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit)
    env_path = os.getenv("BACKUP_CONFIG_PATH")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path("backup_config.yaml"))
    candidates.append(Path(__file__).resolve().parent.parent.parent / "backup_config.yaml")
    seen: set[Path] = set()
    for p in candidates:
        if p in seen:
            continue
        seen.add(p)
        if p.exists():
            try:
                if yaml:
                    with p.open("r") as f:
                        data = yaml.safe_load(f) or {}
                else:
                    data = json.loads(p.read_text())
                if isinstance(data, dict):
                    return data
            except Exception:  # pragma: no cover
                pass
    return {}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Document Portal Backup Service (YAML-config driven)")
    p.add_argument("--config", help="Path to backup_config.yaml (overrides BACKUP_CONFIG_PATH)")
    # Optional overrides (CLI > YAML)
    p.add_argument("--bucket", help="S3 bucket (override YAML)")
    p.add_argument("--prefix", help="S3 key prefix (override YAML)")
    p.add_argument("--dirs", help="Comma separated list of source directories (override YAML)")
    p.add_argument("--interval", type=int, help="Seconds between runs (override YAML; 0/absent = single run)")
    p.add_argument("--manifest", default=os.getenv("BACKUP_MANIFEST", ".backup_manifest.json"))
    p.add_argument("--no-incremental", action="store_true", help="Disable incremental mode (per-file uploads)")
    p.add_argument("--archive", action="store_true", help="Create a tar.gz snapshot instead of per-file upload")
    p.add_argument("--once", action="store_true", help="Single run then exit even if interval > 0")
    return p.parse_args()


def main() -> int:
    # Load env first so LOG_LEVEL and any bundled secrets apply before logger creation
    load_env(required=None, bundle_env="API_KEYS")
    log = get_logger("cli")
    args = parse_args()
    cfg = _load_config_file(Path(args.config) if args.config else None)
    s3_cfg = cfg.get("backup", {}).get("s3", {}) if isinstance(cfg, dict) else {}

    # Resolve core parameters (CLI > YAML)
    bucket = args.bucket or s3_cfg.get("bucket")
    if not bucket:
        log.error("missing_bucket", extra={"source": "yaml_or_cli"})
        return 2
    prefix = args.prefix or s3_cfg.get("prefix", "backups/")
    include_dirs: list[str]
    if args.dirs:
        include_dirs = [d.strip() for d in args.dirs.split(",") if d.strip()]
    else:
        include_dirs = [str(d) for d in s3_cfg.get("include_dirs", [])]
    if not include_dirs:
        # Fallback default if YAML empty
        include_dirs = ["/app/data", "/app/logs"]

    # Interval resolution
    interval = args.interval if args.interval is not None else int(s3_cfg.get("interval_seconds", 0) or 0)

    # Mode flags
    incremental = not args.no_incremental and not args.archive

    # Early S3 connectivity / permission check
    if bucket:
        try:
            import boto3  # local import to avoid unused at parse time
            s3_client = boto3.client("s3")
            s3_client.head_bucket(Bucket=bucket)
            log.info("s3_bucket_check", extra={"bucket": bucket, "status": "ok"})
        except Exception as e:  # noqa: BLE001
            log.error("s3_bucket_check_failed", extra={"bucket": bucket, "error": str(e)})
            return 4

    run_backup_once(
        bucket=bucket,
        prefix=prefix,
        include_dirs=include_dirs,
        manifest_path=args.manifest,
        incremental=incremental,
        archive=args.archive,
    )

    if args.once or interval <= 0:
        return 0

    runner = PeriodicRunner(
        bucket,
        prefix,
        include_dirs,
        manifest_path=args.manifest,
        incremental=incremental,
        archive=args.archive,
        interval=interval,
    )
    try:
        runner.start()
    except KeyboardInterrupt:  # pragma: no cover
        log.info("received_interrupt")
        runner.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
