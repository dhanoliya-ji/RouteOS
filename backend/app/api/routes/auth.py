"""Registration, login and "who am I".

The one route module that queries the database directly instead of going
through a service — the operations are single-statement and carry no business
rules beyond what is inline here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_current_user
from app.core.errors import APIError
from app.core.security import create_access_token, hash_password, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import Token, UserOut, UserRegister

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
async def register(data: UserRegister, db: AsyncSession = Depends(get_db)) -> User:
    """Create an account. Unauthenticated — anyone may register.

    Open registration suits a demo. Note UserRegister lets the caller name a
    role, so in a real deployment this endpoint would either force VIEWER or
    move behind an admin guard.
    """
    # Check first for a clean 409 rather than letting the UNIQUE constraint
    # raise an opaque IntegrityError. (Strictly this is a race: two simultaneous
    # registrations could both pass the check, and the database constraint is
    # what actually guarantees uniqueness — this only improves the error.)
    existing = (await db.execute(select(User).where(User.email == data.email))).scalar_one_or_none()
    if existing:
        raise APIError("EMAIL_TAKEN", "A user with this email already exists", 409)

    user = User(
        name=data.name,
        email=data.email,
        # Hashed here, at the boundary. The plaintext exists only for the life
        # of this call and is never stored or logged.
        password_hash=hash_password(data.password),
        role=data.role,
    )
    db.add(user)
    await db.commit()
    # refresh so server-assigned columns (id, created_at) are populated for the
    # response.
    await db.refresh(user)
    # Returned as UserOut, which has no password field — so the hash we just
    # created cannot come back out.
    return user


@router.post("/login", response_model=Token)
async def login(
    form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)
) -> Token:
    """Exchange credentials for a bearer token.

    Takes an OAuth2 password-flow **form**, not a JSON body. That is what makes
    the Authorize button in /docs work, and it is why the field is called
    `username` even though we treat it as an email — the spec fixes the name.
    """
    # OAuth2 form uses "username"; we treat it as the email.
    user = (await db.execute(select(User).where(User.email == form.username))).scalar_one_or_none()

    # One error for both "no such user" and "wrong password", deliberately: a
    # distinct message would let someone enumerate which email addresses have
    # accounts.
    if user is None or not verify_password(form.password, user.password_hash):
        raise APIError("INVALID_CREDENTIALS", "Incorrect email or password", 401)

    # A disabled account gets its own 403, because here the distinction is
    # useful and leaks nothing new — the caller has already proven the password.
    if not user.is_active:
        raise APIError("USER_DISABLED", "This account is disabled", 403)

    # The role goes into the token so permission checks need no lookup for it.
    token = create_access_token(user.id, user.role.value)
    return Token(access_token=token)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    """The caller's own record.

    The whole body is the dependency: get_current_user already decoded the
    token and loaded the row, so there is nothing left to do. Clients use this
    to validate a stored token and to render the current role.
    """
    return user
