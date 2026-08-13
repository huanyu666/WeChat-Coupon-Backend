from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import base64
import platform
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
import subprocess
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


MIGRATION_FORMAT = "wx-coupon-runtime-migration"
MIGRATION_SCHEMA_VERSION = 1
MIGRATION_MANIFEST_NAME = "migration_manifest.json"
LEGACY_BACKUP_MANIFEST_NAME = "backup_manifest.json"
MIGRATION_BACKUP_DIR_NAME = ".migration-backups"
MIGRATION_ARCHIVE_KINDS = ("exports", "imports", "pre-import", "scheduled")
DEFAULT_MAX_IMPORT_BYTES = 1024 * 1024 * 1024
DEPLOYMENT_SNAPSHOT_ROOT = "deployment_snapshot"
DEPLOYMENT_SNAPSHOT_MANIFEST_NAME = "deployment_snapshot.json"
REDIS_SHORTLINK_ARCHIVE_ROOT = "redis_shortlinks"
REDIS_SHORTLINK_EXPORT_NAME = "shortlinks.json"
SHORTLINK_KEY_PREFIX = "wx:shortlink:key:"
SHORTLINK_EXPIRES_ZSET_KEY = "wx:shortlink:expires"
REDIS_RUNTIME_ARCHIVE_ROOT = "redis_runtime"
REDIS_RUNTIME_EXPORT_NAME = "redis_runtime.json"
REDIS_RUNTIME_KEY_PREFIXES = (
    "wx:shortlink:key:",
    "wx:payload:map:",
    "mt_order_token:",
)
REDIS_RUNTIME_EXACT_KEYS = (
    "wx:shortlink:expires",
    "wx:shortlink:manual",
)
DEPLOYMENT_SNAPSHOT_FILES = (
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "Dockerfile",
    ".env.example",
    ".env.docker.example",
    ".env.dev.example",
    "deploy/openresty/docker-http-proxy.conf.example",
    "deploy/openresty/wx-coupon.conf.example",
)
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
    "merchant_benefits",
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


def _run_git_command(root_dir: Path, args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root_dir), *args],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        ).strip()
    except Exception:
        return ""


def collect_deployment_info(root_dir: Path) -> dict[str, Any]:
    status = _run_git_command(root_dir, ["status", "--short"])
    commit = _run_git_command(root_dir, ["rev-parse", "HEAD"])
    short_commit = _run_git_command(root_dir, ["rev-parse", "--short", "HEAD"])
    branch = _run_git_command(root_dir, ["rev-parse", "--abbrev-ref", "HEAD"])
    remote = _run_git_command(root_dir, ["config", "--get", "remote.origin.url"])
    return {
        "project_root": str(root_dir),
        "git_commit": commit,
        "git_short_commit": short_commit,
        "git_branch": branch,
        "git_remote_origin": remote,
        "git_dirty": bool(status),
        "git_dirty_file_count": len([line for line in status.splitlines() if line.strip()]),
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "hostname": platform.node(),
        "deployment_name": os.getenv("WX_SERVICE_DEPLOYMENT_NAME", ""),
        "service_env": os.getenv("WX_SERVICE_ENV", ""),
    }


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


