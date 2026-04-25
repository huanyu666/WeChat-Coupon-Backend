from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


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
        path = get_project_root()
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


def resolve_project_path(*parts: str) -> Path:
    return get_project_root().joinpath(*parts)


def resolve_runtime_data_path(*parts: str) -> Path:
    return get_runtime_data_dir().joinpath(*parts)


def resolve_runtime_or_legacy_data_paths(*parts: str) -> tuple[Path, Path]:
    return resolve_runtime_data_path(*parts), resolve_project_path(*parts)


def first_existing_path(candidates: Iterable[Path | str]) -> Path | None:
    for candidate in candidates:
        path = Path(candidate).expanduser().resolve()
        if path.exists():
            return path
    return None
