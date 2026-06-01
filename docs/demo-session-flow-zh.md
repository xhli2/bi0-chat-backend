# Demo Query 与全流程测试指南

本文档提供 **4 组 Demo Query**、**API 调用顺序**、**预期持久化结果** 与 **自动化报告脚本** 说明。与 [session-persistence-zh.md](./session-persistence-zh.md)、[user-query-flow-zh.md](./user-query-flow-zh.md) 互补。

**逐步产出说明（Query → 步骤 → 生成什么）** 见 **[demo-query-flow-trace-zh.md](./demo-query-flow-trace-zh.md)**。

---

## 0. 环境准备

```powershell
cd g:\backend_temp
docker compose up -d --build
$env:DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5432/backend_temp"
uv run alembic upgrade head
```

`.env` 建议（LLM 路径）：

```env
DEFAULT_LLM_MODEL=deepseek-chat
DEFAULT_PROVIDER_BASE_URL=https://api.deepseek.com/v1
DEFAULT_PROVIDER_API_KEY=sk-...
TENANT_TOOL_POLICIES_JSON={"public":["*"]}
E2E_TENANT_ID=public
```

启动 worker（若未用 compose）：

```powershell
uv run celery -A app.worker.celery_app.celery_app worker -Q high,default,low --loglevel=info
```

---

## 1. Demo Query 一览

所有 Query **共用同一 `session_id`**，用于验证跨轮 memory / entities / tool_calls。

| ID | 目的 | agent_type | 用户 prompt（示例） |
|----|------|------------|---------------------|
| **Q1** | KV 友好偏好 + 变异背景 | `research` | 见下方 Q1 全文 |
| **Q2** | NCBI 文献工具链 | `research` | PubMed BRCA1 检索 |
| **Q3** | SpliceAI submit（supervisor） | `supervisor` | submit + 说明 job_id |
| **Q4** | 续聊不重复 submit | `research` | 「结果出来了吗？」 |
| **Q5** | 基因注释 MyGene+Ensembl | `research` | BRCA1 gene annotation |
| **Q6** | Ensembl VEP 变异后果 | `research` | 9:g.22125504G>C |
| **Q7** | PDB + AlphaFold 结构 | `research` | BRCA1 结构 + P38398 |
| **Q8** | ClinVar 临床意义 | `research` | ClinVar pathogenic |
| **Q9** | 序列 GC 统计 script | `research` | seq_stats.py / FASTA |

### Q1 全文（推荐复制）

```
genome_build: GRCh38
report_language: 中文

请用 2-3 句话介绍剪接位点变异 NM_000518.5:c.694+1G>A 的一般分析思路。
```

### Q2 全文

```
Search PubMed for BRCA1 c.694+1G>A splicing. Reply in 2 sentences, 中文.
```

### Q3 全文

```
对变异 NM_000518.5:c.694+1G>A（GRCh38）执行 SpliceAI 评估。
先 submit job，再简要说明 job_id。
```

### Q4 全文

```
SpliceAI 结果出来了吗？若已有 job，请 get_result 并中文总结，不要重复 submit。
```

### Q5 全文（gene-annotation）

```
请用 gene annotation 流程查 BRCA1：MyGene 解析 symbol/Entrez，再用 Ensembl 确认染色体坐标和 biotype。中文 2 句话总结。
```

### Q6 全文（variant-consequence / Ensembl VEP）

```
用 Ensembl VEP 注释变异 9:g.22125504G>C，说明 most_severe_consequence 和影响基因。中文简要回答。
```

### Q7 全文（structure-lookup）

```
查 BRCA1 相关 PDB 实验结构（top 3）以及 UniProt P38398 的 AlphaFold 预测模型 ID。中文列表回答。
```

### Q8 全文（clinvar-lookup）

```
在 ClinVar 里搜 BRCA1 pathogenic 相关记录，用 db=clinvar，返回 top ID 并中文说明需进一步读 record。
```

### Q9 全文（sequence-utils）

```
请说明如何用 sequence-utils 的 seq_stats.py 统计 FASTA 的 GC 含量；若 workspace 已有 inputs/seq.fasta 则运行 bio_script_runner 并报告结果。
```

