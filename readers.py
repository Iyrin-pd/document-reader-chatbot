import io

import fitz  # PyMuPDF
import pandas as pd
from docx import Document
from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from config import MAX_VISION_CALLS
from vision import describe_image

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
MIN_IMAGE_SIDE = 200          # ignore logos and icons smaller than this
SHEET_GROUP_CHARS = 500


class Budget:
    """Caps how many vision calls one file can use."""

    def __init__(self, n):
        self.n = n

    def take(self):
        if self.n <= 0:
            return False
        self.n -= 1
        return True


def rows_to_markdown(rows):
    rows = [
        [("" if c is None else str(c).replace("\n", " ").strip()) for c in r]
        for r in rows if r
    ]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


# ---------- PDF ----------
def read_pdf(data, budget):
    doc = fitz.open(stream=data, filetype="pdf")
    pages = []
    for i, page in enumerate(doc):
        parts = []
        text = page.get_text().strip()
        if text:
            parts.append(text)

        try:
            for t in page.find_tables().tables:
                md = rows_to_markdown(t.extract())
                if md:
                    parts.append("Table:\n" + md)
        except Exception:
            pass

        scanned = len(text) < 50
        big_images = [
            im for im in page.get_images(full=True)
            if im[2] >= MIN_IMAGE_SIDE and im[3] >= MIN_IMAGE_SIDE
        ]
        if (scanned or big_images) and budget.take():
            png = page.get_pixmap(dpi=150).tobytes("png")
            out = describe_image(png, "transcribe" if scanned else "describe")
            if out:
                label = "Page content (OCR)" if scanned else "Figures on this page"
                parts.append(f"{label}:\n{out}")

        if parts:
            pages.append((i + 1, "\n\n".join(parts)))
    return pages


# ---------- Word ----------
def read_docx(data, budget):
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for t in doc.tables:
        md = rows_to_markdown([[c.text for c in r.cells] for r in t.rows])
        if md:
            parts.append("Table:\n" + md)
    for rel in doc.part.rels.values():
        if "image" in rel.reltype and budget.take():
            out = describe_image(rel.target_part.blob, "describe")
            if out:
                parts.append("Image in document:\n" + out)
    return [(1, "\n\n".join(parts))] if parts else []


# ---------- PowerPoint ----------
def read_pptx(data, budget):
    prs = Presentation(io.BytesIO(data))
    pages = []
    for n, slide in enumerate(prs.slides, 1):
        parts = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text.strip())
            if shape.has_table:
                md = rows_to_markdown([[c.text for c in r.cells] for r in shape.table.rows])
                if md:
                    parts.append("Table:\n" + md)
            if shape.has_chart:
                try:
                    for plot in shape.chart.plots:
                        cats = list(plot.categories)
                        for s in plot.series:
                            vals = ", ".join(f"{c}: {v}" for c, v in zip(cats, s.values))
                            parts.append(f"Chart series '{s.name}': {vals}")
                except Exception:
                    pass
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE and budget.take():
                out = describe_image(shape.image.blob, "describe")
                if out:
                    parts.append("Image on slide:\n" + out)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip():
            parts.append("Speaker notes: " + slide.notes_slide.notes_text_frame.text.strip())
        if parts:
            pages.append((n, "\n\n".join(parts)))
    return pages


# ---------- Excel / CSV ----------
def df_to_entries(df, sheet_name, page):
    df = df.fillna("").astype(str)
    header = [str(c) for c in df.columns]
    head_md = rows_to_markdown([header]).split("\n")[:2]
    entries, group, size = [], [], 0

    def flush():
        if group:
            text = f"Sheet: {sheet_name}\n" + "\n".join(head_md + group)
            entries.append((page, text))

    for row in df.values.tolist():
        line = "| " + " | ".join(c.replace("\n", " ").strip() for c in row) + " |"
        if size + len(line) > SHEET_GROUP_CHARS and group:
            flush()
            group, size = [], 0
        group.append(line)
        size += len(line)
    flush()
    return entries


def read_xlsx(data):
    sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)
    entries = []
    for idx, (name, df) in enumerate(sheets.items(), 1):
        entries += df_to_entries(df, name, idx)
    return entries


def read_csv(data):
    try:
        df = pd.read_csv(io.BytesIO(data))
    except UnicodeDecodeError:
        df = pd.read_csv(io.BytesIO(data), encoding="latin-1")
    return df_to_entries(df, "CSV", 1)


# ---------- dispatcher ----------
def read_document(file_obj, filename):
    """Return a list of (page_number, text) for any supported file."""
    data = file_obj.getvalue() if hasattr(file_obj, "getvalue") else file_obj.read()
    name = filename.lower()
    budget = Budget(MAX_VISION_CALLS)

    if name.endswith(".pdf"):
        return read_pdf(data, budget)
    if name.endswith(".docx"):
        return read_docx(data, budget)
    if name.endswith(".pptx"):
        return read_pptx(data, budget)
    if name.endswith(".xlsx"):
        return read_xlsx(data)
    if name.endswith(".csv"):
        return read_csv(data)
    if name.endswith((".txt", ".md")):
        return [(1, data.decode("utf-8", errors="ignore"))]
    if name.endswith(IMAGE_EXTS):
        out = describe_image(data, "image")
        return [(1, out)] if out else []
    return []