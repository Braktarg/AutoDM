import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Send,
  Dices,
  Sword,
  Eye,
  MessageSquare,
  Backpack,
  Map,
  Users2,
  Sparkles,
  ChevronRight,
  ChevronDown,
  Flame,
  Hash,
} from "lucide-react";
import { apiFetch, apiUrl } from "@/lib/api";
import { getToken, setLastCampaignId } from "@/lib/auth";
import type {
  CampaignOut,
  ChatMessage,
  CampaignMember,
  NarrativeEvent,
  PartyInventoryRow,
  WorldSummary,
} from "@/lib/types";
import { RequireAuth } from "@/lib/RequireAuth";

export const Route = createFileRoute("/campaign/$campaignId/play")({
  head: () => ({
    meta: [
      { title: "Mesa — AutoDM" },
      { name: "description", content: "Chat con el DM IA y estado del mundo." },
    ],
  }),
  component: PlayPage,
});

type PlayRes = {
  reply: string;
  rag_preview: Record<string, unknown>;
  timings?: Record<string, unknown>;
};

type StreamEvt =
  | {
      type: "meta";
      rag_preview?: Record<string, unknown>;
      pace?: string;
      timings?: Record<string, unknown>;
    }
  | { type: "chunk"; content?: string }
  | { type: "done"; reply?: string; timings?: Record<string, unknown> }
  | { type: "error"; detail?: string }
  | { type: "end" };

function rollDie(sides: number): number {
  return Math.floor(Math.random() * sides) + 1;
}

/** Notación tipo 1d20, 2d6+3, d8 (cuenta 1). */
function parseAndRollDice(expr: string): { text: string; total: number } | null {
  const s = expr.trim().toLowerCase().replace(/\s/g, "");
  const m = s.match(/^(\d*)d(\d+)([+-]\d+)?$/);
  if (!m) return null;
  let count = parseInt(m[1] || "1", 10);
  if (!Number.isFinite(count) || count < 1) count = 1;
  count = Math.min(count, 24);
  const sides = parseInt(m[2], 10);
  if (!Number.isFinite(sides) || sides < 2) return null;
  const cappedSides = Math.min(sides, 100);
  const mod = m[3] ? parseInt(m[3], 10) : 0;
  const rolls: number[] = [];
  for (let i = 0; i < count; i++) rolls.push(rollDie(cappedSides));
  const sumDice = rolls.reduce((a, b) => a + b, 0);
  const total = sumDice + mod;
  const modStr =
    mod === 0 ? "" : mod > 0 ? ` + ${mod}` : ` − ${Math.abs(mod)}`;
  const notation = `${count}d${cappedSides}${m[3] ?? ""}`;
  const text = `[Tirada ${notation}] dados [${rolls.join(", ")}]${modStr} → **total ${total}**`;
  return { text, total };
}

function mergeQuickSnippet(prev: string, snippet: string): string {
  const a = prev.trimEnd();
  const b = snippet.trim();
  if (!a) return b;
  return `${a}\n\n${b}`;
}

function summarizeTimings(timings?: Record<string, unknown>): string | null {
  if (!timings) return null;
  const sec = (k: string) => {
    const v = timings[k];
    if (typeof v !== "number" || Number.isNaN(v)) return null;
    return (v / 1000).toFixed(v >= 10_000 ? 1 : 2);
  };
  const parts: string[] = [];
  const p = sec("prepare_ms");
  const l = sec("llm_ms");
  const d = sec("persist_ms");
  const t = sec("total_ms");
  if (p !== null) parts.push(`contexto ${p}s`);
  if (l !== null) parts.push(`modelo ${l}s`);
  if (d !== null) parts.push(`guardado ${d}s`);
  if (t !== null) parts.push(`total ${t}s`);
  if (typeof timings.consistency_score === "number") {
    parts.push(`consistencia ${Math.round(timings.consistency_score)}/100`);
  }
  const pace = typeof timings.pace === "string" ? timings.pace : "";
  const head = pace && pace !== "custom" ? `${pace}: ` : "";
  if (parts.length === 0 && !head) return null;
  return head + parts.join(" · ");
}

