import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { api, setCsrf, watchEvents } from "./api";
import type {
  AgentEvent,
  Bootstrap,
  Project,
  ProjectSummary,
  Provider,
} from "./api";

const paths: Record<string, ReactNode> = {
  plus: <path d="M12 5v14M5 12h14" />,
  arrow: <path d="m5 12 7-7 7 7M12 5v14" />,
  chevron: <path d="m9 5 7 7-7 7" />,
  code: (
    <>
      <path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-14-2 18" />
    </>
  ),
  folder: <path d="M3 7V5h6l2 2h10v13H3Z" />,
  play: <path d="m8 5 11 7-11 7Z" />,
  stop: <rect x="6" y="6" width="12" height="12" rx="1" />,
  check: <path d="m5 12 4 4L19 6" />,
  refresh: (
    <>
      <path d="M20 7v5h-5M4 17v-5h5" />
      <path d="M19 10a7 7 0 0 0-12-4L4 9m1 5a7 7 0 0 0 12 4l3-3" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7h16M4 17h16" />
      <circle cx="8" cy="7" r="3" />
      <circle cx="16" cy="17" r="3" />
    </>
  ),
  close: <path d="m6 6 12 12M6 18 18 6" />,
  external: (
    <>
      <path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7" />
    </>
  ),
  terminal: (
    <>
      <path d="m4 6 6 6-6 6m9 0h7" />
    </>
  ),
  copy: (
    <>
      <rect x="8" y="8" width="12" height="13" rx="2" />
      <path d="M16 8V3H3v13h5" />
    </>
  ),
  monitor: (
    <>
      <rect x="3" y="4" width="18" height="13" rx="2" />
      <path d="M8 21h8m-4-4v4" />
    </>
  ),
  mobile: (
    <>
      <rect x="7" y="2" width="10" height="20" rx="2" />
      <path d="M11 18h2" />
    </>
  ),
  activity: <path d="M2 12h5l3-8 4 16 3-8h5" />,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6m0-11v1" />
    </>
  ),
};
function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] || paths.code}
    </svg>
  );
}
function Mark({ small = false }: { small?: boolean }) {
  return (
    <span className={`mark ${small ? "small" : ""}`} aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}
const statuses: Record<string, string> = {
  idle: "Ready for a direction",
  running: "Working",
  awaiting_input: "Your input is needed",
  ready: "Build verified",
  failed: "Needs attention",
  interrupted: "Interrupted",
  cancelled: "Stopped",
};
const stageNames: Record<string, string> = {
  load_memory: "Loading project context",
  clarify_analyze: "Understanding your request",
  ask_clarifications: "Waiting for your direction",
  finalize_requirements: "Defining the brief",
  planner: "Planning the application",
  architect: "Structuring the project",
  resolve_packages: "Resolving dependencies",
  generate: "Writing the application",
  style: "Refining the interface",
  review: "Checking source contracts",
  packages: "Installing and verifying the build",
  repair: "Repairing build errors",
  edit: "Making your changes",
  persist_memory: "Saving project decisions",
  persist_failure: "Saving diagnostics",
};
function eventLabel(event: AgentEvent) {
  const d = event.data;
  if (event.type.startsWith("node_"))
    return `${stageNames[String(d.node)] || d.node}${event.type === "node_completed" ? " · done" : event.type === "node_failed" ? " · failed" : ""}`;
  if (event.type === "file_generated")
    return `${d.operation === "edit" ? "Updated" : "Wrote"} ${d.path}`;
  if (event.type === "file_generation_started") return `Writing ${d.path}`;
  if (event.type === "sandbox_command_started") return String(d.command);
  if (event.type === "accepted") return "Request accepted";
  if (event.type === "build_passed") return "Build passed";
  if (event.type === "build_failed") return "Build failed";
  if (event.type === "cache_hit") return `Reused ${d.path}`;
  return event.type.replaceAll("_", " ");
}
const visibleEvents = new Set([
  "node_started",
  "node_completed",
  "node_failed",
  "file_generated",
  "file_generation_started",
  "sandbox_command_started",
  "build_passed",
  "build_failed",
  "accepted",
  "cache_hit",
]);
function readDraft(id: string | null) {
  return localStorage.getItem(`forma-draft-${id || "new"}`) || "";
}

export function App() {
  const [boot, setBoot] = useState<Bootstrap | null>(null);
  const [providers, setProviders] = useState<Provider[]>([]);
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [id, setId] = useState<string | null>(() =>
    localStorage.getItem("forma-project"),
  );
  const [project, setProject] = useState<Project | null>(null);
  const [draft, setDraft] = useState(() =>
    readDraft(localStorage.getItem("forma-project")),
  );
  const [provider, setProvider] = useState("ollama");
  const [model, setModel] = useState("");
  const [framework, setFramework] = useState("react");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState(false);
  const [rail, setRail] = useState(false);
  const [tokenNeeded, setTokenNeeded] = useState(false);
  const [token, setToken] = useState("");
  const [tab, setTab] = useState("preview");
  const [file, setFile] = useState<{ path: string; content: string } | null>(
    null,
  );
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(true);
  const [mobilePreview, setMobilePreview] = useState(false);
  const [diagnostics, setDiagnostics] = useState(false);
  const [copied, setCopied] = useState(false);
  const [loadingProject, setLoadingProject] = useState(false);
  const cursor = useRef(0);
  const activeId = useRef(id);
  const messagesEnd = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const sending = useRef(false);
  const pendingRequest = useRef<{
    content: string;
    id: string;
    projectId: string;
  } | null>(null);
  const running = project?.status === "running";
  const currentProvider = providers.find((p) => p.id === provider);
  const report = project?.state.package_report;
  const commands = [
    ...(report?.resolution_commands || []),
    ...(report?.commands || []),
  ];
  const refreshProjects = useCallback(
    async () => setProjects(await api<ProjectSummary[]>("/projects")),
    [],
  );
  const refreshProject = useCallback(async (pid: string) => {
    const data = await api<Project>(`/projects/${pid}`);
    if (activeId.current === pid) setProject(data);
  }, []);
  const bootstrap = useCallback(async () => {
    try {
      const data = await api<Bootstrap>("/bootstrap");
      setCsrf(data.csrf_token);
      setBoot(data);
      setTokenNeeded(false);
      setError("");
      await refreshProjects();
      const found = await api<Provider[]>("/providers");
      setProviders(found);
      const first =
        found.find((p) => p.available && p.models.length) || found[0];
      if (first) {
        setProvider(first.id);
        setModel(
          first.models.includes(first.default_model)
            ? first.default_model
            : first.models[0] || first.default_model,
        );
      }
    } catch (e) {
      const msg = (e as Error).message;
      if (msg === "ACCESS_TOKEN_REQUIRED") setTokenNeeded(true);
      else setError(msg);
    }
  }, [refreshProjects]);
  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);
  useEffect(() => {
    activeId.current = id;
    setProject(null);
    setEvents([]);
    setFile(null);
    cursor.current = 0;
    setDraft(readDraft(id));
    setRail(false);
    if (id) {
      localStorage.setItem("forma-project", id);
      setLoadingProject(true);
      void refreshProject(id)
        .catch((e) => {
          setError(e.message);
        })
        .finally(() => setLoadingProject(false));
    } else {
      localStorage.removeItem("forma-project");
      setLoadingProject(false);
    }
  }, [id, refreshProject]);
  useEffect(() => {
    localStorage.setItem(`forma-draft-${id || "new"}`, draft);
  }, [draft, id]);
  useEffect(() => {
    messagesEnd.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [project?.messages.length, project?.status]);
  useEffect(() => {
    if (!id || !boot) return;
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout> | undefined;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    const connect = async () => {
      try {
        await watchEvents(
          id,
          cursor.current,
          (event) => {
            if (activeId.current !== id) return;
            setConnected(true);
            cursor.current = event.id;
            if (visibleEvents.has(event.type))
              setEvents((old) => [...old, event].slice(-120));
            if (!refresh)
              refresh = setTimeout(() => {
                refresh = undefined;
                void refreshProject(id).catch(() => setConnected(false));
                void refreshProjects().catch(() => {});
              }, 400);
          },
          controller.signal,
          () => setConnected(true),
        );
      } catch {
        if (!controller.signal.aborted) setConnected(false);
      }
      if (!controller.signal.aborted) {
        setConnected(false);
        retry = setTimeout(connect, 2000);
      }
    };
    void connect();
    return () => {
      controller.abort();
      clearTimeout(retry);
      clearTimeout(refresh);
    };
  }, [id, boot, refreshProject, refreshProjects]);

  async function submit(event?: FormEvent, action?: string) {
    event?.preventDefault();
    if (sending.current || running || (!action && !draft.trim())) return;
    sending.current = true;
    setBusy(true);
    setError("");
    let pid = id;
    const content =
      action === "build"
        ? "Build and validate the current project."
        : action === "resume"
          ? "Resume the last unfinished operation."
          : action === "retry"
            ? "Retry generation from the original request."
            : draft.trim();
    try {
      if (!pid) {
        if (!model.trim()) throw new Error("Choose a model before starting.");
        const created = await api<Project>("/projects", {
          method: "POST",
          body: JSON.stringify({
            title: content.slice(0, 65),
            framework,
            provider,
            model: model.trim(),
          }),
        });
        pid = created.id;
        activeId.current = pid;
        setId(pid);
        localStorage.setItem(`forma-draft-${pid}`, content);
      }
      if (
        !pendingRequest.current ||
        pendingRequest.current.content !== content ||
        pendingRequest.current.projectId !== pid
      )
        pendingRequest.current = {
          content,
          id: crypto.randomUUID(),
          projectId: pid,
        };
      await api(`/projects/${pid}/messages`, {
        method: "POST",
        body: JSON.stringify({
          content,
          request_id: pendingRequest.current.id,
          action: action || null,
        }),
      });
      pendingRequest.current = null;
      if (!action) {
        setDraft("");
        localStorage.removeItem(`forma-draft-${pid}`);
        localStorage.removeItem("forma-draft-new");
      }
      await refreshProject(pid);
      await refreshProjects();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }
  async function perform(operation: () => Promise<unknown>) {
    setError("");
    setBusy(true);
    try {
      await operation();
      if (id) await refreshProject(id);
      await refreshProjects();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function openFile(path: string) {
    if (!id) return;
    const pid = id;
    try {
      const result = await api<{ path: string; content: string }>(
        `/projects/${pid}/file?path=${encodeURIComponent(path)}`,
      );
      if (activeId.current !== pid) return;
      setFile(result);
      setTab("code");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  function chooseProject(pid: string | null) {
    if (pid === id) return;
    setError("");
    setId(pid);
  }
  function chooseProvider(value: string) {
    setProvider(value);
    const p = providers.find((p) => p.id === value);
    setModel(p?.models[0] || p?.default_model || "");
  }
  const latest = [
    ...events.slice(events.map((e) => e.type).lastIndexOf("accepted") + 1),
  ]
    .reverse()
    .find(
      (e) => e.type === "node_started" || e.type === "file_generation_started",
    );

  return (
    <div className="app-shell">
      <a className="skip-link" href="#conversation">
        Skip to conversation
      </a>
      {rail && (
        <button
          className="rail-scrim"
          aria-label="Close project navigation"
          onClick={() => setRail(false)}
        />
      )}
      <aside className={`rail ${rail ? "open" : ""}`} aria-label="Projects">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            chooseProject(null);
          }}
        >
          <Mark />
          <span>
            forma<span className="brand-period">.</span>
          </span>
          <span className="studio-tag">STUDIO</span>
        </a>
        <button className="new-project" onClick={() => chooseProject(null)}>
          <Icon name="plus" />
          New conversation
        </button>
        <div className="rail-heading">
          <span>YOUR WORKSPACE</span>
          <span>{String(projects.length).padStart(2, "0")}</span>
        </div>
        <nav className="project-list">
          {projects.length ? (
            projects.map((p) => (
              <button
                key={p.id}
                className={`project-link ${p.id === id ? "selected" : ""}`}
                onClick={() => chooseProject(p.id)}
              >
                <span className={`project-dot ${p.status}`} />
                <span>
                  <strong>{p.title}</strong>
                  <small>
                    {p.framework} <span>·</span>{" "}
                    {statuses[p.status] || p.status}
                  </small>
                </span>
                {p.id === id && <Icon name="chevron" size={14} />}
              </button>
            ))
          ) : (
            <p className="rail-empty">
              A place for your next idea.
              <br />
              Your projects will live here.
            </p>
          )}
        </nav>
        <div className="rail-bottom">
          <div className="runtime-note">
            <span
              className={`status-dot ${boot?.runtime.runtime !== "mock" ? "green" : ""}`}
            />
            <span>
              {boot
                ? boot.runtime.runtime === "mock"
                  ? "Host execution"
                  : boot.runtime.runtime
                : "Connecting…"}
            </span>
            <button
              className="icon-button"
              title="Environment details"
              aria-label="Environment details"
              onClick={() => setSettings(true)}
            >
              <Icon name="info" size={15} />
            </button>
          </div>
          <button
            className="environment-button"
            onClick={() => setSettings(true)}
          >
            <Icon name="settings" size={17} />
            <span>Environment & models</span>
            <Icon name="chevron" size={14} />
          </button>
          <div className="rail-signoff">
            <span>LOCAL-FIRST. IDEA-FORWARD.</span>
            <span>v0.3</span>
          </div>
        </div>
      </aside>
      <main className="workspace">
        <header className="workspace-header">
          <div className="breadcrumb">
            <button
              className="icon-button mobile-menu"
              aria-label="Open projects"
              onClick={() => setRail(true)}
            >
              <Icon name="menu" />
            </button>
            <span>Workspace</span>
            <span className="slash">/</span>
            <strong>
              {project?.title ||
                (loadingProject ? "Loading project…" : "New conversation")}
            </strong>
          </div>
          <div className="header-actions">
            <span className={`connection ${connected ? "" : "offline"}`}>
              <span className="status-dot green" />
              {connected ? "Connected" : "Reconnecting…"}
            </span>
            <button
              className="icon-button"
              aria-label="Refresh workspace"
              title="Refresh workspace"
              onClick={() => {
                void bootstrap();
                if (id) void refreshProject(id);
              }}
            >
              <Icon name="refresh" size={16} />
            </button>
          </div>
        </header>
        {error && error !== "ACCESS_TOKEN_REQUIRED" && (
          <div className="error-banner" role="alert">
            <Icon name="info" />
            <span>{error}</span>
            <button
              className="icon-button"
              aria-label="Dismiss error"
              onClick={() => setError("")}
            >
              <Icon name="close" />
            </button>
          </div>
        )}
        <div className={`workspace-body ${!id ? "is-new" : ""}`}>
          <section
            className="conversation"
            id="conversation"
            aria-label="Conversation"
          >
            <div className="conversation-scroll">
              {!id ? (
                <div className="welcome">
                  <div className="eyebrow">
                    <span className="tiny-cross">✳</span> A CONVERSATION IS A
                    STARTING POINT
                  </div>
                  <h1>
                    Make room
                    <br />
                    for your <em>next idea.</em>
                  </h1>
                  <p>
                    Describe what you have in mind.
                    <br />
                    Build it, explore it, make it your own.
                  </p>
                  <div className="starter-heading">
                    A FEW PLACES TO BEGIN <span>↘</span>
                  </div>
                  <div className="starters">
                    {[
                      [
                        "01",
                        "Something useful",
                        "Build a personal reading journal with book search, reading statuses, and a monthly reading goal. Use local storage.",
                      ],
                      [
                        "02",
                        "Something expressive",
                        "Build a distinctive portfolio for a ceramic artist, with an exhibition archive, work gallery, and contact section.",
                      ],
                      [
                        "03",
                        "Something interactive",
                        "Build a focus timer with adjustable work and break sessions, daily session history, and keyboard controls.",
                      ],
                    ].map(([n, title, prompt]) => (
                      <button
                        key={n}
                        onClick={() => {
                          setDraft(prompt);
                          input.current?.focus();
                        }}
                      >
                        <span>{n}</span>
                        <strong>{title}</strong>
                        <Icon name="chevron" size={15} />
                      </button>
                    ))}
                  </div>
                </div>
              ) : loadingProject ? (
                <div className="loading-state">
                  <span className="spinner" />
                  Opening your project…
                </div>
              ) : (
                <div
                  className="messages"
                  aria-live="polite"
                  aria-relevant="additions"
                >
                  <div className="conversation-intro">
                    <span className="eyebrow">
                      {project?.framework} / {project?.model}
                    </span>
                    <h2>Let’s give it form.</h2>
                  </div>
                  {project?.messages.map((message) => (
                    <article
                      key={message.id}
                      className={`message ${message.role}`}
                    >
                      <div className="message-byline">
                        {message.role === "assistant" ? (
                          <Mark small />
                        ) : (
                          <span className="user-avatar">Y</span>
                        )}
                        <strong>
                          {message.role === "assistant" ? "Forma" : "You"}
                        </strong>
                        <time>
                          {new Date(message.created * 1000).toLocaleTimeString(
                            [],
                            { hour: "2-digit", minute: "2-digit" },
                          )}
                        </time>
                      </div>
                      <div className="message-content">{message.content}</div>
                    </article>
                  ))}
                  {running && (
                    <div className="working-card" role="status">
                      <span className="spinner" />
                      <div>
                        <strong>
                          {latest
                            ? eventLabel(latest)
                            : "Starting the workflow"}
                        </strong>
                        <span>Following the actual generation pipeline</span>
                      </div>
                      <button
                        className="icon-button"
                        aria-label="Show activity"
                        onClick={() => setTab("activity")}
                      >
                        <Icon name="activity" />
                      </button>
                    </div>
                  )}
                  {project?.status === "awaiting_input" && (
                    <div className="input-needed">
                      <span className="status-dot" />
                      Reply below to continue this project.
                    </div>
                  )}
                  {project?.recovery && (
                    <button
                      className="text-button retry-button"
                      disabled={busy}
                      onClick={() =>
                        void submit(undefined, project.recovery?.action)
                      }
                    >
                      <Icon name="refresh" size={15} />
                      {project.recovery.label}
                    </button>
                  )}
                  <div ref={messagesEnd} />
                </div>
              )}
            </div>
            <div className="composer-area">
              <form
                className={`composer ${running ? "is-running" : ""}`}
                onSubmit={(e) => void submit(e)}
              >
                <label htmlFor="message" className="sr-only">
                  {project?.status === "awaiting_input"
                    ? "Answer the agent"
                    : "Describe your project or requested changes"}
                </label>
                <textarea
                  ref={input}
                  id="message"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder={
                    running
                      ? "Your next thought can wait here…"
                      : project?.status === "awaiting_input"
                        ? "Tell me a little more…"
                        : id
                          ? "What would you like to change or explore?"
                          : "An idea, a rough brief, a “what if”…"
                  }
                  rows={3}
                  onKeyDown={(e) => {
                    if (
                      e.key === "Enter" &&
                      !e.shiftKey &&
                      !e.nativeEvent.isComposing
                    ) {
                      e.preventDefault();
                      void submit();
                    }
                  }}
                />
                <div className="composer-toolbar">
                  <div className="composer-options">
                    {!id ? (
                      <>
                        <label>
                          <span className="sr-only">Framework</span>
                          <select
                            value={framework}
                            onChange={(e) => setFramework(e.target.value)}
                          >
                            {(
                              boot?.frameworks || [
                                { id: "react", label: "React" },
                                { id: "vue", label: "Vue" },
                                { id: "svelte", label: "Svelte" },
                                { id: "next", label: "Next.js" },
                              ]
                            ).map((f) => (
                              <option key={f.id} value={f.id}>
                                {f.label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <button
                          type="button"
                          className="model-chip"
                          onClick={() => setSettings(true)}
                        >
                          <span className="status-dot green" />
                          {model || "Choose model"}
                        </button>
                      </>
                    ) : (
                      <span className="project-status">
                        <span
                          className={`status-dot ${project?.status === "ready" ? "green" : ""}`}
                        />
                        {statuses[project?.status || "idle"]}
                      </span>
                    )}
                  </div>
                  {running ? (
                    <button
                      type="button"
                      className="send-button stop-button"
                      disabled={busy}
                      aria-label="Stop generation"
                      onClick={() =>
                        void perform(() =>
                          api(`/projects/${id}/cancel`, { method: "POST" }),
                        )
                      }
                    >
                      <Icon name="stop" />
                    </button>
                  ) : (
                    <button
                      className="send-button"
                      disabled={
                        busy || !draft.trim() || !boot || loadingProject
                      }
                      aria-label="Send message"
                    >
                      {busy ? (
                        <span className="spinner" />
                      ) : (
                        <Icon name="arrow" />
                      )}
                    </button>
                  )}
                </div>
              </form>
              <div className="composer-caption">
                <span>
                  {boot?.runtime.runtime === "mock"
                    ? "Builds run on your machine without container isolation."
                    : "Your ideas, your files, your workspace."}
                </span>
                <span>
                  ↵ send <span className="caption-separator">·</span> ⇧↵ new
                  line
                </span>
              </div>
            </div>
          </section>
          <section className="artifact-panel" aria-label="Project artifacts">
            <div
              className="panel-tabs"
              role="tablist"
              aria-label="Workspace panels"
            >
              {[
                ["preview", "monitor", "Preview"],
                ["code", "code", "Code"],
                ["changes", "refresh", "Changes"],
                ["activity", "activity", "Activity"],
              ].map(([key, icon, label]) => (
                <button
                  key={key}
                  role="tab"
                  id={`tab-${key}`}
                  aria-controls="workspace-panel"
                  tabIndex={tab === key ? 0 : -1}
                  onKeyDown={(e) => {
                    const keys = ["preview", "code", "changes", "activity"];
                    const next =
                      e.key === "ArrowRight"
                        ? (keys.indexOf(key) + 1) % 4
                        : e.key === "ArrowLeft"
                          ? (keys.indexOf(key) + 3) % 4
                          : e.key === "Home"
                            ? 0
                            : e.key === "End"
                              ? 3
                              : -1;
                    if (next >= 0) {
                      e.preventDefault();
                      setTab(keys[next]);
                      document.getElementById(`tab-${keys[next]}`)?.focus();
                    }
                  }}
                  aria-selected={tab === key}
                  className={tab === key ? "active" : ""}
                  onClick={() => setTab(key)}
                >
                  <Icon name={icon} size={16} />
                  <span>{label}</span>
                  {key === "code" && !!project?.files.length && (
                    <small>{project.files.length}</small>
                  )}
                </button>
              ))}
            </div>
            <div
              className="panel-content"
              id="workspace-panel"
              role="tabpanel"
              aria-labelledby={`tab-${tab}`}
            >
              {tab === "preview" && (
                <>
                  {project?.preview.running ? (
                    <>
                      <div className="preview-toolbar">
                        <span>
                          <span className="status-dot green" />
                          Live build preview
                        </span>
                        <div>
                          <button
                            className={`icon-button ${!mobilePreview ? "selected" : ""}`}
                            title="Desktop viewport"
                            aria-label="Desktop viewport"
                            onClick={() => setMobilePreview(false)}
                          >
                            <Icon name="monitor" size={16} />
                          </button>
                          <button
                            className={`icon-button ${mobilePreview ? "selected" : ""}`}
                            title="Mobile viewport"
                            aria-label="Mobile viewport"
                            onClick={() => setMobilePreview(true)}
                          >
                            <Icon name="mobile" size={16} />
                          </button>
                          <a
                            className="icon-button"
                            title="Open preview in a new tab"
                            aria-label="Open preview in a new tab"
                            href={project.preview.url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <Icon name="external" size={15} />
                          </a>
                          <button
                            className="icon-button"
                            aria-label="Stop preview"
                            onClick={() =>
                              void perform(() =>
                                api(`/projects/${id}/preview`, {
                                  method: "DELETE",
                                }),
                              )
                            }
                          >
                            <Icon name="stop" size={15} />
                          </button>
                        </div>
                      </div>
                      <div
                        className={`preview-frame ${mobilePreview ? "mobile" : ""}`}
                      >
                        <iframe
                          title="Generated application preview"
                          src={project.preview.url}
                          sandbox="allow-scripts allow-forms allow-same-origin allow-downloads"
                        />
                      </div>
                    </>
                  ) : (
                    <div className="preview-empty">
                      <div className="artwork" aria-hidden="true">
                        <div className="artwork-window">
                          <span />
                          <span />
                          <span />
                          <div className="artwork-page">
                            <i />
                            <i />
                            <i />
                          </div>
                        </div>
                        <div className="artwork-orbit" />
                        <div className="artwork-label">IDEA → INTERFACE</div>
                        <div className="artwork-point" />
                      </div>
                      <span className="eyebrow">
                        THE OTHER SIDE OF THE CONVERSATION
                      </span>
                      <h2>
                        {running
                          ? "Work in progress."
                          : project?.state.build_succeeded
                            ? "Ready to explore."
                            : "Your ideas take shape here."}
                      </h2>
                      <p>
                        {running
                          ? "The agent is working on your application. A new preview will be available after verification."
                          : project?.state.build_succeeded
                            ? "Your application passed its build. Open it, try it, and tell the agent what comes next."
                            : "A live preview of what you’re making.\nIt starts with a message."}
                      </p>
                      {project?.state.build_succeeded && (
                        <button
                          className="primary-button"
                          disabled={busy || running}
                          onClick={() =>
                            void perform(() =>
                              api(`/projects/${id}/preview`, {
                                method: "POST",
                              }),
                            )
                          }
                        >
                          <Icon name="play" size={15} />
                          Open preview
                        </button>
                      )}
                      <div className="framework-stamps">
                        <span>REACT</span>
                        <i>+</i>
                        <span>NEXT.JS</span>
                        <i>+</i>
                        <span>VUE</span>
                        <i>+</i>
                        <span>SVELTE</span>
                      </div>
                    </div>
                  )}
                </>
              )}
              {tab === "code" && (
                <div className="code-workspace">
                  <div className="file-list">
                    <span className="eyebrow">PROJECT FILES</span>
                    {project?.files.length ? (
                      project.files.map((f) => (
                        <button
                          key={f.path}
                          className={file?.path === f.path ? "active" : ""}
                          onClick={() => void openFile(f.path)}
                        >
                          <Icon name="code" size={13} />
                          <span>{f.path}</span>
                        </button>
                      ))
                    ) : (
                      <p>Files appear as the agent creates your application.</p>
                    )}
                  </div>
                  <div className="source-view">
                    {file ? (
                      <>
                        <div className="source-heading">
                          <span>{file.path}</span>
                          <button
                            className="icon-button"
                            aria-label="Copy source"
                            onClick={() => {
                              void navigator.clipboard
                                .writeText(file.content)
                                .then(() => {
                                  setCopied(true);
                                  setTimeout(() => setCopied(false), 1500);
                                })
                                .catch(() =>
                                  setError("Could not copy to the clipboard."),
                                );
                            }}
                          >
                            <Icon name={copied ? "check" : "copy"} size={15} />
                          </button>
                        </div>
                        <pre tabIndex={0} aria-label={`Source of ${file.path}`}>
                          <code>
                            {file.content.split("\n").map((line, i) => (
                              <span className="source-line" key={i}>
                                <span
                                  className="line-number"
                                  aria-hidden="true"
                                >
                                  {i + 1}
                                </span>
                                <span>{line || " "}</span>
                              </span>
                            ))}
                          </code>
                        </pre>
                      </>
                    ) : (
                      <div className="panel-empty">
                        <Icon name="folder" size={30} />
                        <h3>A closer look.</h3>
                        <p>Select a file to inspect its source.</p>
                      </div>
                    )}
                  </div>
                </div>
              )}
              {tab === "changes" && (
                <div className="changes-view">
                  {project?.changes?.files.length ? (
                    project.changes.files.map((change) => (
                      <article key={change.path}>
                        <h3>
                          <Icon name="code" size={15} />
                          {change.path}
                        </h3>
                        <pre tabIndex={0}>
                          {change.diff.split("\n").map((line, i) => (
                            <span
                              key={i}
                              className={
                                line.startsWith("+")
                                  ? "diff-add"
                                  : line.startsWith("-")
                                    ? "diff-remove"
                                    : ""
                              }
                            >
                              {line}
                              {"\n"}
                            </span>
                          ))}
                        </pre>
                      </article>
                    ))
                  ) : (
                    <div className="panel-empty">
                      <Icon name="refresh" size={30} />
                      <h3>Room to evolve.</h3>
                      <p>
                        Ask for a change after generation.
                        <br />
                        The latest source diff will appear here.
                      </p>
                    </div>
                  )}
                </div>
              )}
              {tab === "activity" && (
                <div className="activity-view">
                  <div className="activity-title">
                    <h3>Behind the work</h3>
                    <span>LIVE WORKFLOW EVENTS</span>
                  </div>
                  {events.length ? (
                    <ol>
                      {events.map((event) => (
                        <li
                          key={event.id}
                          className={
                            event.type.includes("failed") ? "event-failed" : ""
                          }
                        >
                          <span className="event-marker">
                            {event.type.endsWith("completed") ||
                            event.type === "build_passed" ? (
                              <Icon name="check" size={12} />
                            ) : (
                              <span />
                            )}
                          </span>
                          <div>
                            <strong>{eventLabel(event)}</strong>
                            <time>
                              {new Date(
                                event.created * 1000,
                              ).toLocaleTimeString()}
                            </time>
                          </div>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <div className="panel-empty">
                      <Icon name="activity" size={30} />
                      <h3>Every step, in view.</h3>
                      <p>
                        Real workflow activity appears here
                        <br />
                        once you start a conversation.
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className={`diagnostics ${diagnostics ? "expanded" : ""}`}>
              <button
                className="diagnostics-toggle"
                aria-expanded={diagnostics}
                onClick={() => setDiagnostics(!diagnostics)}
              >
                <span>
                  <Icon name="terminal" size={15} />
                  Build & diagnostics
                </span>
                <span>
                  {project?.state.build_succeeded ? (
                    <>
                      <span className="status-dot green" />
                      {running ? "Previous build passed" : "Passed"}
                    </>
                  ) : project?.status === "failed" ? (
                    <>
                      <span className="status-dot red" />
                      Failed
                    </>
                  ) : (
                    <span className="muted">No verified build</span>
                  )}
                  <Icon name="chevron" size={13} />
                </span>
              </button>
              {diagnostics && (
                <div className="diagnostics-content">
                  <div className="diagnostics-actions">
                    <button
                      className="text-button"
                      disabled={!project?.state.blueprint || running || busy}
                      onClick={() => void submit(undefined, "build")}
                    >
                      <Icon name="play" size={13} />
                      Build project
                    </button>
                    {project?.state.project_root && (
                      <span title={project.state.project_root}>
                        {project.state.project_root}
                      </span>
                    )}
                  </div>
                  {project?.error && (
                    <p className="diagnostic-error">{project.error}</p>
                  )}
                  {commands.map((command, i) => (
                    <details key={i} open={!command.ok}>
                      <summary>
                        {command.ok ? "✓" : "×"} {command.action}
                      </summary>
                      <pre>
                        {command.stdout}
                        {"\n"}
                        {command.stderr}
                      </pre>
                    </details>
                  ))}
                  {!!project?.state.errors?.length && (
                    <details>
                      <summary>
                        Workflow errors ({project.state.errors.length})
                      </summary>
                      <pre>{JSON.stringify(project.state.errors, null, 2)}</pre>
                    </details>
                  )}
                  {!commands.length && !project?.error && (
                    <p>Build output will appear after the first validation.</p>
                  )}
                </div>
              )}
            </div>
          </section>
        </div>
      </main>
      {(settings || tokenNeeded) && (
        <div
          className="modal-backdrop"
          onMouseDown={(e) => {
            if (e.target === e.currentTarget && !tokenNeeded)
              setSettings(false);
          }}
        >
          <dialog
            ref={(node) => {
              if (node && !node.open) node.showModal();
            }}
            onCancel={(e) => {
              e.preventDefault();
              if (!tokenNeeded) setSettings(false);
            }}
            className="settings-dialog"
            aria-labelledby="settings-title"
            onKeyDown={(e) => {
              if (e.key === "Escape" && !tokenNeeded) setSettings(false);
            }}
          >
            <header>
              <div>
                <span className="eyebrow">YOUR LOCAL WORKSPACE</span>
                <h2 id="settings-title">
                  {tokenNeeded
                    ? "Connect to your studio."
                    : "Set the conditions."}
                </h2>
              </div>
              {!tokenNeeded && (
                <button
                  className="icon-button"
                  aria-label="Close settings"
                  onClick={() => setSettings(false)}
                >
                  <Icon name="close" />
                </button>
              )}
            </header>
            {tokenNeeded ? (
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  sessionStorage.setItem("forma-token", token);
                  void bootstrap();
                }}
              >
                <label>
                  Server access token
                  <input
                    type="password"
                    value={token}
                    onChange={(e) => setToken(e.target.value)}
                    autoFocus
                  />
                </label>
                <button className="primary-button">Connect</button>
              </form>
            ) : (
              <>
                <p className="settings-description">
                  Models do the thinking. Your machine keeps the work.
                  <br />
                  Choose defaults for a new project, or apply them to this
                  project before its next operation.
                </p>
                <label>
                  Inference provider
                  <select
                    value={provider}
                    onChange={(e) => chooseProvider(e.target.value)}
                  >
                    {providers.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Model
                  <input
                    list="models"
                    value={model}
                    onChange={(e) => setModel(e.target.value)}
                    placeholder="Enter a model available on your provider"
                  />
                  <datalist id="models">
                    {currentProvider?.models.map((m) => (
                      <option key={m} value={m} />
                    ))}
                  </datalist>
                </label>
                <div
                  className={`provider-notice ${currentProvider?.available ? "available" : ""}`}
                >
                  <Icon
                    name={currentProvider?.available ? "check" : "info"}
                    size={17}
                  />
                  <p>
                    {currentProvider?.available
                      ? `${currentProvider.models.length} model${currentProvider.models.length === 1 ? "" : "s"} discovered. Generation depends on the selected model’s coding and structured-output capabilities.`
                      : currentProvider?.error || "Checking the provider…"}
                  </p>
                </div>
                <button
                  className="text-button"
                  onClick={() =>
                    void api<Provider[]>("/providers")
                      .then(setProviders)
                      .catch((e) => setError(e.message))
                  }
                >
                  <Icon name="refresh" size={14} />
                  Check connection
                </button>
                <div className="environment-details">
                  <span>Execution</span>
                  <strong>
                    {boot?.runtime.runtime === "mock"
                      ? "Host process"
                      : boot?.runtime.runtime}
                  </strong>
                  <span>Documentation</span>
                  <strong>{boot?.retrieval}</strong>
                  <span>Project storage</span>
                  <code>{boot?.data_directory}</code>
                </div>
                {boot?.runtime.runtime === "mock" && (
                  <p className="host-warning">
                    Generated build code runs on the host. Use Docker for
                    isolated execution of untrusted projects.
                  </p>
                )}
                <footer>
                  {id && (
                    <button
                      className="text-button"
                      disabled={running || busy || !model.trim()}
                      onClick={() =>
                        void perform(async () => {
                          await api(`/projects/${id}/model`, {
                            method: "PATCH",
                            body: JSON.stringify({
                              provider,
                              model: model.trim(),
                            }),
                          });
                          setSettings(false);
                        })
                      }
                    >
                      Use for this project
                    </button>
                  )}
                  <button
                    className="primary-button"
                    onClick={() => setSettings(false)}
                  >
                    Back to the workspace
                    <Icon name="chevron" size={14} />
                  </button>
                </footer>
              </>
            )}
          </dialog>
        </div>
      )}
    </div>
  );
}
