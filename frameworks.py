"""Small framework contracts: native files, exact baseline packages and build outputs."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re

FRAMEWORKS = {
    "react": {"label": "React", "runtime": {"react": "19.3.0", "react-dom": "19.3.0", "react-router-dom": "7.18.4"}, "dev": {"vite": "8.3.1", "@vitejs/plugin-react": "6.1.1"}, "extension": ".jsx", "output": "dist"},
    "vue": {"label": "Vue", "runtime": {"vue": "3.5.43", "vue-router": "4.6.4"}, "dev": {"vite": "8.3.1", "@vitejs/plugin-vue": "6.0.9"}, "extension": ".vue", "output": "dist"},
    "svelte": {"label": "Svelte", "runtime": {"svelte": "5.57.1"}, "dev": {"vite": "8.3.1", "@sveltejs/vite-plugin-svelte": "7.3.1"}, "extension": ".svelte", "output": "dist"},
    "next": {"label": "Next.js", "runtime": {"next": "16.3.6", "react": "19.3.0", "react-dom": "19.3.0"}, "dev": {}, "extension": ".jsx", "output": "out"},
}


def framework_id(value: str | None) -> str:
    text = (value or "react").lower()
    for key in ("next", "vue", "svelte", "react"):
        if key in text:
            return key
    raise ValueError(f"Unsupported framework: {value}. Choose React, Next.js, Vue, or Svelte.")


def manifest(name: str, framework: str) -> dict[str, Any]:
    key = framework_id(framework)
    return {"name": name, "private": True, "version": "0.0.0", "type": "module", "scripts": {
        "dev": "next dev" if key == "next" else "vite",
        "build": "next build --webpack" if key == "next" else "vite build",
        **({"preview": "vite preview"} if key != "next" else {}),
    }}


def adapt_blueprint(blueprint: dict[str, Any]) -> dict[str, Any]:
    key = framework_id(blueprint["framework"])
    contract = FRAMEWORKS[key]
    blueprint["framework"] = key
    blueprint["build_output"] = contract["output"]
    for group in ("pages", "layouts", "components"):
        for item in blueprint[group]:
            item["path"] = str(Path(item["path"]).with_suffix(contract["extension"]))
    pages = {item["name"]: item for item in blueprint["pages"]}
    for route in blueprint["routes"]:
        if not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]*", route["path"]):
            raise ValueError("Routes must be static paths, such as / or /about. Dynamic routes are not supported yet.")
        route["file"] = pages[route["page"]]["path"]
    framework_packages = {p for f in FRAMEWORKS.values() for p in [*f["runtime"], *f["dev"]]}
    requested = [p for p in blueprint["dependencies"] if p not in framework_packages]
    blueprint["dependencies"] = sorted(set(requested) | set(contract["runtime"]) | set(contract["dev"]))
    templates = {}
    generated = [{**item, "role": item["kind"]} for group in ("layouts", "components", "pages") for item in blueprint[group]]
    generated.append({"path": "src/styles/globals.css", "role": "stylesheet"})
    if key == "next":
        templates["next.config.mjs"] = 'export default { output: "export", trailingSlash: true, images: { unoptimized: true } };\n'
        templates["src/app/layout.jsx"] = 'import "../styles/globals.css";\nexport default function RootLayout({ children }) { return <html lang="en"><body>{children}</body></html>; }\n'
        for route in blueprint["routes"]:
            target = "src/app/" + route["path"].strip("/")
            target = target.rstrip("/") + "/page.jsx"
            import os
            relative = os.path.relpath(route["file"], str(Path(target).parent)).replace("\\", "/")
            templates[target] = f'export {{ default }} from {json.dumps(relative)};\n'
        if "src/app/page.jsx" not in templates:
            generated.append({"path": "src/app/page.jsx", "role": "page", "name": "Home", "description": blueprint["summary"]})
    else:
        templates["index.html"] = '<!doctype html><html lang="en"><head><meta charset="UTF-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/><title>Generated application</title></head><body><div id="root"></div><script type="module" src="/src/main.js"></script></body></html>\n'
        plugin, imported = {"react": ("@vitejs/plugin-react", "react"), "vue": ("@vitejs/plugin-vue", "vue"), "svelte": ("@sveltejs/vite-plugin-svelte", "{ svelte }")}[key]
        func = "svelte" if key == "svelte" else key
        templates["vite.config.js"] = f'import {{ defineConfig }} from "vite";\nimport {imported} from "{plugin}";\nexport default defineConfig({{ plugins: [{func}()] }});\n'
        app = "src/App" + contract["extension"]
        generated.append({"path": app, "role": "app_root", "name": "App", "description": "Compose the shared layout and the declared routes."})
        if key == "react":
            templates["src/main.js"] = 'import React from "react";\nimport { createRoot } from "react-dom/client";\nimport { BrowserRouter } from "react-router-dom";\nimport App from "./App.jsx";\nimport "./styles/globals.css";\ncreateRoot(document.getElementById("root")).render(React.createElement(BrowserRouter, null, React.createElement(App)));\n'
            imports = "\n".join(f'import Page{i} from "../{route["file"][4:]}";' for i, route in enumerate(blueprint["routes"]))
            routes = "".join(f'<Route path={{{json.dumps(route["path"])}}} element={{<Page{i} />}} />' for i, route in enumerate(blueprint["routes"]))
            templates["src/routes/AppRoutes.jsx"] = f'import {{ Routes, Route }} from "react-router-dom";\n{imports}\nexport default function AppRoutes() {{ return <Routes>{routes}</Routes>; }}\n'
        elif key == "vue":
            imports = "\n".join(f'import Page{i} from "./{route["file"][4:]}";' for i, route in enumerate(blueprint["routes"]))
            routes = ",".join(f'{{ path: {json.dumps(route["path"])}, component: Page{i} }}' for i, route in enumerate(blueprint["routes"]))
            templates["src/router.js"] = f'import {{ createRouter, createWebHistory }} from "vue-router";\n{imports}\nexport default createRouter({{history:createWebHistory(),routes:[{routes}]}});\n'
            templates["src/main.js"] = 'import { createApp } from "vue";\nimport App from "./App.vue";\nimport router from "./router.js";\nimport "./styles/globals.css";\ncreateApp(App).use(router).mount("#root");\n'
        else:
            templates["src/main.js"] = 'import { mount } from "svelte";\nimport App from "./App.svelte";\nimport "./styles/globals.css";\nmount(App, { target: document.getElementById("root") });\n'
    blueprint["templates"] = templates
    blueprint["generation_items"] = generated
    blueprint["build_order"] = [item["path"] for item in generated]
    all_files = sorted({"package.json", *templates, *(item["path"] for item in generated)})
    if len(blueprint["build_order"]) != len(set(blueprint["build_order"])):
        raise ValueError("Component names produce duplicate file paths; revise the plan.")
    blueprint["filesystem"] = [{"path": p, "kind": "file"} for p in all_files]
    return blueprint


def generation_rules(framework: str) -> str:
    return {
        "react": "Use React functional components. App composes src/routes/AppRoutes.jsx; BrowserRouter is already mounted by main.js. Use ordinary CSS unless explicitly configured otherwise.",
        "vue": "Write Vue 3 single-file components with <script setup>, <template>, and optional <style scoped>. App renders <RouterView /> with shared layout. Use vue-router RouterLink. Do not emit JSX or React imports.",
        "svelte": "Write Svelte 5 .svelte components. Use Svelte syntax, never React/JSX. Use $props() for props. App selects the declared page from window.location.pathname and ordinary anchors; navigation may reload the document. Do not import a router package.",
        "next": "Use Next.js App Router with static export. UI page/component files using hooks or events must begin with 'use client'. Root layout and route wrapper files already exist. Use next/link. No server actions, runtime API routes, cookies, dynamic routes, or remote font downloads. Use CSS and local/system fonts.",
    }[framework_id(framework)]
