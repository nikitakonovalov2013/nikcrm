from __future__ import annotations

import html


def esc(s: str | None) -> str:
    if s is None:
        return ""
    return html.escape(str(s), quote=False)


def format_plain_url(label: str, url: str) -> str:
    return f"{esc(label)}\n{str(url)}"
