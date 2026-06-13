from fastapi import APIRouter

from app.api import contents, health, sources

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(sources.router)
api_router.include_router(contents.router)