> Q9 依赖 `bio:script:run` 与 workspace 内 FASTA；自动化脚本会先跑 **Bio 工具直连 smoke**（含 seq_stats 本地验证），再跑 agent 轮次。

---

## 1.1 新增 Bio 工具权限

JWT / tenant 需包含（除原有 bio 权限外）：

- `bio:annotation:read` — MyGene、Ensembl、VEP  
- `bio:structure:read` — PDB、AlphaFold  

`.env` 示例：

```env
TENANT_DEFAULT_PERMISSIONS_JSON={"public":["session:read","bio:ncbi:read","bio:uniprot:read","bio:annotation:read","bio:structure:read","bio:spliceai:submit","bio:spliceai:read","bio:script:run","http:external","mcp:invoke"]}
TENANT_TOOL_POLICIES_JSON={"public":["*"]}
E2E_APPROVE_TOOLS=http_search_wrapper,mcp_proxy_call,bio_script_runner,bio_spliceai_submit,bio_spliceai_get_result,bio_mygene_query,bio_ensembl_gene_lookup,bio_ensembl_vep,bio_pdb_search,bio_alphafold_lookup,bio_ncbi_search,bio_uniprot_lookup
```

---

## 2. API 调用顺序（手动）

### Step 0 — 鉴权

```http
POST /api/v1/auth/register
{"email":"demo@example.com","password":"password123"}

POST /api/v1/auth/login
→ access_token
```

Header（后续请求）：

```
Authorization: Bearer <token>
X-Tenant-ID: public
X-Trace-ID: <uuid>
```

### Step 1 — 创建 Session

```http
POST /api/v1/sessions
{"title":"Demo Bio Session"}
→ session_id
```

### Step 2 — 每轮 Agent Run

```http
POST /api/v1/agents/run
{
  "agent_type": "research",
  "prompt": "<Q1 全文>",
  "model": "deepseek-chat",
  "session_id": "<session_id>",
  "context_policy": "balanced",
  "provider_api_key": "<可选，服务端 .env 已有则可省略>"
}
→ task_id, stream_url, status_url
```

轮询：

```http
GET /api/v1/tasks/{task_id}
```

若 `awaiting_approval: true`：

```http
POST /api/v1/collaboration/tasks/{task_id}/approve-tool
{"tool_name":"bio_spliceai_submit"}

POST /api/v1/agents/{task_id}/resume
{"approved_tool":"bio_spliceai_submit","provider_api_key":"..."}
```

### Step 3 — 每轮结束后查 Session API

| 顺序 | API | 验证什么 |
|------|-----|----------|
| 1 | `GET /sessions/{id}/messages` | user/assistant 是否追加 |
| 2 | `GET /sessions/{id}/runs` | task_id、usage、agent_type |
| 3 | `GET /sessions/{id}/timeline` | entities、tool_calls、artifacts |
| 4 | `GET /sessions/{id}/token-usage` | input/output tokens |
| 5 | `GET /sessions/{id}/diagnostics` | message/memory 计数 |

### Step 4 — 触发 KV / Summary 生成

KV **不会每轮自动生成**，需对话够长或手动：

```http
POST /api/v1/sessions/{id}/summarize
```

等待 Celery 完成后：

```http
GET /api/v1/sessions/{id}/memory
GET /api/v1/sessions/{id}/summary
```

### Step 5 — 用户级 Token 汇总

```http
GET /api/v1/sessions/token-usage
GET /api/v1/sessions
```

---

## 3. 各 Query 预期持久化（PostgreSQL）

### Q1 结束后

| 表 | 预期 |
|----|------|
| `chat_messages` | +2（user, assistant），metadata 含 agent_type/model |
| `session_runs` | +1 status=success，usage_json |
| `chat_memory_kv` | 可能仍空（未 refresh） |
| `session_entities` | 通常空（未调 bio 工具） |

SSE `Context prepared`：`kv_hit` 多为 **false**（正常）。

### Q2 结束后

| 表 | 预期 |
|----|------|
| `chat_tool_calls` | +N，`bio_ncbi_search` status=success |
| `session_entities` | 可能有 literature/gene（来自 evidence） |

### Q3 结束后

| 表 | 预期 |
|----|------|
| `chat_tool_calls` | `bio_spliceai_submit` success |
| `session_entities` | variant + job（is_active=true） |
| `spliceai_jobs` | status 经 worker → success |
| `session_artifacts` | kind=spliceai_result |
| `session_runs` | supervisor 时 plan_json 非空 |