def _get_sync_redis_client():
    try:
        from redis import Redis
    except Exception as exc:
        raise MigrationError(f"redis client unavailable: {exc}") from exc

    redis_url = str(os.getenv("WX_SERVICE_REDIS_URL") or os.getenv("REDIS_URL") or "").strip()
    redis_host = str(os.getenv("WX_SERVICE_REDIS_HOST") or os.getenv("REDIS_HOST") or "").strip()
    redis_socket_path = os.getenv("WX_SERVICE_REDIS_SOCKET_PATH")
    if redis_socket_path is None and not redis_url and not redis_host:
        redis_socket_path = "/run/redis/redis-server.sock"
    redis_socket_path = str(redis_socket_path or "").strip()
    redis_password_env = os.getenv("WX_SERVICE_REDIS_PASSWORD")
    if redis_password_env is None:
        redis_password_env = os.getenv("REDIS_PASSWORD")
    redis_password = (redis_password_env or None) if redis_password_env is not None else None
    redis_db_raw = str(os.getenv("WX_SERVICE_REDIS_DB") or os.getenv("REDIS_DB") or "0").strip()
    try:
        redis_db = int(redis_db_raw)
    except ValueError:
        redis_db = 0
    timeout_raw = str(os.getenv("WX_SERVICE_REDIS_SOCKET_TIMEOUT_SECONDS") or os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS") or "1.5").strip()
    try:
        timeout = float(timeout_raw)
    except ValueError:
        timeout = 1.5

    kwargs = {
        "decode_responses": False,
        "socket_timeout": timeout,
        "socket_connect_timeout": timeout,
    }
    if redis_password is not None:
        kwargs["password"] = redis_password
    if redis_url:
        if os.getenv("WX_SERVICE_REDIS_DB") or os.getenv("REDIS_DB"):
            kwargs["db"] = redis_db
        return Redis.from_url(redis_url, **kwargs)
    if redis_host:
        port_raw = str(os.getenv("WX_SERVICE_REDIS_PORT") or os.getenv("REDIS_PORT") or "6379").strip()
        try:
            port = int(port_raw)
        except ValueError:
            port = 6379
        return Redis(host=redis_host, port=port, db=redis_db, **kwargs)
    if redis_socket_path and os.path.exists(redis_socket_path):
        return Redis(unix_socket_path=redis_socket_path, db=redis_db, **kwargs)
    raise MigrationError("redis socket/url/host not available")


def _bytes_to_b64(value: bytes | bytearray | memoryview | None) -> str:
    return base64.b64encode(bytes(value or b"")).decode("ascii")


def _b64_to_bytes(value: str) -> bytes:
    return base64.b64decode(str(value or "").encode("ascii"))


def export_redis_shortlinks(destination_dir: Path) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "included": False,
        "key_count": 0,
        "expires_count": 0,
        "bytes": 0,
        "error": "",
    }
    try:
        client = _get_sync_redis_client()
        client.ping()
        cursor = 0
        entries: list[dict[str, Any]] = []
        while True:
            cursor, keys = client.scan(cursor=cursor, match=f"{SHORTLINK_KEY_PREFIX}*", count=500)
            for raw_key in sorted(keys):
                key = raw_key.decode("utf-8", "replace") if isinstance(raw_key, bytes) else str(raw_key)
                value = client.get(raw_key)
                if value is None:
                    continue
                ttl_ms = int(client.pttl(raw_key))
                entries.append({
                    "key": key,
                    "value_b64": _bytes_to_b64(value),
                    "ttl_ms": ttl_ms,
                })
                stats["bytes"] += len(value)
            if cursor == 0:
                break
        expires_entries = [
            {
                "member": member.decode("utf-8", "replace") if isinstance(member, bytes) else str(member),
                "score": float(score),
            }
            for member, score in client.zrange(SHORTLINK_EXPIRES_ZSET_KEY, 0, -1, withscores=True)
        ]
        payload = {
            "format": "wx-coupon-redis-shortlinks",
            "schema_version": 1,
            "created_at": _now_iso(),
            "key_prefix": SHORTLINK_KEY_PREFIX,
            "expires_zset_key": SHORTLINK_EXPIRES_ZSET_KEY,
            "entries": entries,
            "expires_entries": expires_entries,
        }
        destination_dir.mkdir(parents=True, exist_ok=True)
        export_path = destination_dir / REDIS_SHORTLINK_EXPORT_NAME
        export_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        stats["included"] = True
        stats["key_count"] = len(entries)
        stats["expires_count"] = len(expires_entries)
        stats["bytes"] += export_path.stat().st_size
    except Exception as exc:
        stats["error"] = str(exc)
        raise MigrationError(f"Redis 短链备份失败: {exc}") from exc
    finally:
        try:
            client.close()  # type: ignore[name-defined]
        except Exception:
            pass
    return stats


