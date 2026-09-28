from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ViewportName(str, Enum):
    MOBILE = "mobile"
    TABLET = "tablet"
    DESKTOP = "desktop"


@dataclass(frozen=True, slots=True)
class ViewportConfig:
    name: ViewportName
    width: int
    height: int
    device_scale_factor: float = 1.0
    is_mobile: bool = False

    @property
    def key(self) -> str:
        return self.name.value


DEFAULT_VIEWPORTS: dict[ViewportName, ViewportConfig] = {
    ViewportName.MOBILE: ViewportConfig(
        name=ViewportName.MOBILE,
        width=375,
        height=667,
        device_scale_factor=2.0,
        is_mobile=True,
    ),
    ViewportName.TABLET: ViewportConfig(
        name=ViewportName.TABLET,
        width=768,
        height=1024,
        device_scale_factor=1.5,
        is_mobile=False,
    ),
    ViewportName.DESKTOP: ViewportConfig(
        name=ViewportName.DESKTOP,
        width=1280,
        height=800,
        device_scale_factor=1.0,
        is_mobile=False,
    ),
}
