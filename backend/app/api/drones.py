from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import Settings, get_settings
from app.schemas.drones import DroneCatalog, DroneDetail
from app.services.drones import DroneCatalogService, DroneCatalogUnavailable

router = APIRouter(prefix="/drones", tags=["drones"])


def get_drone_catalog(settings: Annotated[Settings, Depends(get_settings)]) -> DroneCatalogService:
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
    slug: str, service: Annotated[DroneCatalogService, Depends(get_drone_catalog)],
) -> DroneDetail:
    try:
        detail = service.detail(slug)
    except DroneCatalogUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if detail is None:
        raise HTTPException(status_code=404, detail="未找到该无人机型号。")
    return detail