def export_redis_runtime(destination_dir: Path) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "included": False,
        "key_count": 0,
        "bytes": 0,
        "error": "",
    }
    try:
        client = _get_sync_redis_client()
        client.ping()
        entries: list[dict[str, Any]] = []
        seen_keys: set[str] = set()

        for prefix in REDIS_RUNTIME_KEY_PREFIXES:
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=f"{prefix}*", count=500)
                for raw_key in sorted(keys):
                    key = raw_key.decode("utf-8", "replace") if isinstance(raw_key, bytes) else str(raw_key)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    value_type = client.type(raw_key)
                    value_type_text = value_type.decode("utf-8", "replace") if isinstance(value_type, bytes) else str(value_type)
                    ttl_ms = int(client.pttl(raw_key))
                    entry: dict[str, Any] = {
                        "key": key,
                        "type": value_type_text,
                        "ttl_ms": ttl_ms,
                    }
                    if value_type_text == "string":
                        value = client.get(raw_key)
                        if value is None:
                            continue
                        entry["value_b64"] = _bytes_to_b64(value)
                        stats["bytes"] += len(value)
                    elif value_type_text == "zset":
                        entry["zset_entries"] = [
                            {
                                "member": member.decode("utf-8", "replace") if isinstance(member, bytes) else str(member),
                                "score": float(score),
                            }
                            for member, score in client.zrange(raw_key, 0, -1, withscores=True)
                        ]
                    elif value_type_text == "set":
                        entry["set_members"] = sorted(
                            member.decode("utf-8", "replace") if isinstance(member, bytes) else str(member)
                            for member in client.smembers(raw_key)
                        )
                    else:
                        continue
                    entries.append(entry)
                if cursor == 0:
                    break

        for key in REDIS_RUNTIME_EXACT_KEYS:
            if key in seen_keys or not client.exists(key):
                continue
            value_type = client.type(key)
            value_type_text = value_type.decode("utf-8", "replace") if isinstance(value_type, bytes) else str(value_type)
            ttl_ms = int(client.pttl(key))
            entry = {"key": key, "type": value_type_text, "ttl_ms": ttl_ms}
            if value_type_text == "zset":
                entry["zset_entries"] = [
                    {
                        "member": member.decode("utf-8", "replace") if isinstance(member, bytes) else str(member),
                        "score": float(score),
                    }
                    for member, score in client.zrange(key, 0, -1, withscores=True)
                ]
            elif value_type_text == "set":
                entry["set_members"] = sorted(
                    member.decode("utf-8", "replace") if isinstance(member, bytes) else str(member)
                    for member in client.smembers(key)
                )
            elif value_type_text == "string":
                value = client.get(key)
                if value is None:
                    continue
                entry["value_b64"] = _bytes_to_b64(value)
                stats["bytes"] += len(value)
            else:
                continue
            entries.append(entry)
            seen_keys.add(key)

        payload = {
            "format": "wx-coupon-redis-runtime",
            "schema_version": 1,
            "created_at": _now_iso(),
            "entries": entries,
        }
        destination_dir.mkdir(parents=True, exist_ok=True)
        export_path = destination_dir / REDIS_RUNTIME_EXPORT_NAME
        export_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        stats["included"] = True
        stats["key_count"] = len(entries)
        stats["bytes"] += export_path.stat().st_size
    except Exception as exc:
        stats["error"] = str(exc)
        raise MigrationError(f"Redis 运行态备份失败: {exc}") from exc
    finally:
        try:
            client.close()  # type: ignore[name-defined]
        except Exception:
            pass
    return stats


def restore_redis_runtime_from_file(source_path: Path) -> dict[str, Any]:
    if not source_path.is_file():
        return {"restored": False, "key_count": 0}
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        entries = payload.get("entries", [])
        if not isinstance(entries, list):
            raise ValueError("invalid redis runtime payload")
        client = _get_sync_redis_client()
        client.ping()

        delete_keys: list[str] = []
        for prefix in REDIS_RUNTIME_KEY_PREFIXES:
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=f"{prefix}*", count=500)
                delete_keys.extend(
                    key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key)
                    for key in keys
                )
                if cursor == 0:
                    break
        delete_keys.extend(REDIS_RUNTIME_EXACT_KEYS)
        unique_delete_keys = sorted({key for key in delete_keys if key})
        if unique_delete_keys:
            client.delete(*unique_delete_keys)

        pipe = client.pipeline(transaction=False)
        restored_count = 0
        for item in entries:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            value_type = str(item.get("type") or "").strip()
            if not key or not value_type:
                continue
            ttl_ms = int(item.get("ttl_ms") or -1)
            if value_type == "string":
                value = _b64_to_bytes(str(item.get("value_b64") or ""))
                if ttl_ms > 0:
                    pipe.psetex(key, ttl_ms, value)
                else:
                    pipe.set(key, value)
            elif value_type == "zset":
                mapping = {}
                for zset_item in item.get("zset_entries") or []:
                    if not isinstance(zset_item, dict):
                        continue
                    member = str(zset_item.get("member") or "").strip()
                    if not member:
                        continue
                    mapping[member] = float(zset_item.get("score") or 0)
                if mapping:
                    pipe.zadd(key, mapping)
                    if ttl_ms > 0:
                        pipe.pexpire(key, ttl_ms)
            elif value_type == "set":
                members = [
                    str(member).strip()
                    for member in (item.get("set_members") or [])
                    if str(member).strip()
                ]
                if members:
                    pipe.sadd(key, *members)
                    if ttl_ms > 0:
                        pipe.pexpire(key, ttl_ms)
            else:
                continue
            restored_count += 1
        pipe.execute()
        return {"restored": True, "key_count": restored_count}
    except Exception as exc:
        raise MigrationError(f"Redis 运行态恢复失败: {exc}") from exc
    finally:
        try:
            client.close()  # type: ignore[name-defined]
        except Exception:
            pass


