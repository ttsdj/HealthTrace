import os
import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from backend.infra.database import SessionLocal
from backend.db.models import User

# The signing algorithm is pinned in code on purpose.  Reading it from the
# environment (previously JWT_ALGORITHM, default HS256) let a misconfiguration
# such as `none` turn into an authentication bypass.
ALGORITHM = "HS256"
MIN_JWT_SECRET_LENGTH = 32
_PLACEHOLDER_MARKERS = ("replace", "your-", "change-this", "example", "placeholder")

ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))
ADMIN_INVITE_CODE = os.getenv("ADMIN_INVITE_CODE", "")
PBKDF2_ROUNDS = int(os.getenv("PASSWORD_PBKDF2_ROUNDS", "310000"))

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


class JWTConfigurationError(RuntimeError):
    """The signing key is missing or obviously insecure."""


def is_placeholder_secret(value: str | None) -> bool:
    lowered = (value or "").strip().lower()
    return not lowered or any(marker in lowered for marker in _PLACEHOLDER_MARKERS)


def jwt_secret_key() -> str:
    """Return the JWT signing key, or raise rather than fall back to a guessable one.

    Resolved on every call instead of at import time so that a missing key can
    never sign or verify a token, while deployments that inject the secret at
    runtime still work.
    """
    raw = (os.getenv("JWT_SECRET_KEY") or "").strip()
    if len(raw) < MIN_JWT_SECRET_LENGTH or is_placeholder_secret(raw):
        raise JWTConfigurationError(
            "JWT_SECRET_KEY must be set to a non-placeholder secret of at least "
            f"{MIN_JWT_SECRET_LENGTH} characters"
        )
    return raw


def validate_jwt_configuration() -> None:
    """Raise if the JWT signing key is unusable. Used for fail-fast startup."""
    jwt_secret_key()


def _auth_not_configured() -> HTTPException:
    """503 for endpoints that need a signing key when the key is unusable.

    The reason is deliberately not echoed back: the caller is unauthenticated and
    would otherwise learn from the response whether a secret is configured.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="authentication is not configured",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_db(request: Request):
    """Yield the request Session when middleware has already created one.

    Audited requests use a single Session for authorization, endpoint work and
    the final audit record.  This prevents the audit middleware from checking
    out a second connection while the dependency Session is still held.
    """
    db = getattr(request.state, "db_session", None)
    shared = db is not None
    if db is None:
        db = SessionLocal()
    try:
        yield db
    finally:
        if not shared:
            db.close()


def verify_password(plain_password: str, password_hash: str) -> bool:
    if not plain_password or not password_hash:
        return False

    # New format: pbkdf2_sha256$<rounds>$<salt_b64>$<digest_b64>
    if password_hash.startswith("pbkdf2_sha256$"):
        try:
            _, rounds, salt_b64, digest_b64 = password_hash.split("$", 3)
            salt = base64.b64decode(salt_b64.encode("ascii"))
            expected = base64.b64decode(digest_b64.encode("ascii"))
            calculated = hashlib.pbkdf2_hmac(
                "sha256",
                plain_password.encode("utf-8"),
                salt,
                int(rounds),
            )
            return hmac.compare_digest(calculated, expected)
        except Exception:
            return False

    # Backward compatibility for legacy passlib/bcrypt hashes.
    if password_hash.startswith("$2") or password_hash.startswith("$bcrypt"):
        try:
            from passlib.context import CryptContext

            legacy_context = CryptContext(schemes=["bcrypt_sha256", "bcrypt"], deprecated="auto")
            return legacy_context.verify(plain_password, password_hash)
        except Exception:
            return False

    return False


def get_password_hash(password: str) -> str:
    if not password:
        raise ValueError("password is required")

    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ROUNDS,
    )
    salt_b64 = base64.b64encode(salt).decode("ascii")
    digest_b64 = base64.b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt_b64}${digest_b64}"


def create_access_token(username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": username,
        "role": role,
        "exp": expire,
    }
    try:
        secret = jwt_secret_key()
    except JWTConfigurationError as exc:
        raise _auth_not_configured() from exc
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    user = db.query(User).filter(User.username == username).first()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效或过期的认证令牌",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        secret = jwt_secret_key()
    except JWTConfigurationError as exc:
        raise _auth_not_configured() from exc

    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if not user:
        raise credentials_exception
    request.state.current_user = user
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理员权限不足")
    return current_user


def resolve_role(requested_role: str | None, admin_code: str | None) -> str:
    role = (requested_role or "user").strip().lower()
    if role != "admin":
        return "user"
    if ADMIN_INVITE_CODE and admin_code == ADMIN_INVITE_CODE:
        return "admin"
    raise HTTPException(status_code=403, detail="管理员邀请码错误")
