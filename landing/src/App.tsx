import { Component, useState, useEffect, useRef, useSyncExternalStore } from "react";
import type { CSSProperties, FormEvent, ReactNode } from "react";
import { Analytics } from "@vercel/analytics/react";
import {
  ApiError,
  calendlyEmbedUrl,
  driveFileUrl,
  fetchFigmaImages,
  fetchGitHubStats,
  getIntegrations,
  sendContact,
  useIntegrations,
} from "./integrations";
import type { FigmaImage, GitHubStats } from "./integrations";

const AGENTS = [
  {
    id: "claude",
    name: "Claude",
    vendor: "Anthropic",
    model: "claude-sonnet-5",
    color: "#6c6cff",
    colorDim: "#6c6cff18",
    icon: "◆",
    status: "ready",
  },
  {
    id: "gpt",
    name: "ChatGPT",
    vendor: "OpenAI",
    model: "gpt-5",
    color: "#3ddc84",
    colorDim: "#3ddc8418",
    icon: "●",
    status: "ready",
  },
  {
    id: "llama",
    name: "Llama 3",
    vendor: "Ollama · Local",
    model: "llama3",
    color: "#ff8c42",
    colorDim: "#ff8c4218",
    icon: "▲",
    status: "ready",
  },
];

const NAV_ITEMS = [
  { label: "Features", href: "#features" },
  { label: "Agents", href: "#agents" },
  { label: "Workflow", href: "#workflow" },
  { label: "Contact", href: "#contact" },
  { label: "Docs", href: "https://github.com/bbbiyeba/AgentBridge#readme", external: true },
];

const FEATURES = [
  {
    tag: "01 / orchestration",
    title: "Turn-based agent scheduling",
    body: "Define which agents run, in which order, and when they hand off. Agents read each other's output — no copy-pasting between tabs.",
  },
  {
    tag: "02 / local-first",
    title: "Runs entirely on your machine",
    body: "No cloud orchestration layer. Your code never leaves your filesystem. Ollama models run air-gapped; remote models go direct to their APIs.",
  },
  {
    tag: "03 / shared context",
    title: "One codebase, all agents",
    body: "Every agent reads and writes the same workspace/ directory and a shared mailboard.json ledger. Changes one agent makes are visible to the next immediately.",
  },
  {
    tag: "04 / composable",
    title: "Mix models by task type",
    body: "Route architecture decisions to Claude, boilerplate to a fast local model, and code review to ChatGPT. Combine strengths across vendors.",
  },
];

const LOG_LINES = [
  { time: "09:14:02", agent: "claude", icon: "◆", color: "#6c6cff", text: "Analysing codebase structure..." },
  { time: "09:14:05", agent: "claude", icon: "◆", color: "#6c6cff", text: "Found 3 components with prop-drilling issues" },
  { time: "09:14:05", agent: "claude", icon: "◆", color: "#6c6cff", text: "Proposing context refactor in src/store.ts" },
  { time: "09:14:07", agent: "system", icon: "→", color: "#8686b4", text: "Handing off to reviewer (ChatGPT)" },
  { time: "09:14:08", agent: "gpt", icon: "●", color: "#3ddc84", text: "Reading src/store.ts..." },
  { time: "09:14:10", agent: "gpt", icon: "●", color: "#3ddc84", text: "Writing UserContext, CartContext..." },
  { time: "09:14:14", agent: "gpt", icon: "●", color: "#3ddc84", text: "Updated 7 files, set handoff to null" },
  { time: "09:14:15", agent: "system", icon: "✓", color: "#3ddc84", text: "Turn complete" },
];

function TerminalLog() {
  const [visible, setVisible] = useState(0);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    intervalRef.current = setInterval(() => {
      setVisible((v) => {
        if (v >= LOG_LINES.length) {
          clearInterval(intervalRef.current!);
          return v;
        }
        return v + 1;
      });
    }, 480);
    return () => clearInterval(intervalRef.current!);
  }, []);

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [visible]);

  return (
    <div
      ref={containerRef}
      className="rounded-xl overflow-hidden border"
      style={{
        background: "#0d0d14",
        borderColor: "var(--color-border)",
        fontFamily: "var(--font-mono)",
        fontSize: 13,
        height: 320,
        overflowY: "auto",
      }}
    >
      <div
        className="flex items-center gap-2 px-4 py-3 border-b"
        style={{ borderColor: "var(--color-border)", background: "#111118" }}
      >
        <div className="w-2.5 h-2.5 rounded-full" style={{ background: "#ff5c8d" }} />
        <div className="w-2.5 h-2.5 rounded-full" style={{ background: "#ff8c42" }} />
        <div className="w-2.5 h-2.5 rounded-full" style={{ background: "#3ddc84" }} />
        <span className="ml-2" style={{ color: "var(--color-muted)", fontSize: 11 }}>
          agentbridge · example turn
        </span>
      </div>
      <div className="p-4 space-y-1.5">
        {LOG_LINES.slice(0, visible).map((line, i) => (
          <div key={i} className="flex items-start gap-3">
            <span style={{ color: "var(--color-muted)", minWidth: 64, fontSize: 11 }}>{line.time}</span>
            <span style={{ color: line.color, minWidth: 16, fontSize: 14 }}>{line.icon}</span>
            <span style={{ color: "var(--color-text)", lineHeight: 1.5 }}>{line.text}</span>
          </div>
        ))}
        {visible < LOG_LINES.length && (
          <div className="flex items-center gap-3">
            <span style={{ color: "var(--color-muted)", minWidth: 64, fontSize: 11 }}></span>
            <span
              style={{
                display: "inline-block",
                width: 8,
                height: 14,
                background: "var(--color-accent)",
                animation: "blink 1s steps(1) infinite",
              }}
            />
          </div>
        )}
      </div>
      <style>{`@keyframes blink { 0%,100%{opacity:1} 50%{opacity:0} }`}</style>
    </div>
  );
}

