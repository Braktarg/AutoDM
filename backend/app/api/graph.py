"""
Endpoints para inspeccionar y extender el GraphRAG de una mesa (aislado).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.session import get_session
from app.deps import get_campaign_for_user, get_current_user
from app.services.graph_service import export_world_summary, get_graph_backend

router = APIRouter(prefix="/campaigns/{campaign_id}/graph", tags=["graph"])


@router.get("/world")
async def world_summary(
    campaign_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    await get_campaign_for_user(campaign_id, session, user)
    return export_world_summary(campaign_id)


class GraphUpsertNode(BaseModel):
    label: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=256)
    properties: dict[str, Any] | None = None


class GraphRelate(BaseModel):
    from_label: str
    from_name: str
    rel_type: str
    to_label: str
    to_name: str
    properties: dict[str, Any] | None = None


@router.post("/nodes")
async def upsert_node(
    campaign_id: str,
    body: GraphUpsertNode,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    await get_campaign_for_user(campaign_id, session, user)
    g = get_graph_backend(campaign_id)
    nid = g.upsert_node(body.label, body.name, body.properties)
    return {"id": nid}


@router.post("/edges")
async def relate(
    campaign_id: str,
    body: GraphRelate,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, bool]:
    await get_campaign_for_user(campaign_id, session, user)
    g = get_graph_backend(campaign_id)
    g.relate(
        body.from_label,
        body.from_name,
        body.rel_type,
        body.to_label,
        body.to_name,
        body.properties,
    )
    return {"ok": True}


@router.get("/context")
async def preview_context(
    campaign_id: str,
    q: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    await get_campaign_for_user(campaign_id, session, user)
    g = get_graph_backend(campaign_id)
    topics = [t for t in q.replace(",", " ").split() if t]
    text = g.query_text_context(topics=topics[:20], limit=40)
    return {"context": text}
