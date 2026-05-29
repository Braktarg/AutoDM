import { getToken } from "./auth";

function baseUrl(): string {
  return (
    (typeof import.meta !== "undefined" && import.meta.env?.VITE_API_URL) ||
    "http://127.0.0.1:8000"
  ).replace(/\/$/, "");
}

export function apiUrl(path: string): string {
  const p = path.startsWith("/") ? path : `/${path}`;
  return `${baseUrl()}${p}`;
}

function explainFetchError(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  if (
    msg === "Failed to fetch" ||
    (err instanceof TypeError && /network|fetch|load failed/i.test(msg))
  ) {
    return `Sin conexión con la API (${baseUrl()}). Arranca el backend FastAPI en el puerto 8000.`;
  }
  return msg;
}

function formatDetail(text: string): string {
  const t = text.trim();
  if (!t) return "Error";
  try {
    const j = JSON.parse(t) as { detail?: unknown };
    if (j.detail == null) return t;
    if (typeof j.detail === "string") return j.detail;
    if (Array.isArray(j.detail)) {
      return j.detail
        .map((x) =>
          typeof x === "object" && x && "msg" in x
            ? String((x as { msg: string }).msg)
            : String(x)
        )
        .join(" · ");
    }
    return String(j.detail);
  } catch {
    return t;
  }
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { token?: string | null } = {}
): Promise<T> {
  const { token, headers, ...rest } = options;
  const h = new Headers(headers);
  const tok = token ?? getToken();
  if (!(rest.body instanceof FormData)) {
    h.set("Content-Type", "application/json");
  }
  if (tok) {
    h.set("Authorization", `Bearer ${tok}`);
  }
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { ...rest, headers: h });
  } catch (e) {
    throw new Error(explainFetchError(e));
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(formatDetail(text) || res.statusText);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}

export async function apiUpload(
  path: string,
  formData: FormData,
  token: string | null
): Promise<unknown> {
  const t = token ?? getToken();
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      method: "POST",
      headers: t ? { Authorization: `Bearer ${t}` } : {},
      body: formData,
    });
  } catch (e) {
    throw new Error(explainFetchError(e));
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(formatDetail(text) || res.statusText);
  }
  return res.json();
}
