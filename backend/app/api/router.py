from __future__ import annotations

from fastapi import APIRouter

from app.api.routers import admin, auth, content, session, stats

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(session.router)
api_router.include_router(content.router)
api_router.include_router(stats.router)
