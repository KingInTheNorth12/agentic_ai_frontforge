export type ProjectSummary = {
  id: string;
  title: string;
  framework: string;
  provider: string;
  model: string;
  status: string;
  error: string | null;
  updated: number;
};
export type Message = {
  id: number;
  role: string;
  content: string;
  created: number;
};
export type AgentEvent = {
  id: number;
  type: string;
  data: Record<string, unknown>;
  created: number;
};
export type Project = ProjectSummary & {
  changes?: { files: { path: string; diff: string }[] };
  recovery?: { action: string; label: string } | null;
  messages: Message[];
  files: { path: string; size: number }[];
  preview: { running: boolean; url?: string };
  state: {
    phase?: string;
    project_root?: string;
    build_succeeded?: boolean;
    repair_attempts?: number;
    clarification_questions?: { key: string; prompt: string }[];
    package_report?: {
      resolution_commands?: {
        action: string;
        ok: boolean;
        stdout: string;
        stderr: string;
      }[];
      commands?: {
        action: string;
        ok: boolean;
        stdout: string;
        stderr: string;
      }[];
      locked_versions?: Record<string, string>;
    };
    errors?: Record<string, unknown>[];
    edit_report?: { files?: { path: string; diff: string }[] };
    blueprint?: { summary?: string };
  };
};
export type Bootstrap = {
  csrf_token: string;
  frameworks: { id: string; label: string }[];
  runtime: { runtime: string; available: boolean; details: string };
  active_project: string | null;
  retrieval: string;
  data_directory: string;
};
export type Provider = {
  id: string;
  label: string;
  configured: boolean;
  available: boolean;
  models: string[];
  default_model: string;
  error?: string;
};
let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
function headers() {
  return {
    "Content-Type": "application/json",
    "X-Studio-CSRF": csrf,
    ...(sessionStorage.getItem("forma-token")
      ? { Authorization: `Bearer ${sessionStorage.getItem("forma-token")}` }
      : {}),
  };
}
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers: { ...headers(), ...options.headers },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      response.status === 401
        ? "ACCESS_TOKEN_REQUIRED"
        : typeof data.detail === "string"
          ? data.detail
          : `Request failed (${response.status})`,
    );
  }
  return response.json();
}
export async function watchEvents(
  id: string,
  after: number,
  onEvent: (e: AgentEvent) => void,
  signal: AbortSignal,
  onOpen: () => void,
) {
  const response = await fetch(`/api/projects/${id}/events?after=${after}`, {
    headers: headers(),
    signal,
  });
  if (!response.ok || !response.body)
    throw new Error("Activity connection lost");
  onOpen();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end;
      while ((end = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        const data = frame
          .split("\n")
          .find((line) => line.startsWith("data: "));
        if (data) onEvent(JSON.parse(data.slice(6)));
      }
    }
  } finally {
    reader.releaseLock();
  }
}
