"""
Runtime identity helpers for distinguishing dev/prod deployments.
"""
from __future__ import annotations

import os
import socket
from pathlib import Path
from urllib.parse import urlparse


def _first_env(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _normalize_mode(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"prod", "production", "live", "online"}:
        return "production"
    if normalized in {"dev", "development", "local", "test", "testing"}:
        return "development"
    if normalized in {"stage", "staging", "preprod", "preview"}:
        return "staging"
    return ""


def _infer_mode(explicit_mode: str, deployment_name: str, public_base_url: str) -> str:
    normalized = _normalize_mode(explicit_mode)
    if normalized:
        return normalized

    deployment_lower = str(deployment_name or "").lower()
    if "prod" in deployment_lower:
        return "production"
    if "dev" in deployment_lower:
        return "development"
    if "stag" in deployment_lower or "preview" in deployment_lower:
        return "staging"

    parsed = urlparse(public_base_url)
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return "development"
    if parsed.port == 18080:
        return "development"
    if public_base_url:
        return "production"
    return "unknown"


def _mode_label(mode: str) -> str:
    return {
        "production": "生产环境",
        "development": "开发环境",
        "staging": "预发环境",
    }.get(mode, "未知环境")


def _mode_badge(mode: str) -> str:
    return {
        "production": "PROD",
        "development": "DEV",
        "staging": "STAGE",
    }.get(mode, "UNKNOWN")


def _join_url(base_url: str, path: str) -> str:
    normalized_base = str(base_url or "").strip().rstrip("/")
    if not normalized_base:
        return ""
    normalized_path = "/" + str(path or "").strip().lstrip("/")
    return normalized_base + normalized_path


def _config_source(config_file: str) -> str:
    normalized = str(config_file or "").strip()
    if not normalized:
        return "unknown"
    path = Path(normalized)
    path_text = normalized.replace("\\", "/")
    if path_text.startswith("/data/") or "/runtime-data/" in path_text:
        return "runtime"
    if path_text.startswith("/app/"):
        return "image"
    if path.name == "config.toml":
        return "local"
    return "custom"


def _resolve_config_file() -> str:
    try:
        from config.config import CONFIG_FILE

        return str(CONFIG_FILE or "")
    except Exception:
        return ""


def build_runtime_identity(config_file: str | None = None) -> dict:
    public_base_url = _first_env("WX_SERVICE_PUBLIC_URL").rstrip("/")
    shortlink_base_url = _first_env("GO_SHORTLINK_PUBLIC_BASE_URL").rstrip("/")
    try:
        from utils.shortlink_service import get_shortlink_config

        configured_shortlink_base_url = get_shortlink_config().public_base_url
        if configured_shortlink_base_url:
            shortlink_base_url = configured_shortlink_base_url
    except Exception:
        pass
    deployment_name = _first_env("WX_SERVICE_DEPLOYMENT_NAME", "COMPOSE_PROJECT_NAME")
    explicit_mode = _first_env("WX_SERVICE_ENV", "APP_ENV", "ENVIRONMENT")
    resolved_config_file = str(config_file if config_file is not None else _resolve_config_file()).strip()
    mode = _infer_mode(explicit_mode, deployment_name, public_base_url)

    return {
        "mode": mode,
        "mode_label": _mode_label(mode),
        "mode_badge": _mode_badge(mode),
        "deployment_name": deployment_name,
        "public_base_url": public_base_url,
        "shortlink_base_url": shortlink_base_url,
        "admin_url": _join_url(public_base_url, "/index"),
        "wechat_callback_url": _join_url(public_base_url, "/wechat"),
        "config_file": resolved_config_file,
        "config_source": _config_source(resolved_config_file),
        "data_dir": _first_env("WX_SERVICE_DATA_DIR"),
        "log_dir": _first_env("WX_SERVICE_LOG_DIR"),
        "runtime_dir": _first_env("WX_SERVICE_RUNTIME_DIR"),
        "hostname": socket.gethostname(),
    }
