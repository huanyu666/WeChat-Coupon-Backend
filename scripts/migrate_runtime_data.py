from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TARGET_DIR = PROJECT_ROOT / "runtime-data"

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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fingerprint(path: Path) -> list[tuple[str, str, int, str]]:
    if path.is_file():
        return [(".", "file", path.stat().st_size, _sha256_file(path))]
    items: list[tuple[str, str, int, str]] = []
    for child in sorted(path.rglob("*")):
        relative = child.relative_to(path).as_posix()
        if child.is_dir():
            items.append((relative, "dir", 0, ""))
        elif child.is_file():
            items.append((relative, "file", child.stat().st_size, _sha256_file(child)))
    return items


def _same_content(left: Path, right: Path) -> bool:
    if not left.exists() or not right.exists():
        return False
    if left.is_file() != right.is_file() or left.is_dir() != right.is_dir():
        return False
    return _fingerprint(left) == _fingerprint(right)


def _discover_legacy_items(source_root: Path, target_dir: Path) -> list[Path]:
    discovered: list[Path] = []
    if not source_root.exists():
        return discovered
    target_resolved = target_dir.resolve()
    for child in sorted(source_root.iterdir(), key=lambda item: item.name):
        child_resolved = child.resolve()
        if child_resolved == target_resolved or target_resolved in child_resolved.parents:
            continue
        if child.is_dir() and child.name in RUNTIME_DIR_NAMES:
            discovered.append(child)
            continue
        if child.is_file() and child.name in RUNTIME_FILE_NAMES:
            discovered.append(child)
    return discovered


def _build_plan(source_root: Path, target_dir: Path, *, overwrite: bool, remove_legacy: bool) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for source in _discover_legacy_items(source_root, target_dir):
        target = target_dir / source.name
        item: dict[str, Any] = {
            "name": source.name,
            "source": str(source),
            "target": str(target),
            "type": "dir" if source.is_dir() else "file",
            "action": "",
            "remove_legacy": False,
        }
        if not target.exists():
            item["action"] = "copy"
            item["remove_legacy"] = remove_legacy
        elif _same_content(source, target):
            item["action"] = "already_migrated"
            item["remove_legacy"] = remove_legacy
        elif overwrite:
            item["action"] = "overwrite"
            item["remove_legacy"] = remove_legacy
        else:
            item["action"] = "conflict"
        plan.append(item)
    return plan


def _backup_existing(path: Path, backup_root: Path) -> Path:
    backup_path = backup_root / "overwritten-targets" / path.name
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        if backup_path.exists():
            shutil.rmtree(backup_path)
        shutil.copytree(path, backup_path)
    else:
        shutil.copy2(path, backup_path)
    return backup_path


def _move_legacy(path: Path, backup_root: Path) -> Path:
    destination = backup_root / "legacy-root-items" / path.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    shutil.move(str(path), str(destination))
    return destination


def _copy_item(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _apply_plan(plan: list[dict[str, Any]], backup_root: Path, *, skip_conflicts: bool) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    conflicts = [item for item in plan if item["action"] == "conflict"]
    if conflicts and not skip_conflicts:
        names = ", ".join(item["name"] for item in conflicts)
        raise RuntimeError(f"目标 runtime-data 已有不同内容，需人工确认或加 --overwrite: {names}")

    for item in plan:
        source = Path(item["source"])
        target = Path(item["target"])
        result = dict(item)
        if item["action"] == "copy":
            _copy_item(source, target)
            result["applied"] = "copied"
        elif item["action"] == "overwrite":
            result["backup_path"] = str(_backup_existing(target, backup_root))
            _copy_item(source, target)
            result["applied"] = "overwritten"
        elif item["action"] == "already_migrated":
            result["applied"] = "skipped_same_content"
        elif item["action"] == "conflict" and skip_conflicts:
            result["applied"] = "skipped_conflict"
        else:
            result["applied"] = "skipped"

        if item.get("remove_legacy") and source.exists() and _same_content(source, target):
            result["legacy_backup_path"] = str(_move_legacy(source, backup_root))
        applied.append(result)
    return applied


def _print_plan(plan: list[dict[str, Any]]) -> None:
    if not plan:
        print("MIGRATE_RUNTIME_DATA_NO_LEGACY_ITEMS")
        return
    for item in plan:
        suffix = " remove_legacy=true" if item.get("remove_legacy") else ""
        print(f"- {item['action']} {item['name']} {item['source']} -> {item['target']}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", default=str(PROJECT_ROOT))
    parser.add_argument("--target-dir", default=str(DEFAULT_TARGET_DIR))
    parser.add_argument("--overwrite", action="store_true", help="目标已有不同内容时，先备份再覆盖")
    parser.add_argument("--skip-conflicts", action="store_true", help="目标已有不同内容时跳过该项，继续迁移其他项")
    parser.add_argument("--remove-legacy", action="store_true", help="迁移成功后把根目录旧文件移动到 backups/")
    parser.add_argument("--yes", action="store_true", help="确认执行；不加时只预览")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    source_root = Path(args.source_root).expanduser().resolve()
    target_dir = Path(args.target_dir).expanduser().resolve()
    backup_root = PROJECT_ROOT / "backups" / f"runtime-migrate-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    try:
        plan = _build_plan(source_root, target_dir, overwrite=args.overwrite, remove_legacy=args.remove_legacy)
        if not args.yes:
            if args.json:
                print(json.dumps({"ok": True, "dry_run": True, "plan": plan}, ensure_ascii=False, indent=2))
            else:
                _print_plan(plan)
                print("MIGRATE_RUNTIME_DATA_DRY_RUN add --yes to apply")
            return 0

        target_dir.mkdir(parents=True, exist_ok=True)
        applied = _apply_plan(plan, backup_root, skip_conflicts=args.skip_conflicts)
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": exc.__class__.__name__, "message": str(exc)}, ensure_ascii=False))
        else:
            print(f"MIGRATE_RUNTIME_DATA_FAILED {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"ok": True, "dry_run": False, "backup_root": str(backup_root), "applied": applied}, ensure_ascii=False, indent=2))
    else:
        for item in applied:
            print(f"- {item.get('applied')} {item['name']} {item['source']} -> {item['target']}")
            if item.get("backup_path"):
                print(f"  target_backup={item['backup_path']}")
            if item.get("legacy_backup_path"):
                print(f"  legacy_backup={item['legacy_backup_path']}")
        print(f"MIGRATE_RUNTIME_DATA_OK target={target_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
