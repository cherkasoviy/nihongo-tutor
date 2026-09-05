"""A ``Bot`` whose session never touches the network: queued results in, recorded requests out."""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncGenerator
from typing import Any

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import UNSET_PARSE_MODE, Chat, Message, ResponseParameters, User


class MockedSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.responses: deque[Any] = deque()
        self.requests: deque[TelegramMethod[Any]] = deque()
        self.closed = True

    def add_result(self, result: Any) -> None:
        self.responses.append(result)

    def get_request(self) -> TelegramMethod[Any]:
        return self.requests.popleft()

    async def close(self) -> None:
        self.closed = True

    async def make_request(
        self, bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = UNSET_PARSE_MODE
    ) -> TelegramType:
        self.closed = False
        self.requests.append(method)
        if self.responses:
            return self.responses.popleft()  # type: ignore[no-any-return]
        # Default: a plausible Message for send/edit methods, True for everything else.
        return _default_result(method)  # type: ignore[return-value]

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:  # pragma: no cover - not used
        yield b""


def _default_result(method: TelegramMethod[Any]) -> Any:
    name = method.__api_method__
    if name in {"sendMessage", "editMessageText", "sendVoice", "sendPhoto"}:
        chat_id = getattr(method, "chat_id", 1)
        return Message(
            message_id=1,
            date=0,
            chat=Chat(id=int(chat_id) if isinstance(chat_id, int | str) else 1, type="private"),
            text=getattr(method, "text", None),
        )
    if name == "getMe":
        return User(id=42, is_bot=True, first_name="Tutor", username="nihongo_tutor_test_bot")
    return True


class MockedBot(Bot):
    def __init__(self, token: str = "42:TEST") -> None:  # noqa: S107
        super().__init__(
            token=token,
            session=MockedSession(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self._me = User(id=42, is_bot=True, first_name="Tutor", username="nihongo_tutor_test_bot")

    @property
    def mocked(self) -> MockedSession:
        assert isinstance(self.session, MockedSession)
        return self.session

    def sent_texts(self) -> list[str]:
        return [str(getattr(m, "text", "")) for m in self.mocked.requests if m.__api_method__ == "sendMessage"]


__all__ = ["MockedBot", "MockedSession", "ResponseParameters"]
