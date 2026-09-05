"""Import every model module so ``Base.metadata`` is complete for Alembic autogenerate."""

from app.db.models.ai import AiUsageLedger
from app.db.models.audio import AudioAsset
from app.db.models.content import Item, ItemStage, ItemType, Kana, KanaKind, KanaScript
from app.db.models.learning import (
    Card,
    CardDirection,
    CardState,
    DailyPlan,
    LearningSession,
    ReviewLog,
    SessionClient,
    SessionOutcome,
    SessionStep,
    StepKind,
    StepStatus,
    Streak,
)
from app.db.models.users import FuriganaMode, Invite, InviteRedemption, User, UserRole, UserStatus

__all__ = [
    "AiUsageLedger",
    "AudioAsset",
    "Card",
    "CardDirection",
    "CardState",
    "DailyPlan",
    "FuriganaMode",
    "Invite",
    "InviteRedemption",
    "Item",
    "ItemStage",
    "ItemType",
    "Kana",
    "KanaKind",
    "KanaScript",
    "LearningSession",
    "ReviewLog",
    "SessionClient",
    "SessionOutcome",
    "SessionStep",
    "StepKind",
    "StepStatus",
    "Streak",
    "User",
    "UserRole",
    "UserStatus",
]
