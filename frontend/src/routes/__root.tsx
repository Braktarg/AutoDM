import { Outlet, Link, createRootRoute, HeadContent, Scripts, useRouterState } from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { AppSidebar } from "@/components/AppSidebar";
import { queryClient } from "@/lib/queryClient";
import appCss from "../styles.css?url";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center px-4">
      <div className="max-w-md text-center glass rounded-2xl p-10">
        <div className="text-7xl font-display font-bold text-gradient-ember">404</div>
        <h2 className="mt-4 text-xl font-semibold">Esta sala no existe en el mapa</h2>
        <p className="mt-2 text-sm text-muted-foreground">El DM no tiene registros de este lugar.</p>
        <Link to="/" className="mt-6 inline-flex items-center justify-center rounded-lg bg-gradient-ember px-5 py-2.5 text-sm font-medium text-primary-foreground ember-glow">
          Volver al campamento
        </Link>
      </div>
    </div>
  );
}

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "AutoDM — AI Dungeon Master para tus campañas" },
      { name: "description", content: "Plataforma de juego de rol con DM impulsado por IA. Mundos vivos, personajes persistentes, narrativa dinámica." },
      { name: "theme-color", content: "#0d0d0d" },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      { rel: "preconnect", href: "https://fonts.gstatic.com", crossOrigin: "anonymous" },
      { rel: "stylesheet", href: "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
});

function RootShell({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es" className="dark">
      <head><HeadContent /></head>
      <body>{children}<Scripts /></body>
    </html>
  );
}

function RootComponent() {
  const path = useRouterState({ select: (s) => s.location.pathname });
  const authOnly = path === "/login" || path === "/register";

  return (
    <QueryClientProvider client={queryClient}>
      {authOnly ? (
        <Outlet />
      ) : (
        <div className="flex min-h-screen w-full">
          <AppSidebar />
          <main className="flex-1 min-w-0">
            <Outlet />
          </main>
        </div>
      )}
    </QueryClientProvider>
  );
}
