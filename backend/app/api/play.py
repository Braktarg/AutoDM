import asyncio
import json
import logging
import time
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ActionEffects,
    ActionRequest,
    CampaignMember,
    ChatMessage,
    NarrativeEvent,
    PlayerInventoryItem,
    User,
)
from app.db.session import get_session
from app.deps import (
    get_campaign_for_user,
    get_current_user,
    require_sse_campaign_member,
)
from app.config import settings
from app.schemas import (
    ChatMessageOut,
    PlayTurnRequest,
    PlayTurnResponse,
    NarrativeEventOut,
    SessionOpenRequest,
    SessionOpenResponse,
)
from app.services.action_pipeline import handle_action_pipeline
from app.services.dm_engine import (
    DmCampaignPromptContext,
    build_dm_reply,
    build_dm_reply_from_action_effects,
    build_dm_reply_stream,
    build_session_opening_reply,
    build_session_opening_stream,
    split_session_opening_parts,
)
from app.services.chat_broadcast import notify_chat_updated, subscribe, unsubscribe
from app.services.consistency_eval import evaluate_dm_reply
from app.services.graph_mentions import apply_graph_mentions
from app.services.graph_service import project_effects_to_graph
from app.services.narrative_events import (
    build_hierarchical_memory_block,
    record_turn_events,
)
from app.services.inventory_service import party_inventory_markdown
from app.services.play_side_effects import schedule_play_turn_side_effects
from app.services.session_memory import maybe_refresh_session_summary
from app.services.world_state_service import (
    apply_player_delta,
    apply_world_delta,
    load_or_create_player_state,
    load_or_create_world_state,
)

router = APIRouter(prefix="/campaigns/{campaign_id}", tags=["play"])
logger = logging.getLogger(__name__)


@router.get("/messages", response_model=list[ChatMessageOut])
async def list_messages(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[ChatMessageOut]:
    await get_campaign_for_user(campaign_id, session, user)
    res = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.campaign_id == campaign_id)
        .order_by(ChatMessage.created_at.asc())
    )
    rows = res.scalars().all()
    return [ChatMessageOut.model_validate(m) for m in rows]


@router.get("/events/types", response_model=list[str])
async def list_narrative_event_types(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[str]:
    await get_campaign_for_user(campaign_id, session, user)
    res = await session.execute(
        select(NarrativeEvent.event_type)
        .where(NarrativeEvent.campaign_id == campaign_id)
        .distinct()
    )
    return sorted({row[0] for row in res.all() if row[0]})


@router.get("/events", response_model=list[NarrativeEventOut])
async def list_narrative_events(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    event_type: str | None = Query(default=None, max_length=64),
    min_severity: int | None = Query(default=None, ge=1, le=3),
    limit: int = Query(default=40, ge=1, le=120),
) -> list[NarrativeEventOut]:
    await get_campaign_for_user(campaign_id, session, user)
    conds = [NarrativeEvent.campaign_id == campaign_id]
    if event_type and event_type.strip():
        conds.append(NarrativeEvent.event_type == event_type.strip())
    if min_severity is not None:
        conds.append(NarrativeEvent.severity >= min_severity)
    res = await session.execute(
        select(NarrativeEvent)
        .where(and_(*conds))
        .order_by(desc(NarrativeEvent.created_at))
        .limit(limit)
    )
    rows = list(reversed(res.scalars().all()))
    return [NarrativeEventOut.model_validate(e) for e in rows]


@router.get("/messages/events")
async def messages_events_stream(
    campaign_id: str,
    _user: Annotated[User, Depends(require_sse_campaign_member)],
) -> StreamingResponse:
    """
    SSE: avisa cuando hay mensajes nuevos en la mesa (otros jugadores / DM).
    Usar `?token=<JWT>` porque EventSource no admite cabecera Authorization.
    """
    async def gen():
        q = subscribe(campaign_id)
        try:
            yield (
                "data: "
                + json.dumps({"type": "connected", "campaign_id": campaign_id})
                + "\n\n"
            )
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=25.0)
                    yield f"data: {json.dumps(item)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            unsubscribe(campaign_id, q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/session/open", response_model=SessionOpenResponse)
