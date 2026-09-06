"""Callback-data factories.

Every callback carries the ``session_steps`` row it answers. That is what makes a double tap safe:
the handler loads the step, sees it is no longer pending, and does nothing. Telegram will happily
deliver the same callback twice (bad connection, impatient learner), so idempotency is not optional.
"""

from __future__ import annotations

import uuid

from aiogram.filters.callback_data import CallbackData


class StepChoice(CallbackData, prefix="ch"):
    """A tap on one option of a multiple-choice grid."""

    step_id: uuid.UUID
    choice: int


class StepReveal(CallbackData, prefix="rv"):
    """ "Показать ответ" on a self-graded recognition step."""

    step_id: uuid.UUID


class StepSelfGrade(CallbackData, prefix="sg"):
    """One of Не помню / Помню / Легко after the answer was revealed."""

    step_id: uuid.UUID
    grade: str


class StepAck(CallbackData, prefix="ak"):
    """ "Дальше" on an introduction step, which carries no grade."""

    step_id: uuid.UUID


class SessionAction(CallbackData, prefix="ss"):
    """Session-level controls that are not tied to a single step."""

    action: str
