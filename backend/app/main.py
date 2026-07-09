from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRONTEND_DIST_DIR = PROJECT_ROOT / "frontend" / "dist"
API_PATH_PREFIXES = (
    "/health",
    "/sources",
    "/jobs",
    "/runs",
    "/contents",
    "/search",
    "/answer",
    "/search-queries",
    "/database",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def _is_api_like_path(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in API_PATH_PREFIXES)


def _add_frontend_static_routes(app: FastAPI, frontend_dist_dir: Path) -> None:
    index_file = frontend_dist_dir / "index.html"
    if not index_file.is_file():
        return

    assets_dir = frontend_dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def serve_frontend_root() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend_fallback(full_path: str) -> FileResponse:
        request_path = f"/{full_path}"
        if _is_api_like_path(request_path):
            raise HTTPException(status_code=404, detail="Not Found")

        candidate_file = frontend_dist_dir / full_path
        if candidate_file.is_file():
            return FileResponse(candidate_file)
        return FileResponse(index_file)


def create_app(frontend_dist_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Intelligence RAG API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(api_router)
    _add_frontend_static_routes(app, frontend_dist_dir or DEFAULT_FRONTEND_DIST_DIR)
    return app


app = create_app()
