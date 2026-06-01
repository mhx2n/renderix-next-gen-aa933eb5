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
    """Return (raw_bytes, filename, stats). Always plain UTF-8 bytes —
    we are converting source text, not re-rendering documents."""
    ext = (ext or "txt").lower().lstrip(".")
    if ext not in _BY_EXT:
        ext = "txt"
    data = (text or "").encode("utf-8", errors="replace")
    lines = (text or "").count("\n") + (1 if text and not text.endswith("\n") else 0)
    stats = {
        "messages": 1,
        "lines": max(1, lines),
        "characters": len(text or ""),
        "bytes": len(data),
        "ext": ext,
    }
    return data, _safe_filename(ext), stats