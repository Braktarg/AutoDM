import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class MemberRole(str, enum.Enum):
    owner = "owner"
    player = "player"


class DocKind(str, enum.Enum):
    manual = "manual"
    character_sheet = "character_sheet"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    campaigns_owned: Mapped[list["Campaign"]] = relationship(
        back_populates="owner", foreign_keys="Campaign.owner_id"
    )
    memberships: Mapped[list["CampaignMember"]] = relationship(back_populates="user")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(200))
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    owner_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    game_system: Mapped[str | None] = mapped_column(String(80), nullable=True)
    narrative_mode: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # ai_generated | creator_guided
    status: Mapped[str] = mapped_column(
        String(32), default="lobby"
    )  # lobby | active
    narrative_style: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Nombre de archivo bajo uploads/{campaign_id}/ (ej. cover.jpg)
    cover_image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Sprint 2: memoria de sesión comprimida (LLM) y notas libres del director.
    session_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    dm_prompt_addon: Mapped[str | None] = mapped_column(Text, nullable=True)

    owner: Mapped["User"] = relationship(
        foreign_keys=[owner_id], back_populates="campaigns_owned"
    )
    members: Mapped[list["CampaignMember"]] = relationship(back_populates="campaign")
    documents: Mapped[list["CampaignDocument"]] = relationship(back_populates="campaign")
    messages: Mapped[list["ChatMessage"]] = relationship(back_populates="campaign")


class CampaignMember(Base):
    __tablename__ = "campaign_members"
    __table_args__ = (UniqueConstraint("campaign_id", "user_id", name="uq_member"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    role: Mapped[MemberRole] = mapped_column(Enum(MemberRole), default=MemberRole.player)

    campaign: Mapped["Campaign"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship(back_populates="memberships")


class CampaignDocument(Base):
    __tablename__ = "campaign_documents"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"))
    kind: Mapped[DocKind] = mapped_column(Enum(DocKind))
    filename: Mapped[str] = mapped_column(String(512))
    storage_path: Mapped[str] = mapped_column(String(1024))
    uploaded_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    meta: Mapped[dict | None] = mapped_column(SQLiteJSON, nullable=True)

    campaign: Mapped["Campaign"] = relationship(back_populates="documents")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"))
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    campaign: Mapped["Campaign"] = relationship(back_populates="messages")


class NarrativeEvent(Base):
    __tablename__ = "narrative_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text)
    actors: Mapped[list[str]] = mapped_column(JSON, default=list)
    targets: Mapped[list[str]] = mapped_column(JSON, default=list)
    location: Mapped[str | None] = mapped_column(String(180), nullable=True)
    severity: Mapped[int] = mapped_column(default=1)
    payload: Mapped[dict | None] = mapped_column(SQLiteJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerInventoryItem(Base):
    """Objetos llevados por un jugador en una campaña (visible para todo el grupo y el DM)."""

    __tablename__ = "player_inventory_items"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    campaign_id: Mapped[str] = mapped_column(String(36), ForeignKey("campaigns.id"), index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int] = mapped_column(default=1)
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorldState(Base):
    """
    Estado operativo persistente por campaña.
    Fuente única de validación para el motor (v1 simulador jugable).
    """

    __tablename__ = "world_states"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id"), unique=True, index=True
    )
    state: Mapped[dict | None] = mapped_column(SQLiteJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PlayerState(Base):
    """Estado operativo de un jugador en una campaña (extraído de ficha + evolución)."""

    __tablename__ = "player_states"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    state: Mapped[dict | None] = mapped_column(SQLiteJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ActionRequest(Base):
    """Intención interpretada por el motor (pre-LLM narrativo)."""

    __tablename__ = "action_requests"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), index=True
    )
    player_text: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict | None] = mapped_column(SQLiteJSON, nullable=True)
    validation: Mapped[dict | None] = mapped_column(SQLiteJSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ActionEffects(Base):
    """Efectos validados/aplicados del turno (la narrativa debe derivar de esto)."""

    __tablename__ = "action_effects"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id"), index=True
    )
    action_request_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("action_requests.id"), unique=True, index=True
    )
    effects: Mapped[dict | None] = mapped_column(SQLiteJSON, nullable=True)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
