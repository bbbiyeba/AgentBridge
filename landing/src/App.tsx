import { useState, useEffect, useRef } from "react";

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
    body: "Route architecture decisions to Claude, boilerplate to a fast local model, and code review to GPT-4o. Combine strengths across vendors.",
  },
];

const LOG_LINES = [
  { time: "09:14:02", agent: "claude", icon: "◆", color: "#6c6cff", text: "Analysing codebase structure..." },
  { time: "09:14:05", agent: "claude", icon: "◆", color: "#6c6cff", text: "Found 3 components with prop-drilling issues" },
  { time: "09:14:05", agent: "claude", icon: "◆", color: "#6c6cff", text: "Proposing context refactor in src/store.ts" },
  { time: "09:14:07", agent: "system", icon: "→", color: "#7070a0", text: "Handing off to reviewer (ChatGPT)" },
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
            style={{ background: active ? agent.color : "#7070a0" }}
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

export default function App() {
  const [activeAgent, setActiveAgent] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => {
    const t = setInterval(() => setActiveAgent((a) => (a + 1) % AGENTS.length), 2200);
    return () => clearInterval(t);
  }, []);

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
            style={{ background: "var(--color-accent)", color: "#fff", fontFamily: "var(--font-mono)" }}
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
          >
            {menuOpen ? "✕" : "☰"}
          </button>
        </div>
      </nav>

      {menuOpen && (
        <div
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
                  background: "var(--color-accent)",
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

          {/* Agent pills row */}
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

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {AGENTS.map((agent, i) => (
              <AgentCard key={agent.id} agent={agent} active={activeAgent === i} />
            ))}
          </div>
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
                    background: "var(--color-accent)",
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

      {/* Footer */}
      <footer className="border-t px-6 md:px-12 py-8" style={{ borderColor: "var(--color-border)" }}>
        <div className="max-w-6xl mx-auto flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
          <div className="flex items-center gap-2.5">
            <div
              className="w-5 h-5 rounded flex items-center justify-center text-xs font-bold"
              style={{ background: "var(--color-accent)", color: "#fff", fontFamily: "var(--font-mono)" }}
            >
              A
            </div>
            <span className="text-xs font-semibold" style={{ fontFamily: "var(--font-display)", color: "var(--color-muted)" }}>
              AgentBridge
            </span>
          </div>
          <div className="flex items-center gap-6 text-xs" style={{ color: "var(--color-muted)", fontFamily: "var(--font-mono)" }}>
            <a href="https://github.com/bbbiyeba/AgentBridge#readme" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">docs</a>
            <a href="https://github.com/bbbiyeba/AgentBridge" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">github</a>
            <a href="https://agent-bridge-one.vercel.app/app" target="_blank" rel="noopener noreferrer" className="hover:text-white transition-colors">dashboard</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
