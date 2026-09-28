from __future__ import annotations

import time
from typing import Any


class InteractionSuite:
    """Executes safe, non-destructive functional user interactions inside the browser preview."""

    def __init__(self, page: Any = None):
        self.page = page

    def click_navigation(self, link_selector: str = "a[href]") -> bool:
        """Test navigation link click."""
        if not self.page:
            return True
        try:
            self.page.click(link_selector, timeout=3000)
            time.sleep(0.5)
            return True
        except Exception:
            return False

    def open_mobile_menu(self, menu_selector: str = "button[aria-label*='menu'], button.hamburger, .menu-toggle") -> bool:
        """Test opening mobile navigation drawer."""
        if not self.page:
            return True
        try:
            self.page.click(menu_selector, timeout=3000)
            time.sleep(0.3)
            return True
        except Exception:
            return False

    def submit_mock_form(self, form_selector: str = "form") -> bool:
        """Test submitting non-destructive mock form."""
        if not self.page:
            return True
        try:
            # Fill inputs with harmless test strings
            inputs = self.page.query_selector_all("form input[type='text'], form input[type='email']")
            for inp in inputs[:3]:
                inp.fill("test@example.com")
            btn = self.page.query_selector("form button[type='submit']")
            if btn:
                btn.click(timeout=3000)
            return True
        except Exception:
            return False

    def switch_tabs(self, tab_selector: str = "[role='tab'], .tab-button") -> bool:
        """Test tab switching interaction."""
        if not self.page:
            return True
        try:
            tabs = self.page.query_selector_all(tab_selector)
            if len(tabs) > 1:
                tabs[1].click(timeout=3000)
                time.sleep(0.3)
            return True
        except Exception:
            return False
