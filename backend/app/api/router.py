from fastapi import APIRouter

from app.api import contents, database, health, literature, search, sources, supplier

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(sources.router)
api_router.include_router(contents.router)
api_router.include_router(search.router)
api_router.include_router(database.router)
api_router.include_router(literature.router)
api_router.include_router(supplier.router)
