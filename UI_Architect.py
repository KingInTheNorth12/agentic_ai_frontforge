import json, os, re
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
from tools import sanitize_project_name, normalize_path, safe_project_path, reset_output_folder, create_folder, create_empty_file, save_json

UI_ARCHITECT_PROMPT = ("""You are a Software UI Architect Agent.

Input is planner.json from another AI. Its schema can be anything.

Your job is to understand the planner and return one normalized UI blueprint.

Rules:
Return only valid JSON.
Do not generate code.
Do not generate JSX.
Do not generate HTML.
Do not generate CSS.
Do not add markdown.
Do not explain anything.
Do not depend on fixed planner keys.
Do not invent major pages.
Do not invent major features.
Reuse all pages, routes, components, dependencies, assets, styling rules, folders, and files found in planner.
If folders or files are missing, infer the minimum useful ones.
All paths must be relative.
No path may start with /.
Every filesystem item must explicitly say kind as "directory" or "file".
Every page must have a file path.
Every component must have a file path.
Every route must point to a page/component file.
For React projects, include src/main.jsx and src/App.jsx.
For React projects, page files should usually go inside src/pages.
For React projects, layout components should usually go inside src/components/layout.
For React projects, feature components should usually go inside src/components/features.
For React projects, common reusable components should usually go inside src/components/common.
If routing exists, include src/routes/AppRoutes.jsx.
If hardcoded data is needed, include a file inside src/data.
If global styling is needed, include a style file.
Do not rely on file extensions.
Unknown files, extensionless files, and custom framework files are allowed.
Different planner.json should produce a different filesystem.
All JSON strings must be valid.
Escape double quotes inside strings.
Do not use trailing commas.
Do not use comments.
Do not use undefined.
Use null if a value is unknown.

Planner JSON:

{planner_json}

Return this JSON shape only:

{
  "project_name": "",
  "framework": "",
  "project_type": "",
  "summary": "",
  "filesystem": [],
  "pages": [],
  "components": [],
  "routes": [],
  "layouts": [],
  "assets": [],
  "data_sources": [],
  "dependencies": [],
  "styling": {},
  "component_hierarchy": {},
  "build_order": [],
  "unknowns": [],
  "handoff": {
    "component_agent": "",
    "styling_agent": "",
    "package_manager_agent": "",
    "reviewer_agent": ""
  }
}""")

llm = ChatOllama(model="qwen3:latest", temperature=0, format="json")
CLEAN_OUTPUT_FOLDER = True

#make planner file
#planner_agent.main()

def load_planner(path="planner.json"):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def json_from_text(text):
    if not isinstance(text, str):
        raise ValueError("Response is not text")
    text = text.strip().replace("```json", "").replace("```JSON", "").replace("```", "").strip()
    a, b = text.find("{"), text.rfind("}")
    if a == -1 or b == -1 or b <= a:
        raise ValueError("No JSON object found")
    return text[a:b + 1]

def repair_json(bad):
    prompt = f"""
Fix this invalid JSON.
Return only valid JSON.
Do not explain.
Do not add markdown.
Escape invalid double quotes.
Remove trailing commas.
Replace undefined with null.

Invalid JSON:

{bad}
"""
    raw = llm.invoke([HumanMessage(content=prompt)]).content
    return json.loads(json_from_text(raw))

def generate_blueprint(planner_json):
    prompt = UI_ARCHITECT_PROMPT.replace("{planner_json}", planner_json)
    raw = llm.invoke([HumanMessage(content=prompt)]).content
    candidate = json_from_text(raw)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return repair_json(candidate)

def as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]

def norm_kind(kind):
    if not isinstance(kind, str):
        return None
    kind = kind.strip().lower()
    if kind in ["directory", "folder", "dir"]:
        return "directory"
    if kind in ["file", "source_file", "config_file", "asset_file"]:
        return "file"
    return None

def looks_like_file(path):
    path = normalize_path(path)
    return bool(path and not path.endswith("/") and "." in os.path.basename(path))

def pascal_case(name):
    if not isinstance(name, str) or not name.strip():
        return "Unnamed"
    parts = re.split(r"[^a-zA-Z0-9]+", name)
    return "".join(x[:1].upper() + x[1:] for x in parts if x)

def add_dir(folders, path):
    path = normalize_path(path)
    if path:
        folders.add(path)

