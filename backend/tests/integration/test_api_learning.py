"""The Phase 1 routes over the real ASGI app: kana grid, session, stats, settings.

These exist because the routes had no coverage at all and a plain ``vars()`` on a slotted dataclass
took ``/api/stats`` down with a 500 the moment it was called for real. Service-level tests could not
see it: the defect lived in the translation between the service's dataclass and the response model,
which is exactly the seam a route test covers and a service test does not.
"""

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.content_pipeline.import_kana import import_kana
from app.db.models import Card, CardDirection, CardState, SessionStep, StepKind, User, UserRole
from app.domain.session_planner import MAX_NEW_PER_DAY
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


# --- timezone adoption ------------------------------------------------------
# The reminder cron selects on the learner's local clock, so the zone has to be right. The browser
# is the only part of the system that knows it, and the plan sources it from the Mini App.


async def _login_with_timezone(client: httpx.AsyncClient, tg_id: int, timezone: str | None) -> dict[str, object]:
    init_data = make_init_data(
        TEST_BOT_TOKEN, tg_user_id=tg_id, auth_date=int(dt.datetime.now(dt.UTC).timestamp()) - 30
    )
    body: dict[str, object] = {"init_data": init_data}
    if timezone is not None:
        body["timezone"] = timezone
    resp = await client.post("/api/auth/telegram", json=body)
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


