"""Packaged Cyrillic fonts for PDF adapters."""
from pathlib import Path
from threading import Lock

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

_font_lock = Lock()


def register_pdf_fonts() -> None:
    with _font_lock:
        if 'Bervel' not in pdfmetrics.getRegisteredFontNames():
            folder = Path(__file__).with_name('fonts')
            pdfmetrics.registerFont(TTFont('Bervel', str(folder / 'NotoSans-Regular.ttf')))
            pdfmetrics.registerFont(TTFont('BervelBold', str(folder / 'NotoSans-Bold.ttf')))
