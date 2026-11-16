"""Signed outcomes. The tool code signs with a local key that the model never sees.

`steward verify` reads the outcome log with a three-value contract:
0 every row is valid, 10 at least one row is not, 3 the check could not run.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

from . import store

CLEAN, FINDINGS, UNUSABLE = 0, 10, 3


class SigningKeyError(Exception):
    pass


class Signer:
    def __init__(self, key: bytes):
        self.key = key

    @classmethod
    def load(cls, path: Path, create: bool = False) -> "Signer":
        if not path.exists():
            if not create:
                raise SigningKeyError(f"no signing key at {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(secrets.token_bytes(32))
        key = path.read_bytes()
        if not key:
            raise SigningKeyError(f"signing key at {path} is empty")
        return cls(key)

    def sign(self, obj: object) -> str:
        return hmac.new(self.key, store.canonical(obj).encode(), hashlib.sha256).hexdigest()

    def verify(self, obj: object, sig: object) -> bool:
        return isinstance(sig, str) and hmac.compare_digest(self.sign(obj), sig)


def verify_log(log: Path, key_path: Path, field: str = "outcome") -> tuple[int, list[str]]:
    """Check every row of a signed log. Returns (code, messages)."""
    try:
        signer = Signer.load(key_path)
    except (OSError, SigningKeyError) as e:
        return UNUSABLE, [f"cannot load key: {e}"]
    if not log.exists():
        return UNUSABLE, [f"no log at {log}"]
    try:
        rows = store.read(log)
    except OSError as e:
        return UNUSABLE, [f"cannot read {log}: {e}"]
    problems = [f"line {n} does not parse" for n in rows.bad_lines]
    for i, row in enumerate(rows.rows, 1):
        if not signer.verify(row.get(field), row.get("sig")):
            problems.append(f"row {i} has no valid signature: {store.canonical(row.get(field))[:120]}")
    if problems:
        return FINDINGS, problems
    return CLEAN, [f"{len(rows.rows)} signed rows, all valid"]