async def session_open(
    campaign_id: str,
    body: SessionOpenRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> SessionOpenResponse:
    """
    Primera intervención del DM al «levantar la sesión»:
    - Si existe `session_summary`, recapitula la sesión anterior.
    - Si no (primera vez), entrega contexto de mundo y gancho como inicio de campaña.
    Puede partir la respuesta en varios mensajes usando el marcador <<<PART>>> en el modelo.
    """
    c = await get_campaign_for_user(campaign_id, session, user)

    res_names = await session.execute(
        select(User.display_name)
        .join(CampaignMember, CampaignMember.user_id == User.id)
        .where(CampaignMember.campaign_id == campaign_id)
        .order_by(User.display_name.asc())
    )
    member_names = [row[0] for row in res_names.all()]

    inv_md = await party_inventory_markdown(session, campaign_id)
    try:
        result = await asyncio.to_thread(
            build_session_opening_reply,
            campaign_id,
            campaign_name=c.name,
            description=c.description,
            narrative_style=c.narrative_style,
            game_system=c.game_system,
            session_summary=c.session_summary,
            dm_prompt_addon=c.dm_prompt_addon,
            member_names=member_names,
            party_inventory_markdown=inv_md,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    saved: list[ChatMessage] = []
    for part in result["parts"]:
        msg = ChatMessage(
            id=str(uuid.uuid4()),
            campaign_id=campaign_id,
            user_id=None,
            role="assistant",
            content=part,
        )
        session.add(msg)
        saved.append(msg)

    if body.activate_campaign and c.status == "lobby":
        c.status = "active"

    await session.commit()
    for m in saved:
        await session.refresh(m)

    notify_chat_updated(campaign_id)

    opening_text = "\n\n".join(result["parts"])
    await record_turn_events(
        session,
        campaign_id,
        player_text="[system] session_open",
        dm_text=opening_text,
    )
    gmeta = apply_graph_mentions(campaign_id, opening_text, role="assistant")
    if gmeta.get("names"):
        logger.info("graph_mentions session_open campaign=%s %s", campaign_id, gmeta)

    return SessionOpenResponse(
        messages=[ChatMessageOut.model_validate(m) for m in saved],
        opening_kind=result["opening_kind"],
        rag_preview=result["rag_preview"],
        timings=result["timings"],
    )


@router.post("/session/open/stream")
async def session_open_stream(
    campaign_id: str,
    body: SessionOpenRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    c = await get_campaign_for_user(campaign_id, session, user)

    res_names = await session.execute(
        select(User.display_name)
        .join(CampaignMember, CampaignMember.user_id == User.id)
        .where(CampaignMember.campaign_id == campaign_id)
        .order_by(User.display_name.asc())
    )
    member_names = [row[0] for row in res_names.all()]

    inv_md = await party_inventory_markdown(session, campaign_id)
    try:
        stream_iter, rag_preview, timings_ref, opening_kind = await asyncio.to_thread(
            build_session_opening_stream,
            campaign_id,
            campaign_name=c.name,
            description=c.description,
            narrative_style=c.narrative_style,
            game_system=c.game_system,
            session_summary=c.session_summary,
            dm_prompt_addon=c.dm_prompt_addon,
            member_names=member_names,
            party_inventory_markdown=inv_md,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    turn_t_outer = time.perf_counter()

    async def event_gen():
        parts_acc: list[str] = []
        sent_done = False
        try:
            meta = {
                "type": "meta",
                "rag_preview": rag_preview,
                "opening_kind": opening_kind,
                "pace": settings.dm_pace,
                "timings": {
                    "prepare_ms": timings_ref.get("prepare_ms"),
                    "pace": timings_ref.get("pace", settings.dm_pace),
                },
            }
            yield f"data: {json.dumps(meta)}\n\n"

            for chunk in stream_iter:
                parts_acc.append(chunk)
                yield f"data: {json.dumps({'type': 'chunk', 'content': chunk})}\n\n"

            full = "".join(parts_acc).strip()
            split_parts = split_session_opening_parts(full)
            if not split_parts:
                split_parts = [
                    "*Error: no se generó texto de apertura. Reintenta.*"
                ]

            saved_rows: list[ChatMessage] = []
            ids_out: list[str] = []
            for part in split_parts:
                mid = str(uuid.uuid4())
                ids_out.append(mid)
                row = ChatMessage(
                    id=mid,
                    campaign_id=campaign_id,
                    user_id=None,
                    role="assistant",
                    content=part,
                )
                session.add(row)
                saved_rows.append(row)

            if body.activate_campaign and c.status == "lobby":
                c.status = "active"

            persist_t0 = time.perf_counter()
            await session.commit()
            timings_ref["persist_ms"] = round(
                (time.perf_counter() - persist_t0) * 1000, 2
            )
            timings_ref["total_ms"] = round(
                (time.perf_counter() - turn_t_outer) * 1000, 2
            )
            notify_chat_updated(campaign_id)

            for m in saved_rows:
                await session.refresh(m)

            opening_text = "\n\n".join(split_parts)
            await record_turn_events(
                session,
                campaign_id,
                player_text="[system] session_open_stream",
                dm_text=opening_text,
            )
            gmeta = apply_graph_mentions(campaign_id, opening_text, role="assistant")
            if gmeta.get("names"):
                logger.info(
                    "graph_mentions session_open_stream campaign=%s %s",
                    campaign_id,
                    gmeta,
                )

            sent_done = True
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "done",
                        "opening_kind": opening_kind,
                        "messages": [
                            ChatMessageOut.model_validate(m).model_dump(mode="json")
                            for m in saved_rows
                        ],
                        "timings": timings_ref,
                    }
                )
                + "\n\n"
            )
        except RuntimeError as e:
            await session.rollback()
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"
        except Exception:
            await session.rollback()
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "error",
                        "detail": "Error interno generando la apertura de sesión.",
                    }
                )
                + "\n\n"
            )
        finally:
            if not sent_done:
                yield "data: {\"type\":\"end\"}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/play", response_model=PlayTurnResponse)
