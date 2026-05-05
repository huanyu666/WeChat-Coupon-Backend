from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime
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
create_migration_archive = _runtime_migration.create_migration_archive
get_migration_runtime_data_dir = _runtime_migration.get_migration_runtime_data_dir


def _default_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = PROJECT_ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir / f"wx-runtime-backup-{timestamp}.tar.gz"


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup wx-coupon runtime data using the migration package format.")
    parser.add_argument("--output", default="", help="Output .tar.gz path. Defaults to backups/wx-runtime-backup-*.tar.gz.")
    parser.add_argument("--include-env", action="store_true", help="Also include project .env when present.")
    parser.add_argument(
        "--include-legacy",
        action="store_true",
        help="Also include legacy project-root runtime files. Default is runtime-data only.",
    )
    parser.add_argument("--runtime-dir", default="", help="Override runtime-data directory.")
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve() if args.output else _default_output_path().resolve()
    runtime_dir = Path(args.runtime_dir).expanduser().resolve() if args.runtime_dir else get_migration_runtime_data_dir()

    try:
        result = create_migration_archive(
            output_path,
            include_env=args.include_env,
            include_legacy=args.include_legacy,
            runtime_dir=runtime_dir,
            project_root=PROJECT_ROOT,
        )
    except MigrationError as exc:
        print(f"BACKUP_RUNTIME_DATA_FAILED {exc}", file=sys.stderr)
        return 1

    print(f"BACKUP_RUNTIME_DATA_OK path={result['archive_path']}")
    print(f"- runtime_data_dir={result['runtime_data_dir']}")
    print(f"- archive_size={result['archive_size']}")
    print(f"- file_count={result['runtime_stats']['files']}")
    print(f"- dir_count={result['runtime_stats']['dirs']}")
    print(f"- legacy_file_count={result['legacy_stats']['files']}")
    print(f"- legacy_included={str(args.include_legacy).lower()}")
    print(f"- env_included={str(result['env_included']).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