def restore_redis_shortlinks_from_file(source_path: Path) -> dict[str, Any]:
    if not source_path.is_file():
        return {"restored": False, "key_count": 0, "expires_count": 0}
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        entries = payload.get("entries", [])
        expires_entries = payload.get("expires_entries", [])
        if not isinstance(entries, list) or not isinstance(expires_entries, list):
            raise ValueError("invalid redis shortlink payload")
        client = _get_sync_redis_client()
        client.ping()
        cursor = 0
        delete_keys: list[bytes | str] = []
        while True:
            cursor, keys = client.scan(cursor=cursor, match=f"{SHORTLINK_KEY_PREFIX}*", count=500)
            delete_keys.extend(keys)
            if len(delete_keys) >= 500:
                client.delete(*delete_keys)
                delete_keys = []
            if cursor == 0:
                break
        if delete_keys:
            client.delete(*delete_keys)
        client.delete(SHORTLINK_EXPIRES_ZSET_KEY)

        restored_count = 0
        pipe = client.pipeline(transaction=False)
        for item in entries:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not key.startswith(SHORTLINK_KEY_PREFIX):
                continue
            value = _b64_to_bytes(str(item.get("value_b64") or ""))
            try:
                ttl_ms = int(item.get("ttl_ms"))
            except (TypeError, ValueError):
                ttl_ms = -1
            if ttl_ms > 0:
                pipe.psetex(key, ttl_ms, value)
            else:
                pipe.set(key, value)
            restored_count += 1
        zset_mapping: dict[str, float] = {}
        for item in expires_entries:
            if not isinstance(item, dict):
                continue
            member = str(item.get("member") or "").strip()
            if not member:
                continue
            try:
                zset_mapping[member] = float(item.get("score"))
            except (TypeError, ValueError):
                continue
        if zset_mapping:
            pipe.zadd(SHORTLINK_EXPIRES_ZSET_KEY, zset_mapping)
        pipe.execute()
        return {
            "restored": True,
            "key_count": restored_count,
            "expires_count": len(zset_mapping),
        }
    except Exception as exc:
        raise MigrationError(f"Redis 短链恢复失败: {exc}") from exc
    finally:
        try:
            client.close()  # type: ignore[name-defined]
        except Exception:
            pass


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


