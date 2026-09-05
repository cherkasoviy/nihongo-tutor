import datetime as dt

import pytest

from app.api.security import InitDataError, verify_init_data
from tests.conftest import TEST_BOT_TOKEN
from tests.helpers.initdata import make_init_data

NOW = dt.datetime(2026, 9, 5, 12, 0, tzinfo=dt.UTC)
MAX_AGE = 24 * 3600


def test_valid_init_data_parses_user() -> None:
    raw = make_init_data(TEST_BOT_TOKEN, tg_user_id=777, auth_date=int(NOW.timestamp()) - 60)
    parsed = verify_init_data(raw, bot_token=TEST_BOT_TOKEN, max_age_seconds=MAX_AGE, now=NOW)
    assert parsed.user is not None
    assert parsed.user.id == 777
    assert parsed.user.first_name == "Аня"


def test_expired_auth_date_rejected() -> None:
    raw = make_init_data(TEST_BOT_TOKEN, tg_user_id=777, auth_date=int(NOW.timestamp()) - MAX_AGE - 1)
    with pytest.raises(InitDataError, match="too old"):
        verify_init_data(raw, bot_token=TEST_BOT_TOKEN, max_age_seconds=MAX_AGE, now=NOW)


def test_tampered_hash_rejected() -> None:
    raw = make_init_data(TEST_BOT_TOKEN, tg_user_id=777, auth_date=int(NOW.timestamp()))
    tampered = raw.replace("%22id%22%3A777", "%22id%22%3A778")  # user.id 777 -> 778, hash unchanged
    assert tampered != raw
    with pytest.raises(InitDataError, match="signature"):
        verify_init_data(tampered, bot_token=TEST_BOT_TOKEN, max_age_seconds=MAX_AGE, now=NOW)


def test_wrong_bot_token_rejected() -> None:
    raw = make_init_data("999:OTHER-TOKEN", tg_user_id=777, auth_date=int(NOW.timestamp()))
    with pytest.raises(InitDataError, match="signature"):
        verify_init_data(raw, bot_token=TEST_BOT_TOKEN, max_age_seconds=MAX_AGE, now=NOW)


@pytest.mark.parametrize("raw", ["", "hash=deadbeef", "user=%7B%7D&auth_date=1"])
def test_garbage_rejected(raw: str) -> None:
    with pytest.raises(InitDataError):
        verify_init_data(raw, bot_token=TEST_BOT_TOKEN, max_age_seconds=MAX_AGE, now=NOW)


def test_missing_bot_token_rejected() -> None:
    raw = make_init_data(TEST_BOT_TOKEN, tg_user_id=777, auth_date=int(NOW.timestamp()))
    with pytest.raises(InitDataError, match="not configured"):
        verify_init_data(raw, bot_token="", max_age_seconds=MAX_AGE, now=NOW)
