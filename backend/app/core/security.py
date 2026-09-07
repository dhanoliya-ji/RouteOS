"""Password hashing and JWT token helpers.

Two unrelated cryptographic jobs that happen to both belong at the bottom of
the stack:

    passwords  -> hashed once at registration, verified at login
    tokens     -> signed at login, verified on every subsequent request

Nothing here touches the database or knows what a request is. `routes/auth.py`
calls the password functions; `api/dependencies/auth.py` calls the token ones.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import settings

# bcrypt, via passlib.
#
# bcrypt is a *deliberately slow* hash. That is the point: a fast hash (md5,
# sha256) lets an attacker who steals the table try billions of guesses per
# second, while bcrypt's work factor makes each guess expensive. It also salts
# every hash automatically, so two users with the same password get different
# stored values and a precomputed rainbow table is useless.
#
# deprecated="auto" means that if the scheme list is ever extended, hashes made
# with an older scheme still verify — so passwords can be migrated without
# forcing everyone to reset.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage.

    The result embeds the algorithm, work factor and salt, which is why no
    separate salt column is needed. Only this value is ever stored — the
    plaintext exists just long enough to hash it.
    """
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a login attempt against a stored hash.

    passlib re-derives the hash using the salt and work factor recorded inside
    `hashed_password`, then compares in constant time — so the comparison
    itself does not leak how much of the password was correct.
    """
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str | int, role: str, expires_minutes: int | None = None) -> str:
    """Mint a signed JWT identifying a user.

    A JWT is *signed, not encrypted*: anyone holding it can read the payload,
    but cannot alter it without invalidating the signature. So the payload may
    carry identifiers — never anything secret.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload: dict[str, Any] = {
        # "sub" (subject) is the registered JWT claim for "who this is about".
        # Stringified because the spec requires a string, and the auth
        # dependency parses it back to an int.
        "sub": str(subject),
        # The role rides along so a permission check needs no database lookup
        # for it. Safe because the signature makes it untamperable.
        "role": role,
        # "exp" is enforced by the library on decode, not by our code — an
        # expired token raises rather than returning a payload we might forget
        # to check. UTC throughout: a naive local timestamp would expire at the
        # wrong moment on a differently-configured host.
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Verify a token's signature and expiry, returning its payload.

    Raises a `jwt.PyJWTError` subclass on a bad signature, a malformed token or
    an expired one — the caller in `api/dependencies/auth.py` turns that into a
    401.

    Note `algorithms=[...]` is an explicit allow-list. Accepting whatever the
    token's own header claims is the classic JWT vulnerability: an attacker
    sets `alg: none` (or swaps to a weaker algorithm) and forges a token. By
    naming the algorithm we accept, the header cannot influence verification.
    """
    return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
