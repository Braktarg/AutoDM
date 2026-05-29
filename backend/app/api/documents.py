import uuid
import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import CampaignDocument, DocKind, User
from app.db.session import get_session
from app.deps import get_campaign_for_user, get_current_user
from app.schemas import CampaignDocumentOut
from app.services import rag_service
from app.services.pdf_ingest import chunk_text, extract_text_from_pdf
from app.services.rag_sources import normalize_rule_source_tier
from app.services.character_state_extractor import extract_player_state_sync
from app.services.world_state_service import (
    apply_player_delta,
    load_or_create_player_state,
)

router = APIRouter(prefix="/campaigns/{campaign_id}/documents", tags=["documents"])


@router.get("", response_model=list[CampaignDocumentOut])
async def list_documents(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[CampaignDocumentOut]:
    await get_campaign_for_user(campaign_id, session, user)
    res = await session.execute(
        select(CampaignDocument)
        .where(CampaignDocument.campaign_id == campaign_id)
        .order_by(CampaignDocument.created_at.desc())
    )
    rows = res.scalars().all()
    return [
        CampaignDocumentOut(
            id=d.id,
            kind=d.kind.value,
            filename=d.filename,
            created_at=d.created_at,
            meta=d.meta,
        )
        for d in rows
    ]


async def _save_and_ingest_manual(
    session: AsyncSession,
    campaign_id: str,
    user: User,
    file: UploadFile,
    *,
    source_tier: str = "manual",
) -> CampaignDocument:
    raw = await file.read()
    if len(raw) < 4 or not raw.startswith(b"%PDF"):
        raise HTTPException(
            400, "No es un PDF reconocible (falta cabecera %PDF o archivo vacío)."
        )
    doc_id = str(uuid.uuid4())
    orig_name = (file.filename or "").strip()
    if not orig_name.lower().endswith(".pdf"):
        orig_name = f"manual-{doc_id[:8]}.pdf"
    safe_name = f"{doc_id}.pdf"
    sub = Path(campaign_id)
    dest_dir = settings.uploads_dir / sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / safe_name
    path.write_bytes(raw)

    source_label = doc_id
    tier = normalize_rule_source_tier(source_tier)
    n_chunks = 0
    rag_error: str | None = None
    try:
        text = extract_text_from_pdf(raw)
        chunks = chunk_text(text)
        if not chunks:
            rag_error = (
                "No se extrajo texto del PDF (¿documento escaneado sin OCR?). "
                "El archivo se guardó; puedes probar otro PDF."
            )
        else:
            rag_service.clear_rules_for_source(campaign_id, source_label)
            n_chunks = rag_service.upsert_rule_chunks(
                campaign_id,
                chunks,
                source_label,
                source_tier=tier,
            )
    except Exception as e:
        rag_error = str(e)[:900]

    meta: dict = {"chunks": n_chunks, "source_tier": tier}
    if rag_error:
        meta["rag_error"] = rag_error

    row = CampaignDocument(
        id=doc_id,
        campaign_id=campaign_id,
        kind=DocKind.manual,
        filename=orig_name,
        storage_path=str(path),
        uploaded_by=user.id,
        meta=meta,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def _save_and_ingest_sheet(
    session: AsyncSession,
    campaign_id: str,
    user: User,
    file: UploadFile,
) -> CampaignDocument:
    raw = await file.read()
    if len(raw) < 4 or not raw.startswith(b"%PDF"):
        raise HTTPException(400, "No es un PDF reconocible.")

    res = await session.execute(
        select(CampaignDocument).where(
            CampaignDocument.campaign_id == campaign_id,
            CampaignDocument.uploaded_by == user.id,
            CampaignDocument.kind == DocKind.character_sheet,
        )
    )
    old = res.scalars().first()
    if old:
        await session.delete(old)

    doc_id = str(uuid.uuid4())
    orig_sheet_name = (file.filename or "").strip()
    if not orig_sheet_name.lower().endswith(".pdf"):
        orig_sheet_name = f"ficha-{doc_id[:8]}.pdf"
    safe_name = f"sheet_{user.id}_{doc_id}.pdf"
    sub = Path(campaign_id)
    dest_dir = settings.uploads_dir / sub
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / safe_name
    path.write_bytes(raw)

    n_chunks = 0
    rag_error: str | None = None
    try:
        text = extract_text_from_pdf(raw)
        chunks = chunk_text(text)
        if not chunks:
            rag_error = "Sin texto extraíble en el PDF de ficha."
        else:
            n_chunks = rag_service.upsert_character_sheet(
                campaign_id,
                user.id,
                chunks,
                display_name=user.display_name,
            )

            # Sprint 6: Persistir “estado operativo” del personaje para validación.
            # Se hace con un extractor controlado (JSON) y se guarda en PlayerState.
            try:
                ps = await asyncio.to_thread(
                    extract_player_state_sync,
                    text,
                    player_name=user.display_name,
                )
                # Asegura fila antes de mergear.
                _ = await load_or_create_player_state(session, campaign_id, user.id)
                await apply_player_delta(
                    session,
                    campaign_id,
                    user.id,
                    ps,
                )
            except Exception:
                # Si extracción falla, no bloqueamos la subida: se marcará por defecto como “incierto”.
                _ = await load_or_create_player_state(session, campaign_id, user.id)
    except Exception as e:
        rag_error = str(e)[:900]

    meta: dict = {"chunks": n_chunks}
    if rag_error:
        meta["rag_error"] = rag_error

    row = CampaignDocument(
        id=doc_id,
        campaign_id=campaign_id,
        kind=DocKind.character_sheet,
        filename=orig_sheet_name,
        storage_path=str(path),
        uploaded_by=user.id,
        meta=meta,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post("/manual")
async def upload_manual(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
    source_tier: Annotated[str, Form()] = "manual",
) -> dict:
    await get_campaign_for_user(campaign_id, session, user)
    doc = await _save_and_ingest_manual(
        session, campaign_id, user, file, source_tier=source_tier
    )
    out: dict = {
        "id": doc.id,
        "kind": "manual",
        "chunks_indexed": doc.meta,
    }
    if doc.meta and doc.meta.get("rag_error"):
        out["warning"] = doc.meta["rag_error"]
    return out


@router.post("/character-sheet")
async def upload_character_sheet(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
) -> dict:
    await get_campaign_for_user(campaign_id, session, user)
    doc = await _save_and_ingest_sheet(session, campaign_id, user, file)
    out: dict = {
        "id": doc.id,
        "kind": "character_sheet",
        "chunks_indexed": doc.meta,
    }
    if doc.meta and doc.meta.get("rag_error"):
        out["warning"] = doc.meta["rag_error"]
    return out
