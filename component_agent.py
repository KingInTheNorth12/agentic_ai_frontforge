import json, os, re, glob
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
from tools import save_json
from retriever import retrieve
 
 
 
COMPONENT_AGENT_PROMPT = ("""
You are the Component Agent in a frontend generation pipeline.
Generate a single, complete React component.
 
Component spec (from UI Architect): {component_json}
Component name: {component_name}
Component type: {component_type}
Styling library: {styling}
 
Requirements from the project plan (from Planner Agent):
{planner_context}
 
Relevant documentation context (may be empty):
{context}
 
Rules:
- Return ONLY the code, no explanation, no markdown fences.
- Use functional components with hooks.
- Use {styling} for all styling — no inline styles unless {styling} requires it.
- Include a default export.
- Use hardcoded/mock data where dynamic data would normally come from a backend.
""")


ENTRYPOINT_PROMPT = ("""
You are the Component Agent generating a project entrypoint file.

File path: {path}
Entrypoint type: {etype}
Framework: React
Styling library: {styling}
Routes (if relevant): {routes_json}
Pages available (if relevant): {names_json}

Requirements from the project plan:
{planner_context}

Relevant documentation context (may be empty):
{context}

Rules:
- Return ONLY the code, no explanation, no markdown fences.
- If etype is "entry": write src/main.jsx that mounts <App /> to #root using createRoot.
- If etype is "app_root": write src/App.jsx that renders the router (e.g. <AppRoutes /> or <BrowserRouter>) and any global layout wrapper.
- If etype is "routes": write src/routes/AppRoutes.jsx defining all given routes with react-router-dom, importing each page component from its correct path under src/pages.
- If etype is "stylesheet": write plain CSS (or Tailwind directives if styling library is tailwind) for globals.css — do not write JSX, do not include a default export.
""")
 
 
llm = ChatOllama(model="qwen3:latest",
                temperature=0.4,
                validate_model_on_init=True)
 
# Locating and finding things
 
def find_latest_blueprint():
    """ui_architect.py doesn't tell us the folder name directly, so we just
    grab the most recently written ui_blueprint.json on disk."""
    matches = glob.glob("*/ui_blueprint.json")
    if not matches:
        raise FileNotFoundError("No ui_blueprint.json found. Run ui_architect.py first.")
    return max(matches, key=os.path.getmtime)
 
def load_blueprint(path=None):
    path = path or find_latest_blueprint()
    with open(path, "r", encoding="utf-8") as f:
        bp = json.load(f)
    project_folder = os.path.dirname(path)
    return bp, project_folder
 
def load_planner(project_folder, path="planner.json"):
    """planner.json is written by the Planner Agent before ui_architect.py
    ever runs (ui_architect.py itself reads it from the root via load_planner()
    in that file). Check inside the project folder first in case a copy was
    placed there, then fall back to the root where the Planner Agent writes it."""
    candidates = [os.path.join(project_folder, "planner.json"), path]
    for candidate in candidates:
        if os.path.exists(candidate):
            with open(candidate, "r", encoding="utf-8") as f:
                return json.load(f)
    print(f"[Component Agent] Warning: planner.json not found, continuing with blueprint only.")
    return {}
 
#Matching blueprint item to planner json entry
 
def normalize_name(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())
 
def name_from_path(path):
    """Fallback: derive a component name from its filename, e.g.
    'src/components/Footer.jsx' -> 'Footer'."""
    return os.path.splitext(os.path.basename(str(path)))[0]
 
def find_planner_entry(name, planner):
    """planner.json's exact shape depends on your Planner Agent's output
    format. This checks two shapes:
      1. A dict mapping component name -> description
         (e.g. planner["components"] = {"Navbar": "description", ...})
      2. Any list found anywhere in planner.json containing dicts with a
         name/title/component/page field that matches.
    """
    target = normalize_name(name)
    if not target or not isinstance(planner, dict):
        return {}
 
    # Shape 1: dict-of-descriptions (matches this project's planner.json)
    for key, value in planner.items():
        if isinstance(value, dict):
            for sub_key, sub_val in value.items():
                if normalize_name(sub_key) == target and isinstance(sub_val, str):
                    return {"description": sub_val}
 
    # Shape 2: list of dicts with identifying fields
    for value in planner.values():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            item_name = (
                item.get("name") or item.get("component_name")
                or item.get("page") or item.get("title")
            )
            if item_name and normalize_name(item_name) == target:
                return item
    return {}
 
def summarize_planner_entry(entry):
    """Pull out just the descriptive fields the LLM actually needs —
    dumping the whole entry adds noise (ids, ordering, etc.)."""
    if not entry:
        return ""
    fields = ["description", "purpose", "functionality", "props", "requirements", "notes", "details"]
    parts = [f"{f}: {entry[f]}" for f in fields if entry.get(f)]
    return "\n".join(parts)
 
 
 
