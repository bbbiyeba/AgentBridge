import { useEffect, useState } from "react";

// The integrations run on the Flask backend (it holds the API credentials;
// this static site can't). Override with VITE_API_BASE for local dev, e.g.
// VITE_API_BASE=http://127.0.0.1:5050 pnpm dev
export const API_BASE = (import.meta.env.VITE_API_BASE || "https://agent-bridge-one.vercel.app").replace(/\/$/, "");

export type IntegrationStatus = {
  gmail?: { configured: boolean };
  drive?: { configured: boolean; files?: string[] };
  figma?: { configured: boolean; embed_url?: string };
};

export type FigmaImage = { id: string; name: string; url: string };

export class ApiError extends Error {}

let statusPromise: Promise<IntegrationStatus> | null = null;

// One shared request per page load: every component that asks gets the
// same promise. Any failure resolves to {} so features just stay hidden
// (and the contact form falls back) instead of breaking the page.
export function getIntegrations(): Promise<IntegrationStatus> {
  statusPromise ??= fetch(`${API_BASE}/api/integrations`)
    .then((r) => (r.ok ? r.json() : { integrations: {} }))
    .then((d) => (d.integrations ?? {}) as IntegrationStatus)
    .catch(() => ({}));
  return statusPromise;
}

/** null while loading, then the live status of every integration. */
export function useIntegrations(): IntegrationStatus | null {
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  useEffect(() => {
    let alive = true;
    getIntegrations().then((s) => alive && setStatus(s));
    return () => {
      alive = false;
    };
  }, []);
  return status;
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api/integrations${path}`, init);
  } catch {
    throw new ApiError("Could not reach the server. Try again in a bit.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new ApiError(data.error || "Something went wrong.");
  return data as T;
}

export function sendContact(body: { name: string; email: string; message: string; website: string }) {
  return api<{ sent: boolean }>("/gmail/contact", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function fetchFigmaImages() {
  return api<{ images: FigmaImage[]; file: { name?: string; lastModified?: string }; embed_url: string }>(
    "/figma/images",
  );
}

export function driveFileUrl(alias: string, download = false) {
  return `${API_BASE}/api/integrations/drive/files/${encodeURIComponent(alias)}${download ? "?download=1" : ""}`;
}
