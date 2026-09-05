"""Import every model module so ``Base.metadata`` is complete for Alembic autogenerate."""

from app.db.models.ai import AiUsageLedger
from app.db.models.audio import AudioAsset
from app.db.models.content import Item, ItemStage, ItemType
from app.db.models.learning import Card, CardDirection, CardState, ReviewLog
from app.db.models.users import Invite, InviteRedemption, User, UserRole, UserStatus

__all__ = [
    "AiUsageLedger",
    "AudioAsset",
    "Card",
    "CardDirection",
    "CardState",
    "Invite",
    "InviteRedemption",
    "Item",
    "ItemStage",
    "ItemType",
    "ReviewLog",
    "User",
    "UserRole",
    "UserStatus",
]
