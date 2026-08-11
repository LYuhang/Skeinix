"""Meta endpoints: /version, /me, /enums."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from .. import __version__ as api_version
from ..auth.deps import current_user
from ..schemas.meta import EnumsOut, MeOut, VersionOut

router = APIRouter(prefix="/api/v1", tags=["meta"])


@router.get("/version", response_model=VersionOut)
async def version() -> VersionOut:
    """Engine + API version. No auth (used by deploy/health checks)."""
    from vibecanvas_engine import __version__ as engine_version
    return VersionOut(engine=engine_version, api=api_version)


@router.get("/public-config")
async def public_config() -> dict:
    """Unauthenticated UI feature flags. Never include secrets here."""
    from ..config import config as app_config
    return {
        "enable_test_user": bool(getattr(app_config, "enable_test_user", False)),
        "enterprise_sso_enabled": bool(
            getattr(app_config, "enterprise_sso_enabled", False)
        ),
        "account_deletion_mode": app_config.account_deletion_mode,
        "account_deletion_retention_days": (
            app_config.account_deletion_retention_days
        ),
    }


@router.get("/me", response_model=MeOut, dependencies=[Depends(current_user)])
async def me() -> MeOut:
    """Current user. Single-user dev returns the configured username."""
    from ..config import config as app_config
    username = getattr(
        getattr(app_config, "user", None), "default_username", None
    ) or "dev"
    return MeOut(username=username)


@router.get("/enums", response_model=EnumsOut,
            dependencies=[Depends(current_user)])
async def enums() -> EnumsOut:
    """Return the enum snapshot consumed by frontend code generation."""
    from ..enums import get_frontend_enums
    return EnumsOut(enums=get_frontend_enums())
