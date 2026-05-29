import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Swords, Crown, Check, ScrollText, Loader2 } from "lucide-react";
import { apiFetch, apiUrl } from "@/lib/api";
import { getToken, setLastCampaignId } from "@/lib/auth";
import type {
  CampaignOut,
  CampaignMember,
  ChatMessage,
  SessionOpenResponse,
  UserOut,
} from "@/lib/types";
import { RequireAuth } from "@/lib/RequireAuth";

export const Route = createFileRoute("/campaign/$campaignId/lobby")({
  head: () => ({
    meta: [
      { title: "Lobby — AutoDM" },
      { name: "description", content: "Sala de preparación e inicio de campaña." },
    ],
  }),
  component: LobbyPage,
});

function LobbyPage() {
  return (
    <RequireAuth>
      <Lobby />
    </RequireAuth>
  );
}

type SessionOpenStreamEvt =
  | { type: "meta"; timings?: Record<string, unknown> }
  | { type: "chunk"; content?: string }
  | {
      type: "done";
      opening_kind?: string;
      messages?: ChatMessage[];
      timings?: Record<string, unknown>;
    }
  | { type: "error"; detail?: string }
  | { type: "end" };

async function sessionOpenStream(
  campaignId: string,
  onChunk: (delta: string) => void
): Promise<SessionOpenResponse> {
  const tok = getToken();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 180_000);
  try {
    const res = await fetch(apiUrl(`/api/campaigns/${campaignId}/session/open/stream`), {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
      },
      body: JSON.stringify({ activate_campaign: true }),
      signal: controller.signal,
    });
    if (!res.ok) {
      const t = await res.text();
      throw new Error(t || "No se pudo abrir la sesión.");
    }
    if (!res.body) {
      throw new Error("Streaming de apertura no disponible.");
    }

    const decoder = new TextDecoder();
    const reader = res.body.getReader();
    let buffer = "";
    let fullText = "";
    let openingKind = "cold_open";
    let timings: Record<string, unknown> | undefined;

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const line = block
          .split("\n")
          .map((l) => l.trim())
          .find((l) => l.startsWith("data:"));
        if (!line) continue;
        const raw = line.slice(5).trim();
        if (!raw) continue;
        let evt: SessionOpenStreamEvt;
        try {
          evt = JSON.parse(raw) as SessionOpenStreamEvt;
        } catch {
          continue;
        }
        if (evt.type === "chunk") {
          const delta = evt.content ?? "";
          if (delta) {
            fullText += delta;
            onChunk(delta);
          }
          continue;
        }
        if (evt.type === "meta") {
          timings = evt.timings;
          continue;
        }
        if (evt.type === "done") {
          openingKind = evt.opening_kind ?? openingKind;
          timings = evt.timings ?? timings;
          const msgs = Array.isArray(evt.messages) ? evt.messages : [];
          return {
            messages: msgs.length
              ? msgs
              : [
                  {
                    id: `tmp-${Date.now()}`,
                    role: "assistant",
                    content: fullText.trim(),
                    user_id: null,
                    created_at: new Date().toISOString(),
                  },
                ],
            opening_kind: openingKind,
            rag_preview: {},
            timings,
          };
        }
        if (evt.type === "error") {
          throw new Error(evt.detail || "Error en apertura de sesión.");
        }
      }
    }

    if (fullText.trim()) {
      return {
        messages: [
          {
            id: `tmp-${Date.now()}`,
            role: "assistant",
            content: fullText.trim(),
            user_id: null,
            created_at: new Date().toISOString(),
          },
        ],
        opening_kind: openingKind,
        rag_preview: {},
        timings,
      };
    }
    throw new Error("La apertura terminó sin contenido.");
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (msg.toLowerCase().includes("aborted")) {
      throw new Error(
        "La apertura tardó demasiado (timeout 180s). Reintenta o reduce contexto/modelo."
      );
    }
    throw e;
  } finally {
    clearTimeout(timeout);
  }
}

