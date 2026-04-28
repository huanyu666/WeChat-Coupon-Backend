from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

MODE_TEMPLATES = {
    "dev": PROJECT_ROOT / ".env.dev.example",
    "prod": PROJECT_ROOT / ".env.docker.example",
}
MODE_PORT_KEYS = {
    "dev": "WX_DEV_HTTP_PORT",
    "prod": "WX_HTTP_PORT",
}


def _read_env_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _parse_env_lines(lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def _format_env_value(value: str) -> str:
    normalized = str(value or "").strip()
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("环境变量值不能包含换行")
    return normalized


def _update_env_lines(lines: list[str], updates: dict[str, str]) -> list[str]:
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue

        key, _value = stripped.split("=", 1)
        key = key.strip()
        if key in remaining:
            output.append(f"{key}={_format_env_value(remaining.pop(key))}")
        else:
            output.append(line)

    if remaining:
        if output and output[-1].strip():
            output.append("")
        for key in sorted(remaining):
            output.append(f"{key}={_format_env_value(remaining[key])}")

    return output


def _write_env(path: Path, lines: list[str], *, backup: bool) -> Path | None:
    backup_path: Path | None = None
    if backup and path.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.name}.bak.{timestamp}")
        shutil.copy2(path, backup_path)

    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.replace(temp_path, path)
    return backup_path


def _normalize_base_url(value: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        return ""
    if "://" not in normalized:
        normalized = "http://" + normalized
    return normalized


def _validate_port(key: str, value: str, failures: list[str]) -> None:
    if not str(value or "").isdigit():
        failures.append(f"{key} 必须是数字端口")
        return
    port = int(value)
    if port < 1 or port > 65535:
        failures.append(f"{key} 超出合法端口范围: {port}")


def _collect_failures(mode: str, values: dict[str, str]) -> list[str]:
    failures: list[str] = []
    port_key = MODE_PORT_KEYS[mode]
    _validate_port(port_key, values.get(port_key, ""), failures)

    shortlink_url = values.get("GO_SHORTLINK_PUBLIC_BASE_URL", "").strip()
    if not shortlink_url:
        failures.append("GO_SHORTLINK_PUBLIC_BASE_URL 不能为空")
    elif "://" not in shortlink_url:
        failures.append("GO_SHORTLINK_PUBLIC_BASE_URL 必须包含 http:// 或 https://")

    redis_url = values.get("WX_SERVICE_REDIS_URL", "").strip()
    if not redis_url:
        failures.append("WX_SERVICE_REDIS_URL 不能为空；Docker 部署应使用 redis://redis:6379/0")

    if mode == "prod" and "localhost" in shortlink_url.lower():
        failures.append("生产模式 GO_SHORTLINK_PUBLIC_BASE_URL 不应使用 localhost")

    return failures


def _build_updates(args: argparse.Namespace) -> dict[str, str]:
    updates: dict[str, str] = {}
    if args.port:
        updates[MODE_PORT_KEYS[args.mode]] = str(args.port).strip()
    if args.shortlink_base_url:
        updates["GO_SHORTLINK_PUBLIC_BASE_URL"] = _normalize_base_url(args.shortlink_base_url)
    for item in args.set_value:
        if "=" not in item:
            raise ValueError(f"--set 格式必须是 KEY=VALUE: {item}")
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"--set KEY 不能为空: {item}")
        updates[key] = value.strip()
    return updates


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["dev", "prod"], default="dev")
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"))
    parser.add_argument("--create", action="store_true", help="如果 .env 不存在，从模板创建")
    parser.add_argument("--check", action="store_true", help="只校验 .env，不写入")
    parser.add_argument("--port", default="")
    parser.add_argument("--shortlink-base-url", default="")
    parser.add_argument("--set", dest="set_value", action="append", default=[])
    args = parser.parse_args()

    env_path = Path(args.env_file).expanduser().resolve()
    template_path = MODE_TEMPLATES[args.mode]

    try:
        created_env = False
        if not env_path.exists():
            if not args.create:
                print(f"CONFIGURE_ENV_FAILED missing_env_file path={env_path}", file=sys.stderr)
                return 1
            if not template_path.exists():
                print(f"CONFIGURE_ENV_FAILED missing_template path={template_path}", file=sys.stderr)
                return 1
            env_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(template_path, env_path)
            created_env = True
            print(f"CONFIGURE_ENV_CREATED {env_path} <= {template_path.name}")

        lines = _read_env_lines(env_path)
        updates = _build_updates(args)
        if updates and not args.check:
            lines = _update_env_lines(lines, updates)
            backup_path = _write_env(env_path, lines, backup=not created_env)
            if backup_path is not None:
                print(f"CONFIGURE_ENV_BACKUP {backup_path}")
            print(f"CONFIGURE_ENV_UPDATED {env_path}")
        elif updates and args.check:
            lines = _update_env_lines(lines, updates)

        values = _parse_env_lines(lines)
        failures = _collect_failures(args.mode, values)
    except Exception as exc:
        print(f"CONFIGURE_ENV_FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    if failures:
        print("CONFIGURE_ENV_FAILED")
        for item in failures:
            print(f"- {item}")
        return 1

    print(f"CONFIGURE_ENV_OK mode={args.mode} path={env_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