async def test_the_browsers_timezone_is_adopted_on_first_login(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tg_id = await _learner(sessionmaker, with_kana=False)
    body = await _login_with_timezone(client, tg_id, "Asia/Tokyo")
    assert body["user"]["timezone"] == "Asia/Tokyo"  # type: ignore[index]


async def test_a_timezone_chosen_in_settings_survives_the_next_login(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Otherwise opening the app from a laptop abroad would silently move every reminder."""
    tg_id = await _learner(sessionmaker, with_kana=False)
    first = await _login_with_timezone(client, tg_id, "Asia/Tokyo")
    headers = {"Authorization": f"Bearer {first['access_token']}"}

    await client.patch("/api/settings", json={"timezone": "Europe/Berlin"}, headers=headers)

    again = await _login_with_timezone(client, tg_id, "America/New_York")
    assert again["user"]["timezone"] == "Europe/Berlin"  # type: ignore[index]


async def test_a_nonsense_timezone_from_the_client_is_ignored(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tg_id = await _learner(sessionmaker, with_kana=False)
    body = await _login_with_timezone(client, tg_id, "Mars/Olympus_Mons")
    assert body["user"]["timezone"] == "Europe/Moscow"  # type: ignore[index]


async def test_login_without_a_timezone_changes_nothing(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    tg_id = await _learner(sessionmaker, with_kana=False)
    body = await _login_with_timezone(client, tg_id, None)
    assert body["user"]["timezone"] == "Europe/Moscow"  # type: ignore[index]


async def test_a_self_graded_step_is_revealed_before_it_is_graded(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """Revealing is not answering: the step stays open, and the wait is what decides Hard.

    A card only graduates to free recall once it reaches ``review`` state, which takes days of real
    scheduling. Rather than wait, this puts one card there directly — the API test is about the
    reveal/grade round trip, and the graduation rule itself is covered by the 30-day simulation.
    """
    tg_id = await _learner(sessionmaker)
    headers = await _auth(client, tg_id)

    # One sitting to create the cards, answered so the session closes.
    session = (await client.post("/api/session/today", headers=headers)).json()
    step = session["current"]
    while step is not None:
        want = step["char"] if step["kind"] == "review_prod" else step["cyrillic"]
        body = {"acknowledged": True} if step["mode"] == "ack" else {"choice": step["choices"].index(want)}
        step = (await client.post(f"/api/session/steps/{step['id']}/answer", json=body, headers=headers)).json()["next"]

    async with sessionmaker() as s:
        card = (
            await s.scalars(
                select(Card)
                .join(User, User.id == Card.user_id)
                .where(User.tg_user_id == tg_id, Card.direction == CardDirection.recognition)
                .limit(1)
            )
        ).one()
        card.state = CardState.review
        card.due = dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
        await s.commit()

    practice = (await client.post("/api/session/practice", headers=headers)).json()
    assert practice["kind"] == "practice"

    # The graduated card is somewhere in the sitting, not necessarily first: other cards are on
    # their learning steps and are due too.
    async with sessionmaker() as s:
        rows = list(
            await s.scalars(
                select(SessionStep).where(SessionStep.session_id == uuid.UUID(practice["id"])).order_by(SessionStep.idx)
            )
        )
        graduated = [r for r in rows if r.payload.get("mode") == "self"]
        assert graduated, [r.payload.get("mode") for r in rows]
        self_step = {
            "id": str(graduated[0].id),
            "cyrillic": graduated[0].payload["cyrillic"],
            "choices": graduated[0].payload.get("choices", []),
        }

    assert self_step["choices"] == [], "free recall must not ship the answer in the payload"

    revealed = await client.post(f"/api/session/steps/{self_step['id']}/reveal", headers=headers)
    assert revealed.status_code == 200
    assert revealed.json()["answer"] == self_step["cyrillic"]

    # Revealing must not close the step.
    graded = await client.post(
        f"/api/session/steps/{self_step['id']}/answer", json={"self_grade": "knew"}, headers=headers
    )
    assert graded.status_code == 200
    body = graded.json()
    assert body["accepted"] and body["correct"]


async def test_revealing_someone_elses_step_is_not_found(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    mine = await _auth(client, await _learner(sessionmaker))
    theirs = await _auth(client, await _learner(sessionmaker, with_kana=False))
    step = (await client.post("/api/session/today", headers=mine)).json()["current"]

    resp = await client.post(f"/api/session/steps/{step['id']}/reveal", headers=theirs)
    assert resp.status_code == 404


# --- pace -------------------------------------------------------------------


async def test_the_pace_can_be_chosen_and_reset(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    headers = await _auth(client, await _learner(sessionmaker, with_kana=False))

    assert (await client.get("/api/auth/me", headers=headers)).json()["daily_new_items_target"] is None

    chosen = (await client.patch("/api/settings", json={"daily_new_items_target": 15}, headers=headers)).json()
    assert chosen["daily_new_items_target"] == 15

    back = (await client.patch("/api/settings", json={"reset_new_items_target": True}, headers=headers)).json()
    assert back["daily_new_items_target"] is None, "resetting returns to the stage default"


@pytest.mark.parametrize("pace", [0, -1, MAX_NEW_PER_DAY + 1, 999])
async def test_an_unsurvivable_pace_is_refused(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession], pace: int
) -> None:
    headers = await _auth(client, await _learner(sessionmaker, with_kana=False))
    resp = await client.patch("/api/settings", json={"daily_new_items_target": pace}, headers=headers)
    assert resp.status_code == 422


async def test_a_chosen_pace_changes_how_much_the_lesson_teaches(
    client: httpx.AsyncClient, sessionmaker: async_sessionmaker[AsyncSession]
) -> None:
    """The setting has to reach the planner, not just the users table."""
    headers = await _auth(client, await _learner(sessionmaker))
    await client.patch("/api/settings", json={"daily_new_items_target": 3}, headers=headers)

    session = (await client.post("/api/session/today", headers=headers)).json()
    async with sessionmaker() as s:
        intros = int(
            (
                await s.execute(
                    select(func.count())
                    .select_from(SessionStep)
                    .where(
                        SessionStep.session_id == uuid.UUID(session["id"]),
                        SessionStep.kind == StepKind.intro_item,
                    )
                )
            ).scalar_one()
        )
    assert intros == 3, f"asked for 3 new syllables, lesson introduced {intros}"
