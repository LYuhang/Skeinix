"""Password hashing — argon2id via passlib (OWASP #1 recommendation)."""
from passlib.context import CryptContext

# If argon2-cffi is unavailable, change "argon2" -> "bcrypt".
_ctx = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(plain: str) -> str:
    return _ctx.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _ctx.verify(plain, hashed)
