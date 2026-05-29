import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Settings, Award, Calendar, Flame } from "lucide-react";
import { apiFetch } from "@/lib/api";
import type { CampaignSummary, UserOut } from "@/lib/types";
import { RequireAuth } from "@/lib/RequireAuth";

export const Route = createFileRoute("/profile")({
  head: () => ({
    meta: [
      { title: "Perfil — AutoDM" },
      { name: "description", content: "Tu cuenta y mesas en AutoDM." },
    ],
  }),
  component: ProfileRoute,
});

function ProfileRoute() {
  return (
    <RequireAuth>
      <Profile />
    </RequireAuth>
  );
}

function Profile() {
  const { data: me } = useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<UserOut>("/api/auth/me"),
  });

  const { data: campaigns = [] } = useQuery({
    queryKey: ["campaigns"],
    queryFn: () => apiFetch<CampaignSummary[]>("/api/campaigns"),
  });

  const joined = me?.created_at
    ? new Date(me.created_at).toLocaleDateString("es", { month: "long", year: "numeric" })
    : "—";
  const initial = me?.display_name?.[0]?.toUpperCase() ?? "?";

  return (
    <div className="px-8 lg:px-12 py-10 max-w-6xl mx-auto">
      <div className="glass rounded-3xl overflow-hidden mb-8 relative">
        <div className="h-40 bg-gradient-to-br from-primary/40 via-accent/30 to-info/20 relative">
          <div className="absolute inset-0 scanline-bg opacity-50" />
        </div>
        <div className="px-8 pb-8 -mt-12 relative">
          <div className="flex items-end gap-6 flex-wrap">
            <div className="h-28 w-28 rounded-3xl bg-gradient-ember grid place-items-center text-primary-foreground font-display font-bold text-5xl ember-glow ring-4 ring-card">
              {initial}
            </div>
            <div className="flex-1 min-w-0 pb-1">
              <h1 className="font-display text-3xl font-bold">{me?.display_name ?? "…"}</h1>
              <p className="text-muted-foreground">
                {me?.email} · se unió en {joined}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Tag>Jugador</Tag>
                <Tag tone="accent">AutoDM</Tag>
              </div>
            </div>
            <button
              type="button"
              className="px-4 py-2.5 rounded-xl glass-strong font-medium text-sm inline-flex items-center gap-2 opacity-60 cursor-not-allowed"
              title="Próximamente"
            >
              <Settings className="h-4 w-4" /> Ajustes
            </button>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
        {[
          { label: "Mesas", value: String(campaigns.length), icon: Flame },
          { label: "Activas", value: String(campaigns.filter((c) => c.status === "active").length), icon: Award },
          { label: "En lobby", value: String(campaigns.filter((c) => c.status === "lobby").length), icon: Calendar },
          { label: "Cuenta", value: "OK", icon: Flame },
        ].map((s) => (
          <div key={s.label} className="glass rounded-2xl p-5">
            <s.icon className="h-5 w-5 text-primary mb-2" />
            <div className="font-display text-3xl font-bold text-gradient-ember">{s.value}</div>
            <div className="text-xs text-muted-foreground mt-0.5">{s.label}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="glass rounded-2xl p-6">
          <h2 className="font-display text-lg font-semibold mb-4">Tus mesas</h2>
          <div className="space-y-2">
            {campaigns.map((c) => (
              <Link
                key={c.id}
                to="/campaign/$campaignId/play"
                params={{ campaignId: c.id }}
                className="p-3 rounded-xl bg-muted/30 border border-border hover:border-primary/40 transition flex items-center gap-3"
              >
                <div className="h-10 w-10 rounded-xl bg-gradient-to-br from-primary to-accent grid place-items-center text-primary-foreground font-bold">
                  {c.name[0]?.toUpperCase()}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium truncate">{c.name}</div>
                  <div className="text-xs text-muted-foreground truncate">
                    {c.status === "active" ? "En juego" : "Lobby"} · {c.member_count} jugadores
                  </div>
                </div>
              </Link>
            ))}
            {campaigns.length === 0 && (
              <p className="text-sm text-muted-foreground">No tienes mesas todavía.</p>
            )}
          </div>
        </div>

        <div className="glass rounded-2xl p-6">
          <h2 className="font-display text-lg font-semibold mb-4">Historial</h2>
          <p className="text-sm text-muted-foreground">
            El historial de sesiones por campaña llegará en una versión posterior del backend; por ahora usa el dashboard
            para ver tus mesas activas.
          </p>
        </div>
      </div>
    </div>
  );
}

function Tag({ children, tone }: { children: React.ReactNode; tone?: "accent" | "info" }) {
  const cls =
    tone === "accent"
      ? "bg-accent/20 text-accent"
      : tone === "info"
        ? "bg-info/20 text-info"
        : "bg-primary/20 text-primary";
  return (
    <span className={`text-[10px] uppercase tracking-widest px-2 py-1 rounded-md font-medium ${cls}`}>
      {children}
    </span>
  );
}