def collect_deployment_snapshot(root_dir: Path, destination_dir: Path) -> dict[str, Any]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for relative_name in DEPLOYMENT_SNAPSHOT_FILES:
        source = root_dir / relative_name
        if not source.is_file():
            skipped.append({"path": relative_name, "reason": "missing"})
            continue
        target = destination_dir / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        files.append({
            "path": relative_name,
            "size": target.stat().st_size,
            "sha256": _sha256_file(target),
        })

    manifest = {
        "format": "wx-coupon-deployment-snapshot",
        "schema_version": 1,
        "created_at": _now_iso(),
        "description": "项目内可见部署参考文件快照；导入时仅用于检查，不自动覆盖当前部署文件。",
        "files": files,
        "skipped": skipped,
    }
    (destination_dir / DEPLOYMENT_SNAPSHOT_MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "included": True,
        "file_count": len(files),
        "skipped_count": len(skipped),
        "files": files,
        "skipped": skipped,
    }


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
    include_redis_shortlinks: bool = False,
    include_redis_runtime: bool = False,
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
        stage_redis_dir = temp_dir / REDIS_SHORTLINK_ARCHIVE_ROOT
        redis_shortlink_stats: dict[str, Any] = {
            "included": False,
            "key_count": 0,
            "expires_count": 0,
            "bytes": 0,
            "error": "",
        }
        if include_redis_shortlinks:
            redis_shortlink_stats = export_redis_shortlinks(stage_redis_dir)
        stage_redis_runtime_dir = temp_dir / REDIS_RUNTIME_ARCHIVE_ROOT
        redis_runtime_stats: dict[str, Any] = {
            "included": False,
            "key_count": 0,
            "bytes": 0,
            "error": "",
        }
        if include_redis_runtime:
            redis_runtime_stats = export_redis_runtime(stage_redis_runtime_dir)
        env_included = False
        env_path = root_dir / ".env"
        stage_env_path = temp_dir / "project_env" / ".env"
        if include_env and env_path.is_file():
            stage_env_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(env_path, stage_env_path)
            env_included = True

        archive_items = _collect_archive_items(stage_runtime_dir, "runtime_data")
        archive_items.extend(_collect_archive_items(stage_legacy_dir, "legacy_project_root"))
        archive_items.extend(_collect_archive_items(stage_redis_dir, REDIS_SHORTLINK_ARCHIVE_ROOT))
        archive_items.extend(_collect_archive_items(stage_redis_runtime_dir, REDIS_RUNTIME_ARCHIVE_ROOT))
        stage_deployment_dir = temp_dir / DEPLOYMENT_SNAPSHOT_ROOT
        deployment_snapshot_stats = collect_deployment_snapshot(root_dir, stage_deployment_dir)
        archive_items.extend(_collect_archive_items(stage_deployment_dir, DEPLOYMENT_SNAPSHOT_ROOT))

        manifest = {
            "format": MIGRATION_FORMAT,
            "schema_version": MIGRATION_SCHEMA_VERSION,
            "created_at": _now_iso(),
            "contents": {
                "runtime_data": {
                    "included": True,
                    "description": "公众号配置、业务配置、激活码、商家券数据库等运行数据；导入时默认覆盖 runtime-data。",
                    "sensitive": True,
                    "restore_behavior": "default_restore",
                },
                "project_env": {
                    "included": env_included,
                    "description": ".env 部署环境文件；可能包含域名、端口、Redis 等部署参数。",
                    "sensitive": True,
                    "restore_behavior": "restore_only_when_selected",
                },
                "redis_shortlinks": {
                    "included": bool(redis_shortlink_stats.get("included")),
                    "description": "Redis 中的短链映射；导入时会覆盖当前短链相关 key。",
                    "sensitive": False,
                    "restore_behavior": "restore_only_when_selected",
                },
                "redis_runtime": {
                    "included": bool(redis_runtime_stats.get("included")),
                    "description": "Redis 中的运行态 key，包括短链、Web 查询 token、payload 映射等；导入时会覆盖当前对应 key。",
                    "sensitive": True,
                    "restore_behavior": "restore_only_when_selected",
                },
                "deployment_snapshot": {
                    "included": True,
                    "description": "Docker/OpenResty 示例等项目内部署参考文件；导入时仅检查，不自动覆盖。",
                    "sensitive": False,
                    "restore_behavior": "inspect_only",
                },
            },
            "project_root": str(root_dir),
            "deployment": collect_deployment_info(root_dir),
            "runtime_data_dir": str(data_dir),
            "include_env_requested": include_env,
            "env_included": env_included,
            "runtime_stats": copy_stats,
            "include_legacy_requested": include_legacy,
            "legacy_stats": legacy_stats,
            "include_redis_shortlinks_requested": include_redis_shortlinks,
            "redis_shortlinks_included": bool(redis_shortlink_stats.get("included")),
            "redis_shortlink_stats": redis_shortlink_stats,
            "include_redis_runtime_requested": include_redis_runtime,
            "redis_runtime_included": bool(redis_runtime_stats.get("included")),
            "redis_runtime_stats": redis_runtime_stats,
            "deployment_snapshot_included": True,
            "deployment_snapshot_stats": deployment_snapshot_stats,
            "items": archive_items,
        }

        with tarfile.open(archive_path, "w:gz") as tar:
            _write_json_to_tar(tar, MIGRATION_MANIFEST_NAME, manifest)
            tar.add(stage_runtime_dir, arcname="runtime_data")
            if legacy_stats["files"] or legacy_stats["dirs"]:
                tar.add(stage_legacy_dir, arcname="legacy_project_root")
            if redis_shortlink_stats.get("included"):
                tar.add(stage_redis_dir, arcname=REDIS_SHORTLINK_ARCHIVE_ROOT)
            if redis_runtime_stats.get("included"):
                tar.add(stage_redis_runtime_dir, arcname=REDIS_RUNTIME_ARCHIVE_ROOT)
            tar.add(stage_deployment_dir, arcname=DEPLOYMENT_SNAPSHOT_ROOT)
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
        "redis_shortlinks_included": bool(redis_shortlink_stats.get("included")),
        "redis_runtime_included": bool(redis_runtime_stats.get("included")),
        "runtime_stats": copy_stats,
        "legacy_stats": legacy_stats,
        "redis_shortlink_stats": redis_shortlink_stats,
        "redis_runtime_stats": redis_runtime_stats,
        "deployment_snapshot_included": True,
        "deployment_snapshot_stats": deployment_snapshot_stats,
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
    if root_name not in {
        "runtime_data",
        "legacy_project_root",
        "project_env",
        REDIS_SHORTLINK_ARCHIVE_ROOT,
        REDIS_RUNTIME_ARCHIVE_ROOT,
        DEPLOYMENT_SNAPSHOT_ROOT,
    }:
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
    redis_shortlinks_included = False
    redis_shortlink_size = 0
    redis_runtime_included = False
    redis_runtime_size = 0
    deployment_snapshot_included = False
    deployment_snapshot_file_count = 0
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
                elif (
                    root_name == REDIS_SHORTLINK_ARCHIVE_ROOT
                    and relative_path == Path(REDIS_SHORTLINK_EXPORT_NAME)
                    and member.isfile()
                ):
                    redis_shortlinks_included = True
                    redis_shortlink_size += max(0, int(member.size))
                    runtime_total_size += max(0, int(member.size))
                elif (
                    root_name == REDIS_RUNTIME_ARCHIVE_ROOT
                    and relative_path == Path(REDIS_RUNTIME_EXPORT_NAME)
                    and member.isfile()
                ):
                    redis_runtime_included = True
                    redis_runtime_size += max(0, int(member.size))
                    runtime_total_size += max(0, int(member.size))
                elif root_name == DEPLOYMENT_SNAPSHOT_ROOT:
                    deployment_snapshot_included = True
                    if member.isfile():
                        deployment_snapshot_file_count += 1
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
        "redis_shortlinks_included": redis_shortlinks_included or bool(
            manifest and manifest.get("redis_shortlinks_included")
        ),
        "redis_shortlink_stats": (manifest or {}).get("redis_shortlink_stats", {}),
        "redis_shortlink_size": redis_shortlink_size,
        "redis_runtime_included": redis_runtime_included or bool(
            manifest and manifest.get("redis_runtime_included")
        ),
        "redis_runtime_stats": (manifest or {}).get("redis_runtime_stats", {}),
        "redis_runtime_size": redis_runtime_size,
        "deployment_snapshot_included": deployment_snapshot_included or bool(
            manifest and manifest.get("deployment_snapshot_included")
        ),
        "deployment_snapshot_stats": (manifest or {}).get("deployment_snapshot_stats", {
            "file_count": deployment_snapshot_file_count,
        }),
        "deployment": (manifest or {}).get("deployment", {}),
        "contents": (manifest or {}).get("contents", {}),
        "manifest": manifest,
    }


