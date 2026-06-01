import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from uuid import uuid4

from redis.asyncio import Redis

from app.core.config import get_settings
from app.schemas.realtime import AgentEvent, TaskState


class TaskManager:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._use_memory_store = self._settings.environment == "test"
        if self._use_memory_store:
            self._memory_states: dict[str, TaskState] = {}
            self._memory_events: dict[str, list[AgentEvent]] = {}
            self._memory_dead_letter: list[dict] = []
            self._memory_checkpoints: dict[str, list[dict]] = {}
            self._memory_approved_tools: dict[str, set[str]] = {}
            self._memory_run_specs: dict[str, dict] = {}
            self._memory_seq = 0
            self._memory_stream_conditions: dict[str, asyncio.Condition] = {}
            self._memory_secrets: dict[str, dict[str, str]] = {}
        else:
            self._redis = Redis.from_url(self._settings.redis_url, decode_responses=True)

    def _state_key(self, task_id: str) -> str:
        return f"task:{task_id}:state"

    def _events_key(self, task_id: str) -> str:
        return f"task:{task_id}:events"

    def _dead_letter_key(self) -> str:
        return "tasks:dead_letter"

    def _secret_key(self, secret_ref: str) -> str:
        return f"secret:{secret_ref}"

    async def create_task(
        self,
        priority: str = "default",
        model: str | None = None,
        tenant_id: str = "public",
        trace_id: str | None = None,
        session_id: str | None = None,
        user_id: int | None = None,
        context_policy: str = "balanced",
        owner_id: int | None = None,
        reviewer_id: int | None = None,
        current_operator_id: int | None = None,
        handoff_reason: str | None = None,
        sla_seconds: int | None = None,
    ) -> str:
        task_id = str(uuid4())
        if self._use_memory_store:
            self._memory_states[task_id] = TaskState(
                task_id=task_id,
                status="queued",
                priority=priority,  # type: ignore[arg-type]
                model=model,
                session_id=session_id,
                user_id=user_id,
                context_policy=context_policy,
                tenant_id=tenant_id,
                trace_id=trace_id,
                retry_count=0,
                poison=False,
                failure_reason=None,
                interrupted=False,
                latest_event_id=None,
                owner_id=owner_id,
                reviewer_id=reviewer_id,
                current_operator_id=current_operator_id or owner_id,
                handoff_reason=handoff_reason,
                sla_seconds=sla_seconds,
                awaiting_approval=False,
                workflow_step=0,
            )
            return task_id

        await self._redis.hset(
            self._state_key(task_id),
            mapping={
                "task_id": task_id,
                "status": "queued",
                "priority": priority,
                "model": model or "",
                "session_id": session_id or "",
                "user_id": "" if user_id is None else str(user_id),
                "context_policy": context_policy,
                "tenant_id": tenant_id,
                "trace_id": trace_id or "",
                "retry_count": "0",
                "poison": "0",
                "failure_reason": "",
                "interrupted": "0",
                "latest_event_id": "",
                "owner_id": "" if owner_id is None else str(owner_id),
                "reviewer_id": "" if reviewer_id is None else str(reviewer_id),
                "current_operator_id": (
                    "" if (current_operator_id is None and owner_id is None) else str(current_operator_id or owner_id)
                ),
                "handoff_reason": handoff_reason or "",
                "sla_seconds": "" if sla_seconds is None else str(sla_seconds),
                "awaiting_approval": "0",
                "workflow_step": "0",
            },
        )
        return task_id

    async def get_state(self, task_id: str) -> TaskState | None:
        if self._use_memory_store:
            return self._memory_states.get(task_id)

        raw = await self._redis.hgetall(self._state_key(task_id))
        if not raw:
            return None
        return TaskState(
            task_id=raw["task_id"],
            status=raw["status"],
            priority=raw.get("priority", "default"),
            model=raw.get("model") or None,
            session_id=raw.get("session_id") or None,
            user_id=int(raw["user_id"]) if raw.get("user_id") not in (None, "") else None,
            context_policy=raw.get("context_policy", "balanced"),
            tenant_id=raw.get("tenant_id", "public"),
            trace_id=raw.get("trace_id") or None,
            retry_count=int(raw.get("retry_count", "0")),
            poison=raw.get("poison", "0") == "1",
            failure_reason=raw.get("failure_reason") or None,
            interrupted=raw.get("interrupted", "0") == "1",
            latest_event_id=raw.get("latest_event_id") or None,
            owner_id=int(raw["owner_id"]) if raw.get("owner_id") not in (None, "") else None,
            reviewer_id=int(raw["reviewer_id"]) if raw.get("reviewer_id") not in (None, "") else None,
            current_operator_id=(
                int(raw["current_operator_id"]) if raw.get("current_operator_id") not in (None, "") else None
            ),
            handoff_reason=raw.get("handoff_reason") or None,
            sla_seconds=int(raw["sla_seconds"]) if raw.get("sla_seconds") not in (None, "") else None,
            awaiting_approval=raw.get("awaiting_approval", "0") == "1",
            workflow_step=int(raw.get("workflow_step", "0")),
        )

    async def emit(self, event: AgentEvent) -> str:
        if self._use_memory_store:
            state = self._memory_states.get(event.task_id)
            if state is None:
                return ""

            self._memory_seq += 1
            stream_id = f"{int(datetime.now(timezone.utc).timestamp() * 1000)}-{self._memory_seq}"
            event.id = stream_id
            self._memory_events.setdefault(event.task_id, []).append(event)

            state.latest_event_id = stream_id
            if event.type == "status":
                status = event.payload.get("status")
                if status in {"queued", "running", "success", "failed", "cancelled"}:
                    state.status = status
                if status in {"success", "failed", "cancelled"}:
                    state.awaiting_approval = False
                elif "awaiting_approval" in event.payload:
                    state.awaiting_approval = bool(event.payload.get("awaiting_approval"))
                state.workflow_step = int(event.payload.get("workflow_step", state.workflow_step))
            if event.type == "approval_required":
                state.awaiting_approval = True
            if event.type == "handoff_end":
                state.awaiting_approval = False

            condition = self._memory_stream_conditions.setdefault(event.task_id, asyncio.Condition())
            async with condition:
                condition.notify_all()
            return stream_id

        state_key = self._state_key(event.task_id)
        if not await self._redis.exists(state_key):
            return ""

        stream_id = await self._redis.xadd(
            self._events_key(event.task_id),
            fields={
                "type": event.type,
                "task_id": event.task_id,
                "created_at": event.created_at.isoformat(),
                "payload": json.dumps(event.payload, ensure_ascii=False),
            },
            maxlen=self._settings.sse_backlog_size,
            approximate=True,
        )
        event.id = stream_id
        await self._redis.hset(state_key, mapping={"latest_event_id": stream_id})
        state_patch: dict[str, str] = {}
        if event.type == "status":
            status = event.payload.get("status")
            if status in {"queued", "running", "success", "failed", "cancelled"}:
                state_patch["status"] = status
            if status in {"success", "failed", "cancelled"}:
                state_patch["awaiting_approval"] = "0"
            elif "awaiting_approval" in event.payload:
                state_patch["awaiting_approval"] = "1" if event.payload.get("awaiting_approval") else "0"
            if "workflow_step" in event.payload:
                state_patch["workflow_step"] = str(event.payload.get("workflow_step", 0))
        if event.type == "approval_required":
            state_patch["awaiting_approval"] = "1"
        if event.type == "handoff_end":
            state_patch["awaiting_approval"] = "0"
        if state_patch:
            await self._redis.hset(state_key, mapping=state_patch)
        return stream_id

    async def update_collaboration(
        self,
        task_id: str,
        reviewer_id: int | None = None,
        current_operator_id: int | None = None,
        handoff_reason: str | None = None,
        sla_seconds: int | None = None,
    ) -> TaskState | None:
        state = await self.get_state(task_id)
        if state is None:
            return None

        if self._use_memory_store:
            if reviewer_id is not None:
                state.reviewer_id = reviewer_id
            if current_operator_id is not None:
                state.current_operator_id = current_operator_id
            if handoff_reason is not None:
                state.handoff_reason = handoff_reason
            if sla_seconds is not None:
                state.sla_seconds = sla_seconds
            return state

        mapping: dict[str, str] = {}
        if reviewer_id is not None:
            mapping["reviewer_id"] = str(reviewer_id)
        if current_operator_id is not None:
            mapping["current_operator_id"] = str(current_operator_id)
        if handoff_reason is not None:
            mapping["handoff_reason"] = handoff_reason
        if sla_seconds is not None:
            mapping["sla_seconds"] = str(sla_seconds)
        if mapping:
            await self._redis.hset(self._state_key(task_id), mapping=mapping)
        return await self.get_state(task_id)

    async def save_checkpoint(self, task_id: str, checkpoint: dict) -> None:
        if self._use_memory_store:
            if task_id not in self._memory_states:
                return
            self._memory_checkpoints.setdefault(task_id, []).append(checkpoint)
            return
        await self._redis.rpush(f"task:{task_id}:checkpoints", json.dumps(checkpoint, ensure_ascii=False))

    async def list_checkpoints(self, task_id: str, limit: int = 20) -> list[dict]:
        if self._use_memory_store:
            return self._memory_checkpoints.get(task_id, [])[-limit:]
        rows = await self._redis.lrange(f"task:{task_id}:checkpoints", -limit, -1)
        parsed: list[dict] = []
        for row in rows:
            try:
                parsed.append(json.loads(row))
            except json.JSONDecodeError:
                continue
        return parsed

    async def approve_tool(self, task_id: str, tool_name: str) -> None:
        if self._use_memory_store:
            self._memory_approved_tools.setdefault(task_id, set()).add(tool_name)
            return
        await self._redis.sadd(f"task:{task_id}:approved_tools", tool_name)

    async def approved_tools(self, task_id: str) -> set[str]:
        if self._use_memory_store:
            return set(self._memory_approved_tools.get(task_id, set()))
        values = await self._redis.smembers(f"task:{task_id}:approved_tools")
        return {item for item in values}

    async def save_run_spec(self, task_id: str, spec: dict) -> None:
        if self._use_memory_store:
            self._memory_run_specs[task_id] = spec
            return
        await self._redis.set(f"task:{task_id}:run_spec", json.dumps(spec, ensure_ascii=False))

    async def patch_run_spec(self, task_id: str, patch: dict) -> dict | None:
        current = await self.get_run_spec(task_id)
        if current is None:
            return None
        merged = {**current, **patch}
        await self.save_run_spec(task_id, merged)
        return merged

    async def get_run_spec(self, task_id: str) -> dict | None:
        if self._use_memory_store:
            return self._memory_run_specs.get(task_id)
        raw = await self._redis.get(f"task:{task_id}:run_spec")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def put_secret(self, value: str, ttl_seconds: int) -> str:
        secret_ref = str(uuid4())
        ttl = max(1, ttl_seconds)
        if self._use_memory_store:
            expires_at = datetime.now(timezone.utc).timestamp() + ttl
            self._memory_secrets[secret_ref] = {"value": value, "expires_at": str(expires_at)}
            return secret_ref
        await self._redis.set(self._secret_key(secret_ref), value, ex=ttl)
        return secret_ref

    async def consume_secret(self, secret_ref: str) -> str | None:
        if self._use_memory_store:
            entry = self._memory_secrets.get(secret_ref)
            if not entry:
                return None
            expires_at = float(entry.get("expires_at", "0"))
            if datetime.now(timezone.utc).timestamp() > expires_at:
                self._memory_secrets.pop(secret_ref, None)
                return None
            self._memory_secrets.pop(secret_ref, None)
            return entry.get("value")
        key = self._secret_key(secret_ref)
        value = await self._redis.get(key)
        if value is None:
            return None
        await self._redis.delete(key)
        return value

    async def secret_exists(self, secret_ref: str) -> bool:
        if self._use_memory_store:
            entry = self._memory_secrets.get(secret_ref)
            if not entry:
                return False
            expires_at = float(entry.get("expires_at", "0"))
            if datetime.now(timezone.utc).timestamp() > expires_at:
                self._memory_secrets.pop(secret_ref, None)
                return False
            return True
        return bool(await self._redis.exists(self._secret_key(secret_ref)))

    async def interrupt(self, task_id: str) -> bool:
        if self._use_memory_store:
            state = self._memory_states.get(task_id)
            if state is None:
                return False
            state.interrupted = True
            return True

        state_key = self._state_key(task_id)
        if not await self._redis.exists(state_key):
            return False
        await self._redis.hset(state_key, mapping={"interrupted": "1"})
        return True

    async def cancelled(self, task_id: str) -> bool:
        if self._use_memory_store:
            state = self._memory_states.get(task_id)
            return bool(state and state.interrupted)
        return (await self._redis.hget(self._state_key(task_id), "interrupted")) == "1"

    async def resume(self, task_id: str) -> bool:
        if self._use_memory_store:
            state = self._memory_states.get(task_id)
            if state is None:
                return False
            state.interrupted = False
            state.awaiting_approval = False
            state.status = "running"
            return True
        state_key = self._state_key(task_id)
        if not await self._redis.exists(state_key):
            return False
        await self._redis.hset(state_key, mapping={"interrupted": "0", "awaiting_approval": "0", "status": "running"})
        return True

    async def increment_retry(self, task_id: str, reason: str) -> int:
        if self._use_memory_store:
            state = self._memory_states.get(task_id)
            if state is None:
                return 0
            state.retry_count += 1
            state.failure_reason = reason
            return state.retry_count

        state_key = self._state_key(task_id)
        retry_count = await self._redis.hincrby(state_key, "retry_count", 1)
        await self._redis.hset(state_key, mapping={"failure_reason": reason})
        return int(retry_count)

    async def mark_dead_letter(self, task_id: str, reason: str) -> None:
        if self._use_memory_store:
            state = await self.get_state(task_id)
            if state is None:
                return
            state.status = "failed"
            state.poison = True
            state.failure_reason = reason
            self._memory_dead_letter.insert(
                0,
                {
                    "task_id": task_id,
                    "trace_id": state.trace_id,
                    "tenant_id": state.tenant_id,
                    "reason": reason,
                    "retry_count": state.retry_count,
                    "time": datetime.now(timezone.utc).isoformat(),
                },
            )
            return

        state_key = self._state_key(task_id)
        state = await self.get_state(task_id)
        if state is None:
            return

        await self._redis.hset(
            state_key,
            mapping={
                "status": "failed",
                "poison": "1",
                "failure_reason": reason,
            },
        )
        await self._redis.lpush(
            self._dead_letter_key(),
            json.dumps(
                {
                    "task_id": task_id,
                    "trace_id": state.trace_id,
                    "tenant_id": state.tenant_id,
                    "reason": reason,
                    "retry_count": state.retry_count,
                    "time": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
            ),
        )

    async def queue_depths(self) -> dict[str, int]:
        if self._use_memory_store:
            depths = {"high": 0, "default": 0, "low": 0}
            for state in self._memory_states.values():
                if state.status in {"queued", "running"} and state.priority in depths:
                    depths[state.priority] += 1
            depths["dead_letter"] = len(self._memory_dead_letter)
            return depths

        priorities = ["high", "default", "low"]
        values = await self._redis.mget([f"{queue}" for queue in priorities])
        depths: dict[str, int] = {}
        for queue, raw in zip(priorities, values, strict=False):
            if raw is None:
                depths[queue] = await self._redis.llen(queue)
            else:
                depths[queue] = int(raw) if str(raw).isdigit() else await self._redis.llen(queue)
        depths["dead_letter"] = await self._redis.llen(self._dead_letter_key())
        return depths

    async def recent_events(self, task_id: str, limit: int = 30) -> list[AgentEvent]:
        if self._use_memory_store:
            return self._memory_events.get(task_id, [])[-limit:]

        rows = await self._redis.xrevrange(self._events_key(task_id), max="+", min="-", count=limit)
        events = [self._deserialize_event(stream_id, payload) for stream_id, payload in rows]
        events.reverse()
        return events

    async def dead_letter_items(self, limit: int = 50) -> list[dict]:
        if self._use_memory_store:
            return self._memory_dead_letter[:limit]

        rows = await self._redis.lrange(self._dead_letter_key(), 0, max(limit - 1, 0))
        result: list[dict] = []
        for row in rows:
            try:
                parsed = json.loads(row)
            except json.JSONDecodeError:
                parsed = {"raw": row}
            result.append(parsed)
        return result

    async def stream_events(self, task_id: str, last_event_id: str | None) -> AsyncGenerator[str, None]:
        if self._use_memory_store:
            state = self._memory_states.get(task_id)
            if state is None:
                return

            events = self._memory_events.get(task_id, [])
            next_idx = 0
            if last_event_id:
                for i, event in enumerate(events):
                    if event.id == last_event_id:
                        next_idx = i + 1
                        break

            heartbeat_seconds = self._settings.sse_heartbeat_seconds
            while True:
                events = self._memory_events.get(task_id, [])
                while next_idx < len(events):
                    event = events[next_idx]
                    next_idx += 1
                    yield self._encode_sse(event)
                    if event.type == "status" and event.payload.get("status") in {"success", "failed", "cancelled"}:
                        return

                current_state = self._memory_states.get(task_id)
                if current_state is None:
                    return
                if current_state.status in {"success", "failed", "cancelled"}:
                    return

                condition = self._memory_stream_conditions.setdefault(task_id, asyncio.Condition())
                try:
                    async with condition:
                        await asyncio.wait_for(condition.wait(), timeout=heartbeat_seconds)
                except TimeoutError:
                    yield self._encode_comment("heartbeat")
            return

        state = await self.get_state(task_id)
        if state is None:
            return

        events_key = self._events_key(task_id)
        replay_min = self._next_stream_id(last_event_id) if last_event_id else "-"
        replayed = await self._redis.xrange(events_key, min=replay_min, max="+")
        latest_seen = last_event_id or "0-0"
        for stream_id, data in replayed:
            latest_seen = stream_id
            event = self._deserialize_event(stream_id, data)
            yield self._encode_sse(event)

        heartbeat_seconds = self._settings.sse_heartbeat_seconds
        while True:
            data = await self._redis.xread({events_key: latest_seen}, block=heartbeat_seconds * 1000, count=50)
            if not data:
                yield self._encode_comment("heartbeat")
                continue

            _, rows = data[0]
            for stream_id, payload in rows:
                latest_seen = stream_id
                event = self._deserialize_event(stream_id, payload)
                yield self._encode_sse(event)
                if event.type == "status" and event.payload.get("status") in {"success", "failed", "cancelled"}:
                    return

    def _deserialize_event(self, stream_id: str, data: dict[str, str]) -> AgentEvent:
        created_at_raw = data.get("created_at")
        created_at = datetime.fromisoformat(created_at_raw) if created_at_raw else datetime.now(timezone.utc)
        payload = json.loads(data.get("payload", "{}"))
        return AgentEvent(id=stream_id, type=data["type"], task_id=data["task_id"], created_at=created_at, payload=payload)

    def _next_stream_id(self, stream_id: str | None) -> str:
        if not stream_id:
            return "-"
        try:
            ms, seq = stream_id.split("-")
            return f"{ms}-{int(seq) + 1}"
        except Exception:
            return stream_id

    def _encode_sse(self, event: AgentEvent) -> str:
        payload = json.dumps(event.model_dump(mode="json"), ensure_ascii=False)
        return f"id: {event.id}\nevent: {event.type}\ndata: {payload}\n\n"

    def _encode_comment(self, message: str) -> str:
        now = datetime.now(timezone.utc).isoformat()
        return f": {message} {now}\n\n"


task_manager = TaskManager()
