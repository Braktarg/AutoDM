export type UserOut = {
  id: string;
  email: string;
  display_name: string;
  created_at: string;
};

export type CampaignOut = {
  id: string;
  name: string;
  invite_code: string;
  owner_id: string;
  narrative_style: string | null;
  description: string | null;
  game_system: string | null;
  narrative_mode: string | null;
  status: string;
  created_at: string;
  cover_image: string | null;
  session_summary: string | null;
  dm_prompt_addon: string | null;
};

export type CampaignSummary = CampaignOut & {
  member_count: number;
  last_narrative_preview: string | null;
};

export type CampaignMember = {
  user_id: string;
  display_name: string;
  role: string;
};

export type ChatMessage = {
  id: string;
  role: string;
  content: string;
  user_id: string | null;
  created_at: string;
};

export type CampaignDocument = {
  id: string;
  kind: string;
  filename: string;
  created_at: string;
  meta: {
    chunks?: number;
    rag_error?: string;
    source_tier?: string;
  } | null;
};

export type WorldSummary = {
  nodes_by_label: Record<string, { name: string; properties: Record<string, unknown> }[]>;
  edge_count: number;
  recent_events: { name: string; properties: Record<string, unknown> }[];
};

export type SessionOpenResponse = {
  messages: ChatMessage[];
  opening_kind: string;
  rag_preview: Record<string, unknown>;
  timings?: Record<string, unknown>;
};

export type NarrativeEvent = {
  id: string;
  campaign_id: string;
  event_type: string;
  summary: string;
  actors: string[];
  targets: string[];
  location: string | null;
  severity: number;
  payload: Record<string, unknown> | null;
  created_at: string;
};

export type InventoryItem = {
  id: string;
  campaign_id: string;
  user_id: string;
  name: string;
  quantity: number;
  category: string | null;
  notes: string | null;
  sort_order: number;
  created_at: string;
  updated_at: string;
};

export type PartyInventoryRow = {
  user_id: string;
  display_name: string;
  items: InventoryItem[];
};
