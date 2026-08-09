import uuid
import pytest
from sqlalchemy import text
from vibecanvas_api.storage.db import session_scope


@pytest.mark.asyncio
async def test_session_scope_sets_tenant(pg_engine):
    tid = str(uuid.uuid4())
    async with session_scope(tenant_id=tid) as s:
        got = (await s.execute(
            text("SELECT current_setting('app.tenant_id', true)"))).scalar()
        assert got == tid


@pytest.mark.asyncio
async def test_session_scope_none_leaves_unset(pg_engine):
    async with session_scope() as s:
        got = (await s.execute(
            text("SELECT current_setting('app.tenant_id', true)"))).scalar()
        assert got in (None, "")
