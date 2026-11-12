"""Standalone unified read API, including native document image retrieval."""

from fastapi import FastAPI

from app.api.document_graph import router as documents
from app.api.knowledge_graph import router

app = FastAPI(title="Unified Knowledge Graph API", version="1.0")
app.include_router(router)
app.include_router(documents)
