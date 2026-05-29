from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    display_name: str = Field(min_length=1, max_length=120)


class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    narrative_style: str | None = None
    description: str | None = None
    game_system: str | None = Field(default=None, max_length=80)
    narrative_mode: str | None = Field(
        default=None,
        description="ai_generated | creator_guided",
    )


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    narrative_style: str | None = None
    description: str | None = None
    game_system: str | None = Field(default=None, max_length=80)
    narrative_mode: str | None = None
    status: str | None = Field(default=None, description="lobby | active")
    dm_prompt_addon: str | None = Field(
        default=None,
        description="Notas extra del director inyectadas en el system prompt de esta mesa.",
    )


class CampaignOut(BaseModel):
    id: str
    name: str
    invite_code: str
    owner_id: str
    narrative_style: str | None
    description: str | None = None
    game_system: str | None = None
    narrative_mode: str | None = None
    status: str = "lobby"
    created_at: datetime
    cover_image: str | None = None
    session_summary: str | None = None
    dm_prompt_addon: str | None = None

    model_config = {"from_attributes": True}


class CampaignSummaryOut(CampaignOut):
    member_count: int = 0
    last_narrative_preview: str | None = None


class CampaignMemberOut(BaseModel):
    user_id: str
    display_name: str
    role: str

    model_config = {"from_attributes": True}


class CampaignDocumentOut(BaseModel):
    id: str
    kind: str
    filename: str
    created_at: datetime
    meta: dict | None = None

    model_config = {"from_attributes": True}


class JoinCampaignBody(BaseModel):
    invite_code: str = Field(min_length=4, max_length=32)


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    user_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PlayTurnRequest(BaseModel):
    message: str = Field(min_length=1)


class PlayTurnResponse(BaseModel):
    reply: str
    rag_preview: dict
    timings: dict | None = None


class SessionOpenRequest(BaseModel):
    """Abre la sesión en mesa: narración inicial o recap según exista memoria comprimida."""

    activate_campaign: bool = Field(
        default=True,
        description="Si la mesa está en lobby, pasarla a active tras generar la apertura.",
    )


class SessionOpenResponse(BaseModel):
    messages: list[ChatMessageOut]
    opening_kind: str
    rag_preview: dict
    timings: dict | None = None


class NarrativeEventOut(BaseModel):
    id: str
    campaign_id: str
    event_type: str
    summary: str
    actors: list[str]
    targets: list[str]
    location: str | None = None
    severity: int
    payload: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class InventoryItemCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=999_999)
    category: str | None = Field(default=None, max_length=80)
    notes: str | None = None
    sort_order: int = Field(default=0, ge=-10_000, le=10_000)


class InventoryItemPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    quantity: int | None = Field(default=None, ge=0, le=999_999)
    category: str | None = Field(default=None, max_length=80)
    notes: str | None = None
    sort_order: int | None = Field(default=None, ge=-10_000, le=10_000)


class InventoryItemOut(BaseModel):
    id: str
    campaign_id: str
    user_id: str
    name: str
    quantity: int
    category: str | None = None
    notes: str | None = None
    sort_order: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PartyInventoryOut(BaseModel):
    user_id: str
    display_name: str
    items: list[InventoryItemOut]
