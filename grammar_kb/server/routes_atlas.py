"""词光词条查询与媒体文件。媒体缺文件时 404，调用方退回 TTS。"""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import FileResponse

from ..atlas import (
    ATTRIBUTION,
    LICENSE,
    LICENSE_URL,
    SOURCE_REPO,
    count_entries,
    lookup_public,
    media_ready,
    resolve_atlas_dir,
    resolve_media_file,
)
from .http import _ok

_MIME = {".webp": "image/webp", ".mp3": "audio/mpeg"}


def register_atlas(app, ctx):
    kbq = ctx.kbq

    @app.get("/atlas-info")
    def atlas_info():
        """导入规模与署名。媒体目录空着也返回 200，前端据此隐藏插画。"""
        directory = resolve_atlas_dir()
        return _ok({
            "count": count_entries(kbq.db.conn),
            "license": LICENSE,
            "license_url": LICENSE_URL,
            "attribution": ATTRIBUTION,
            "source": SOURCE_REPO,
            "modified": False,
            "media_ready": media_ready(directory),
            "media_dir": str(directory),
        })

    @app.get("/atlas/{word:path}")
    def atlas_word(word: str):
        entry = lookup_public(kbq.db.conn, word, full=True)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"词光词库未收录：{word}")
        return _ok(entry)

    @app.get("/atlas-media/{tail:path}")
    def atlas_media(tail: str):
        """插画与录音。不要求登录：``<img>`` / ``<audio>`` 带不上 Authorization。

        这些文件是 CC BY 4.0 的公开词库素材，不是学生进度。
        """
        path = resolve_media_file(resolve_atlas_dir(), tail)
        if path is None:
            raise HTTPException(status_code=404, detail="词光媒体文件不存在")
        return FileResponse(
            path,
            media_type=_MIME.get(path.suffix, "application/octet-stream"),
            headers={"Cache-Control": "public, max-age=86400"},
        )
