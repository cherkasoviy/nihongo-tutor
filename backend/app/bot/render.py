"""Turning a ``session_steps`` row into a Telegram message.

One message per step, edited in place as the learner answers, so a session is a single evolving
message rather than a wall of chat history. The plan's furigana note does not apply here: the kana
stage is deliberately the part that lives in chat, because kana needs no ruby.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aiogram.types import InlineKeyboardMarkup

from app.bot import keyboards, texts_ru
from app.db.models.learning import SessionStep, StepKind
from app.services.session_service import AnswerOutcome


@dataclass(frozen=True, slots=True)
class Rendered:
    text: str
    keyboard: InlineKeyboardMarkup | None


def render_step(step: SessionStep) -> Rendered:
    payload: dict[str, Any] = step.payload
    if step.kind is StepKind.intro_item:
        return Rendered(_intro_text(payload), keyboards.ack_keyboard(step.id))

    if payload.get("mode") == "self":
        # Free recall: no options on screen, because seeing them is the answer.
        return Rendered(
            texts_ru.STEP_SELF_PROMPT.format(char=payload.get("char", "")),
            keyboards.reveal_keyboard(step.id),
        )

    choices = [str(c) for c in payload.get("choices", [])]
    if step.kind is StepKind.review_prod:
        text = texts_ru.STEP_PROD_PROMPT.format(cyrillic=payload.get("cyrillic", ""))
    elif step.kind is StepKind.wrapup:
        text = texts_ru.STEP_WRAPUP_PROMPT.format(char=payload.get("char", ""))
    else:
        text = texts_ru.STEP_RECOG_PROMPT.format(char=payload.get("char", ""))
    return Rendered(text, keyboards.choice_keyboard(step.id, choices))


def _intro_text(payload: dict[str, Any]) -> str:
    text = texts_ru.STEP_INTRO_KANA.format(
        char=payload.get("char", ""),
        cyrillic=payload.get("cyrillic", ""),
        mnemonic=payload.get("mnemonic_ru") or "",
    ).rstrip()
    word, gloss = payload.get("example_word"), payload.get("example_gloss_ru")
    if word and gloss:
        text += texts_ru.STEP_INTRO_KANA_EXAMPLE.format(word=word, gloss=gloss)
    return text


def render_revealed(step: SessionStep) -> Rendered:
    """The answer, plus the three self-grading buttons."""
    payload: dict[str, Any] = step.payload
    return Rendered(
        texts_ru.STEP_SELF_REVEALED.format(char=payload.get("char", ""), cyrillic=payload.get("cyrillic", "")),
        keyboards.self_grade_keyboard(step.id),
    )


def render_feedback(step: SessionStep, outcome: AnswerOutcome) -> str:
    """The answered step, rewritten in place: the question stays visible above the verdict."""
    if step.payload.get("mode") == "self":
        grade = str((step.result or {}).get("self_grade", "knew"))
        answered = texts_ru.STEP_SELF_REVEALED.format(
            char=step.payload.get("char", ""), cyrillic=step.payload.get("cyrillic", "")
        ).split("\n\n")[0]
        return f"{answered}\n\n{texts_ru.FEEDBACK_SELF.get(grade, texts_ru.FEEDBACK_CORRECT)}"

    question = render_step(step).text
    if outcome.correct:
        verdict = texts_ru.FEEDBACK_CORRECT
    else:
        verdict = texts_ru.FEEDBACK_WRONG.format(answer=outcome.correct_label)
    return f"{question}\n\n{verdict}"
