import planner_agent
import json, os, re, subprocess, shutil, stat
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage
from tools import (
    sanitize_project_name,
    normalize_path,
    safe_project_path,
    reset_output_folder,
    create_folder,
    create_empty_file,
    save_json,
)

UI_ARCHITECT_PROMPT = """You are a Software UI Architect Agent.

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
  "framework": "React",
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
}"""

llm = ChatOllama(model="qwen3:latest", temperature=0, format="json")
CLEAN_OUTPUT_FOLDER = True


def _npm_executable():
    """Resolve the npm binary cross-platform (npm.cmd on Windows)."""
    exe = shutil.which("npm")
    if not exe:
        raise RuntimeError("npm not found on PATH. Install Node.js/npm first.")
    return exe


def run_vite_scaffold(folder):
    """
    Hardcoded step: create a fresh React + Vite project using create-vite.
    `folder` is the target project folder (relative or absolute).
    Scaffolds non-interactively using npx -y and DEVNULL stdin to avoid hanging.
    """
    folder_abs = os.path.abspath(folder)
    parent = os.path.dirname(folder_abs) or os.getcwd()
    project_name = os.path.basename(folder_abs)

    os.makedirs(parent, exist_ok=True)

    npx = shutil.which("npx") or _npm_executable()
    cmd = [
        npx, "-y", "create-vite@latest", project_name,
        "--template", "react"
    ]

    print(f"\n[UI Architect] Running Vite scaffold: {' '.join(cmd)} (cwd={parent})")
    result = subprocess.run(
        cmd,
        cwd=parent,
        capture_output=True,
        text=True,
        shell=(os.name == "nt"),
        stdin=subprocess.DEVNULL,
    )

    if result.stdout:
        print(result.stdout)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr)
        raise RuntimeError(f"Vite scaffold failed for '{folder}' (exit {result.returncode})")

    print(f"[UI Architect] Vite scaffold created at: {folder}")


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


def normalize_vite_rel_path(path):
    """
    Normalizes any file or folder path so that it maps to the correct
    standard location inside a Vite React project.
    Root configs stay at root; public assets stay in public/;
    all source code, components, pages, routes, services, utils, hooks,
    styles, data, and assets are placed inside src/.
    """
    path = normalize_path(path)
    if not path:
        return None

    # Handle standard root files/folders in Vite
    root_exact = {
        "package.json", "package-lock.json", "vite.config.js", "vite.config.ts",
        "index.html", "readme.md", ".gitignore", ".env", ".env.local",
        "public", "src", "node_modules", "ui_blueprint.json"
    }

    parts = path.split("/")
    first = parts[0].lower()

    if path.lower() in root_exact or first in ["public", "src", ".git", "node_modules"]:
        # In Vite, index.html is located at root, not inside public/
        if path.lower() == "public/index.html":
            return "index.html"
        return path

    # Check for root config files or dotfiles
    if len(parts) == 1 and (
        path.startswith(".")
        or path.lower().endswith(".json")
        or path.lower().endswith(".md")
        or path.lower().endswith(".config.js")
        or path.lower().endswith(".config.ts")
        or path.lower().endswith(".config.mjs")
    ):
        return path

    # For React/Vite source paths, ensure they are placed inside src/
    return f"src/{path}"


def add_dir(folders, path):
    p = normalize_vite_rel_path(path)
    if p:
        folders.add(p)


