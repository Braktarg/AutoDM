"""
Motor AutoDM: combina RAG de reglas + contexto de fichas + GraphRAG de la mesa.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings
from app.services.dm_prompts import (
    CONSISTENCY_GUARD,
    NARRATION_VOICE,
    OPENING_COLD_START,
    OPENING_PARTS_RULE,
    OPENING_RECAP,
    SESSION_OPENING_LENGTH,
    campaign_addon_block,
    game_system_addon,
    scene_mode_hint,
)
from app.services.graph_service import get_graph_backend
from app.services.ollama_client import ollama_chat, ollama_chat_stream
from app.services.rag_service import retrieve_all_context


@dataclass(frozen=True)
class DmCampaignPromptContext:
    game_system: str | None = None
    session_summary: str | None = None
    dm_prompt_addon: str | None = None
    episodic_memory: str | None = None
    party_inventory: str | None = None


def _clock_ms_since(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 2)


SYSTEM_PROMPT = """Identidad: eres el Dungeon Master (AutoDM). Idioma de los jugadores por defecto **español**; conserva nombres propios del material tal cual.
Reglas: solo desde los fragmentos indexados de esta campaña; si falta una regla, dilo en mesa y propón tirada o criterio claro.
Coherencia: respeta mundo, fichas y memoria que el sistema inyecte en el mensaje de usuario; no contradigas hechos jugados sin causa dramática explícita.
"""


def _render_action_effects_reply(
    *,
    player_display_name: str,
    action_req: dict[str, Any],
    action_effects: dict[str, Any],
    world_state: dict[str, Any],
    player_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Narración determinista basada en ActionEffects.
    Evita improvisación descontrolada: todo viene de efectos validados.
    """
    effects_allowed = bool(action_effects.get("allowed"))
    action_type = str(action_req.get("action_type") or "unknown")

    scene_delta = (action_effects.get("world_state_delta") or {}).get("scene") or ""
    narration_facts = action_effects.get("narration_facts") or []
    dm_questions = action_effects.get("dm_questions") or []
    needs_clar = bool(action_effects.get("needs_player_clarification"))
    needs_roll = bool(action_effects.get("needs_roll"))

    scene_val = (world_state or {}).get("scene")
    if isinstance(scene_val, dict):
        current_scene = (scene_val or {}).get("summary") or ""
    elif isinstance(scene_val, str):
        current_scene = scene_val
    else:
        current_scene = ""

    if not current_scene:
        current_scene = (world_state or {}).get("scene_summary") or ""

    # 1) Estado actual (corto y funcional)
    estado = current_scene.strip() or "La escena sigue su curso, con detalles que encajan con lo que el estado operativo permite."

    # 2) Cambio + consecuencia
    if effects_allowed:
        cambio = scene_delta.strip() or f"El mundo responde a tu acción ({action_type})."
        consecuencias = "; ".join([f.strip() for f in narration_facts if f.strip()][:4]) or cambio
    else:
        cambio = "No se ejecuta la acción."
        consecuencias = "; ".join([f.strip() for f in narration_facts if f.strip()][:3]) or "El motor rechaza la intención por falta de condiciones."

    # 3) Nueva situación / pregunta
    question = dm_questions[0].strip() if dm_questions else "¿Qué haces ahora?"
    if needs_roll:
        question = f"{question} (Necesitas indicar la tirada o el criterio mecánico.)"
    if needs_clar:
        question = f"{question}"

    # Evitar prosa repetitiva: 5-7 líneas max.
    reply = (
        f"Estado actual: {estado}\n"
        f"Reacción del mundo: {cambio}\n"
        f"Consecuencia: {consecuencias}\n\n"
        f"Nueva situación: {question}"
    )

    return {
        "reply": reply.strip(),
        "effects_allowed": effects_allowed,
        "action_type": action_type,
    }