def add_file(folders, files, path):
    path = normalize_path(path)
    if path:
        files.add(path)
        parent = os.path.dirname(path)
        if parent:
            folders.add(parent)

def join_path(base, child):
    child = normalize_path(child)
    if base and child:
        return normalize_path(os.path.join(base, child))
    return child

def add_by_kind(folders, files, path, kind=None, default=None):
    path = normalize_path(path)
    kind = norm_kind(kind)

    if not path:
        return

    if kind == "directory":
        add_dir(folders, path)
    elif kind == "file":
        add_file(folders, files, path)
    elif default == "directory":
        add_dir(folders, path)
    elif default == "file":
        add_file(folders, files, path)
    elif looks_like_file(path):
        add_file(folders, files, path)
    else:
        add_dir(folders, path)

def consume(item, folders, files, base=None, default=None):
    if isinstance(item, str):
        add_by_kind(folders, files, join_path(base, item), default=default)
        return

    if isinstance(item, list):
        for x in item:
            consume(x, folders, files, base, default)
        return

    if not isinstance(item, dict):
        return

    current = base
    kind = norm_kind(item.get("kind") or item.get("type"))
    path = item.get("path") or item.get("filepath") or item.get("file_path")

    if isinstance(path, str):
        full = join_path(base, path)
        add_by_kind(folders, files, full, kind=kind, default=default)
        current = os.path.dirname(normalize_path(full)) if kind == "file" or looks_like_file(full) else normalize_path(full)

    folder = item.get("folder") or item.get("directory") or item.get("dir")
    file = item.get("file") or item.get("source_file") or item.get("config_file")

    if isinstance(folder, str):
        current = join_path(base, folder)
        add_dir(folders, current)

    if isinstance(file, str):
        add_file(folders, files, join_path(base, file))

    for k in ["children", "items"]:
        for x in as_list(item.get(k)):
            consume(x, folders, files, current)

    for k in ["subfolders", "directories", "folders"]:
        for x in as_list(item.get(k)):
            consume(x, folders, files, current)

    for k in ["files", "file_list"]:
        for x in as_list(item.get(k)):
            consume(x, folders, files, current, "file")

def normalize_blueprint(bp):
    if not isinstance(bp, dict):
        raise ValueError("Blueprint must be object")

    defaults = {
        "project_name": "frontend-project",
        "framework": "React",
        "project_type": "",
        "summary": "",
        "filesystem": [],
        "pages": [],
        "components": [],
        "routes": [],
        "layouts": [],
        "entrypoints": [],
        "assets": [],
        "data_sources": [],
        "dependencies": [],
        "styling": {},
        "component_hierarchy": {},
        "build_order": [],
        "unknowns": [],
        "handoff": {
            "component_agent": "",
            "styling_agent": "",
            "package_manager_agent": "",
            "reviewer_agent": ""
        }
    }

    for k, v in defaults.items():
        bp.setdefault(k, v)

    for k in ["filesystem", "pages", "components", "routes", "layouts", "entrypoints",
          "assets", "data_sources", "dependencies", "build_order", "unknowns"]:
        bp[k] = as_list(bp.get(k))

    for k in ["styling", "component_hierarchy", "handoff"]:
        if not isinstance(bp.get(k), dict):
            bp[k] = defaults[k]

    return bp

