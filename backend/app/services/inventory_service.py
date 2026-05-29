"""Inventario por jugador y campaña — fuente oficial para el DM y la UI."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import CampaignMember, PlayerInventoryItem, User


async def party_inventory_markdown(session: AsyncSession, campaign_id: str) -> str:
    res = await session.execute(
        select(CampaignMember)
        .where(CampaignMember.campaign_id == campaign_id)
        .options(selectinload(CampaignMember.user))
    )
    members = res.scalars().all()
    if not members:
        return "(sin miembros en la mesa)"

    user_ids = [m.user_id for m in members]
    inv_res = await session.execute(
        select(PlayerInventoryItem)
        .where(
            PlayerInventoryItem.campaign_id == campaign_id,
            PlayerInventoryItem.user_id.in_(user_ids),
        )
        .order_by(
            PlayerInventoryItem.user_id,
            PlayerInventoryItem.sort_order,
            PlayerInventoryItem.name,
        )
    )
    rows = inv_res.scalars().all()
    by_user: dict[str, list[PlayerInventoryItem]] = {}
    for it in rows:
        if it.quantity < 1:
            continue
        by_user.setdefault(it.user_id, []).append(it)

    lines: list[str] = []
    for m in sorted(members, key=lambda x: (x.user.display_name or "").lower()):
        name = m.user.display_name if m.user else m.user_id
        items = by_user.get(m.user_id, [])
        if not items:
            lines.append(f"- **{name}**: (vacío)")
            continue
        parts = []
        for it in items:
            extra = f" — _{it.notes}_" if (it.notes or "").strip() else ""
            cat = f" [{it.category}]" if (it.category or "").strip() else ""
            parts.append(f"{it.name} ×{it.quantity}{cat}{extra}")
        lines.append(f"- **{name}**: " + "; ".join(parts))
    return "\n".join(lines)
