from __future__ import annotations

from fastapi import APIRouter

from app.api.routers import admin, auth

api_router = APIRouter(prefix="/api")
api_router.include_router(auth.router)
api_router.include_router(admin.router)
