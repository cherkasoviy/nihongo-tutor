import datetime as dt
import uuid

import pytest

from app.api.security import TokenError, create_access_token, decode_access_token

SECRET = "unit-test-secret-with-at-least-thirty-two-bytes"
NOW = dt.datetime.now(dt.UTC).replace(microsecond=0)


def _token(**overrides: object) -> str:
    params: dict[str, object] = {
        "user_id": uuid.UUID(int=1),
        "tg_user_id": 777,
        "role": "learner",
        "secret": SECRET,
        "ttl_seconds": 3600,
        "now": NOW,
    }
    params.update(overrides)
    return create_access_token(**params)  # type: ignore[arg-type]


def test_roundtrip() -> None:
    claims = decode_access_token(_token(), secret=SECRET)
    assert claims.user_id == uuid.UUID(int=1)
    assert claims.tg_user_id == 777
    assert claims.role == "learner"
    assert claims.expires_at == NOW + dt.timedelta(hours=1)


def test_expired_token_rejected() -> None:
    old = _token(now=NOW - dt.timedelta(hours=2))
    with pytest.raises(TokenError):
        decode_access_token(old, secret=SECRET)


def test_wrong_secret_rejected() -> None:
    with pytest.raises(TokenError):
        decode_access_token(_token(), secret="another-secret-that-is-also-long-enough-xx")


def test_tampered_payload_rejected() -> None:
    header, payload, sig = _token().split(".")
    tampered = ".".join([header, payload[:-2] + ("AA" if payload[-2:] != "AA" else "BB"), sig])
    with pytest.raises(TokenError):
        decode_access_token(tampered, secret=SECRET)


def test_empty_secret_refuses_to_issue() -> None:
    with pytest.raises(TokenError):
        _token(secret="")
