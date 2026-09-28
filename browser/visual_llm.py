from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from llm import make_model_for_role

try:
    from cli.events import emit, EventType
except ImportError:
    emit = None  # type: ignore
    EventType = None  # type: ignore


class VisualDefect(BaseModel):
    route: str = "/"
    viewport: str = "desktop"
    region: str = "main"
    severity: Literal["low", "medium", "high", "critical"] = "medium"
    issue: str
    suggested_correction: str
    target_file: str = ""


class VisualEvaluationResult(BaseModel):
    has_defects: bool = False
    visual_score: float = 100.0
    defects: list[VisualDefect] = Field(default_factory=list)
    summary_notes: str = ""


VISUAL_EVAL_PROMPT = """
You are a conservative visual UI reviewer analyzing a rendered frontend application layout.
Evaluate rendering issues based on the supplied deterministic findings and layout summary.

Focus on:
1. Layout coherence and visual alignment
2. Spacing consistency and excessive padding/margin
3. Horizontal overflow or element clipping
4. Excessive density or obvious emptiness
5. Contrast or typography legibility issues

Rules:
- Do not redesign the application from scratch.
- Return structured visual defects identifying route, viewport, region, severity, issue, and suggested correction.
- Map issues to the likely target source file (e.g. src/App.jsx, src/components/Header.jsx).
"""


class VisualEvaluator:
    """Evaluates rendering visual defects using structured LLM judgment."""

    def __init__(self):
        self.model = make_model_for_role("review", temperature=0.0).with_structured_output(VisualEvaluationResult)

    def evaluate_findings(
        self,
        findings: list[dict[str, Any]],
        blueprint: dict[str, Any] | None = None,
    ) -> VisualEvaluationResult:
        """Evaluate deterministic findings for visual defects."""
        # Check if there are any deterministic issues or visual evaluation targets
        issues = [f for f in findings if not f.get("render_success") or f.get("horizontal_overflow") or f.get("console_errors")]
        if not issues:
            return VisualEvaluationResult(
                has_defects=False,
                visual_score=100.0,
                defects=[],
                summary_notes="No visual or rendering defects detected across viewports.",
            )

        payload = {
            "deterministic_findings": issues,
            "blueprint_summary": blueprint.get("summary") if blueprint else "",
            "blueprint_components": blueprint.get("components") if blueprint else [],
        }

        try:
            res = self.model.invoke([
                {"role": "system", "content": VISUAL_EVAL_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ])
            if emit is not None and EventType is not None and res.has_defects:
                for defect in res.defects:
                    emit(
                        EventType.VISUAL_DEFECT_DETECTED,
                        route=defect.route,
                        viewport=defect.viewport,
                        issue=defect.issue,
                    )
            return res
        except Exception as exc:
            return VisualEvaluationResult(
                has_defects=True,
                visual_score=50.0,
                defects=[
                    VisualDefect(
                        route="/",
                        viewport="desktop",
                        region="main",
                        severity="medium",
                        issue=f"Visual evaluation exception: {exc}",
                        suggested_correction="Check component CSS layout properties.",
                    )
                ],
                summary_notes=f"Visual evaluation fallback: {exc}",
            )
