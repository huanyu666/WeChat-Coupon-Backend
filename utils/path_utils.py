from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable

DEFAULT_SERVICE_SOCKET_PATH = "/run/wx_service-python/wx_service.sock"


def get_project_root() -> Path:
    custom_root = os.getenv("WX_SERVICE_ROOT", "").strip()
    if custom_root:
        return Path(custom_root).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def get_runtime_data_dir() -> Path:
    custom_dir = os.getenv("WX_SERVICE_DATA_DIR", "").strip()
    if custom_dir:
        path = Path(custom_dir).expanduser().resolve()
    elif os.getenv("STATE_DIRECTORY", "").strip():
        path = Path(os.getenv("STATE_DIRECTORY", "").strip()).expanduser().resolve()
    else:
        path = get_project_root() / "runtime-data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_runtime_dir() -> Path:
    custom_dir = os.getenv("WX_SERVICE_RUNTIME_DIR", "").strip()
    if custom_dir:
        path = Path(custom_dir).expanduser().resolve()
    elif os.getenv("RUNTIME_DIRECTORY", "").strip():
        path = Path(os.getenv("RUNTIME_DIRECTORY", "").strip()).expanduser().resolve()
    else:
        path = get_project_root()
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_log_dir() -> Path:
    custom_dir = os.getenv("WX_SERVICE_LOG_DIR", "").strip()
    if custom_dir:
        path = Path(custom_dir).expanduser().resolve()
    elif os.getenv("LOGS_DIRECTORY", "").strip():
        path = Path(os.getenv("LOGS_DIRECTORY", "").strip()).expanduser().resolve()
    else:
        path = get_project_root() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_service_socket_path() -> str:
    return os.getenv("WX_SERVICE_SOCKET_PATH", DEFAULT_SERVICE_SOCKET_PATH).strip()


def prepare_unix_socket_path(socket_path: str) -> None:
    if not socket_path:
        return
    path = Path(socket_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def resolve_project_path(*parts: str) -> Path:
    return get_project_root().joinpath(*parts)


def resolve_runtime_data_path(*parts: str) -> Path:
    return get_runtime_data_dir().joinpath(*parts)


def resolve_runtime_or_legacy_data_paths(*parts: str) -> tuple[Path, Path]:
    """Transitional compatibility API.

    Runtime data is now runtime-data only. The second return value is kept for
    older callers that still expect a ``(primary, legacy)`` tuple.
    """
    primary_path = resolve_runtime_data_path(*parts)
    return primary_path, primary_path


def first_existing_path(candidates: Iterable[Path | str]) -> Path | None:
    for candidate in candidates:
        path = Path(candidate).expanduser().resolve()
        if path.exists():
            return path
    return None
