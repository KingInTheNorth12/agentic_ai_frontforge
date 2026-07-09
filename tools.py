import json, os, re, shutil

def sanitize_project_name(name):
    if not isinstance(name, str) or not name.strip():
        return "frontend-project"
    name = re.sub(r"[^a-z0-9]+", "-", name.strip().lower())
    name = re.sub(r"-+", "-", name).strip("-")
    return name or "frontend-project"

def normalize_path(path):
    if not isinstance(path, str) or not path.strip():
        return None
    path = path.strip().strip("\"'` ").replace("\\", "/")
    if "://" in path or path.startswith("~"):
        return None
    path = path.lstrip("/")
    while path.startswith("./"):
        path = path[2:]
    path = re.sub(r"/+", "/", path)
    if path in ["", ".", "/"] or any(p == ".." for p in path.split("/")):
        return None
    return path

def safe_project_path(root, rel):
    rel = normalize_path(rel)
    if not rel:
        return None
    root_abs = os.path.abspath(root)
    full = os.path.abspath(os.path.join(root_abs, rel))
    try:
        return full if os.path.commonpath([root_abs, full]) == root_abs else None
    except ValueError:
        return None

def reset_output_folder(folder, clean=True):
    folder_abs = os.path.abspath(folder)
    cwd = os.path.abspath(os.getcwd())
    if os.path.commonpath([cwd, folder_abs]) != cwd or folder_abs == cwd:
        raise Exception("Unsafe output folder path")
    if os.path.exists(folder_abs) and clean:
        shutil.rmtree(folder_abs)
    os.makedirs(folder_abs, exist_ok=True)

def create_folder(path):
    if path:
        os.makedirs(path, exist_ok=True)

def create_empty_file(path):
    if not path:
        return
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if not os.path.exists(path):
        open(path, "w", encoding="utf-8").close()

def save_json(path, data):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)