function AgentCard({ agent, active }: { agent: typeof AGENTS[0]; active: boolean }) {
  return (
    <div
      className="rounded-xl p-5 border transition-all duration-300"
      style={{
        background: active ? agent.colorDim : "var(--color-surface)",
        borderColor: active ? agent.color + "44" : "var(--color-border)",
        transform: active ? "translateY(-2px)" : undefined,
      }}
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <span
            className="w-9 h-9 rounded-lg flex items-center justify-center text-lg font-bold"
            style={{ background: agent.colorDim, color: agent.color, fontFamily: "var(--font-mono)" }}
          >
            {agent.icon}
          </span>
          <div>
            <div className="font-semibold text-sm" style={{ fontFamily: "var(--font-display)", color: "var(--color-text)" }}>
              {agent.name}
            </div>
            <div className="text-xs" style={{ color: "var(--color-muted)" }}>{agent.vendor}</div>
          </div>
        </div>
        <div
          className="flex items-center gap-1.5 text-xs px-2 py-1 rounded-full"
          style={{
            background: active ? agent.color + "22" : "var(--color-surface-2)",
            color: active ? agent.color : "var(--color-muted)",
            fontFamily: "var(--font-mono)",
          }}
        >
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: active ? agent.color : "#8686b4" }}
          />
          {active ? "active" : "ready"}
        </div>
      </div>
      <div
        className="text-xs px-2.5 py-1.5 rounded-md inline-block"
        style={{
          background: "var(--color-surface-2)",
          color: "var(--color-muted)",
          fontFamily: "var(--font-mono)",
        }}
      >
        {agent.model}
      </div>
    </div>
  );
}

// Web3Forms access keys are meant to be public client-side (they only let
// something send TO the registered email, not read anything) - safe to
// commit. Override via VITE_WEB3FORMS_ACCESS_KEY if it's ever rotated.
const WEB3FORMS_ACCESS_KEY =
  import.meta.env.VITE_WEB3FORMS_ACCESS_KEY || "6f72d69b-efd9-4529-8d43-29411795dcb6";

async function sendViaWeb3Forms(data: FormData) {
  if (!WEB3FORMS_ACCESS_KEY) throw new Error("Contact form isn't configured yet (missing access key).");
  const body = new FormData();
  for (const field of ["name", "email", "message"]) body.append(field, String(data.get(field) ?? ""));
  body.append("access_key", WEB3FORMS_ACCESS_KEY);
  body.append("subject", "New message from the AgentBridge site");
  let res: Response;
  try {
    res = await fetch("https://api.web3forms.com/submit", {
      method: "POST",
      headers: { Accept: "application/json" },
      body,
      signal: AbortSignal.timeout(15000),
    });
  } catch {
    throw new Error("Could not reach the form service. Try again in a bit.");
  }
  // An error page (e.g. HTML 429) isn't JSON; report it as what it is.
  const result = await res.json().catch(() => null);
  if (!res.ok || !result?.success) {
    throw new Error(result?.message || `The form service returned an error (${res.status}). Try again in a bit.`);
  }
}