async function streamPlayTurn(
  campaignId: string,
  message: string,
  onChunk: (delta: string) => void
): Promise<PlayRes> {
  const tok = getToken();
  const res = await fetch(apiUrl(`/api/campaigns/${campaignId}/play/stream`), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
    },
    body: JSON.stringify({ message }),
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(t || "No se pudo iniciar streaming");
  }
  if (!res.body) {
    throw new Error("Streaming no disponible en este navegador.");
  }

  const decoder = new TextDecoder();
  const reader = res.body.getReader();
  let buffer = "";
  let reply = "";
  let ragPreview: Record<string, unknown> = {};
  let timings: Record<string, unknown> | undefined;

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const block of chunks) {
      const line = block
        .split("\n")
        .map((l) => l.trim())
        .find((l) => l.startsWith("data:"));
      if (!line) continue;
      const raw = line.slice(5).trim();
      if (!raw) continue;
      let evt: StreamEvt;
      try {
        evt = JSON.parse(raw) as StreamEvt;
      } catch {
        continue;
      }
      if (evt.type === "meta") {
        ragPreview = evt.rag_preview ?? {};
        timings = evt.timings;
        continue;
      }
      if (evt.type === "chunk") {
        const delta = evt.content ?? "";
        if (delta) {
          reply += delta;
          onChunk(delta);
        }
        continue;
      }
      if (evt.type === "done") {
        return {
          reply: evt.reply ?? reply,
          rag_preview: ragPreview,
          timings: evt.timings ?? timings,
        };
      }
      if (evt.type === "error") {
        throw new Error(evt.detail || "Error durante streaming.");
      }
      if (evt.type === "end") {
        return { reply, rag_preview: ragPreview, timings };
      }
    }
  }

  return { reply, rag_preview: ragPreview, timings };
}

function narrativeEventsUrl(
  campaignId: string,
  opts: { eventType: string; minSeverity: number; limit?: number }
): string {
  const limit = opts.limit ?? 80;
  const p = new URLSearchParams();
  p.set("limit", String(limit));
  if (opts.eventType.trim()) {
    p.set("event_type", opts.eventType.trim());
  }
  if (opts.minSeverity >= 1) {
    p.set("min_severity", String(opts.minSeverity));
  }
  return `/api/campaigns/${campaignId}/events?${p.toString()}`;
}

