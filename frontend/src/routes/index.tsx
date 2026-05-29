import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Plus, LogIn, Users, Clock, Sparkles, ArrowRight, Trash2 } from "lucide-react";
import campaign1 from "@/assets/campaign-1.jpg";
import campaign2 from "@/assets/campaign-2.jpg";
import campaign3 from "@/assets/campaign-3.jpg";
import heroEmber from "@/assets/hero-ember.jpg";
import { CampaignCoverImage } from "@/components/CampaignCoverImage";
import { apiFetch } from "@/lib/api";
import type { CampaignSummary, UserOut } from "@/lib/types";
import { RequireAuth } from "@/lib/RequireAuth";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Dashboard — AutoDM" },
      {
        name: "description",
        content:
          "Tus campañas activas, eventos narrativos recientes y acceso rápido a tus mesas.",
      },
    ],
  }),
  component: DashboardRoute,
});

const imgs = [campaign1, campaign2, campaign3];

function DashboardRoute() {
  return (
    <RequireAuth>
      <Dashboard />
    </RequireAuth>
  );
}

function Dashboard() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [joinOpen, setJoinOpen] = useState(false);
  const [joinCode, setJoinCode] = useState("");
  const [joinErr, setJoinErr] = useState<string | null>(null);

  const { data: me } = useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<UserOut>("/api/auth/me"),
  });

  const { data: campaigns = [], isLoading } = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => apiFetch<CampaignSummary[]>("/api/campaigns"),
  });

  const deleteMut = useMutation({
    mutationFn: (id: string) => apiFetch<void>(`/api/campaigns/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
    },
    onError: (e: Error) => {
      window.alert(e.message);
    },
  });

  const joinMut = useMutation({
    mutationFn: (code: string) =>
      apiFetch<{ id: string }>("/api/campaigns/join", {
        method: "POST",
        body: JSON.stringify({ invite_code: code.trim().toUpperCase() }),
      }),
    onSuccess: (c) => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      setJoinOpen(false);
      setJoinCode("");
      navigate({ to: "/campaign/$campaignId/lobby", params: { campaignId: c.id } });
    },
    onError: (e: Error) => setJoinErr(e.message),
  });

  const firstName = me?.display_name?.split(/\s+/)[0] ?? "Aventurero";
  const totalPlayers = campaigns.reduce((acc, c) => acc + (c.member_count || 0), 0);

  return (
    <div className="relative">
      <div className="relative overflow-hidden">
        <img
          src={heroEmber}
          alt=""
          width={1920}
          height={1088}
          className="absolute inset-0 h-full w-full object-cover opacity-50"
        />
        <div className="absolute inset-0 bg-gradient-to-b from-background/30 via-background/70 to-background" />
        <div className="relative px-8 lg:px-12 pt-12 pb-16">
          <div className="max-w-5xl">
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full glass text-xs font-medium text-muted-foreground mb-4">
              <span className="h-1.5 w-1.5 rounded-full bg-success animate-pulse" />
              {campaigns.length} mesas · {totalPlayers} plazas en tus mesas
            </div>
            <h1 className="font-display text-5xl lg:text-6xl font-bold tracking-tight leading-[1.05]">
              Hola, {firstName}. <br />
              <span className="text-gradient-ember">Tu mundo te espera.</span>
            </h1>
            <p className="mt-4 text-base lg:text-lg text-muted-foreground max-w-xl">
              Entra a una mesa, crea una campaña nueva o únete con código de invitación.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link
                to="/create"
                className="inline-flex items-center gap-2 px-5 py-3 rounded-xl bg-gradient-ember text-primary-foreground font-medium ember-glow hover:scale-[1.02] transition"
              >
                <Plus className="h-4 w-4" /> Crear nueva mesa
              </Link>
              <button
                type="button"
                onClick={() => {
                  setJoinErr(null);
                  setJoinOpen(true);
                }}
                className="inline-flex items-center gap-2 px-5 py-3 rounded-xl glass-strong font-medium hover:bg-muted transition"
              >
                <LogIn className="h-4 w-4" /> Unirse a mesa
              </button>
            </div>
          </div>
        </div>
      </div>

      {joinOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 px-4">
          <div className="glass-strong rounded-2xl p-6 max-w-md w-full border border-border">
            <h2 className="font-display text-xl font-semibold">Unirse con código</h2>
            <p className="text-sm text-muted-foreground mt-1">Pega el código que te dio el dueño de la mesa.</p>
            {joinErr && <p className="text-sm text-destructive mt-3">{joinErr}</p>}
            <input
              value={joinCode}
              onChange={(e) => setJoinCode(e.target.value)}
              placeholder="ej. ABC12XYZ"
              className="mt-4 w-full bg-input border border-border rounded-xl px-4 py-3 font-mono uppercase text-sm"
            />
            <div className="mt-4 flex gap-2 justify-end">
              <button
                type="button"
                onClick={() => setJoinOpen(false)}
                className="px-4 py-2 rounded-xl glass-strong text-sm"
              >
                Cancelar
              </button>
              <button
                type="button"
                disabled={joinMut.isPending || joinCode.trim().length < 4}
                onClick={() => joinMut.mutate(joinCode)}
                className="px-4 py-2 rounded-xl bg-gradient-ember text-primary-foreground text-sm font-medium"
              >
                Unirse
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="px-8 lg:px-12 pb-16 -mt-4">
        <div className="flex items-end justify-between mb-5">
          <div>
            <h2 className="font-display text-2xl font-semibold">Tus mesas</h2>
            <p className="text-sm text-muted-foreground">Campañas en las que participas.</p>
          </div>
        </div>

        {isLoading && (
          <p className="text-sm text-muted-foreground py-8">Cargando mesas…</p>
        )}

        {!isLoading && campaigns.length === 0 && (
          <div className="glass rounded-2xl p-10 text-center">
            <p className="text-muted-foreground">Aún no tienes mesas. Crea una o únete con un código.</p>
          </div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
          {campaigns.map((c, i) => {
            const isOwner = me?.id === c.owner_id;
            return (
              <div
                key={c.id}
                className="group glass rounded-2xl overflow-hidden hover:border-primary/40 transition-all hover:-translate-y-0.5 relative"
              >
                {isOwner && (
                  <button
                    type="button"
                    title="Eliminar mesa"
                    disabled={deleteMut.isPending}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      if (
                        !window.confirm(
                          `¿Eliminar la mesa «${c.name}»? Se borrarán mensajes, documentos, RAG y archivos. No se puede deshacer.`
                        )
                      ) {
                        return;
                      }
                      deleteMut.mutate(c.id);
                    }}
                    className="absolute top-3 right-3 z-20 h-9 w-9 rounded-lg bg-destructive/90 text-destructive-foreground grid place-items-center hover:bg-destructive border border-border/50 shadow-lg opacity-90 hover:opacity-100 disabled:opacity-40"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                )}
                <Link
                  to="/campaign/$campaignId/play"
                  params={{ campaignId: c.id }}
                  className="block"
                >
                  <div className="relative h-44 overflow-hidden">
                    <CampaignCoverImage
                      campaignId={c.id}
                      hasCover={!!c.cover_image}
                      fallbackSrc={imgs[i % imgs.length]}
                      alt={c.name}
                      width={768}
                      height={512}
                      className="h-full w-full object-cover group-hover:scale-105 transition-transform duration-700"
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-card via-card/40 to-transparent" />
                    <div className="absolute top-3 left-3 flex gap-2 flex-wrap pr-12">
                      {c.game_system && (
                        <span className="text-[10px] uppercase tracking-wider px-2 py-1 rounded-md glass-strong font-medium">
                          {c.game_system}
                        </span>
                      )}
                      <span
                        className={`text-[10px] uppercase tracking-wider px-2 py-1 rounded-md font-medium ${
                          c.status === "active"
                            ? "bg-success/20 text-success"
                            : "bg-muted text-muted-foreground"
                        }`}
                      >
                        {c.status === "active" ? "Activa" : "Lobby"}
                      </span>
                    </div>
                  </div>
                  <div className="p-5">
                    <h3 className="font-display text-lg font-semibold leading-tight">{c.name}</h3>
                    <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                      <span className="inline-flex items-center gap-1">
                        <Users className="h-3 w-3" />
                        {c.member_count} jugadores
                      </span>
                      <span className="inline-flex items-center gap-1">
                        <Clock className="h-3 w-3" />
                        código {c.invite_code}
                      </span>
                    </div>
                    {c.last_narrative_preview && (
                      <div className="mt-4 p-3 rounded-lg bg-muted/40 border-l-2 border-primary text-sm italic text-muted-foreground line-clamp-3">
                        &ldquo;{c.last_narrative_preview}&rdquo;
                      </div>
                    )}
                  </div>
                </Link>
              </div>
            );
          })}
        </div>

        <div className="mt-12 grid grid-cols-1 lg:grid-cols-3 gap-5">
          <div className="lg:col-span-2 glass rounded-2xl p-6">
            <div className="flex items-center justify-between mb-5">
              <h2 className="font-display text-xl font-semibold">Actividad reciente</h2>
              <span className="text-xs text-muted-foreground">Resumen</span>
            </div>
            <ul className="space-y-1">
              {campaigns.slice(0, 4).map((c) => (
                <li key={c.id} className="flex items-start gap-4 p-3 rounded-lg hover:bg-muted/40 transition">
                  <div className="h-9 w-9 rounded-lg bg-gradient-ember/20 ember-border grid place-items-center shrink-0">
                    <Sparkles className="h-4 w-4 text-primary" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium">{c.name}</p>
                    <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                      {c.last_narrative_preview || "Sin mensajes aún. Entra a la mesa y escribe la primera acción."}
                    </p>
                  </div>
                </li>
              ))}
              {campaigns.length === 0 && (
                <li className="text-sm text-muted-foreground p-3">No hay actividad reciente.</li>
              )}
            </ul>
          </div>

          <div className="glass rounded-2xl p-6 relative overflow-hidden">
            <div className="absolute -top-12 -right-12 h-40 w-40 rounded-full bg-gradient-ember opacity-20 blur-3xl" />
            <h3 className="font-display text-lg font-semibold">Tu perfil</h3>
            <p className="text-xs text-muted-foreground mt-1">{me?.email}</p>
            <div className="mt-5 space-y-4">
              <div className="flex items-baseline justify-between">
                <span className="text-sm text-muted-foreground">Mesas</span>
                <span className="font-display font-bold text-2xl text-gradient-ember">{campaigns.length}</span>
              </div>
              <div className="flex items-baseline justify-between">
                <span className="text-sm text-muted-foreground">En juego</span>
                <span className="font-display font-bold text-2xl text-gradient-ember">
                  {campaigns.filter((x) => x.status === "active").length}
                </span>
              </div>
              <Link
                to="/profile"
                className="inline-flex items-center gap-1 text-sm text-primary hover:underline mt-2"
              >
                Ver perfil <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
