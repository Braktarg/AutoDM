"""
Resumen de sesión comprimido (cada N respuestas del DM). Sprint 2.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import Campaign, ChatMessage
from app.services.ollama_client import ollama_chat

logger = logging.getLogger(__name__)

SUMMARY_PROMPT = """Eres un archivista de mesa de rol. Sintetiza lo ocurrido para el director.
Salida: lista de viñetas en español (máximo 35 líneas cortas). Incluye sólo hechos y acuerdos jugables.
Debe cubrir: ubicación o escena actual, PNJs relevantes, objetivos abiertos, peligros, decisiones tomadas.
Si hay resumen previo, fusiónalo y actualiza (no repitas texto inútil). No inventes hechos que no aparezcan abajo.
"""

SUMMARY_USER_TEMPLATE = """### Resumen previo
{previous}

### Último tramo del chat (más reciente al final)
{chat}
"""


def _compose_summary_sync(previous: str | None, transcript: str) -> str:
    previous = (previous or "").strip() or "(ninguno)"
    user_content = SUMMARY_USER_TEMPLATE.format(previous=previous, chat=transcript)
    messages = [
        {"role": "system", "content": SUMMARY_PROMPT},
        {"role": "user", "content": user_content},
    ]
    if settings.llm_provider == "ollama":
        return ollama_chat(
            messages,
            options_extra={"temperature": 0.25, "num_predict": 512},
            timeout=min(180.0, settings.ollama_chat_timeout_seconds),
        )
    if not settings.openai_api_key:
        return previous if previous != "(ninguno)" else transcript[:1200]

    import httpx

    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.25,
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


async def maybe_refresh_session_summary(session: AsyncSession, campaign_id: str) -> None:
    n = settings.dm_session_summary_every_n
    if n <= 0:
        return

    cnt = await session.scalar(
        select(func.count(ChatMessage.id)).where(
            ChatMessage.campaign_id == campaign_id,
            ChatMessage.role == "assistant",
        )
    )
    assistant_count = int(cnt or 0)
    if assistant_count == 0 or assistant_count % n != 0:
        return

    camp = await session.get(Campaign, campaign_id)
    if not camp:
        return

    res = await session.execute(
        select(ChatMessage.role, ChatMessage.content)
        .where(ChatMessage.campaign_id == campaign_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(48)
    )
    rows = list(reversed(res.all()))
    lines: list[str] = []
    for role, content in rows:
        rlabel = "DM" if role == "assistant" else "Jugador"
        c = (content or "").strip().replace("\n", " ")
        if len(c) > 600:
            c = c[:597] + "…"
        lines.append(f"{rlabel}: {c}")
    transcript = "\n".join(lines)
    if not transcript.strip():
        return

    try:
        new_summary = await asyncio.to_thread(
            _compose_summary_sync,
            camp.session_summary,
            transcript,
        )
    except Exception as e:
        logger.warning("session_summary skip campaign=%s err=%s", campaign_id, e)
        return

    new_summary = (new_summary or "").strip()
    if not new_summary:
        return
    if len(new_summary) > 12000:
        new_summary = new_summary[:11997] + "…"

    camp.session_summary = new_summary
    await session.commit()
    logger.info(
        "session_summary updated campaign=%s assistant_turns=%s",
        campaign_id,
        assistant_count,
    )
