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


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a wx-coupon runtime migration package.")
    parser.add_argument("archive", help="Migration .tar.gz archive.")
    parser.add_argument("--target-dir", default="", help="Override runtime-data directory.")
    parser.add_argument("--restore-env", action="store_true", help="Restore .env from package when present.")
    parser.add_argument("--yes", action="store_true", help="Apply import. Without this flag only previews.")
    args = parser.parse_args()

    archive_path = Path(args.archive).expanduser().resolve()
    target_dir = Path(args.target_dir).expanduser().resolve() if args.target_dir else None
    try:
        result = restore_migration_archive(
            archive_path,
            target_dir=target_dir,
            project_root=PROJECT_ROOT,
            restore_env=args.restore_env,
            apply=args.yes,
        )
    except MigrationError as exc:
        print(f"IMPORT_MIGRATION_FAILED {exc}", file=sys.stderr)
        return 1

    if not args.yes:
        preview = result["preview"]
        print(f"IMPORT_MIGRATION_PREVIEW target={result['target_dir']}")
        print(f"- archive={preview['archive_path']}")
        print(f"- runtime_files={preview['runtime_file_count']}")
        print(f"- runtime_dirs={preview['runtime_dir_count']}")
        print(f"- runtime_total_size={preview['runtime_total_size']}")
        print(f"- env_included={str(preview['env_included']).lower()}")
        print("IMPORT_MIGRATION_DRY_RUN add --yes to apply")
        return 0

    print(f"IMPORT_MIGRATION_OK target={result['target_dir']}")
    if result.get("pre_import_backup_path"):
        print(f"- pre_import_backup={result['pre_import_backup_path']}")
    print(f"- env_restored={str(result.get('env_restored', False)).lower()}")
    print(f"- restart_required={str(result.get('restart_required', False)).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