function Lobby() {
  const { campaignId } = Route.useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  useEffect(() => {
    setLastCampaignId(campaignId);
  }, [campaignId]);

  const { data: me } = useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<UserOut>("/api/auth/me"),
  });

  const { data: campaign } = useQuery({
    queryKey: ["campaign", campaignId],
    queryFn: () => apiFetch<CampaignOut>(`/api/campaigns/${campaignId}`),
  });

  const { data: members = [] } = useQuery({
    queryKey: ["members", campaignId],
    queryFn: () => apiFetch<CampaignMember[]>(`/api/campaigns/${campaignId}/members`),
  });

  const { data: messages = [] } = useQuery({
    queryKey: ["messages", campaignId],
    queryFn: () => apiFetch<ChatMessage[]>(`/api/campaigns/${campaignId}/messages`),
    refetchInterval: 15_000,
  });

  const [addonDraft, setAddonDraft] = useState("");
  const [openingPreview, setOpeningPreview] = useState("");
  useEffect(() => {
    setAddonDraft(campaign?.dm_prompt_addon ?? "");
  }, [campaign?.dm_prompt_addon]);

  const saveAddonMut = useMutation({
    mutationFn: () =>
      apiFetch<CampaignOut>(`/api/campaigns/${campaignId}`, {
        method: "PATCH",
        body: JSON.stringify({ dm_prompt_addon: addonDraft.trim() ? addonDraft.trim() : null }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaign", campaignId] });
    },
  });

  const sessionOpenMut = useMutation({
    mutationFn: async () => {
      setOpeningPreview("");
      return sessionOpenStream(campaignId, (delta) => {
        setOpeningPreview((prev) => (prev + delta).slice(-1200));
      });
    },
    onSuccess: () => {
      setOpeningPreview("");
      queryClient.invalidateQueries({ queryKey: ["messages", campaignId] });
      queryClient.invalidateQueries({ queryKey: ["campaign", campaignId] });
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      navigate({ to: "/campaign/$campaignId/play", params: { campaignId } });
    },
    onError: () => setOpeningPreview(""),
  });

  const isOwner = me && campaign && me.id === campaign.owner_id;
  const narrativeLabel =
    campaign?.narrative_mode === "creator_guided"
      ? "Guiada por el creador"
      : campaign?.narrative_mode === "ai_generated"
        ? "IA libre"
        : "Por definir";

  return (
    <div className="px-8 lg:px-12 py-10 max-w-7xl mx-auto">
      <div className="flex items-end justify-between gap-4 mb-8 flex-wrap">
        <div>
          <div className="inline-flex items-center gap-2 text-xs uppercase tracking-widest text-primary mb-2">
            <span className="h-1.5 w-1.5 rounded-full bg-primary animate-pulse" /> Lobby ·{" "}
            {campaign?.status === "lobby" ? "esperando" : "activa"}
          </div>
          <h1 className="font-display text-4xl font-bold">{campaign?.name ?? "…"}</h1>
          <p className="text-muted-foreground">
            {campaign?.game_system ?? "Sistema"} · código{" "}
            <span className="font-mono font-semibold">{campaign?.invite_code}</span>
          </p>
          {sessionOpenMut.isError && (
            <p className="text-sm text-destructive mt-2 max-w-lg">
              {(sessionOpenMut.error as Error)?.message ??
                "No se pudo generar la apertura. Comprueba Ollama o el modelo en el servidor."}
            </p>
          )}
          {sessionOpenMut.isPending && openingPreview.trim() && (
            <div className="mt-3 max-w-2xl rounded-xl border border-border bg-muted/25 p-3">
              <div className="text-[10px] uppercase tracking-widest text-muted-foreground mb-1">
                Generando apertura…
              </div>
              <p className="text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed">
                {openingPreview}
              </p>
            </div>
          )}
        </div>
        <div className="flex flex-col items-end gap-2">
          <div className="flex flex-wrap gap-2 justify-end">
            <Link
              to="/campaign/$campaignId/play"
              params={{ campaignId }}
              className="px-4 py-2.5 rounded-xl glass-strong font-medium text-sm inline-flex items-center gap-2"
            >
              <Swords className="h-4 w-4" /> Ir a la mesa
            </Link>
            {campaign?.status === "lobby" && (
              <button
                type="button"
                disabled={sessionOpenMut.isPending}
                onClick={() => sessionOpenMut.mutate()}
                className="px-5 py-2.5 rounded-xl bg-gradient-ember text-primary-foreground font-medium ember-glow inline-flex items-center gap-2 disabled:opacity-50"
              >
                {sessionOpenMut.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ScrollText className="h-4 w-4" />
                )}{" "}
                {sessionOpenMut.isPending ? "Generando apertura…" : "Iniciar sesión"}
              </button>
            )}
          </div>
          {campaign?.status === "lobby" && (
            <p className="text-[11px] text-muted-foreground max-w-sm text-right leading-snug">
              El DM narrará el contexto o el resumen de la última sesión (según corresponda) y activará la mesa.
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-1 glass rounded-2xl p-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="font-display font-semibold">Jugadores</h2>
            <span className="text-xs text-muted-foreground">{members.length} en mesa</span>
          </div>
          <div className="space-y-2">
            {members.map((p) => {
              const host = p.role === "owner";
              return (
                <div
                  key={p.user_id}
                  className="p-3 rounded-xl border transition bg-muted/30 border-border"
                >
                  <div className="flex items-center gap-3">
                    <div className="h-11 w-11 rounded-full bg-gradient-to-br from-primary to-accent grid place-items-center text-primary-foreground font-display font-bold relative shrink-0">
                      {p.display_name[0]?.toUpperCase()}
                      {host && (
                        <div className="absolute -top-1 -right-1 h-5 w-5 rounded-full bg-gradient-ember grid place-items-center ember-glow">
                          <Crown className="h-3 w-3 text-primary-foreground" />
                        </div>
                      )}
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium truncate">{p.display_name}</div>
                      <div className="text-xs text-muted-foreground truncate capitalize">{p.role}</div>
                    </div>
                    <div className="inline-flex items-center gap-1 text-xs text-success font-medium">
                      <Check className="h-3.5 w-3.5" /> OK
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="mt-5 p-4 rounded-xl bg-muted/30 border border-border">
            <div className="text-xs uppercase tracking-widest text-muted-foreground mb-2">Configuración</div>
            <div className="space-y-2 text-sm">
              <Row label="Modo narrativo" value={narrativeLabel} />
              <Row label="Estado mesa" value={campaign?.status === "active" ? "Activa" : "Lobby"} />
              <Row label="Estilo DM" value={campaign?.narrative_style ? "Definido" : "Por defecto"} />
            </div>
          </div>

          {isOwner && (
            <div className="mt-5 p-4 rounded-xl border border-primary/25 bg-primary/5">
              <div className="text-xs uppercase tracking-widest text-muted-foreground mb-2">
                Notas para el DM (esta mesa)
              </div>
              <p className="text-[11px] text-muted-foreground mb-2 leading-snug">
                Se añaden al prompt del sistema. Tonos veto, trampas del arco, PNJs tabú, etc.
              </p>
              <textarea
                value={addonDraft}
                onChange={(e) => setAddonDraft(e.target.value)}
                rows={5}
                className="w-full text-xs rounded-lg bg-background/80 border border-border px-3 py-2 mb-2 focus:outline-none focus:ring-1 focus:ring-primary resize-y min-h-[100px]"
                placeholder="Ej.: No matar al PNJ «Elías» antes del acto II…"
              />
              <button
                type="button"
                disabled={saveAddonMut.isPending}
                onClick={() => saveAddonMut.mutate()}
                className="text-xs px-3 py-1.5 rounded-lg bg-gradient-ember text-primary-foreground font-medium disabled:opacity-50"
              >
                {saveAddonMut.isPending ? "Guardando…" : "Guardar notas"}
              </button>
            </div>
          )}

          {campaign?.session_summary?.trim() ? (
            <div className="mt-5 p-4 rounded-xl bg-muted/20 border border-border">
              <div className="text-xs uppercase tracking-widest text-muted-foreground mb-2">Memoria comprimida</div>
              <p className="text-xs text-muted-foreground whitespace-pre-wrap max-h-48 overflow-y-auto leading-relaxed">
                {campaign.session_summary}
              </p>
            </div>
          ) : null}
        </div>

        <div className="lg:col-span-2 glass rounded-2xl flex flex-col min-h-[520px]">
          <div className="px-5 py-4 border-b border-border flex items-center justify-between">
            <h2 className="font-display font-semibold">Últimos mensajes en la mesa</h2>
            <span className="text-xs text-muted-foreground">{messages.length} mensajes</span>
          </div>
          <div className="flex-1 overflow-y-auto p-5 space-y-4">
            {messages.map((m) => {
              const isDm = m.role === "assistant";
              const author = isDm
                ? "DM"
                : members.find((x) => x.user_id === m.user_id)?.display_name ?? "Jugador";
              return (
                <div key={m.id} className="flex gap-3">
                  <div className="h-8 w-8 rounded-full bg-gradient-ember grid place-items-center text-primary-foreground text-xs font-bold shrink-0">
                    {author[0]?.toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-muted-foreground mb-0.5">{author}</div>
                    <div className="bg-muted/40 border border-border rounded-2xl rounded-tl-sm px-4 py-2.5 text-sm inline-block max-w-full">
                      {m.content}
                    </div>
                  </div>
                </div>
              );
            })}
            {messages.length === 0 && (
              <p className="text-sm text-muted-foreground text-center py-8">
                Aún no hay mensajes. Entra a la mesa para rolear con el DM y ver el historial aquí.
              </p>
            )}
          </div>
          <div className="p-4 border-t border-border text-xs text-muted-foreground space-y-2">
            <p>
              El chat de juego vive en <strong className="text-foreground">Mesa activa</strong>. Desde el lobby solo ves un
              resumen del canal.
            </p>
            <p className="text-[11px] text-muted-foreground/90 leading-snug">
              En la mesa podéis usar{" "}
              <kbd className="px-1 py-px rounded bg-muted/80 border border-border font-mono text-[10px]">[[Nombre]]</kbd>{" "}
              o <kbd className="px-1 py-px rounded bg-muted/80 border border-border font-mono text-[10px]">#Nombre</kbd>{" "}
              en los mensajes para enlazar entidades al mapa del mundo (GraphRAG).
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-muted-foreground text-xs">{label}</span>
      <span className="text-sm font-medium text-right truncate max-w-[60%]">{value}</span>
    </div>
  );
}
