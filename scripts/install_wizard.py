from __future__ import annotations

import argparse
import html
import ipaddress
import json
import secrets
import subprocess
import sys
import urllib.parse
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUPS_DIR = PROJECT_ROOT / "backups"
SITE_VERIFICATION_DIR = PROJECT_ROOT / "runtime-data" / "site-verification"


def _compose_file(mode: str) -> str:
    return "docker-compose.dev.yml" if mode == "dev" else "docker-compose.yml"


def _compose_project_name(mode: str) -> str:
    return "wx-coupon-dev" if mode == "dev" else "wx-coupon-prod"


def _port_key(mode: str) -> str:
    return "WX_DEV_HTTP_PORT" if mode == "dev" else "WX_HTTP_PORT"


def _status_values(status: dict[str, Any]) -> dict[str, Any]:
    values = status.get("values")
    return values if isinstance(values, dict) else {}


def _resolve_backup_archive(value: str) -> Path:
    raw_value = str(value or "").strip()
    if not raw_value:
        raise ValueError("请输入 backups/ 下的备份文件路径")
    candidate = Path(raw_value)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    elif len(candidate.parts) == 1:
        resolved = (BACKUPS_DIR / candidate).resolve()
    else:
        resolved = (PROJECT_ROOT / candidate).resolve()
    backups_root = BACKUPS_DIR.resolve()
    if backups_root != resolved and backups_root not in resolved.parents:
        raise ValueError("恢复预览只允许读取 backups/ 目录下的归档")
    if resolved.suffixes[-2:] != [".tar", ".gz"]:
        raise ValueError("恢复预览只允许 .tar.gz 备份归档")
    return resolved


def _run_command(command: list[str], timeout: float = 30) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
        return {
            "ok": completed.returncode == 0,
            "returncode": completed.returncode,
            "output": completed.stdout,
        }
    except Exception as exc:
        return {
            "ok": False,
            "returncode": -1,
            "output": f"{exc.__class__.__name__}: {exc}",
        }


def _load_env_status(mode: str) -> dict[str, Any]:
    result = _run_command(
        [
            sys.executable,
            "scripts/configure_env.py",
            "--mode",
            mode,
            "--create",
            "--check",
            "--json",
        ]
    )
    try:
        payload = json.loads(result["output"])
    except Exception:
        payload = {"ok": False, "output": result["output"]}
    return payload


def _collect_wizard_checks(mode: str, status: dict[str, Any]) -> list[dict[str, str]]:
    values = _status_values(status)
    port_key = _port_key(mode)
    port = str(values.get(port_key) or "").strip()
    shortlink_url = str(values.get("GO_SHORTLINK_PUBLIC_BASE_URL") or "").strip()
    checks: list[dict[str, str]] = []
    if port.isdigit() and 1 <= int(port) <= 65535:
        checks.append({"level": "ok", "text": f"{port_key}={port}"})
    else:
        checks.append({"level": "bad", "text": f"{port_key} 不是合法端口: {port or '(空)'}"})
    if shortlink_url.startswith(("http://", "https://")):
        checks.append({"level": "ok", "text": f"GO_SHORTLINK_PUBLIC_BASE_URL={shortlink_url}"})
    else:
        checks.append({"level": "bad", "text": "GO_SHORTLINK_PUBLIC_BASE_URL 必须包含 http:// 或 https://"})
    if mode == "prod":
        lowered = shortlink_url.lower()
        parsed = urllib.parse.urlparse(shortlink_url)
        host = parsed.hostname or ""
        is_ip_host = False
        try:
            ipaddress.ip_address(host)
            is_ip_host = True
        except ValueError:
            is_ip_host = False
        if not lowered:
            checks.append({"level": "bad", "text": "生产模式必须填写正式公开访问地址"})
        elif "localhost" in lowered or "127.0.0.1" in lowered:
            checks.append({"level": "bad", "text": "生产模式公开访问地址不应使用 localhost 或 127.0.0.1"})
        elif is_ip_host and lowered.startswith("http://"):
            checks.append({"level": "warn", "text": "当前生产模式暂用 IP + HTTP，可继续测试；后续接域名时建议改为 HTTPS"})
        elif not lowered.startswith("https://"):
            checks.append({"level": "warn", "text": "生产模式建议使用 https:// 域名"})
        if port and port != "8080":
            checks.append({"level": "warn", "text": f"生产端口为 {port}，宝塔/OpenResty 反代模板默认代理 8080，请确认两边一致"})
    return checks


