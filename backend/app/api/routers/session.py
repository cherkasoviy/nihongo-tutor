"""``/api/session``: the Mini App's view of today's lesson.

Deliberately the same engine the bot drives — ``session_service`` owns the rules, and
``session_steps.status`` means a step answered in chat is already closed when the Mini App asks for
it. That is what lets a learner start in one client and finish in the other.
"""

from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import CurrentUser
from app.api.schemas import AnswerIn, AnswerOut, SessionOut, SessionStepOut
from app.db.base import SessionDep
from app.db.models.learning import LearningSession, SessionClient, SessionStep
from app.domain.grading import SelfGrade
from app.services import session_service

router = APIRouter(prefix="/session", tags=["session"])


def _step_out(step: SessionStep | None) -> SessionStepOut | None:
    if step is None:
        return None
    payload = step.payload
    return SessionStepOut(
        id=step.id,
        idx=step.idx,
        kind=step.kind.value,
        status=step.status.value,
        mode=str(payload.get("mode", "choice")),
        prompt=payload.get("prompt"),
        char=payload.get("char"),
        cyrillic=payload.get("cyrillic"),
        mnemonic_ru=payload.get("mnemonic_ru"),
        example_word=payload.get("example_word"),
        example_gloss_ru=payload.get("example_gloss_ru"),
        choices=[str(c) for c in payload.get("choices", [])],
    )


def _session_out(learning: LearningSession, current: SessionStep | None) -> SessionOut:
    return SessionOut(
        id=learning.id,
        local_date=learning.local_date,
        planned_steps=learning.planned_steps,
        completed_steps=learning.completed_steps,
        outcome=learning.outcome.value,
        current=_step_out(current),
    )


@router.post("/today", response_model=SessionOut)
async def start_today(user: CurrentUser, session: SessionDep) -> SessionOut:
    """Start or resume today's session. Idempotent: calling it twice returns the same session."""
    now = dt.datetime.now(dt.UTC)
    learning, _ = await session_service.start_or_resume(session, user=user, now=now, client=SessionClient.miniapp)
    current = await session_service.next_step(session, learning_session_id=learning.id)
    if current is not None:
        await session_service.mark_shown(session, step=current, now=now, message_id=None)
    await session.commit()
    return _session_out(learning, current)


@router.post("/steps/{step_id}/answer", response_model=AnswerOut)
async def answer_step(step_id: uuid.UUID, body: AnswerIn, user: CurrentUser, session: SessionDep) -> AnswerOut:
    now = dt.datetime.now(dt.UTC)
    step = await session.get(SessionStep, step_id)
    if step is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown step")

    learning = await session.get(LearningSession, step.session_id)
    if learning is None or learning.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown step")

    if body.acknowledged:
        outcome = await session_service.acknowledge(session, step=step, now=now)
    elif body.self_grade is not None:
        outcome = await session_service.submit_self_grade(
            session, user=user, step=step, grade=SelfGrade(body.self_grade), now=now
        )
    elif body.choice is not None:
        outcome = await session_service.submit_choice(session, user=user, step=step, choice=body.choice, now=now)
    else:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="no answer supplied")

    nxt = await session_service.next_step(session, learning_session_id=learning.id)
    finished = nxt is None
    if nxt is None:
        await session_service.finish(session, user=user, learning=learning, now=now)
    elif outcome.accepted:
        await session_service.mark_shown(session, step=nxt, now=now, message_id=None)
    await session.commit()

    return AnswerOut(
        accepted=outcome.accepted,
        correct=outcome.correct,
        correct_label=outcome.correct_label,
        rating=int(outcome.rating) if outcome.rating is not None else None,
        session_finished=finished,
        next=_step_out(nxt),
    )
