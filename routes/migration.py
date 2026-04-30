from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from utils.auth_utils import get_current_user
from utils.logger import setup_logger
from utils.path_utils import resolve_project_path
from utils.runtime_migration import (
    MigrationError,
    create_migration_archive,
    get_max_import_bytes,
    get_migration_archive_dir,
    get_migration_runtime_data_dir,
    inspect_migration_archive,
    list_migration_archives,
    resolve_migration_archive,
    restore_migration_archive,
    safe_migration_archive_filename,
    summarize_runtime_data,
)


logger = setup_logger(__name__)
templates = Jinja2Templates(directory=str(resolve_project_path("html")))
router = APIRouter(prefix="", tags=["数据迁移"])


def _safe_archive_filename(filename: str | None) -> str:
    try:
        return safe_migration_archive_filename(filename or "migration.tar.gz")
    except MigrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _validate_archive_filename(filename: str) -> None:
    lower_name = filename.lower()
    if not (lower_name.endswith(".tar.gz") or lower_name.endswith(".tgz")):
        raise HTTPException(status_code=400, detail="只支持 .tar.gz 或 .tgz 迁移包")


def _migration_error_response(exc: MigrationError, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"success": False, "error": str(exc)}, status_code=status_code)


def _audit_migration(action: str, operator: str, success: bool, **fields) -> None:
    safe_fields = {
        key: value
        for key, value in fields.items()
        if key in {"file", "kind", "include_env", "restore_env", "pre_backup", "error", "restart_required"}
    }
    log = logger.info if success else logger.warning
    log("migration_audit action=%s operator=%s success=%s fields=%s", action, operator, success, safe_fields)


def _reload_runtime_configs(operator: str, action: str) -> list[str]:
    warnings: list[str] = []
    try:
        from routes.wechat import reload_wechat_runtime_configs
        from utils.wechat_utils import access_token_cache

        reload_wechat_runtime_configs()
        access_token_cache.clear()
    except Exception as exc:
        logger.exception("迁移操作后重载配置失败: action=%s operator=%s", action, operator)
        warnings.append(f"数据已更新，但运行配置重载失败：{exc.__class__.__name__}")
    return warnings


@router.get("/migration", response_class=HTMLResponse)
async def migration_page(request: Request):
    return templates.TemplateResponse(request, "migration.html", {"request": request})


@router.get("/api/migration/status")
async def migration_status(current_user: str = Depends(get_current_user)):
    runtime_dir = get_migration_runtime_data_dir()
    return JSONResponse({
        "success": True,
        "operator": current_user,
        "runtime": summarize_runtime_data(runtime_dir),
        "max_import_bytes": get_max_import_bytes(),
        "archives": list_migration_archives(runtime_dir=runtime_dir),
    })


@router.get("/api/migration/export")
async def migration_export(
    include_env: bool = False,
    current_user: str = Depends(get_current_user),
):
    runtime_dir = get_migration_runtime_data_dir()
    try:
        result = create_migration_archive(include_env=include_env, runtime_dir=runtime_dir)
    except MigrationError as exc:
        _audit_migration("export", current_user, False, include_env=include_env, error=str(exc))
        return _migration_error_response(exc, status_code=400)

    archive_path = Path(result["archive_path"])
    _audit_migration(
        "export",
        current_user,
        True,
        file=archive_path.name,
        include_env=include_env,
    )
    return FileResponse(
        archive_path,
        media_type="application/gzip",
        filename=archive_path.name,
    )


@router.get("/api/migration/archive")
async def migration_archive_download(
    kind: str,
    name: str,
    current_user: str = Depends(get_current_user),
):
    runtime_dir = get_migration_runtime_data_dir()
    try:
        archive_path = resolve_migration_archive(kind, name, runtime_dir=runtime_dir)
        inspect_migration_archive(archive_path, max_bytes=get_max_import_bytes())
    except MigrationError as exc:
        _audit_migration("download", current_user, False, kind=kind, file=name, error=str(exc))
        return _migration_error_response(exc, status_code=400)

    _audit_migration("download", current_user, True, kind=kind, file=archive_path.name)
    return FileResponse(
        archive_path,
        media_type="application/gzip",
        filename=archive_path.name,
    )


