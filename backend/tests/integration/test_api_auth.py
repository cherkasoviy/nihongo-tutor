"""initData -> JWT exchange and admin authorization over the real ASGI app (lifespan not run)."""

import datetime as dt
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.db.models import UserRole
from app.main import create_app
from app.services import user_service
from app.services.user_service import TelegramIdentity
from tests.conftest import TEST_BOT_TOKEN, fresh_tg_id
from tests.helpers.initdata import make_init_data

pytestmark = pytest.mark.integration


@pytest.fixture
async def client(migrated_database_url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(get_settings())
    app.state.bot_username = "nihongo_tutor_test_bot"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _register(sessionmaker: async_sessionmaker[AsyncSession], role: UserRole) -> int:
    tg_id = fresh_tg_id()
    async with sessionmaker() as s:
        await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=tg_id, first_name="Аня"),
            role=role,
            invited_by=None,
            daily_budget_usd=0.35,
            now=dt.datetime.now(dt.UTC),
        )
        await s.commit()
    return tg_id


def _init_data(tg_id: int, age_seconds: int = 30) -> str:
    return make_init_data(
        TEST_BOT_TOKEN, tg_user_id=tg_id, auth_date=int(dt.datetime.now(dt.UTC).timestamp()) - age_seconds
    )


async def _login(client: httpx.AsyncClient, tg_id: int) -> dict[str, str]:
    resp = await client.post("/api/auth/telegram", json={"init_data": _init_data(tg_id)})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def test_exchange_and_me(client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    tg_id = await _register(sessionmaker, UserRole.learner)
    resp = await client.post("/api/auth/telegram", json={"init_data": _init_data(tg_id)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["tg_user_id"] == tg_id
    assert body["user"]["role"] == "learner"

    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["tg_user_id"] == tg_id


async def test_unregistered_user_is_forbidden(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/auth/telegram", json={"init_data": _init_data(fresh_tg_id())})
    assert resp.status_code == 403


async def test_tampered_init_data_is_unauthorized(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tg_id = await _register(sessionmaker, UserRole.learner)
    raw = _init_data(tg_id)
    resp = await client.post("/api/auth/telegram", json={"init_data": raw[:-4] + "0000"})
    assert resp.status_code == 401


async def test_stale_init_data_is_unauthorized(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tg_id = await _register(sessionmaker, UserRole.learner)
    resp = await client.post("/api/auth/telegram", json={"init_data": _init_data(tg_id, age_seconds=3 * 24 * 3600)})
    assert resp.status_code == 401


async def test_admin_routes_guarded(client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    learner = await _login(client, await _register(sessionmaker, UserRole.learner))
    admin = await _login(client, await _register(sessionmaker, UserRole.admin))

    assert (await client.get("/api/admin/invites", headers=learner)).status_code == 403
    assert (await client.post("/api/admin/invites", json={"max_uses": 2}, headers=learner)).status_code == 403

    created = await client.post("/api/admin/invites", json={"max_uses": 2, "ttl_days": 7}, headers=admin)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["max_uses"] == 2 and body["uses"] == 0
    assert body["start_link"] == f"https://t.me/nihongo_tutor_test_bot?start={body['code']}"

    listed = await client.get("/api/admin/invites", headers=admin)
    assert listed.status_code == 200
    assert [i["code"] for i in listed.json()] == [body["code"]]

    revoked = await client.delete(f"/api/admin/invites/{body['id']}", headers=admin)
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True
