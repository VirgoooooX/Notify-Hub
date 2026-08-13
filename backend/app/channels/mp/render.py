"""WeChat Official Account friendly article rendering for the article library.

The API publish path keeps a plain paragraph renderer (``text_to_html``) while
the article workspace stores a richer, editor-friendly HTML snapshot that can be
copied directly into the WeChat backend or imported by the browser userscript.
"""

from __future__ import annotations

from html import escape

SECTION_STYLE = "font-size:16px;color:#3f3f3f;line-height:1.75;letter-spacing:0.2px;"
PARAGRAPH_STYLE = "margin:0 0 16px;"
HEADING_STYLE = "font-size:19px;font-weight:700;margin:24px 0 12px;color:#1f1f1f;"
BLOCKQUOTE_STYLE = (
    "margin:0 0 16px;padding:10px 14px;border-left:4px solid #d2d2d2;"
    "background:#f7f7f7;color:#666;border-radius:4px;"
)
LIST_STYLE = "margin:0 0 16px;padding-left:22px;"
LIST_ITEM_STYLE = "margin:0 0 6px;"


def _escape(value: str) -> str:
    return escape(value, quote=True)


def _render_paragraph(line: str) -> str:
    return f'<p style="{PARAGRAPH_STYLE}">{_escape(line)}</p>'


def _render_heading(line: str) -> str:
    text = _escape(line.lstrip("# ").strip())
    return f'<p style="{HEADING_STYLE}">{text}</p>'


def _render_list(lines: list[str]) -> str:
    items = "".join(
        f'<li style="{LIST_ITEM_STYLE}">{_escape(line.lstrip("- ").strip())}</li>' for line in lines
    )
    return f'<ul style="{LIST_STYLE}">{items}</ul>'


def _render_blockquote(lines: list[str]) -> str:
    text = "".join(
        f'<p style="margin:0 0 8px;">{_escape(line.lstrip("> ").strip())}</p>' for line in lines
    )
    return f'<blockquote style="{BLOCKQUOTE_STYLE}">{text}</blockquote>'


def render_body(content: str) -> str:
    """Convert plain article text into WeChat friendly HTML blocks."""
    parts: list[str] = []
    index = 0
    lines = content.splitlines()
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("# "):
            parts.append(_render_heading(line))
            index += 1
        elif line.startswith("> "):
            block: list[str] = []
            while index < len(lines) and lines[index].strip().startswith("> "):
                block.append(lines[index])
                index += 1
            parts.append(_render_blockquote(block))
        elif line.startswith("- "):
            block = []
            while index < len(lines) and lines[index].strip().startswith("- "):
                block.append(lines[index])
                index += 1
            parts.append(_render_list(block))
        else:
            parts.append(_render_paragraph(line))
            index += 1
    if not parts:
        parts.append(_render_paragraph(content))
    return "".join(parts)


def render_wechat_html(
    *,
    content: str,
    cover_url: str | None = None,
    source_url: str | None = None,
) -> str:
    """Render a complete article body ready for the WeChat editor clipboard."""
    body_parts: list[str] = []
    if cover_url:
        body_parts.append(
            '<p style="margin:0 0 18px;text-align:center;">'
            f'<img src="{_escape(cover_url)}" '
            'style="max-width:100%;border-radius:8px;display:block;margin:0 auto;" />'
            "</p>"
        )
    body_parts.append(render_body(content))
    if source_url:
        body_parts.append(
            '<p style="margin:24px 0 0;padding-top:12px;border-top:1px solid #eee;'
            'color:#999;font-size:13px;text-align:right;">'
            "原文链接："
            f'<a style="color:#576b95;text-decoration:none;" href="{_escape(source_url)}">'
            f"{_escape(source_url)}</a></p>"
        )
    return f'<section style="{SECTION_STYLE}">{"".join(body_parts)}</section>'
