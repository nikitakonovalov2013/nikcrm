from __future__ import annotations

import html


def esc(s: str | None) -> str:
    if s is None:
        return ""
    return html.escape(str(s), quote=False)