function ContactForm() {
  const [status, setStatus] = useState<"idle" | "sending" | "sent" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState("");
  // State updates are async, so a fast double-click could start two sends
  // before the button re-renders as disabled. A ref blocks it synchronously.
  const inFlight = useRef(false);
  // The form (and the focused button) is replaced on success; move focus to
  // the confirmation so keyboard and screen-reader users land on it.
  const sentRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (status === "sent") sentRef.current?.focus();
  }, [status]);

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (inFlight.current) return;
    inFlight.current = true;
    setStatus("sending");
    const form = e.currentTarget;
    const data = new FormData(form);
    const fail = (message: string) => {
      setStatus("error");
      setErrorMessage(message);
    };
    const done = () => {
      setStatus("sent");
      form.reset();
    };

    try {
      // Honeypot filled in: a bot. Pretend it worked without sending anything.
      if (String(data.get("website") ?? "").trim()) return done();

      // Prefer the backend's Gmail integration. If it isn't configured -- or
      // the backend itself is down or erroring -- fall back to Web3Forms so a
      // visitor's message still gets through. (Errors about the input, or
      // rate limiting, are shown instead: retrying elsewhere wouldn't help.)
      if ((await getIntegrations()).gmail?.configured) {
        try {
          await sendContact({
            name: String(data.get("name") ?? ""),
            email: String(data.get("email") ?? ""),
            message: String(data.get("message") ?? ""),
            website: "",
          });
          return done();
        } catch (err) {
          if (!(err instanceof ApiError) || !err.isServerFailure) {
            return fail(err instanceof ApiError ? err.message : "Something went wrong sending that.");
          }
          console.warn("Gmail contact send failed; falling back to Web3Forms.", err);
        }
      }

      await sendViaWeb3Forms(data);
      done();
    } catch (err) {
      fail(err instanceof Error ? err.message : "Something went wrong sending that.");
    } finally {
      inFlight.current = false;
    }
  }

  const inputStyle: CSSProperties = {
    background: "var(--color-bg)",
    border: "1px solid var(--color-border)",
    borderRadius: 10,
    padding: "10px 12px",
    color: "var(--color-text)",
    fontFamily: "var(--font-body)",
    fontSize: 14,
    width: "100%",
  };

  if (status === "sent") {
    return (
      <div
        ref={sentRef}
        tabIndex={-1}
        role="status"
        className="rounded-xl p-6 border text-sm outline-none"
        style={{ borderColor: "var(--color-border)", background: "var(--color-surface)", color: "var(--color-text)" }}
      >
        <span style={{ color: "var(--color-green)" }} aria-hidden="true">✓</span> Thanks — that's sent. I'll get back to you soon.
      </div>
    );
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3 max-w-md">
      {/* Visually hidden labels: placeholders vanish while typing and aren't
          reliably announced by screen readers. */}
      <label htmlFor="contact-name" className="sr-only">Name</label>
      <input id="contact-name" type="text" name="name" autoComplete="name" placeholder="First and Last Name" required style={inputStyle} />
      <label htmlFor="contact-email" className="sr-only">Email</label>
      <input id="contact-email" type="email" name="email" autoComplete="email" placeholder="Email" required style={inputStyle} />
      <label htmlFor="contact-message" className="sr-only">Message</label>
      <textarea id="contact-message" name="message" placeholder="What's up?" required rows={4} maxLength={5000} style={{ ...inputStyle, resize: "vertical" }} />
      {/* Honeypot: hidden from people and screen readers; bots fill it in and the backend drops their message. */}
      <input
        type="text"
        name="website"
        tabIndex={-1}
        autoComplete="off"
        aria-hidden="true"
        style={{ position: "absolute", left: "-10000px", width: 1, height: 1, overflow: "hidden" }}
      />
      <button
        type="submit"
        disabled={status === "sending"}
        className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl font-medium text-sm transition-all duration-200 w-fit"
        style={{
          background: "var(--color-accent-strong)",
          color: "#fff",
          fontFamily: "var(--font-display)",
          opacity: status === "sending" ? 0.6 : 1,
          cursor: status === "sending" ? "default" : "pointer",
          border: "none",
        }}
      >
        {status === "sending" ? "Sending..." : "Send message"}
      </button>
      {status === "error" && (
        <p className="text-xs" role="alert" style={{ color: "#f85149" }}>
          {errorMessage}
        </p>
      )}
    </form>
  );
}

