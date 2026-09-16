"""The cache key has to cover everything that changes the bytes."""

from __future__ import annotations

from app.speech.google_tts import wrap_ssml
from app.speech.keys import SLOW_RATE, Encoding, Ssml, clip_hash, clip_path


def _key(**over: object) -> str:
    base: dict[str, object] = {
        "provider": "google",
        "voice": "ja-JP-Neural2-B",
        "rate": 1.0,
        "ssml": Ssml.plain,
        "text": "みず",
    }
    return clip_hash(**{**base, **over})  # type: ignore[arg-type]


def test_the_same_request_is_the_same_key() -> None:
    assert _key() == _key()


def test_every_input_that_changes_the_audio_changes_the_key() -> None:
    baseline = _key()
    assert _key(text="みせ") != baseline, "different text"
    assert _key(voice="ja-JP-Neural2-C") != baseline, "different voice"
    assert _key(rate=0.7) != baseline, "different rate"
    assert _key(ssml=Ssml.slow) != baseline, "normal and slow must not collide"
    assert _key(provider="azure") != baseline, "a new engine must not serve the old one's audio"


def test_rate_is_normalised_so_one_number_is_one_key() -> None:
    """0.7 and 0.70 are the same speaking rate and must not be two cache entries."""
    assert _key(rate=0.7) == _key(rate=0.70)


def test_the_path_is_the_key() -> None:
    digest = _key()
    assert clip_path("/data/audio", digest, Encoding.mp3).name == f"{digest}.mp3"
    assert clip_path("/data/audio", digest, Encoding.ogg).name == f"{digest}.ogg"


def test_the_normal_case_sends_no_markup_at_all() -> None:
    """Google bills SSML tags as characters. A bare <speak></speak> is 15 of them, and with one
    request per encoding that is 18,060 billed characters across full seed coverage against 4,244
    of actual Japanese — 4.3x the content, for a wrapper the normal case does not need."""
    assert wrap_ssml("みず", Ssml.plain) == "みず"
    assert "<" not in wrap_ssml("これは何ですか", Ssml.plain)


def test_slow_is_the_same_text_under_a_prosody_rate() -> None:
    slow = wrap_ssml("みず", Ssml.slow)
    assert f'rate="{SLOW_RATE:.0%}"' in slow and "みず" in slow
    assert slow.startswith("<speak>")


def test_markup_in_the_text_cannot_escape_the_ssml_document() -> None:
    """Only the SSML path escapes, because only it builds a document. The plain path sends the
    text verbatim, where < and & are just characters."""
    assert "<b>" not in wrap_ssml("a<b>c", Ssml.slow)
    assert "&amp;" in wrap_ssml("a & b", Ssml.slow)
