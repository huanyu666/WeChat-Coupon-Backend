from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import sys
import tarfile
from datetime import datetime
from pathlib import Path, PurePosixPath

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_path_utils():
    module_path = PROJECT_ROOT / "utils" / "path_utils.py"
    spec = importlib.util.spec_from_file_location("wx_path_utils", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载路径工具: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_path_utils = _load_path_utils()
HOST_RUNTIME_DATA_DIR = PROJECT_ROOT / "runtime-data"


def get_restore_runtime_data_dir() -> Path:
    custom_dir = os.getenv("WX_BACKUP_DATA_DIR", "").strip()
    if custom_dir:
        return Path(custom_dir).expanduser().resolve()
    return HOST_RUNTIME_DATA_DIR.resolve()


def _safe_relative_path(member_name: str) -> tuple[str, Path] | None:
    path = PurePosixPath(member_name)
    parts = path.parts
    if len(parts) < 2 or parts[0] not in {"runtime_data", "legacy_project_root"}:
        return None
    source_name = parts[0]
    relative = PurePosixPath(*parts[1:])
    if not str(relative) or relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"备份包包含不安全路径: {member_name}")
    return source_name, Path(*relative.parts)


def _select_members(tar: tarfile.TarFile) -> list[tuple[tarfile.TarInfo, Path]]:
    selected_by_path: dict[Path, tuple[int, tarfile.TarInfo]] = {}
    for member in tar.getmembers():
        resolved = _safe_relative_path(member.name)
        if resolved is None:
            continue
        source_name, relative_path = resolved
        if not (member.isdir() or member.isfile()):
            raise ValueError(f"备份包包含不支持的条目类型: {member.name}")
        priority = 2 if source_name == "runtime_data" else 1
        current = selected_by_path.get(relative_path)
        if current is None or priority > current[0]:
            selected_by_path[relative_path] = (priority, member)
    return [
        (member, relative_path)
        for relative_path, (_, member) in sorted(
            selected_by_path.items(),
            key=lambda item: (len(item[0].parts), str(item[0])),
        )
    ]


def _backup_existing(target_dir: Path, relative_roots: set[Path]) -> Path | None:
    existing_roots = [target_dir / root for root in sorted(relative_roots) if (target_dir / root).exists()]
    if not existing_roots:
        return None

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = PROJECT_ROOT / "backups" / f"pre-restore-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    for source in existing_roots:
        destination = backup_dir / source.name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return backup_dir


def _restore_members(tar: tarfile.TarFile, selected: list[tuple[tarfile.TarInfo, Path]], target_dir: Path) -> None:
    for member, relative_path in selected:
        target_path = target_dir / relative_path
        if member.isdir():
            target_path.mkdir(parents=True, exist_ok=True)
            continue

        source_file = tar.extractfile(member)
        if source_file is None:
            raise ValueError(f"无法读取备份条目: {member.name}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with source_file, target_path.open("wb") as output_file:
            shutil.copyfileobj(source_file, output_file)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", help="backup_runtime_data.py 生成的 .tar.gz 文件")
    parser.add_argument("--target-dir", default="")
    parser.add_argument("--yes", action="store_true", help="确认执行恢复；不加时只预览")
    args = parser.parse_args()

    archive_path = Path(args.archive).expanduser().resolve()
    if not archive_path.exists():
        print(f"RESTORE_RUNTIME_DATA_FAILED archive_not_found path={archive_path}", file=sys.stderr)
        return 1

    target_dir = Path(args.target_dir).expanduser().resolve() if args.target_dir else get_restore_runtime_data_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            selected = _select_members(tar)
            if not selected:
                print("RESTORE_RUNTIME_DATA_SKIPPED no_runtime_data_entries_found", file=sys.stderr)
                return 1

            print(f"RESTORE_RUNTIME_DATA_TARGET {target_dir}")
            for member, relative_path in selected:
                print(f"- {relative_path} <= {member.name}")

            if not args.yes:
                print("RESTORE_RUNTIME_DATA_DRY_RUN add --yes to apply")
                return 0

            top_level_roots = {Path(relative_path.parts[0]) for _, relative_path in selected if relative_path.parts}
            backup_dir = _backup_existing(target_dir, top_level_roots)
            _restore_members(tar, selected, target_dir)
    except Exception as exc:
        print(f"RESTORE_RUNTIME_DATA_FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    if backup_dir is not None:
        print(f"RESTORE_RUNTIME_DATA_PREVIOUS_BACKUP {backup_dir}")
    print("RESTORE_RUNTIME_DATA_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
