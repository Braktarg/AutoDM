from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, select

from app.config import settings
from app.db.models import Campaign, NarrativeEvent
from app.db.session import async_session_factory
from app.services.chat_broadcast import notify_chat_updated
from app.services.narrative_events import _project_event_to_graph
from app.services.world_state_service import load_or_create_world_state

logger = logging.getLogger(__name__)


async def world_tick_loop(stop: asyncio.Event) -> None:
    interval = int(settings.world_tick_interval_seconds or 0)
    if interval <= 0:
        return
    logger.info("world_scheduler started interval=%ss", interval)
    while not stop.is_set():
        try:
            await _run_once()
        except Exception as e:
            logger.warning("world_scheduler tick error: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def _run_once() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=max(60, settings.world_tick_interval_seconds))
    async with async_session_factory() as session:
        rows = await session.execute(
            select(Campaign.id).where(Campaign.status == "active")
        )
        for (cid,) in rows.all():
            last = await session.execute(
                select(NarrativeEvent)
                .where(NarrativeEvent.campaign_id == cid)
                .order_by(desc(NarrativeEvent.created_at))
                .limit(1)
            )
            ev = last.scalar_one_or_none()
            if ev and ev.created_at and ev.created_at > cutoff:
                continue
            ws = await load_or_create_world_state(session, cid)
            ws_state: dict[str, Any] = dict(ws.state or {})

            t = (ws_state.get("time") or {}) if isinstance(ws_state.get("time"), dict) else {}
            cur_tick = t.get("tick") or 0
            try:
                cur_tick_i = int(cur_tick)
            except Exception:
                cur_tick_i = 0
            new_tick = cur_tick_i + 1

            # Rutina NPC mínima: rotación determinista del mood si existen NPCs.
            npcs = ws_state.get("npcs") if isinstance(ws_state.get("npcs"), list) else []
            moods = ["neutral", "alert", "restless", "busy"]
            npc_updates: list[dict[str, Any]] = []
            if npcs:
                for idx, npc in enumerate(npcs):
                    if not isinstance(npc, dict):
                        continue
                    name = str(npc.get("name") or "").strip()
                    if not name:
                        continue
                    npc_mood = moods[(new_tick + idx) % len(moods)]
                    npc["mood"] = npc_mood
                    npc["last_activity_tick"] = new_tick
                    npc_updates.append({"name": name, "mood": npc_mood})

            # Persistimos delta (sin reconstruir el mundo completo).
            ws_state["time"] = {"tick": new_tick}
            ws.state = ws_state
            await session.flush()

            summary = (
                f"World tick: el tiempo avanza a t={new_tick}."
                if not npc_updates
                else f"World tick: t={new_tick}. Actividad NPC: " + ", ".join(
                    [f"{x['name']}({x['mood']})" for x in npc_updates[:4]]
                ) + ("" if len(npc_updates) <= 4 else "…")
            )

            new_ev = NarrativeEvent(
                campaign_id=cid,
                event_type="world_tick",
                summary=summary,
                actors=[x.get("name") for x in npc_updates[:3] if x.get("name")],
                targets=[],
                severity=1,
                payload={"scheduler": True, "tick": new_tick, "npc_updates": npc_updates},
            )
            session.add(new_ev)
            await session.flush()
            _project_event_to_graph(cid, new_ev)
            notify_chat_updated(cid)

        await session.commit()

