from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import Settings, get_settings
from app.schemas.drones import DroneCatalog, DroneDetail
from app.services.drones import DroneCatalogService, DroneCatalogUnavailable

router = APIRouter(prefix="/drones", tags=["drones"])


def get_drone_catalog(
    settings: Annotated[Settings, Depends(get_settings)],
    brand: Annotated[
        Literal["dji", "parrot", "autel", "hubsan", "skydio"], Query(description="无人机品牌")
    ] = "dji",
) -> DroneCatalogService:
    if brand == "hubsan":
        return DroneCatalogService(
            settings.hubsan_documents_dir / "机型参数目录", manufacturer="Hubsan"
        )
    if brand == "skydio":
        return DroneCatalogService(
            settings.skydio_documents_dir / "机型参数目录", manufacturer="Skydio"
        )
    if brand == "autel":
        return DroneCatalogService(
            settings.autel_documents_dir / "机型参数目录", manufacturer="Autel"
        )
    if brand == "parrot":
        return DroneCatalogService(settings.parrot_documents_dir, manufacturer="Parrot")
    return DroneCatalogService(settings.drone_documents_dir)


@router.get("", response_model=DroneCatalog)
def list_drones(
    service: Annotated[DroneCatalogService, Depends(get_drone_catalog)],
) -> DroneCatalog:
    try:
        return service.catalog()
    except DroneCatalogUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{slug}", response_model=DroneDetail)
def get_drone(
    slug: str,
    service: Annotated[DroneCatalogService, Depends(get_drone_catalog)],
) -> DroneDetail:
    try:
        detail = service.detail(slug)
    except DroneCatalogUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="未找到该无人机型号。")
    return detail