### Q4 结束后

| 表 / Prompt | 预期 |
|-------------|------|
| `[ActiveAnalysis]` | 含 job_id、variant |
| `[RecentToolCalls]` | 含 submit 记录，避免重复 submit |
| `chat_tool_calls` | 可能 +`bio_spliceai_get_result` |
| assistant | 中文总结 SpliceAI 分数 |

### Summarize 后

| 表 | 预期 |
|----|------|
| `chat_memory_kv` | `genome_build: GRCh38`, `report_language: 中文` |
| `chat_summaries` | version+1 |
| 下一轮 run | `kv_hit: true`, `[SessionMemory]` 出现 |

---

## 4. 详细生成流程（单轮 Agent Run）

```mermaid
sequenceDiagram
    participant C as Client
    participant API as FastAPI
    participant PG as PostgreSQL
    participant R as Redis
    participant W as Celery Worker

    C->>API: POST /agents/run
    API->>PG: ensure_session, add user message
    API->>PG: create session_runs(running)
    API->>R: create task, save run_spec
    API->>W: enqueue run_agent_task

    W->>PG: ContextLoader
    Note over W,PG: messages, summary, memory_kv,<br/>entities, tool_calls
    W->>W: PromptBuilder → snapshot
    Note over W: kv_hit, summary_hit,<br/>[ActiveAnalysis], [SessionMemory]

    W->>R: emit status Context prepared
    W->>W: LLM / supervisor / tools
    W->>PG: chat_tool_calls, session_entities
    W->>PG: add assistant message + metadata
    W->>PG: complete session_runs

    alt should_refresh_summary
        W->>W: refresh_session_memory_task
        W->>PG: chat_summaries + chat_memory_kv
    end

    C->>API: GET /sessions/{id}/timeline
    API->>PG: aggregate
```

### Prompt 组装优先级（当前实现）

1. `instructions` = system + tenant + context_packs + skills  
2. `context_blocks`（按顺序）  
   - `[ActiveAnalysis]` ← session_entities  
   - `[RecentToolCalls]` ← chat_tool_calls  
   - `[SessionSummary]` ← chat_summaries  
   - `[SessionMemory]` ← chat_memory_kv（**kv_hit 来源**）  
   - `[RecentMessages]` ← chat_messages  
3. `[CurrentUserMessage]` = 本轮 user prompt  

---

## 5. 自动化：生成流程报告

一键跑 4 个 Demo Query + Session API 快照 + Markdown 报告：

```powershell
$env:DEMO_MODEL="deepseek-chat"   # 或 builtin 仅 smoke
$env:DEMO_POLL_TIMEOUT="360"
uv run python scripts/demo_session_flow.py
```

输出：

```
reports/demo-session-flow-report.md
```

报告包含：

- 预检 healthz/readyz  
- 每步 task_id、status、kv_hit、summary_hit  
- timeline/memory/runs 等 API JSON 快照  
- summarize 前后 memory 对比  
- 验收清单与 API 索引  

---

## 6. 与其它脚本的关系

| 脚本 | 用途 |
|------|------|
| `scripts/demo_session_flow.py` | **同 session 多轮** + Session API + 报告 |
| `scripts/e2e_poll_all.py` | 多 agent 场景 smoke（偏 agent 能力） |
| `uv run pytest tests/test_sessions_api.py` | Session API 单测 |

---

## 7. 常见问题

| 现象 | 原因 | 处理 |
|------|------|------|
| `kv_hit` 一直 false | KV 未 refresh 或已过期 | `POST /summarize` 或聊满 8 轮 |
| Q4 仍重复 submit | entities 未写入 | 查 Q3 是否 success、timeline.entities |
| awaiting_approval 卡住 | 未 approve/resume | collaboration approve + resume 带 provider_key |
| timeline 无 artifacts | 未跑 script/SpliceAI | 仅 Q3 成功后有 spliceai_result |

---

## 8. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-05-31 | 初版：4 组 Demo Query、API 顺序、自动化报告脚本 |
| 2026-05-31 | 扩展 Q5–Q9 Bio Skill + 权限与 E2E_APPROVE_TOOLS 说明 |
