const KEY = "autodm_token";
const LAST_CAMP = "autodm_last_campaign";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(KEY);
}

export function setToken(t: string): void {
  localStorage.setItem(KEY, t);
}

export function clearToken(): void {
  localStorage.removeItem(KEY);
}

export function getLastCampaignId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(LAST_CAMP);
}

export function setLastCampaignId(id: string): void {
  localStorage.setItem(LAST_CAMP, id);
}