function formatEventTime(iso: string): string {
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString("es", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function NarrativeEventCard({
  e,
  expanded,
  onToggle,
}: {
  e: NarrativeEvent;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/15 overflow-hidden text-left w-full">
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex gap-2 items-start text-xs text-muted-foreground py-2 px-2 hover:bg-muted/30 transition text-left"
      >
        <span className="shrink-0 mt-0.5 text-primary">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" aria-hidden />
          )}
        </span>
        <span className="flex-1 min-w-0">
          <span className="text-[10px] uppercase tracking-wide text-primary/90 block">
            {e.event_type} · sev {e.severity} · {formatEventTime(e.created_at)}
          </span>
          <span className="leading-snug block mt-0.5 line-clamp-3">{e.summary}</span>
        </span>
      </button>
      {expanded && (
        <div className="px-3 pb-3 pt-0 space-y-2 border-t border-border/40 text-[11px] text-muted-foreground">
          <div>
            <span className="text-[9px] uppercase tracking-wider text-muted-foreground/80">ID</span>
            <div className="font-mono text-[10px] break-all">{e.id}</div>
          </div>
          {e.location && (
            <div>
              <span className="text-[9px] uppercase tracking-wider text-muted-foreground/80">Lugar</span>
              <div>{e.location}</div>
            </div>
          )}
          <div>
            <span className="text-[9px] uppercase tracking-wider text-muted-foreground/80">Actores</span>
            <div className="flex flex-wrap gap-1 mt-0.5">
              {(e.actors?.length ? e.actors : ["(ninguno)"]).map((a) => (
                <span
                  key={a}
                  className="px-1.5 py-px rounded bg-muted/80 border border-border/50 text-[10px]"
                >
                  {a}
                </span>
              ))}
            </div>
          </div>
          <div>
            <span className="text-[9px] uppercase tracking-wider text-muted-foreground/80">Objetivos</span>
            <div className="flex flex-wrap gap-1 mt-0.5">
              {(e.targets?.length ? e.targets : ["(ninguno)"]).map((t) => (
                <span
                  key={t}
                  className="px-1.5 py-px rounded bg-muted/80 border border-border/50 text-[10px]"
                >
                  {t}
                </span>
              ))}
            </div>
          </div>
          {e.payload && Object.keys(e.payload).length > 0 && (
            <div>
              <span className="text-[9px] uppercase tracking-wider text-muted-foreground/80">Payload</span>
              <pre className="mt-1 max-h-32 overflow-auto rounded border border-border/50 bg-background/50 p-2 text-[10px] leading-relaxed whitespace-pre-wrap font-mono">
                {JSON.stringify(e.payload, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function PlayPage() {
  return (
    <RequireAuth>
      <PlayScreen />
    </RequireAuth>
  );
}

type RagTraceRow = {
  source?: string;
  source_tier?: string;
  preview?: string;
  rerank_score?: number | null;
};

function parseRagTraces(preview: Record<string, unknown>): {
  rules: RagTraceRow[];
  sheets: RagTraceRow[];
} {
  const rules = Array.isArray(preview.rules_trace)
    ? (preview.rules_trace as RagTraceRow[])
    : [];
  const sheets = Array.isArray(preview.sheets_trace)
    ? (preview.sheets_trace as RagTraceRow[])
    : [];
  return { rules, sheets };
}

function PlayScreen() {
  const { campaignId } = Route.useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [input, setInput] = useState("");
  const [streamingReply, setStreamingReply] = useState("");
  const [lastLatency, setLastLatency] = useState<string | null>(null);
  const [lastActionBlocked, setLastActionBlocked] = useState(false);
  const [playError, setPlayError] = useState<string | null>(null);
  const [lastRagPreview, setLastRagPreview] = useState<Record<string, unknown> | null>(
    null
  );
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [minSeverityFilter, setMinSeverityFilter] = useState(0);
  const [expandedEventId, setExpandedEventId] = useState<string | null>(null);

  useEffect(() => {
    setLastCampaignId(campaignId);
  }, [campaignId]);

  useEffect(() => {
    const tok = getToken();
    if (!tok) return;
    const url = `${apiUrl(`/api/campaigns/${campaignId}/messages/events`)}?token=${encodeURIComponent(tok)}`;
    const es = new EventSource(url);
    es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data) as { type?: string };
        if (d.type === "messages_updated") {
          queryClient.invalidateQueries({ queryKey: ["messages", campaignId] });
          queryClient.invalidateQueries({ queryKey: ["campaign", campaignId] });
          queryClient.invalidateQueries({ queryKey: ["narrative-events", campaignId] });
          queryClient.invalidateQueries({ queryKey: ["narrative-event-types", campaignId] });
        }
        if (d.type === "inventory_updated") {
          queryClient.invalidateQueries({ queryKey: ["party-inventory", campaignId] });
          queryClient.invalidateQueries({ queryKey: ["inventory-me", campaignId] });
        }
      } catch {
        /* mensaje SSE no JSON */
      }
    };
    return () => es.close();
  }, [campaignId, queryClient]);

  const { data: campaign } = useQuery({
    queryKey: ["campaign", campaignId],
    queryFn: () => apiFetch<CampaignOut>(`/api/campaigns/${campaignId}`),
  });

  const { data: members = [] } = useQuery({
    queryKey: ["members", campaignId],
    queryFn: () => apiFetch<CampaignMember[]>(`/api/campaigns/${campaignId}/members`),
  });

  const { data: world } = useQuery({
    queryKey: ["world", campaignId],
    queryFn: () => apiFetch<WorldSummary>(`/api/campaigns/${campaignId}/graph/world`),
  });

  const { data: messages = [] } = useQuery({
    queryKey: ["messages", campaignId],
    queryFn: () => apiFetch<ChatMessage[]>(`/api/campaigns/${campaignId}/messages`),
    refetchInterval: 120_000,
  });
  const { data: eventTypeOptions = [] } = useQuery({
    queryKey: ["narrative-event-types", campaignId],
    queryFn: () => apiFetch<string[]>(`/api/campaigns/${campaignId}/events/types`),
    refetchInterval: 120_000,
  });

  const { data: partyInventory = [] } = useQuery({
    queryKey: ["party-inventory", campaignId],
    queryFn: () =>
      apiFetch<PartyInventoryRow[]>(`/api/campaigns/${campaignId}/inventory/party`),
    refetchInterval: 120_000,
  });

  const { data: narrativeEvents = [] } = useQuery({
    queryKey: [
      "narrative-events",
      campaignId,
      eventTypeFilter,
      minSeverityFilter,
    ],
    queryFn: () =>
      apiFetch<NarrativeEvent[]>(
        narrativeEventsUrl(campaignId, {
          eventType: eventTypeFilter,
          minSeverity: minSeverityFilter,
          limit: 80,
        })
      ),
    refetchInterval: 120_000,
  });

  const nameByUser = useMemo(() => {
    const m: Record<string, string> = {};
    for (const p of members) m[p.user_id] = p.display_name;
    return m;
  }, [members]);

  const playMut = useMutation({
    mutationFn: async (message: string) => {
      setStreamingReply("");
      return streamPlayTurn(campaignId, message, (delta) => {
        setStreamingReply((prev) => prev + delta);
      });
    },
    onSuccess: (data: PlayRes) => {
      setPlayError(null);
      setStreamingReply("");
      setLastRagPreview(data.rag_preview ?? {});
      setLastLatency(summarizeTimings(data.timings ?? undefined));
      const reply = (data.reply || "").trim();
      const blocked =
        reply.includes("No se ejecuta la acción.") ||
        reply.toLowerCase().includes("rechaza la acción") ||
        reply.toLowerCase().includes("acción rechazada");
      setLastActionBlocked(blocked);
      queryClient.invalidateQueries({ queryKey: ["messages", campaignId] });
      queryClient.invalidateQueries({ queryKey: ["campaign", campaignId] });
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      queryClient.invalidateQueries({ queryKey: ["world", campaignId] });
      queryClient.invalidateQueries({ queryKey: ["narrative-events", campaignId] });
      queryClient.invalidateQueries({ queryKey: ["narrative-event-types", campaignId] });
    },
    onError: (err) => {
      setStreamingReply("");
      const msg = (err as Error)?.message ?? "Error al enviar la acción";
      setPlayError(
        msg.length > 280
          ? `${msg.slice(0, 280)}… (revisa que el backend y Ollama estén activos)`
          : msg
      );
    },
  });

  function onSend() {
    const t = input.trim();
    if (!t || playMut.isPending) return;
    setInput("");
    playMut.mutate(t);
  }

  function insertQuickSnippet(snippet: string) {
    setInput((prev) => mergeQuickSnippet(prev, snippet));
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      const el = textareaRef.current;
      if (el) {
        const len = el.value.length;
        el.setSelectionRange(len, len);
      }
    });
  }

  function onQuickDice() {
    const trimmed = input.trim();
    const diceMatch = trimmed.match(/\b(\d*d\d+(?:[+-]\d+)?)\b/i);
    const expr = diceMatch ? diceMatch[1].replace(/\s/g, "") : "1d20";
    const rolled = parseAndRollDice(expr);
    if (!rolled) {
      insertQuickSnippet(
        "[Tirada] Pon una notación válida en el texto (ej. 1d20, 2d6+3) y vuelve a pulsar «Tirar dados», o escribe tu acción y pídele la tirada al DM."
      );
      return;
    }
    insertQuickSnippet(
      `${rolled.text}. Describe para qué es la tirada (sigilo, ataque, salvación, etc.) si no queda claro en la escena.`
    );
  }

  function onQuickInventory() {
    navigate({ to: "/campaign/$campaignId/character", params: { campaignId } });
  }

  const npcs = world?.nodes_by_label?.NPC ?? world?.nodes_by_label?.["Npc"] ?? [];
  const places = world?.nodes_by_label?.Location ?? world?.nodes_by_label?.Place ?? [];
  const events = world?.recent_events ?? [];
  const ragTraces = lastRagPreview ? parseRagTraces(lastRagPreview) : { rules: [], sheets: [] };
  const hasRagTrace =
    ragTraces.rules.length > 0 || ragTraces.sheets.length > 0;

  return (
    <div className="h-screen flex flex-col">
      <header className="glass-strong border-b border-border px-6 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <div className="h-9 w-9 rounded-xl bg-gradient-ember grid place-items-center ember-glow shrink-0">
            <Flame className="h-4 w-4 text-primary-foreground" />
          </div>
          <div className="min-w-0">
            <div className="font-display font-semibold leading-tight truncate">
              {campaign?.name ?? "Cargando…"}
            </div>
            <div className="text-xs text-muted-foreground truncate">
              {campaign?.game_system ?? "Mesa"} · {campaign?.status === "lobby" ? "Lobby" : "En juego"}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <div className="hidden md:flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="h-1.5 w-1.5 rounded-full bg-success animate-pulse" />{" "}
            {members.length} en la mesa
          </div>
          <div className="flex -space-x-2">
            {members.slice(0, 5).map((p, i) => (
              <div
                key={p.user_id}
                className={`h-7 w-7 rounded-full border-2 border-background grid place-items-center text-[10px] font-bold text-primary-foreground ${
                  ["bg-gradient-ember", "bg-info", "bg-accent text-accent-foreground", "bg-success text-background", "bg-muted"][i % 5]
                }`}
              >
                {p.display_name[0]?.toUpperCase()}
              </div>
            ))}
          </div>
        </div>
      </header>

      <div className="flex-1 grid grid-cols-12 min-h-0">
        <aside className="hidden lg:flex col-span-3 flex-col border-r border-border glass overflow-y-auto">
          <div className="p-4 border-b border-border">
            <div className="text-[10px] uppercase tracking-widest text-muted-foreground flex items-center gap-1.5">
              <Users2 className="h-3 w-3" /> Jugadores · {members.length}
            </div>
          </div>
          <div className="p-3 space-y-2">
            {members.map((p) => (
              <div
                key={p.user_id}
                className="w-full text-left p-3 rounded-xl bg-muted/30 border border-border"
              >
                <div className="flex items-center gap-3">
                  <div className="h-10 w-10 rounded-full bg-gradient-to-br from-primary to-accent grid place-items-center text-primary-foreground font-display font-bold text-sm shrink-0">
                    {p.display_name[0]?.toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">{p.display_name}</div>
                    <div className="text-xs text-muted-foreground truncate">{p.role}</div>
                  </div>
                </div>
                <Link
                  to="/campaign/$campaignId/character"
                  params={{ campaignId }}
                  className="mt-2 text-[10px] text-muted-foreground/70 hover:text-primary inline-flex items-center gap-1"
                >
                  Ficha / docs <ChevronRight className="h-2.5 w-2.5" />
                </Link>
              </div>
            ))}
          </div>
        </aside>

        <section className="col-span-12 lg:col-span-6 flex flex-col min-h-0 scanline-bg">
          <div className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
            {(playMut.isError || playError) && (
              <div className="text-sm text-destructive bg-destructive/10 border border-destructive/30 rounded-xl px-3 py-2">
                {playError ??
                  (playMut.error as Error)?.message ??
                  "Error al enviar. Comprueba el backend (puerto 8000) y Ollama."}
              </div>
            )}
            {messages.map((m) => {
              if (m.role === "assistant") {
                return (
                  <div key={m.id} className="flex gap-3">
                    <div className="h-9 w-9 rounded-xl bg-gradient-ember grid place-items-center shrink-0 ember-glow">
                      <Sparkles className="h-4 w-4 text-primary-foreground" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-xs font-display font-semibold text-primary mb-1 inline-flex items-center gap-1.5">
                        Dungeon Master <span className="text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/15 text-primary">IA</span>
                      </div>
                      <div className="glass-strong rounded-2xl rounded-tl-sm px-4 py-3 text-[15px] leading-relaxed border-l-2 border-primary">
                        {m.content}
                      </div>
                    </div>
                  </div>
                );
              }
              const who = m.user_id ? nameByUser[m.user_id] ?? "Jugador" : "Jugador";
              return (
                <div key={m.id} className="flex gap-3">
                  <div className="h-9 w-9 rounded-full bg-info grid place-items-center text-primary-foreground font-bold text-sm shrink-0">
                    {who[0]?.toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-muted-foreground mb-1">{who}</div>
                    <div className="bg-muted/40 border border-border rounded-2xl rounded-tl-sm px-4 py-3 text-sm">
                      {m.content}
                    </div>
                  </div>
                </div>
              );
            })}
            {playMut.isPending && (
              <div className="flex gap-3 opacity-90">
                <div className="h-9 w-9 rounded-xl bg-gradient-ember grid place-items-center shrink-0">
                  <Sparkles className="h-4 w-4 text-primary-foreground" />
                </div>
                <div className="glass rounded-2xl rounded-tl-sm px-4 py-3 border-l-2 border-primary max-w-[90%]">
                  {streamingReply ? (
                    <span className="text-[15px] leading-relaxed whitespace-pre-wrap">{streamingReply}</span>
                  ) : (
                    <span className="text-xs text-muted-foreground inline-flex items-center gap-2">
                      <span className="h-2 w-2 rounded-full bg-primary animate-pulse" />
                      El DM está respondiendo…
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>

          <div className="border-t border-border glass-strong p-4 space-y-3">
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={playMut.isPending}
                onClick={onQuickDice}
                title="Genera una tirada (usa 1d20 por defecto; si escribes 2d6+3 en el cuadro, usa esa notación)"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg glass border border-border hover:border-primary/50 hover:bg-muted/40 text-xs font-medium transition disabled:opacity-50"
              >
                <Dices className="h-3.5 w-3.5 text-primary" />
                Tirar dados
              </button>
              <button
                type="button"
                disabled={playMut.isPending}
                onClick={() =>
                  insertQuickSnippet(
                    "[ataq] Ataq a [[objetivo]] con [[arma o habilidad]]. ¿Hay cobertura, ventaja o situación especial? Resuelve impacto y daño según las reglas de esta mesa."
                  )
                }
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg glass border border-border hover:border-primary/50 hover:bg-muted/40 text-xs font-medium transition disabled:opacity-50"
              >
                <Sword className="h-3.5 w-3.5 text-primary" />
                Atacar
              </button>
              <button
                type="button"
                disabled={playMut.isPending}
                onClick={() =>
                  insertQuickSnippet(
                    "[Exploración] Exploro el entorno con calma: salidas, objetos útiles, pistas, ruidos y cualquier detalle que cambie decisiones o riesgos."
                  )
                }
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg glass border border-border hover:border-primary/50 hover:bg-muted/40 text-xs font-medium transition disabled:opacity-50"
              >
                <Eye className="h-3.5 w-3.5 text-primary" />
                Explorar
              </button>
              <button
                type="button"
                disabled={playMut.isPending}
                onClick={() =>
                  insertQuickSnippet(
                    "[Diálogo] Hablo con [[PNJ o grupo]]. Mi tono es [[amable / firme / amenazante…]]. Digo: «…»."
                  )
                }
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg glass border border-border hover:border-primary/50 hover:bg-muted/40 text-xs font-medium transition disabled:opacity-50"
              >
                <MessageSquare className="h-3.5 w-3.5 text-primary" />
                Hablar
              </button>
              <button
                type="button"
                disabled={playMut.isPending}
                onClick={onQuickInventory}
                title="Abre la pantalla de ficha para editar tu inventario"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg glass border border-border hover:border-primary/50 hover:bg-muted/40 text-xs font-medium transition disabled:opacity-50"
              >
                <Backpack className="h-3.5 w-3.5 text-primary" />
                Inventario
              </button>
            </div>
            {hasRagTrace && (
              <div className="text-[10px] text-muted-foreground/95 px-1 space-y-1.5 border border-border/60 rounded-xl p-2 bg-muted/15">
                <div className="uppercase tracking-widest text-[9px] text-muted-foreground">
                  Fragmentos RAG (último turno)
                </div>
                {ragTraces.rules.length > 0 && (
                  <div>
                    <div className="text-[9px] text-primary/90 mb-0.5">Reglas</div>
                    <ul className="space-y-1 max-h-24 overflow-y-auto">
                      {ragTraces.rules.map((r, i) => (
                        <li key={`r-${i}`} className="leading-snug opacity-95">
                          <span className="font-mono text-[9px] text-muted-foreground">
                            [{String(r.source_tier ?? "?")}]
                          </span>{" "}
                          {typeof r.preview === "string"
                            ? r.preview
                            : String(r.source ?? "")}
                          {typeof r.rerank_score === "number" && (
                            <span className="ml-1 opacity-70">· {r.rerank_score}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {ragTraces.sheets.length > 0 && (
                  <div>
                    <div className="text-[9px] text-primary/90 mb-0.5">Fichas</div>
                    <ul className="space-y-1 max-h-20 overflow-y-auto">
                      {ragTraces.sheets.map((r, i) => (
                        <li key={`s-${i}`} className="leading-snug opacity-95">
                          <span className="font-mono text-[9px] text-muted-foreground">
                            [{String(r.source_tier ?? "?")}]
                          </span>{" "}
                          {typeof r.preview === "string"
                            ? r.preview
                            : String(r.source ?? "")}
                          {typeof r.rerank_score === "number" && (
                            <span className="ml-1 opacity-70">· {r.rerank_score}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            )}
            {lastActionBlocked && !playMut.isPending && (
              <div className="text-sm text-amber-foreground bg-amber-500/10 border border-amber-500/30 rounded-xl px-3 py-2">
                Acción bloqueada por validación. Si falta información (inventario / ficha / estado), responde con
                datos concretos y vuelve a intentar.
              </div>
            )}
            {lastLatency && (
              <p className="text-[10px] text-muted-foreground/90 px-1 font-mono" title="Tiempos del último turno (servidor)">
                Turno · {lastLatency}
              </p>
            )}
            <div className="flex items-end gap-2 bg-input border border-border rounded-2xl p-2 focus-within:border-primary/50 transition">
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    onSend();
                  }
                }}
                placeholder="Describe tu acción… (Enter para enviar)"
                rows={1}
                className="flex-1 bg-transparent px-3 py-2 text-sm focus:outline-none resize-none"
              />
              <button
                type="button"
                onClick={onSend}
                disabled={playMut.isPending}
                className="h-10 w-10 rounded-xl bg-gradient-ember grid place-items-center text-primary-foreground ember-glow shrink-0 disabled:opacity-50"
              >
                <Send className="h-4 w-4" />
              </button>
            </div>
            <p className="text-[10px] text-muted-foreground leading-relaxed px-1 flex gap-2 items-start">
              <Hash className="h-3.5 w-3.5 shrink-0 mt-0.5 opacity-70" aria-hidden />
              <span>
                Tipografía tipo Obsidian:{" "}
                <kbd className="px-1 py-px rounded bg-muted/80 border border-border font-mono text-[9px]">
                  [[Nombre]]
                </kbd>{" "}
                (wikilink) o{" "}
                <kbd className="px-1 py-px rounded bg-muted/80 border border-border font-mono text-[9px]">
                  #Nombre
                </kbd>{" "}
                para anclar personas, lugares o cosas al grafo de esta mesa y que el DM los recupere mejor.
              </span>
            </p>
          </div>
        </section>

        <aside className="hidden xl:flex col-span-3 flex-col border-l border-border glass overflow-y-auto">
          <div className="p-4 border-b border-border">
            <div className="text-[10px] uppercase tracking-widest text-muted-foreground flex items-center gap-1.5">
              <Map className="h-3 w-3" /> Mundo (GraphRAG)
            </div>
          </div>
          <div className="p-4 space-y-5">
            <Section title="Inventario del grupo">
              <p className="text-[10px] text-muted-foreground mb-2 leading-relaxed">
                Lo que cada jugador tiene en la app — el DM lo recibe en cada turno.{" "}
                <Link
                  to="/campaign/$campaignId/character"
                  params={{ campaignId }}
                  className="text-primary hover:underline"
                >
                  Editar el tuyo →
                </Link>
              </p>
              <div className="space-y-2 max-h-48 overflow-y-auto pr-0.5">
                {partyInventory.every((p) => p.items.length === 0) && (
                  <p className="text-xs text-muted-foreground">Inventarios vacíos.</p>
                )}
                {partyInventory.map((p) => (
                  <div key={p.user_id} className="text-[11px] border-l-2 border-border pl-2">
                    <span className="font-medium text-foreground/90">{p.display_name}</span>
                    {p.items.length === 0 ? (
                      <span className="text-muted-foreground ml-1">— vacío</span>
                    ) : (
                      <ul className="mt-0.5 text-muted-foreground leading-snug space-y-0.5">
                        {p.items.map((it) => (
                          <li key={it.id}>
                            {it.name} ×{it.quantity}
                            {it.category ? (
                              <span className="opacity-70"> · {it.category}</span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            </Section>

            <Section title="Memoria de sesión">
              {!campaign?.session_summary?.trim() ? (
                <p className="text-xs text-muted-foreground">
                  Aún no hay resumen comprimido. Se actualiza cada varios turnos del DM según configuración del
                  servidor.
                </p>
              ) : (
                <p className="text-xs text-muted-foreground whitespace-pre-wrap leading-relaxed max-h-44 overflow-y-auto border border-border/50 rounded-lg p-2 bg-muted/20">
                  {campaign.session_summary}
                </p>
              )}
            </Section>
            <Section title="Eventos narrativos">
              <div className="flex flex-wrap gap-2 mb-2">
                <label className="flex flex-col gap-0.5 text-[9px] uppercase tracking-wider text-muted-foreground">
                  Tipo
                  <select
                    value={eventTypeFilter}
                    onChange={(ev) => {
                      setEventTypeFilter(ev.target.value);
                      setExpandedEventId(null);
                    }}
                    className="text-xs normal-case rounded-lg border border-border bg-background/80 px-2 py-1.5 max-w-[140px]"
                  >
                    <option value="">Todos</option>
                    {eventTypeOptions.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-0.5 text-[9px] uppercase tracking-wider text-muted-foreground">
                  Severidad mín.
                  <select
                    value={minSeverityFilter}
                    onChange={(ev) => {
                      setMinSeverityFilter(Number(ev.target.value));
                      setExpandedEventId(null);
                    }}
                    className="text-xs normal-case rounded-lg border border-border bg-background/80 px-2 py-1.5"
                  >
                    <option value={0}>Cualquiera</option>
                    <option value={1}>1+</option>
                    <option value={2}>2+</option>
                    <option value={3}>3</option>
                  </select>
                </label>
              </div>
              {narrativeEvents.length === 0 && (
                <p className="text-xs text-muted-foreground">No hay eventos con estos filtros.</p>
              )}
              <div className="space-y-2 max-h-80 overflow-y-auto pr-0.5">
                {[...narrativeEvents].reverse().map((e) => (
                  <NarrativeEventCard
                    key={e.id}
                    e={e}
                    expanded={expandedEventId === e.id}
                    onToggle={() =>
                      setExpandedEventId((id) => (id === e.id ? null : e.id))
                    }
                  />
                ))}
              </div>
            </Section>

            <Section title="Eventos del grafo (legacy)">
              {events.length === 0 && (
                <p className="text-xs text-muted-foreground">Sin eventos legacy en GraphRAG.</p>
              )}
              {events.slice(0, 5).map((e, i) => (
                <div key={i} className="text-xs text-muted-foreground py-1">
                  {String((e as { name?: string }).name ?? JSON.stringify(e))}
                </div>
              ))}
            </Section>

            <Section title="NPCs (muestra)">
              {npcs.length === 0 && <p className="text-xs text-muted-foreground">Sin nodos NPC aún.</p>}
              {npcs.slice(0, 6).map((n, i) => (
                <div key={i} className="flex items-center gap-2 p-2 rounded-lg hover:bg-muted/40 transition">
                  <div className="h-7 w-7 rounded-full grid place-items-center text-[10px] font-bold bg-muted text-muted-foreground">
                    {String(n.name || "?")[0]}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium truncate">{String(n.name)}</div>
                  </div>
                </div>
              ))}
            </Section>

            <Section title="Lugares (muestra)">
              {places.length === 0 && <p className="text-xs text-muted-foreground">Sin lugares indexados aún.</p>}
              {places.slice(0, 6).map((n, i) => (
                <div key={i} className="text-xs py-1.5 px-2 rounded hover:bg-muted/40">
                  {String(n.name)}
                </div>
              ))}
            </Section>

            <div className="text-[10px] text-muted-foreground">
              Aristas en grafo: {world?.edge_count ?? 0}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-widest text-muted-foreground mb-2">{title}</div>
      <div className="space-y-1">{children}</div>
    </div>
  );
}
