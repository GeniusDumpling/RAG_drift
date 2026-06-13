from fastapi import APIRouter

from app.api import health, sources

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(sources.router)
