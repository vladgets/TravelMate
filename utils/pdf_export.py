"""PDF export utility — converts a markdown itinerary to a downloadable PDF.

Uses fpdf2 (pure Python, no system dependencies) + markdown for HTML conversion.
fpdf2's core fonts (Helvetica) are latin-1 only, so we normalize common Unicode
characters (em-dash, curly quotes, etc.) to ASCII equivalents before rendering.
"""

from __future__ import annotations

import datetime
import re

import markdown as md_lib
from fpdf import FPDF

# Map common Unicode characters that fall outside latin-1 to ASCII equivalents
_UNICODE_MAP = str.maketrans({
    "\u2014": "--",    # em dash —
    "\u2013": "-",     # en dash –
    "\u2018": "'",     # left single quote '
    "\u2019": "'",     # right single quote '
    "\u201c": '"',     # left double quote "
    "\u201d": '"',     # right double quote "
    "\u2026": "...",   # ellipsis …
    "\u2022": "*",     # bullet •
    "\u00b7": "*",     # middle dot ·
    "\u2012": "-",     # figure dash
    "\xa0":   " ",     # non-breaking space
    "\u2019": "'",     # apostrophe variant
})


def _sanitize(text: str) -> str:
    """Replace non-latin-1 characters with ASCII equivalents."""
    text = text.translate(_UNICODE_MAP)
    # Drop any remaining non-latin-1 chars rather than crash
    return text.encode("latin-1", errors="ignore").decode("latin-1")


class _ItineraryPDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, "TravelMate -- AI Vacation Planner", align="L")
        self.set_font("Helvetica", "", 10)
        self.cell(
            0, 8,
            datetime.date.today().strftime("%B %d, %Y"),
            align="R", new_x="LMARGIN", new_y="NEXT",
        )
        self.set_draw_color(200, 200, 200)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 9)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}", align="C")


def generate_itinerary_pdf(markdown_content: str) -> bytes:
    """Convert a markdown itinerary string to PDF bytes."""
    clean = _sanitize(markdown_content)
    html = _markdown_to_clean_html(clean)

    pdf = _ItineraryPDF()
    pdf.set_margins(left=20, top=20, right=20)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.set_text_color(30, 30, 30)

    pdf.write_html(html)

    return bytes(pdf.output())


def _markdown_to_clean_html(text: str) -> str:
    """Convert markdown to HTML, stripping tags fpdf2 can't render."""
    html = md_lib.markdown(text, extensions=["nl2br", "sane_lists"])
    # Replace unsupported block elements with safe equivalents
    html = re.sub(r"<(blockquote|table|thead|tbody|tr|td|th|code|pre)[^>]*>", "<p>", html)
    html = re.sub(r"</(blockquote|table|thead|tbody|tr|td|th|code|pre)>", "</p>", html)
    # Collapse multiple blank paragraphs
    html = re.sub(r"(<p>\s*</p>\s*)+", "<br>", html)
    return html
