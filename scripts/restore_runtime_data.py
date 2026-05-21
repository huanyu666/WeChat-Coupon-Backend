from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_runtime_migration():
    module_path = PROJECT_ROOT / "utils" / "runtime_migration.py"
    spec = importlib.util.spec_from_file_location("wx_runtime_migration", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载迁移工具: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_runtime_migration = _load_runtime_migration()
MigrationError = _runtime_migration.MigrationError
restore_migration_archive = _runtime_migration.restore_migration_archive
get_migration_runtime_data_dir = _runtime_migration.get_migration_runtime_data_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore wx-coupon runtime data from a migration package.")
    parser.add_argument("archive", help="Runtime backup or migration .tar.gz archive.")
    parser.add_argument("--target-dir", default="", help="Override runtime-data directory.")
    parser.add_argument("--restore-env", action="store_true", help="Restore .env from package when present.")
    parser.add_argument("--restore-redis-shortlinks", action="store_true", help="Restore Redis shortlink mappings when present.")
    parser.add_argument("--yes", action="store_true", help="Apply restore; without this flag only previews.")
    args = parser.parse_args()

    archive_path = Path(args.archive).expanduser().resolve()
    if not archive_path.exists():
        print(f"RESTORE_RUNTIME_DATA_FAILED archive_not_found path={archive_path}", file=sys.stderr)
        return 1

    target_dir = Path(args.target_dir).expanduser().resolve() if args.target_dir else get_migration_runtime_data_dir()

    try:
        result = restore_migration_archive(
            archive_path,
            target_dir=target_dir,
            project_root=PROJECT_ROOT,
            restore_env=args.restore_env,
            restore_redis_shortlinks=args.restore_redis_shortlinks,
            apply=args.yes,
        )
    except MigrationError as exc:
        print(f"RESTORE_RUNTIME_DATA_FAILED {exc}", file=sys.stderr)
        return 1

    if not args.yes:
        preview = result["preview"]
        print(f"RESTORE_RUNTIME_DATA_TARGET {result['target_dir']}")
        print(f"- archive={preview['archive_path']}")
        print(f"- runtime_files={preview['runtime_file_count']}")
        print(f"- runtime_dirs={preview['runtime_dir_count']}")
        print(f"- runtime_total_size={preview['runtime_total_size']}")
        print(f"- env_included={str(preview['env_included']).lower()}")
        print(f"- redis_shortlinks_included={str(preview.get('redis_shortlinks_included', False)).lower()}")
        print(f"- deployment_snapshot_included={str(preview.get('deployment_snapshot_included', False)).lower()}")
        print("RESTORE_RUNTIME_DATA_DRY_RUN add --yes to apply")
        return 0

    if result.get("pre_import_backup_path"):
        print(f"RESTORE_RUNTIME_DATA_PREVIOUS_BACKUP {result['pre_import_backup_path']}")
    print(f"RESTORE_RUNTIME_DATA_OK target={result['target_dir']}")
    print(f"- env_restored={str(result.get('env_restored', False)).lower()}")
    print(f"- redis_shortlinks_restored={str(result.get('redis_shortlinks_restored', False)).lower()}")
    print(f"- restart_required={str(result.get('restart_required', False)).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
