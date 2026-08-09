from __future__ import annotations

import pytest
from fastapi import HTTPException

from vibecanvas_api.routes import vfs
from vibecanvas_api.services.chat_workspace import (
    chat_id_from_workspace_scope,
    chat_workspace_scope_id,
)


class _WorkflowRepoStub:
    def __init__(self, _session, _user_id):
        pass

    async def get_meta(self, wf_id: str):
        if wf_id == "wf_ok":
            return {"wf_id": wf_id, "major_version": 1, "minor_version": 0}
        return None


def test_chat_workspace_scope_roundtrips_without_embedding_owner():
    scope = chat_workspace_scope_id("chat/含空格/abc")
    assert scope.startswith("__chatws_v2_")
    assert chat_id_from_workspace_scope(scope) == "chat/含空格/abc"
    assert "owner" not in scope


@pytest.mark.asyncio
async def test_writable_vfs_scope_accepts_only_canonical_chat_data(monkeypatch):
    monkeypatch.setattr(vfs, "WorkflowRepo", _WorkflowRepoStub)
    user_id = "user-12345678901234567890"
    scope = chat_workspace_scope_id("chatabc")
    await vfs._ensure_writable_vfs_scope(
        session=object(),
        user_id=user_id,
        wf_id=scope,
        path="/data/result.txt",
    )
    with pytest.raises(HTTPException, match="invalid_folder"):
        await vfs._ensure_writable_vfs_scope(
            session=object(),
            user_id=user_id,
            wf_id=scope,
            path="/memory/private.txt",
        )


def test_retired_user_prefixed_chat_scope_is_not_decoded():
    assert chat_id_from_workspace_scope(
        "__chatws_deadbeefdeadbeefdead_chatabc"
    ) is None
