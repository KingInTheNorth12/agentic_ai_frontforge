"""Browser-Based Visual & Functional Evaluation Subsystem."""

from __future__ import annotations

from browser.evaluator import BrowserEvaluator
from browser.interaction import InteractionSuite
from browser.preview import PreviewServerManager, get_preview_manager
from browser.screenshots import ScreenshotStore, get_screenshot_store
from browser.viewports import ViewportConfig, ViewportName, DEFAULT_VIEWPORTS
from browser.visual_llm import VisualDefect, VisualEvaluator

__all__ = [
    "BrowserEvaluator",
    "InteractionSuite",
    "PreviewServerManager",
    "get_preview_manager",
    "ScreenshotStore",
    "get_screenshot_store",
    "ViewportConfig",
    "ViewportName",
    "DEFAULT_VIEWPORTS",
    "VisualDefect",
    "VisualEvaluator",
]