// Live renders of frames from a Figma file, via the backend's Figma
// integration. Renders nothing until that integration is configured.
function DesignShowcase() {
  const integrations = useIntegrations();
  const enabled = !!integrations?.figma?.configured;
  const [images, setImages] = useState<FigmaImage[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!enabled) return;
    fetchFigmaImages()
      .then((d) => setImages(d.images))
      .catch((err) => setError(err instanceof ApiError ? err.message : "Couldn't load designs."));
  }, [enabled]);

  if (!enabled || (!images.length && !error)) return null;

  return (
    <section id="design" className="border-t" style={{ borderColor: "var(--color-border)" }}>
      <div className="max-w-6xl mx-auto px-6 md:px-12 py-20 md:py-28">
        <div className="text-xs mb-3" style={{ fontFamily: "var(--font-mono)", color: "var(--color-accent)" }}>
          design · live from figma
        </div>
        <h2
          className="text-3xl md:text-4xl font-semibold tracking-tight mb-10"
          style={{ fontFamily: "var(--font-display)", color: "var(--color-text)" }}
        >
          Designed in Figma
        </h2>
        {error ? (
          <p className="text-sm" style={{ color: "var(--color-muted)" }}>
            {error}
          </p>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {images.map((img) => (
              <figure
                key={img.id}
                className="rounded-xl border overflow-hidden"
                style={{ borderColor: "var(--color-border)", background: "var(--color-surface)" }}
              >
                <img src={img.url} alt={img.name} loading="lazy" className="w-full h-auto block" />
                <figcaption
                  className="px-4 py-3 text-xs border-t"
                  style={{ borderColor: "var(--color-border)", color: "var(--color-muted)", fontFamily: "var(--font-mono)" }}
                >
                  {img.name}
                </figcaption>
              </figure>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

// Wraps each integration-driven section: if one throws while rendering
// (say, the backend's data changed shape), that section disappears instead
// of React unmounting the entire page.
class OptionalSection extends Component<{ name: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    console.warn(`The ${this.props.name} section failed to render and was hidden.`, error);
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

const compact = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

function SectionHeading({ eyebrow, title, aside }: { eyebrow: string; title: string; aside?: ReactNode }) {
  return (
    <div className="flex flex-col md:flex-row md:items-end justify-between gap-6 mb-10">
      <div>
        <div className="text-xs mb-3" style={{ fontFamily: "var(--font-mono)", color: "var(--color-accent)" }}>
          {eyebrow}
        </div>
        <h2
          className="text-3xl md:text-4xl font-semibold tracking-tight"
          style={{ fontFamily: "var(--font-display)", color: "var(--color-text)" }}
        >
          {title}
        </h2>
      </div>
      {aside}
    </div>
  );
}

// Live GitHub stats via the backend's GitHub integration (cached ~10 min).
// Renders nothing until that integration is configured and has loaded.
function GitHubSection() {
  const integrations = useIntegrations();
  const enabled = !!integrations?.github?.configured;
  const [stats, setStats] = useState<GitHubStats | null>(null);

  useEffect(() => {
    if (!enabled) return;
    // A failed load just leaves the section hidden; stats are a nice-to-have.
    fetchGitHubStats().then(setStats).catch(() => {});
  }, [enabled]);

  if (!stats) return null;

  const tiles = [
    { label: "Public repos", value: stats.totals.public_repos },
    { label: "Stars earned", value: stats.totals.stars },
    { label: "Followers", value: stats.totals.followers },
    ...(stats.totals.contributions_last_year != null
      ? [{ label: "Contributions this year", value: stats.totals.contributions_last_year }]
      : []),
  ];

  return (
    <section id="github" className="border-t" style={{ borderColor: "var(--color-border)" }}>
      <div className="max-w-6xl mx-auto px-6 md:px-12 py-20 md:py-28">
        <SectionHeading
          eyebrow="open source · live from github"
          title="Built in the open"
          aside={
            <a
              href={stats.profile.html_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm w-fit"
              style={{ color: "var(--color-muted)", fontFamily: "var(--font-mono)" }}
            >
              github.com/{stats.profile.login} →
            </a>
          }
        />

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-10">
          {tiles.map((t) => (
            <div
              key={t.label}
              className="rounded-xl border p-5"
              style={{ borderColor: "var(--color-border)", background: "var(--color-surface)" }}
            >
              <div className="text-xs mb-2" style={{ color: "var(--color-muted)" }}>
                {t.label}
              </div>
              <div
                className="text-3xl font-semibold"
                style={{ color: "var(--color-text)", fontFamily: "var(--font-body)" }}
                title={t.value.toLocaleString()}
              >
                {compact.format(t.value)}
              </div>
            </div>
          ))}
        </div>

        {stats.languages.length > 0 && (
          <p className="text-sm mb-8" style={{ color: "var(--color-muted)" }}>
            Most used:{" "}
            {stats.languages.map((l, i) => (
              <span key={l.name}>
                {i > 0 && " · "}
                <span style={{ color: "var(--color-text)" }}>{l.name}</span> ({l.repos} {l.repos === 1 ? "repo" : "repos"})
              </span>
            ))}
          </p>
        )}

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {stats.featured.map((r) => (
            <a
              key={r.name}
              href={r.url}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded-xl border p-5 flex flex-col gap-2 transition-colors"
              style={{ borderColor: "var(--color-border)", background: "var(--color-surface)" }}
              onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "var(--color-border-bright)")}
              onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "var(--color-border)")}
            >
              <div className="text-sm font-semibold" style={{ color: "var(--color-text)", fontFamily: "var(--font-mono)" }}>
                {r.name}
              </div>
              {r.description && (
                <div className="text-sm leading-relaxed" style={{ color: "var(--color-muted)" }}>
                  {r.description}
                </div>
              )}
              <div className="text-xs flex gap-4 mt-auto pt-2" style={{ color: "var(--color-muted)", fontFamily: "var(--font-mono)" }}>
                {r.language && <span>{r.language}</span>}
                <span>★ {compact.format(r.stars)}</span>
                {r.forks > 0 && <span>⑂ {compact.format(r.forks)}</span>}
                {r.pushed_at && <span>updated {new Date(r.pushed_at).toLocaleDateString(undefined, { month: "short", year: "numeric" })}</span>}
              </div>
            </a>
          ))}
        </div>
      </div>
    </section>
  );
}

// Calendly booking page embedded inline. No API call involved; the URL
// comes from the backend's config so it can change without a rebuild.
function BookingSection() {
  const url = useIntegrations()?.calendly?.url;
  if (!url) return null;
  return (
    <section id="book" className="border-t" style={{ borderColor: "var(--color-border)" }}>
      <div className="max-w-6xl mx-auto px-6 md:px-12 py-20 md:py-28">
        <SectionHeading eyebrow="book a time" title="Grab a slot on my calendar" />
        <div
          className="rounded-xl border overflow-hidden"
          style={{ borderColor: "var(--color-border)", background: "var(--color-surface)" }}
        >
          <iframe
            src={calendlyEmbedUrl(url)}
            title="Book a meeting on Calendly"
            loading="lazy"
            className="w-full block"
            style={{ height: 720, border: "none" }}
          />
        </div>
        <p className="text-xs mt-3" style={{ color: "var(--color-muted)" }}>
          Calendar not loading?{" "}
          <a href={url} target="_blank" rel="noopener noreferrer" style={{ color: "var(--color-text)" }}>
            Open it on Calendly
          </a>
          .
        </p>
      </div>
    </section>
  );
}

function BookingLink() {
  if (!useIntegrations()?.calendly?.url) return null;
  return (
    <a
      href="#book"
      className="inline-flex items-center gap-2 text-sm w-fit"
      style={{ color: "var(--color-muted)", fontFamily: "var(--font-body)" }}
      onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.color = "var(--color-text)")}
      onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.color = "var(--color-muted)")}
    >
      <span style={{ color: "var(--color-accent)" }}>◷</span> Book a call
    </a>
  );
}