def _format_command_label(command: list[str]) -> str:
    return " ".join(command)


def _normalize_log_tail(value: str) -> str:
    allowed_tails = {"80", "160", "300", "500"}
    requested = str(value or "").strip()
    return requested if requested in allowed_tails else "160"


def _log_services(value: str) -> list[str]:
    service = str(value or "all").strip()
    if service == "app":
        return ["app"]
    if service == "redis":
        return ["redis"]
    return ["app", "redis"]


def _run_logs_action(mode: str, service: str = "all", tail: str = "160") -> dict[str, Any]:
    compose_file = _compose_file(mode)
    project_name = _compose_project_name(mode)
    normalized_tail = _normalize_log_tail(tail)
    command = [
        "docker",
        "compose",
        "-f",
        compose_file,
        "-p",
        project_name,
        "logs",
        "--tail",
        normalized_tail,
        *_log_services(service),
    ]
    result = _run_command(command, timeout=60)
    result["command"] = _format_command_label(command)
    return result


def _run_wizard_action(mode: str, action: str) -> dict[str, Any]:
    compose_file = _compose_file(mode)
    project_name = _compose_project_name(mode)
    commands = {
        "doctor": (["./doctor.sh", mode], 60),
        "status": (["./status.sh", mode], 90),
        "preflight": (["./preflight.sh", mode], 90),
        "compose_ps": (["docker", "compose", "-f", compose_file, "-p", project_name, "ps"], 45),
        "docker_ps": (
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name={project_name}",
                "--format",
                "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}",
            ],
            45,
        ),
        "backup": (["./backup.sh"], 180),
        "install": (["./install.sh", mode], 360),
    }
    command_info = commands.get(action)
    if command_info is None:
        return {
            "ok": False,
            "returncode": -1,
            "output": f"INSTALL_WIZARD_FAILED unknown_action={action}",
        }
    command, timeout = command_info
    result = _run_command(command, timeout=timeout)
    result["command"] = _format_command_label(command)
    return result


def _should_skip_public_proxy_check(mode: str, public_url: str) -> bool:
    normalized = str(public_url or "").strip()
    if mode == "dev" or not normalized:
        return True
    parsed = urllib.parse.urlparse(normalized)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".example.com") or host == "example.com"


def _run_proxy_check(mode: str, public_url: str) -> dict[str, Any]:
    normalized_url = str(public_url or "").strip().rstrip("/")
    command = ["./proxy_check.sh", mode]
    if _should_skip_public_proxy_check(mode, normalized_url):
        command.append("--skip-public")
    else:
        command.append(normalized_url)
    result = _run_command(command, timeout=60)
    result["command"] = _format_command_label(command)
    return result


def _run_proxy_config_preview(mode: str, public_url: str) -> dict[str, Any]:
    normalized_url = str(public_url or "").strip().rstrip("/")
    command = ["./setup_proxy.sh", "--mode", mode, "--replace-existing"]
    if normalized_url:
        command.extend(["--public-base-url", normalized_url])
    result = _run_command(command, timeout=60)
    result["command"] = _format_command_label(command)
    return result


def _run_runtime_migration(skip_conflicts: bool = False, apply_changes: bool = False) -> dict[str, Any]:
    command = ["./migrate_runtime.sh"]
    if skip_conflicts:
        command.append("--skip-conflicts")
    if apply_changes:
        command.append("--yes")
    result = _run_command(command, timeout=90)
    result["command"] = _format_command_label(command)
    return result


def _run_site_verification_add(filename: str, content: str) -> dict[str, Any]:
    normalized_filename = str(filename or "").strip()
    command = ["./site_verify.sh", "add", normalized_filename, str(content or "")]
    result = _run_command(command, timeout=30)
    result["command"] = f"./site_verify.sh add {normalized_filename} <content>"
    return result


def _run_site_verification_remove(filename: str) -> dict[str, Any]:
    normalized_filename = str(filename or "").strip()
    command = ["./site_verify.sh", "remove", normalized_filename]
    result = _run_command(command, timeout=30)
    result["command"] = _format_command_label(command)
    return result


