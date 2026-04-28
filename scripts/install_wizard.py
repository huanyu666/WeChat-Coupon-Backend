from __future__ import annotations

import argparse
import html
import json
import secrets
import subprocess
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


def _html_page(token: str, mode: str, status: dict[str, Any], message: str = "") -> str:
    values = status.get("values") if isinstance(status.get("values"), dict) else {}
    port_key = "WX_DEV_HTTP_PORT" if mode == "dev" else "WX_HTTP_PORT"
    port = str(values.get(port_key) or ("18080" if mode == "dev" else "8080"))
    shortlink_url = str(values.get("GO_SHORTLINK_PUBLIC_BASE_URL") or ("http://localhost:18080" if mode == "dev" else ""))
    failures = status.get("failures") if isinstance(status.get("failures"), list) else []
    escaped_message = html.escape(message)
    escaped_failures = "".join(f"<li>{html.escape(str(item))}</li>" for item in failures)

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
    form, section {{ background: #fff; border: 1px solid #dfe3e8; border-radius: 8px; padding: 20px; margin-top: 18px; }}
    label {{ display: block; font-weight: 650; margin: 14px 0 6px; }}
    input, select {{ width: 100%; box-sizing: border-box; padding: 10px 12px; border: 1px solid #c8ced8; border-radius: 6px; font-size: 15px; }}
    button {{ margin-top: 18px; padding: 10px 14px; border: 0; border-radius: 6px; background: #1769e0; color: #fff; font-weight: 650; cursor: pointer; }}
    pre {{ white-space: pre-wrap; background: #101418; color: #e8edf2; padding: 14px; border-radius: 6px; overflow: auto; }}
    .ok {{ color: #0d7a3f; font-weight: 700; }}
    .bad {{ color: #b42318; font-weight: 700; }}
    .row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
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
    {f"<pre>{escaped_message}</pre>" if message else ""}
  </section>
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
    <p>配置完成后，在服务器终端执行：</p>
    <pre>./install.sh {html.escape(mode)}</pre>
    <p>日常检查：</p>
    <pre>./status.sh {html.escape(mode)}
./doctor.sh {html.escape(mode)}</pre>
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
        length = int(self.headers.get("Content-Length") or "0")
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        mode = form.get("mode", [self.server.default_mode])[0]
        if mode not in {"dev", "prod"}:
            mode = self.server.default_mode
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
