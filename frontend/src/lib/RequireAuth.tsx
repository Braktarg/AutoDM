import { useNavigate } from "@tanstack/react-router";
import { useEffect, useState } from "react";
import { getToken } from "./auth";

export function RequireAuth({ children }: { children: React.ReactNode }) {
  const navigate = useNavigate();
  const [ok, setOk] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      navigate({ to: "/login" });
      return;
    }
    setOk(true);
  }, [navigate]);

  if (!ok) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center px-4">
        <p className="text-sm text-muted-foreground">Cargando sesión…</p>
      </div>
    );
  }

  return <>{children}</>;
}
