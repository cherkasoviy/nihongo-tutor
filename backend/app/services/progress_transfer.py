"""Move a learner's progress between databases.

Needed because card rows cannot simply be copied. ``cards.item_id`` points at ``items.id``, which is
a UUID minted when ``import_kana`` runs, so two databases that imported the same seed hold the same
208 syllables under entirely different ids. A dump of the learner tables restored elsewhere would
reference rows that do not exist.

So progress travels keyed by what the content actually *is* — ``(script, char, direction)`` — and the
importer resolves those against whatever ids the destination happens to use. That also makes the
transfer re-runnable: a learner can keep using the local instance while a server is being set up and
the export re-applied at cutover, which a one-shot ``pg_dump`` restore into a live database cannot do.

What travels: the learner's settings — including ``fsrs_params``, the personalised optimiser weights
that only hundreds of reviews can reproduce — their streak, every card with its full FSRS state,
every review log, and finished sessions. What does not, and why:

* ``session_steps`` and ``daily_plans`` are working state for one sitting; both are rebuilt on demand,
  and an unfinished session is skipped rather than half-copied.
* ``ai_usage_ledger`` is per-deployment billing, not learner progress.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.content import Item, ItemType, Kana
from app.db.models.learning import (
    Card,
    CardDirection,
    CardState,
    LearningSession,
    ReviewLog,
    SessionClient,
    SessionKind,
    SessionOutcome,
    Streak,
)
from app.db.models.users import FuriganaMode, UserRole
from app.logging import get_logger
from app.services import user_service
from app.services.user_service import TelegramIdentity

log = get_logger(__name__)

FORMAT_VERSION: Final = 1


@dataclass(frozen=True, slots=True)
class TransferReport:
    cards: int = 0
    reviews: int = 0
    sessions: int = 0
    skipped_unknown_syllables: int = 0
    skipped_unfinished_sessions: int = 0


def _iso(value: dt.datetime | dt.date | dt.time | None) -> str | None:
    return None if value is None else value.isoformat()


async def export_progress(session: AsyncSession, *, tg_user_id: int) -> dict[str, Any]:
    """Everything needed to continue this learner's history somewhere else."""
    user = await user_service.get_by_tg_id(session, tg_user_id)
    if user is None:
        raise LookupError(f"no learner with tg_user_id={tg_user_id}")

    rows = (
        await session.execute(
            select(Card, Kana)
            .join(Item, Item.id == Card.item_id)
            .join(Kana, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana))
            .where(Card.user_id == user.id)
            .order_by(Item.curriculum_order, Card.direction)
        )
    ).all()

    logs_by_card: dict[Any, list[ReviewLog]] = {}
    if rows:
        for entry in await session.scalars(
            select(ReviewLog).where(ReviewLog.card_id.in_([card.id for card, _ in rows])).order_by(ReviewLog.review_at)
        ):
            logs_by_card.setdefault(entry.card_id, []).append(entry)

    cards: list[dict[str, Any]] = []
    for card, kana in rows:
        cards.append(
            {
                "script": kana.script.value,
                "char": kana.char,
                "direction": card.direction.value,
                "state": card.state.value,
                "step": card.step,
                "stability": card.stability,
                "difficulty": card.difficulty,
                "due": _iso(card.due),
                "last_review": _iso(card.last_review),
                "reps": card.reps,
                "lapses": card.lapses,
                "elapsed_days": card.elapsed_days,
                "scheduled_days": card.scheduled_days,
                "suspended": card.suspended,
                "reviews": [
                    {
                        "rating": entry.rating,
                        "review_at": _iso(entry.review_at),
                        "elapsed_days": entry.elapsed_days,
                        "scheduled_days": entry.scheduled_days,
                        "state_before": entry.state_before.value,
                        "response_ms": entry.response_ms,
                        "auto_graded": entry.auto_graded,
                        "intra_session": entry.intra_session,
                    }
                    for entry in logs_by_card.get(card.id, [])
                ],
            }
        )

    finished = [
        row
        for row in await session.scalars(
            select(LearningSession)
            .where(
                LearningSession.user_id == user.id,
                LearningSession.outcome != SessionOutcome.in_progress,
            )
            .order_by(LearningSession.started_at)
        )
    ]
    streak = await session.get(Streak, user.id)

    return {
        "version": FORMAT_VERSION,
        "exported_at": _iso(dt.datetime.now(dt.UTC)),
        "learner": {
            "tg_user_id": user.tg_user_id,
            "tg_username": user.tg_username,
            "first_name": user.first_name,
            "language_code": user.language_code,
            "role": user.role.value,
            "timezone": user.timezone,
            "reminder_time": _iso(user.reminder_time),
            "daily_minutes_target": user.daily_minutes_target,
            "daily_new_items_target": user.daily_new_items_target,
            "furigana_mode": user.furigana_mode.value,
            "desired_retention": str(user.desired_retention),
            "daily_budget_usd": str(user.daily_budget_usd),
            # The learner's own optimised FSRS weights. Rebuildable only by re-running the optimiser
            # over hundreds of reviews, so losing them silently costs far more than it looks.
            "fsrs_params": user.fsrs_params,
            "onboarded_at": _iso(user.onboarded_at),
        },
        "streak": (
            None
            if streak is None
            else {
                "current": streak.current,
                "longest": streak.longest,
                "last_active_date": _iso(streak.last_active_date),
                "freezes_available": streak.freezes_available,
                "freeze_earned_week": streak.freeze_earned_week,
                "freeze_used_dates": [_iso(d) for d in (streak.freeze_used_dates or [])],
            }
        ),
        "sessions": [
            {
                "local_date": _iso(row.local_date),
                "kind": row.kind.value,
                "client": row.client.value,
                "outcome": row.outcome.value,
                "started_at": _iso(row.started_at),
                "finished_at": _iso(row.finished_at),
                "planned_steps": row.planned_steps,
                "completed_steps": row.completed_steps,
                "active_ms": row.active_ms,
            }
            for row in finished
        ],
        "cards": cards,
    }


