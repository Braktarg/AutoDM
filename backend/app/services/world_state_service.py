from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlayerState, WorldState


async def load_or_create_world_state(
    session: AsyncSession, campaign_id: str
) -> WorldState:
    res = await session.execute(
        select(WorldState).where(WorldState.campaign_id == campaign_id)
    )
    row = res.scalar_one_or_none()
    if row:
        return row
    row = WorldState(
        campaign_id=campaign_id,
        state={
            "version": 1,
            "scene": {},
            "npcs": [],
            "conflicts": [],
            "reputation": {},
            "time": {"tick": 0},
        },
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


def _deep_merge(dst: dict[str, Any], src: Mapping[str, Any]) -> dict[str, Any]:
    for k, v in src.items():
        if isinstance(v, Mapping) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)  # type: ignore[index]
        else:
            dst[k] = v
    return dst


async def apply_world_delta(
    session: AsyncSession, campaign_id: str, delta: Mapping[str, Any]
    , *,
    commit: bool = True,
) -> WorldState:
    ws = await load_or_create_world_state(session, campaign_id)
    base = dict(ws.state or {})
    _deep_merge(base, delta)
    ws.state = base
    if commit:
        await session.commit()
        await session.refresh(ws)
    return ws


async def load_or_create_player_state(
    session: AsyncSession, campaign_id: str, user_id: str
) -> PlayerState:
    res = await session.execute(
        select(PlayerState).where(
            PlayerState.campaign_id == campaign_id, PlayerState.user_id == user_id
        )
    )
    row = res.scalar_one_or_none()
    if row:
        return row
    row = PlayerState(
        campaign_id=campaign_id,
        user_id=user_id,
        state={
            "version": 1,
            "hp": {},
            "conditions": [],
            "resources": {},
            "equipment": [],
            "inventory_claims": [],
            "skills": [],
            "spells": [],
            "magic_known": [],
            "uncertain_fields": [],
            "pending": {},
        },
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def apply_player_delta(
    session: AsyncSession, campaign_id: str, user_id: str, delta: Mapping[str, Any]
    , *,
    commit: bool = True,
) -> PlayerState:
    ps = await load_or_create_player_state(session, campaign_id, user_id)
    base = dict(ps.state or {})
    _deep_merge(base, delta)
    ps.state = base
    if commit:
        await session.commit()
        await session.refresh(ps)
    return ps

