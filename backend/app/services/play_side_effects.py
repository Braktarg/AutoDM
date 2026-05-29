"""Trabajo post-turno que no debe bloquear la respuesta al jugador."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.config import settings
from app.db.session import async_session_factory
from app.services.graph_mentions import apply_graph_mentions
from app.services.graph_service import project_effects_to_graph
from app.services.narrative_events import record_turn_events
from app.services.session_memory import maybe_refresh_session_summary

logger = logging.getLogger(__name__)


async def run_play_turn_side_effects(
    *,
    campaign_id: str,
    player_text: str,
    dm_text: str,
    player_display_name: str,
    action_request: dict[str, Any],
    action_effects: dict[str, Any],
    user_mentions: dict[str, Any] | None = None,
) -> None:
    if not settings.dm_play_defer_heavy_work:
        return
    try:
        await asyncio.to_thread(
            project_effects_to_graph,
            campaign_id,
            player_display_name=player_display_name,
            action_request=action_request,
            action_effects=action_effects,
        )
    except Exception as e:
        logger.info("deferred project_effects_to_graph: %s", e)

    try:
        am = apply_graph_mentions(campaign_id, dm_text, role="assistant")
        um = user_mentions or {}
        if um.get("names") or am.get("names"):
            logger.info(
                "deferred graph_mentions campaign=%s user=%s assistant=%s",
                campaign_id,
                um,
                am,
            )
    except Exception as e:
        logger.info("deferred graph_mentions: %s", e)

    async with async_session_factory() as session:
        try:
            await record_turn_events(
                session,
                campaign_id,
                player_text=player_text,
                dm_text=dm_text,
                action_request=action_request,
                action_effects=action_effects,
            )
            await maybe_refresh_session_summary(session, campaign_id)
            await session.commit()
        except Exception:
            logger.exception("deferred play side effects failed campaign=%s", campaign_id)
            await session.rollback()


def schedule_play_turn_side_effects(**kwargs: Any) -> None:
    asyncio.create_task(run_play_turn_side_effects(**kwargs))
