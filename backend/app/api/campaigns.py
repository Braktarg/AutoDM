from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Campaign,
    CampaignDocument,
    CampaignMember,
    ChatMessage,
    MemberRole,
    User,
)
from app.db.session import get_session
from app.deps import get_campaign_for_user, get_current_user
from app.schemas import (
    CampaignCreate,
    CampaignMemberOut,
    CampaignOut,
    CampaignSummaryOut,
    CampaignUpdate,
    JoinCampaignBody,
)
from app.services.campaign_purge import purge_campaign_side_effects
from app.utils.invite import generate_invite_code

MAX_COVER_BYTES = 4 * 1024 * 1024


def _image_ext_from_magic(head: bytes) -> str | None:
    if len(head) >= 3 and head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if len(head) >= 4 and head[:4] == b"\x89PNG":
        return "png"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


async def _campaign_summary(
    session: AsyncSession, c: Campaign
) -> CampaignSummaryOut:
    cnt_q = await session.execute(
        select(func.count(CampaignMember.id)).where(
            CampaignMember.campaign_id == c.id
        )
    )
    member_count = int(cnt_q.scalar_one() or 0)
    last_q = await session.execute(
        select(ChatMessage.content)
        .where(
            ChatMessage.campaign_id == c.id,
            ChatMessage.role == "assistant",
        )
        .order_by(desc(ChatMessage.created_at))
        .limit(1)
    )
    last_row = last_q.scalar_one_or_none()
    preview = None
    if last_row:
        preview = last_row[:160] + ("…" if len(last_row) > 160 else "")
    base = CampaignOut.model_validate(c).model_dump()
    return CampaignSummaryOut(
        **base,
        member_count=member_count,
        last_narrative_preview=preview,
    )


@router.post("", response_model=CampaignOut)
async def create_campaign(
    body: CampaignCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> CampaignOut:
    code = generate_invite_code()
    c = Campaign(
        name=body.name,
        invite_code=code,
        owner_id=user.id,
        narrative_style=body.narrative_style,
        description=body.description,
        game_system=body.game_system,
        narrative_mode=body.narrative_mode,
        status="lobby",
    )
    session.add(c)
    await session.flush()
    session.add(
        CampaignMember(
            campaign_id=c.id,
            user_id=user.id,
            role=MemberRole.owner,
        )
    )
    await session.commit()
    await session.refresh(c)
    return CampaignOut.model_validate(c)


@router.get("", response_model=list[CampaignSummaryOut])
async def list_my_campaigns(
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[CampaignSummaryOut]:
    res = await session.execute(
        select(Campaign)
        .join(CampaignMember)
        .where(CampaignMember.user_id == user.id)
        .order_by(Campaign.created_at.desc())
    )
    rows = res.scalars().all()
    out: list[CampaignSummaryOut] = []
    for c in rows:
        out.append(await _campaign_summary(session, c))
    return out


@router.post("/join", response_model=CampaignOut)
async def join_campaign(
    body: JoinCampaignBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> CampaignOut:
    res = await session.execute(
        select(Campaign).where(Campaign.invite_code == body.invite_code.strip().upper())
    )
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Código de invitación inválido")
    exists = await session.execute(
        select(CampaignMember).where(
            CampaignMember.campaign_id == c.id,
            CampaignMember.user_id == user.id,
        )
    )
    if exists.scalar_one_or_none():
        return CampaignOut.model_validate(c)
    session.add(
        CampaignMember(
            campaign_id=c.id,
            user_id=user.id,
            role=MemberRole.player,
        )
    )
    await session.commit()
    await session.refresh(c)
    return CampaignOut.model_validate(c)


@router.get("/{campaign_id}/cover")
async def get_campaign_cover(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> FileResponse:
    c = await get_campaign_for_user(campaign_id, session, user)
    if not c.cover_image:
        raise HTTPException(status_code=404, detail="Esta mesa no tiene portada")
    path = settings.uploads_dir / campaign_id / c.cover_image
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Archivo de portada no encontrado")
    return FileResponse(
        path,
        filename=c.cover_image,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/{campaign_id}/cover")
async def upload_campaign_cover(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
    file: UploadFile = File(...),
) -> dict[str, str]:
    c = await get_campaign_for_user(campaign_id, session, user)
    if c.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el dueño puede cambiar la portada",
        )
    raw = await file.read()
    if len(raw) > MAX_COVER_BYTES:
        raise HTTPException(status_code=400, detail="La imagen supera 4 MB")
    ext = _image_ext_from_magic(raw[:64])
    if not ext:
        raise HTTPException(
            status_code=400,
            detail="Formato no admitido (usa JPEG, PNG o WebP)",
        )
    dest_dir = settings.uploads_dir / campaign_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    fname = f"cover.{ext}"
    if c.cover_image and c.cover_image != fname:
        old = dest_dir / c.cover_image
        if old.is_file():
            old.unlink()
    (dest_dir / fname).write_bytes(raw)
    c.cover_image = fname
    await session.commit()
    await session.refresh(c)
    return {"cover_image": fname}


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> CampaignOut:
    c = await get_campaign_for_user(campaign_id, session, user)
    return CampaignOut.model_validate(c)


@router.patch("/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: str,
    body: CampaignUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> CampaignOut:
    c = await get_campaign_for_user(campaign_id, session, user)
    if c.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Solo el dueño puede editar la mesa")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(c, k, v)
    await session.commit()
    await session.refresh(c)
    return CampaignOut.model_validate(c)


@router.get("/{campaign_id}/members", response_model=list[CampaignMemberOut])
async def list_members(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[CampaignMemberOut]:
    await get_campaign_for_user(campaign_id, session, user)
    res = await session.execute(
        select(CampaignMember, User)
        .join(User, User.id == CampaignMember.user_id)
        .where(CampaignMember.campaign_id == campaign_id)
        .order_by(CampaignMember.role.desc())
    )
    out: list[CampaignMemberOut] = []
    for m, u in res.all():
        out.append(
            CampaignMemberOut(
                user_id=u.id,
                display_name=u.display_name,
                role=m.role.value,
            )
        )
    return out


@router.delete("/{campaign_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_campaign(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> Response:
    c = await get_campaign_for_user(campaign_id, session, user)
    if c.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el dueño puede eliminar la mesa",
        )
    await session.execute(
        delete(ChatMessage).where(ChatMessage.campaign_id == campaign_id)
    )
    await session.execute(
        delete(CampaignDocument).where(CampaignDocument.campaign_id == campaign_id)
    )
    await session.execute(
        delete(CampaignMember).where(CampaignMember.campaign_id == campaign_id)
    )
    await session.execute(delete(Campaign).where(Campaign.id == campaign_id))
    await session.commit()
    purge_campaign_side_effects(campaign_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
