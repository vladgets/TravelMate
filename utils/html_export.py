"""HTML export utility — converts a markdown itinerary to a styled, self-contained HTML file.

No encoding issues, full emoji support, renders tables and all markdown correctly.
Opens in any browser without plugins.
"""

from __future__ import annotations

import datetime
import markdown as md_lib

_CSS = """
    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
        font-size: 16px;
        line-height: 1.7;
        color: #1a1a1a;
        background: #f9f9f9;
        padding: 40px 20px;
    }

    .page {
        max-width: 780px;
        margin: 0 auto;
        background: #ffffff;
        border-radius: 12px;
        box-shadow: 0 2px 20px rgba(0,0,0,0.08);
        padding: 48px 56px;
    }

    .site-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding-bottom: 16px;
        margin-bottom: 32px;
        border-bottom: 2px solid #e8e8e8;
    }

    .site-header .brand {
        font-size: 14px;
        font-weight: 700;
        color: #0066cc;
        letter-spacing: 0.5px;
        text-transform: uppercase;
    }

    .site-header .date {
        font-size: 13px;
        color: #888;
    }

    h1 { font-size: 2em; font-weight: 800; color: #111; margin: 0 0 24px; line-height: 1.2; }
    h2 { font-size: 1.35em; font-weight: 700; color: #0066cc; margin: 36px 0 12px;
         padding-bottom: 6px; border-bottom: 1px solid #e0eeff; }
    h3 { font-size: 1.1em; font-weight: 600; color: #333; margin: 20px 0 8px; }

    p  { margin: 10px 0; }

    ul, ol { padding-left: 24px; margin: 10px 0; }
    li { margin: 4px 0; }

    strong { font-weight: 600; }
    em { font-style: italic; color: #444; }

    table {
        width: 100%;
        border-collapse: collapse;
        margin: 16px 0;
        font-size: 0.95em;
    }
    th {
        background: #f0f5ff;
        color: #0055bb;
        font-weight: 600;
        padding: 10px 14px;
        text-align: left;
        border: 1px solid #d0dff5;
    }
    td {
        padding: 9px 14px;
        border: 1px solid #e8e8e8;
        vertical-align: top;
    }
    tr:nth-child(even) td { background: #fafbff; }

    code {
        background: #f4f4f4;
        border-radius: 4px;
        padding: 2px 6px;
        font-size: 0.88em;
        font-family: "SF Mono", "Fira Code", monospace;
        color: #c7254e;
    }

    blockquote {
        border-left: 4px solid #0066cc;
        margin: 16px 0;
        padding: 10px 16px;
        background: #f0f5ff;
        border-radius: 0 6px 6px 0;
        color: #444;
    }

    hr { border: none; border-top: 1px solid #e8e8e8; margin: 28px 0; }

    .site-footer {
        margin-top: 40px;
        padding-top: 16px;
        border-top: 1px solid #e8e8e8;
        font-size: 12px;
        color: #aaa;
        text-align: center;
    }
"""


def generate_itinerary_html(markdown_content: str) -> bytes:
    """Convert a markdown itinerary string to a styled self-contained HTML file (UTF-8)."""
    body_html = md_lib.markdown(
        markdown_content,
        extensions=["tables", "nl2br", "sane_lists", "fenced_code"],
    )
    today = datetime.date.today().strftime("%B %d, %Y")

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TravelMate Itinerary</title>
  <style>{_CSS}</style>
</head>
<body>
  <div class="page">
    <div class="site-header">
      <span class="brand">✈️ TravelMate</span>
      <span class="date">Generated {today}</span>
    </div>

    {body_html}

    <div class="site-footer">
      Created by TravelMate &mdash; AI Vacation Planner
    </div>
  </div>
</body>
</html>"""

    return html.encode("utf-8")
