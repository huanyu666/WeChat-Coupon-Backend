from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_runtime_migration():
    module_path = PROJECT_ROOT / "utils" / "runtime_migration.py"
    spec = importlib.util.spec_from_file_location("wx_runtime_migration", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载迁移工具: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_runtime_migration = _load_runtime_migration()
MigrationError = _runtime_migration.MigrationError
create_migration_archive = _runtime_migration.create_migration_archive
inspect_migration_archive = _runtime_migration.inspect_migration_archive
restore_migration_archive = _runtime_migration.restore_migration_archive


def _run_command(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=str(PROJECT_ROOT), env=env, check=True)


def _wait_for_healthz(base_url: str, timeout_seconds: float) -> None:
    deadline = time.time() + max(timeout_seconds, 5.0)
    last_error: Exception | None = None
    request = urllib.request.Request(
        base_url.rstrip("/") + "/healthz",
        headers={"Accept": "application/json"},
    )
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            if int(response.getcode() or 0) == 200 and isinstance(payload, dict) and payload.get("ok") is True:
                return
            last_error = RuntimeError(f"unexpected healthz payload: status={response.getcode()} payload={payload}")
        except Exception as exc:
            last_error = exc
        time.sleep(1.0)
    raise RuntimeError(f"healthz not ready within {timeout_seconds:.1f}s") from last_error


def _archive_has_site_verification_files(inspect_result: dict[str, object]) -> bool:
    manifest = inspect_result.get("manifest")
    if not isinstance(manifest, dict):
        return False

    items = manifest.get("items")
    if not isinstance(items, list):
        return False

    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("root") != "runtime_data":
            continue
        if item.get("type") != "file":
            continue
        relative_path = str(item.get("path") or "").strip()
        if relative_path.startswith("site-verification/"):
            return True
    return False


def _validate_import_target(target_dir: Path, *, require_site_verification_files: bool) -> tuple[list[str], list[str]]:
    required_paths = [
        target_dir / "config.toml",
        target_dir / "wechat_accounts.runtime.json",
        target_dir / "system_settings.runtime.json",
        target_dir / "merchant_coupons" / "merchant_coupons.db",
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    warnings: list[str] = []

    site_verification_dir = target_dir / "site-verification"
    if not site_verification_dir.is_dir():
        missing.append(str(site_verification_dir))
    else:
        verification_files = [path for path in site_verification_dir.iterdir() if path.is_file()]
        if require_site_verification_files and not verification_files:
            missing.append(f"{site_verification_dir} (empty)")
        elif not verification_files:
            warnings.append(f"{site_verification_dir} (no files in archive)")

    return missing, warnings


def _run_startup_check(target_dir: Path, port: int, timeout_seconds: float, image: str) -> None:
    startup_temp_dir = Path(tempfile.mkdtemp(prefix="wx-coupon-migration-startup-"))
    container_name = f"wx-coupon-migration-drill-{secrets.token_hex(4)}"
    env = os.environ.copy()
    env.update(
        {
            "GO_SHORTLINK_PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
            "WX_SMOKE_BASE_URL": f"http://127.0.0.1:{port}",
        }
    )

    try:
        for directory in ("logs", "runtime", "go", "redis"):
            (startup_temp_dir / directory).mkdir(parents=True, exist_ok=True)

        _run_command(
            [
                "docker",
                "run",
                "--rm",
                "-d",
                "--name",
                container_name,
                "-p",
                f"127.0.0.1:{port}:80",
                "-e",
                "WX_SERVICE_ENV=drill",
                "-e",
                "WX_SERVICE_DEPLOYMENT_NAME=wx-coupon-migration-drill",
                "-e",
                "WX_SERVICE_DATA_DIR=/data",
                "-e",
                "WX_SERVICE_RUNTIME_DIR=/run/wx_service-python",
                "-e",
                "WX_SERVICE_LOG_DIR=/logs",
                "-e",
                "WX_SERVICE_SOCKET_PATH=",
                "-e",
                "WX_SERVICE_AUTO_START_GO=false",
                "-e",
                "GO_INTERNAL_API_SOCKET_PATH=/run/wx_service/meituan-query-internal.sock",
                "-e",
                f"GO_SHORTLINK_PUBLIC_BASE_URL=http://127.0.0.1:{port}",
                "-v",
                f"{target_dir}:/data",
                "-v",
                f"{startup_temp_dir / 'logs'}:/logs",
                "-v",
                f"{startup_temp_dir / 'runtime'}:/run/wx_service-python",
                "-v",
                f"{startup_temp_dir / 'go'}:/run/wx_service",
                "-v",
                f"{startup_temp_dir / 'redis'}:/run/redis",
                image,
            ],
            env=env,
        )

        deadline = time.time() + max(timeout_seconds, 5.0)
        last_error: Exception | None = None
        base_url = f"http://127.0.0.1:{port}"
        while time.time() < deadline:
            try:
                _wait_for_healthz(base_url, timeout_seconds=15.0)
                _run_command(
                    [
                        "python3",
                        "scripts/business_smoke_check.py",
                        "--base-url",
                        base_url,
                        "--timeout",
                        "8",
                    ],
                    env=env,
                )
                print(f"MIGRATION_DRILL_STARTUP_OK base_url={base_url}")
                return
            except (subprocess.CalledProcessError, RuntimeError) as exc:
                last_error = exc
                time.sleep(1.0)

        try:
            log_excerpt = subprocess.check_output(
                ["docker", "logs", container_name],
                cwd=str(PROJECT_ROOT),
                text=True,
                stderr=subprocess.STDOUT,
            )[-1200:]
        except Exception:
            log_excerpt = ""
        if last_error is not None:
            raise RuntimeError(
                f"startup smoke did not pass within {timeout_seconds:.1f}s log={log_excerpt!r}"
            ) from last_error
        raise RuntimeError(f"startup smoke timed out after {timeout_seconds:.1f}s log={log_excerpt!r}")
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        shutil.rmtree(startup_temp_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a full migration export/inspect/import drill for wx-coupon.")
    parser.add_argument("--archive", default="", help="Existing migration archive path. If omitted, export a fresh archive first.")
    parser.add_argument("--runtime-dir", default="", help="Override runtime-data directory for export.")
    parser.add_argument("--target-dir", default="", help="Import target directory. Defaults to a temporary directory.")
    parser.add_argument("--include-env", action="store_true", help="Include .env during export.")
    parser.add_argument("--restore-env", action="store_true", help="Restore .env during import.")
    parser.add_argument("--keep-target-dir", action="store_true", help="Keep the temporary import directory after the drill.")
    parser.add_argument("--startup-check", action="store_true", help="Start an isolated local instance with the imported runtime directory and run smoke checks.")
    parser.add_argument("--startup-port", type=int, default=18180, help="Port used by the optional isolated startup check.")
    parser.add_argument("--startup-timeout", type=float, default=30.0, help="Timeout in seconds for the optional isolated startup check.")
    parser.add_argument("--startup-image", default="wx-coupon-backend:dev", help="Docker image used by the optional isolated startup check.")
    args = parser.parse_args()

    runtime_dir = Path(args.runtime_dir).expanduser().resolve() if args.runtime_dir else None
    archive_path = Path(args.archive).expanduser().resolve() if args.archive else None

    temp_target_dir: str | None = None
    if args.target_dir:
        target_dir = Path(args.target_dir).expanduser().resolve()
    else:
        temp_target_dir = tempfile.mkdtemp(prefix="wx-coupon-migration-drill-")
        target_dir = Path(temp_target_dir).resolve()

    try:
        if archive_path is None:
            export_result = create_migration_archive(
                include_env=args.include_env,
                runtime_dir=runtime_dir,
                project_root=PROJECT_ROOT,
            )
            archive_path = Path(export_result["archive_path"]).resolve()
            print(f"MIGRATION_DRILL_EXPORT_OK archive={archive_path}")
        else:
            print(f"MIGRATION_DRILL_EXPORT_SKIPPED archive={archive_path}")

        inspect_result = inspect_migration_archive(archive_path)
        print(
            "MIGRATION_DRILL_INSPECT_OK "
            f"files={inspect_result['runtime_file_count']} dirs={inspect_result['runtime_dir_count']} "
            f"size={inspect_result['runtime_total_size']} env_included={str(inspect_result['env_included']).lower()}"
        )

        import_result = restore_migration_archive(
            archive_path,
            target_dir=target_dir,
            project_root=PROJECT_ROOT,
            restore_env=args.restore_env,
            apply=True,
        )
        print(f"MIGRATION_DRILL_IMPORT_OK target={import_result['target_dir']}")

        require_site_verification_files = _archive_has_site_verification_files(inspect_result)
        missing, warnings = _validate_import_target(
            target_dir,
            require_site_verification_files=require_site_verification_files,
        )
        if missing:
            print("MIGRATION_DRILL_VALIDATE_FAILED missing=" + ", ".join(missing), file=sys.stderr)
            return 1

        for warning in warnings:
            print(f"MIGRATION_DRILL_VALIDATE_WARN {warning}")
        if args.startup_check:
            _run_startup_check(
                target_dir,
                port=int(args.startup_port),
                timeout_seconds=float(args.startup_timeout),
                image=str(args.startup_image),
            )
        print(f"MIGRATION_DRILL_VALIDATE_OK target={target_dir}")
        return 0
    except MigrationError as exc:
        print(f"MIGRATION_DRILL_FAILED {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_target_dir and not args.keep_target_dir:
            shutil.rmtree(temp_target_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