def _select_runtime_members(
    tar: tarfile.TarFile,
) -> tuple[list[tuple[tarfile.TarInfo, Path]], tarfile.TarInfo | None, tarfile.TarInfo | None, tarfile.TarInfo | None]:
    selected_by_path: dict[Path, tuple[int, tarfile.TarInfo]] = {}
    env_member: tarfile.TarInfo | None = None
    redis_shortlinks_member: tarfile.TarInfo | None = None
    redis_runtime_member: tarfile.TarInfo | None = None

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
        elif (
            root_name == REDIS_SHORTLINK_ARCHIVE_ROOT
            and relative_path == Path(REDIS_SHORTLINK_EXPORT_NAME)
            and member.isfile()
        ):
            redis_shortlinks_member = member
        elif (
            root_name == REDIS_RUNTIME_ARCHIVE_ROOT
            and relative_path == Path(REDIS_RUNTIME_EXPORT_NAME)
            and member.isfile()
        ):
            redis_runtime_member = member

    selected = [
        (member, relative_path)
        for relative_path, (_, member) in sorted(
            selected_by_path.items(),
            key=lambda item: (len(item[0].parts), item[0].as_posix()),
        )
    ]
    return selected, env_member, redis_shortlinks_member, redis_runtime_member


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
    restore_redis_shortlinks: bool = False,
    restore_redis_runtime: bool = False,
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
            "restore_redis_shortlinks": restore_redis_shortlinks,
            "restore_redis_runtime": restore_redis_runtime,
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
            include_redis_shortlinks=restore_redis_shortlinks,
            include_redis_runtime=restore_redis_runtime,
            runtime_dir=data_dir,
            project_root=root_dir,
        )
        pre_backup_path = pre_backup_result["archive_path"]

    restart_required = False
    with tempfile.TemporaryDirectory(prefix="wx-coupon-import-") as temp_name:
        temp_dir = Path(temp_name)
        stage_runtime_dir = temp_dir / "runtime_data"
        stage_env_path = temp_dir / "project_env" / ".env"
        stage_redis_shortlinks_path = temp_dir / REDIS_SHORTLINK_ARCHIVE_ROOT / REDIS_SHORTLINK_EXPORT_NAME
        stage_redis_runtime_path = temp_dir / REDIS_RUNTIME_ARCHIVE_ROOT / REDIS_RUNTIME_EXPORT_NAME
        total_bytes = 0

        try:
            with tarfile.open(path, "r:gz") as tar:
                selected, env_member, redis_shortlinks_member, redis_runtime_member = _select_runtime_members(tar)
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
                if restore_redis_shortlinks and redis_shortlinks_member is not None:
                    total_bytes += max(0, int(redis_shortlinks_member.size))
                    if total_bytes > limit:
                        raise MigrationError(f"archive data exceeds limit: {limit} bytes")
                    _extract_member_file(tar, redis_shortlinks_member, stage_redis_shortlinks_path)
                if restore_redis_runtime and redis_runtime_member is not None:
                    total_bytes += max(0, int(redis_runtime_member.size))
                    if total_bytes > limit:
                        raise MigrationError(f"archive data exceeds limit: {limit} bytes")
                    _extract_member_file(tar, redis_runtime_member, stage_redis_runtime_path)
        except tarfile.TarError as exc:
            raise MigrationError(f"invalid tar archive: {exc}") from exc

        if not stage_runtime_dir.exists():
            raise MigrationError("archive runtime data could not be staged")

        _clear_directory_contents(data_dir, preserve_names={MIGRATION_BACKUP_DIR_NAME})
        _copy_tree_contents(stage_runtime_dir, data_dir)

        if restore_env and stage_env_path.exists():
            shutil.copy2(stage_env_path, root_dir / ".env")
            restart_required = True
        redis_restore_result = {"restored": False, "key_count": 0, "expires_count": 0}
        if restore_redis_shortlinks and stage_redis_shortlinks_path.exists():
            redis_restore_result = restore_redis_shortlinks_from_file(stage_redis_shortlinks_path)
        redis_runtime_restore_result = {"restored": False, "key_count": 0}
        if restore_redis_runtime and stage_redis_runtime_path.exists():
            redis_runtime_restore_result = restore_redis_runtime_from_file(stage_redis_runtime_path)

    return {
        "success": True,
        "applied": True,
        "target_dir": str(data_dir),
        "restore_env": restore_env,
        "restore_redis_shortlinks": restore_redis_shortlinks,
        "restore_redis_runtime": restore_redis_runtime,
        "env_restored": restore_env and inspected.get("env_included", False),
        "redis_shortlinks_restored": bool(redis_restore_result.get("restored")),
        "redis_shortlink_restore_result": redis_restore_result,
        "redis_runtime_restored": bool(redis_runtime_restore_result.get("restored")),
        "redis_runtime_restore_result": redis_runtime_restore_result,
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
