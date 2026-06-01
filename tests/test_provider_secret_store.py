import pytest

from app.services.provider_policy import assert_provider_name_format, normalize_provider_name, validate_provider_name
from app.services.task_manager import task_manager
from app.services.url_redaction import redact_provider_base_url
from app.core.exceptions import ApiError


def test_normalize_provider_name():
    assert normalize_provider_name(" OpenAI-Compatible ") == "openai-compatible"


def test_provider_name_invalid_format():
    with pytest.raises(ApiError) as exc:
        assert_provider_name_format("openai\nhack")
    assert exc.value.code == "PROVIDER_NAME_INVALID"


def test_validate_provider_name_not_in_allowlist():
    with pytest.raises(ApiError) as exc:
        validate_provider_name(name="custom-provider", tenant_id="public")
    assert exc.value.code == "PROVIDER_NOT_ALLOWED"


def test_validate_provider_name_required_for_custom_provider():
    with pytest.raises(ApiError) as exc:
        validate_provider_name(name=None, tenant_id="public", requires_name=True)
    assert exc.value.code == "PROVIDER_NAME_REQUIRED"


def test_validate_provider_name_accepts_allowlisted_slug():
    assert validate_provider_name(name="OpenAI-Compatible", tenant_id="public") == "openai-compatible"


@pytest.mark.asyncio
async def test_provider_secret_is_one_time_consumable():
    secret_ref = await task_manager.put_secret("super-secret-key", ttl_seconds=60)
    assert await task_manager.secret_exists(secret_ref) is True

    first = await task_manager.consume_secret(secret_ref)
    second = await task_manager.consume_secret(secret_ref)

    assert first == "super-secret-key"
    assert second is None
    assert await task_manager.secret_exists(secret_ref) is False


@pytest.mark.asyncio
async def test_run_spec_does_not_store_plain_provider_api_key(client, auth_headers):
    response = await client.post(
        "/api/v1/agents/run",
        json={
            "agent_type": "echo",
            "prompt": "hello",
            "model": "builtin",
            "provider_base_url": "https://example-proxy.test/v1",
            "provider_api_key": "plain-secret-should-not-persist",
            "provider_name": "openai-compatible",
        },
        headers=auth_headers,
    )
    assert response.status_code == 200
    task_id = response.json()["task_id"]
    run_spec = await task_manager.get_run_spec(task_id)
    assert run_spec is not None
    assert "provider_api_key" not in run_spec
    assert run_spec.get("provider_api_key_ref")
    assert run_spec.get("provider_base_url_redacted") == "https://example-proxy.test"
    assert run_spec.get("provider_name") == "openai-compatible"


@pytest.mark.asyncio
async def test_run_rejects_unknown_provider_name(client, auth_headers):
    response = await client.post(
        "/api/v1/agents/run",
        json={
            "agent_type": "echo",
            "prompt": "hello",
            "model": "builtin",
            "provider_name": "custom-provider",
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "PROVIDER_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_run_rejects_invalid_provider_name_format(client, auth_headers):
    response = await client.post(
        "/api/v1/agents/run",
        json={
            "agent_type": "echo",
            "prompt": "hello",
            "model": "builtin",
            "provider_name": "openai\nhack",
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "PROVIDER_NAME_INVALID"


@pytest.mark.asyncio
async def test_run_requires_provider_name_when_custom_key_supplied(client, auth_headers):
    response = await client.post(
        "/api/v1/agents/run",
        json={
            "agent_type": "echo",
            "prompt": "hello",
            "model": "builtin",
            "provider_api_key": "secret-without-name",
        },
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert response.json()["code"] == "PROVIDER_NAME_REQUIRED"


def test_provider_base_url_redaction():
    assert (
        redact_provider_base_url("https://my-gateway.example.com/v1/chat/completions?token=abc")
        == "https://my-gateway.example.com"
    )
    assert redact_provider_base_url("http://127.0.0.1:8080/proxy/path?x=1") == "http://127.0.0.1:8080"
