"""Resolve deployment webhook secrets only inside the API host boundary."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.secret_service import secret_service


async def resolve_deployment_hmac_secret(
    session: AsyncSession,
    deployment: dict,
) -> str:
    if deployment.get("trigger_type") != "webhook":
        raise ValueError("deployment is not a webhook")
    secret_ref = deployment.get("hmac_secret_ref")
    if not secret_ref:
        raise RuntimeError("deployment webhook has no SecretService reference")
    return await secret_service().resolve_text(
        session,
        secret_ref=secret_ref,
        tenant_id=deployment["tenant_id"],
        purpose="deployment_webhook_hmac",
        resource_type="deployment",
        resource_id=deployment["id"],
    )