// Link to the resume served live from Google Drive. Shown only when the
// backend publishes a Drive file under the "resume" alias.
function ResumeLink() {
  const integrations = useIntegrations();
  if (!integrations?.drive?.files?.includes("resume")) return null;
  return (
    <a
      href={driveFileUrl("resume")}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-2 text-sm w-fit"
      style={{ color: "var(--color-muted)", fontFamily: "var(--font-body)" }}
      onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.color = "var(--color-text)")}
      onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.color = "var(--color-muted)")}
    >
      <span style={{ color: "var(--color-accent)" }}>↓</span> Resume
    </a>
  );
}

// One shared rotation drives the hero pills and the agent roster. It lives
// outside React state so each tick re-renders only those two components,
// not the whole page every 2.2 seconds.
const agentRotation = (() => {
  let index = 0;
  const listeners = new Set<() => void>();
  let timer: ReturnType<typeof setInterval> | null = null;
  return {
    subscribe(listener: () => void) {
      listeners.add(listener);
      timer ??= setInterval(() => {
        index = (index + 1) % AGENTS.length;
        listeners.forEach((l) => l());
      }, 2200);
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0 && timer) {
          clearInterval(timer);
          timer = null;
        }
      };
    },
    get: () => index,
  };
})();

function useActiveAgent() {
  return useSyncExternalStore(agentRotation.subscribe, agentRotation.get, agentRotation.get);
}

function AgentPills() {
  const activeAgent = useActiveAgent();
  return (
    <div className="flex flex-wrap gap-3 mt-16">
      {AGENTS.map((a, i) => (
        <div
          key={a.id}
          className="flex items-center gap-2 px-3 py-2 rounded-xl border text-xs transition-all duration-300"
          style={{
            fontFamily: "var(--font-mono)",
            background: activeAgent === i ? a.colorDim : "var(--color-surface)",
            borderColor: activeAgent === i ? a.color + "66" : "var(--color-border)",
            color: activeAgent === i ? a.color : "var(--color-muted)",
          }}
        >
          <span>{a.icon}</span>
          <span>{a.name}</span>
          <span
            className="w-1.5 h-1.5 rounded-full"
            style={{ background: activeAgent === i ? a.color : "#3a3a50" }}
          />
        </div>
      ))}
    </div>
  );
}

function AgentRoster() {
  const activeAgent = useActiveAgent();
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {AGENTS.map((agent, i) => (
        <AgentCard key={agent.id} agent={agent} active={activeAgent === i} />
      ))}
    </div>
  );
}

