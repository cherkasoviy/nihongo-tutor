import uuid

from app.bot import texts_ru
from app.config import Settings
from app.db.uuid7 import uuid7
from app.services.invite_service import InviteFailure, generate_code, normalize_code


def test_admin_ids_parse_from_csv() -> None:
    s = Settings(admin_tg_ids="1, 2;3")  # type: ignore[arg-type]
    assert s.admin_tg_ids == [1, 2, 3]


def test_webhook_url_joins_cleanly() -> None:
    s = Settings(public_url="https://example.org/", webhook_path="/tg/webhook")
    assert s.webhook_url == "https://example.org/tg/webhook"


def test_uuid7_is_version_7_and_monotonic() -> None:
    ids = [uuid7() for _ in range(2000)]
    assert all(u.version == 7 for u in ids)
    assert all(isinstance(u, uuid.UUID) for u in ids)
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)


def test_invite_codes_avoid_ambiguous_chars() -> None:
    for _ in range(200):
        code = generate_code()
        assert len(code) == 8
        assert not set(code) & set("0O1I")
    assert normalize_code("  ab12cd34 ") == "AB12CD34"


def test_every_invite_failure_has_russian_text() -> None:
    for failure in InviteFailure:
        assert failure in texts_ru.INVITE_INVALID
        assert any("Ѐ" <= ch <= "ӿ" for ch in texts_ru.INVITE_INVALID[failure])