def _run_site_verification_list() -> dict[str, Any]:
    command = ["./site_verify.sh", "list"]
    result = _run_command(command, timeout=30)
    result["command"] = _format_command_label(command)
    return result


def _run_restore_preview(archive_value: str) -> dict[str, Any]:
    try:
        archive_path = _resolve_backup_archive(archive_value)
    except Exception as exc:
        return {
            "ok": False,
            "returncode": -1,
            "command": "./restore.sh <backup-archive>",
            "output": f"INSTALL_WIZARD_FAILED {exc.__class__.__name__}: {exc}",
        }
    command = ["./restore.sh", str(archive_path)]
    result = _run_command(command, timeout=90)
    result["command"] = _format_command_label(command)
    return result


def _run_init_admin(username: str, password: str) -> dict[str, Any]:
    normalized_username = str(username or "").strip()
    command = [
        sys.executable,
        "scripts/init_admin_user.py",
        "--username",
        normalized_username,
        "--password",
        str(password or ""),
    ]
    result = _run_command(command, timeout=30)
    result["command"] = f"{sys.executable} scripts/init_admin_user.py --username {normalized_username} --password <hidden>"
    return result


def _run_init_wechat_account(form: dict[str, list[str]]) -> dict[str, Any]:
    account_id = str(form.get("wechat_account_id", [""])[0] or "").strip()
    appid = str(form.get("wechat_appid", [""])[0] or "").strip()
    command = [
        sys.executable,
        "scripts/init_wechat_account.py",
        "--data-dir",
        "runtime-data",
        "--account-id",
        account_id,
        "--name",
        str(form.get("wechat_name", [""])[0] or ""),
        "--appid",
        appid,
        "--app-secret",
        str(form.get("wechat_app_secret", [""])[0] or ""),
        "--token",
        str(form.get("wechat_token", [""])[0] or ""),
        "--encoding-aes-key",
        str(form.get("wechat_encoding_aes_key", [""])[0] or ""),
        "--zmkey",
        str(form.get("wechat_zmkey", [""])[0] or ""),
        "--welcome-message",
        str(form.get("wechat_welcome_message", [""])[0] or ""),
        "--default-reply",
        str(form.get("wechat_default_reply", [""])[0] or ""),
        "--enabled-text-processors",
        str(form.get("wechat_enabled_text_processors", [""])[0] or ""),
        "--enabled-miniprogram-appids",
        str(form.get("wechat_enabled_miniprogram_appids", [""])[0] or ""),
        "--meituan-base-url",
        str(form.get("wechat_meituan_base_url", [""])[0] or ""),
        "--meituan-official-cashback-url",
        str(form.get("wechat_meituan_official_cashback_url", [""])[0] or ""),
        "--url-mode",
        str(form.get("wechat_url_mode", ["all"])[0] or "all"),
        "--authorized-users",
        str(form.get("wechat_authorized_users", [""])[0] or ""),
        "--default-code-duration",
        str(form.get("wechat_default_code_duration", [""])[0] or ""),
    ]
    if form.get("wechat_set_default", [""])[0] == "1":
        command.append("--set-default")
    result = _run_command(command, timeout=30)
    default_flag = " --set-default" if "--set-default" in command else ""
    result["command"] = (
        f"{sys.executable} scripts/init_wechat_account.py --data-dir runtime-data "
        f"--account-id {account_id} --appid {appid} --app-secret <hidden> "
        f"--token <hidden> --encoding-aes-key <hidden> --zmkey <hidden>{default_flag}"
    )
    return result


