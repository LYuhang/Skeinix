"""Server-owned active-organization admission helpers."""

from __future__ import annotations

from fastapi import HTTPException, status

from vibecanvas_api.auth.deps import AuthContext


def require_active_organization(
    auth: AuthContext,
    organization_id: str,
) -> None:
    """Reject path/header attempts to select a different organization."""
    if organization_id != auth.active_organization_id:
        # Do not disclose whether the target organization exists.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="organization_not_found",
        )
