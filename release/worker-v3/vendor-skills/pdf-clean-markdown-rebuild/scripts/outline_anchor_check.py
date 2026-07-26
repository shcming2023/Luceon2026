#!/usr/bin/env python3
import argparse
import html
import re
from pathlib import Path


def escape_text(value):
    return html.escape(str(value or ""), quote=False)


def render_inline_markdown(value):
    rendered = escape_text(value)
    rendered = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        lambda m: (
            f'<img class="md-img" src="{html.escape(m.group(2), quote=True)}" '
            f'alt="{html.escape(m.group(1), quote=True)}">'
        ),
        rendered,
    )
    rendered = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">{m.group(1)}</a>',
        rendered,
    )
    rendered = re.sub(r"`([^`]+)`", r"<code>\1</code>", rendered)
    return rendered


def markdown_headings(markdown):
    headings = []
    for line_no, line in enumerate(markdown.splitlines(), 1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append({
                "line": line_no,
                "level": len(match.group(1)),
                "title": match.group(2).strip(),
            })
    for idx, heading in enumerate(headings):
        heading["end_line"] = headings[idx + 1]["line"] - 1 if idx + 1 < len(headings) else len(markdown.splitlines())
        heading["id"] = f"h-{heading['line']}"
    return headings


def build_outline_anchor_check(markdown, title="Outline Anchor Check", source_pdf_name=None):
    lines = markdown.splitlines()
    headings = markdown_headings(markdown)
    heading_by_line = {heading["line"]: heading for heading in headings}
    source_label = source_pdf_name or (title if title and title != "Outline Anchor Check" else "")
    source_pdf_html = ""
    if source_label:
        source_pdf_html = (
            '<div class="source-pdf">'
            '<span>Source PDF</span>'
            f'<strong>{escape_text(source_label)}</strong>'
            '</div>'
        )

    nav_items = []
    for heading in headings:
        pad = (heading["level"] - 1) * 14
        nav_items.append(
            f'<a class="nav-item level-{heading["level"]}" style="padding-left:{pad}px" href="#{heading["id"]}">'
            f'<span class="nav-level">H{heading["level"]}</span>'
            f'<span class="nav-title">{escape_text(heading["title"])}</span>'
            f'<span class="nav-lines">{heading["line"]}-{heading["end_line"]}</span>'
            "</a>"
        )

    body = []
    paragraph_lines = []
    html_lines = []
    in_html = False

    def flush_paragraph():
        if not paragraph_lines:
            return
        text = "\n".join(paragraph_lines).strip()
        paragraph_lines.clear()
        if text:
            body.append(f"<p>{render_inline_markdown(text).replace(chr(10), '<br>')}</p>")

    def flush_html():
        if not html_lines:
            return
        raw = "\n".join(html_lines)
        html_lines.clear()
        body.append(f'<div class="raw-html">{raw}</div>')

    for line_no, line in enumerate(lines, 1):
        heading = heading_by_line.get(line_no)
        if heading:
            flush_paragraph()
            flush_html()
            in_html = False
            body.append(
                f'<section class="slice" id="{heading["id"]}">'
                f'<div class="slice-meta">Markdown line {heading["line"]} - {heading["end_line"]} · H{heading["level"]}</div>'
                f'<h{heading["level"]}>{escape_text(heading["title"])}</h{heading["level"]}>'
            )
            continue

        stripped = line.strip()
        if stripped.startswith("<div") or stripped.startswith("<table") or in_html:
            flush_paragraph()
            html_lines.append(line)
            if "</div>" in stripped or "</table>" in stripped:
                flush_html()
                in_html = False
            else:
                in_html = True
            continue

        if not stripped:
            flush_paragraph()
            flush_html()
            in_html = False
            continue

        paragraph_lines.append(line)

    flush_paragraph()
    flush_html()
    body_html = "\n".join(body)
    if body_html:
        body_html = body_html.replace('\n<section class="slice"', '\n</section>\n<section class="slice"') + "\n</section>"
    else:
        body_html = "<p>No Markdown headings found.</p>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<script>
window.MathJax={{tex:{{inlineMath:[["$","$"],["\\\\(","\\\\)"]],displayMath:[["$$","$$"],["\\\\[","\\\\]"]],processEscapes:true}},options:{{skipHtmlTags:["script","noscript","style","textarea","pre","code"]}}}};
</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<style>
:root{{--ink:#1f2933;--muted:#64748b;--line:#d7dde5;--soft:#f6f7f9;--accent:#0f766e}}
*{{box-sizing:border-box}}
html{{scroll-behavior:smooth}}
body{{margin:0;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;color:var(--ink);background:#eef1f4;line-height:1.55}}
.layout{{display:grid;grid-template-columns:minmax(300px,34vw) 1fr;min-height:100vh}}
aside{{position:sticky;top:0;height:100vh;overflow:auto;padding:18px 12px 32px;background:#fff;border-right:1px solid var(--line)}}
main{{min-width:0;max-width:980px;padding:28px 34px 80px;background:#fff}}
.panel-title{{margin:0 0 4px;font-size:20px}}
.panel-subtitle{{margin:0 0 14px;color:var(--muted);font-size:13px}}
.source-pdf{{border:1px solid var(--line);border-radius:8px;background:var(--soft);padding:9px 10px;margin:10px 0 14px}}
.source-pdf span{{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.04em;margin-bottom:3px}}
.source-pdf strong{{display:block;font-size:13px;line-height:1.35;overflow-wrap:anywhere}}
.nav-item{{display:grid;grid-template-columns:34px 1fr auto;gap:8px;align-items:start;text-decoration:none;color:var(--ink);border-radius:6px;padding:5px 8px;margin:1px 0}}
.nav-item:hover,.nav-item:focus{{background:#e8f3f1;outline:none}}
.nav-level{{color:var(--accent);font-size:11px;font-weight:700;padding-top:2px}}
.nav-title{{font-size:13px;overflow-wrap:anywhere}}
.nav-lines{{color:var(--muted);font-size:11px;white-space:nowrap;padding-top:2px}}
.slice{{scroll-margin-top:18px;padding:18px 0 22px;border-top:1px solid var(--line)}}
.slice:target{{background:linear-gradient(90deg,rgba(15,118,110,.10),transparent 65%);outline:2px solid rgba(15,118,110,.32);outline-offset:8px}}
.slice-meta{{color:var(--muted);font-size:12px;margin-bottom:6px}}
h1,h2,h3,h4,h5,h6{{margin:.35rem 0 .8rem;letter-spacing:0}}
h1{{font-size:30px;border-bottom:2px solid #222;padding-bottom:8px}}
h2{{font-size:23px}}
h3{{font-size:18px}}
p{{margin:.65rem 0}}
.page{{font-size:12px;color:var(--muted);margin:1rem 0 .35rem}}
.raw-html{{overflow:auto;margin:12px 0}}
table{{border-collapse:collapse;width:100%;font-size:13px}}
td,th{{border:1px solid #cbd5e1;padding:5px 7px;vertical-align:top}}
.md-img,img{{max-width:100%;height:auto;display:block;margin:12px 0}}
code{{background:var(--soft);padding:1px 4px;border-radius:4px}}
@media (max-width:900px){{.layout{{grid-template-columns:1fr}}aside{{position:relative;height:auto;max-height:48vh;border-right:0;border-bottom:1px solid var(--line)}}main{{padding:20px}}}}
</style>
</head>
<body>
<div class="layout">
<aside>
<h1 class="panel-title">Markdown Outline Anchors</h1>
<p class="panel-subtitle">Click a heading on the left to jump to the exact clean.md insertion anchor on the right. The line range shows the chunk covered by that heading.</p>
{source_pdf_html}
{''.join(nav_items)}
</aside>
<main>
{body_html}
</main>
</div>
</body>
</html>
"""


def write_outline_anchor_check(markdown, out_dir, title="Outline Anchor Check", source_pdf_name=None):
    out_dir = Path(out_dir)
    html_text = build_outline_anchor_check(markdown, title=title, source_pdf_name=source_pdf_name)
    paths = [out_dir / "outline-view.html", out_dir / "outline-anchor-check.html"]
    for path in paths:
        path.write_text(html_text, encoding="utf-8")
    return paths


def main():
    parser = argparse.ArgumentParser(description="Build a two-pane heading anchor review page for clean.md.")
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--out-dir", type=Path, help="Defaults to the markdown file's parent directory.")
    parser.add_argument("--title", default="Outline Anchor Check")
    parser.add_argument("--source-pdf-name", default="")
    args = parser.parse_args()

    markdown_path = args.markdown.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve() if args.out_dir else markdown_path.parent
    paths = write_outline_anchor_check(
        markdown_path.read_text(encoding="utf-8"),
        out_dir,
        title=args.title,
        source_pdf_name=args.source_pdf_name or None,
    )
    for path in paths:
        print(f"Wrote {path}")


if __name__ == "__main__":
    main()
