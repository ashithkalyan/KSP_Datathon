"""
KAVACH — Chat History PDF Export
====================================
Builds a single, properly-formatted PDF from an officer's conversation
history, in either English or Kannada (the Kannada script is rendered
correctly by embedding Noto Sans Kannada — the open-source Google font
at backend/assets/fonts — since PDF's built-in standard fonts, which
jsPDF's client-side export was limited to, don't include Kannada
glyphs at all and would silently render blank boxes).

Used by two things:
  1. The manual "Export PDF" button in CrimeChat (scope='session')
  2. The automatic export that fires right before logout (scope='login')
     — see /api/chat/export in main.py and App.jsx's handleLogout.
"""
import os
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
_KANNADA_FONT_PATH = os.path.join(_ASSETS_DIR, "NotoSansKannada-Regular.ttf")

_NAVY = HexColor("#0B1D3A")
_GOLD = HexColor("#C5A028")
_DARK = HexColor("#1E293B")
_GREY = HexColor("#64748B")

_kannada_font_registered = False


def _ensure_kannada_font():
    """Registers the Kannada font with reportlab once per process. If the
    font file is somehow missing, falls back to Helvetica — Kannada text
    would then render as blank glyphs, which is a real degradation but a
    safer failure than crashing the whole export."""
    global _kannada_font_registered
    if _kannada_font_registered:
        return True
    if os.path.exists(_KANNADA_FONT_PATH):
        pdfmetrics.registerFont(TTFont("NotoKannada", _KANNADA_FONT_PATH))
        _kannada_font_registered = True
        return True
    return False


def _font_for(text: str, base_font: str) -> str:
    """Picks the Kannada font whenever the text actually contains Kannada
    Unicode codepoints (U+0C80-U+0CFF), else the requested Latin font —
    keeps English-only exports on the crisper standard PDF fonts."""
    if any('\u0c80' <= ch <= '\u0cff' for ch in text) and _ensure_kannada_font():
        return "NotoKannada"
    return base_font


def _wrap_text(c: canvas.Canvas, text: str, font: str, size: float, max_width: float) -> list:
    """Simple width-aware word wrap using reportlab's own string-width
    metrics — needed because reportlab has no built-in paragraph flow in
    plain canvas mode, and Kannada text wraps at different points than
    Latin text of the same character count."""
    words = text.replace("\r", "").split(" ")
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if c.stringWidth(candidate, font, size) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    # also split on hard newlines within the original text
    out = []
    for line in lines:
        out.extend(line.split("\n")) if "\n" in line else out.append(line)
    return out or [""]


def build_chat_history_pdf(officer_name: str, turns: list, scope: str = "login") -> bytes:
    """
    turns: [{"session_id": str, "role": "user"|"assistant", "text": str, "timestamp": str}, ...]
    already ordered chronologically (session, then turn order) by the caller.
    Returns raw PDF bytes.
    """
    from io import BytesIO
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    page_w, page_h = A4
    margin = 18 * mm
    content_w = page_w - 2 * margin
    y = page_h - margin

    scope_label = {"login": "Full Session (since this login)", "all": "Complete Chat History",
                   "session": "Single Conversation"}.get(scope, scope)

    def header_footer(page_num: int):
        c.setFillColor(_NAVY)
        c.rect(0, page_h - 26 * mm, page_w, 26 * mm, fill=1, stroke=0)
        c.setFillColor(_GOLD)
        c.setFont("Helvetica-Bold", 15)
        c.drawString(margin, page_h - 12 * mm, "KAVACH — Karnataka State Police")
        c.setFont("Helvetica", 8)
        c.setFillColor(HexColor("#B4B4B4"))
        c.drawString(margin, page_h - 18 * mm, f"Crime Intelligence Chat Export — {scope_label}")
        c.drawString(margin, page_h - 23 * mm,
                     f"Officer: {officer_name}  |  Generated: {datetime.now().strftime('%d %b %Y, %H:%M')} IST")

        c.setFillColor(_NAVY)
        c.rect(0, 0, page_w, 10 * mm, fill=1, stroke=0)
        c.setFillColor(_GOLD)
        c.setFont("Helvetica", 7)
        c.drawString(margin, 4 * mm, "CONFIDENTIAL — FOR OFFICIAL USE ONLY — Karnataka State Police")
        c.drawRightString(page_w - margin, 4 * mm, f"Page {page_num}  |  KAVACH Intelligence Platform")

    page_num = 1
    header_footer(page_num)
    y = page_h - 32 * mm

    if not turns:
        c.setFillColor(_GREY)
        c.setFont("Helvetica", 10)
        c.drawString(margin, y, "No conversation turns were recorded for this export.")
        c.save()
        return buf.getvalue()

    current_session = None
    for turn in turns:
        if turn["session_id"] != current_session:
            current_session = turn["session_id"]
            if y < margin + 30 * mm:
                c.showPage(); page_num += 1; header_footer(page_num); y = page_h - 32 * mm
            y -= 4 * mm
            c.setFillColor(HexColor("#F1F5F9"))
            c.rect(margin, y - 5 * mm, content_w, 7 * mm, fill=1, stroke=0)
            c.setFillColor(_NAVY)
            c.setFont("Helvetica-Bold", 8)
            c.drawString(margin + 2 * mm, y - 3 * mm, f"SESSION: {current_session}")
            y -= 10 * mm

        role_label = "OFFICER" if turn["role"] == "user" else "KAVACH-AI"
        role_color = _NAVY if turn["role"] == "user" else _GOLD
        ts = ""
        try:
            ts = datetime.fromisoformat(turn["timestamp"]).strftime("%d %b, %H:%M")
        except (ValueError, TypeError):
            ts = turn.get("timestamp") or ""

        label_font = "Helvetica-Bold"
        body_font_base = "Helvetica"
        body_font = _font_for(turn["text"] or "", body_font_base)
        body_size = 9

        if y < margin + 20 * mm:
            c.showPage(); page_num += 1; header_footer(page_num); y = page_h - 32 * mm

        c.setFont(label_font, 8)
        c.setFillColor(role_color)
        c.drawString(margin, y, f"{role_label}")
        c.setFillColor(_GREY)
        c.setFont("Helvetica", 7)
        c.drawString(margin + 45 * mm, y, ts)
        y -= 4.5 * mm

        c.setFillColor(_DARK)
        c.setFont(body_font, body_size)
        for line in _wrap_text(c, turn["text"] or "", body_font, body_size, content_w - 4 * mm):
            if y < margin + 12 * mm:
                c.showPage(); page_num += 1; header_footer(page_num); y = page_h - 32 * mm
                c.setFont(body_font, body_size)
                c.setFillColor(_DARK)
            c.drawString(margin + 4 * mm, y, line)
            y -= 4.3 * mm
        y -= 3 * mm

    c.save()
    return buf.getvalue()
