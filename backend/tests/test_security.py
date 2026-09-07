"""Password hashing and JWT tests (no database).

`app/core/security.py` had no tests at all, despite being the file that decides
whether a request is authenticated. Everything here is pure — hash a string,
sign a token, read it back — so these run in milliseconds and need no fixtures.

The negative cases matter more than the positive ones. A bug that makes login
*fail* is obvious in a second of manual use; a bug that makes a forged or
expired token *pass* is invisible until it is exploited.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    def test_hash_does_not_contain_the_password(self):
        """The stored value must not reveal the input.

        Trivially true for bcrypt, but it is the property the whole scheme
        exists to provide, so it is worth pinning.
        """
        hashed = hash_password("correct horse battery staple")
        assert "correct horse battery staple" not in hashed

    def test_verify_accepts_the_right_password(self):
        hashed = hash_password("s3cret-password")
        assert verify_password("s3cret-password", hashed) is True

    def test_verify_rejects_the_wrong_password(self):
        hashed = hash_password("s3cret-password")
        assert verify_password("s3cret-passwore", hashed) is False
        assert verify_password("", hashed) is False

    def test_same_password_hashes_differently_every_time(self):
        """Proves the hashes are salted.

        Without a per-hash salt, two users choosing the same password would
        store identical values — which leaks that fact, and makes one
        precomputed table useful against every account at once.
        """
        a = hash_password("identical")
        b = hash_password("identical")
        assert a != b
        # ...and both still verify, so the salt is stored inside the hash
        # rather than needing a separate column.
        assert verify_password("identical", a)
        assert verify_password("identical", b)

    def test_hash_is_a_bcrypt_hash(self):
        """The prefix records the algorithm and work factor.

        Pinned because it is what lets passlib verify an old hash after the
        scheme is changed — the parameters travel with the value.
        """
        assert hash_password("x").startswith("$2b$")


class TestTokenRoundTrip:
    def test_subject_and_role_survive(self):
        token = create_access_token(42, "DISPATCHER")
        payload = decode_access_token(token)
        # "sub" is a STRING even though we passed an int: the JWT spec requires
        # it, which is why the auth dependency parses it back with int().
        assert payload["sub"] == "42"
        assert payload["role"] == "DISPATCHER"

    def test_carries_issued_and_expiry_claims(self):
        payload = decode_access_token(create_access_token(1, "VIEWER"))
        assert "iat" in payload and "exp" in payload
        assert payload["exp"] > payload["iat"]

    def test_expiry_honours_the_configured_default(self):
        payload = decode_access_token(create_access_token(1, "VIEWER"))
        lifetime_min = (payload["exp"] - payload["iat"]) / 60
        # Within a minute of the configured value — the two claims are stamped
        # by separate now() calls, so exact equality would be flaky.
        assert abs(lifetime_min - settings.access_token_expire_minutes) < 1

    def test_explicit_expiry_overrides_the_default(self):
        payload = decode_access_token(create_access_token(1, "VIEWER", expires_minutes=5))
        assert abs((payload["exp"] - payload["iat"]) / 60 - 5) < 1


class TestTokenRejection:
    """The cases that actually protect anything."""

    def test_expired_token_is_rejected(self):
        # Negative lifetime, so it is already expired when minted.
        token = create_access_token(1, "ADMIN", expires_minutes=-1)
        with pytest.raises(jwt.ExpiredSignatureError):
            decode_access_token(token)
        # Note the library enforces this, not our code — which is the point:
        # an expired token raises rather than returning a payload a caller
        # might forget to check.

    def test_token_signed_with_another_secret_is_rejected(self):
        forged = jwt.encode(
            {"sub": "1", "role": "ADMIN", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
            # >= 32 bytes, so PyJWT does not emit a key-length warning and
            # clutter the test output.
            "an-attackers-key-of-at-least-32-bytes!!",
            algorithm=settings.algorithm,
        )
        with pytest.raises(jwt.InvalidSignatureError):
            decode_access_token(forged)

    def test_tampered_payload_is_rejected(self):
        """Escalating a role by editing the token must break the signature."""
        header, payload, signature = create_access_token(1, "VIEWER").split(".")
        # Re-sign nothing — just swap in a different payload segment and keep
        # the original signature.
        forged_payload = (
            jwt.encode(
                {"sub": "1", "role": "ADMIN"},
                "any-key-will-do-we-only-want-the-payload",
                algorithm="HS256",
            ).split(".")[1]
        )
        with pytest.raises(jwt.PyJWTError):
            decode_access_token(f"{header}.{forged_payload}.{signature}")

    def test_unsigned_token_is_rejected(self):
        """The classic JWT attack: claim alg "none" and omit the signature.

        This is what `algorithms=[...]` in decode_access_token prevents. Were
        the algorithm taken from the token's own header instead, this would be
        a valid admin token.
        """
        unsigned = jwt.encode({"sub": "1", "role": "ADMIN"}, key="", algorithm="none")
        with pytest.raises(jwt.PyJWTError):
            decode_access_token(unsigned)

    def test_malformed_token_is_rejected(self):
        for garbage in ("", "not-a-token", "a.b.c", "..."):
            with pytest.raises(jwt.PyJWTError):
                decode_access_token(garbage)

    def test_missing_expiry_is_still_decodable(self):
        """Documents actual behaviour rather than asserting a wish.

        PyJWT only enforces `exp` when the claim is present, so a token with no
        expiry never expires. Nothing in this codebase mints one — every token
        goes through create_access_token, which always sets it — so this is a
        property of the library, recorded so the guarantee is not overstated.
        """
        no_exp = jwt.encode({"sub": "1", "role": "ADMIN"}, settings.secret_key,
                            algorithm=settings.algorithm)
        assert decode_access_token(no_exp)["sub"] == "1"