def build_dm_reply_from_action_effects(
    *,
    narrative_style: str | None,
    recent_chat: list[dict[str, str]],
    player_message: str,
    player_display_name: str,
    world_state: dict[str, Any],
    player_state: dict[str, Any] | None,
    action_request: dict[str, Any],
    action_effects: dict[str, Any],
    graph_context: str | None = None,
) -> dict[str, Any]:
    """
    Narración corta y jugable desde ActionEffects (sin decidir lógica).
    """
    # narrative_style y recent_chat se omiten en v1 para evitar “reinvención”;
    # la escena viene del estado operativo y efectos validados.
    rendered = _render_action_effects_reply(
        player_display_name=player_display_name,
        action_req=action_request,
        action_effects=action_effects,
        world_state=world_state,
        player_state=player_state,
    )
    return {
        "reply": rendered["reply"],
        "timings": {"render_ms": None, "model_ms": 0, "pace": settings.dm_pace},
        "rag_preview": {},
    }

BRIEF_REPLY_HINT = (
    "\n\nRitmo de mesa: prioriza respuestas breves (aprox. 120–280 palabras) con lo esencial "
    "para jugar ahora; solo extiéndete si el jugador pide más detalle o es un momento clave."
)


def _call_llm(
    messages: list[dict[str, str]],
) -> str:
    if settings.llm_provider == "ollama":
        return ollama_chat(messages)
    if not settings.openai_api_key:
        return (
            "[Demo sin OPENAI_API_KEY] "
            + messages[-1].get("content", "")[:500]
            + " … (configura OPENAI_API_KEY o usa LLM_PROVIDER=ollama)"
        )
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.85,
    }
    base = (settings.openai_base_url or "https://api.openai.com/v1").rstrip("/")
    with httpx.Client(timeout=120.0) as client:
        r = client.post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()


def _prepare_turn(
    campaign_id: str,
    narrative_style: str | None,
    recent_chat: list[dict[str, str]],
    player_message: str,
    player_display_name: str,
    *,
    campaign_ctx: DmCampaignPromptContext | None = None,
) -> tuple[list[dict[str, str]], dict[str, Any], Any]:
    ctx = campaign_ctx or DmCampaignPromptContext()
    rag = retrieve_all_context(
        campaign_id,
        player_message,
        rules_k=settings.dm_rag_rules_k,
        sheets_k=settings.dm_rag_sheets_k,
    )
    rules_block = "\n---\n".join(
        (
            f"[{x['metadata'].get('source_tier') or 'manual'}] "
            f"{x['metadata'].get('source', '')}: {x['text']}"
        )
        for x in rag["rules"]
    )
    sheets_block = "\n---\n".join(
        (
            f"[{x['metadata'].get('source_tier') or 'character'}] "
            f"{x['metadata'].get('display_name', '')}: {x['text']}"
        )
        for x in rag["character_context"]
    )
    graph = get_graph_backend(campaign_id)
    graph_ctx = graph.query_text_context(
        topics=player_message.lower().split()[:12],
        limit=settings.dm_graph_context_limit,
    )

    style = narrative_style or "Fantasía heroica, ritmo cinematográfico."
    scene_label, scene_block = (
        scene_mode_hint(player_message)
        if settings.dm_scene_adaptation
        else (None, "")
    )
    summary = (ctx.session_summary or "").strip()
    episodic = (ctx.episodic_memory or "").strip()
    summary_block = (
        f"### Resumen de sesión (memoria comprimida)\n{summary}\n"
        if summary
        else "### Resumen de sesión (memoria comprimida)\n(aún sin resumen automático; se genera cada N turnos de DM)\n"
    )
    episodic_block = (
        f"### Memoria episódica jerárquica\n{episodic}\n"
        if episodic
        else "### Memoria episódica jerárquica\n(sin capas episódicas aún)\n"
    )
    inv = (ctx.party_inventory or "").strip()
    inventory_block = (
        "### Inventario de la mesa (registrado en la app por los jugadores)\n"
        f"{inv}\n"
        "Usa esta lista como referencia de objetos portados; si el diálogo reciente contradice un objeto, prioriza la escena y actualiza coherencia en narración.\n"
        if inv
        else "### Inventario de la mesa (registrado en la app por los jugadores)\n"
        "(vacío: no asumas equipo concreto salvo fichas RAG o escena.)\n"
    )

    user_content = f"""Estilo de campaña: {style}

Jugador activo: {player_display_name}

{summary_block}
{episodic_block}
{inventory_block}
### Fragmentos de REGLAS (solo esta mesa)
{rules_block or '(sin fragmentos recuperados)'}

### Contexto de fichas / personajes (RAG)
{sheets_block or '(sin fichas indexadas o sin coincidencia)'}

### Estado del mundo (GraphRAG / memoria de mesa)
{graph_ctx}

### Mensaje del jugador
{player_message}
"""
    if scene_block:
        user_content = scene_block + "\n" + user_content

    system_prompt = SYSTEM_PROMPT + NARRATION_VOICE
    system_prompt += game_system_addon(ctx.game_system)
    system_prompt += campaign_addon_block(ctx.dm_prompt_addon)
    if settings.dm_consistency_guard:
        system_prompt += CONSISTENCY_GUARD
    system_prompt += BRIEF_REPLY_HINT if settings.dm_brief_replies else ""
    n_recent = max(1, settings.dm_recent_chat_messages)
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]
    for m in recent_chat[-n_recent:]:
        messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": user_content})

    rag_preview = {
        "rules_chunks": len(rag["rules"]),
        "sheet_chunks": len(rag["character_context"]),
        "scene_mode": scene_label,
        "rules_trace": rag.get("rules_trace") or [],
        "sheets_trace": rag.get("sheets_trace") or [],
    }
    return messages, rag_preview, graph


