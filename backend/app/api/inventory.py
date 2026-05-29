from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import CampaignMember, PlayerInventoryItem, User
from app.db.session import get_session
from app.deps import get_campaign_for_user, get_current_user
from app.schemas import (
    InventoryItemCreate,
    InventoryItemOut,
    InventoryItemPatch,
    PartyInventoryOut,
)
from app.services.chat_broadcast import notify_inventory_updated

router = APIRouter(prefix="/campaigns/{campaign_id}", tags=["inventory"])


@router.get("/inventory/me", response_model=list[InventoryItemOut])
async def list_my_inventory(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[InventoryItemOut]:
    await get_campaign_for_user(campaign_id, session, user)
    res = await session.execute(
        select(PlayerInventoryItem)
        .where(
            PlayerInventoryItem.campaign_id == campaign_id,
            PlayerInventoryItem.user_id == user.id,
        )
        .order_by(PlayerInventoryItem.sort_order, PlayerInventoryItem.name)
    )
    rows = res.scalars().all()
    return [InventoryItemOut.model_validate(r) for r in rows]


@router.get("/inventory/party", response_model=list[PartyInventoryOut])
async def list_party_inventory(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> list[PartyInventoryOut]:
    await get_campaign_for_user(campaign_id, session, user)
    r_mem = await session.execute(
        select(User, CampaignMember)
        .join(CampaignMember, CampaignMember.user_id == User.id)
        .where(CampaignMember.campaign_id == campaign_id)
        .order_by(User.display_name.asc())
    )
    pairs = r_mem.all()
    out: list[PartyInventoryOut] = []
    for u, _m in pairs:
        q = await session.execute(
            select(PlayerInventoryItem)
            .where(
                PlayerInventoryItem.campaign_id == campaign_id,
                PlayerInventoryItem.user_id == u.id,
                PlayerInventoryItem.quantity >= 1,
            )
            .order_by(PlayerInventoryItem.sort_order, PlayerInventoryItem.name)
        )
        items = [InventoryItemOut.model_validate(r) for r in q.scalars().all()]
        out.append(
            PartyInventoryOut(
                user_id=u.id,
                display_name=u.display_name,
                items=items,
            )
        )
    return out


@router.post(
    "/inventory/me",
    response_model=InventoryItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_my_item(
    campaign_id: str,
    body: InventoryItemCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> PlayerInventoryItem:
    await get_campaign_for_user(campaign_id, session, user)
    row = PlayerInventoryItem(
        campaign_id=campaign_id,
        user_id=user.id,
        name=body.name.strip(),
        quantity=body.quantity,
        category=body.category.strip() if body.category else None,
        notes=body.notes,
        sort_order=body.sort_order,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    notify_inventory_updated(campaign_id)
    return row


@router.patch("/inventory/me/{item_id}", response_model=InventoryItemOut)
async def patch_my_item(
    campaign_id: str,
    item_id: str,
    body: InventoryItemPatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> PlayerInventoryItem:
    await get_campaign_for_user(campaign_id, session, user)
    res = await session.execute(
        select(PlayerInventoryItem).where(
            PlayerInventoryItem.id == item_id,
            PlayerInventoryItem.campaign_id == campaign_id,
            PlayerInventoryItem.user_id == user.id,
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Objeto no encontrado")
    if body.name is not None:
        row.name = body.name.strip()
    if body.quantity is not None:
        row.quantity = body.quantity
    if body.category is not None:
        row.category = body.category.strip() if body.category else None
    if body.notes is not None:
        row.notes = body.notes
    if body.sort_order is not None:
        row.sort_order = body.sort_order
    await session.commit()
    await session.refresh(row)
    notify_inventory_updated(campaign_id)
    return row


@router.delete("/inventory/me/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_item(
    campaign_id: str,
    item_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> None:
    await get_campaign_for_user(campaign_id, session, user)
    res = await session.execute(
        select(PlayerInventoryItem).where(
            PlayerInventoryItem.id == item_id,
            PlayerInventoryItem.campaign_id == campaign_id,
            PlayerInventoryItem.user_id == user.id,
        )
    )
    row = res.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Objeto no encontrado")
    await session.delete(row)
    await session.commit()
    notify_inventory_updated(campaign_id)