def _recent_backup_options() -> str:
    if not BACKUPS_DIR.exists():
        return ""
    archives = sorted(BACKUPS_DIR.glob("*.tar.gz"), key=lambda path: path.stat().st_mtime, reverse=True)[:10]
    items = []
    for archive in archives:
        mtime = datetime.fromtimestamp(archive.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        size_mb = archive.stat().st_size / 1024 / 1024
        label = f"{archive.relative_to(PROJECT_ROOT)} ({size_mb:.1f} MB, {mtime})"
        items.append(f"<li><code>{html.escape(str(archive.relative_to(PROJECT_ROOT)))}</code> - {html.escape(label)}</li>")
    return "<ul>" + "".join(items) + "</ul>" if items else ""


def _site_verification_files_html(shortlink_url: str) -> str:
    if not SITE_VERIFICATION_DIR.exists():
        return "<p>暂无站点认证文件。</p>"
    public_base_url = str(shortlink_url or "").strip().rstrip("/")
    items = []
    for path in sorted(SITE_VERIFICATION_DIR.glob("*.txt")):
        if not path.is_file():
            continue
        filename = path.name
        url_html = ""
        if public_base_url:
            url = f"{public_base_url}/{filename}"
            url_html = f" <a href=\"{html.escape(url)}\" target=\"_blank\" rel=\"noreferrer\">访问</a>"
        items.append(
            f"<li><code>{html.escape(filename)}</code> "
            f"<span class=\"muted\">{path.stat().st_size} bytes</span>{url_html}</li>"
        )
    return "<ul>" + "".join(items) + "</ul>" if items else "<p>暂无站点认证文件。</p>"


def _service_urls(mode: str, status: dict[str, Any]) -> list[tuple[str, str]]:
    values = _status_values(status)
    port = str(values.get(_port_key(mode)) or ("18080" if mode == "dev" else "8080")).strip()
    shortlink_url = str(values.get("GO_SHORTLINK_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    urls: list[tuple[str, str]] = []
    if shortlink_url.startswith(("http://", "https://")):
        urls.append(("公开访问地址", shortlink_url))
    if port.isdigit():
        local_url = f"http://127.0.0.1:{port}"
        if not any(url == local_url for _, url in urls):
            urls.append(("服务器本机地址", local_url))
    return urls


def _install_success_panel(token: str, mode: str, status: dict[str, Any]) -> str:
    action_url = f"/action?token={html.escape(token)}"
    urls = _service_urls(mode, status)
    url_items = "".join(
        f"<li>{html.escape(label)}：<a href=\"{html.escape(url)}\" target=\"_blank\" rel=\"noreferrer\">{html.escape(url)}</a> "
        f"<code>{html.escape(url + '/healthz')}</code> <code>{html.escape(url + '/readyz')}</code></li>"
        for label, url in urls
    )
    mode_hint = (
        "开发模式下一般继续用 VSCode Remote-SSH 修改代码，容器会自动 reload。"
        if mode == "dev"
        else "生产模式下一步应在宝塔/OpenResty 中反代到服务器本机地址，并确认公开访问地址使用正式 HTTPS 域名。"
    )
    return f"""
  <section class="success-panel">
    <h2>安装完成</h2>
    <p>{html.escape(mode_hint)}</p>
    <ul>{url_items}</ul>
    <form class="buttons" method="post" action="{action_url}">
      <input type="hidden" name="mode" value="{html.escape(mode)}">
      <button class="secondary" type="submit" name="action" value="status">再次检查状态</button>
      <button class="secondary" type="submit" name="action" value="logs">查看最近日志</button>
      <button class="secondary" type="submit" name="action" value="backup">安装后备份</button>
    </form>
  </section>"""


def _html_page(token: str, mode: str, status: dict[str, Any], message: str = "", install_success: bool = False) -> str:
    values = _status_values(status)
    port_key = _port_key(mode)
    port = str(values.get(port_key) or ("18080" if mode == "dev" else "8080"))
    shortlink_url = str(values.get("GO_SHORTLINK_PUBLIC_BASE_URL") or ("http://localhost:18080" if mode == "dev" else ""))
    failures = status.get("failures") if isinstance(status.get("failures"), list) else []
    checks = _collect_wizard_checks(mode, status)
    escaped_message = html.escape(message)
    escaped_failures = "".join(f"<li>{html.escape(str(item))}</li>" for item in failures)
    escaped_checks = "".join(
        f"<li><span class=\"{html.escape(item['level'])}\">{html.escape(item['level'].upper())}</span> {html.escape(item['text'])}</li>"
        for item in checks
    )
    action_url = f"/action?token={html.escape(token)}"
    backup_options = _recent_backup_options()
    site_verification_files = _site_verification_files_html(shortlink_url)
    install_panel = _install_success_panel(token, mode, status) if install_success else ""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WX Coupon Installer</title>
  <style>
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f7f9; color: #20242a; }}
    main {{ max-width: 880px; margin: 0 auto; padding: 32px 20px; }}
    h1 {{ font-size: 28px; margin: 0 0 8px; }}
    p {{ color: #59616d; line-height: 1.6; }}
    main > form, main > section {{ background: #fff; border: 1px solid #dfe3e8; border-radius: 8px; padding: 20px; margin-top: 18px; }}
    section form {{ background: transparent; border: 0; border-radius: 0; padding: 0; margin-top: 0; }}
    section form + form {{ margin-top: 18px; }}
    label {{ display: block; font-weight: 650; margin: 14px 0 6px; }}
    input, select {{ width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #c8ced8; border-radius: 6px; font-size: 15px; }}
    textarea {{ width: 100%; min-height: 88px; box-sizing: border-box; padding: 10px 12px; border: 1px solid #c8ced8; border-radius: 6px; font-size: 15px; font-family: inherit; }}
    input[type="checkbox"] {{ width: auto; margin-right: 8px; }}
    button {{ margin-top: 18px; padding: 10px 14px; border: 0; border-radius: 6px; background: #1769e0; color: #fff; font-weight: 650; cursor: pointer; }}
    button.secondary {{ background: #3b4654; margin-right: 8px; }}
    button.warning {{ background: #b54708; margin-right: 8px; }}
    pre {{ white-space: pre-wrap; background: #101418; color: #e8edf2; padding: 14px; border-radius: 6px; overflow: auto; }}
    .ok {{ color: #0d7a3f; font-weight: 700; }}
    .warn {{ color: #b54708; font-weight: 700; }}
    .bad {{ color: #b42318; font-weight: 700; }}
    .success-panel {{ border-color: #86efac; background: #f0fdf4; }}
    .success-panel h2 {{ margin-top: 0; color: #166534; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .buttons {{ display: flex; flex-wrap: wrap; gap: 8px; }}
    .buttons button {{ margin-top: 8px; }}
    .checkbox-label {{ display: flex; align-items: center; margin-top: 16px; }}
    .muted {{ color: #748091; }}
    code {{ background: #eef1f5; border-radius: 4px; padding: 2px 5px; }}
    @media (max-width: 720px) {{ .row {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
<main>
  <h1>WX Coupon Installer</h1>
  <p>临时安装向导。默认只建议通过 SSH 端口转发访问，配置完成后关闭这个进程。</p>
  <section>
    <div>当前模式：<strong>{html.escape(mode)}</strong></div>
    <div>配置状态：<span class="{'ok' if status.get('ok') else 'bad'}">{'OK' if status.get('ok') else 'NEEDS ATTENTION'}</span></div>
    {"<ul>" + escaped_failures + "</ul>" if failures else ""}
    <p>生产域名与端口检查：</p>
    <ul>{escaped_checks}</ul>
    {f"<pre>{escaped_message}</pre>" if message else ""}
  </section>
  {install_panel}
  <form method="post" action="/configure?token={html.escape(token)}">
    <div class="row">
      <div>
        <label for="mode">模式</label>
        <select id="mode" name="mode">
          <option value="dev" {"selected" if mode == "dev" else ""}>dev</option>
          <option value="prod" {"selected" if mode == "prod" else ""}>prod</option>
        </select>
      </div>
      <div>
        <label for="port">端口</label>
        <input id="port" name="port" value="{html.escape(port)}">
      </div>
    </div>
    <label for="shortlink_base_url">短链/公开访问地址</label>
    <input id="shortlink_base_url" name="shortlink_base_url" value="{html.escape(shortlink_url)}">
    <button type="submit">保存并校验 .env</button>
  </form>
  <section>
    <p>安装与检查：</p>
    <form class="buttons" method="post" action="{action_url}">
      <input type="hidden" name="mode" value="{html.escape(mode)}">
      <button class="secondary" type="submit" name="action" value="doctor">运行 doctor</button>
      <button class="secondary" type="submit" name="action" value="status">运行 status</button>
      <button class="secondary" type="submit" name="action" value="preflight">运行 preflight</button>
      <button type="submit" name="action" value="install">执行 install</button>
    </form>
  </section>
  <section>
    <p>容器状态与日志：</p>
    <form class="buttons" method="post" action="{action_url}">
      <input type="hidden" name="mode" value="{html.escape(mode)}">
      <button class="secondary" type="submit" name="action" value="docker_ps">查看 docker ps</button>
      <button class="secondary" type="submit" name="action" value="compose_ps">查看 compose ps</button>
    </form>
    <form method="post" action="{action_url}">
      <input type="hidden" name="mode" value="{html.escape(mode)}">
      <div class="row">
        <div>
          <label for="log_service">日志服务</label>
          <select id="log_service" name="log_service">
            <option value="all">app + redis</option>
            <option value="app">app</option>
            <option value="redis">redis</option>
          </select>
        </div>
        <div>
          <label for="log_tail">日志行数</label>
          <select id="log_tail" name="log_tail">
            <option value="80">80</option>
            <option value="160" selected>160</option>
            <option value="300">300</option>
            <option value="500">500</option>
          </select>
        </div>
      </div>
      <button class="secondary" type="submit" name="action" value="logs">查看容器日志</button>
    </form>
  </section>
  <section>
    <p>备份与恢复预览：</p>
    <form class="buttons" method="post" action="{action_url}">
      <input type="hidden" name="mode" value="{html.escape(mode)}">
      <button class="secondary" type="submit" name="action" value="backup">立即备份 runtime-data</button>
    </form>
    <form method="post" action="{action_url}">
      <input type="hidden" name="mode" value="{html.escape(mode)}">
      <label for="restore_archive">恢复 dry-run 归档路径</label>
      <input id="restore_archive" name="restore_archive" placeholder="backups/你的备份.tar.gz">
      <button class="warning" type="submit" name="action" value="restore_preview">仅预览恢复，不写入数据</button>
    </form>
    {backup_options}
  </section>
  <section>
    <p>IP/反代检查与运行时迁移：</p>
    <form method="post" action="{action_url}">
      <input type="hidden" name="mode" value="{html.escape(mode)}">
      <label for="proxy_base_url">公开访问地址</label>
      <input id="proxy_base_url" name="proxy_base_url" value="{html.escape(shortlink_url)}" placeholder="http://服务器IP:8080 或 https://正式域名">
      <div class="buttons">
        <button class="secondary" type="submit" name="action" value="proxy_check">检查 IP/反代访问</button>
        <button class="secondary" type="submit" name="action" value="proxy_config_preview">生成 Nginx 反代配置</button>
        <button class="secondary" type="submit" name="action" value="migrate_preview">预览旧数据迁移</button>
        <button class="warning" type="submit" name="action" value="migrate_skip_conflicts">迁移无冲突旧数据</button>
      </div>
    </form>
  </section>
  <section>
    <p>站点认证文件：</p>
    <form method="post" action="{action_url}">
      <input type="hidden" name="mode" value="{html.escape(mode)}">
      <div class="row">
        <div>
          <label for="site_verify_filename">文件名</label>
          <input id="site_verify_filename" name="site_verify_filename" placeholder="tencentxxxx.txt">
        </div>
        <div>
          <label for="site_verify_content">认证内容</label>
          <input id="site_verify_content" name="site_verify_content" placeholder="平台给出的纯文本内容">
        </div>
      </div>
      <div class="buttons">
        <button class="secondary" type="submit" name="action" value="site_verify_list">刷新列表</button>
        <button class="warning" type="submit" name="action" value="site_verify_add">添加/覆盖认证文件</button>
      </div>
    </form>
    <form method="post" action="{action_url}">
      <input type="hidden" name="mode" value="{html.escape(mode)}">
      <label for="site_verify_remove_filename">删除文件名</label>
      <input id="site_verify_remove_filename" name="site_verify_remove_filename" placeholder="tencentxxxx.txt">
      <button class="warning" type="submit" name="action" value="site_verify_remove">删除认证文件</button>
    </form>
    {site_verification_files}
  </section>
  <section>
    <p>管理员账号初始化：</p>
    <form method="post" action="{action_url}">
      <input type="hidden" name="mode" value="{html.escape(mode)}">
      <label for="admin_username">管理员用户名</label>
      <input id="admin_username" name="admin_username" placeholder="admin">
      <label for="admin_password">管理员密码</label>
      <input id="admin_password" name="admin_password" type="password" autocomplete="new-password" placeholder="至少 8 位">
      <button class="warning" type="submit" name="action" value="init_admin">初始化/更新管理员账号</button>
    </form>
  </section>
  <section>
    <p>公众号基础配置初始化：</p>
    <form method="post" action="{action_url}">
      <input type="hidden" name="mode" value="{html.escape(mode)}">
      <div class="row">
        <div>
          <label for="wechat_account_id">公众号原始 ID</label>
          <input id="wechat_account_id" name="wechat_account_id" placeholder="gh_xxx">
        </div>
        <div>
          <label for="wechat_name">公众号名称</label>
          <input id="wechat_name" name="wechat_name" placeholder="公众号名称">
        </div>
      </div>
      <div class="row">
        <div>
          <label for="wechat_appid">AppID</label>
          <input id="wechat_appid" name="wechat_appid" placeholder="wx_xxx">
        </div>
        <div>
          <label for="wechat_app_secret">AppSecret</label>
          <input id="wechat_app_secret" name="wechat_app_secret" type="password" autocomplete="new-password">
        </div>
      </div>
      <div class="row">
        <div>
          <label for="wechat_token">Token</label>
          <input id="wechat_token" name="wechat_token" type="password" autocomplete="new-password">
        </div>
        <div>
          <label for="wechat_encoding_aes_key">EncodingAESKey</label>
          <input id="wechat_encoding_aes_key" name="wechat_encoding_aes_key" type="password" autocomplete="new-password">
        </div>
      </div>
      <label for="wechat_zmkey">转码平台 Key</label>
      <input id="wechat_zmkey" name="wechat_zmkey" type="password" autocomplete="new-password">
      <div class="row">
        <div>
          <label for="wechat_url_mode">链接模式</label>
          <select id="wechat_url_mode" name="wechat_url_mode">
            <option value="all">all</option>
            <option value="meituan">meituan</option>
            <option value="dianping">dianping</option>
          </select>
        </div>
        <div>
          <label for="wechat_default_code_duration">默认验证码有效期</label>
          <input id="wechat_default_code_duration" name="wechat_default_code_duration" placeholder="例如 300">
        </div>
      </div>
      <label for="wechat_welcome_message">欢迎语</label>
      <textarea id="wechat_welcome_message" name="wechat_welcome_message"></textarea>
      <label for="wechat_default_reply">默认回复</label>
      <textarea id="wechat_default_reply" name="wechat_default_reply"></textarea>
      <div class="row">
        <div>
          <label for="wechat_enabled_text_processors">启用文本处理器</label>
          <textarea id="wechat_enabled_text_processors" name="wechat_enabled_text_processors" placeholder="每行一个，或用逗号分隔"></textarea>
        </div>
        <div>
          <label for="wechat_enabled_miniprogram_appids">启用小程序 AppID</label>
          <textarea id="wechat_enabled_miniprogram_appids" name="wechat_enabled_miniprogram_appids" placeholder="每行一个，或用逗号分隔"></textarea>
        </div>
      </div>
      <div class="row">
        <div>
          <label for="wechat_meituan_base_url">美团基础链接</label>
          <input id="wechat_meituan_base_url" name="wechat_meituan_base_url">
        </div>
        <div>
          <label for="wechat_meituan_official_cashback_url">美团官方返现链接</label>
          <input id="wechat_meituan_official_cashback_url" name="wechat_meituan_official_cashback_url">
        </div>
      </div>
      <label for="wechat_authorized_users">授权用户</label>
      <textarea id="wechat_authorized_users" name="wechat_authorized_users" placeholder="每行一个，或用逗号分隔"></textarea>
      <label class="checkbox-label">
        <input type="checkbox" name="wechat_set_default" value="1" checked>
        设为默认公众号
      </label>
      <button class="warning" type="submit" name="action" value="init_wechat">初始化/更新公众号配置</button>
    </form>
  </section>
  <section>
    <p>也可以在服务器终端执行：</p>
    <pre>./install.sh {html.escape(mode)}
./status.sh {html.escape(mode)}
./doctor.sh {html.escape(mode)}
./preflight.sh {html.escape(mode)}
./proxy_check.sh {html.escape(mode)} --skip-public
./migrate_runtime.sh
./site_verify.sh add tencentxxxx.txt 认证内容
./site_verify.sh list
./site_verify.sh remove tencentxxxx.txt
./backup.sh
./restore.sh backups/你的备份.tar.gz
python3 scripts/init_admin_user.py --username admin --password '你的安全密码'
python3 scripts/init_wechat_account.py --data-dir runtime-data --account-id gh_xxx --appid wx_xxx --name '公众号名称'</pre>
  </section>
</main>
</body>
</html>"""


class WizardHandler(BaseHTTPRequestHandler):
    server: "WizardServer"

    def _token_ok(self) -> bool:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        return query.get("token", [""])[0] == self.server.token

    def _send_html(self, body: str, status: int = 200) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if not self._token_ok():
            self._send_html("Forbidden", status=403)
            return
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        mode = query.get("mode", [self.server.default_mode])[0]
        if mode not in {"dev", "prod"}:
            mode = self.server.default_mode
        status = _load_env_status(mode)
        self._send_html(_html_page(self.server.token, mode, status))

    def do_POST(self) -> None:
        if not self._token_ok():
            self._send_html("Forbidden", status=403)
            return
        parsed_path = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or "0")
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        mode = form.get("mode", [self.server.default_mode])[0]
        if mode not in {"dev", "prod"}:
            mode = self.server.default_mode

        if parsed_path.path == "/action":
            action = form.get("action", [""])[0]
            if action == "restore_preview":
                result = _run_restore_preview(form.get("restore_archive", [""])[0])
            elif action == "init_admin":
                result = _run_init_admin(
                    username=form.get("admin_username", [""])[0],
                    password=form.get("admin_password", [""])[0],
                )
            elif action == "init_wechat":
                result = _run_init_wechat_account(form)
            elif action == "proxy_check":
                result = _run_proxy_check(mode, form.get("proxy_base_url", [""])[0])
            elif action == "proxy_config_preview":
                result = _run_proxy_config_preview(mode, form.get("proxy_base_url", [""])[0])
            elif action == "migrate_preview":
                result = _run_runtime_migration(skip_conflicts=False, apply_changes=False)
            elif action == "migrate_skip_conflicts":
                result = _run_runtime_migration(skip_conflicts=True, apply_changes=True)
            elif action == "site_verify_add":
                result = _run_site_verification_add(
                    filename=form.get("site_verify_filename", [""])[0],
                    content=form.get("site_verify_content", [""])[0],
                )
            elif action == "site_verify_remove":
                result = _run_site_verification_remove(form.get("site_verify_remove_filename", [""])[0])
            elif action == "site_verify_list":
                result = _run_site_verification_list()
            elif action == "logs":
                result = _run_logs_action(
                    mode,
                    service=form.get("log_service", ["all"])[0],
                    tail=form.get("log_tail", ["160"])[0],
                )
            else:
                result = _run_wizard_action(mode, action)
            status = _load_env_status(mode)
            command = result.get("command") or f"{action} ({mode})"
            message = f"$ {command}\nreturncode={result['returncode']}\n\n{result['output']}"
            install_success = action == "install" and bool(result.get("ok"))
            self._send_html(_html_page(self.server.token, mode, status, message, install_success=install_success))
            return

        port = form.get("port", [""])[0]
        shortlink_base_url = form.get("shortlink_base_url", [""])[0]
        result = _run_command(
            [
                sys.executable,
                "scripts/configure_env.py",
                "--mode",
                mode,
                "--create",
                "--port",
                port,
                "--shortlink-base-url",
                shortlink_base_url,
            ]
        )
        status = _load_env_status(mode)
        self._send_html(_html_page(self.server.token, mode, status, result["output"]))

    def log_message(self, format: str, *args: Any) -> None:
        return


class WizardServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], token: str, default_mode: str):
        super().__init__(server_address, WizardHandler)
        self.token = token
        self.default_mode = default_mode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--mode", choices=["dev", "prod"], default="dev")
    parser.add_argument("--token", default="")
    args = parser.parse_args()

    token = args.token or secrets.token_urlsafe(18)
    server = WizardServer((args.host, args.port), token=token, default_mode=args.mode)
    url = f"http://{args.host}:{args.port}/?token={urllib.parse.quote(token)}"
    print(f"INSTALL_WIZARD_URL {url}", flush=True)
    print("INSTALL_WIZARD_STOP Ctrl+C", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("INSTALL_WIZARD_STOPPED", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
