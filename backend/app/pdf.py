"""A small PDF canvas: text, lines, filled boxes and polylines on A4 pages.

The session report (reports.py) writes monospaced text only. The tear sheet
and the tax summary need a chart and a laid-out header, so this adds the few
drawing operators they use. Like reports.py it writes the PDF by hand, with
the standard fonts every viewer has, so the VM needs no rendering stack.

Coordinates are in points from the top-left corner (PDF counts from the
bottom; the canvas converts). Text is Latin-1: the standard fonts carry no
rupee sign, so amounts are written "Rs".
"""

from __future__ import annotations

PAGE_W, PAGE_H = 595, 842  # A4 points

# Standard Type1 fonts, present in every PDF viewer.
FONTS = {"sans": "Helvetica", "bold": "Helvetica-Bold", "mono": "Courier", "mono-bold": "Courier-Bold"}
_FONT_KEY = {name: f"F{i}" for i, name in enumerate(FONTS, start=1)}

INK = (0.12, 0.12, 0.12)
MUTED = (0.42, 0.42, 0.42)
HAIRLINE = (0.82, 0.80, 0.76)
GOLD = (0.55, 0.43, 0.13)
PROFIT = (0.13, 0.50, 0.25)
LOSS = (0.72, 0.18, 0.16)

_PLAIN = {"—": "-", "–": "-", "−": "-", "…": "...", "₹": "Rs ", "’": "'", "‘": "'", "“": '"', "”": '"', "·": "|", "Δ": "delta "}


def latin(text) -> str:
    """Text the standard fonts can show: a few common symbols spelled out, the rest replaced."""
    s = "" if text is None else str(text)
    for k, v in _PLAIN.items():
        s = s.replace(k, v)
    return s.encode("latin-1", "replace").decode("latin-1")


def _esc(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _rgb(c: tuple[float, float, float]) -> str:
    return " ".join(f"{v:.3f}" for v in c)


def wrap(text: str, width: int) -> list[str]:
    """Words into lines of at most about ``width`` characters."""
    lines, cur = [], ""
    for word in str(text).split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    return lines + ([cur] if cur else [])


def mono_width(text: str, size: float) -> float:
    """Courier is fixed-pitch: every glyph is 0.6 em."""
    return 0.6 * size * len(text)


class Canvas:
    """Pages of drawing operators, rendered once at the end."""

    def __init__(self) -> None:
        self.pages: list[list[str]] = []
        self.new_page()

    def new_page(self) -> None:
        self._ops: list[str] = []
        self.pages.append(self._ops)

    @staticmethod
    def _y(y: float) -> float:
        return PAGE_H - y

    def text(self, x: float, y: float, s, size: float = 9, font: str = "sans",
             color: tuple[float, float, float] = INK, align: str = "left") -> None:
        """Text with its baseline at (x, y). Right alignment is exact for the mono fonts only."""
        t = latin(s)
        if align == "right" and font.startswith("mono"):
            x -= mono_width(t, size)
        self._ops.append(
            f"BT /{_FONT_KEY[font]} {size:g} Tf {_rgb(color)} rg 1 0 0 1 {x:.2f} {self._y(y):.2f} Tm ({_esc(t)}) Tj ET"
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, width: float = 0.5,
             color: tuple[float, float, float] = HAIRLINE, dash: bool = False) -> None:
        d = "[2 2] 0 d " if dash else "[] 0 d "
        self._ops.append(
            f"{_rgb(color)} RG {width:g} w {d}{x1:.2f} {self._y(y1):.2f} m {x2:.2f} {self._y(y2):.2f} l S"
        )

    def polyline(self, points: list[tuple[float, float]], width: float = 1.0,
                 color: tuple[float, float, float] = GOLD) -> None:
        if len(points) < 2:
            return
        x0, y0 = points[0]
        path = [f"{x0:.2f} {self._y(y0):.2f} m"] + [f"{x:.2f} {self._y(y):.2f} l" for x, y in points[1:]]
        self._ops.append(f"{_rgb(color)} RG {width:g} w [] 0 d 1 j " + " ".join(path) + " S")

    def rect(self, x: float, y: float, w: float, h: float,
             fill: tuple[float, float, float] | None = None,
             stroke: tuple[float, float, float] | None = None) -> None:
        """A box with its top-left corner at (x, y)."""
        if h < 0:
            y, h = y + h, -h
        op = f"{x:.2f} {self._y(y + h):.2f} {w:.2f} {h:.2f} re"
        if fill and stroke:
            self._ops.append(f"{_rgb(fill)} rg {_rgb(stroke)} RG 0.5 w {op} B")
        elif fill:
            self._ops.append(f"{_rgb(fill)} rg {op} f")
        else:
            self._ops.append(f"{_rgb(stroke or HAIRLINE)} RG 0.5 w {op} S")

    def footer(self, text: str) -> None:
        """The same small line at the foot of every page, with 'page i of n'."""
        here = self._ops
        n = len(self.pages)
        lines = wrap(text, 150)
        for i, ops in enumerate(self.pages, start=1):
            self._ops = ops
            for k, line in enumerate(lines):
                self.text(40, PAGE_H - 22 - 8 * (len(lines) - 1 - k), line, 6.5, color=MUTED)
            self.text(PAGE_W - 40, PAGE_H - 22, f"page {i} of {n}", 6.5, "mono", MUTED, align="right")
        self._ops = here

    def render(self) -> bytes:
        objects: list[bytes] = []

        def add(obj: bytes) -> int:
            objects.append(obj)
            return len(objects)

        font_ids = {
            key: add(f"<< /Type /Font /Subtype /Type1 /BaseFont /{FONTS[name]} /Encoding /WinAnsiEncoding >>".encode())
            for name, key in _FONT_KEY.items()
        }
        fonts = " ".join(f"/{key} {oid} 0 R" for key, oid in font_ids.items())
        content_ids = []
        for ops in self.pages:
            stream = "\n".join(ops).encode("latin-1", "replace")
            content_ids.append(add(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"))
        pages_id = len(objects) + len(self.pages) + 1
        page_ids = [
            add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                f"/Resources << /Font << {fonts} >> >> /Contents {cid} 0 R >>".encode())
            for cid in content_ids
        ]
        kids = " ".join(f"{pid} 0 R" for pid in page_ids)
        add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode())  # object number pages_id
        catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for i, obj in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
        xref_at = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
        for off in offsets:
            out += f"{off:010d} 00000 n \n".encode()
        out += (f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
                f"startxref\n{xref_at}\n%%EOF\n").encode()
        return bytes(out)


def money(v, sign: bool = False) -> str:
    """'Rs 3,847.64', '-Rs 500.00'; a dash for nothing."""
    if v is None:
        return "-"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "-"
    f = round(f, 2) + 0.0
    body = f"Rs {abs(f):,.2f}"
    if f < 0:
        return "-" + body
    return ("+" + body) if sign and f > 0 else body