def infer_missing_react_files(bp, folders, files):
    framework = str(bp.get("framework", "")).lower()

    if "react" not in framework and "vite" not in framework:
        return

    for folder in [
        "public", "src", "src/components", "src/components/common",
        "src/components/features", "src/components/layout",
        "src/pages", "src/routes", "src/styles", "src/data",
        "src/hooks", "src/utils"
    ]:
        add_dir(folders, folder)

    add_file(folders, files, "package.json")

    entrypoints = bp.setdefault("entrypoints", [])

    def register_entrypoint(path, etype):
        add_file(folders, files, path)
        if not any(e.get("path") == path for e in entrypoints if isinstance(e, dict)):
            entrypoints.append({"path": path, "type": etype})

    register_entrypoint("src/main.jsx", "entry")
    register_entrypoint("src/App.jsx", "app_root")

    if bp.get("routes"):
        register_entrypoint("src/routes/AppRoutes.jsx", "routes")

    if bp.get("styling"):
        register_entrypoint("src/styles/globals.css", "stylesheet")

    for page in bp.get("pages", []):
        if not isinstance(page, dict):
            continue
        name = page.get("name") or page.get("component_name") or page.get("page")
        if not name:
            continue
        if not page.get("file"):
            page["file"] = f"src/pages/{pascal_case(name)}.jsx"
        add_file(folders, files, page["file"])

    for route in bp.get("routes", []):
        if not isinstance(route, dict):
            continue
        name = route.get("page") or route.get("component") or route.get("component_name")
        if name and not route.get("file"):
            route["file"] = f"src/pages/{pascal_case(name)}.jsx"
        if route.get("file"):
            add_file(folders, files, route["file"])

    for component in bp.get("components", []):
        if not isinstance(component, dict):
            continue
        name = component.get("name") or component.get("component_name")
        if not name:
            continue
        if not component.get("file"):
            ctype = str(component.get("type", "")).lower()
            pname = pascal_case(name)
            if "layout" in ctype or pname in ["Navbar", "Footer", "Sidebar", "Header"]:
                component["file"] = f"src/components/layout/{pname}.jsx"
            elif "feature" in ctype:
                component["file"] = f"src/components/features/{pname}.jsx"
            else:
                component["file"] = f"src/components/common/{pname}.jsx"
        add_file(folders, files, component["file"])

    for layout in bp.get("layouts", []):
        if not isinstance(layout, dict):
            continue
        name = layout.get("name") or layout.get("component_name")
        if not name:
            continue
        if not layout.get("file"):
            layout["file"] = f"src/components/layout/{pascal_case(name)}.jsx"
        add_file(folders, files, layout["file"])

def collect_paths(bp):
    folders, files = set(), set()

    for key, default in [
        ("filesystem", None),
        ("directories_to_create", "directory"),
        ("files_to_create", "file"),
        ("folder_structure", None),
        ("file_structure", "file")
    ]:
        consume(bp.get(key, []), folders, files, default=default)

    for key in ["pages", "components", "routes", "layouts", "assets", "data_sources"]:
        for item in as_list(bp.get(key)):
            if isinstance(item, dict):
                if isinstance(item.get("file"), str):
                    add_file(folders, files, item["file"])
                if isinstance(item.get("path"), str) and norm_kind(item.get("kind")):
                    add_by_kind(folders, files, item["path"], kind=item.get("kind"))

    for item in as_list(bp.get("build_order")):
        if isinstance(item, str):
            add_file(folders, files, item)
        elif isinstance(item, dict) and isinstance(item.get("file"), str):
            add_file(folders, files, item["file"])

    infer_missing_react_files(bp, folders, files)

    folders = sorted({normalize_path(x) for x in folders if normalize_path(x)})
    files = sorted({normalize_path(x) for x in files if normalize_path(x)})

    return folders, files

def create_scaffold(root, folders, files):
    made_folders, made_files = [], []

    for folder in folders:
        path = safe_project_path(root, folder)
        if path:
            create_folder(path)
            made_folders.append(folder)

    for file in files:
        path = safe_project_path(root, file)
        if path:
            create_empty_file(path)
            made_files.append(file)

    return made_folders, made_files

def main():
    planner = load_planner()
    bp = normalize_blueprint(generate_blueprint(planner))

    name = bp.get("project_name", "frontend-project")
    folder = sanitize_project_name(name)

    folders, files = collect_paths(bp)

    bp["project_folder"] = folder
    bp["directories_to_create"] = folders
    bp["files_to_create"] = files
    bp["filesystem"] = (
        [{"path": x, "kind": "directory"} for x in folders]
        +
        [{"path": x, "kind": "file"} for x in files]
    )
    bp["metadata_file"] = "ui_blueprint.json"

    reset_output_folder(folder, clean=CLEAN_OUTPUT_FOLDER)
    made_folders, made_files = create_scaffold(folder, folders, files)

    save_json(os.path.join(folder, "ui_blueprint.json"), bp)

    print("\nUI Architect completed")
    print("Project Folder :", folder)
    print("Blueprint      :", os.path.join(folder, "ui_blueprint.json"))
    print("Folders        :", len(made_folders))
    print("Files          :", len(made_files))

if __name__ == "__main__":
    main()