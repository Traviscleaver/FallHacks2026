"""HTML rendering helpers for QTextEdit (Qt's rich-text subset)."""

from __future__ import annotations

import html
import re

from .models import ConceptBreakdown


def _inline(text: str) -> str:
    """Escape, then apply **bold**, *italic* and `code`."""
    out = html.escape(text)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", out)
    return out


def markdown_lite_to_html(text: str) -> str:
    """Tiny Markdown subset for chat answers: paragraphs, bullets, headings."""
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    for raw in lines:
        line = raw.rstrip()
        stripped = line.lstrip()
        if not stripped:
            out.append("<br>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)", stripped)
        if m:
            out.append(f"<b>{_inline(m.group(2))}</b><br>")
            continue
        m = re.match(r"^([-*+]|\d+[.)])\s+(.*)", stripped)
        if m:
            indent = "&nbsp;" * (2 + 2 * ((len(line) - len(stripped)) // 2))
            bullet = "•" if m.group(1) in "-*+" else html.escape(m.group(1))
            out.append(f"{indent}{bullet} {_inline(m.group(2))}<br>")
            continue
        out.append(_inline(stripped) + "<br>")
    # Collapse runs of blank lines and trailing breaks.
    joined = "".join(out)
    joined = re.sub(r"(<br>){3,}", "<br><br>", joined)
    return re.sub(r"(<br>)+$", "", joined)


def breakdown_to_html(b: ConceptBreakdown, primary: str, secondary: str, muted: str) -> str:
    def label(text: str) -> str:
        return (
            f"<p style='color:{muted}; font-size:11px; font-weight:600; "
            f"margin:12px 0 4px 0;'>{text}</p>"
        )

    parts = [f"<p style='color:{primary}; margin-top:0;'>{_inline(b.summary)}</p>"]

    if b.key_concepts:
        parts.append(label("KEY CONCEPTS"))
        items = "".join(
            f"• <b style='color:{primary};'>{_inline(c.term)}:</b> {_inline(c.explanation)}<br>"
            for c in b.key_concepts
        )
        parts.append(f"<p style='color:{secondary}; margin-top:0;'>{items}</p>")

    if b.formulas:
        parts.append(label("FORMULAS"))
        items = "".join(
            f"• <code style='color:{primary};'>{html.escape(f.expression)}</code><br>"
            f"&nbsp;&nbsp;&nbsp;{_inline(f.meaning)}<br>"
            for f in b.formulas
        )
        parts.append(f"<p style='color:{secondary}; margin-top:0;'>{items}</p>")

    parts.append(label("WHAT THE PROFESSOR SAID"))
    if b.lecture_connection.strip():
        parts.append(f"<p style='color:{secondary}; margin-top:0;'>{_inline(b.lecture_connection)}</p>")
    else:
        parts.append(
            f"<p style='color:{muted}; margin-top:0;'><i>No related transcript found so far.</i></p>"
        )

    if b.check_questions:
        parts.append(label("CHECK YOUR UNDERSTANDING"))
        items = "".join(f"• {_inline(q)}<br>" for q in b.check_questions)
        parts.append(f"<p style='color:{secondary}; margin-top:0;'>{items}</p>")

    return "".join(parts)
