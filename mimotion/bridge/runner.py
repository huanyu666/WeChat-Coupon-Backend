#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import main as mimotion_main  # noqa: E402


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dump_result(payload: Dict[str, Any], exit_code: int = 0) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.stdout.flush()
    return exit_code


@dataclass
class AccountConfig:
    login_name: str
    password: str
    account_id: int = 0
    aes_key: str = ""
    device_id: str = ""
    min_step: int = 18000
    max_step: int = 25000
    sleep_seconds: float = 5.0
    use_concurrent: bool = False
    persist_tokens: bool = False
    push_plus_token: str = ""
    push_plus_hour: str = ""
    push_plus_max: int = 30
    push_wechat_webhook_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""


def build_push_config(account: AccountConfig):
    return mimotion_main.push_util.PushConfig(
        push_plus_token=account.push_plus_token,
        push_plus_hour=account.push_plus_hour,
        push_plus_max=int(account.push_plus_max or 30),
        push_wechat_webhook_key=account.push_wechat_webhook_key,
        telegram_bot_token=account.telegram_bot_token,
        telegram_chat_id=account.telegram_chat_id,
    )


def init_mimotion_globals(account: AccountConfig) -> None:
    mimotion_main.time_bj = mimotion_main.get_beijing_time()
    mimotion_main.encrypt_support = bool(account.persist_tokens and account.aes_key and len(account.aes_key.encode("utf-8")) == 16)
    mimotion_main.user_tokens = {}
    if mimotion_main.encrypt_support:
        mimotion_main.aes_key = account.aes_key.encode("utf-8")
        try:
            mimotion_main.user_tokens = mimotion_main.prepare_user_tokens()
        except Exception:
            mimotion_main.user_tokens = {}
    mimotion_main.min_step = int(account.min_step)
    mimotion_main.max_step = int(account.max_step)
    mimotion_main.sleep_seconds = float(account.sleep_seconds)
    mimotion_main.use_concurrent = bool(account.use_concurrent)
    mimotion_main.push_config = build_push_config(account)


def token_cache_dir(account: AccountConfig) -> Path:
    account_key = str(account.account_id or "").strip()
    if not account_key:
        account_key = account.login_name.replace("/", "_").replace("\\", "_").replace(":", "_")
    path = ROOT / "token_cache" / f"account_{account_key}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_single_account(account_data: Dict[str, Any], trigger: str = "manual") -> Dict[str, Any]:
    started_at = utc_now()
    account = AccountConfig(**account_data)
    previous_cwd = Path.cwd()
    if account.persist_tokens:
        os.chdir(token_cache_dir(account))
    try:
        init_mimotion_globals(account)
        runner = mimotion_main.MiMotionRunner(account.login_name, account.password)
        if account.device_id:
            runner.device_id = account.device_id
    except Exception:
        os.chdir(previous_cwd)
        raise
    message = ""
    summary = ""
    success = False
    logs = ""
    try:
        message, success = runner.login_and_post_step(account.min_step, account.max_step)
        logs = runner.log_str
        summary = message
        if mimotion_main.encrypt_support:
            mimotion_main.persist_user_tokens()
    except Exception:
        logs = f"{runner.log_str}\n{traceback.format_exc()}"
        message = "执行异常"
        summary = message
        success = False
    finally:
        os.chdir(previous_cwd)

    return {
        "success": success,
        "message": message,
        "summary": summary,
        "trigger": trigger,
        "logs": logs,
        "account_results": [
            {
                "login_name": account.login_name,
                "success": success,
                "message": message,
                "summary": summary,
            }
        ],
        "started_at": started_at,
        "finished_at": utc_now(),
        "upstream_commit": get_head_commit(),
    }


