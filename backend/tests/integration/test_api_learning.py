"""The Phase 1 routes over the real ASGI app: kana grid, session, stats, settings.

These exist because the routes had no coverage at all and a plain ``vars()`` on a slotted dataclass
took ``/api/stats`` down with a 500 the moment it was called for real. Service-level tests could not
see it: the defect lived in the translation between the service's dataclass and the response model,
which is exactly the seam a route test covers and a service test does not.
"""

import datetime as dt
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.content_pipeline.import_kana import import_kana
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


async def _learner(sessionmaker: async_sessionmaker[AsyncSession], *, with_kana: bool = True) -> int:
    tg_id = fresh_tg_id()
    async with sessionmaker() as s:
        if with_kana:
            await import_kana(s)
        await user_service.create_user(
            s,
            TelegramIdentity(tg_user_id=tg_id, first_name="Аня"),
            role=UserRole.learner,
            invited_by=None,
            daily_budget_usd=0.35,
            now=dt.datetime.now(dt.UTC),
        )
        await s.commit()
    return tg_id


async def _auth(client: httpx.AsyncClient, tg_id: int) -> dict[str, str]:
    init_data = make_init_data(
        TEST_BOT_TOKEN, tg_user_id=tg_id, auth_date=int(dt.datetime.now(dt.UTC).timestamp()) - 30
    )
    resp = await client.post("/api/auth/telegram", json={"init_data": init_data})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# --- stats ------------------------------------------------------------------


async def test_stats_serialises_the_service_dataclass(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The regression guard: LearnerStats is slotted, so ``vars()`` on it raises TypeError."""
    headers = await _auth(client, await _learner(sessionmaker))

    resp = await client.get("/api/stats", headers=headers)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["kana_total"] == 208
    assert body["kana_introduced"] == 0
    assert body["streak_current"] == 0
    assert body["retention_7d"] is None  # not enough history to mean anything yet


async def test_stats_moves_after_a_session(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers = await _auth(client, await _learner(sessionmaker))
    await client.post("/api/session/today", headers=headers)

    body = (await client.get("/api/stats", headers=headers)).json()
    assert body["kana_introduced"] > 0, "starting a session introduces the day's new syllables"


# --- kana grid --------------------------------------------------------------


async def test_kana_grid_returns_the_whole_syllabary_in_order(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers = await _auth(client, await _learner(sessionmaker))

    resp = await client.get("/api/content/kana", headers=headers)
    assert resp.status_code == 200
    cells = resp.json()

    assert len(cells) == 208
    assert [c["group_order"] for c in cells] == sorted(c["group_order"] for c in cells)
    assert all(not c["introduced"] for c in cells)
    assert {c["script"] for c in cells} == {"hiragana", "katakana"}


async def test_kana_grid_can_be_filtered_by_script(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers = await _auth(client, await _learner(sessionmaker))

    cells = (await client.get("/api/content/kana?script=hiragana", headers=headers)).json()
    assert len(cells) == 104
    assert {c["script"] for c in cells} == {"hiragana"}
    assert cells[0]["char"] == "あ"


# --- session ----------------------------------------------------------------


async def test_starting_today_is_idempotent(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The Mini App re-mounts freely; a second call must return the same session, not a new one."""
    headers = await _auth(client, await _learner(sessionmaker))

    first = (await client.post("/api/session/today", headers=headers)).json()
    second = (await client.post("/api/session/today", headers=headers)).json()

    assert first["id"] == second["id"]
    assert first["planned_steps"] == second["planned_steps"] > 0
    assert first["current"]["id"] == second["current"]["id"]


async def test_a_session_can_be_played_to_the_end(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers = await _auth(client, await _learner(sessionmaker))
    session = (await client.post("/api/session/today", headers=headers)).json()
    step = session["current"]

    graded = 0
    for _ in range(200):
        if step is None:
            break
        if step["mode"] == "ack":
            body = {"acknowledged": True}
        else:
            want = step["char"] if step["kind"] == "review_prod" else step["cyrillic"]
            body = {"choice": step["choices"].index(want)}
        resp = await client.post(f"/api/session/steps/{step['id']}/answer", json=body, headers=headers)
        assert resp.status_code == 200, resp.text
        result = resp.json()
        assert result["accepted"]
        if step["mode"] != "ack":
            assert result["correct"], f"the right answer was graded wrong: {step}"
            graded += 1
        step = result["next"]

    assert graded > 0
    assert step is None, "the session must run out of steps"
    assert (await client.get("/api/stats", headers=headers)).json()["streak_current"] == 1


async def test_answering_a_step_twice_is_rejected_without_regrading(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The same guard the bot relies on: two clients, or one impatient tap, grade once."""
    headers = await _auth(client, await _learner(sessionmaker))
    step = (await client.post("/api/session/today", headers=headers)).json()["current"]
    body = {"acknowledged": True} if step["mode"] == "ack" else {"choice": step["choices"].index(step["cyrillic"])}

    first = (await client.post(f"/api/session/steps/{step['id']}/answer", json=body, headers=headers)).json()
    second = (await client.post(f"/api/session/steps/{step['id']}/answer", json=body, headers=headers)).json()

    assert first["accepted"] is True
    assert second["accepted"] is False


async def test_a_step_belonging_to_another_learner_is_not_found(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Step ids are opaque but guessable enough to be worth checking ownership on."""
    mine = await _auth(client, await _learner(sessionmaker))
    theirs = await _auth(client, await _learner(sessionmaker, with_kana=False))

    step = (await client.post("/api/session/today", headers=mine)).json()["current"]
    resp = await client.post(f"/api/session/steps/{step['id']}/answer", json={"acknowledged": True}, headers=theirs)
    assert resp.status_code == 404


async def test_an_answer_with_no_verdict_is_rejected(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers = await _auth(client, await _learner(sessionmaker))
    step = (await client.post("/api/session/today", headers=headers)).json()["current"]

    resp = await client.post(f"/api/session/steps/{step['id']}/answer", json={}, headers=headers)
    assert resp.status_code == 422


# --- settings ---------------------------------------------------------------


async def test_settings_round_trip(client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
    headers = await _auth(client, await _learner(sessionmaker, with_kana=False))

    resp = await client.patch(
        "/api/settings",
        json={
            "timezone": "Europe/Berlin",
            "reminder_time": "19:30:00",
            "daily_minutes_target": 20,
            "furigana_mode": "always",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["timezone"] == "Europe/Berlin"
    assert body["reminder_time"] == "19:30:00"
    assert body["daily_minutes_target"] == 20
    assert body["furigana_mode"] == "always"

    cleared = await client.patch("/api/settings", json={"clear_reminder": True}, headers=headers)
    assert cleared.json()["reminder_time"] is None


async def test_an_unknown_timezone_is_refused(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Accepting it would silently move every future reminder and local date to UTC."""
    headers = await _auth(client, await _learner(sessionmaker, with_kana=False))

    resp = await client.patch("/api/settings", json={"timezone": "Mars/Olympus_Mons"}, headers=headers)
    assert resp.status_code == 422

    assert (await client.get("/api/auth/me", headers=headers)).json()["timezone"] == "Europe/Moscow"
