"""Точка входа FastAPI."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router

app = FastAPI(title="chinese_reader", version="0.1.0")

# В разработке фронт живёт на отдельном порту Vite. В бою фронт отдаёт
# reverse proxy с того же origin, и CORS не нужен вовсе.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api")
