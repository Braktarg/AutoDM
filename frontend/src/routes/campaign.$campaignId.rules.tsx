import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload, FileText, Search, BookOpen, Check, Loader2 } from "lucide-react";
import { apiFetch, apiUpload } from "@/lib/api";
import { setLastCampaignId } from "@/lib/auth";
import type { CampaignDocument, CampaignOut } from "@/lib/types";
import { RequireAuth } from "@/lib/RequireAuth";

export const Route = createFileRoute("/campaign/$campaignId/rules")({
  head: () => ({
    meta: [
      { title: "Reglas — AutoDM" },
      { name: "description", content: "Manuales indexados por mesa." },
    ],
  }),
  component: RulesRoute,
});

function RulesRoute() {
  return (
    <RequireAuth>
      <RulesLibrary />
    </RequireAuth>
  );
}

function RulesLibrary() {
  const { campaignId } = Route.useParams();
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [sourceTier, setSourceTier] = useState<
    "official" | "homebrew" | "dm_notes" | "manual"
  >("manual");

  useEffect(() => {
    setLastCampaignId(campaignId);
  }, [campaignId]);

  const { data: campaign } = useQuery({
    queryKey: ["campaign", campaignId],
    queryFn: () => apiFetch<CampaignOut>(`/api/campaigns/${campaignId}`),
  });

  const { data: docs = [], isLoading } = useQuery({
    queryKey: ["documents", campaignId],
    queryFn: () => apiFetch<CampaignDocument[]>(`/api/campaigns/${campaignId}/documents`),
  });

  const manuals = docs.filter((d) => d.kind === "manual");

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      fd.append("source_tier", sourceTier);
      await apiUpload(`/api/campaigns/${campaignId}/documents/manual`, fd, null);
      await queryClient.invalidateQueries({ queryKey: ["documents", campaignId] });
    } finally {
      setBusy(false);
      e.target.value = "";
    }
  }

  const totalChunks = manuals.reduce((acc, d) => acc + (d.meta?.chunks ?? 0), 0);

  return (
    <div className="px-8 lg:px-12 py-10">
      <div className="flex flex-wrap items-end justify-between gap-4 mb-8">
        <div>
          <Link
            to="/campaign/$campaignId/play"
            params={{ campaignId }}
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            ← Volver a la mesa
          </Link>
          <div className="text-xs uppercase tracking-widest text-muted-foreground mt-2">{campaign?.name}</div>
          <h1 className="font-display text-4xl font-bold mt-1">Biblioteca de reglas</h1>
          <p className="text-muted-foreground">PDFs indexados (RAG) para esta mesa.</p>
        </div>
        <div className="flex flex-col items-end gap-2">
          <label className="text-[10px] uppercase tracking-widest text-muted-foreground">
            Tipo de fuente (prioridad RAG)
            <select
              value={sourceTier}
              onChange={(e) =>
                setSourceTier(e.target.value as typeof sourceTier)
              }
              className="ml-2 mt-1 block text-xs font-sans normal-case rounded-lg border border-border bg-background px-2 py-1.5"
            >
              <option value="official">Oficial</option>
              <option value="homebrew">Homebrew</option>
              <option value="dm_notes">Notas del DM</option>
              <option value="manual">Manual / otro</option>
            </select>
          </label>
          <input ref={fileRef} type="file" accept="application/pdf" className="hidden" onChange={onUpload} />
          <button
            type="button"
            disabled={busy}
            onClick={() => fileRef.current?.click()}
            className="px-5 py-3 rounded-xl bg-gradient-ember text-primary-foreground font-medium ember-glow inline-flex items-center gap-2 disabled:opacity-50"
          >
            <Upload className="h-4 w-4" /> {busy ? "Subiendo…" : "Subir manual (PDF)"}
          </button>
        </div>
      </div>

      <div className="glass rounded-2xl p-1.5 mb-8 flex items-center gap-2">
        <Search className="h-4 w-4 text-muted-foreground ml-3" />
        <input
          readOnly
          placeholder="Las reglas se consultan automáticamente durante el turno del DM."
          className="flex-1 bg-transparent px-2 py-2.5 text-sm focus:outline-none cursor-default"
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
        {[
          { label: "Manuales", value: String(manuals.length) },
          { label: "Fragmentos (chunks)", value: totalChunks ? String(totalChunks) : "—" },
          { label: "Estado", value: isLoading ? "…" : "OK" },
          { label: "Mesa", value: campaign?.game_system ?? "—" },
        ].map((s) => (
          <div key={s.label} className="glass rounded-xl p-4">
            <div className="text-xs text-muted-foreground">{s.label}</div>
            <div className="font-display text-2xl font-bold mt-1 text-gradient-ember">{s.value}</div>
          </div>
        ))}
      </div>

      {isLoading && <p className="text-sm text-muted-foreground">Cargando documentos…</p>}

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        {manuals.map((b) => {
          const chunks = b.meta?.chunks ?? 0;
          const err = b.meta?.rag_error;
          const ready = chunks > 0 && !err;
          return (
            <div key={b.id} className="glass rounded-2xl overflow-hidden hover:border-primary/40 transition group">
              <div className="h-32 bg-gradient-to-br from-primary/40 to-accent/30 relative grid place-items-center">
                <div className="absolute inset-0 scanline-bg opacity-30" />
                <BookOpen className="h-10 w-10 text-primary-foreground/80 relative" />
                <div className="absolute top-2 right-2">
                  {ready ? (
                    <span className="text-[10px] uppercase tracking-wider px-2 py-1 rounded-md bg-success/30 text-success font-medium inline-flex items-center gap-1">
                      <Check className="h-3 w-3" /> Indexado
                    </span>
                  ) : err ? (
                    <span className="text-[10px] uppercase tracking-wider px-2 py-1 rounded-md bg-destructive/20 text-destructive font-medium">
                      Revisar PDF
                    </span>
                  ) : (
                    <span className="text-[10px] uppercase tracking-wider px-2 py-1 rounded-md bg-info/30 text-info font-medium inline-flex items-center gap-1">
                      <Loader2 className="h-3 w-3 animate-spin" /> Procesando
                    </span>
                  )}
                </div>
              </div>
              <div className="p-4">
                <div className="flex items-start gap-2">
                  <FileText className="h-4 w-4 text-primary shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div className="font-display font-semibold text-sm leading-tight">{b.filename}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">
                      {b.meta?.source_tier && (
                        <span className="mr-2 text-[10px] uppercase tracking-wide text-primary/80">
                          {b.meta.source_tier}
                        </span>
                      )}
                      {chunks > 0 ? `${chunks} fragmentos` : err ? "Sin texto extraído" : "Sin chunks aún"}
                    </div>
                    {err && <div className="text-xs text-destructive mt-2">{err}</div>}
                  </div>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {!isLoading && manuals.length === 0 && (
        <p className="text-sm text-muted-foreground mt-6">No hay manuales. Sube un PDF para indexar reglas.</p>
      )}
    </div>
  );
}
