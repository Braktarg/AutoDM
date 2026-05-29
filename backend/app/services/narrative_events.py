"""
Event sourcing narrativo (Sprint 5):
- extrae eventos estructurados de turnos,
- los persiste en SQL,
- proyecta relaciones tipadas al GraphRAG.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NarrativeEvent
from app.services.graph_mentions import extract_mention_names
from app.services.graph_service import get_graph_backend


def _detect_event_type(text: str) -> str:
    t = text.lower()
    if any(k in t for k in ("ataca", "golpe", "daño", "combate", "dispara")):
        return "combat"
    if any(k in t for k in ("miente", "traicion", "engaña", "estafa")):
        return "betrayal"
    if any(k in t for k in ("negocia", "acuerdo", "pacto", "trato")):
        return "deal"
    if any(k in t for k in ("huye", "escapa", "retirada")):
        return "escape"
    if any(k in t for k in ("roba", "hurt", "saquea")):
        return "theft"
    if any(k in t for k in ("mata", "asesina", "ejecuta")):
        return "kill"
    return "scene_update"


def _severity(text: str) -> int:
    t = text.lower()
    if any(k in t for k in ("nuclear", "masacre", "colapso", "aniquila", "genoc")):
        return 3
    if any(k in t for k in ("mata", "asesina", "explota", "incendio", "guerra")):
        return 2
    return 1


def _extract_location(text: str) -> str | None:
    m = re.search(r"\b(en|hacia|desde)\s+([A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ\-]{2,}(?:\s+[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ\-]{2,})?)", text)
    if not m:
        return None
    return m.group(2)[:180]


async def record_turn_events(
    session: AsyncSession,
    campaign_id: str,
    *,
    player_text: str,
    dm_text: str,
    action_request: dict[str, Any] | None = None,
    action_effects: dict[str, Any] | None = None,
) -> list[NarrativeEvent]:
    names = [n for _, n in extract_mention_names(f"{player_text}\n{dm_text}")]
    uniq = list(dict.fromkeys(names))
    actors = uniq[:2]
    targets = uniq[2:5]

    action_type = (action_request or {}).get("action_type") if action_request else None
    at = str(action_type or "").lower()
    type_map = {
        "attack": "combat",
        "cast_spell": "combat",
        "talk": "deal",
        "use_item": "scene_update",
        "explore": "scene_update",
        "extreme": "escape",
    }
    event_type = type_map.get(at) or _detect_event_type(f"{player_text}\n{dm_text}")

    base_sev = _severity(f"{player_text}\n{dm_text}")
    if at in ("attack", "cast_spell"):
        sev = max(2, base_sev)
    elif at in ("talk", "deal"):
        sev = max(1, min(2, base_sev))
    else:
        sev = base_sev

    narration_facts = (action_effects or {}).get("narration_facts") or []
    facts_joined = "; ".join([str(x).strip() for x in narration_facts if str(x).strip()][:3])

    summary = facts_joined or dm_text.strip().replace("\n", " ")
    if len(summary) > 320:
        summary = summary[:317] + "..."
    ev = NarrativeEvent(
        campaign_id=campaign_id,
        event_type=event_type,
        summary=summary or "(sin resumen)",
        actors=actors,
        targets=targets,
        location=_extract_location(dm_text) or _extract_location(player_text),
        severity=sev,
        payload={
            "player": player_text[:400],
            "dm": dm_text[:600],
            "action_type": at or None,
            "effects": {
                "allowed": (action_effects or {}).get("allowed"),
                "block_reason": (action_effects or {}).get("block_reason"),
                "needs_roll": (action_effects or {}).get("needs_roll"),
                "inventory_consumptions": (action_effects or {}).get("inventory_consumptions")[:10]
                if (action_effects or {}).get("inventory_consumptions")
                else [],
                "world_state_delta": (action_effects or {}).get("world_state_delta"),
                "player_state_delta": (action_effects or {}).get("player_state_delta"),
                "narration_facts": narration_facts[:5],
            }
            if action_effects
            else None,
        },
    )
    session.add(ev)
    await session.commit()
    await session.refresh(ev)
    _project_event_to_graph(campaign_id, ev)
    return [ev]


def _project_event_to_graph(campaign_id: str, ev: NarrativeEvent) -> None:
    g = get_graph_backend(campaign_id)
    event_node = f"evt_{ev.id[:8]}"
    g.upsert_node(
        "Event",
        event_node,
        {
            "event_type": ev.event_type,
            "summary": ev.summary,
            "severity": ev.severity,
            "location": ev.location,
            "at": str(ev.created_at),
        },
    )
    all_people = list(dict.fromkeys((ev.actors or []) + (ev.targets or [])))
    for name in all_people:
        g.upsert_node("Entity", name, {"from_event": True})
        g.relate("Entity", name, "INVOLVED_IN", "Event", event_node)
    for a in ev.actors or []:
        for t in ev.targets or []:
            if a.lower() == t.lower():
                continue
            g.relate(
                "Entity",
                a,
                ev.event_type.upper(),
                "Entity",
                t,
                {"event_id": ev.id, "at": str(ev.created_at), "severity": ev.severity},
            )


async def build_hierarchical_memory_block(
    session: AsyncSession,
    campaign_id: str,
    *,
    session_summary: str | None,
) -> str:
    rs = await session.execute(
        select(NarrativeEvent)
        .where(NarrativeEvent.campaign_id == campaign_id)
        .order_by(desc(NarrativeEvent.created_at))
        .limit(80)
    )
    events = rs.scalars().all()
    if not events and not (session_summary or "").strip():
        return "(sin memoria episódica todavía)"

    recent = events[:8]
    recent_lines = [f"- [{e.event_type}] {e.summary}" for e in recent]

    type_count = Counter(e.event_type for e in events[:40])
    arc_lines = [f"- {k}: {v}" for k, v in type_count.most_common(6)]

    high = [e for e in events if e.severity >= 2][:10]
    high_lines = [f"- ({e.severity}) {e.summary}" for e in high]

    summary = (session_summary or "").strip() or "(sin resumen comprimido)"

    return (
        "### Memoria inmediata (resumen)\n"
        f"{summary}\n\n"
        "### Memoria episódica reciente\n"
        + ("\n".join(recent_lines) if recent_lines else "- (sin eventos)")
        + "\n\n### Memoria de arco (conteo por tipo)\n"
        + ("\n".join(arc_lines) if arc_lines else "- (sin arco aún)")
        + "\n\n### Memoria histórica crítica\n"
        + ("\n".join(high_lines) if high_lines else "- (sin eventos críticos)")
    )

