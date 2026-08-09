import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from vibecanvas_api.auth.repo import AuthRepo
from vibecanvas_api.auth.password import hash_password
from vibecanvas_api.auth.tokens import new_token


@pytest.mark.asyncio
async def test_register_creates_tenant_user_identity(pg_session):
    repo = AuthRepo(pg_session)
    user = await repo.register("a@example.com", hash_password("pw12345678"))
    assert user.email == "a@example.com"
    assert user.tenant_id is not None
    found = await repo.find_identity("password", "a@example.com")
    assert found is not None and found.user_id == user.user_id

    raw = (
        await pg_session.execute(
            text(
                "SELECT u.email, u.display_name, u.profile_ciphertext, "
                "i.provider_uid, i.provider_uid_lookup_hash, "
                "i.provider_uid_ciphertext FROM users u "
                "JOIN auth_identities i ON i.user_id=u.user_id "
                "WHERE u.user_id=:user_id"
            ),
            {"user_id": user.user_id},
        )
    ).mappings().one()
    assert raw["email"] == f"redacted-{user.user_id}@invalid.local"
    assert raw["display_name"] == ""
    assert raw["provider_uid"].startswith("redacted-")
    assert "a@example.com" not in raw["profile_ciphertext"]
    assert "a@example.com" not in raw["provider_uid_ciphertext"]
    assert "a@example.com" not in raw["provider_uid_lookup_hash"]
    assert await repo.find_identity("password", " A@EXAMPLE.COM ") is not None


@pytest.mark.asyncio
async def test_session_create_and_resolve(pg_session):
    repo = AuthRepo(pg_session)
    user = await repo.register("b@example.com", hash_password("pw12345678"))
    raw, hashed = new_token()
    await repo.create_session(hashed, user.user_id, user.tenant_id,
                              expires_at=datetime.now(timezone.utc)
                              + timedelta(days=30))
    resolved = await repo.resolve_session(hashed)
    assert resolved is not None
    assert (resolved.user_id, resolved.tenant_id) == (user.user_id, user.tenant_id)


@pytest.mark.asyncio
async def test_expired_session_not_resolved(pg_session):
    repo = AuthRepo(pg_session)
    user = await repo.register("c@example.com", hash_password("pw12345678"))
    _, hashed = new_token()
    await repo.create_session(hashed, user.user_id, user.tenant_id,
                              expires_at=datetime.now(timezone.utc)
                              - timedelta(seconds=1))
    assert await repo.resolve_session(hashed) is None


@pytest.mark.asyncio
async def test_delete_session(pg_session):
    repo = AuthRepo(pg_session)
    user = await repo.register("d@example.com", hash_password("pw12345678"))
    _, hashed = new_token()
    await repo.create_session(hashed, user.user_id, user.tenant_id,
                              expires_at=datetime.now(timezone.utc)
                              + timedelta(days=30))
    await repo.delete_session(hashed)
    assert await repo.resolve_session(hashed) is None
