"""UUIDv7 generator (RFC 9562).

Python 3.12 has no ``uuid.uuid7``; this implementation is time-ordered so B-tree primary
keys stay append-friendly. Monotonic within a process for ids created in the same millisecond.
"""

from __future__ import annotations

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_last_seq = 0


def uuid7() -> uuid.UUID:
    global _last_ms, _last_seq
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms <= _last_ms:
            now_ms = _last_ms
            _last_seq = (_last_seq + 1) & 0x0FFF
            if _last_seq == 0:  # counter overflow: borrow a millisecond
                now_ms += 1
                _last_ms = now_ms
        else:
            _last_ms = now_ms
            _last_seq = int.from_bytes(os.urandom(2), "big") & 0x0FFF
        seq = _last_seq

    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (now_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version 7
    value |= seq << 64  # rand_a used as a monotonic counter
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand_b
    return uuid.UUID(int=value)
