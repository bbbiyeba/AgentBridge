import { useEffect, useState } from "react";

// The integrations run on the Flask backend (it holds the API credentials;
// this static site can't). Override with VITE_API_BASE for local dev, e.g.
// VITE_API_BASE=http://127.0.0.1:5050 pnpm dev
export const API_BASE = (import.meta.env.VITE_API_BASE || "https://agent-bridge-one.vercel.app").replace(/\/$/, "");

export type IntegrationStatus = {
  gmail?: { configured: boolean };
  drive?: { configured: boolean; files?: string[] };
  figma?: { configured: boolean; embed_url?: string };
  github?: { configured: boolean; username?: string };
  calendly?: { configured: boolean; url?: string };
};

export type FigmaImage = { id: string; name: string; url: string };

export type GitHubStats = {
  profile: { login: string; name: string | null; avatar_url: string; html_url: string };
  totals: { public_repos: number; stars: number; followers: number; contributions_last_year: number | null };
  languages: { name: string; repos: number }[];
  featured: {
    name: string;
    description: string | null;
    url: string;
    stars: number;
    forks: number;
    language: string | null;
    pushed_at: string | null;
  }[];
};

/** status is the HTTP status, or 0 when the server couldn't be reached at all. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }

  /** The server is down or broken, as opposed to rejecting what was sent. */
  get isServerFailure() {
    return this.status === 0 || this.status >= 500;
  }
}

// Without a timeout, a backend that accepts the connection and then hangs
// keeps every feature (and the contact form) waiting for minutes.
const STATUS_TIMEOUT_MS = 5000;
const REQUEST_TIMEOUT_MS = 15000;

let statusPromise: Promise<IntegrationStatus> | null = null;

// One shared request: every component that asks gets the same promise. A
// failure resolves to {} so features stay hidden (and the contact form
// falls back) instead of breaking the page -- but it isn't cached, so the
// next caller (e.g. a later contact form submit) tries again.
export function getIntegrations(): Promise<IntegrationStatus> {
  statusPromise ??= fetch(`${API_BASE}/api/integrations`, { signal: AbortSignal.timeout(STATUS_TIMEOUT_MS) })
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((d) => (d.integrations ?? {}) as IntegrationStatus)
    .catch((err) => {
      console.warn("Couldn't load site integrations; their features stay hidden.", err);
      statusPromise = null;
      return {};
    });
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
    res = await fetch(`${API_BASE}/api/integrations${path}`, {
      ...init,
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
  } catch {
    throw new ApiError("Could not reach the server. Try again in a bit.", 0);
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) throw new ApiError(data.error || "Something went wrong.", res.ok ? 502 : res.status);
  return data as T;
}

export function sendContact(body: { name: string; email: string; message: string; website: string }) {
  return api<{ sent: boolean }>("/gmail/contact", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Shape checks for the fields the UI dereferences, so a backend change
// becomes a hidden section rather than a render crash.
export async function fetchFigmaImages() {
  const d = await api<{ images: FigmaImage[]; file: { name?: string; lastModified?: string }; embed_url: string }>(
    "/figma/images",
  );
  if (!Array.isArray(d.images)) throw new ApiError("Unexpected response from the server.", 502);
  return d;
}

export async function fetchGitHubStats() {
  const d = await api<GitHubStats>("/github/stats");
  if (!d.totals || !Array.isArray(d.featured) || !Array.isArray(d.languages) || !d.profile) {
    throw new ApiError("Unexpected response from the server.", 502);
  }
  return d;
}

// Calendly's inline-embed parameters (the same ones its widget.js adds),
// themed to match the site. Colors only apply on paid Calendly plans.
export function calendlyEmbedUrl(url: string) {
  const params = new URLSearchParams({
    embed_domain: window.location.hostname,
    embed_type: "Inline",
    hide_gdpr_banner: "1",
    background_color: "111116",
    text_color: "e8e8f0",
    primary_color: "6c6cff",
  });
  return `${url}?${params}`;
}

export function driveFileUrl(alias: string, download = false) {
  return `${API_BASE}/api/integrations/drive/files/${encodeURIComponent(alias)}${download ? "?download=1" : ""}`;
}
