from fastapi import APIRouter

from app.api import contents, database, health, search, sources

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(sources.router)
api_router.include_router(contents.router)
api_router.include_router(search.router)
api_router.include_router(database.router)