def _apply_graph_event(reply: str, graph: Any) -> None:
    if "evento:" in reply.lower() or "consecuencia" in reply.lower():
        graph.apply_event(
            summary=reply[:400],
            tags=["dm_turn"],
        )


def build_dm_reply(
    campaign_id: str,
    narrative_style: str | None,
    recent_chat: list[dict[str, str]],
    player_message: str,
    player_display_name: str,
    *,
    campaign_ctx: DmCampaignPromptContext | None = None,
) -> dict[str, Any]:
    tp = time.perf_counter()
    messages, rag_preview, graph = _prepare_turn(
        campaign_id,
        narrative_style,
        recent_chat,
        player_message,
        player_display_name,
        campaign_ctx=campaign_ctx,
    )
    prepare_ms = _clock_ms_since(tp)

    tl = time.perf_counter()
    reply = _call_llm(messages)
    llm_ms = _clock_ms_since(tl)
    _apply_graph_event(reply, graph)

    timings: dict[str, Any] = {
        "prepare_ms": prepare_ms,
        "llm_ms": llm_ms,
        "pace": settings.dm_pace,
    }
    return {
        "reply": reply,
        "rag_preview": rag_preview,
        "timings": timings,
    }


def build_dm_reply_stream(
    campaign_id: str,
    narrative_style: str | None,
    recent_chat: list[dict[str, str]],
    player_message: str,
    player_display_name: str,
    *,
    campaign_ctx: DmCampaignPromptContext | None = None,
) -> tuple[Iterator[str], dict[str, Any], dict[str, Any]]:
    timings: dict[str, Any] = {"pace": settings.dm_pace}
    tp = time.perf_counter()
    messages, rag_preview, graph = _prepare_turn(
        campaign_id,
        narrative_style,
        recent_chat,
        player_message,
        player_display_name,
        campaign_ctx=campaign_ctx,
    )
    timings["prepare_ms"] = _clock_ms_since(tp)

    if settings.llm_provider != "ollama":
        tl = time.perf_counter()
        reply = _call_llm(messages)
        timings["llm_ms"] = _clock_ms_since(tl)
        _apply_graph_event(reply, graph)

        def single_chunk() -> Iterator[str]:
            if reply:
                yield reply

        return single_chunk(), rag_preview, timings

    def stream_chunks() -> Iterator[str]:
        tl = time.perf_counter()
        parts: list[str] = []
        try:
            for piece in ollama_chat_stream(messages):
                parts.append(piece)
                yield piece
        finally:
            timings["llm_ms"] = _clock_ms_since(tl)
            _apply_graph_event("".join(parts).strip(), graph)

    return stream_chunks(), rag_preview, timings


