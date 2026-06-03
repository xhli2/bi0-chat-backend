import json
from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置：从环境变量 / .env 加载，字段名即 env 名（大写）。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- 应用与日志 ---
    app_name: str = "backend-temp"
    environment: Literal["dev", "test", "prod"] = "dev"  # test 会跳过部分外部依赖检查
    api_v1_prefix: str = "/api/v1"
    log_level: str = "INFO"  # DEBUG / INFO / WARNING / ERROR
    log_file: str = Field(
        default="",
        description="可选日志文件路径；空则仅控制台。例：logs/app.log",
    )

    # --- 数据库 ---
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/backend_temp",
        description="SQLAlchemy 异步连接串（asyncpg）。",
    )

    # --- JWT 与管理员 ---
    jwt_secret_key: str = "dev-insecure-secret-change-me"  # 非 test 环境禁止使用默认值
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 30
    jwt_refresh_token_expire_minutes: int = 60 * 24 * 7
    admin_user_ids: str = "1"  # 逗号分隔用户 ID，可访问 /admin 接口

    # --- Redis、SSE 与 Celery ---
    sse_heartbeat_seconds: int = 15  # SSE 心跳间隔（秒）
    sse_backlog_size: int = 200  # 断线重连时可回溯的事件条数上限
    redis_url: str = "redis://localhost:6379/0"  # 任务状态、事件流、Celery 默认 broker
    celery_broker_url: str | None = None  # 为空则复用 redis_url
    celery_result_backend: str | None = None  # 为空则复用 redis_url
    celery_task_max_retries: int = 3  # Agent 任务失败后 Celery 最大重试次数
    celery_retry_backoff_seconds: int = 5  # 重试指数退避基数（秒）
    celery_retry_backoff_max_seconds: int = 60  # 单次重试等待上限（秒）

    # --- Supervisor 工作流（多步编排）---
    workflow_step_timeout_seconds_default: int = 45
    workflow_step_timeout_seconds_max: int = 300
    workflow_step_max_retries: int = 2
    workflow_step_retry_backoff_seconds: int = 2
    workflow_max_replan_attempts: int = 1  # 某步失败后重新规划剩余步骤的次数上限

    # --- 模型路由与租户策略 ---
    model_allowlist: str = "builtin,gpt-4.1-mini,gpt-4.1"  # 全局可用模型，逗号分隔
    tenant_model_policies_json: str = "{}"  # {"tenant_id": ["model-a", ...]}
    model_router_auto_alias: str = "auto"  # 前端传 auto 时由路由选择具体模型
    model_router_simple_model: str = "gpt-4.1-mini"
    model_router_complex_model: str = "gpt-4.1"
    default_llm_model: str = ""  # 未指定时的默认模型（空则用路由结果）
    default_provider_base_url: str = ""  # OpenAI 兼容 API 基址，如 DeepSeek
    default_provider_api_key: str = ""
    default_provider_name: str = ""  # 供应商标识，用于策略校验
    model_router_complexity_threshold: int = 6  # 复杂度评分 >= 阈值时倾向 complex 模型
    model_token_limits_json: str = '{"builtin":8000,"gpt-4.1-mini":128000,"gpt-4.1":128000}'
    model_fallback_chains_json: str = '{"gpt-4.1-mini":["gpt-4.1"],"gpt-4.1":["gpt-4.1-mini"]}'
    provider_secret_ttl_seconds: int = 600  # 请求中传入的 API Key 在 Redis 中的暂存 TTL
    provider_secret_fallback_enabled: bool = False
    provider_secret_fallback_fernet_key: str = ""  # 启用 fallback 加密存储时的 Fernet 密钥
    provider_allowlist: str = "builtin,openai,azure-openai,anthropic,deepseek,openai-compatible"
    tenant_provider_policies_json: str = "{}"  # {"tenant_id": ["openai", ...]}

    # --- 工具（ToolRegistry）---
    tool_call_timeout_seconds_default: int = 15
    tool_call_timeout_seconds_max: int = 60
    tool_call_retry_attempts: int = 2
    tool_call_retry_backoff_seconds: float = 0.5
    tool_approval_required_tools: str = "http_search_wrapper,mcp_proxy_call,bio_script_runner"
    tool_guardrail_blocked_patterns: str = "sk-,api_key,authorization: bearer"  # 工具入参命中则拒绝
    mcp_proxy_base_url: str = "http://localhost:8081"
    mcp_proxy_timeout_seconds: int = 15
    mcp_proxy_default_server: str = "default"
    mcp_allowed_servers: str = ""  # 空表示不额外限制；非空为逗号分隔白名单
    http_tool_allowed_hosts: str = ""  # 空表示仅受 denied 限制
    http_tool_denied_hosts: str = "localhost,127.0.0.1,0.0.0.0,169.254.169.254"
    http_tool_max_response_bytes: int = 120000
    tenant_tool_policies_json: str = "{}"  # {"tenant_id": ["tool_a", "*"]}
    agent_tool_bindings_json: str = "{}"  # {"agent_type": {"skills": [], "tools": []}}

    # --- 会话上下文与记忆 ---
    context_budget_tokens: int = 4000  # 单次组装的 token 预算上限
    context_ratio_system_tenant: float = 0.10
    context_ratio_memory_kv: float = 0.15
    context_ratio_summary: float = 0.25
    context_ratio_recent_messages: float = 0.50
    context_recent_message_limit: int = 24
    summary_trigger_turns: int = 8
    summary_trigger_token_threshold: int = 2000
    max_messages_per_session: int = 2000
    max_summary_versions: int = 5
    max_kv_per_session: int = 200
    kv_ttl_hours: int = 72  # 会话 KV 记忆过期时间

    # --- 人工审批 ---
    approval_ticket_default_sla_seconds: int = 900
    approval_ticket_scan_batch_size: int = 200
    approval_ticket_scan_interval_seconds: int = 60  # Celery beat 扫描逾期审批的间隔

    # --- CORS 与访问令牌默认权限 ---
    cors_allowed_origins: str = ""  # 逗号分隔；空则不加 CORS 中间件
    access_token_default_tenant: str = "public"
    access_token_default_permissions: str = "session:read"
    access_token_default_scopes: str = "agent:run"
    tenant_default_permissions_json: str = "{}"  # {"tenant_id": ["perm:a", ...]}

    # --- Context Pack 与 LLM Planner ---
    context_pack_max_selected: int = 2
    context_pack_max_chars: int = 2400
    planner_llm_complexity_threshold: int = 6  # 高于阈值时用 LLM 生成结构化计划
    planner_llm_model: str = ""  # 空则回退 model_router_simple_model

    # --- SpliceAI 外部服务 ---
    spliceai_service_url: str = ""  # 空则在非 test 环境走模拟；本地可填真实服务地址
    spliceai_service_timeout_seconds: int = 60

    # --- Skill 脚本沙箱 ---
    script_workspace_root: str = "workspaces"
    script_runner_timeout_seconds: int = 120
    script_runner_max_output_bytes: int = 50000
    script_runner_mode: str = "local"  # local | docker
    script_runner_docker_image: str = "python:3.12-slim"
    script_runner_docker_memory: str = "512m"
    script_runner_docker_network: str = "none"  # 隔离网络，降低脚本外连风险
    skills_root: str = "skills"
    skill_materialize_on_session_start: bool = True  # 会话开始时把 skill 文件落到 workspace
    skill_script_allowlist_enabled: bool = True
    workspace_cleanup_on_session_stop: bool = False

    # --- 派生属性：解析逗号/JSON 配置，供业务代码直接使用 ---

    @property
    def effective_celery_broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def effective_celery_result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def parsed_model_allowlist(self) -> set[str]:
        return {item.strip() for item in self.model_allowlist.split(",") if item.strip()}

    @property
    def parsed_provider_allowlist(self) -> set[str]:
        return {item.strip().lower() for item in self.provider_allowlist.split(",") if item.strip()}

    @property
    def parsed_tenant_provider_policies(self) -> dict[str, set[str]]:
        try:
            raw = json.loads(self.tenant_provider_policies_json)
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}

        normalized: dict[str, set[str]] = {}
        for tenant_id, providers in raw.items():
            if not isinstance(tenant_id, str) or not isinstance(providers, list):
                continue
            values = {provider.strip().lower() for provider in providers if isinstance(provider, str) and provider.strip()}
            if values:
                normalized[tenant_id] = values
        return normalized

    @property
    def parsed_tenant_model_policies(self) -> dict[str, list[str]]:
        try:
            raw = json.loads(self.tenant_model_policies_json)
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}

        normalized: dict[str, list[str]] = {}
        for tenant_id, models in raw.items():
            if isinstance(tenant_id, str) and isinstance(models, list):
                normalized[tenant_id] = [model for model in models if isinstance(model, str)]
        return normalized

    @property
    def parsed_model_token_limits(self) -> dict[str, int]:
        try:
            raw = json.loads(self.model_token_limits_json)
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}
        limits: dict[str, int] = {}
        for model, limit in raw.items():
            if isinstance(model, str) and isinstance(limit, int) and limit > 0:
                limits[model] = limit
        return limits

    @property
    def parsed_model_fallback_chains(self) -> dict[str, list[str]]:
        try:
            raw = json.loads(self.model_fallback_chains_json)
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}
        chains: dict[str, list[str]] = {}
        for model, candidates in raw.items():
            if not isinstance(model, str) or not isinstance(candidates, list):
                continue
            chains[model] = [name for name in candidates if isinstance(name, str) and name.strip()]
        return chains

    @property
    def parsed_tenant_tool_policies(self) -> dict[str, list[str]]:
        try:
            raw = json.loads(self.tenant_tool_policies_json)
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, list[str]] = {}
        for tenant_id, tools in raw.items():
            if isinstance(tenant_id, str) and isinstance(tools, list):
                normalized[tenant_id] = [tool for tool in tools if isinstance(tool, str)]
        return normalized

    @property
    def parsed_agent_tool_bindings(self) -> dict[str, dict[str, list[str]]]:
        try:
            raw = json.loads(self.agent_tool_bindings_json)
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}

        normalized: dict[str, dict[str, list[str]]] = {}
        for agent_type, item in raw.items():
            if not isinstance(agent_type, str) or not isinstance(item, dict):
                continue
            skills = item.get("skills", [])
            tools = item.get("tools", [])
            if not isinstance(skills, list) or not isinstance(tools, list):
                continue
            normalized[agent_type] = {
                "skills": [skill for skill in skills if isinstance(skill, str)],
                "tools": [tool for tool in tools if isinstance(tool, str)],
            }
        return normalized

    @property
    def parsed_cors_allowed_origins(self) -> list[str]:
        if not self.cors_allowed_origins.strip():
            return []
        return [item.strip() for item in self.cors_allowed_origins.split(",") if item.strip()]

    @property
    def parsed_admin_user_ids(self) -> set[int]:
        values: set[int] = set()
        for raw in self.admin_user_ids.split(","):
            item = raw.strip()
            if not item:
                continue
            if item.isdigit():
                values.add(int(item))
        return values

    @property
    def parsed_tool_approval_required_tools(self) -> set[str]:
        return {item.strip() for item in self.tool_approval_required_tools.split(",") if item.strip()}

    @property
    def parsed_tool_guardrail_blocked_patterns(self) -> tuple[str, ...]:
        return tuple(item.strip().lower() for item in self.tool_guardrail_blocked_patterns.split(",") if item.strip())

    @property
    def parsed_http_tool_allowed_hosts(self) -> set[str]:
        return {item.strip().lower() for item in self.http_tool_allowed_hosts.split(",") if item.strip()}

    @property
    def parsed_http_tool_denied_hosts(self) -> set[str]:
        return {item.strip().lower() for item in self.http_tool_denied_hosts.split(",") if item.strip()}

    @property
    def parsed_mcp_allowed_servers(self) -> set[str]:
        return {item.strip() for item in self.mcp_allowed_servers.split(",") if item.strip()}

    @property
    def parsed_access_token_default_permissions(self) -> set[str]:
        return {item.strip() for item in self.access_token_default_permissions.split(",") if item.strip()}

    @property
    def parsed_access_token_default_scopes(self) -> set[str]:
        return {item.strip() for item in self.access_token_default_scopes.split(",") if item.strip()}

    @property
    def parsed_tenant_default_permissions(self) -> dict[str, set[str]]:
        try:
            raw = json.loads(self.tenant_default_permissions_json)
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}
        result: dict[str, set[str]] = {}
        for tenant_id, permissions in raw.items():
            if not isinstance(tenant_id, str) or not isinstance(permissions, list):
                continue
            normalized = {perm for perm in permissions if isinstance(perm, str) and perm.strip()}
            if normalized:
                result[tenant_id] = normalized
        return result


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """进程内单例配置；测试如需换 env 请先 get_settings.cache_clear()。"""
    return Settings()