async def import_progress(session: AsyncSession, payload: dict[str, Any]) -> TransferReport:
    """Apply an export to this database, resolving syllables by script and character.

    Idempotent, so it can be re-run right before a cutover: cards are matched on
    ``(learner, item, direction)`` and overwritten with the exported state, review logs are matched
    on ``(card, review_at, rating)``, and sessions on ``(learner, started_at, kind)``. Nothing is
    duplicated by running it twice, and a card the destination has never heard of is reported rather
    than silently dropped.
    """
    version = payload.get("version")
    if version != FORMAT_VERSION:
        raise ValueError(f"unsupported export version {version!r}; this build reads {FORMAT_VERSION}")

    learner = payload["learner"]
    user = await user_service.get_by_tg_id(session, int(learner["tg_user_id"]))
    if user is None:
        user = await user_service.create_user(
            session,
            TelegramIdentity(
                tg_user_id=int(learner["tg_user_id"]),
                username=learner.get("tg_username"),
                first_name=learner.get("first_name"),
                language_code=learner.get("language_code"),
            ),
            role=UserRole(learner.get("role", UserRole.learner.value)),
            invited_by=None,
            daily_budget_usd=0.35,
            now=dt.datetime.now(dt.UTC),
        )

    user.timezone = learner["timezone"]
    user.reminder_time = dt.time.fromisoformat(learner["reminder_time"]) if learner.get("reminder_time") else None
    user.daily_minutes_target = int(learner["daily_minutes_target"])
    user.daily_new_items_target = learner.get("daily_new_items_target")
    user.furigana_mode = FuriganaMode(learner["furigana_mode"])
    user.desired_retention = float(learner["desired_retention"])
    # ``.get`` rather than ``[]``: a payload written before these two joined the block must still
    # import, and a learner whose optimiser has never run legitimately has no weights.
    if learner.get("daily_budget_usd") is not None:
        user.daily_budget_usd = float(learner["daily_budget_usd"])
    if learner.get("fsrs_params") is not None:
        user.fsrs_params = learner["fsrs_params"]
    if learner.get("onboarded_at"):
        user.onboarded_at = dt.datetime.fromisoformat(learner["onboarded_at"])
    await session.flush()

    # (script, char) -> item id, as this database numbers them.
    items = {
        (kana.script.value, kana.char): item_id
        for kana, item_id in (
            await session.execute(
                select(Kana, Item.id).join(Item, (Item.ref_id == Kana.id) & (Item.type == ItemType.kana))
            )
        ).all()
    }

    cards_seen = reviews_added = sessions_added = unknown = 0
    for entry in payload.get("cards", []):
        item_id = items.get((entry["script"], entry["char"]))
        if item_id is None:
            unknown += 1
            continue

        direction = CardDirection(entry["direction"])
        card = await session.scalar(
            select(Card).where(Card.user_id == user.id, Card.item_id == item_id, Card.direction == direction)
        )
        if card is None:
            card = Card(user_id=user.id, item_id=item_id, direction=direction, due=dt.datetime.now(dt.UTC))
            session.add(card)

        card.state = CardState(entry["state"])
        card.step = entry["step"]
        card.stability = entry["stability"]
        card.difficulty = entry["difficulty"]
        card.due = dt.datetime.fromisoformat(entry["due"])
        card.last_review = dt.datetime.fromisoformat(entry["last_review"]) if entry.get("last_review") else None
        card.reps = entry["reps"]
        card.lapses = entry["lapses"]
        card.elapsed_days = entry["elapsed_days"]
        card.scheduled_days = entry["scheduled_days"]
        card.suspended = entry["suspended"]
        await session.flush()

        seen = {
            (row.review_at, row.rating)
            for row in await session.scalars(select(ReviewLog).where(ReviewLog.card_id == card.id))
        }
        for entry_log in entry.get("reviews", []):
            review_at = dt.datetime.fromisoformat(entry_log["review_at"])
            if (review_at, entry_log["rating"]) in seen:
                continue
            session.add(
                ReviewLog(
                    card_id=card.id,
                    rating=entry_log["rating"],
                    review_at=review_at,
                    elapsed_days=entry_log["elapsed_days"],
                    scheduled_days=entry_log["scheduled_days"],
                    state_before=CardState(entry_log["state_before"]),
                    response_ms=entry_log["response_ms"],
                    auto_graded=entry_log["auto_graded"],
                    intra_session=entry_log["intra_session"],
                )
            )
            reviews_added += 1
        cards_seen += 1

    for entry_session in payload.get("sessions", []):
        started_at = dt.datetime.fromisoformat(entry_session["started_at"])
        kind = SessionKind(entry_session["kind"])
        exists = await session.scalar(
            select(LearningSession.id).where(
                LearningSession.user_id == user.id,
                LearningSession.started_at == started_at,
                LearningSession.kind == kind,
            )
        )
        if exists is not None:
            continue
        session.add(
            LearningSession(
                user_id=user.id,
                local_date=dt.date.fromisoformat(entry_session["local_date"]),
                kind=kind,
                client=SessionClient(entry_session["client"]),
                outcome=SessionOutcome(entry_session["outcome"]),
                started_at=started_at,
                finished_at=(
                    dt.datetime.fromisoformat(entry_session["finished_at"])
                    if entry_session.get("finished_at")
                    else None
                ),
                planned_steps=entry_session["planned_steps"],
                completed_steps=entry_session["completed_steps"],
                active_ms=entry_session["active_ms"],
            )
        )
        sessions_added += 1

    if payload.get("streak"):
        data = payload["streak"]
        row = await session.get(Streak, user.id)
        if row is None:
            row = Streak(user_id=user.id)
            session.add(row)
        row.current = data["current"]
        row.longest = data["longest"]
        row.last_active_date = dt.date.fromisoformat(data["last_active_date"]) if data.get("last_active_date") else None
        row.freezes_available = data["freezes_available"]
        row.freeze_earned_week = data.get("freeze_earned_week")
        row.freeze_used_dates = [dt.date.fromisoformat(d) for d in data.get("freeze_used_dates", [])]

    await session.flush()
    report = TransferReport(
        cards=cards_seen,
        reviews=reviews_added,
        sessions=sessions_added,
        skipped_unknown_syllables=unknown,
    )
    log.info(
        "progress imported",
        tg_user_id=user.tg_user_id,
        cards=report.cards,
        reviews=report.reviews,
        sessions=report.sessions,
        skipped=report.skipped_unknown_syllables,
    )
    return report
