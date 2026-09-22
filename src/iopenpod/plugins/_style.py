"""Small design-system adapter for the migrated plugin panels."""

from iopenpod.gui.styles import (
    FONT_FAMILY,
    Metrics,
    accent_btn_css,
    btn_css,
    make_separator,
    paint_css,
)


class Colors:
    TEXT_PRIMARY = paint_css("text.primary")
    TEXT_SECONDARY = paint_css("text.secondary")
    TEXT_TERTIARY = paint_css("text.tertiary")
    SURFACE = paint_css("surface.raised")
    BORDER = paint_css("border.subtle")
    ACCENT = paint_css("data.accent.fill")
    SUCCESS = paint_css("status.success.text")
    WARNING = paint_css("status.warning.text")
    DANGER = paint_css("status.danger.text")


__all__ = [
    "Colors", "FONT_FAMILY", "Metrics", "accent_btn_css", "btn_css",
    "make_separator",
]
