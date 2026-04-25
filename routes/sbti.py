from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, RedirectResponse, Response

from utils.path_utils import resolve_project_path


router = APIRouter(prefix="", tags=["sbti"])

SBTI_DIR = resolve_project_path("html", "sbti")
SBTI_HTML = SBTI_DIR / "sbti.html"
ALLOWED_STATIC_DIRS = {"img"}
ALLOWED_STATIC_SUFFIXES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def _html_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    }


def _asset_headers() -> dict[str, str]:
    return {
        "Cache-Control": "public, max-age=86400",
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-origin",
    }


def _resolve_static_asset(asset_path: str) -> Path:
    normalized = Path(str(asset_path or "").strip())
    if not normalized.parts:
        raise HTTPException(status_code=404, detail="Not Found")
    if normalized.parts[0] not in ALLOWED_STATIC_DIRS:
        raise HTTPException(status_code=404, detail="Not Found")
    if any(part.startswith(".") for part in normalized.parts):
        raise HTTPException(status_code=403, detail="Forbidden")

    candidate = (SBTI_DIR / normalized).resolve()
    try:
        candidate.relative_to(SBTI_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Forbidden") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    media_type = ALLOWED_STATIC_SUFFIXES.get(candidate.suffix.lower())
    if not media_type:
        raise HTTPException(status_code=403, detail="Forbidden")
    return candidate


@router.get("/sbti")
async def sbti_redirect() -> RedirectResponse:
    return RedirectResponse(url="/sbti/", status_code=307)


@router.get("/sbti/")
async def sbti_page() -> FileResponse:
    if not SBTI_HTML.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(
        path=SBTI_HTML,
        media_type="text/html; charset=utf-8",
        headers=_html_headers(),
    )


@router.get("/sbti/{asset_path:path}")
async def sbti_asset(asset_path: str) -> Response:
    asset = _resolve_static_asset(asset_path)
    return FileResponse(
        path=asset,
        media_type=ALLOWED_STATIC_SUFFIXES[asset.suffix.lower()],
        headers=_asset_headers(),
    )
