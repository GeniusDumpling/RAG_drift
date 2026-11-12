"""Standalone read-only API for the dedicated document graph instance."""

from fastapi import FastAPI

from app.api.document_graph import router

app = FastAPI(title="Document Knowledge Graph API", version="0.1.0")
app.include_router(router)
