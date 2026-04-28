from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from utils.path_utils import resolve_runtime_data_path

router = APIRouter()

_FILENAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_MAX_FILE_BYTES = 4096


def _verification_file_path(filename: str):
    normalized = str(filename or "").strip()
    if normalized.endswith(".txt"):
        normalized = normalized[:-4]
    if not _FILENAME_RE.fullmatch(normalized):
        raise HTTPException(status_code=404, detail="Not Found")
    root = resolve_runtime_data_path("site-verification").resolve()
    path = (root / f"{normalized}.txt").resolve()
    if root != path.parent:
        raise HTTPException(status_code=404, detail="Not Found")
    return path


@router.get("/{filename}.txt")
async def site_verification_file(filename: str):
    path = _verification_file_path(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Not Found")
    content = path.read_bytes()
    if len(content) > _MAX_FILE_BYTES:
        raise HTTPException(status_code=404, detail="Not Found")
    return Response(
        content=content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "no-store",
        },
    )