export default function App() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div style={{ background: "var(--color-bg)", minHeight: "100vh" }}>
      {/* Nav */}
      <nav
        className="sticky top-0 z-50 flex items-center justify-between px-6 md:px-12 py-4 border-b"
        style={{
          background: "var(--color-bg)",
          borderColor: "var(--color-border)",
          backdropFilter: "blur(12px)",
        }}
      >
        <div className="flex items-center gap-2.5">
          <div
            className="w-7 h-7 rounded-md flex items-center justify-center text-sm font-bold"
            style={{ background: "var(--color-accent-strong)", color: "#fff", fontFamily: "var(--font-mono)" }}
          >
            A
          </div>
          <span className="font-semibold text-sm tracking-tight" style={{ fontFamily: "var(--font-display)", color: "var(--color-text)" }}>
            AgentBridge
          </span>
        </div>

        <div className="hidden md:flex items-center gap-8">
          {NAV_ITEMS.map((item) => (
            <a
              key={item.label}
              href={item.href}
              target={item.external ? "_blank" : undefined}
              rel={item.external ? "noopener noreferrer" : undefined}
              className="text-sm transition-colors duration-200"
              style={{ color: "var(--color-muted)" }}
              onMouseEnter={(e) => ((e.target as HTMLElement).style.color = "var(--color-text)")}
              onMouseLeave={(e) => ((e.target as HTMLElement).style.color = "var(--color-muted)")}
            >
              {item.label}
            </a>
          ))}
        </div>

        <div className="flex items-center gap-3">
          <a
            href="https://github.com/bbbiyeba/AgentBridge"
            target="_blank"
            rel="noopener noreferrer"
            className="hidden md:inline-flex items-center gap-2 text-sm px-3 py-1.5 rounded-lg border transition-colors"
            style={{
              fontFamily: "var(--font-mono)",
              color: "var(--color-muted)",
              borderColor: "var(--color-border)",
              fontSize: 12,
            }}
          >
            ★ GitHub
          </a>
          <button
            className="md:hidden"
            onClick={() => setMenuOpen(!menuOpen)}
            style={{ color: "var(--color-muted)" }}
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
            aria-controls="mobile-menu"
          >
            <span aria-hidden="true">{menuOpen ? "✕" : "☰"}</span>
          </button>
        </div>
      </nav>

      {menuOpen && (
        <div
          id="mobile-menu"
          className="md:hidden border-b px-6 py-4 flex flex-col gap-4"
          style={{ background: "var(--color-surface)", borderColor: "var(--color-border)" }}
        >
          {NAV_ITEMS.map((item) => (
            <a
              key={item.label}
              href={item.href}
              target={item.external ? "_blank" : undefined}
              rel={item.external ? "noopener noreferrer" : undefined}
              className="text-sm"
              style={{ color: "var(--color-muted)" }}
              onClick={() => setMenuOpen(false)}
            >
              {item.label}
            </a>
          ))}
        </div>
      )}

      {/* Hero */}
      <section className="relative overflow-hidden">
        {/* Grid lines */}
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            backgroundImage:
              "linear-gradient(var(--color-border) 1px, transparent 1px), linear-gradient(90deg, var(--color-border) 1px, transparent 1px)",
            backgroundSize: "80px 80px",
            opacity: 0.35,
          }}
        />
        <div
          className="absolute inset-0 pointer-events-none"
          style={{
            background:
              "radial-gradient(ellipse 70% 60% at 50% 0%, #6c6cff14 0%, transparent 70%)",
          }}
        />

        <div className="relative max-w-6xl mx-auto px-6 md:px-12 pt-24 pb-20 md:pt-36 md:pb-28">
          <div className="max-w-3xl">
            <div
              className="inline-flex items-center gap-2 text-xs px-3 py-1.5 rounded-full border mb-8"
              style={{
                fontFamily: "var(--font-mono)",
                color: "var(--color-accent)",
                borderColor: "var(--color-accent)" + "44",
                background: "var(--color-accent-dim)",
              }}
            >
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: "var(--color-accent)" }} />
              Local-first multi-agent orchestration
            </div>

            <h1
              className="text-4xl md:text-6xl lg:text-7xl font-semibold leading-tight tracking-tight mb-6"
              style={{ fontFamily: "var(--font-display)", color: "var(--color-text)" }}
            >
              Multiple AI agents.
              <br />
              <span style={{ color: "var(--color-accent)" }}>One codebase.</span>
            </h1>

            <p
              className="text-base md:text-lg leading-relaxed mb-10 max-w-xl"
              style={{ color: "var(--color-muted)", fontFamily: "var(--font-body)" }}
            >
              Claude, ChatGPT, and local Ollama models take turns working on your project — reading each other's changes, no copy-pasting. Runs entirely on your machine.
            </p>

            <div className="flex flex-wrap items-center gap-4">
              <a
                href="https://agent-bridge-one.vercel.app/app"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl font-medium text-sm transition-all duration-200"
                style={{
                  background: "var(--color-accent-strong)",
                  color: "#fff",
                  fontFamily: "var(--font-display)",
                }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.opacity = "0.9")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.opacity = "1")}
              >
                See it in action
                <span>→</span>
              </a>
              <a
                href="https://github.com/bbbiyeba/AgentBridge#readme"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl font-medium text-sm border transition-all duration-200"
                style={{
                  borderColor: "var(--color-border-bright)",
                  color: "var(--color-muted)",
                  fontFamily: "var(--font-display)",
                }}
                onMouseEnter={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = "var(--color-text)";
                  (e.currentTarget as HTMLElement).style.color = "var(--color-text)";
                }}
                onMouseLeave={(e) => {
                  (e.currentTarget as HTMLElement).style.borderColor = "var(--color-border-bright)";
                  (e.currentTarget as HTMLElement).style.color = "var(--color-muted)";
                }}
              >
                Read the docs
              </a>
            </div>
          </div>

          <AgentPills />
        </div>
      </section>

      {/* Features */}
      <section id="features" className="border-t" style={{ borderColor: "var(--color-border)" }}>
        <div className="max-w-6xl mx-auto px-6 md:px-12 py-20 md:py-28">
          <div className="grid md:grid-cols-2 gap-px" style={{ background: "var(--color-border)" }}>
            {FEATURES.map((f, i) => (
              <div
                key={i}
                className="p-8 md:p-10 transition-colors duration-200"
                style={{ background: "var(--color-bg)" }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--color-surface)")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.background = "var(--color-bg)")}
              >
                <div
                  className="text-xs mb-5"
                  style={{ fontFamily: "var(--font-mono)", color: "var(--color-accent)" }}
                >
                  {f.tag}
                </div>
                <h3
                  className="text-xl font-semibold mb-3 leading-tight"
                  style={{ fontFamily: "var(--font-display)", color: "var(--color-text)" }}
                >
                  {f.title}
                </h3>
                <p className="text-sm leading-relaxed" style={{ color: "var(--color-muted)" }}>
                  {f.body}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Agents roster */}
      <section id="agents" className="border-t" style={{ borderColor: "var(--color-border)" }}>
        <div className="max-w-6xl mx-auto px-6 md:px-12 py-20 md:py-28">
          <div className="flex flex-col md:flex-row md:items-end justify-between gap-6 mb-12">
            <div>
              <div
                className="text-xs mb-3"
                style={{ fontFamily: "var(--font-mono)", color: "var(--color-accent)" }}
              >
                connected agents
              </div>
              <h2
                className="text-3xl md:text-4xl font-semibold tracking-tight"
                style={{ fontFamily: "var(--font-display)", color: "var(--color-text)" }}
              >
                Mix any model, any vendor
              </h2>
            </div>
            <p className="text-sm max-w-xs leading-relaxed" style={{ color: "var(--color-muted)" }}>
              Connect Claude, ChatGPT, or any Ollama model. Add an agent by naming it, its provider, and a model in config.yaml.
            </p>
          </div>

          <AgentRoster />
        </div>
      </section>

      {/* Workflow demo */}
      <section id="workflow" className="border-t" style={{ borderColor: "var(--color-border)" }}>
        <div className="max-w-6xl mx-auto px-6 md:px-12 py-20 md:py-28">
          <div className="grid md:grid-cols-2 gap-12 md:gap-20 items-center">
            <div>
              <div
                className="text-xs mb-3"
                style={{ fontFamily: "var(--font-mono)", color: "var(--color-accent)" }}
              >
                live pipeline
              </div>
              <h2
                className="text-3xl md:text-4xl font-semibold tracking-tight mb-5"
                style={{ fontFamily: "var(--font-display)", color: "var(--color-text)" }}
              >
                Watch agents hand off in real time
              </h2>
              <p className="text-sm leading-relaxed mb-8" style={{ color: "var(--color-muted)" }}>
                Define a pipeline in a single YAML file. AgentBridge drives each agent in sequence, passing working directory state and the prior agent's output as context. No wrappers, no abstractions — just your code and the models.
              </p>

              <div className="space-y-3">
                {[
                  { step: "01", label: "Write config.yaml", detail: "Define each agent's name, provider, model, and role prompt" },
                  { step: "02", label: "Run python -m agentbridge run", detail: "Runs each agent in turn against your workspace" },
                  { step: "03", label: "Review the diff", detail: "Each agent's changes committed atomically" },
                ].map((s) => (
                  <div
                    key={s.step}
                    className="flex items-start gap-4 p-4 rounded-xl border"
                    style={{ borderColor: "var(--color-border)", background: "var(--color-surface)" }}
                  >
                    <span
                      className="text-xs pt-0.5"
                      style={{ fontFamily: "var(--font-mono)", color: "var(--color-accent)" }}
                    >
                      {s.step}
                    </span>
                    <div>
                      <div
                        className="text-sm font-medium mb-0.5"
                        style={{ fontFamily: "var(--font-display)", color: "var(--color-text)" }}
                      >
                        {s.label}
                      </div>
                      <div className="text-xs" style={{ color: "var(--color-muted)" }}>{s.detail}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>

            <div>
              <TerminalLog />
              <div
                className="mt-3 text-xs px-2"
                style={{ fontFamily: "var(--font-mono)", color: "var(--color-muted)" }}
              >
                illustrative example — not live data
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="border-t" style={{ borderColor: "var(--color-border)" }}>
        <div className="max-w-6xl mx-auto px-6 md:px-12 py-20 md:py-28">
          <div
            className="rounded-2xl p-10 md:p-16 relative overflow-hidden"
            style={{ background: "var(--color-surface)" }}
          >
            <div
              className="absolute inset-0 pointer-events-none"
              style={{
                background: "radial-gradient(ellipse 60% 80% at 100% 50%, #6c6cff0d 0%, transparent 70%)",
              }}
            />
            <div className="relative max-w-xl">
              <h2
                className="text-3xl md:text-4xl font-semibold tracking-tight mb-4"
                style={{ fontFamily: "var(--font-display)", color: "var(--color-text)" }}
              >
                Start orchestrating in minutes
              </h2>
              <p className="text-sm leading-relaxed mb-8" style={{ color: "var(--color-muted)" }}>
                Needs Python 3.11+. Bring your own Anthropic/OpenAI keys, or use Ollama with no keys at all.
              </p>

              <div
                className="flex flex-col gap-1.5 px-5 py-3.5 rounded-xl border mb-6 w-fit"
                style={{
                  background: "var(--color-bg)",
                  borderColor: "var(--color-border-bright)",
                  fontFamily: "var(--font-mono)",
                  fontSize: 13,
                }}
              >
                <div><span style={{ color: "var(--color-accent)" }}>$</span> <span style={{ color: "var(--color-text)" }}>git clone https://github.com/bbbiyeba/AgentBridge.git</span></div>
                <div><span style={{ color: "var(--color-accent)" }}>$</span> <span style={{ color: "var(--color-text)" }}>pip install -r requirements.txt</span></div>
                <div><span style={{ color: "var(--color-accent)" }}>$</span> <span style={{ color: "var(--color-text)" }}>python -m agentbridge web</span></div>
              </div>

              <div className="flex flex-wrap gap-3">
                <a
                  href="https://github.com/bbbiyeba/AgentBridge#readme"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl font-medium text-sm transition-all duration-200"
                  style={{
                    background: "var(--color-accent-strong)",
                    color: "#fff",
                    fontFamily: "var(--font-display)",
                  }}
                  onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.opacity = "0.9")}
                  onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.opacity = "1")}
                >
                  Read the docs →
                </a>
                <a
                  href="https://github.com/bbbiyeba/AgentBridge"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl font-medium text-sm border transition-all duration-200"
                  style={{
                    borderColor: "var(--color-border-bright)",
                    color: "var(--color-muted)",
                    fontFamily: "var(--font-display)",
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.borderColor = "var(--color-text)";
                    (e.currentTarget as HTMLElement).style.color = "var(--color-text)";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.borderColor = "var(--color-border-bright)";
                    (e.currentTarget as HTMLElement).style.color = "var(--color-muted)";
                  }}
                >
                  View on GitHub
                </a>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Contact */}
      <section id="contact" className="border-t" style={{ borderColor: "var(--color-border)" }}>
        <div className="max-w-6xl mx-auto px-6 md:px-12 py-20 md:py-28">
          <div
            className="text-xs mb-3"
            style={{ fontFamily: "var(--font-mono)", color: "var(--color-accent)" }}
          >
            get in touch
          </div>
          <h2
            className="text-3xl md:text-4xl font-semibold tracking-tight mb-4"
            style={{ fontFamily: "var(--font-display)", color: "var(--color-text)" }}
          >
            Questions, bugs, or ideas?
          </h2>
          <p className="text-sm leading-relaxed mb-8 max-w-md" style={{ color: "var(--color-muted)" }}>
            Send a message directly, or open an issue on GitHub if it's a bug or feature request.
          </p>
          <div className="grid md:grid-cols-2 gap-12 items-start">
            <ContactForm />
            <div className="flex flex-col gap-3">
              <a
                href="https://github.com/bbbiyeba"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-sm w-fit"
                style={{ color: "var(--color-muted)", fontFamily: "var(--font-body)" }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.color = "var(--color-text)")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.color = "var(--color-muted)")}
              >
                <span style={{ color: "var(--color-accent)" }}>★</span> github.com/bbbiyeba
              </a>
              <a
                href="https://www.linkedin.com/in/bryce-biyeba/"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-sm w-fit"
                style={{ color: "var(--color-muted)", fontFamily: "var(--font-body)" }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.color = "var(--color-text)")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.color = "var(--color-muted)")}
              >
                <span style={{ color: "var(--color-accent)" }}>in</span> linkedin.com/in/bryce-biyeba
              </a>
              <a
                href="https://github.com/bbbiyeba/AgentBridge/issues"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 text-sm w-fit"
                style={{ color: "var(--color-muted)", fontFamily: "var(--font-body)" }}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.color = "var(--color-text)")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.color = "var(--color-muted)")}
              >
                <span style={{ color: "var(--color-accent)" }}>⚑</span> Report an issue
              </a>
              <OptionalSection name="ResumeLink">
                <ResumeLink />
              </OptionalSection>
              <OptionalSection name="BookingLink">
                <BookingLink />
              </OptionalSection>
            </div>
          </div>
        </div>
      </section>

      {/* Integration-driven sections. They appear only once the backend
          reports them configured, so they sit below Contact: loading late
          can't push the contact form down while someone is using it. */}
      <OptionalSection name="Booking">
        <BookingSection />
      </OptionalSection>
      <OptionalSection name="GitHub">
        <GitHubSection />
      </OptionalSection>
      <OptionalSection name="Design">
        <DesignShowcase />
      </OptionalSection>

      {/* Footer */}
      <footer className="border-t px-6 md:px-12 py-8" style={{ borderColor: "var(--color-border)" }}>
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <div
              className="w-5 h-5 rounded flex items-center justify-center text-xs font-bold"
              style={{ background: "var(--color-accent-strong)", color: "#fff", fontFamily: "var(--font-mono)" }}
            >
              A
            </div>
            <span className="text-xs" style={{ fontFamily: "var(--font-body)", color: "var(--color-muted)" }}>
              Built by{" "}
              <a
                href="https://www.linkedin.com/in/bryce-biyeba/"
                target="_blank"
                rel="noopener noreferrer"
                className="font-semibold hover:text-white transition-colors"
                style={{ color: "var(--color-muted)" }}
              >
                Bryce Biyeba
              </a>
            </span>
          </div>
          <div className="flex items-center gap-6 text-xs" style={{ color: "var(--color-muted)", fontFamily: "var(--font-mono)" }}>
            <a href="https://github.com/bbbiyeba/AgentBridge#readme" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">docs</a>
            <a href="https://github.com/bbbiyeba/AgentBridge" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">github</a>
            <a href="https://agent-bridge-one.vercel.app/app" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">dashboard</a>
            <a href="https://www.linkedin.com/in/bryce-biyeba/" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">linkedin</a>
            <a href="#contact" className="hover:text-white transition-colors">contact</a>
          </div>
        </div>
      </footer>
      <Analytics />
    </div>
  );
}