def validate_account(account_data: Dict[str, Any]) -> Dict[str, Any]:
    started_at = utc_now()
    account = AccountConfig(**account_data)
    previous_cwd = Path.cwd()
    if account.persist_tokens:
        os.chdir(token_cache_dir(account))
    try:
        init_mimotion_globals(account)
        runner = mimotion_main.MiMotionRunner(account.login_name, account.password)
        if account.device_id:
            runner.device_id = account.device_id
    except Exception:
        os.chdir(previous_cwd)
        raise
    logs = ""
    try:
        app_token = runner.login()
        success = bool(app_token)
        message = "账号校验成功" if success else "账号校验失败"
        logs = runner.log_str
    except Exception:
        success = False
        message = "账号校验异常"
        logs = traceback.format_exc()
    finally:
        os.chdir(previous_cwd)
    return {
        "success": success,
        "message": message,
        "summary": message,
        "logs": logs,
        "account_results": [
            {"login_name": account.login_name, "success": success, "message": message}
        ],
        "started_at": started_at,
        "finished_at": utc_now(),
        "upstream_commit": get_head_commit(),
    }


def run_job(payload: Dict[str, Any]) -> Dict[str, Any]:
    started_at = utc_now()
    account_results: List[Dict[str, Any]] = []
    logs: List[str] = []
    success_count = 0
    accounts = payload.get("accounts") or []
    for item in accounts:
        result = run_single_account(item, trigger=str(payload.get("trigger") or "job"))
        account_results.extend(result.get("account_results") or [])
        if result.get("success"):
            success_count += 1
        logs.append(f"[{item.get('login_name', '')}]\n{result.get('logs', '')}")

    total = len(accounts)
    success = success_count == total and total > 0
    summary = f"执行账号总数{total}，成功：{success_count}，失败：{total - success_count}"
    return {
        "success": success,
        "message": summary,
        "summary": summary,
        "logs": "\n\n".join(logs),
        "account_results": account_results,
        "started_at": started_at,
        "finished_at": utc_now(),
        "upstream_commit": get_head_commit(),
    }


def get_head_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(ROOT),
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def sync_upstream(payload: Dict[str, Any]) -> Dict[str, Any]:
    started_at = utc_now()
    branch = str(payload.get("branch") or "master")
    before = get_head_commit()
    steps = [
        ["git", "fetch", "origin"],
        ["git", "checkout", branch],
        ["git", "pull", "--ff-only", "origin", branch],
    ]
    logs: List[str] = []
    success = True
    message = "同步成功"
    for step in steps:
        proc = subprocess.run(step, cwd=str(ROOT), capture_output=True, text=True)
        logs.append(f"$ {' '.join(step)}\n{proc.stdout}{proc.stderr}")
        if proc.returncode != 0:
            success = False
            message = f"命令失败: {' '.join(step)}"
            break
    after = get_head_commit()
    return {
        "success": success,
        "message": message,
        "summary": message,
        "logs": "\n".join(logs),
        "account_results": [],
        "started_at": started_at,
        "finished_at": utc_now(),
        "upstream_commit": after,
        "from_commit": before,
        "to_commit": after,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("run-account", "run-job", "validate-account", "sync-upstream"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--input", required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    payload = load_json(args.input)
    try:
        if args.command == "run-account":
            result = run_single_account(payload)
        elif args.command == "run-job":
            result = run_job(payload)
        elif args.command == "validate-account":
            result = validate_account(payload)
        elif args.command == "sync-upstream":
            result = sync_upstream(payload)
        else:
            result = {"success": False, "message": f"未知命令: {args.command}"}
            return dump_result(result, 2)
        return dump_result(result, 0 if result.get("success", False) else 1)
    except Exception:
        return dump_result(
            {
                "success": False,
                "message": "runner 执行异常",
                "summary": "runner 执行异常",
                "logs": traceback.format_exc(),
                "account_results": [],
                "started_at": utc_now(),
                "finished_at": utc_now(),
                "upstream_commit": get_head_commit(),
            },
            1,
        )


if __name__ == "__main__":
    raise SystemExit(main())
