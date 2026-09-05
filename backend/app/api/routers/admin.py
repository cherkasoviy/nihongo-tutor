"""Admin endpoints. Phase 0: invites."""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, HTTPException, Request, status

from app.api.deps import AdminUser
from app.api.schemas import InviteCreate, InviteOut
from app.db.base import SessionDep
from app.services import invite_service

router = APIRouter(prefix="/admin", tags=["admin"])


def _bot_username(request: Request) -> str | None:
    return getattr(request.app.state, "bot_username", None)


@router.post("/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
async def create_invite(body: InviteCreate, admin: AdminUser, session: SessionDep, request: Request) -> InviteOut:
    invite = await invite_service.create_invite(
        session,
        created_by=admin,
        max_uses=body.max_uses,
        ttl=dt.timedelta(days=body.ttl_days) if body.ttl_days else None,
        note=body.note,
    )
    await session.commit()
    return InviteOut.from_invite(invite, _bot_username(request))


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(admin: AdminUser, session: SessionDep, request: Request) -> list[InviteOut]:
    invites = await invite_service.list_invites(session)
    username = _bot_username(request)
    return [InviteOut.from_invite(i, username) for i in invites]


@router.delete("/invites/{invite_id}", response_model=InviteOut)
async def revoke_invite(invite_id: uuid.UUID, admin: AdminUser, session: SessionDep, request: Request) -> InviteOut:
    invite = await invite_service.revoke_invite(session, invite_id)
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="invite not found")
    await session.commit()
    return InviteOut.from_invite(invite, _bot_username(request))