def add_file(folders, files, path):
    p = normalize_vite_rel_path(path)
    if p:
        files.add(p)
        parent = os.path.dirname(p)
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
    """
    Recursively parses blueprint and planner items (strings, lists, objects, trees)
    and populates folder and file sets.
    """
    if isinstance(item, str):
        add_by_kind(folders, files, join_path(base, item), default=default)
        return

    if isinstance(item, list):
        for x in item:
            consume(x, folders, files, base, default)
        return

    if not isinstance(item, dict):
        return

    # Check if item is a structured entry with known keys
    known_keys = {
        "kind", "type", "path", "filepath", "file_path", "folder",
        "directory", "dir", "file", "source_file", "config_file",
        "children", "items", "subfolders", "directories", "folders",
        "files", "file_list", "name", "component_name"
    }

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

    # Also handle generic nested tree structures (e.g. planner folderStructure)
    for k, v in item.items():
        if k in known_keys:
            continue
        child_path = join_path(base, k)
        if isinstance(v, dict):
            consume(v, folders, files, child_path)
        elif isinstance(v, list):
            consume(v, folders, files, child_path)
        elif isinstance(v, str):
            if looks_like_file(child_path):
                add_file(folders, files, child_path)
            elif "component" in str(v).lower() or (base and "component" in base.lower()):
                add_file(folders, files, f"{child_path}.jsx")
            else:
                add_dir(folders, child_path)


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
    """
    Ensures all standard Vite React directories and required component/page
    files exist in their appropriate places inside the Vite project structure.
    """
    # Core directories required for a clean Vite + React app
    for folder in [
        "public",
        "src",
        "src/assets",
        "src/assets/images",
        "src/assets/styles",
        "src/components",
        "src/components/common",
        "src/components/features",
        "src/components/layout",
        "src/pages",
        "src/routes",
        "src/services",
        "src/utils",
        "src/hooks",
        "src/styles",
        "src/data",
    ]:
        add_dir(folders, folder)

    add_file(folders, files, "package.json")
    add_file(folders, files, "index.html")
    add_file(folders, files, "vite.config.js")

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

    # Pages
    for page in bp.get("pages", []):
        if not isinstance(page, dict):
            continue
        name = page.get("name") or page.get("component_name") or page.get("page")
        if not name:
            continue
        if not page.get("file"):
            page["file"] = f"src/pages/{pascal_case(name)}.jsx"
        page["file"] = normalize_vite_rel_path(page["file"])
        add_file(folders, files, page["file"])

    # Routes
    for route in bp.get("routes", []):
        if not isinstance(route, dict):
            continue
        name = route.get("page") or route.get("component") or route.get("component_name")
        if name and not route.get("file"):
            route["file"] = f"src/pages/{pascal_case(name)}.jsx"
        if route.get("file"):
            route["file"] = normalize_vite_rel_path(route["file"])
            add_file(folders, files, route["file"])

    # Components
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
            elif "feature" in ctype or pname in ["MainContent"]:
                component["file"] = f"src/components/features/{pname}.jsx"
            else:
                component["file"] = f"src/components/common/{pname}.jsx"
        component["file"] = normalize_vite_rel_path(component["file"])
        add_file(folders, files, component["file"])

    # Layouts
    for layout in bp.get("layouts", []):
        if not isinstance(layout, dict):
            continue
        name = layout.get("name") or layout.get("component_name")
        if not name:
            continue
        if not layout.get("file"):
            layout["file"] = f"src/components/layout/{pascal_case(name)}.jsx"
        layout["file"] = normalize_vite_rel_path(layout["file"])
        add_file(folders, files, layout["file"])


def collect_paths(bp, planner_data=None):
    folders, files = set(), set()

    # Ingest from blueprint fields
    for key, default in [
        ("filesystem", None),
        ("directories_to_create", "directory"),
        ("files_to_create", "file"),
        ("folder_structure", None),
        ("folderStructure", None),
        ("file_structure", "file"),
    ]:
        consume(bp.get(key, []), folders, files, default=default)

    # Ingest directly from planner folderStructure if available
    if isinstance(planner_data, dict):
        if "folderStructure" in planner_data:
            consume(planner_data["folderStructure"], folders, files)
        if "folder_structure" in planner_data:
            consume(planner_data["folder_structure"], folders, files)

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

    # Clean and sort normalized paths
    normalized_folders = sorted({normalize_vite_rel_path(x) for x in folders if normalize_vite_rel_path(x)})
    normalized_files = sorted({normalize_vite_rel_path(x) for x in files if normalize_vite_rel_path(x)})

    return normalized_folders, normalized_files


def create_scaffold(root, folders, files):
    """
    Additive scaffold on top of the Vite project.
    All required folders are created. Files already generated by Vite
    are preserved and never overwritten.
    """
    made_folders, made_files, skipped_files = [], [], []

    for folder in folders:
        path = safe_project_path(root, folder)
        if path:
            create_folder(path)
            made_folders.append(folder)

    for file in files:
        path = safe_project_path(root, file)
        if not path:
            continue
        if os.path.exists(path):
            skipped_files.append(file)
            continue
        create_empty_file(path)
        made_files.append(file)

    return made_folders, made_files, skipped_files


def main():
    planner_raw = load_planner()
    try:
        planner_data = json.loads(planner_raw)
    except Exception:
        planner_data = {}

    bp = normalize_blueprint(generate_blueprint(planner_raw))

    name = bp.get("project_name", "frontend-project")
    folder = sanitize_project_name(name)

    # 1. Reset / clean target project folder
    reset_output_folder(folder, clean=CLEAN_OUTPUT_FOLDER)

    # 2. Run Vite scaffold directly into the project folder
    run_vite_scaffold(folder)

    # 3. Compute the blueprint's desired folders/files at appropriate Vite paths
    folders, files = collect_paths(bp, planner_data=planner_data)

    bp["project_folder"] = folder
    bp["directories_to_create"] = folders
    bp["files_to_create"] = files
    bp["filesystem"] = (
        [{"path": x, "kind": "directory"} for x in folders]
        +
        [{"path": x, "kind": "file"} for x in files]
    )
    bp["metadata_file"] = "ui_blueprint.json"

    # 4. Additively create all required remaining folders and placeholder files
    made_folders, made_files, skipped_files = create_scaffold(folder, folders, files)

    save_json(os.path.join(folder, "ui_blueprint.json"), bp)

    print("\n[UI Architect] Completed successfully!")
    print("Project Folder :", folder)
    print("Blueprint      :", os.path.join(folder, "ui_blueprint.json"))
    print("Folders created/ensured :", len(made_folders))
    print("New files created       :", len(made_files))
    print("Vite files preserved    :", len(skipped_files))


if __name__ == "__main__":
    main()