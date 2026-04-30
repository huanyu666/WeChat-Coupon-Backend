from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


MIGRATION_FORMAT = "wx-coupon-runtime-migration"
MIGRATION_SCHEMA_VERSION = 1
MIGRATION_MANIFEST_NAME = "migration_manifest.json"
LEGACY_BACKUP_MANIFEST_NAME = "backup_manifest.json"
MIGRATION_BACKUP_DIR_NAME = ".migration-backups"
MIGRATION_ARCHIVE_KINDS = ("exports", "imports", "pre-import")
DEFAULT_MAX_IMPORT_BYTES = 1024 * 1024 * 1024
LEGACY_RUNTIME_FILE_NAMES = {
    "config.toml",
    "wechat_accounts.runtime.json",
    "activation_codes.json",
    "activation_codes_link.json",
    "activation_codes_meituan_order.json",
    "scenes.json",
    "p_values.json",
    "order_leaderboard.db",
    "merchant_coupons.db",
}
LEGACY_RUNTIME_DIR_NAMES = {
    "merchant_coupons",
}


class MigrationError(RuntimeError):
    pass


def _load_path_utils():
    try:
        from utils import path_utils

        return path_utils
    except Exception:
        module_path = Path(__file__).resolve().parent / "path_utils.py"
        spec = importlib.util.spec_from_file_location("wx_path_utils", module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load path utils: {module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


_path_utils = _load_path_utils()
get_project_root = _path_utils.get_project_root
get_runtime_data_dir = _path_utils.get_runtime_data_dir


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _get_env_bytes(name: str, default: int) -> int:
    raw_value = os.getenv(name, "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default


def get_migration_runtime_data_dir() -> Path:
    custom_dir = os.getenv("WX_MIGRATION_DATA_DIR", "").strip() or os.getenv("WX_BACKUP_DATA_DIR", "").strip()
    if custom_dir:
        return Path(custom_dir).expanduser().resolve()

    if os.getenv("WX_SERVICE_DATA_DIR", "").strip() or os.getenv("STATE_DIRECTORY", "").strip():
        return get_runtime_data_dir().resolve()

    host_runtime_dir = get_project_root().resolve() / "runtime-data"
    if host_runtime_dir.exists():
        return host_runtime_dir.resolve()

    return get_runtime_data_dir().resolve()


def normalize_migration_archive_kind(kind: str) -> str:
    normalized = str(kind or "").strip()
    if normalized not in MIGRATION_ARCHIVE_KINDS:
        raise MigrationError(f"unsupported migration archive kind: {kind}")
    return normalized


def safe_migration_archive_filename(filename: str | None) -> str:
    base_name = Path(filename or "").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name).strip("._")
    if not cleaned:
        raise MigrationError("migration archive filename is empty")
    lower_name = cleaned.lower()
    if not (lower_name.endswith(".tar.gz") or lower_name.endswith(".tgz")):
        raise MigrationError("只支持 .tar.gz 或 .tgz 迁移包")
    return cleaned


def get_migration_archive_dir(kind: str = "exports", runtime_dir: Path | None = None) -> Path:
    kind = normalize_migration_archive_kind(kind)
    custom_dir = os.getenv("WX_MIGRATION_ARCHIVE_DIR", "").strip()
    if custom_dir:
        archive_dir = Path(custom_dir).expanduser().resolve() / kind
    else:
        project_root = get_project_root().resolve()
        data_dir = (runtime_dir or get_migration_runtime_data_dir()).resolve()
        try:
            data_dir.relative_to(project_root)
            archive_dir = project_root / "backups" / f"migration-{kind}"
        except ValueError:
            archive_dir = data_dir / MIGRATION_BACKUP_DIR_NAME / kind
    archive_dir.mkdir(parents=True, exist_ok=True)
    return archive_dir


def resolve_migration_archive(kind: str, filename: str, runtime_dir: Path | None = None) -> Path:
    archive_dir = get_migration_archive_dir(kind, runtime_dir=runtime_dir).resolve()
    raw_filename = str(filename or "").strip()
    if Path(raw_filename).name != raw_filename or "/" in raw_filename or "\\" in raw_filename:
        raise MigrationError(f"unsafe archive filename: {filename}")
    safe_name = safe_migration_archive_filename(filename)
    archive_path = (archive_dir / safe_name).resolve()
    try:
        archive_path.relative_to(archive_dir)
    except ValueError as exc:
        raise MigrationError(f"unsafe archive filename: {filename}") from exc
    if not archive_path.is_file():
        raise MigrationError(f"archive not found: {safe_name}")
    return archive_path


def get_default_export_path(include_env: bool = False, runtime_dir: Path | None = None) -> Path:
    suffix = "with-env" if include_env else "runtime"
    return get_migration_archive_dir("exports", runtime_dir=runtime_dir) / f"wx-coupon-migration-{suffix}-{_timestamp()}.tar.gz"


def get_max_import_bytes() -> int:
    return _get_env_bytes("WX_MIGRATION_MAX_IMPORT_BYTES", DEFAULT_MAX_IMPORT_BYTES)


def _should_skip_runtime_path(relative_path: Path) -> bool:
    return bool(relative_path.parts and relative_path.parts[0] == MIGRATION_BACKUP_DIR_NAME)


def _iter_runtime_paths(runtime_dir: Path) -> list[Path]:
    if not runtime_dir.exists():
        return []

    paths: list[Path] = []
    for root, dir_names, file_names in os.walk(runtime_dir):
        root_path = Path(root)
        relative_root = root_path.relative_to(runtime_dir)
        dir_names[:] = [
            name
            for name in sorted(dir_names)
            if not _should_skip_runtime_path(relative_root / name)
        ]
        for name in sorted(dir_names):
            paths.append(root_path / name)
        for name in sorted(file_names):
            path = root_path / name
            if not _should_skip_runtime_path(path.relative_to(runtime_dir)):
                paths.append(path)
    return paths


def runtime_data_has_content(runtime_dir: Path | None = None) -> bool:
    data_dir = (runtime_dir or get_migration_runtime_data_dir()).resolve()
    return any(True for _ in _iter_runtime_paths(data_dir))


def _is_sqlite_sidecar(path: Path) -> bool:
    if path.name.endswith("-wal") or path.name.endswith("-shm"):
        base_name = path.name.rsplit("-", 1)[0]
        return (path.parent / base_name).exists()
    return False


def _is_sqlite_candidate(path: Path) -> bool:
    return path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}


def _copy_sqlite_database(source: Path, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        source_uri = f"{source.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(source_uri, uri=True, timeout=10) as source_db:
            with sqlite3.connect(str(destination), timeout=10) as destination_db:
                source_db.backup(destination_db)
        shutil.copystat(source, destination, follow_symlinks=False)
        return True
    except Exception:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        return False


def _copy_runtime_tree(source_dir: Path, destination_dir: Path) -> dict[str, Any]:
    if not source_dir.exists():
        raise MigrationError(f"runtime data dir not found: {source_dir}")
    if not source_dir.is_dir():
        raise MigrationError(f"runtime data path is not a directory: {source_dir}")

    destination_dir.mkdir(parents=True, exist_ok=True)
    stats: dict[str, Any] = {
        "files": 0,
        "dirs": 0,
        "bytes": 0,
        "sqlite_backups": 0,
        "skipped": [],
    }

    for path in _iter_runtime_paths(source_dir):
        relative_path = path.relative_to(source_dir)
        target_path = destination_dir / relative_path
        if path.is_symlink():
            stats["skipped"].append({"path": relative_path.as_posix(), "reason": "symlink"})
            continue
        if _is_sqlite_sidecar(path):
            stats["skipped"].append({"path": relative_path.as_posix(), "reason": "sqlite-sidecar"})
            continue
        if path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            stats["dirs"] += 1
            continue
        if not path.is_file():
            stats["skipped"].append({"path": relative_path.as_posix(), "reason": "unsupported-type"})
            continue

        if _is_sqlite_candidate(path) and _copy_sqlite_database(path, target_path):
            stats["sqlite_backups"] += 1
        else:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target_path)

        stats["files"] += 1
        try:
            stats["bytes"] += target_path.stat().st_size
        except OSError:
            pass

    return stats


def _merge_copy_stats(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("files", "dirs", "bytes", "sqlite_backups"):
        target[key] = int(target.get(key, 0)) + int(source.get(key, 0))
    target.setdefault("skipped", [])
    target["skipped"].extend(source.get("skipped", []))


def _copy_runtime_file(source: Path, destination: Path, stats: dict[str, Any], display_path: str) -> None:
    if source.is_symlink():
        stats["skipped"].append({"path": display_path, "reason": "symlink"})
        return
    if _is_sqlite_sidecar(source):
        stats["skipped"].append({"path": display_path, "reason": "sqlite-sidecar"})
        return
    if not source.is_file():
        stats["skipped"].append({"path": display_path, "reason": "unsupported-type"})
        return

    if _is_sqlite_candidate(source) and _copy_sqlite_database(source, destination):
        stats["sqlite_backups"] += 1
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    stats["files"] += 1
    try:
        stats["bytes"] += destination.stat().st_size
    except OSError:
        pass


def _discover_legacy_runtime_items(project_root: Path, runtime_dir: Path) -> list[Path]:
    if not project_root.exists():
        return []

    items: list[Path] = []
    runtime_resolved = runtime_dir.resolve()
    for child in sorted(project_root.iterdir(), key=lambda item: item.name):
        try:
            child_resolved = child.resolve()
        except OSError:
            continue
        if child_resolved == runtime_resolved or runtime_resolved in child_resolved.parents:
            continue
        if child.is_dir() and child.name in LEGACY_RUNTIME_DIR_NAMES:
            items.append(child)
        elif child.is_file() and child.name in LEGACY_RUNTIME_FILE_NAMES:
            items.append(child)
    return items


def _copy_legacy_runtime_items(project_root: Path, runtime_dir: Path, destination_dir: Path) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "files": 0,
        "dirs": 0,
        "bytes": 0,
        "sqlite_backups": 0,
        "skipped": [],
    }
    seen_targets: set[Path] = set()
    for source in _discover_legacy_runtime_items(project_root, runtime_dir):
        target = destination_dir / source.name
        if target in seen_targets:
            continue
        seen_targets.add(target)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            stats["dirs"] += 1
            _merge_copy_stats(stats, _copy_runtime_tree(source, target))
        else:
            _copy_runtime_file(source, target, stats, source.name)
    return stats


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as input_file:
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect_archive_items(stage_dir: Path, root_name: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not stage_dir.exists():
        return items
    for path in sorted(stage_dir.rglob("*"), key=lambda item: item.relative_to(stage_dir).as_posix()):
        relative_path = path.relative_to(stage_dir).as_posix()
        if path.is_dir():
            items.append({"root": root_name, "path": relative_path, "type": "dir"})
        elif path.is_file():
            items.append({
                "root": root_name,
                "path": relative_path,
                "type": "file",
                "size": path.stat().st_size,
                "sha256": _sha256_file(path),
            })
    return items


def _write_json_to_tar(tar: tarfile.TarFile, name: str, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mode = 0o600
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(data))


def create_migration_archive(
    output_path: Path | str | None = None,
    *,
    include_env: bool = False,
    include_legacy: bool = False,
    runtime_dir: Path | str | None = None,
    project_root: Path | str | None = None,
) -> dict[str, Any]:
    root_dir = Path(project_root).expanduser().resolve() if project_root else get_project_root().resolve()
    data_dir = Path(runtime_dir).expanduser().resolve() if runtime_dir else get_migration_runtime_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        from utils.system_settings_store import ensure_system_settings_store

        ensure_system_settings_store()
    except Exception:
        pass

    archive_path = Path(output_path).expanduser().resolve() if output_path else get_default_export_path(include_env, data_dir)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="wx-coupon-migration-") as temp_name:
        temp_dir = Path(temp_name)
        stage_runtime_dir = temp_dir / "runtime_data"
        copy_stats = _copy_runtime_tree(data_dir, stage_runtime_dir)
        stage_legacy_dir = temp_dir / "legacy_project_root"
        legacy_stats: dict[str, Any] = {
            "files": 0,
            "dirs": 0,
            "bytes": 0,
            "sqlite_backups": 0,
            "skipped": [],
        }
        if include_legacy:
            legacy_stats = _copy_legacy_runtime_items(root_dir, data_dir, stage_legacy_dir)
        env_included = False
        env_path = root_dir / ".env"
        stage_env_path = temp_dir / "project_env" / ".env"
        if include_env and env_path.is_file():
            stage_env_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(env_path, stage_env_path)
            env_included = True

        archive_items = _collect_archive_items(stage_runtime_dir, "runtime_data")
        archive_items.extend(_collect_archive_items(stage_legacy_dir, "legacy_project_root"))

        manifest = {
            "format": MIGRATION_FORMAT,
            "schema_version": MIGRATION_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "project_root": str(root_dir),
            "runtime_data_dir": str(data_dir),
            "include_env_requested": include_env,
            "env_included": env_included,
            "runtime_stats": copy_stats,
            "include_legacy_requested": include_legacy,
            "legacy_stats": legacy_stats,
            "items": archive_items,
        }

        with tarfile.open(archive_path, "w:gz") as tar:
            _write_json_to_tar(tar, MIGRATION_MANIFEST_NAME, manifest)
            tar.add(stage_runtime_dir, arcname="runtime_data")
            if legacy_stats["files"] or legacy_stats["dirs"]:
                tar.add(stage_legacy_dir, arcname="legacy_project_root")
            if env_included:
                tar.add(stage_env_path, arcname="project_env/.env")

    archive_stat = archive_path.stat()
    return {
        "success": True,
        "archive_path": str(archive_path),
        "archive_name": archive_path.name,
        "archive_size": archive_stat.st_size,
        "created_at": manifest["created_at"],
        "runtime_data_dir": str(data_dir),
        "env_included": env_included,
        "runtime_stats": copy_stats,
        "legacy_stats": legacy_stats,
        "item_count": len(manifest["items"]),
    }


def _safe_member_path(member_name: str) -> tuple[str, Path] | None:
    if member_name in {MIGRATION_MANIFEST_NAME, LEGACY_BACKUP_MANIFEST_NAME}:
        return None

    posix_path = PurePosixPath(member_name)
    parts = posix_path.parts
    if not parts or posix_path.is_absolute() or ".." in parts:
        raise MigrationError(f"unsafe archive path: {member_name}")

    root_name = parts[0]
    if root_name not in {"runtime_data", "legacy_project_root", "project_env"}:
        raise MigrationError(f"unsupported archive root: {root_name}")

    if len(parts) == 1:
        return root_name, Path(".")
    relative = PurePosixPath(*parts[1:])
    if relative.is_absolute() or ".." in relative.parts:
        raise MigrationError(f"unsafe archive path: {member_name}")
    return root_name, Path(*relative.parts)


def _read_manifest(tar: tarfile.TarFile) -> dict[str, Any] | None:
    for name in (MIGRATION_MANIFEST_NAME, LEGACY_BACKUP_MANIFEST_NAME):
        try:
            member = tar.getmember(name)
        except KeyError:
            continue
        extracted = tar.extractfile(member)
        if extracted is None:
            return None
        with extracted:
            try:
                payload = json.loads(extracted.read().decode("utf-8"))
            except Exception as exc:
                raise MigrationError(f"invalid manifest: {exc}") from exc
            return payload if isinstance(payload, dict) else None
    return None


def inspect_migration_archive(archive_path: Path | str, *, max_bytes: int | None = None) -> dict[str, Any]:
    path = Path(archive_path).expanduser().resolve()
    if not path.is_file():
        raise MigrationError(f"archive not found: {path}")

    limit = max_bytes if max_bytes is not None else get_max_import_bytes()
    runtime_file_count = 0
    runtime_dir_count = 0
    runtime_total_size = 0
    env_included = False
    roots: set[str] = set()

    try:
        with tarfile.open(path, "r:gz") as tar:
            manifest = _read_manifest(tar)
            for member in tar.getmembers():
                resolved = _safe_member_path(member.name)
                if resolved is None:
                    continue
                root_name, relative_path = resolved
                roots.add(root_name)
                if not (member.isfile() or member.isdir()):
                    raise MigrationError(f"unsupported archive entry type: {member.name}")
                if root_name in {"runtime_data", "legacy_project_root"}:
                    if _should_skip_runtime_path(relative_path):
                        continue
                    if member.isfile():
                        runtime_file_count += 1
                        runtime_total_size += max(0, int(member.size))
                    elif member.isdir() and relative_path != Path("."):
                        runtime_dir_count += 1
                elif root_name == "project_env" and relative_path == Path(".env") and member.isfile():
                    env_included = True
                    runtime_total_size += max(0, int(member.size))

                if runtime_total_size > limit:
                    raise MigrationError(f"archive data exceeds limit: {limit} bytes")
    except tarfile.TarError as exc:
        raise MigrationError(f"invalid tar archive: {exc}") from exc

    archive_stat = path.stat()
    manifest_format = ""
    created_at = ""
    if manifest:
        manifest_format = str(manifest.get("format") or "legacy-runtime-backup")
        created_at = str(manifest.get("created_at") or "")

    return {
        "success": True,
        "archive_path": str(path),
        "archive_name": path.name,
        "archive_size": archive_stat.st_size,
        "created_at": created_at,
        "format": manifest_format or "unknown",
        "schema_version": manifest.get("schema_version") if manifest else None,
        "roots": sorted(roots),
        "runtime_file_count": runtime_file_count,
        "runtime_dir_count": runtime_dir_count,
        "runtime_total_size": runtime_total_size,
        "env_included": env_included,
        "manifest": manifest,
    }


def _select_runtime_members(tar: tarfile.TarFile) -> tuple[list[tuple[tarfile.TarInfo, Path]], tarfile.TarInfo | None]:
    selected_by_path: dict[Path, tuple[int, tarfile.TarInfo]] = {}
    env_member: tarfile.TarInfo | None = None

    for member in tar.getmembers():
        resolved = _safe_member_path(member.name)
        if resolved is None:
            continue
        root_name, relative_path = resolved
        if not (member.isfile() or member.isdir()):
            raise MigrationError(f"unsupported archive entry type: {member.name}")
        if root_name in {"runtime_data", "legacy_project_root"}:
            if _should_skip_runtime_path(relative_path):
                continue
            priority = 2 if root_name == "runtime_data" else 1
            current = selected_by_path.get(relative_path)
            if current is None or priority > current[0]:
                selected_by_path[relative_path] = (priority, member)
        elif root_name == "project_env" and relative_path == Path(".env") and member.isfile():
            env_member = member

    selected = [
        (member, relative_path)
        for relative_path, (_, member) in sorted(
            selected_by_path.items(),
            key=lambda item: (len(item[0].parts), item[0].as_posix()),
        )
    ]
    return selected, env_member


def _clear_directory_contents(target_dir: Path, preserve_names: set[str] | None = None) -> None:
    preserve = preserve_names or set()
    target_dir.mkdir(parents=True, exist_ok=True)
    for child in target_dir.iterdir():
        if child.name in preserve:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _copy_tree_contents(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for child in source_dir.iterdir():
        destination = target_dir / child.name
        if child.is_dir():
            shutil.copytree(child, destination, copy_function=shutil.copy2)
        else:
            shutil.copy2(child, destination)


def _extract_member_file(tar: tarfile.TarFile, member: tarfile.TarInfo, target_path: Path) -> None:
    source_file = tar.extractfile(member)
    if source_file is None:
        raise MigrationError(f"cannot read archive entry: {member.name}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with source_file, target_path.open("wb") as output_file:
        shutil.copyfileobj(source_file, output_file)


def restore_migration_archive(
    archive_path: Path | str,
    *,
    target_dir: Path | str | None = None,
    project_root: Path | str | None = None,
    restore_env: bool = False,
    apply: bool = True,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    path = Path(archive_path).expanduser().resolve()
    root_dir = Path(project_root).expanduser().resolve() if project_root else get_project_root().resolve()
    data_dir = Path(target_dir).expanduser().resolve() if target_dir else get_migration_runtime_data_dir()
    limit = max_bytes if max_bytes is not None else get_max_import_bytes()

    inspected = inspect_migration_archive(path, max_bytes=limit)
    if inspected["runtime_file_count"] == 0 and inspected["runtime_dir_count"] == 0:
        raise MigrationError("archive has no runtime data entries")

    if not apply:
        return {
            "success": True,
            "applied": False,
            "target_dir": str(data_dir),
            "restore_env": restore_env,
            "restart_required": False,
            "preview": inspected,
        }

    data_dir.mkdir(parents=True, exist_ok=True)
    pre_backup_path = None
    if runtime_data_has_content(data_dir) or (restore_env and (root_dir / ".env").exists()):
        backup_dir = get_migration_archive_dir("pre-import", runtime_dir=data_dir)
        pre_backup_result = create_migration_archive(
            backup_dir / f"wx-coupon-pre-import-{_timestamp()}.tar.gz",
            include_env=restore_env,
            runtime_dir=data_dir,
            project_root=root_dir,
        )
        pre_backup_path = pre_backup_result["archive_path"]

    restart_required = False
    with tempfile.TemporaryDirectory(prefix="wx-coupon-import-") as temp_name:
        temp_dir = Path(temp_name)
        stage_runtime_dir = temp_dir / "runtime_data"
        stage_env_path = temp_dir / "project_env" / ".env"
        total_bytes = 0

        try:
            with tarfile.open(path, "r:gz") as tar:
                selected, env_member = _select_runtime_members(tar)
                for member, relative_path in selected:
                    if relative_path == Path(".") and member.isdir():
                        stage_runtime_dir.mkdir(parents=True, exist_ok=True)
                        continue
                    if member.isdir():
                        (stage_runtime_dir / relative_path).mkdir(parents=True, exist_ok=True)
                        continue
                    total_bytes += max(0, int(member.size))
                    if total_bytes > limit:
                        raise MigrationError(f"archive data exceeds limit: {limit} bytes")
                    _extract_member_file(tar, member, stage_runtime_dir / relative_path)

                if restore_env and env_member is not None:
                    total_bytes += max(0, int(env_member.size))
                    if total_bytes > limit:
                        raise MigrationError(f"archive data exceeds limit: {limit} bytes")
                    _extract_member_file(tar, env_member, stage_env_path)
        except tarfile.TarError as exc:
            raise MigrationError(f"invalid tar archive: {exc}") from exc

        if not stage_runtime_dir.exists():
            raise MigrationError("archive runtime data could not be staged")

        _clear_directory_contents(data_dir, preserve_names={MIGRATION_BACKUP_DIR_NAME})
        _copy_tree_contents(stage_runtime_dir, data_dir)

        if restore_env and stage_env_path.exists():
            shutil.copy2(stage_env_path, root_dir / ".env")
            restart_required = True

    return {
        "success": True,
        "applied": True,
        "target_dir": str(data_dir),
        "restore_env": restore_env,
        "env_restored": restore_env and inspected.get("env_included", False),
        "restart_required": restart_required,
        "pre_import_backup_path": pre_backup_path,
        "imported": inspected,
    }


def summarize_runtime_data(runtime_dir: Path | str | None = None) -> dict[str, Any]:
    data_dir = Path(runtime_dir).expanduser().resolve() if runtime_dir else get_migration_runtime_data_dir()
    stats = {"files": 0, "dirs": 0, "bytes": 0}
    if data_dir.exists():
        for path in _iter_runtime_paths(data_dir):
            if path.is_symlink():
                continue
            if path.is_dir():
                stats["dirs"] += 1
            elif path.is_file():
                stats["files"] += 1
                try:
                    stats["bytes"] += path.stat().st_size
                except OSError:
                    pass
    return {
        "runtime_data_dir": str(data_dir),
        "exists": data_dir.exists(),
        "stats": stats,
    }


def list_migration_archives(limit: int = 8, runtime_dir: Path | None = None) -> list[dict[str, Any]]:
    archives_by_path: dict[Path, str] = {}
    for kind in MIGRATION_ARCHIVE_KINDS:
        archive_dir = get_migration_archive_dir(kind, runtime_dir=runtime_dir)
        for pattern in ("*.tar.gz", "*.tgz"):
            for path in archive_dir.glob(pattern):
                if path.is_file():
                    archives_by_path[path.resolve()] = kind

    result = []
    for path in sorted(archives_by_path, key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        stat = path.stat()
        result.append({
            "kind": archives_by_path[path],
            "name": path.name,
            "path": str(path),
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        })
    return result
