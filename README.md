# Forma — a conversational frontend studio

Describe an application, answer the agent's questions, inspect its files and build results, open a preview, and continue developing through conversation. Forma connects a React/TypeScript workspace to the existing Python/LangGraph generation engine. The original CLI remains available.

The interface uses actual workflow events, saved projects, and production build output. It does not simulate generation or preview results.

## Run locally

Requirements: Python 3.11+, Node **22.12+** (or another Vite 8 supported release), npm, and an inference endpoint. Ollama is the default; no account or paid API is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
npm --prefix web ci
npm --prefix web run build
```

Start Ollama separately and install a coding-capable model, for example:

```bash
ollama serve
# In another terminal, if the model is not already installed:
ollama pull qwen3:latest
```

Start the studio from this repository:

```bash
source .venv/bin/activate
python -m studio
```

Open **http://127.0.0.1:8787**. Choose a framework and model, then describe a small application. Local models may take several minutes per project. Coding ability, structured output, available memory, and context capacity affect results; a successful build is necessary but does not prove every requested behavior is correct.

The built UI lives in `studio/static/` and is included in Python wheels built **after** the UI build. For UI development, keep the API running and run `npm --prefix web run dev`; Vite proxies `/api` to port 8787. Python changes require an API restart. Use one API process per data directory.

## The workspace

- **Conversation:** initial requests, clarification answers, source edits, and questions about the current application.
- **Preview:** serves a successful production build on a separate local origin. It stops before another operation modifies the project.
- **Code:** inspect generated source, configuration, and reports. Hidden files, dependencies, build trees, and paths outside the project are excluded.
- **Changes:** the most recent agent edit as a source diff. Previous file contents are kept in the project's `.agent/revisions/` directory.
- **Activity:** real orchestration and file events, replayed after reconnecting.
- **Build & diagnostics:** actual install/build output, validation errors, a rebuild action, and the output directory.

One agent operation and one preview run at a time. Refreshing the browser leaves the worker running. Drafts are retained in that browser. Conversation, events, and checkpoints are stored on disk. Stop cancels the owned worker; recovery offers a checkpoint resume only when unfinished graph work actually exists. Completed build failures offer a rebuild; early failures can retry generation.

Follow-up edits use the same project and installed dependencies. The editor can change or add source files, but cannot change package manifests, delete files, or freely execute shell commands. Large source contexts exceeding 180,000 characters are rejected with an explanation.

## Frameworks

| Target | Generated structure | Production output |
| --- | --- | --- |
| React | Vite, JSX, React Router, CSS | `dist/` |
| Vue | Vite, Vue single-file components, Vue Router, CSS | `dist/` |
| Svelte | Vite, Svelte 5 components, client-side path selection, CSS | `dist/` |
| Next.js | App Router, static route wrappers, JSX, CSS | `out/` |

Support is frontend-only with static routes. Next.js uses static export: API routes, server actions, request-time rendering, and dynamic server features are outside the current contract. Svelte uses Vite rather than SvelteKit. Native templates for all four targets are exercised by `scripts/check_frameworks.py` with real npm installations/builds.

## Models and configuration

See [.env.example](.env.example) for supported settings. The Python application reads the process environment; it does **not** automatically load `.env`. Export settings or source a trusted local `.env` with `set -a; source .env; set +a` before starting. Never commit credentials.

Ollama model discovery uses `/api/tags`. Qwen3 defaults to disabled reasoning for predictable structured output latency; set `FRONTEND_AGENT_REASONING=true` to experiment. Ollama context defaults to 16,384 tokens; `FRONTEND_AGENT_CONTEXT_LENGTH` trades memory for context, and `FRONTEND_AGENT_MAX_TOKENS` limits the response (default 8,192). An embedding model is unnecessary while retrieval is disabled. Environment & models can apply a different provider/model to a stopped project before its next operation.

For a compatible local or cloud Chat Completions endpoint:

```bash
export FRONTEND_AGENT_API_BASE_URL=https://your-provider.example/v1
export FRONTEND_AGENT_API_MODEL=your-model-id
export FRONTEND_AGENT_API_KEY=your-server-side-key
export FRONTEND_AGENT_STRUCTURED_OUTPUT=json_mode
```

Choose **Compatible API** in Environment & models. `/models` discovery is best-effort; a model can be entered manually. Supported structured output modes are `json_mode`, `json_schema`, and `function_calling`; select a mode supported by that endpoint and model. JSON mode includes the schema in the prompt and validates the result locally. Providers without supported structured output are not silently treated as compatible. Provider keys stay in the server environment and are not sent to the UI or build subprocesses.

## Storage and execution

Studio data defaults to `.frontend_agent/` under the configured workspace. Set `FRONTEND_AGENT_DATA_DIR` to relocate it. It contains:

```text
studio.sqlite          projects, messages, durable events
checkpoints.sqlite     LangGraph workflow checkpoints
store.sqlite           project decisions and dependency memory
projects/<project-id>/ generated source and reports
```

Back up the entire data directory with the server stopped. Existing CLI projects are preserved but not automatically imported into studio history. CLI generation continues to use unique output directories; studio edits deliberately reuse the managed project directory.

Docker or Apple Container provides build containment when available. Without either, the existing `mock` backend executes **real npm commands on the host**, with a restricted environment but no filesystem/network isolation. The UI identifies this mode. Do not confuse it with simulated builds. For untrusted requests, use `FRONTEND_AGENT_SANDBOX_BACKEND=docker` and a working Docker runtime. Dependency install lifecycle scripts remain disabled by default. The registry/lockfile phase has network access; containerized build execution disables it.

## Documentation retrieval

The existing Chroma pipeline is retained but disabled by default. The inspected collection contained **zero embedded records**; earlier ingestion recorded library/source, but not versions. Unversioned documentation can no longer silently enter generation. Optional retrieval requires a matching library and installed major version:

```bash
python -m rag.ingest /path/to/official/react/docs --library react --version 19.3.0
export FRONTEND_AGENT_RAG=1
```

This optional path requires the configured Ollama embedding model. A retrieval miss or unavailable embedding service falls back to the native framework contract and exact dependency versions. Major matching reduces conflicts; it does not guarantee minor-version compatibility. See [architecture](docs/ARCHITECTURE.md) for the investigation and decision.

## Verification and development

```bash
python -m pytest -q
python -m pip check
npm --prefix web run build
python scripts/check_frameworks.py --output /tmp/forma-framework-check
python -m pip wheel --no-deps --no-build-isolation . -w /tmp/forma-wheels
```

The framework check needs npm registry access and writes disposable generated projects to the chosen directory. It verifies native template/build contracts without claiming that a model generated those fixtures. Real model/browser verification and limitations are recorded in [verification](docs/VERIFICATION.md).

Legacy entry points remain `frontend-agent --help`, `frontend-agent doctor`, `frontend-agent run "Build a landing page"`, and `python main.py --thread my-project`.

## Deployment

Nothing has been deployed. [Deployment preparation](docs/DEPLOYMENT.md) describes private hosting, persistent storage, inference, separate preview origins, free-tier constraints, and the additional work required before public multi-user use.
