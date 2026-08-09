from vibecanvas_api.storage.models import (
    Base, User,
)


def test_auth_tables_registered():
    names = set(Base.metadata.tables.keys())
    assert {"tenants", "users", "auth_identities", "sessions",
            "password_reset_tokens"} <= names


def test_user_email_unique():
    assert any(c.unique for c in User.__table__.c if c.name == "email")
