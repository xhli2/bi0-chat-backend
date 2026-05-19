from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.approval_ticket import ApprovalTicket


class ApprovalFlowService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.settings = get_settings()

    async def create_ticket(
        self,
        task_id: str,
        tenant_id: str,
        tool_name: str,
        requested_by: int | None,
        session_id: str | None = None,
        trace_id: str | None = None,
        reason: str | None = None,
        reviewer_id: int | None = None,
        sla_seconds: int | None = None,
    ) -> ApprovalTicket:
        now = datetime.now(timezone.utc)
        effective_sla = sla_seconds if sla_seconds is not None else self.settings.approval_ticket_default_sla_seconds
        ticket = ApprovalTicket(
            task_id=task_id,
            session_id=session_id,
            tenant_id=tenant_id,
            tool_name=tool_name,
            status="pending",
            requested_by=requested_by,
            reviewer_id=reviewer_id,
            trace_id=trace_id,
            reason=reason,
            due_at=now + timedelta(seconds=max(60, effective_sla)),
            requested_at=now,
        )
        self.db.add(ticket)
        await self.db.commit()
        await self.db.refresh(ticket)
        return ticket

    async def get_ticket(self, ticket_id: str) -> ApprovalTicket | None:
        result = await self.db.execute(select(ApprovalTicket).where(ApprovalTicket.id == ticket_id))
        return result.scalars().first()

    async def list_task_tickets(self, task_id: str) -> list[ApprovalTicket]:
        stmt: Select[tuple[ApprovalTicket]] = (
            select(ApprovalTicket)
            .where(ApprovalTicket.task_id == task_id)
            .order_by(ApprovalTicket.created_at.desc())
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def mark_approved(self, ticket: ApprovalTicket, reviewer_id: int, decision_note: str | None = None) -> ApprovalTicket:
        ticket.status = "approved"
        ticket.reviewer_id = reviewer_id
        ticket.decision_note = decision_note
        ticket.decided_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(ticket)
        return ticket

    async def mark_rejected(self, ticket: ApprovalTicket, reviewer_id: int, decision_note: str | None = None) -> ApprovalTicket:
        ticket.status = "rejected"
        ticket.reviewer_id = reviewer_id
        ticket.decision_note = decision_note
        ticket.decided_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(ticket)
        return ticket

    async def scan_overdue(self) -> tuple[int, int]:
        now = datetime.now(timezone.utc)
        stmt: Select[tuple[ApprovalTicket]] = (
            select(ApprovalTicket)
            .where(
                ApprovalTicket.status == "pending",
                ApprovalTicket.due_at.is_not(None),
                ApprovalTicket.due_at < now,
            )
            .limit(self.settings.approval_ticket_scan_batch_size)
        )
        result = await self.db.execute(stmt)
        tickets = list(result.scalars().all())
        for ticket in tickets:
            ticket.status = "overdue"
        if tickets:
            await self.db.commit()
        return len(tickets), len(tickets)
