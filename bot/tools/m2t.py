"""/m2t — text-to-file converter with a paginated format menu.

Exports:
    FORMATS        — list[(ext, label, category)]
    CATEGORIES     — list[str] (insertion-ordered)
    PAGE_SIZE      — buttons per page
    page_count()   — total pages
    build_page()   — (text, [[(label, callback_data)]]) for a given page
    build_file()   — (bytes, filename, stats) for a given extension + content
    is_supported() — bool
"""
from __future__ import annotations

import time
import io
import zipfile
from datetime import datetime

# (extension WITHOUT leading dot, friendly label, category bucket)
FORMATS: list[tuple[str, str, str]] = [
    # 1) Pure text
    ("txt",  "Plain Text",        "Pure Text"),
    ("md",   "Markdown",          "Pure Text"),
    ("csv",  "CSV",               "Pure Text"),
    ("tsv",  "TSV",               "Pure Text"),
    ("log",  "Log",               "Pure Text"),
    ("ini",  "INI",               "Pure Text"),
    ("cfg",  "Config",            "Pure Text"),
    ("env",  "Env Vars",          "Pure Text"),
    ("yaml", "YAML",              "Pure Text"),
    ("yml",  "YAML",              "Pure Text"),
    ("json", "JSON",              "Pure Text"),
    ("xml",  "XML",               "Pure Text"),
    ("toml", "TOML",              "Pure Text"),
    # 2) Code / scripts
    ("c",   "C",          "Code"),
    ("h",   "C Header",   "Code"),
    ("cpp", "C++",        "Code"),
    ("hpp", "C++ Header", "Code"),
    ("py",  "Python",     "Code"),
    ("java","Java",       "Code"),
    ("js",  "JavaScript", "Code"),
    ("ts",  "TypeScript", "Code"),
    ("php", "PHP",        "Code"),
    ("rb",  "Ruby",       "Code"),
    ("go",  "Go",         "Code"),
    ("rs",  "Rust",       "Code"),
    ("swift","Swift",     "Code"),
    ("kt",  "Kotlin",     "Code"),
    ("sh",  "Shell",      "Code"),
    ("bash","Bash",       "Code"),
    ("ps1", "PowerShell", "Code"),
    ("sql", "SQL",        "Code"),
    ("html","HTML",       "Code"),
    ("css", "CSS",        "Code"),
    ("scss","SCSS",       "Code"),
    ("sass","Sass",       "Code"),
    ("less","Less",       "Code"),
    ("lua", "Lua",        "Code"),
    ("r",   "R",          "Code"),
    ("pl",  "Perl",       "Code"),
    ("asm", "Assembly",   "Code"),
    # 3) Markup / docs source
    ("tex",   "LaTeX",            "Markup"),
    ("rst",   "reStructuredText", "Markup"),
    ("adoc",  "AsciiDoc",         "Markup"),
    ("sgml",  "SGML",             "Markup"),
    ("xhtml", "XHTML",            "Markup"),
    # 4) Web / serialization
    ("rss",   "RSS",              "Web"),
    ("atom",  "Atom",             "Web"),
    ("srt",   "Subtitle (SRT)",   "Web"),
    ("vtt",   "Subtitle (VTT)",   "Web"),
    # 5) Code metadata
    ("gitignore",   ".gitignore",     "Meta"),
    ("gitattributes",".gitattributes","Meta"),
    ("editorconfig",".editorconfig",  "Meta"),
    ("dockerfile",  "Dockerfile",     "Meta"),
    ("makefile",    "Makefile",       "Meta"),
    ("gradle",      "Gradle",         "Meta"),
    ("npmrc",       ".npmrc",         "Meta"),
    ("babelrc",     ".babelrc",       "Meta"),
    ("eslintrc",    ".eslintrc",      "Meta"),
    # 6) Document formats
    ("docx", "Word DOCX",   "Documents"),
    ("odt",  "OpenDocument","Documents"),
    ("rtf",  "Rich Text",   "Documents"),
    ("epub", "EPUB eBook",  "Documents"),
    ("pdf",  "PDF",         "Documents"),
    # 7) Source / config / manifest
    ("manifest",  "Manifest",   "System"),
    ("properties","Properties", "System"),
    ("conf",      "Conf",       "System"),
    ("service",   "Service",    "System"),
    ("desktop",   ".desktop",   "System"),
    ("bat",       "Batch",      "System"),
    ("cmd",       "CMD",        "System"),
]

