from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}\.txt$")
MAX_CONTENT_BYTES = 4096


def _default_data_dir() -> Path:
    return PROJECT_ROOT / "runtime-data"


def _normalize_filename(value: str) -> str:
    filename = Path(str(value or "").strip()).name
    if not filename.endswith(".txt"):
        filename += ".txt"
    if not FILENAME_RE.fullmatch(filename):
        raise ValueError("文件名只允许字母、数字、点、下划线、横线，且必须是 .txt")
    return filename


def _verification_dir(data_dir: str) -> Path:
    base = Path(data_dir).expanduser().resolve() if data_dir else _default_data_dir().resolve()
    path = base / "site-verification"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _target_path(data_dir: str, filename: str) -> Path:
    root = _verification_dir(data_dir).resolve()
    target = (root / _normalize_filename(filename)).resolve()
    if target.parent != root:
        raise ValueError("认证文件必须位于 site-verification 目录内")
    return target


def _write_file(path: Path, content: str) -> None:
    raw = str(content)
    if "\r" in raw or "\n" in raw:
        raise ValueError("认证内容不能包含换行")
    encoded = raw.encode("utf-8")
    if not encoded:
        raise ValueError("认证内容不能为空")
    if len(encoded) > MAX_CONTENT_BYTES:
        raise ValueError(f"认证内容不能超过 {MAX_CONTENT_BYTES} 字节")
    with NamedTemporaryFile("wb", dir=str(path.parent), delete=False) as temp_file:
        temp_file.write(encoded)
        temp_path = Path(temp_file.name)
    temp_path.replace(path)


def _list_files(data_dir: str) -> list[dict[str, Any]]:
    root = _verification_dir(data_dir)
    items: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.txt")):
        if path.is_file() and FILENAME_RE.fullmatch(path.name):
            items.append({"filename": path.name, "size": path.stat().st_size, "path": str(path)})
    return items


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default=str(_default_data_dir()))
    parser.add_argument("--json", action="store_true")
    subparsers = parser.add_subparsers(dest="action", required=True)

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("filename")
    add_parser.add_argument("content")
    add_parser.add_argument("--json", action="store_true")

    remove_parser = subparsers.add_parser("remove")
    remove_parser.add_argument("filename")
    remove_parser.add_argument("--json", action="store_true")

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--json", action="store_true")

    args = parser.parse_args()

    try:
        if args.action == "add":
            path = _target_path(args.data_dir, args.filename)
            _write_file(path, args.content)
            payload = {"ok": True, "action": "add", "filename": path.name, "path": str(path)}
        elif args.action == "remove":
            path = _target_path(args.data_dir, args.filename)
            existed = path.exists()
            path.unlink(missing_ok=True)
            payload = {"ok": True, "action": "remove", "filename": path.name, "path": str(path), "existed": existed}
        else:
            payload = {"ok": True, "action": "list", "files": _list_files(args.data_dir)}
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": exc.__class__.__name__, "message": str(exc)}, ensure_ascii=False))
        else:
            print(f"SITE_VERIFICATION_FILE_FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload["action"] == "list":
        for item in payload["files"]:
            print(f"SITE_VERIFICATION_FILE {item['filename']} size={item['size']} path={item['path']}")
        print("SITE_VERIFICATION_FILE_OK")
    else:
        existed = f" existed={payload['existed']}" if "existed" in payload else ""
        print(f"SITE_VERIFICATION_FILE_OK action={payload['action']} filename={payload['filename']} path={payload['path']}{existed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
