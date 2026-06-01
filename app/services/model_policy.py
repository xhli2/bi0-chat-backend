from app.core.config import get_settings
from app.core.exceptions import ApiError


def validate_model_for_tenant(model: str, tenant_id: str) -> None:
    settings = get_settings()
    if model == settings.model_router_auto_alias:
        return
    allowlist = settings.parsed_model_allowlist
    if model not in allowlist:
        raise ApiError(
            status_code=400,
            code="MODEL_NOT_ALLOWED",
            detail=f"Model '{model}' is not in global allowlist.",
        )

    tenant_policies = settings.parsed_tenant_model_policies
    tenant_allowed = tenant_policies.get(tenant_id)
    if tenant_allowed is not None and "*" not in tenant_allowed and model not in tenant_allowed:
        raise ApiError(
            status_code=403,
            code="TENANT_MODEL_FORBIDDEN",
            detail=f"Tenant '{tenant_id}' cannot use model '{model}'.",
        )