@router.post("/api/migration/inspect")
async def migration_inspect(
    archive: UploadFile = File(...),
    current_user: str = Depends(get_current_user),
):
    filename = _safe_archive_filename(archive.filename)
    _validate_archive_filename(filename)
    upload_dir = get_migration_archive_dir("imports", runtime_dir=get_migration_runtime_data_dir())
    upload_path = upload_dir / f"inspect-{filename}"
    max_bytes = get_max_import_bytes()
    total_size = 0

    try:
        with upload_path.open("wb") as output_file:
            while True:
                chunk = await archive.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"迁移包超过限制：{max_bytes} bytes")
                output_file.write(chunk)
        inspected = inspect_migration_archive(upload_path, max_bytes=max_bytes)
    except HTTPException:
        try:
            upload_path.unlink()
        except FileNotFoundError:
            pass
        raise
    except MigrationError as exc:
        try:
            upload_path.unlink()
        except FileNotFoundError:
            pass
        _audit_migration("inspect", current_user, False, file=filename, error=str(exc))
        return _migration_error_response(exc, status_code=400)

    _audit_migration("inspect", current_user, True, file=filename)
    return JSONResponse({
        "success": True,
        "operator": current_user,
        "archive": inspected,
    })


@router.post("/api/migration/import")
async def migration_import(
    archive: UploadFile = File(...),
    confirm: bool = Form(False),
    restore_env: bool = Form(False),
    current_user: str = Depends(get_current_user),
):
    if not confirm:
        raise HTTPException(status_code=400, detail="导入前必须勾选确认覆盖当前运行数据")

    filename = _safe_archive_filename(archive.filename)
    _validate_archive_filename(filename)
    runtime_dir = get_migration_runtime_data_dir()
    upload_dir = get_migration_archive_dir("imports", runtime_dir=runtime_dir)
    upload_path = upload_dir / f"import-{filename}"
    max_bytes = get_max_import_bytes()
    total_size = 0

    try:
        with upload_path.open("wb") as output_file:
            while True:
                chunk = await archive.read(1024 * 1024)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_bytes:
                    raise HTTPException(status_code=413, detail=f"迁移包超过限制：{max_bytes} bytes")
                output_file.write(chunk)

        result = restore_migration_archive(
            upload_path,
            target_dir=runtime_dir,
            restore_env=restore_env,
            apply=True,
            max_bytes=max_bytes,
        )
    except HTTPException:
        try:
            upload_path.unlink()
        except FileNotFoundError:
            pass
        raise
    except MigrationError as exc:
        _audit_migration("import", current_user, False, file=filename, restore_env=restore_env, error=str(exc))
        return _migration_error_response(exc, status_code=400)

    warnings = _reload_runtime_configs(current_user, "import")
    _audit_migration(
        "import",
        current_user,
        True,
        file=filename,
        restore_env=restore_env,
        pre_backup=Path(result["pre_import_backup_path"]).name if result.get("pre_import_backup_path") else "",
        restart_required=result.get("restart_required", False),
    )
    return JSONResponse({
        "success": True,
        "message": "迁移包已导入，当前运行数据已更新",
        "operator": current_user,
        "result": result,
        "warnings": warnings,
    })


@router.post("/api/migration/rollback")
async def migration_rollback(
    archive_name: str = Form(...),
    restore_env: bool = Form(False),
    current_user: str = Depends(get_current_user),
):
    runtime_dir = get_migration_runtime_data_dir()
    max_bytes = get_max_import_bytes()

    try:
        archive_path = resolve_migration_archive("pre-import", archive_name, runtime_dir=runtime_dir)
        result = restore_migration_archive(
            archive_path,
            target_dir=runtime_dir,
            restore_env=restore_env,
            apply=True,
            max_bytes=max_bytes,
        )
    except MigrationError as exc:
        _audit_migration("rollback", current_user, False, kind="pre-import", file=archive_name, restore_env=restore_env, error=str(exc))
        return _migration_error_response(exc, status_code=400)

    warnings = _reload_runtime_configs(current_user, "rollback")
    _audit_migration(
        "rollback",
        current_user,
        True,
        kind="pre-import",
        file=archive_path.name,
        restore_env=restore_env,
        pre_backup=Path(result["pre_import_backup_path"]).name if result.get("pre_import_backup_path") else "",
        restart_required=result.get("restart_required", False),
    )
    return JSONResponse({
        "success": True,
        "message": "已回滚到导入前自动备份",
        "operator": current_user,
        "result": result,
        "warnings": warnings,
    })