# Insertion-ordered unique category list.
_seen = set()
CATEGORIES: list[str] = []
for _e, _l, _c in FORMATS:
    if _c not in _seen:
        _seen.add(_c); CATEGORIES.append(_c)

# Extension lookup
_BY_EXT = {ext.lower(): (ext, lbl, cat) for ext, lbl, cat in FORMATS}

PAGE_SIZE = 24  # 3 cols * 8 rows


def is_supported(ext: str) -> bool:
    return (ext or "").lower().lstrip(".") in _BY_EXT


def page_count() -> int:
    n = len(FORMATS)
    return max(1, (n + PAGE_SIZE - 1) // PAGE_SIZE)


def build_page(page: int = 0) -> tuple[str, list[list[tuple[str, str]]]]:
    """Returns (header_text, button_rows).

    Each button row item is (label, callback_data).
    callback_data:
        m2t:f:<ext>   -> show usage for that format
        m2t:p:<page>  -> navigate
        m2t:noop      -> static indicator
    """
    pages = page_count()
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(FORMATS))
    slice_ = FORMATS[start:end]

    # Group within page by category for readability.
    text = (
        "<b>Message → File</b>\n"
        f"Page <b>{page+1}/{pages}</b>  •  {len(FORMATS)} formats supported\n\n"
        "Tap a format below, or run:\n"
        "• <code>/m2t &lt;ext&gt;</code> while replying to a text message\n"
        "• <code>/m2t &lt;ext&gt; your text here</code>"
    )

    rows: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    for ext, label, _cat in slice_:
        cur.append((f".{ext}", f"m2t:f:{ext}"))
        if len(cur) == 3:
            rows.append(cur); cur = []
    if cur:
        rows.append(cur)

    nav: list[tuple[str, str]] = []
    if page > 0:
        nav.append(("« Prev", f"m2t:p:{page-1}"))
    nav.append((f"{page+1}/{pages}", "m2t:noop"))
    if page < pages - 1:
        nav.append(("Next »", f"m2t:p:{page+1}"))
    rows.append(nav)
    if pages > 2:
        rows.append([
            ("⏮ First", "m2t:p:0"),
            ("Last ⏭", f"m2t:p:{pages-1}"),
        ])
    return text, rows


def format_usage(ext: str) -> str:
    info = _BY_EXT.get((ext or "").lower().lstrip("."))
    if not info:
        return "Unknown format."
    ext_, label, cat = info
    return (
        f"<b>.{ext_}</b> — {label}  <i>({cat})</i>\n\n"
        f"Reply to any text message with <code>/m2t {ext_}</code>\n"
        f"or send <code>/m2t {ext_} your text here</code>."
    )


