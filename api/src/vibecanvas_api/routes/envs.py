"""Read-only status surface for Workflow Python dependency overlays.

The HTTP surface of the content-addressed Python-library overlay build
pipeline. Phase-3 callers:

Package installation is deliberately not exposed as an HTTP operation. A new
overlay can only be prepared by the Workflow page's node/whole-workflow
execution path while it initializes the execution sandbox. ``POST /ensure`` is
kept as an explicit compatibility rejection so older clients get a useful
message instead of silently starting a build on Save.

Auth vs. data scope: these routes REQUIRE a logged-in user (``current_user``),
but ``env_builds`` is a GLOBAL, RLS-free public-PyPI registry keyed by a content
hash of the declared requirements — an overlay is shared across all tenants. So
the DB access goes through ``session_scope_admin()`` (the admin/non-tenant
session), NOT a tenant-bound one.

The durable status remains readable via ``GET /{overlay_key}``; this module
never creates or changes a dependency layer.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from ..auth.deps import AuthContext, current_user
from ..services.tenant_db import session_scope_admin
from ..storage.repo_env_builds import EnvBuildsRepo

router = APIRouter(prefix="/api/v1/envs", tags=["envs"])


class EnsureEnvBody(BaseModel):
    """A ``requirements.txt``-format declaration. Empty string = "no libs"."""

    requirements: str = ""


@router.post("/ensure")
async def ensure_env(
    body: EnsureEnvBody,
    ctx: AuthContext = Depends(current_user),
):
    """Reject the retired save-time build endpoint.

    Keeping the route during the compatibility window makes the lifecycle
    boundary visible to older web bundles without allowing an installation.
    """
    del body, ctx
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "dependencies are installed only when the Workflow page initializes "
            "a sandbox to execute a node or workflow"
        ),
    )


@router.get("/{overlay_key}")
async def env_status(
    overlay_key: str,
    ctx: AuthContext = Depends(current_user),
):
    """Poll an overlay's build status.

    Returns ``{overlay_key, status, error_log}``. ``error_log`` is ``null``
    unless the build failed. An absent key returns a uniform
    ``{status:"unknown", error_log:null}`` (200, NOT 404) so the poller has a
    single response shape to handle.
    """
    async with session_scope_admin() as s:
        row = await EnvBuildsRepo(s).get(overlay_key)
    if row is None:
        return {"overlay_key": overlay_key, "status": "unknown", "error_log": None}
    return {
        "overlay_key": overlay_key,
        "status": row["status"],
        "error_log": row.get("error_log"),
    }
