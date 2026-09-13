"""Read-only production snapshot for the owner's phone.

Exists because diagnosing the duplicate-practice bug took a day, and every step of it needed a
laptop with a psql client. The owner is usually holding a phone.

Deliberately narrow: counts, settings and session shapes. No message content, no answers, nothing
a learner said. If something here would embarrass a learner to know was readable, it does not
belong in this module.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.content import Item
from app.db.models.learning import Card, CardState, LearningSession, SessionKind, SessionOutcome
from app.db.models.users import User
from app.domain import clock
from app.services import placement_service

MAX_LEARNERS: Final = 20


@dataclass(frozen=True, slots=True)
class SessionRow:
    kind: str
    outcome: str
    planned: int
    completed: int
    started_local: str


@dataclass(frozen=True, slots=True)
class LearnerDiag:
    tg_user_id: int
    role: str
    timezone: str
    local_date: dt.date
    daily_minutes_target: int
    daily_new_items_target: int | None
    cards_by_state: dict[str, int]
    due_now: int
    claimed_unverified: int
    sessions_today: tuple[SessionRow, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class ContentDiag:
    by_type: dict[str, tuple[int, int]]  # type -> (total, active)


@dataclass(frozen=True, slots=True)
class Diagnostics:
    generated_at: dt.datetime
    content: ContentDiag
    learners: tuple[LearnerDiag, ...]


async def _content(session: AsyncSession) -> ContentDiag:
    rows = await session.execute(
        select(Item.type, func.count(), func.count().filter(Item.active.is_(True))).group_by(Item.type)
    )
    return ContentDiag(by_type={t.value: (int(total), int(active)) for t, total, active in rows})


async def _learner(session: AsyncSession, user: User, now: dt.datetime) -> LearnerDiag:
    today = clock.local_date(now, user.timezone)

    states = await session.execute(select(Card.state, func.count()).where(Card.user_id == user.id).group_by(Card.state))
    by_state = {s.value: int(n) for s, n in states}
    for state in CardState:
        by_state.setdefault(state.value, 0)

    due = await session.scalar(
        select(func.count())
        .select_from(Card)
        .where(Card.user_id == user.id, Card.suspended.is_(False), Card.due <= now, Card.state != CardState.new)
    )

    rows = await session.scalars(
        select(LearningSession)
        .where(LearningSession.user_id == user.id, LearningSession.local_date == today)
        .order_by(LearningSession.started_at)
    )
    sessions = tuple(
        SessionRow(
            kind=r.kind.value,
            outcome=r.outcome.value,
            planned=r.planned_steps,
            completed=r.completed_steps,
            started_local=clock.local_now(r.started_at, user.timezone).strftime("%H:%M"),
        )
        for r in rows
    )

    return LearnerDiag(
        tg_user_id=user.tg_user_id,
        role=user.role.value,
        timezone=user.timezone,
        local_date=today,
        daily_minutes_target=user.daily_minutes_target,
        daily_new_items_target=user.daily_new_items_target,
        cards_by_state=by_state,
        due_now=int(due or 0),
        claimed_unverified=await placement_service.claimed_but_unverified(session, user_id=user.id),
        sessions_today=sessions,
    )


async def collect(session: AsyncSession, *, now: dt.datetime) -> Diagnostics:
    users = list(await session.scalars(select(User).order_by(User.created_at).limit(MAX_LEARNERS)))
    return Diagnostics(
        generated_at=now,
        content=await _content(session),
        learners=tuple([await _learner(session, u, now) for u in users]),
    )


def _session_line(row: SessionRow) -> str:
    mark = {
        SessionOutcome.completed.value: "✓",
        SessionOutcome.in_progress.value: "…",
        SessionOutcome.abandoned.value: "✗",
    }.get(row.outcome, "?")
    label = "урок" if row.kind == SessionKind.daily.value else "повтор"
    return f"  {row.started_local} {label} {row.completed}/{row.planned} {mark}"


def render(diag: Diagnostics) -> str:
    """Short enough to read on a phone without scrolling past the interesting part."""
    out: list[str] = []
    content = ", ".join(f"{t} {active}/{total}" for t, (total, active) in sorted(diag.content.by_type.items()))
    out.append(f"<b>Контент:</b> {content or 'пусто'}")

    for lr in diag.learners:
        out.append("")
        pace = lr.daily_new_items_target if lr.daily_new_items_target is not None else "по умолчанию"
        out.append(f"<b>{lr.tg_user_id}</b> ({lr.role}) — {lr.timezone}, {lr.local_date:%d.%m}")
        out.append(f"  цель {lr.daily_minutes_target} мин, новых/день: {pace}")
        states = " ".join(f"{k}={v}" for k, v in sorted(lr.cards_by_state.items()) if v)
        out.append(f"  карточки: {states or 'нет'}")
        out.append(f"  к повтору сейчас: {lr.due_now}")
        if lr.claimed_unverified:
            out.append(f"  заявлено, но не проверено: {lr.claimed_unverified}")
        if lr.sessions_today:
            out.append("  сегодня:")
            out.extend(_session_line(r) for r in lr.sessions_today)
        else:
            out.append("  сегодня: занятий не было")

    out.append("")
    out.append(f"<i>{diag.generated_at:%d.%m %H:%M} UTC</i>")
    return "\n".join(out)
