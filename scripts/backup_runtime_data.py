from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent


RUNTIME_FILE_NAMES = {
    "wechat_accounts.runtime.json",
    "activation_codes.json",
    "activation_codes_link.json",
    "activation_codes_meituan_order.json",
    "scenes.json",
    "p_values.json",
    "order_leaderboard.db",
    "merchant_coupons.db",
}
RUNTIME_DIR_NAMES = {
    "merchant_coupons",
}


def get_project_root() -> Path:
    custom_root = os.getenv("WX_SERVICE_ROOT", "").strip()
    if custom_root:
        return Path(custom_root).expanduser().resolve()
    return PROJECT_ROOT


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


def _discover_runtime_paths(base_dir: Path) -> list[Path]:
    discovered: list[Path] = []
    seen: set[Path] = set()
    if not base_dir.exists():
        return discovered
    for child in sorted(base_dir.iterdir(), key=lambda item: item.name):
        resolved = child.resolve()
        if resolved in seen:
            continue
        if child.is_dir() and child.name in RUNTIME_DIR_NAMES:
            discovered.append(child)
            seen.add(resolved)
            continue
        if not child.is_file():
            continue
        if child.name in RUNTIME_FILE_NAMES or child.suffix.lower() in {".json", ".db"}:
            discovered.append(child)
            seen.add(resolved)
    return discovered


def _build_sources() -> list[tuple[str, Path]]:
    project_root = get_project_root().resolve()
    runtime_data_dir = get_runtime_data_dir().resolve()
    sources: list[tuple[str, Path]] = [("runtime_data", runtime_data_dir)]
    if runtime_data_dir != project_root:
        sources.append(("legacy_project_root", project_root))
    return sources


def _default_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = PROJECT_ROOT / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir / f"wx-runtime-backup-{timestamp}.tar.gz"


def _iter_archive_entries() -> tuple[list[dict], list[tuple[Path, str]]]:
    manifest_items: list[dict] = []
    archive_items: list[tuple[Path, str]] = []
    seen_real_paths: set[Path] = set()
    for source_name, base_dir in _build_sources():
        for path in _discover_runtime_paths(base_dir):
            real_path = path.resolve()
            if real_path in seen_real_paths:
                continue
            seen_real_paths.add(real_path)
            arcname = f"{source_name}/{path.name}"
            manifest_items.append(
                {
                    "source": source_name,
                    "base_dir": str(base_dir),
                    "path": str(path),
                    "arcname": arcname,
                    "type": "dir" if path.is_dir() else "file",
                }
            )
            archive_items.append((path, arcname))
    return manifest_items, archive_items


def _write_manifest(tar: tarfile.TarFile, output_path: Path, manifest_items: Iterable[dict]) -> None:
    manifest = {
        "created_at": datetime.now().isoformat(),
        "project_root": str(get_project_root().resolve()),
        "runtime_data_dir": str(get_runtime_data_dir().resolve()),
        "output_path": str(output_path),
        "items": list(manifest_items),
    }
    payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    info = tarfile.TarInfo(name="backup_manifest.json")
    info.size = len(payload)
    tar.addfile(info, io.BytesIO(payload))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    output_path = Path(args.output).expanduser().resolve() if args.output else _default_output_path().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_items, archive_items = _iter_archive_entries()
    if not archive_items:
        print("BACKUP_RUNTIME_DATA_SKIPPED no_runtime_items_found", file=sys.stderr)
        return 1

    with tarfile.open(output_path, "w:gz") as tar:
        _write_manifest(tar, output_path, manifest_items)
        for path, arcname in archive_items:
            tar.add(path, arcname=arcname)

    print(f"BACKUP_RUNTIME_DATA_OK path={output_path}")
    for item in manifest_items:
        print(f"- {item['arcname']} <= {item['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