async def play_turn(
    campaign_id: str,
    body: PlayTurnRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> PlayTurnResponse:
    turn_t_outer = time.perf_counter()
    c = await get_campaign_for_user(campaign_id, session, user)

    user_msg = ChatMessage(
        campaign_id=campaign_id,
        user_id=user.id,
        role="user",
        content=body.message,
    )
    session.add(user_msg)
    await session.flush()
    # Persistimos el mensaje del jugador antes de llamar al LLM:
    # si el modelo falla, el chat igualmente conserva lo que escribió.
    await session.commit()
    notify_chat_updated(campaign_id)
    um = apply_graph_mentions(campaign_id, body.message, role="user")

    res_hist = await session.execute(
        select(ChatMessage)
        .where(ChatMessage.campaign_id == campaign_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(40)
    )
    hist_rows = list(reversed(res_hist.scalars().all()))
    recent = []
    for m in hist_rows[:-1]:
        recent.append({"role": m.role, "content": m.content})

    async def _apply_inventory_consumptions(
        *,
        effects: dict[str, Any],
    ) -> None:
        consumptions = effects.get("inventory_consumptions") or []
        if not consumptions:
            return
        rows_res = await session.execute(
            select(PlayerInventoryItem).where(
                PlayerInventoryItem.campaign_id == campaign_id,
                PlayerInventoryItem.user_id == user.id,
                PlayerInventoryItem.quantity >= 1,
            )
        )
        rows = list(rows_res.scalars().all())

        def norm(n: str) -> str:
            return " ".join((n or "").strip().split()).lower()

        for citem in consumptions:
            item_name = norm(str(citem.get("item_name") or ""))
            qty = citem.get("quantity") or 0
            try:
                qty_needed = int(qty)
            except Exception:
                qty_needed = 0
            if not item_name or qty_needed <= 0:
                continue

            # Consume en “pila” recorriendo items existentes.
            for row in sorted(
                [r for r in rows if norm(r.name) == item_name],
                key=lambda r: (r.sort_order, r.name),
            ):
                if qty_needed <= 0:
                    break
                take = min(int(row.quantity), qty_needed)
                if take <= 0:
                    continue
                row.quantity = int(row.quantity) - take
                qty_needed -= take
                if row.quantity <= 0:
                    await session.delete(row)

            if qty_needed > 0:
                raise RuntimeError(
                    f"No hay inventario suficiente para consumir '{item_name}' (faltan {qty_needed})."
                )

    # 1) Build context operativo: WorldState + PlayerState + inventario estructurado
    ws = await load_or_create_world_state(session, campaign_id)
    ps = await load_or_create_player_state(session, campaign_id, user.id)
    player_state = dict(ps.state or {})

    inv_rows = await session.execute(
        select(PlayerInventoryItem)
        .where(
            PlayerInventoryItem.campaign_id == campaign_id,
            PlayerInventoryItem.user_id == user.id,
            PlayerInventoryItem.quantity >= 1,
        )
    )
    inv_structured = [
        {"name": r.name, "quantity": int(r.quantity)}
        for r in inv_rows.scalars().all()
        if r.quantity and int(r.quantity) > 0
    ]

    # 2) Pipeline de acción (parse -> validate -> resolve -> parches)
    try:
        pipeline = await handle_action_pipeline(
            player_text=body.message,
            campaign_id=campaign_id,
            user_id=user.id,
            player_state=player_state,
            world_state=(ws.state or {}),
            inventory_items=inv_structured,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    action_req = pipeline["action_request"]
    validation = pipeline["validation"]
    effects = pipeline["effects"]
    world_delta = pipeline["world_delta"] or {}
    player_delta = pipeline["player_delta"] or {}

    # 3) Persistimos el request + effects para auditoría
    ar = ActionRequest(
        campaign_id=campaign_id,
        user_id=user.id,
        player_text=body.message,
        parsed=action_req,
        validation=validation,
    )
    session.add(ar)
    await session.flush()

    ae = ActionEffects(
        campaign_id=campaign_id,
        action_request_id=ar.id,
        effects=effects,
        applied=bool(effects.get("allowed")),
    )
    session.add(ae)

    # 4) Aplicación determinista de parches (y consumos de inventario)
    if effects.get("allowed"):
        await _apply_inventory_consumptions(effects=effects)
        if world_delta:
            ws = await apply_world_delta(
                session, campaign_id, world_delta, commit=False
            )
        if player_delta:
            ps = await apply_player_delta(
                session, campaign_id, user.id, player_delta, commit=False
            )

    if not settings.dm_play_defer_heavy_work:
        try:
            project_effects_to_graph(
                campaign_id,
                player_display_name=user.display_name,
                action_request=action_req,
                action_effects=effects,
            )
        except Exception as e:
            logger.info("project_effects_to_graph failed: %s", e)

    # 5) Narración limitada desde ActionEffects
    rendered = build_dm_reply_from_action_effects(
        narrative_style=c.narrative_style,
        recent_chat=recent,
        player_message=body.message,
        player_display_name=user.display_name,
        world_state=ws.state or {},
        player_state=ps.state or {},
        action_request=action_req,
        action_effects=effects,
    )

    assistant = ChatMessage(
        campaign_id=campaign_id,
        user_id=None,
        role="assistant",
        content=rendered["reply"],
    )
    session.add(assistant)
    persist_t0 = time.perf_counter()
    await session.commit()
    persist_ms = round((time.perf_counter() - persist_t0) * 1000, 2)
    notify_chat_updated(campaign_id)

    t_out = dict(rendered.get("timings") or {})
    t_out["persist_ms"] = persist_ms
    t_out["total_ms"] = round((time.perf_counter() - turn_t_outer) * 1000, 2)
    t_out["pipeline_meta"] = pipeline.get("pipeline_meta") or {}
    logger.info("play_turn campaign=%s timings=%s", campaign_id, t_out)

    if settings.dm_play_defer_heavy_work:
        schedule_play_turn_side_effects(
            campaign_id=campaign_id,
            player_text=body.message,
            dm_text=rendered["reply"],
            player_display_name=user.display_name,
            action_request=action_req,
            action_effects=effects,
            user_mentions=um,
        )
    else:
        if settings.dm_consistency_guard:
            consistency = evaluate_dm_reply(rendered["reply"])
            t_out["consistency_score"] = consistency["score"]
            t_out["consistency_issues"] = consistency["issues"]
        await maybe_refresh_session_summary(session, campaign_id)
        await record_turn_events(
            session,
            campaign_id,
            player_text=body.message,
            dm_text=rendered["reply"],
            action_request=action_req,
            action_effects=effects,
        )
        apply_graph_mentions(campaign_id, rendered["reply"], role="assistant")

    return PlayTurnResponse(
        reply=rendered["reply"],
        rag_preview=rendered.get("rag_preview") or {},
        timings=t_out,
    )


@router.post("/play/stream")
async def play_turn_stream(
    campaign_id: str,
    body: PlayTurnRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> StreamingResponse:
    c = await get_campaign_for_user(campaign_id, session, user)

    user_msg = ChatMessage(
        campaign_id=campaign_id,
        user_id=user.id,
        role="user",
        content=body.message,
    )
    session.add(user_msg)
    await session.flush()
    # Persistimos el input del jugador antes del stream del DM.
    await session.commit()
    notify_chat_updated(campaign_id)
    um = apply_graph_mentions(campaign_id, body.message, role="user")

    turn_t_outer = time.perf_counter()

    async def _apply_inventory_consumptions(
        *, effects: dict[str, Any]
    ) -> None:
        consumptions = effects.get("inventory_consumptions") or []
        if not consumptions:
            return
        rows_res = await session.execute(
            select(PlayerInventoryItem).where(
                PlayerInventoryItem.campaign_id == campaign_id,
                PlayerInventoryItem.user_id == user.id,
                PlayerInventoryItem.quantity >= 1,
            )
        )
        rows = list(rows_res.scalars().all())

        def norm(n: str) -> str:
            return " ".join((n or "").strip().split()).lower()

        for citem in consumptions:
            item_name = norm(str(citem.get("item_name") or ""))
            qty = citem.get("quantity") or 0
            try:
                qty_needed = int(qty)
            except Exception:
                qty_needed = 0
            if not item_name or qty_needed <= 0:
                continue

            for row in sorted(
                [r for r in rows if norm(r.name) == item_name],
                key=lambda r: (r.sort_order, r.name),
            ):
                if qty_needed <= 0:
                    break
                take = min(int(row.quantity), qty_needed)
                if take <= 0:
                    continue
                row.quantity = int(row.quantity) - take
                qty_needed -= take
                if row.quantity <= 0:
                    await session.delete(row)

            if qty_needed > 0:
                raise RuntimeError(
                    f"No hay inventario suficiente para consumir '{item_name}' (faltan {qty_needed})."
                )

    async def event_gen():
        parts: list[str] = []
        sent_done = False
        timings_ref: dict[str, Any] = {
            "pace": settings.dm_pace,
            "pipeline": settings.dm_action_pipeline,
        }
        action_req: dict[str, Any] = {}
        effects: dict[str, Any] = {}
        reply_text = ""
        try:
            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "meta",
                        "rag_preview": {},
                        "pace": settings.dm_pace,
                        "timings": {"status": "processing", "pipeline": settings.dm_action_pipeline},
                    }
                )
                + "\n\n"
            )

            prep_t0 = time.perf_counter()
            ws, ps, inv_rows, hist_rows = await asyncio.gather(
                load_or_create_world_state(session, campaign_id),
                load_or_create_player_state(session, campaign_id, user.id),
                session.execute(
                    select(PlayerInventoryItem).where(
                        PlayerInventoryItem.campaign_id == campaign_id,
                        PlayerInventoryItem.user_id == user.id,
                        PlayerInventoryItem.quantity >= 1,
                    )
                ),
                session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.campaign_id == campaign_id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(40)
                ),
            )
            inv_structured = [
                {"name": r.name, "quantity": int(r.quantity)}
                for r in inv_rows.scalars().all()
                if r.quantity and int(r.quantity) > 0
            ]
            hist_list = list(reversed(hist_rows.scalars().all()))
            recent = [
                {"role": m.role, "content": m.content} for m in hist_list[:-1]
            ]

            pipeline_t0 = time.perf_counter()
            pipeline = await handle_action_pipeline(
                player_text=body.message,
                campaign_id=campaign_id,
                user_id=user.id,
                player_state=(ps.state or {}),
                world_state=(ws.state or {}),
                inventory_items=inv_structured,
            )
            timings_ref["pipeline_ms"] = round(
                (time.perf_counter() - pipeline_t0) * 1000, 2
            )
            timings_ref["pipeline_meta"] = pipeline.get("pipeline_meta") or {}

            action_req = pipeline["action_request"]
            validation = pipeline["validation"]
            effects = pipeline["effects"]
            world_delta = pipeline["world_delta"] or {}
            player_delta = pipeline["player_delta"] or {}

            ar = ActionRequest(
                campaign_id=campaign_id,
                user_id=user.id,
                player_text=body.message,
                parsed=action_req,
                validation=validation,
            )
            session.add(ar)
            await session.flush()

            ae = ActionEffects(
                campaign_id=campaign_id,
                action_request_id=ar.id,
                effects=effects,
                applied=bool(effects.get("allowed")),
            )
            session.add(ae)

            if effects.get("allowed"):
                await _apply_inventory_consumptions(effects=effects)
                if world_delta:
                    ws = await apply_world_delta(
                        session, campaign_id, world_delta, commit=False
                    )
                if player_delta:
                    ps = await apply_player_delta(
                        session, campaign_id, user.id, player_delta, commit=False
                    )

            if not settings.dm_play_defer_heavy_work:
                try:
                    project_effects_to_graph(
                        campaign_id,
                        player_display_name=user.display_name,
                        action_request=action_req,
                        action_effects=effects,
                    )
                except Exception as e:
                    logger.info("project_effects_to_graph failed: %s", e)

            rendered = build_dm_reply_from_action_effects(
                narrative_style=c.narrative_style,
                recent_chat=recent,
                player_message=body.message,
                player_display_name=user.display_name,
                world_state=ws.state or {},
                player_state=ps.state or {},
                action_request=action_req,
                action_effects=effects,
            )
            reply_text = rendered["reply"]
            timings_ref["prepare_ms"] = round(
                (time.perf_counter() - prep_t0) * 1000, 2
            )
            rag_preview = rendered.get("rag_preview") or {}

            meta = {
                "type": "meta",
                "rag_preview": rag_preview,
                "pace": settings.dm_pace,
                "timings": {
                    "prepare_ms": timings_ref.get("prepare_ms"),
                    "pipeline_ms": timings_ref.get("pipeline_ms"),
                    "pace": timings_ref.get("pace", settings.dm_pace),
                    "pipeline": timings_ref.get("pipeline"),
                    "pipeline_meta": timings_ref.get("pipeline_meta"),
                },
            }
            yield f"data: {json.dumps(meta)}\n\n"
            # Streaming “simple” desde una respuesta determinista.
            # Esto mantiene compatibilidad con el frontend (chunk/done).
            chunk_size = 60
            for i in range(0, len(reply_text), chunk_size):
                chunk = reply_text[i : i + chunk_size]
                if not chunk:
                    continue
                parts.append(chunk)
                payload = {"type": "chunk", "content": chunk}
                yield f"data: {json.dumps(payload)}\n\n"

            reply = "".join(parts).strip()
            assistant = ChatMessage(
                campaign_id=campaign_id,
                user_id=None,
                role="assistant",
                content=reply,
            )
            session.add(assistant)
            persist_t0 = time.perf_counter()
            await session.commit()
            timings_ref["persist_ms"] = round(
                (time.perf_counter() - persist_t0) * 1000, 2
            )
            notify_chat_updated(campaign_id)
            timings_ref["total_ms"] = round(
                (time.perf_counter() - turn_t_outer) * 1000, 2
            )

            logger.info(
                "play_stream campaign=%s timings=%s",
                campaign_id,
                timings_ref,
            )
            if settings.dm_play_defer_heavy_work:
                schedule_play_turn_side_effects(
                    campaign_id=campaign_id,
                    player_text=body.message,
                    dm_text=reply,
                    player_display_name=user.display_name,
                    action_request=action_req,
                    action_effects=effects,
                    user_mentions=um,
                )
            else:
                await maybe_refresh_session_summary(session, campaign_id)
                await record_turn_events(
                    session,
                    campaign_id,
                    player_text=body.message,
                    dm_text=reply,
                    action_request=action_req,
                    action_effects=effects,
                )
                if settings.dm_consistency_guard:
                    consistency = evaluate_dm_reply(reply)
                    timings_ref["consistency_score"] = consistency["score"]
                    timings_ref["consistency_issues"] = consistency["issues"]
                apply_graph_mentions(campaign_id, reply, role="assistant")

            sent_done = True
            yield (
                "data: "
                + json.dumps({"type": "done", "reply": reply, "timings": timings_ref})
                + "\n\n"
            )
        except RuntimeError as e:
            await session.rollback()
            yield f"data: {json.dumps({'type': 'error', 'detail': str(e)})}\n\n"
        except Exception:
            await session.rollback()
            yield "data: {\"type\":\"error\",\"detail\":\"Error interno durante streaming.\"}\n\n"
        finally:
            if not sent_done:
                yield "data: {\"type\":\"end\"}\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
