from fastapi import FastAPI

from api.routes import router

app = FastAPI(
    title="PDF RAG Service",
    description="PoC service for parsing PDFs, embedding chunks, and searching via Qdrant.",
    version="0.1.0",
)

app.include_router(router)
