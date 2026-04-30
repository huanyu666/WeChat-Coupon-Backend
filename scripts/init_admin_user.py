from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "runtime-data" / "config.toml"


_SECTION_RE = re.compile(r"^\s*\[([^\]]+)]\s*$")
_ADMIN_LINE_RE = re.compile(r"^(\s*)([^\s=#][^=]*?)(\s*=\s*)([\"'])([0-9a-fA-F]{64})([\"'])(.*)$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")


def _resolve_default_config_path() -> Path:
    raw_path = str(os.getenv("WX_SERVICE_CONFIG_FILE") or os.getenv("CONFIG_FILE") or "").strip()
    if raw_path:
        return Path(raw_path).expanduser()

    runtime_config_path = PROJECT_ROOT / "runtime-data" / "config.toml"
    if runtime_config_path.exists():
        return runtime_config_path

    legacy_config_path = PROJECT_ROOT / "config.toml"
    if legacy_config_path.exists():
        return legacy_config_path

    return DEFAULT_CONFIG_PATH


def _validate_username(username: str) -> str:
    normalized = str(username or "").strip()
    if not normalized:
        raise ValueError("管理员用户名不能为空")
    if not _USERNAME_RE.fullmatch(normalized):
        raise ValueError("管理员用户名只能包含字母、数字、下划线、点、@ 和短横线，长度 1-64")
    return normalized


def _validate_password(password: str) -> str:
    value = str(password or "")
    if len(value) < 8:
        raise ValueError("管理员密码长度至少 8 位")
    if "\n" in value or "\r" in value:
        raise ValueError("管理员密码不能包含换行")
    return value


def _quote_key(username: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", username):
        return username
    return json.dumps(username, ensure_ascii=False)


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _find_admin_section(lines: list[str]) -> tuple[int | None, int | None]:
    start: int | None = None
    end: int | None = None
    for index, line in enumerate(lines):
        match = _SECTION_RE.match(line)
        if not match:
            continue
        if start is None:
            if match.group(1).strip() == "admin_users":
                start = index
            continue
        end = index
        break
    return start, end


def _line_key(line: str) -> str | None:
    match = _ADMIN_LINE_RE.match(line)
    if not match:
        return None
    raw_key = match.group(2).strip()
    if (raw_key.startswith('"') and raw_key.endswith('"')) or (raw_key.startswith("'") and raw_key.endswith("'")):
        return raw_key[1:-1]
    return raw_key


def _upsert_admin_user(lines: list[str], username: str, password_hash: str) -> tuple[list[str], str]:
    start, end = _find_admin_section(lines)
    new_line = f'{_quote_key(username)} = "{password_hash}"'
    if start is None:
        output = list(lines)
        if output and output[-1].strip():
            output.append("")
        output.extend(["[admin_users]", new_line])
        return output, "created_section"

    section_end = end if end is not None else len(lines)
    output = list(lines)
    for index in range(start + 1, section_end):
        if _line_key(output[index]) == username:
            match = _ADMIN_LINE_RE.match(output[index])
            if match is None:
                output[index] = new_line
            else:
                output[index] = f"{match.group(1)}{match.group(2).strip()}{match.group(3)}{match.group(4)}{password_hash}{match.group(6)}{match.group(7)}"
            return output, "updated_user"

    insert_at = section_end
    while insert_at > start + 1 and not output[insert_at - 1].strip():
        insert_at -= 1
    output.insert(insert_at, new_line)
    return output, "created_user"


def _write_config(path: Path, lines: list[str], backup: bool) -> Path | None:
    backup_path: Path | None = None
    if backup and path.exists():
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup_path = path.with_name(f"{path.name}.bak.{timestamp}")
        shutil.copy2(path, backup_path)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.replace(temp_path, path)
    return backup_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-file", default=str(_resolve_default_config_path()))
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default="")
    parser.add_argument("--password-sha256", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        username = _validate_username(args.username)
        if args.password_sha256:
            password_hash = str(args.password_sha256).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", password_hash):
                raise ValueError("--password-sha256 必须是 64 位小写十六进制 SHA256")
        else:
            password_hash = _hash_password(_validate_password(args.password))

        config_path = Path(args.config_file).expanduser().resolve()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        lines = config_path.read_text(encoding="utf-8").splitlines() if config_path.exists() else []
        output_lines, action = _upsert_admin_user(lines, username, password_hash)
        backup_path = _write_config(config_path, output_lines, backup=True)
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": exc.__class__.__name__, "message": str(exc)}, ensure_ascii=False))
        else:
            print(f"INIT_ADMIN_USER_FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    payload = {
        "ok": True,
        "action": action,
        "username": username,
        "config_path": str(config_path),
        "backup_path": str(backup_path) if backup_path is not None else "",
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"INIT_ADMIN_USER_OK action={action} username={username} config_path={config_path}")
        if backup_path is not None:
            print(f"INIT_ADMIN_USER_BACKUP {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
