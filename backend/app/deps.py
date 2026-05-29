from typing import Annotated

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Campaign, CampaignMember, User
from app.db.session import async_session_factory, get_session
from app.security import decode_token

security = HTTPBearer(auto_error=False)


async def get_current_user(
    session: Annotated[AsyncSession, Depends(get_session)],
    creds: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(security),
    ],
) -> User:
    if creds is None or not creds.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado",
        )
    user_id = decode_token(creds.credentials)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado",
        )
    return user


async def get_current_user_sse(
    session: Annotated[AsyncSession, Depends(get_session)],
    token: Annotated[str | None, Query()] = None,
    creds: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(security),
    ] = None,
) -> User:
    """
    Igual que get_current_user, pero acepta JWT en query `token` para EventSource
    (los navegadores no envían Authorization en SSE).
    Nota: no uses este dependency en endpoints SSE de larga duración: mantiene la sesión
    SQL abierta. Usa `require_sse_campaign_member` en su lugar.
    """
    raw = (token.strip() if token else None) or (
        creds.credentials if creds and creds.credentials else None
    )
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado",
        )
    user_id = decode_token(raw)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )
    res = await session.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario no encontrado",
        )
    return user


async def require_sse_campaign_member(
    campaign_id: str,
    token: Annotated[str | None, Query()] = None,
    creds: Annotated[
        HTTPAuthorizationCredentials | None,
        Depends(security),
    ] = None,
) -> User:
    """
    Autenticación para SSE/EventSource (`?token=jwt`): abre y cierra una sesión SQL
    sólo durante la validación para no ocupar una conexión del pool durante el stream.
    """
    raw = (token.strip() if token else None) or (
        creds.credentials if creds and creds.credentials else None
    )
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado",
        )
    user_id = decode_token(raw)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
        )
    async with async_session_factory() as session:
        res = await session.execute(select(User).where(User.id == user_id))
        user = res.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Usuario no encontrado",
            )
        await get_campaign_for_user(campaign_id, session, user)
    return user


async def get_campaign_member(
    campaign_id: str,
    session: AsyncSession,
    user: User,
) -> CampaignMember:
    res = await session.execute(
        select(CampaignMember).where(
            CampaignMember.campaign_id == campaign_id,
            CampaignMember.user_id == user.id,
        )
    )
    m = res.scalar_one_or_none()
    if not m:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No perteneces a esta mesa",
        )
    return m


async def get_campaign_for_user(
    campaign_id: str,
    session: AsyncSession,
    user: User,
) -> Campaign:
    await get_campaign_member(campaign_id, session, user)
    res = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
    c = res.scalar_one_or_none()
    if not c:
        raise HTTPException(status_code=404, detail="Mesa no encontrada")
    return c
