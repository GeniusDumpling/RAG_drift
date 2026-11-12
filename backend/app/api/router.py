from fastapi import APIRouter

from app.api import (
    contents,
    database,
    document_graph,
    document_library,
    drones,
    health,
    ifixit_media,
    knowledge_graph,
    literature,
    scheduled_tasks,
    search,
    sources,
    supplier,
    video_graph,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(ifixit_media.router)
api_router.include_router(sources.router)
api_router.include_router(contents.router)
api_router.include_router(search.router)
api_router.include_router(database.router)
api_router.include_router(document_library.router)
api_router.include_router(document_graph.router)
api_router.include_router(knowledge_graph.router)
api_router.include_router(drones.router)
api_router.include_router(literature.router)
api_router.include_router(supplier.router)
api_router.include_router(scheduled_tasks.router)
api_router.include_router(video_graph.router)
