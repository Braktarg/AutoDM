import { Link, useNavigate, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  Home,
  Plus,
  Swords,
  ScrollText,
  BookOpen,
  Users,
  User,
  Flame,
  LogOut,
} from "lucide-react";
import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import { clearToken, getLastCampaignId, getToken } from "@/lib/auth";
import type { UserOut } from "@/lib/types";

export function AppSidebar() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const navigate = useNavigate();
  const [campaignId, setCampaignId] = useState<string | null>(null);

  useEffect(() => {
    const m = path.match(/^\/campaign\/([^/]+)/);
    if (m?.[1]) {
      setCampaignId(m[1]);
      return;
    }
    setCampaignId(getLastCampaignId());
  }, [path]);

  const authed = typeof window !== "undefined" && !!getToken();

  const { data: me } = useQuery({
    queryKey: ["me"],
    queryFn: () => apiFetch<UserOut>("/api/auth/me"),
    enabled: authed,
    retry: false,
  });

  const baseItems: { title: string; to: string; icon: typeof Home; match: (p: string) => boolean }[] = [
    { title: "Dashboard", to: "/", icon: Home, match: (p) => p === "/" },
    { title: "Crear mesa", to: "/create", icon: Plus, match: (p) => p === "/create" },
    { title: "Perfil", to: "/profile", icon: User, match: (p) => p === "/profile" },
  ];

  const campaignPaths = {
    play: "/campaign/$campaignId/play",
    lobby: "/campaign/$campaignId/lobby",
    character: "/campaign/$campaignId/character",
    rules: "/campaign/$campaignId/rules",
  } as const;

  const campaignItems =
    campaignId != null
      ? ([
          { title: "Mesa activa", key: "play" as const, icon: Swords },
          { title: "Lobby", key: "lobby" as const, icon: Users },
          { title: "Personaje", key: "character" as const, icon: ScrollText },
          { title: "Reglas", key: "rules" as const, icon: BookOpen },
        ] as const)
      : [];

  function logout() {
    clearToken();
    navigate({ to: "/login" });
  }

  const initial = me?.display_name?.[0]?.toUpperCase() ?? "?";

  return (
    <aside className="hidden md:flex w-60 shrink-0 flex-col glass-strong border-r border-border h-screen sticky top-0">
      <div className="px-5 py-5 flex items-center gap-2.5 border-b border-border">
        <div className="relative h-9 w-9 rounded-xl bg-gradient-ember grid place-items-center ember-glow">
          <Flame className="h-5 w-5 text-primary-foreground" strokeWidth={2.5} />
        </div>
        <div className="leading-tight">
          <div className="font-display font-bold text-base tracking-tight">AutoDM</div>
          <div className="text-[10px] uppercase tracking-widest text-muted-foreground">AI Dungeon Master</div>
        </div>
      </div>

      <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
        <div className="px-2 py-2 text-[10px] uppercase tracking-widest text-muted-foreground">Principal</div>
        {baseItems.map((item) => {
          const active = item.match(path);
          return (
            <Link
              key={item.to}
              to={item.to}
              className={`group flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all ${
                active
                  ? "bg-gradient-ember text-primary-foreground font-medium ember-glow"
                  : "text-muted-foreground hover:text-foreground hover:bg-muted"
              }`}
            >
              <item.icon className="h-4 w-4" strokeWidth={2} />
              <span>{item.title}</span>
            </Link>
          );
        })}

        {campaignItems.length > 0 && (
          <>
            <div className="px-2 py-3 mt-2 text-[10px] uppercase tracking-widest text-muted-foreground">
              Mesa actual
            </div>
            {campaignItems.map((item) => {
              const routePath = campaignPaths[item.key];
              const href = `/campaign/${campaignId}/${item.key}`;
              const active = path === href;
              return (
                <Link
                  key={item.key}
                  to={routePath}
                  params={{ campaignId }}
                  className={`group flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all ${
                    active
                      ? "bg-gradient-ember text-primary-foreground font-medium ember-glow"
                      : "text-muted-foreground hover:text-foreground hover:bg-muted"
                  }`}
                >
                  <item.icon className="h-4 w-4" strokeWidth={2} />
                  <span>{item.title}</span>
                </Link>
              );
            })}
          </>
        )}

        {campaignId == null && (
          <p className="px-3 py-2 text-[11px] text-muted-foreground leading-snug">
            Abre una mesa desde el dashboard para ver accesos directos.
          </p>
        )}
      </nav>

      <div className="p-3 border-t border-border">
        <div className="glass rounded-xl p-3 flex items-center gap-3">
          <div className="h-9 w-9 rounded-full bg-gradient-ember grid place-items-center text-primary-foreground font-display font-bold text-sm">
            {initial}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-medium truncate">{me?.display_name ?? "Sesión"}</div>
            <div className="text-xs text-muted-foreground truncate">{me?.email ?? ""}</div>
          </div>
          <button
            type="button"
            onClick={logout}
            className="h-8 w-8 rounded-lg hover:bg-muted grid place-items-center text-muted-foreground hover:text-foreground shrink-0"
            title="Salir"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>
      </div>
    </aside>
  );
}
