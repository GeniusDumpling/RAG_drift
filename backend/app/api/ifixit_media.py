from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.services.ifixit_media import asset_file

router = APIRouter(prefix="/ifixit-media", tags=["ifixit-media"])


@router.get("/assets/{asset_id}")
def image(asset_id: str) -> FileResponse:
    asset = asset_file(asset_id)
    if asset is None:
        raise HTTPException(404, "Image asset not found")
    path, mime = asset
    return FileResponse(
        path,
        media_type=mime,
        headers={
            "X-Content-Type-Options": "nosniff",
            "ETag": f'"{asset_id}"',
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )
