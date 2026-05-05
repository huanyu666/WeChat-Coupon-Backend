from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip()
    return values


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _normalize_base_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _project_label(path: Path) -> str:
    return path.resolve().name or str(path.resolve())


def _detect_peer_root(project_root: Path) -> Path | None:
    parent = project_root.resolve().parent
    name = project_root.resolve().name
    candidates: list[Path] = []
    if name.endswith("-dev"):
        candidates.append(parent / f"{name[:-4]}-prod")
    elif name.endswith("-prod"):
        candidates.append(parent / f"{name[:-5]}-dev")
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _collect_summary(project_root: Path, mode: str) -> dict[str, Any]:
    env_values = _parse_env_file(project_root / ".env")
    system_settings = _load_json(project_root / "runtime-data" / "system_settings.runtime.json")
    account_store = _load_json(project_root / "runtime-data" / "wechat_accounts.runtime.json")

    shortlink_config = dict(system_settings.get("shortlink_config") or {})
    accounts = account_store.get("accounts") or {}
    if not isinstance(accounts, dict):
        accounts = {}

    default_account_id = str(account_store.get("default_account_id") or "").strip()
    missing_fields: list[str] = []
    skipped_test_accounts: list[str] = []
    for account_id, raw_config in accounts.items():
        if not isinstance(raw_config, dict):
            missing_fields.append(f"{account_id}:invalid_config")
            continue
        account_name = str(raw_config.get("name") or "").strip()
        if bool(raw_config.get("diagnostic_ignore_missing_fields")):
            skipped_test_accounts.append(account_id)
            continue
        absent = [
            field
            for field in ("appid", "token", "encoding_aes_key")
            if not str(raw_config.get(field) or "").strip()
        ]
        if absent:
            missing_fields.append(f"{account_id}:{','.join(absent)}")

    runtime_base_url = _normalize_base_url(shortlink_config.get("public_base_url"))
    env_base_url = _normalize_base_url(env_values.get("GO_SHORTLINK_PUBLIC_BASE_URL"))
    effective_base_url = runtime_base_url or env_base_url

    warnings: list[str] = []
    if runtime_base_url and env_base_url and runtime_base_url != env_base_url:
        warnings.append(
            f"shortlink_base_url_mismatch runtime={runtime_base_url} env={env_base_url}"
        )
    if not effective_base_url:
        warnings.append("missing_shortlink_public_base_url")
    if not default_account_id:
        warnings.append("missing_default_account_id")
    elif default_account_id not in accounts:
        warnings.append(f"default_account_not_found account={default_account_id}")
    if missing_fields:
        warnings.append(f"accounts_missing_required_fields count={len(missing_fields)}")

    return {
        "project_root": str(project_root.resolve()),
        "project_label": _project_label(project_root),
        "mode": mode,
        "shortlink_public_base_url": effective_base_url,
        "shortlink_public_base_url_runtime": runtime_base_url,
        "shortlink_public_base_url_env": env_base_url,
        "shortlink_default_ttl_seconds": shortlink_config.get("default_ttl_seconds"),
        "shortlink_cleanup_timezone": str(shortlink_config.get("cleanup_timezone") or "").strip(),
        "shortlink_cleanup_time": str(shortlink_config.get("cleanup_time") or "").strip(),
        "wechat_account_count": len(accounts),
        "default_account_id": default_account_id,
        "missing_required_accounts": missing_fields,
        "skipped_test_accounts": skipped_test_accounts,
        "warnings": warnings,
    }


def _compare_summaries(current: dict[str, Any], peer: dict[str, Any]) -> list[str]:
    drift_messages: list[str] = []
    compare_fields = (
        "shortlink_default_ttl_seconds",
        "shortlink_cleanup_timezone",
        "shortlink_cleanup_time",
        "default_account_id",
    )
    for field in compare_fields:
        if current.get(field) != peer.get(field):
            drift_messages.append(
                f"{field} current={current.get(field)} peer={peer.get(field)}"
            )
    return drift_messages


def _print_summary(prefix: str, summary: dict[str, Any]) -> None:
    print(f"{prefix}_PROJECT {summary['project_label']}")
    print(f"{prefix}_MODE {summary['mode']}")
    print(f"{prefix}_SHORTLINK_EFFECTIVE {summary['shortlink_public_base_url']}")
    print(f"{prefix}_SHORTLINK_RUNTIME {summary['shortlink_public_base_url_runtime']}")
    print(f"{prefix}_SHORTLINK_ENV {summary['shortlink_public_base_url_env']}")
    print(f"{prefix}_SHORTLINK_TTL {summary['shortlink_default_ttl_seconds']}")
    print(f"{prefix}_SHORTLINK_CLEANUP_TZ {summary['shortlink_cleanup_timezone']}")
    print(f"{prefix}_SHORTLINK_CLEANUP_TIME {summary['shortlink_cleanup_time']}")
    print(f"{prefix}_WECHAT_ACCOUNT_COUNT {summary['wechat_account_count']}")
    print(f"{prefix}_DEFAULT_ACCOUNT {summary['default_account_id']}")
    if summary["skipped_test_accounts"]:
        for item in summary["skipped_test_accounts"]:
            print(f"{prefix}_ACCOUNT_SKIP_TEST {item}")
    if summary["missing_required_accounts"]:
        for item in summary["missing_required_accounts"]:
            print(f"{prefix}_ACCOUNT_WARN {item}")
    if summary["warnings"]:
        for item in summary["warnings"]:
            print(f"{prefix}_WARN {item}")
    else:
        print(f"{prefix}_WARN none")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["dev", "prod"], default="dev")
    parser.add_argument("--project-root", default="")
    parser.add_argument("--peer-root", default="")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve() if args.project_root else Path(__file__).resolve().parent.parent
    peer_root: Path | None = None
    if args.peer_root:
        peer_root = Path(args.peer_root).expanduser().resolve()
    else:
        peer_root = _detect_peer_root(project_root)

    summary = _collect_summary(project_root, args.mode)
    peer_summary = None
    if peer_root is not None and peer_root.exists():
        peer_mode = "prod" if args.mode == "dev" else "dev"
        peer_summary = _collect_summary(peer_root, peer_mode)

    drift_messages = _compare_summaries(summary, peer_summary) if peer_summary else []
    ok = not summary["warnings"] and not drift_messages

    if args.json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "summary": summary,
                    "peer_summary": peer_summary,
                    "drift_messages": drift_messages,
                },
                ensure_ascii=False,
            )
        )
        return 0 if (ok or not args.strict) else 1

    _print_summary("RUNTIME_CONFIG", summary)
    if peer_summary:
        _print_summary("RUNTIME_CONFIG_PEER", peer_summary)
    if drift_messages:
        for item in drift_messages:
            print(f"RUNTIME_CONFIG_DRIFT {item}")
    else:
        print("RUNTIME_CONFIG_DRIFT none")
    print("RUNTIME_CONFIG_OK" if ok else "RUNTIME_CONFIG_WARN")
    return 0 if (ok or not args.strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