SESSION_PART_MARKER = "<<<PART>>>"


def split_session_opening_parts(text: str) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    if SESSION_PART_MARKER in t:
        parts = [p.strip() for p in t.split(SESSION_PART_MARKER) if p.strip()]
        return parts if parts else [t]
    return [t]


def _compile_session_opening_messages(
    campaign_id: str,
    *,
    campaign_name: str,
    description: str | None,
    narrative_style: str | None,
    game_system: str | None,
    session_summary: str | None,
    dm_prompt_addon: str | None,
    member_names: list[str],
    party_inventory_markdown: str | None = None,
) -> tuple[list[dict[str, str]], str, dict[str, Any]]:
    summary_text = (session_summary or "").strip()
    opening_kind = "recap" if summary_text else "cold_open"

    rag_query = (
        "continuidad situación lugares PNJs tensión pendiente"
        if opening_kind == "recap"
        else (description or narrative_style or campaign_name or "ambientación mundo campaña")[
            :700
        ]
    )
    rag = retrieve_all_context(
        campaign_id,
        rag_query,
        rules_k=settings.dm_rag_rules_k,
        sheets_k=settings.dm_rag_sheets_k,
    )
    rules_block = "\n---\n".join(
        (
            f"[{x['metadata'].get('source_tier') or 'manual'}] "
            f"{x['metadata'].get('source', '')}: {x['text']}"
        )
        for x in rag["rules"]
    )
    sheets_block = "\n---\n".join(
        (
            f"[{x['metadata'].get('source_tier') or 'character'}] "
            f"{x['metadata'].get('display_name', '')}: {x['text']}"
        )
        for x in rag["character_context"]
    )
    graph = get_graph_backend(campaign_id)
    graph_ctx = graph.query_text_context([], limit=55)

    style = narrative_style or "Fantasía heroica, ritmo cinematográfico."
    party = ", ".join(member_names) if member_names else "(mesa sin lista de nombres)"

    desc_block = (description or "").strip()
    if not desc_block:
        desc_block = (
            "(No hay descripción larga en la ficha de campaña: "
            "extrae tono del nombre y del estilo narrativo sin contradecir las notas del director.)"
        )

    memory_section = (
        f"### Memoria de sesiones anteriores (resumen comprimido)\n{summary_text}\n"
        if opening_kind == "recap"
        else "### Memoria de sesiones anteriores\n(aún no hay resumen guardado: "
        "trata esta apertura como **primera sesión de campaña en mesa**.)\n"
    )
    inv_md = (party_inventory_markdown or "").strip()
    inventory_opening_section = (
        f"### Inventario registrado en la app (oficial)\n{inv_md}\n"
        if inv_md
        else "### Inventario registrado en la app (oficial)\n"
        "(vacío: los jugadores pueden añadir objetos desde la pantalla de ficha.)\n"
    )

    user_content = f"""Genera la narración de **apertura de sesión** usando solo la información de contexto que sigue.

### Datos de la mesa
- **Nombre de campaña**: {campaign_name}
- **Sistema / escenario**: {game_system or "no indicado"}
- **Estilo narrativo**: {style}

### Descripción / premisa (director de mesa)
{desc_block}

### Jugadores en esta mesa
{party}

{inventory_opening_section}{memory_section}
### Fragmentos de REGLAS (solo esta mesa)
{rules_block or "(sin fragmentos indexados; no inventes reglas mecánicas concretas.)"}

### Fichas / personajes (RAG)
{sheets_block or "(sin fichas indexadas.)"}

### Estado del mundo (GraphRAG)
{graph_ctx.strip() or "(grafo aún vacío: puedes plantar ambiente y tensión si encaja con la descripción y las notas del director.)"}
"""

    opening_instructions = OPENING_RECAP if opening_kind == "recap" else OPENING_COLD_START
    system_prompt = (
        SYSTEM_PROMPT
        + NARRATION_VOICE
        + SESSION_OPENING_LENGTH
        + game_system_addon(game_system)
        + campaign_addon_block(dm_prompt_addon)
        + CONSISTENCY_GUARD
        + opening_instructions
        + OPENING_PARTS_RULE
    )

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    rag_preview: dict[str, Any] = {
        "rules_chunks": len(rag["rules"]),
        "sheet_chunks": len(rag["character_context"]),
        "scene_mode": "session_open",
        "opening_kind": opening_kind,
        "rules_trace": rag.get("rules_trace") or [],
        "sheets_trace": rag.get("sheets_trace") or [],
    }
    return messages, opening_kind, rag_preview