def _safe_filename(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    # Special names that are conventionally extensionless.
    SPECIAL = {
        "gitignore": ".gitignore",
        "gitattributes": ".gitattributes",
        "editorconfig": ".editorconfig",
        "dockerfile": "Dockerfile",
        "makefile": "Makefile",
        "npmrc": ".npmrc",
        "babelrc": ".babelrc",
        "eslintrc": ".eslintrc",
    }
    if ext in SPECIAL:
        base = SPECIAL[ext]
        return f"message_{int(time.time())%1000000:06d}_{base}"
    return f"message_{int(time.time())%1000000:06d}.{ext}"


def build_file(ext: str, text: str) -> tuple[bytes, str, dict]:
    """Return (raw_bytes, filename, stats).

    Text-based formats are emitted as UTF-8. Binary document formats
    (pdf, docx, odt, rtf, epub) are rendered to real, valid files so
    Telegram/PDF readers can open them without "invalid format" errors.
    """
    ext = (ext or "txt").lower().lstrip(".")
    if ext not in _BY_EXT:
        ext = "txt"
    src = text or ""
    if ext == "pdf":
        data = _render_pdf(src)
    elif ext == "rtf":
        data = _render_rtf(src)
    elif ext == "docx":
        data = _render_docx(src)
    elif ext == "odt":
        data = _render_odt(src)
    elif ext == "epub":
        data = _render_epub(src)
    else:
        data = src.encode("utf-8", errors="replace")
    lines = src.count("\n") + (1 if src and not src.endswith("\n") else 0)
    stats = {
        "messages": 1,
        "lines": max(1, lines),
        "characters": len(src),
        "bytes": len(data),
        "ext": ext,
    }
    return data, _safe_filename(ext), stats


# ----------------------------------------------------------------------
# Binary renderers
# ----------------------------------------------------------------------
def _render_pdf(text: str) -> bytes:
    """Render text to a real PDF using fpdf2 (Unicode-safe via core fonts +
    latin-1 replacement for unsupported glyphs)."""
    try:
        from fpdf import FPDF  # fpdf2
    except Exception:
        # Last-resort hand-built minimal PDF (latin-1 only).
        return _render_pdf_minimal(text)
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    safe = (text or "").encode("latin-1", errors="replace").decode("latin-1")
    if not safe.strip():
        safe = " "
    for line in safe.split("\n"):
        try:
            pdf.multi_cell(0, 6, line if line else " ")
        except Exception:
            pdf.multi_cell(0, 6, " ")
    out = pdf.output(dest="S")
    if isinstance(out, str):
        out = out.encode("latin-1", errors="replace")
    return bytes(out)


def _render_pdf_minimal(text: str) -> bytes:
    """Tiny single-page PDF without dependencies. Latin-1 only."""
    safe = (text or " ").encode("latin-1", errors="replace").decode("latin-1")
    # Escape PDF special chars
    safe = safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    lines = safe.split("\n")[:60]
    stream_lines = ["BT", "/F1 11 Tf", "50 800 Td", "13 TL"]
    for i, ln in enumerate(lines):
        stream_lines.append(f"({ln[:120]}) Tj" if i == 0 else f"T* ({ln[:120]}) Tj")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    buf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref_off = len(buf)
    buf += f"xref\n0 {len(objs)+1}\n".encode()
    buf += b"0000000000 65535 f \n"
    for off in offsets:
        buf += f"{off:010d} 00000 n \n".encode()
    buf += f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref_off}\n%%EOF\n".encode()
    return bytes(buf)


def _render_rtf(text: str) -> bytes:
    body = (text or "").replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
    body = body.replace("\n", "\\par\n")
    rtf = "{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Helvetica;}}\\fs22 " + body + "}"
    return rtf.encode("latin-1", errors="replace")


def _render_docx(text: str) -> bytes:
    """Minimal valid .docx (OOXML) without python-docx."""
    from xml.sax.saxutils import escape as xe
    paragraphs = "".join(
        f"<w:p><w:r><w:t xml:space=\"preserve\">{xe(line)}</w:t></w:r></w:p>"
        for line in (text or " ").split("\n")
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f'<w:body>{paragraphs}</w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '</Types>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '</Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)
    return buf.getvalue()


def _render_odt(text: str) -> bytes:
    from xml.sax.saxutils import escape as xe
    paragraphs = "".join(f"<text:p>{xe(line)}</text:p>" for line in (text or " ").split("\n"))
    content_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'office:version="1.2">'
        f'<office:body><office:text>{paragraphs}</office:text></office:body>'
        '</office:document-content>'
    )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
        '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '</manifest:manifest>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/manifest.xml", manifest)
        z.writestr("content.xml", content_xml)
    return buf.getvalue()


def _render_epub(text: str) -> bytes:
    from xml.sax.saxutils import escape as xe
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    body = "".join(f"<p>{xe(line) or '&#160;'}</p>" for line in (text or " ").split("\n"))
    chapter = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml">'
        f'<head><title>Message</title></head><body>{body}</body></html>'
    )
    content_opf = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="BookId" version="3.0">'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        '<dc:identifier id="BookId">urn:uuid:message</dc:identifier>'
        '<dc:title>Message</dc:title><dc:language>en</dc:language>'
        f'<meta property="dcterms:modified">{ts}</meta>'
        '</metadata>'
        '<manifest>'
        '<item id="ch1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>'
        '</manifest>'
        '<spine><itemref idref="ch1"/></spine>'
        '</package>'
    )
    nav = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
        '<head><title>Nav</title></head><body>'
        '<nav epub:type="toc"><ol><li><a href="ch1.xhtml">Message</a></li></ol></nav>'
        '</body></html>'
    )
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>'
        '</container>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container)
        z.writestr("OEBPS/content.opf", content_opf)
        z.writestr("OEBPS/ch1.xhtml", chapter)
        z.writestr("OEBPS/nav.xhtml", nav)
    return buf.getvalue()