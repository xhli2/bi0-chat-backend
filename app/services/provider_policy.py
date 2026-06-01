import re

from app.core.config import get_settings
from app.core.exceptions import ApiError

_PROVIDER_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def normalize_provider_name(name: str) -> str:
    return name.strip().lower()


def assert_provider_name_format(name: str) -> None:
    if not _PROVIDER_NAME_RE.fullmatch(name):
        raise ApiError(
            status_code=400,
            code="PROVIDER_NAME_INVALID",
            detail="provider_name must be 1-64 chars: lowercase letters, digits, hyphen, underscore.",
        )


def validate_provider_name(
    *,
    name: str | None,
    tenant_id: str,
    requires_name: bool = False,
) -> str | None:
    if requires_name and not name:
        raise ApiError(
            status_code=400,
            code="PROVIDER_NAME_REQUIRED",
            detail="provider_name is required when provider_base_url or provider_api_key is supplied.",
        )
    if name is None:
        return None

    normalized = normalize_provider_name(name)
    assert_provider_name_format(normalized)

    settings = get_settings()
    allowlist = settings.parsed_provider_allowlist
    if normalized not in allowlist:
        raise ApiError(
            status_code=400,
            code="PROVIDER_NOT_ALLOWED",
            detail=f"Provider '{normalized}' is not in global allowlist.",
        )

    tenant_policies = settings.parsed_tenant_provider_policies
    tenant_allowed = tenant_policies.get(tenant_id)
    if tenant_allowed is not None and normalized not in tenant_allowed:
        raise ApiError(
            status_code=403,
            code="TENANT_PROVIDER_FORBIDDEN",
            detail=f"Tenant '{tenant_id}' cannot use provider '{normalized}'.",
        )

    return normalized
