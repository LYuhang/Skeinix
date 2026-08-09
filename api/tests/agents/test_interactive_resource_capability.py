from __future__ import annotations

import time

from vibecanvas_api.services.vfs_signing import (
    issue_vfs_resource_capability,
    vfs_resource_access_allowed,
    verify_vfs_resource_capability,
)


def test_resource_capability_is_opaque_scoped_and_cross_worker_stateless():
    token = issue_vfs_resource_capability(
        tenant_id="tenant-secret",
        audience="interactive-artifact",
        allowed_paths=["/data/images/", "/data/report.pdf"],
        wf_id="__chatws_user_chat",
        expires_in_s=60,
    )
    assert "tenant-secret" not in token
    assert "chatws" not in token
    scope = verify_vfs_resource_capability(token)
    assert scope == {
        "tenant": "tenant-secret",
        "wf_id": "__chatws_user_chat",
        "run_id": "",
        "audience": "interactive-artifact",
        "operation": "read",
        "allowed_paths": ["/data/images/", "/data/report.pdf"],
    }
    assert vfs_resource_access_allowed(
        scope,
        audience="interactive-artifact",
        path="/data/images/frame.png",
    )
    assert not vfs_resource_access_allowed(
        scope,
        audience="file-preview",
        path="/data/images/frame.png",
    )
    assert not vfs_resource_access_allowed(
        scope,
        audience="interactive-artifact",
        path="/data/private.txt",
    )
    assert not vfs_resource_access_allowed(
        scope,
        audience="interactive-artifact",
        path="/data/report.pdf/child",
    )


def test_resource_capability_rejects_tampering_and_expiry():
    token = issue_vfs_resource_capability(
        tenant_id="tenant_1",
        audience="file-preview",
        allowed_paths=["/data/report.pdf"],
        wf_id="scope_1",
        expires_in_s=1,
    )
    assert verify_vfs_resource_capability(token[:-1] + ("A" if token[-1] != "A" else "B")) is None
    assert verify_vfs_resource_capability(token, now=int(time.time()) + 2) is None