def get_context(component_name, component_type, styling):
    """Query the RAG retriever for docs relevant to this component.
    retrieve() in RAG_Pipeline/retriever.py takes only a query string —
    it returns retriever.invoke(query) on a Chroma retriever configured
    with search_kwargs={"k": 5}, i.e. a List[Document]. Wrapped in
    try/except so a retriever error never kills the whole generation run."""
    query = f"{component_type} component using {styling} similar to {component_name}"
    try:
        results = retrieve(query)
    except Exception as e:
        print(f"[Component Agent] RAG retrieval failed for '{component_name}': {e}")
        return ""
 
    if not results:
        return ""
 
    # Normalize whatever retrieve() returns into a list of plain strings.
    # Primary case: List[Document] -> use .page_content.
    chunks = []
    for r in results:
        if isinstance(r, str):
            chunks.append(r)
        elif hasattr(r, "page_content"):          # LangChain Document (expected case)
            chunks.append(r.page_content)
        elif isinstance(r, dict) and r.get("text"):  # {"text": ..., "score": ...}
            chunks.append(r["text"])
        elif isinstance(r, (list, tuple)) and r:   # (text, score) tuple
            chunks.append(str(r[0]))
 
    return "\n---\n".join(chunks)
# Code Extraction
 
def code_from_text(text):
    if not isinstance(text, str):
        raise ValueError("Response is not text")
    text = text.strip()
    # pull out a fenced code block if present, else assume the whole reply is code
    match = re.search(r"```(?:jsx|tsx|javascript|js)?\s*(.*?)```", text, re.DOTALL)
    code = match.group(1).strip() if match else text
    return code
 
# Generation
def generate_entrypoint_code(item, bp, planner):
    path = item.get("path")
    etype = item.get("type", "entry")
    styling = bp.get("styling", {}).get("library", "tailwind")
    context = get_context(path, etype, styling)

    name = name_from_path(path)
    planner_entry = find_planner_entry(name, planner)
    planner_context = summarize_planner_entry(planner_entry)

    names_json = json.dumps([
        p.get("path") for p in bp.get("pages", []) if isinstance(p, dict)
    ])

    prompt = (
        ENTRYPOINT_PROMPT
        .replace("{path}", path)
        .replace("{etype}", etype)
        .replace("{styling}", styling)
        .replace("{routes_json}", json.dumps(bp.get("routes", [])))
        .replace("{names_json}", names_json)
        .replace("{planner_context}", planner_context)
        .replace("{context}", context)
    )

    raw = llm.invoke([HumanMessage(content=prompt)]).content
    return code_from_text(raw)



def generate_component_code(component, bp, planner):
    name = (
        component.get("name")
        or component.get("component_name")
        or (name_from_path(component["path"]) if component.get("path") else "UnnamedComponent")
    )
    ctype = component.get("type", "common")
    styling = bp.get("styling", {}).get("library", "tailwind")
    context = get_context(name, ctype, styling)
 
    planner_entry = find_planner_entry(name, planner)
    planner_context = summarize_planner_entry(planner_entry)
 
    prompt = (
        COMPONENT_AGENT_PROMPT
        .replace("{component_name}", name)
        .replace("{component_type}", ctype)
        .replace("{styling}", styling)
        .replace("{context}", context)
        .replace("{planner_context}", planner_context)
        .replace("{component_json}", json.dumps(component))
    )
 
    raw = llm.invoke([HumanMessage(content=prompt)]).content
    code = code_from_text(raw)
 
    return code
 
# Writing 
 
def write_code(project_folder, relative_path, code):
    full_path = os.path.join(project_folder, relative_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w", encoding="utf-8") as f:
        f.write(code)
 
def process_items(items, bp, planner, project_folder, log):
    for item in items:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        name = item.get("name") or item.get("component_name") or name_from_path(item["path"])
        try:
            code = generate_component_code(item, bp, planner)
            write_code(project_folder, item["path"], code)
            log["success"].append(name)
            print(".")
        except Exception as e:
            log["failed"].append({"name": name, "error": str(e)})



def process_entrypoints(items, bp, planner, project_folder, log):
    for item in items:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        name = name_from_path(item["path"])
        try:
            code = generate_entrypoint_code(item, bp, planner)
            write_code(project_folder, item["path"], code)
            log["success"].append(name)
        except Exception as e:
            log["failed"].append({"name": name, "error": str(e)})
 
def main():
    bp, project_folder = load_blueprint()
    planner = load_planner(project_folder)
    log = {"success": [], "failed": []}
 
    process_items(bp.get("components", []), bp, planner, project_folder, log)
    process_items(bp.get("pages", []), bp, planner, project_folder, log)
    process_items(bp.get("layouts", []), bp, planner, project_folder, log)
    process_entrypoints(bp.get("entrypoints", []), bp, planner, project_folder, log)
 
    save_json(os.path.join(project_folder, "component_agent_log.json"), log)
 
    print("\nComponent Agent completed")
    print("Project Folder :", project_folder)
    print("Generated      :", len(log["success"]))
    print("Failed         :", len(log["failed"]))
    if log["failed"]:
        print("Failed items   :", [f["name"] for f in log["failed"]])
 
if __name__ == "__main__":
    main()