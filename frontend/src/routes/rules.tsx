import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";
import { getLastCampaignId } from "@/lib/auth";

export const Route = createFileRoute("/rules")({
  component: RedirectRules,
});

function RedirectRules() {
  const navigate = useNavigate();
  useEffect(() => {
    const id = getLastCampaignId();
    if (id) {
      navigate({ to: "/campaign/$campaignId/rules", params: { campaignId: id } });
    } else {
      navigate({ to: "/" });
    }
  }, [navigate]);
  return <div className="p-8 text-sm text-muted-foreground">Redirigiendo…</div>;
}
