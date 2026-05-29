import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import {
  Check,
  Upload,
  FileText,
  Sparkles,
  Copy,
  ArrowRight,
  ArrowLeft,
  Wand2,
  PenLine,
  ImagePlus,
} from "lucide-react";
import { apiFetch, apiUpload } from "@/lib/api";
import type { CampaignOut } from "@/lib/types";
import { RequireAuth } from "@/lib/RequireAuth";

export const Route = createFileRoute("/create")({
  head: () => ({
    meta: [
      { title: "Crear mesa — AutoDM" },
      {
        name: "description",
        content: "Configura una nueva campaña: nombre, reglas, narrativa y jugadores.",
      },
    ],
  }),
  component: CreateWrapped,
});

const steps = [
  { n: 1, title: "Identidad", subtitle: "Nombre y mundo" },
  { n: 2, title: "Reglas", subtitle: "Sistema de juego" },
  { n: 3, title: "Narrativa", subtitle: "Estilo del DM" },
  { n: 4, title: "Jugadores", subtitle: "Invitar a tu mesa" },
];

function CreateWrapped() {
  return (
    <RequireAuth>
      <CreateCampaign />
    </RequireAuth>
  );
}

function CreateCampaign() {
  const [step, setStep] = useState(1);
  const [storyMode, setStoryMode] = useState<"ai" | "guided">("ai");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [gameSystem, setGameSystem] = useState("D&D 5e");
  const [narrativeStyle, setNarrativeStyle] = useState(
    "Narra con tono cinematográfico, oscuro y poético. Da peso a las decisiones morales."
  );
  const [campaignId, setCampaignId] = useState<string | null>(null);
  const [inviteCode, setInviteCode] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [uploads, setUploads] = useState<{ name: string; ok?: string; err?: string }[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);
  const [coverFile, setCoverFile] = useState<File | null>(null);
  const [coverPreview, setCoverPreview] = useState<string | null>(null);
  const coverRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    return () => {
      if (coverPreview) URL.revokeObjectURL(coverPreview);
    };
  }, [coverPreview]);

  const narrativeMode = storyMode === "ai" ? "ai_generated" : "creator_guided";

  async function ensureCampaign() {
    if (campaignId) return campaignId;
    setErr(null);
    setLoading(true);
    try {
      const c = await apiFetch<CampaignOut>("/api/campaigns", {
        method: "POST",
        body: JSON.stringify({
          name: name.trim() || "Nueva campaña",
          description: description.trim() || null,
        }),
      });
      setCampaignId(c.id);
      setInviteCode(c.invite_code);
      return c.id;
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Error al crear");
      throw e;
    } finally {
      setLoading(false);
    }
  }

  async function goNext() {
    setErr(null);
    if (step === 1) {
      setLoading(true);
      try {
        const id = await ensureCampaign();
        if (coverFile) {
          const fd = new FormData();
          fd.append("file", coverFile);
          await apiUpload(`/api/campaigns/${id}/cover`, fd, null);
        }
        setStep(2);
      } catch {
        /* err set */
      } finally {
        setLoading(false);
      }
      return;
    }
    if (step === 2) {
      const id = await ensureCampaign();
      setLoading(true);
      try {
        await apiFetch<CampaignOut>(`/api/campaigns/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ game_system: gameSystem.trim() || null }),
        });
        setStep(3);
      } catch (e) {
        setErr(e instanceof Error ? e.message : "Error al guardar");
      } finally {
        setLoading(false);
      }
      return;
    }
    if (step === 3) {
      const id = await ensureCampaign();
      setLoading(true);
      try {
        await apiFetch<CampaignOut>(`/api/campaigns/${id}`, {
          method: "PATCH",
          body: JSON.stringify({
            narrative_mode: narrativeMode,
            narrative_style: narrativeStyle.trim() || null,
          }),
        });
        setStep(4);
      } catch (e) {
        setErr(e instanceof Error ? e.message : "Error al guardar");
      } finally {
        setLoading(false);
      }
    }
  }

  async function onPickPdf(e: React.ChangeEvent<HTMLInputElement>) {
    const id = campaignId;
    if (!id || !e.target.files?.length) return;
    for (const file of Array.from(e.target.files)) {
      if (!file.name.toLowerCase().endsWith(".pdf")) {
        setUploads((u) => [...u, { name: file.name, err: "Solo PDF" }]);
        continue;
      }
      const fd = new FormData();
      fd.append("file", file);
      fd.append("source_tier", "manual");
      try {
        await apiUpload(`/api/campaigns/${id}/documents/manual`, fd, null);
        setUploads((u) => [...u, { name: file.name, ok: "Indexado" }]);
      } catch (er) {
        setUploads((u) => [
          ...u,
          { name: file.name, err: er instanceof Error ? er.message : "Error" },
        ]);
      }
    }
    e.target.value = "";
  }

  const copyCode = () => {
    if (inviteCode) void navigator.clipboard.writeText(inviteCode);
  };

  function onCoverPick(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    if (!/^image\/(jpeg|png|webp)$/.test(f.type)) {
      setErr("La portada debe ser JPEG, PNG o WebP");
      e.target.value = "";
      return;
    }
    if (f.size > 4 * 1024 * 1024) {
      setErr("La imagen supera 4 MB");
      e.target.value = "";
      return;
    }
    setErr(null);
    setCoverFile(f);
    setCoverPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return URL.createObjectURL(f);
    });
    e.target.value = "";
  }

  function clearCover() {
    setCoverFile(null);
    setCoverPreview((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  }

  return (
    <div className="px-8 lg:px-12 py-10 max-w-5xl mx-auto">
      <div className="mb-8">
        <Link
          to="/"
          className="text-xs text-muted-foreground hover:text-foreground inline-flex items-center gap-1 mb-3"
        >
          <ArrowLeft className="h-3 w-3" /> Volver al dashboard
        </Link>
        <h1 className="font-display text-4xl font-bold">Forja una nueva mesa</h1>
        <p className="text-muted-foreground mt-1">Cuatro pasos para encender la primera llama.</p>
      </div>

      {err && (
        <div className="mb-4 text-sm text-destructive bg-destructive/10 border border-destructive/30 rounded-xl px-3 py-2">
          {err}
        </div>
      )}

      <div className="glass rounded-2xl p-2 mb-8">
        <div className="grid grid-cols-4 gap-1">
          {steps.map((s) => {
            const active = step === s.n;
            const done = step > s.n;
            return (
              <button
                key={s.n}
                type="button"
                onClick={() => done && setStep(s.n)}
                className={`p-4 rounded-xl text-left transition ${
                  active
                    ? "bg-gradient-ember text-primary-foreground ember-glow"
                    : done
                      ? "bg-muted/50"
                      : "hover:bg-muted/40 opacity-60"
                }`}
                disabled={!done && !active}
              >
                <div className="flex items-center gap-2">
                  <div
                    className={`h-6 w-6 rounded-full grid place-items-center text-xs font-bold ${
                      active
                        ? "bg-background/20 text-primary-foreground"
                        : done
                          ? "bg-success text-background"
                          : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {done ? <Check className="h-3 w-3" /> : s.n}
                  </div>
                  <div className="text-xs font-medium opacity-80">Paso {s.n}</div>
                </div>
                <div className="font-display font-semibold mt-1.5">{s.title}</div>
                <div
                  className={`text-xs mt-0.5 ${active ? "text-primary-foreground/80" : "text-muted-foreground"}`}
                >
                  {s.subtitle}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="glass rounded-2xl p-8 min-h-[420px]">
        {step === 1 && (
          <div className="space-y-6">
            <div>
              <label className="text-xs uppercase tracking-widest text-muted-foreground">Nombre de campaña</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="mt-2 w-full bg-input border border-border rounded-xl px-4 py-3.5 font-display text-lg focus:outline-none focus:ring-2 focus:ring-primary/40"
                placeholder="Las Llamas de Therion"
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-widest text-muted-foreground">Descripción del mundo</label>
              <textarea
                rows={6}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Contexto que el DM usará como semilla…"
                className="mt-2 w-full bg-input border border-border rounded-xl px-4 py-3 text-sm leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/40 resize-none"
              />
              <p className="text-xs text-muted-foreground mt-2 inline-flex items-center gap-1">
                <Sparkles className="h-3 w-3 text-primary" /> El DM IA usará esto como semilla para construir el mundo.
              </p>
            </div>

            <div>
              <label className="text-xs uppercase tracking-widest text-muted-foreground">Portada de la mesa</label>
              <p className="text-sm text-muted-foreground mt-1 mb-3">
                Opcional · JPEG, PNG o WebP · máx. 4 MB. Se mostrará en tu dashboard.
              </p>
              <input
                ref={coverRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                className="hidden"
                onChange={onCoverPick}
              />
              <div className="flex flex-wrap items-start gap-4">
                <button
                  type="button"
                  onClick={() => coverRef.current?.click()}
                  className="relative h-36 w-full max-w-[220px] rounded-xl border-2 border-dashed border-border hover:border-primary/50 bg-muted/20 overflow-hidden flex flex-col items-center justify-center gap-2 text-sm text-muted-foreground hover:text-foreground transition"
                >
                  {coverPreview ? (
                    <img src={coverPreview} alt="" className="absolute inset-0 h-full w-full object-cover" />
                  ) : (
                    <>
                      <ImagePlus className="h-8 w-8 text-primary" />
                      <span>Elegir imagen</span>
                    </>
                  )}
                </button>
                {coverPreview && (
                  <button
                    type="button"
                    onClick={clearCover}
                    className="text-sm text-destructive hover:underline self-center"
                  >
                    Quitar imagen
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {step === 2 && (
          <div className="space-y-5">
            <div>
              <label className="text-xs uppercase tracking-widest text-muted-foreground">Sistema de juego</label>
              <input
                value={gameSystem}
                onChange={(e) => setGameSystem(e.target.value)}
                className="mt-2 w-full bg-input border border-border rounded-xl px-4 py-3 text-sm"
              />
            </div>
            <div>
              <h3 className="font-display text-xl font-semibold">Manuales del sistema</h3>
              <p className="text-sm text-muted-foreground mt-1">Sube PDFs: el DM los indexará (RAG) para consultar reglas en partida.</p>
            </div>

            <input ref={fileRef} type="file" accept="application/pdf" multiple className="hidden" onChange={onPickPdf} />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="w-full border-2 border-dashed border-border rounded-2xl p-10 text-center hover:border-primary/50 transition cursor-pointer bg-muted/20"
            >
              <div className="h-14 w-14 mx-auto rounded-2xl bg-gradient-ember/20 ember-border grid place-items-center mb-3">
                <Upload className="h-6 w-6 text-primary" />
              </div>
              <div className="font-medium">Subir PDFs (varios)</div>
              <div className="text-xs text-muted-foreground mt-1">Solo archivos con cabecera PDF válida</div>
            </button>

            {uploads.length > 0 && (
              <div className="space-y-2">
                {uploads.map((f, i) => (
                  <div key={i} className="flex items-center gap-3 p-3 rounded-xl bg-muted/30 border border-border">
                    <div className="h-9 w-9 rounded-lg bg-background grid place-items-center">
                      <FileText className="h-4 w-4 text-primary" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium truncate">{f.name}</div>
                      {f.ok && <div className="text-xs text-success">{f.ok}</div>}
                      {f.err && <div className="text-xs text-destructive">{f.err}</div>}
                    </div>
                    {f.ok && <Check className="h-5 w-5 text-success" />}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {step === 3 && (
          <div className="space-y-6">
            <div>
              <h3 className="font-display text-xl font-semibold">Estilo del DM</h3>
              <p className="text-sm text-muted-foreground mt-1">¿Cómo debe construirse la historia?</p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <button
                type="button"
                onClick={() => setStoryMode("ai")}
                className={`text-left p-5 rounded-2xl border-2 transition ${
                  storyMode === "ai"
                    ? "border-primary bg-primary/10 ember-glow"
                    : "border-border bg-muted/20 hover:border-primary/40"
                }`}
              >
                <div className="h-10 w-10 rounded-xl bg-gradient-ember grid place-items-center mb-3">
                  <Wand2 className="h-5 w-5 text-primary-foreground" />
                </div>
                <div className="font-display font-semibold">Generada por IA</div>
                <p className="text-sm text-muted-foreground mt-1">Modo {narrativeMode} — el DM improvisa con el contexto de la mesa.</p>
              </button>
              <button
                type="button"
                onClick={() => setStoryMode("guided")}
                className={`text-left p-5 rounded-2xl border-2 transition ${
                  storyMode === "guided"
                    ? "border-primary bg-primary/10 ember-glow"
                    : "border-border bg-muted/20 hover:border-primary/40"
                }`}
              >
                <div className="h-10 w-10 rounded-xl bg-accent/30 grid place-items-center mb-3">
                  <PenLine className="h-5 w-5 text-accent" />
                </div>
                <div className="font-display font-semibold">Guiada por el creador</div>
                <p className="text-sm text-muted-foreground mt-1">Tú marcas el rumbo; el DM reacciona a la mesa.</p>
              </button>
            </div>

            <div>
              <label className="text-xs uppercase tracking-widest text-muted-foreground">Prompt narrativo del DM</label>
              <textarea
                rows={5}
                value={narrativeStyle}
                onChange={(e) => setNarrativeStyle(e.target.value)}
                className="mt-2 w-full bg-input border border-border rounded-xl px-4 py-3 text-sm leading-relaxed focus:outline-none focus:ring-2 focus:ring-primary/40 resize-none"
              />
            </div>
          </div>
        )}

        {step === 4 && inviteCode && (
          <div className="space-y-6">
            <div>
              <h3 className="font-display text-xl font-semibold">Invita a tu mesa</h3>
              <p className="text-sm text-muted-foreground mt-1">Comparte el código con tus jugadores.</p>
            </div>

            <div className="flex gap-2">
              <div className="flex-1 flex items-center gap-2 bg-input border border-border rounded-xl px-4 py-3">
                <span className="text-sm font-mono font-semibold tracking-wider">{inviteCode}</span>
              </div>
              <button
                type="button"
                onClick={copyCode}
                className="px-4 rounded-xl bg-gradient-ember text-primary-foreground font-medium ember-glow inline-flex items-center gap-2"
              >
                <Copy className="h-4 w-4" /> Copiar
              </button>
            </div>

            <p className="text-sm text-muted-foreground">
              Los jugadores pueden unirse desde el dashboard con &ldquo;Unirse a mesa&rdquo; e introducir este código.
            </p>
          </div>
        )}
      </div>

      <div className="flex items-center justify-between mt-6">
        <button
          type="button"
          onClick={() => setStep((s) => Math.max(1, s - 1))}
          disabled={step === 1 || loading}
          className="px-5 py-2.5 rounded-xl glass-strong font-medium disabled:opacity-40 inline-flex items-center gap-2"
        >
          <ArrowLeft className="h-4 w-4" /> Anterior
        </button>
        {step < 4 ? (
          <button
            type="button"
            onClick={() => void goNext()}
            disabled={loading}
            className="px-5 py-2.5 rounded-xl bg-gradient-ember text-primary-foreground font-medium ember-glow inline-flex items-center gap-2"
          >
            {loading ? "Guardando…" : "Siguiente"} <ArrowRight className="h-4 w-4" />
          </button>
        ) : (
          campaignId && (
            <Link
              to="/campaign/$campaignId/lobby"
              params={{ campaignId }}
              className="px-5 py-2.5 rounded-xl bg-gradient-ember text-primary-foreground font-medium ember-glow inline-flex items-center gap-2"
            >
              Ir al lobby <ArrowRight className="h-4 w-4" />
            </Link>
          )
        )}
      </div>
    </div>
  );
}