def build_session_opening_reply(
    campaign_id: str,
    *,
    campaign_name: str,
    description: str | None,
    narrative_style: str | None,
    game_system: str | None,
    session_summary: str | None,
    dm_prompt_addon: str | None,
    member_names: list[str],
    party_inventory_markdown: str | None = None,
) -> dict[str, Any]:
    t_prep = time.perf_counter()
    messages, opening_kind, rag_preview = _compile_session_opening_messages(
        campaign_id,
        campaign_name=campaign_name,
        description=description,
        narrative_style=narrative_style,
        game_system=game_system,
        session_summary=session_summary,
        dm_prompt_addon=dm_prompt_addon,
        member_names=member_names,
        party_inventory_markdown=party_inventory_markdown,
    )
    prepare_ms = _clock_ms_since(t_prep)

    t_llm = time.perf_counter()
    raw = _call_llm(messages)
    llm_ms = _clock_ms_since(t_llm)

    parts = split_session_opening_parts(raw)
    if not parts:
        parts = [
            "*(El narrador no devolvió texto; revisa la conexión con el modelo e inténtalo de nuevo.)*"
        ]

    timings: dict[str, Any] = {
        "prepare_ms": prepare_ms,
        "llm_ms": llm_ms,
        "pace": settings.dm_pace,
    }
    return {
        "parts": parts,
        "opening_kind": opening_kind,
        "rag_preview": rag_preview,
        "timings": timings,
    }


def build_session_opening_stream(
    campaign_id: str,
    *,
    campaign_name: str,
    description: str | None,
    narrative_style: str | None,
    game_system: str | None,
    session_summary: str | None,
    dm_prompt_addon: str | None,
    member_names: list[str],
    party_inventory_markdown: str | None = None,
) -> tuple[Iterator[str], dict[str, Any], dict[str, Any], str]:
    t_prep = time.perf_counter()
    messages, opening_kind, rag_preview = _compile_session_opening_messages(
        campaign_id,
        campaign_name=campaign_name,
        description=description,
        narrative_style=narrative_style,
        game_system=game_system,
        session_summary=session_summary,
        dm_prompt_addon=dm_prompt_addon,
        member_names=member_names,
        party_inventory_markdown=party_inventory_markdown,
    )
    timings: dict[str, Any] = {
        "prepare_ms": _clock_ms_since(t_prep),
        "pace": settings.dm_pace,
        "opening_kind": opening_kind,
    }

    if settings.llm_provider != "ollama":
        t_llm = time.perf_counter()
        reply = _call_llm(messages)
        timings["llm_ms"] = _clock_ms_since(t_llm)

        def once() -> Iterator[str]:
            if reply:
                yield reply

        return once(), rag_preview, timings, opening_kind

    def stream_chunks() -> Iterator[str]:
        t_llm = time.perf_counter()
        try:
            for piece in ollama_chat_stream(messages):
                yield piece
        finally:
            timings["llm_ms"] = _clock_ms_since(t_llm)

    return stream_chunks(), rag_preview, timings, opening_kind
