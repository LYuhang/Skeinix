from unittest.mock import AsyncMock, MagicMock

import pytest

from vibecanvas_api.authorization.parent_resolvers import _vfs_run_parent
from vibecanvas_api.authorization.types import ResourceRef, ResourceType


@pytest.mark.asyncio
async def test_vfs_run_parent_ignores_node_rows_without_workflow_id():
    """A run contains both workflow-owned and node-result rows."""
    no_chat = MagicMock()
    no_chat.scalar_one_or_none.return_value = None
    workflow_row = MagicMock()
    workflow_row.scalar_one_or_none.return_value = "wf-1"
    session = MagicMock()
    session.execute = AsyncMock(side_effect=[no_chat, workflow_row])

    parent = await _vfs_run_parent(
        session,
        ResourceRef(ResourceType.VFS_RUN, "run-1", "org-1"),
    )

    assert parent == ResourceRef(ResourceType.WORKFLOW, "wf-1", "org-1")
    workflow_query = str(session.execute.await_args_list[1].args[0])
    assert "vfs_run.wf_id IS NOT NULL" in workflow_query
