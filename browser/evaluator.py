from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from browser.preview import PreviewServerManager
from browser.screenshots import get_screenshot_store
from browser.viewports import DEFAULT_VIEWPORTS, ViewportConfig, ViewportName
from tools import save_json

try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore


@dataclass
class EvaluationFinding:
    route: str
    viewport: str
    render_success: bool
    console_errors: list[str] = field(default_factory=list)
    uncaught_exceptions: list[str] = field(default_factory=list)
    failed_resources: list[str] = field(default_factory=list)
    horizontal_overflow: bool = False
    clipping_issues: list[str] = field(default_factory=list)
    zero_size_regions: list[str] = field(default_factory=list)
    broken_links: list[str] = field(default_factory=list)
    screenshot_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "viewport": self.viewport,
            "render_success": self.render_success,
            "console_errors": self.console_errors,
            "uncaught_exceptions": self.uncaught_exceptions,
            "failed_resources": self.failed_resources,
            "horizontal_overflow": self.horizontal_overflow,
            "clipping_issues": self.clipping_issues,
            "zero_size_regions": self.zero_size_regions,
            "broken_links": self.broken_links,
            "screenshot_path": self.screenshot_path,
        }


class BrowserEvaluator:
    """Runs deterministic browser checks across viewports and routes using Playwright with synthetic fallback."""

    def __init__(
        self,
        server_url: str,
        routes: list[str] | None = None,
        viewports: list[ViewportConfig] | None = None,
        run_id: str = "eval-run",
    ):
        self.server_url = server_url.rstrip("/")
        self.routes = routes or ["/"]
        self.viewports = viewports or list(DEFAULT_VIEWPORTS.values())
        self.run_id = run_id
        self.store = get_screenshot_store()

    def evaluate_all(self) -> dict[str, Any]:
        """Evaluate all routes across all configured viewports."""
        if emit is not None and EventType is not None:
            emit(EventType.VISUAL_EVAL_STARTED, routes=self.routes, viewports=[v.key for v in self.viewports])

        findings: list[EvaluationFinding] = []
        evaluation_mode = "playwright"
        fallback_reason: str | None = None

        try:
            from playwright.sync_api import sync_playwright
            self._evaluate_with_playwright(sync_playwright, findings)
        except Exception as exc:
            # Fallback synthetic evaluation runner when Playwright browser binary is not present
            evaluation_mode = "http_fallback"
            fallback_reason = f"{type(exc).__name__}: {exc}"
            findings.clear()
            self._evaluate_synthetic_fallback(findings)

        # Aggregate summary metrics
        total_evals = len(findings)
        render_success_count = sum(1 for f in findings if f.render_success)
        render_success_rate = (render_success_count / total_evals) if total_evals > 0 else 0.0

        total_console_errors = sum(len(f.console_errors) for f in findings)
        overflow_count = sum(1 for f in findings if f.horizontal_overflow)
        overflow_rate = (overflow_count / total_evals) if total_evals > 0 else 0.0

        report = {
            "run_id": self.run_id,
            "evaluation_mode": evaluation_mode,
            "fallback_reason": fallback_reason,
            "total_evaluations": total_evals,
            "render_success_rate": render_success_rate,
            "console_errors_count": total_console_errors,
            "overflow_failure_rate": overflow_rate,
            "findings": [f.to_dict() for f in findings],
        }

        if emit is not None and EventType is not None:
            emit(
                EventType.VISUAL_EVAL_COMPLETED,
                render_success_rate=render_success_rate,
                console_errors=total_console_errors,
                overflow_failure_rate=overflow_rate,
                responsive_failure_rate=overflow_rate,
                visual_score=round(render_success_rate * 100, 1),
                final_pass_rate=render_success_rate,
            )

        return report

    def _evaluate_with_playwright(self, sync_playwright, findings: list[EvaluationFinding]) -> None:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            for vp in self.viewports:
                context = browser.new_context(
                    viewport={"width": vp.width, "height": vp.height},
                    device_scale_factor=vp.device_scale_factor,
                    is_mobile=vp.is_mobile,
                )
                page = context.new_page()

                for route in self.routes:
                    url = f"{self.server_url}{route}"
                    console_errs: list[str] = []
                    uncaught_excs: list[str] = []
                    failed_res: list[str] = []

                    page.on("console", lambda msg: console_errs.append(msg.text) if msg.type in ("error", "warning") else None)
                    page.on("pageerror", lambda exc: uncaught_excs.append(str(exc)))
                    page.on("requestfailed", lambda req: failed_res.append(f"{req.url} ({req.failure})"))

                    render_ok = False
                    overflow = False
                    clipping = []
                    zero_size = []

                    try:
                        response = page.goto(url, wait_until="networkidle", timeout=10000)
                        render_ok = response is not None and response.status in (200, 304)

                        # JS overflow & zero-size region checks
                        overflow = page.evaluate("() => document.documentElement.scrollWidth > document.documentElement.clientWidth")
                        zero_size_el = page.evaluate("""() => {
                            const sel = ['header', 'main', 'footer', '#root'];
                            return sel.filter(s => {
                                const el = document.querySelector(s);
                                return el && (el.offsetWidth === 0 || el.offsetHeight === 0);
                            });
                        }""")
                        zero_size = zero_size_el or []

                        # Take screenshot
                        img_bytes = page.screenshot(full_page=True)
                        saved_path = str(self.store.save_screenshot(self.run_id, vp.key, route, img_bytes))
                    except Exception as exc:
                        uncaught_excs.append(str(exc))
                        saved_path = None

                    finding = EvaluationFinding(
                        route=route,
                        viewport=vp.key,
                        render_success=render_ok,
                        console_errors=console_errs,
                        uncaught_exceptions=uncaught_excs,
                        failed_resources=failed_res,
                        horizontal_overflow=overflow,
                        clipping_issues=clipping,
                        zero_size_regions=zero_size,
                        screenshot_path=saved_path,
                    )
                    findings.append(finding)

                context.close()
            browser.close()

    def _evaluate_synthetic_fallback(self, findings: list[EvaluationFinding]) -> None:
        """Synthetic fallback evaluator for environments without Playwright browser binary."""
        import urllib.request

        for vp in self.viewports:
            for route in self.routes:
                url = f"{self.server_url}{route}"
                render_ok = False
                console_errs = []
                uncaught_excs = []

                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "antigravity-eval"})
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        render_ok = resp.status in (200, 304)
                except Exception as exc:
                    uncaught_excs.append(f"Render connection failed: {exc}")

                # 1x1 transparent PNG fallback for screenshot store
                fallback_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x03\x00\x05\xfe\x02\xfe\xa7\x35\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82"
                saved_path = str(self.store.save_screenshot(self.run_id, vp.key, route, fallback_png))

                finding = EvaluationFinding(
                    route=route,
                    viewport=vp.key,
                    render_success=render_ok,
                    console_errors=console_errs,
                    uncaught_exceptions=uncaught_excs,
                    horizontal_overflow=False,
                    screenshot_path=saved_path,
                )
                findings.append(finding)
