import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload, FileText, Sparkles, Backpack, Trash2, Plus } from "lucide-react";
import { apiFetch, apiUpload } from "@/lib/api";
import { setLastCampaignId } from "@/lib/auth";
import type { CampaignDocument, CampaignOut, InventoryItem } from "@/lib/types";
import { RequireAuth } from "@/lib/RequireAuth";

export const Route = createFileRoute("/campaign/$campaignId/character")({
  head: () => ({
    meta: [
      { title: "Ficha — AutoDM" },
      { name: "description", content: "Sube tu ficha PDF para el contexto del DM." },
    ],
  }),
  component: CharacterRoute,
});

function CharacterRoute() {
  return (
    <RequireAuth>
      <CharacterPage />
    </RequireAuth>
  );
}

function CharacterPage() {
  const { campaignId } = Route.useParams();
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setLastCampaignId(campaignId);
  }, [campaignId]);

  const { data: campaign } = useQuery({
    queryKey: ["campaign", campaignId],
    queryFn: () => apiFetch<CampaignOut>(`/api/campaigns/${campaignId}`),
  });

  const { data: docs = [] } = useQuery({
    queryKey: ["documents", campaignId],
    queryFn: () => apiFetch<CampaignDocument[]>(`/api/campaigns/${campaignId}/documents`),
  });

  const { data: myInventory = [], isLoading: invLoading } = useQuery({
    queryKey: ["inventory-me", campaignId],
    queryFn: () => apiFetch<InventoryItem[]>(`/api/campaigns/${campaignId}/inventory/me`),
  });

  const sheets = docs.filter((d) => d.kind === "character_sheet");

  const [newName, setNewName] = useState("");
  const [newQty, setNewQty] = useState("1");
  const [newCategory, setNewCategory] = useState("");
  const [newNotes, setNewNotes] = useState("");

  const addItemMut = useMutation({
    mutationFn: async () => {
      const q = parseInt(String(newQty), 10);
      const qty = Number.isFinite(q) && q >= 1 ? q : 1;
      await apiFetch<InventoryItem>(`/api/campaigns/${campaignId}/inventory/me`, {
        method: "POST",
        body: JSON.stringify({
          name: newName.trim(),
          quantity: qty,
          category: newCategory.trim() || null,
          notes: newNotes.trim() || null,
        }),
      });
    },
    onSuccess: () => {
      setNewName("");
      setNewQty("1");
      setNewCategory("");
      setNewNotes("");
      queryClient.invalidateQueries({ queryKey: ["inventory-me", campaignId] });
      queryClient.invalidateQueries({ queryKey: ["party-inventory", campaignId] });
    },
  });

  const patchQtyMut = useMutation({
    mutationFn: async (p: { id: string; quantity: number }) => {
      await apiFetch(`/api/campaigns/${campaignId}/inventory/me/${p.id}`, {
        method: "PATCH",
        body: JSON.stringify({ quantity: p.quantity }),
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inventory-me", campaignId] });
      queryClient.invalidateQueries({ queryKey: ["party-inventory", campaignId] });
    },
  });

  const deleteItemMut = useMutation({
    mutationFn: async (id: string) => {
      await apiFetch(`/api/campaigns/${campaignId}/inventory/me/${id}`, {
        method: "DELETE",
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["inventory-me", campaignId] });
      queryClient.invalidateQueries({ queryKey: ["party-inventory", campaignId] });
    },
  });

  async function onUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (!f) return;
    setBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      await apiUpload(`/api/campaigns/${campaignId}/documents/character-sheet`, fd, null);
      await queryClient.invalidateQueries({ queryKey: ["documents", campaignId] });
    } finally {
      setBusy(false);
      e.target.value = "";
    }
  }

  return (
    <div className="px-8 lg:px-12 py-10 max-w-4xl mx-auto">
      <Link
        to="/campaign/$campaignId/play"
        params={{ campaignId }}
        className="text-xs text-muted-foreground hover:text-foreground"
      >
        ← Volver a la mesa
      </Link>

      <div className="flex flex-wrap items-end justify-between gap-4 mb-8 mt-4">
        <div>
          <div className="text-xs uppercase tracking-widest text-muted-foreground">{campaign?.name}</div>
          <h1 className="font-display text-4xl font-bold mt-1">Ficha de personaje</h1>
          <p className="text-muted-foreground">
            Sube un PDF de ficha; el DM lo usa como contexto en la mesa (RAG por tipo documento).
          </p>
        </div>
        <div>
          <input ref={fileRef} type="file" accept="application/pdf" className="hidden" onChange={onUpload} />
          <button
            type="button"
            disabled={busy}
            onClick={() => fileRef.current?.click()}
            className="px-4 py-2.5 rounded-xl bg-gradient-ember text-primary-foreground font-medium text-sm ember-glow inline-flex items-center gap-2 disabled:opacity-50"
          >
            <Upload className="h-4 w-4" /> {busy ? "Subiendo…" : "Subir ficha PDF"}
          </button>
        </div>
      </div>

      <div className="glass rounded-2xl p-6 space-y-4 mb-8">
        <h2 className="font-display text-lg font-semibold inline-flex items-center gap-2">
          <Backpack className="h-4 w-4 text-primary" /> Mi inventario
        </h2>
        <p className="text-sm text-muted-foreground">
          Objetos que lleva tu personaje. El AutoDM ve el inventario de toda la mesa en cada turno; aquí solo editas los tuyos.
        </p>
        {invLoading && <p className="text-xs text-muted-foreground">Cargando…</p>}
        {!invLoading && myInventory.length === 0 && (
          <p className="text-sm text-muted-foreground">Aún no tienes objetos registrados.</p>
        )}
        <ul className="space-y-2">
          {myInventory.map((it) => (
            <li
              key={it.id}
              className="flex flex-wrap items-center gap-2 p-3 rounded-xl bg-muted/30 border border-border text-sm"
            >
              <div className="flex-1 min-w-[140px]">
                <span className="font-medium">{it.name}</span>
                {it.category && (
                  <span className="text-[10px] uppercase text-muted-foreground ml-2">({it.category})</span>
                )}
                {it.notes && <p className="text-xs text-muted-foreground mt-0.5">{it.notes}</p>}
              </div>
              <label className="flex items-center gap-1 text-xs text-muted-foreground">
                Cant.
                <input
                  type="number"
                  min={0}
                  className="w-16 rounded border border-border bg-background px-2 py-1 text-xs"
                  defaultValue={it.quantity}
                  key={`${it.id}-${it.updated_at}`}
                  onBlur={(ev) => {
                    const v = parseInt(ev.target.value, 10);
                    const q = Number.isFinite(v) ? Math.max(0, v) : it.quantity;
                    if (q !== it.quantity) {
                      patchQtyMut.mutate({ id: it.id, quantity: q });
                    }
                  }}
                />
              </label>
              <button
                type="button"
                aria-label={`Eliminar ${it.name}`}
                onClick={() => deleteItemMut.mutate(it.id)}
                className="p-2 rounded-lg text-destructive hover:bg-destructive/10 shrink-0"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
        <div className="border-t border-border pt-4 space-y-2">
          <div className="text-[10px] uppercase tracking-widest text-muted-foreground flex items-center gap-1">
            <Plus className="h-3 w-3" /> Añadir objeto
          </div>
          <div className="grid sm:grid-cols-2 gap-2">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Nombre (ej. Antorcha)"
              className="rounded-xl border border-border bg-background px-3 py-2 text-sm sm:col-span-2"
            />
            <input
              type="number"
              min={1}
              value={newQty}
              onChange={(e) => setNewQty(e.target.value)}
              placeholder="Cantidad"
              className="rounded-xl border border-border bg-background px-3 py-2 text-sm"
            />
            <input
              value={newCategory}
              onChange={(e) => setNewCategory(e.target.value)}
              placeholder="Categoría (opc.)"
              className="rounded-xl border border-border bg-background px-3 py-2 text-sm"
            />
            <textarea
              value={newNotes}
              onChange={(e) => setNewNotes(e.target.value)}
              placeholder="Notas (opc.)"
              rows={2}
              className="rounded-xl border border-border bg-background px-3 py-2 text-sm sm:col-span-2 resize-none"
            />
          </div>
          <button
            type="button"
            disabled={!newName.trim() || addItemMut.isPending}
            onClick={() => addItemMut.mutate()}
            className="px-4 py-2 rounded-xl bg-muted border border-border text-sm font-medium hover:bg-muted/80 disabled:opacity-50"
          >
            {addItemMut.isPending ? "Guardando…" : "Guardar en inventario"}
          </button>
        </div>
      </div>

      <div className="glass rounded-2xl p-6 space-y-4">
        <h2 className="font-display text-lg font-semibold inline-flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" /> Fichas en esta mesa
        </h2>
        {sheets.length === 0 && (
          <p className="text-sm text-muted-foreground">Aún no hay fichas subidas.</p>
        )}
        <ul className="space-y-2">
          {sheets.map((d) => (
            <li key={d.id} className="flex items-center gap-3 p-3 rounded-xl bg-muted/30 border border-border">
              <FileText className="h-4 w-4 text-primary shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">{d.filename}</div>
                <div className="text-xs text-muted-foreground">
                  {d.meta?.chunks != null ? `${d.meta.chunks} fragmentos indexados` : "—"}
                  {d.meta?.rag_error && (
                    <span className="text-destructive ml-2">{d.meta.rag_error}</span>
                  )}
                </div>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <p className="text-xs text-muted-foreground mt-6">
        Inventario y ficha PDF se combinan en el contexto del DM: el inventario es lista viva editable; la ficha sigue siendo contexto narrativo/mechanic vía RAG.
      </p>
    </div>
  );
}
