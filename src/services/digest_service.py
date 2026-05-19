from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db.models import (
    Client,
    ClientStage,
    Conversation,
    MessageDirection,
    Reminder,
    ReminderStatus,
    User,
)
from src.db.models import Message as MessageModel
from src.services.reminders import reminder_text, short_id

_COLD_AFTER = timedelta(days=7)
_HOT_WINDOW = timedelta(hours=24)
_COLD_STAGES = {ClientStage.QUALIFIED, ClientStage.NEGOTIATION}


@dataclass
class HotSignal:
    client: Client
    message: MessageModel
    intent: str
    suggested_action: str


@dataclass
class Digest:
    manager: User
    needs_reply: list[Client] = field(default_factory=list)
    cold: list[Client] = field(default_factory=list)
    hot: list[HotSignal] = field(default_factory=list)
    today_reminders: list[tuple[Reminder, Client]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.needs_reply or self.cold or self.hot or self.today_reminders)


def _day_bounds_utc(now: datetime) -> tuple[datetime, datetime]:
    tz = ZoneInfo(settings.tz)
    local = now.astimezone(tz)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def _fmt_dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone(ZoneInfo(settings.tz)).strftime("%Y-%m-%d %H:%M")


async def _needs_reply(session: AsyncSession, manager: User) -> list[Client]:
    stmt = (
        select(Client)
        .where(
            Client.org_id == manager.org_id,
            Client.owner_user_id == manager.id,
            Client.last_inbound_at.is_not(None),
            or_(
                Client.last_outbound_at.is_(None),
                Client.last_inbound_at > Client.last_outbound_at,
            ),
        )
        .order_by(Client.last_inbound_at.desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _cold_clients(session: AsyncSession, manager: User, *, now: datetime) -> list[Client]:
    cutoff = now - _COLD_AFTER
    stmt = (
        select(Client)
        .where(
            Client.org_id == manager.org_id,
            Client.owner_user_id == manager.id,
            Client.stage.in_(_COLD_STAGES),
            Client.last_touch_at.is_not(None),
            Client.last_touch_at < cutoff,
        )
        .order_by(Client.last_touch_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _hot_signals(session: AsyncSession, manager: User, *, now: datetime) -> list[HotSignal]:
    since = now - _HOT_WINDOW
    stmt = (
        select(MessageModel, Client)
        .join(Conversation, Conversation.id == MessageModel.conversation_id)
        .join(Client, Client.id == Conversation.client_id)
        .where(
            Client.org_id == manager.org_id,
            Client.owner_user_id == manager.id,
            MessageModel.direction == MessageDirection.IN,
            MessageModel.sent_at >= since,
            MessageModel.analysis["urgency"].astext == "high",
        )
        .order_by(MessageModel.sent_at.desc())
    )
    rows = (await session.execute(stmt)).all()
    out: list[HotSignal] = []
    seen_clients: set = set()
    for msg, client in rows:
        if client.id in seen_clients:
            continue
        # Skip if manager already responded after the high-urgency message
        if (
            client.last_outbound_at is not None
            and msg.sent_at is not None
            and client.last_outbound_at >= msg.sent_at
        ):
            continue
        seen_clients.add(client.id)
        analysis = msg.analysis or {}
        out.append(
            HotSignal(
                client=client,
                message=msg,
                intent=str(analysis.get("intent") or "—"),
                suggested_action=str(analysis.get("suggested_action") or "").strip(),
            )
        )
    return out


async def _today_reminders(
    session: AsyncSession, manager: User, *, now: datetime
) -> list[tuple[Reminder, Client]]:
    day_start, day_end = _day_bounds_utc(now)
    stmt = (
        select(Reminder, Client)
        .join(Client, Client.id == Reminder.client_id)
        .where(
            Reminder.user_id == manager.id,
            Reminder.status == ReminderStatus.PENDING,
            Reminder.due_at >= day_start,
            Reminder.due_at < day_end,
        )
        .order_by(Reminder.due_at)
    )
    rows = (await session.execute(stmt)).all()
    return [(r, c) for r, c in rows]


async def collect_digest(
    session: AsyncSession, manager: User, *, now: datetime | None = None
) -> Digest:
    now = now or datetime.now(UTC)
    return Digest(
        manager=manager,
        needs_reply=await _needs_reply(session, manager),
        cold=await _cold_clients(session, manager, now=now),
        hot=await _hot_signals(session, manager, now=now),
        today_reminders=await _today_reminders(session, manager, now=now),
    )


def format_digest_html(digest: Digest) -> str:
    name = html.escape(digest.manager.name or "Менеджер")
    lines: list[str] = [f"☀️ <b>Дайджест на день, {name}</b>"]

    lines.append("")
    if digest.hot:
        lines.append(f"🔥 <b>Горящие сигналы ({len(digest.hot)})</b>")
        for signal in digest.hot:
            slug = html.escape(signal.client.slug)
            client_name = html.escape(signal.client.name or signal.client.slug)
            text = html.escape((signal.message.text or "").strip()[:160])
            lines.append(
                f"• {client_name} (<code>{slug}</code>) — {html.escape(signal.intent)}\n"
                f"  <i>{text}</i>"
            )
            if signal.suggested_action:
                lines.append(f"  ↳ {html.escape(signal.suggested_action)}")
        lines.append("")

    if digest.needs_reply:
        lines.append(f"💬 <b>Ждут ответа ({len(digest.needs_reply)})</b>")
        for c in digest.needs_reply:
            cname = html.escape(c.name or c.slug)
            lines.append(
                f"• {cname} (<code>{html.escape(c.slug)}</code>) — "
                f"вход. {_fmt_dt(c.last_inbound_at)}"
            )
        lines.append("")

    if digest.cold:
        lines.append(f"🧊 <b>Остывшие ({len(digest.cold)})</b>")
        for c in digest.cold:
            cname = html.escape(c.name or c.slug)
            stage = html.escape(c.stage.value)
            lines.append(
                f"• {cname} (<code>{html.escape(c.slug)}</code>) — {stage}, "
                f"last: {_fmt_dt(c.last_touch_at)}"
            )
        lines.append("")

    if digest.today_reminders:
        lines.append(f"⏰ <b>Напоминания на сегодня ({len(digest.today_reminders)})</b>")
        for reminder, c in digest.today_reminders:
            rid = short_id(reminder.id)
            cname = html.escape(c.name or c.slug)
            txt = html.escape(reminder_text(reminder))
            lines.append(f"• <code>{rid}</code> {_fmt_dt(reminder.due_at)} — {cname}\n  «{txt}»")
        lines.append("")

    if digest.is_empty():
        lines.append("Сегодня ничего срочного. Хорошего дня! ☕")

    return "\n".join(lines).rstrip()
