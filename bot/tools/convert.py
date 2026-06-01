"""/convert — unit, number-system, encoding and code conversions.

Public API:
    CONVERSIONS    — list[(slug, label, category, doc)]
    CATEGORIES     — list[str]
    page_count()   — total pages
    build_page()   — menu
    is_supported(slug)
    do_convert(slug, value) -> (result_text, explanation_template)
        result_text is None when the slug is unknown deterministically (caller
        should fall back to AI).
"""
from __future__ import annotations

import base64
import hashlib
import html as _html
import math
import re
import urllib.parse

# ---------- catalogue ----------------------------------------------------
# (slug, label, category, doc/example)
CONVERSIONS: list[tuple[str, str, str, str]] = [
    # Number systems
    ("bin2dec",  "Binary → Decimal",      "Number Systems", "1011 → 11"),
    ("dec2bin",  "Decimal → Binary",      "Number Systems", "11 → 1011"),
    ("bin2hex",  "Binary → Hex",          "Number Systems", "11111111 → FF"),
    ("hex2bin",  "Hex → Binary",          "Number Systems", "FF → 11111111"),
    ("bin2oct",  "Binary → Octal",        "Number Systems", "1011 → 13"),
    ("oct2bin",  "Octal → Binary",        "Number Systems", "17 → 1111"),
    ("dec2hex",  "Decimal → Hex",         "Number Systems", "255 → FF"),
    ("hex2dec",  "Hex → Decimal",         "Number Systems", "FF → 255"),
    ("dec2oct",  "Decimal → Octal",       "Number Systems", "8 → 10"),
    ("oct2dec",  "Octal → Decimal",       "Number Systems", "17 → 15"),
    ("oct2hex",  "Octal → Hex",           "Number Systems", "17 → F"),
    ("hex2oct",  "Hex → Octal",           "Number Systems", "FF → 377"),
    ("base",     "Any-base ↔ Any-base",   "Number Systems", "/convert base 2 16 1011"),
    # Digital codes
    ("bin2gray", "Binary → Gray",         "Digital Codes",  "1011 → 1110"),
    ("gray2bin", "Gray → Binary",         "Digital Codes",  "1110 → 1011"),
    ("dec2bcd",  "Decimal → BCD",         "Digital Codes",  "29 → 00101001"),
    ("bcd2dec",  "BCD → Decimal",         "Digital Codes",  "00101001 → 29"),
    ("dec2xs3",  "Decimal → Excess-3",    "Digital Codes",  "5 → 1000"),
    ("xs32dec",  "Excess-3 → Decimal",    "Digital Codes",  "1000 → 5"),
    ("ones",     "Decimal → 1's Comp.",   "Digital Codes",  "/convert ones <bits> <dec>"),
    ("twos",     "Decimal → 2's Comp.",   "Digital Codes",  "/convert twos <bits> <dec>"),
    ("ascii",    "Text → ASCII codes",    "Digital Codes",  "Hi → 72 105"),
    ("unascii",  "ASCII codes → Text",    "Digital Codes",  "72 105 → Hi"),
    # Data / encoding
    ("text2bin", "Text → Binary",         "Data",           "Hi → 01001000 01101001"),
    ("bin2text", "Binary → Text",         "Data",           "01001000 → H"),
    ("text2hex", "Text → Hex",            "Data",           "Hi → 4869"),
    ("hex2text", "Hex → Text",            "Data",           "4869 → Hi"),
    ("b64enc",   "Base64 encode",         "Data",           "Hello → SGVsbG8="),
    ("b64dec",   "Base64 decode",         "Data",           "SGVsbG8= → Hello"),
    ("urlenc",   "URL encode",            "Data",           "a b → a%20b"),
    ("urldec",   "URL decode",            "Data",           "a%20b → a b"),
    ("htmlenc",  "HTML entities encode",  "Data",           "<a> → &lt;a&gt;"),
    ("htmldec",  "HTML entities decode",  "Data",           "&lt;a&gt; → <a>"),
    # Crypto / hash
    ("md5",      "MD5 hash",              "Cryptography",   "hello → 5d41402a..."),
    ("sha1",     "SHA-1 hash",            "Cryptography",   "hello → aaf4c61d..."),
    ("sha256",   "SHA-256 hash",          "Cryptography",   "hello → 2cf24dba..."),
    ("sha512",   "SHA-512 hash",          "Cryptography",   "hello → 9b71d224..."),
    # Networking
    ("ip2bin",   "IPv4 → Binary",         "Networking",     "192.168.1.1 → 11000000..."),
    ("bin2ip",   "Binary → IPv4",         "Networking",     "11000000... → 192.168..."),
    ("cidr2mask","CIDR → Subnet mask",    "Networking",     "/24 → 255.255.255.0"),
    ("mask2cidr","Subnet mask → CIDR",    "Networking",     "255.255.255.0 → /24"),
    # Mathematics / colours
    ("deg2rad",  "Degrees → Radians",     "Mathematics",    "180 → 3.14159"),
    ("rad2deg",  "Radians → Degrees",     "Mathematics",    "3.14159 → 180"),
    ("frac2dec", "Fraction → Decimal",    "Mathematics",    "3/4 → 0.75"),
    ("roman2dec","Roman → Decimal",       "Mathematics",    "XIV → 14"),
    ("dec2roman","Decimal → Roman",       "Mathematics",    "14 → XIV"),
    ("rgb2hex",  "RGB → Hex",             "Mathematics",    "255 0 0 → #FF0000"),
    ("hex2rgb",  "Hex → RGB",             "Mathematics",    "#FF0000 → 255 0 0"),
    # Temperature / units
    ("c2f",      "Celsius → Fahrenheit",  "Units",          "100 → 212"),
    ("f2c",      "Fahrenheit → Celsius",  "Units",          "212 → 100"),
    ("c2k",      "Celsius → Kelvin",      "Units",          "0 → 273.15"),
    ("k2c",      "Kelvin → Celsius",      "Units",          "273.15 → 0"),
    ("km2mi",    "Kilometre → Mile",      "Units",          "10 → 6.2137"),
    ("mi2km",    "Mile → Kilometre",      "Units",          "10 → 16.0934"),
    ("kg2lb",    "Kilogram → Pound",      "Units",          "10 → 22.0462"),
    ("lb2kg",    "Pound → Kilogram",      "Units",          "10 → 4.5359"),
    # Engineering
    ("freq2wl",  "Frequency → Wavelength","Engineering",    "100e6 (Hz) → 3 m"),
    ("wl2freq",  "Wavelength → Frequency","Engineering",    "3 (m) → 100e6 Hz"),
    ("db2ratio", "dB → Power Ratio",      "Engineering",    "20 → 100"),
    ("ratio2db", "Power Ratio → dB",      "Engineering",    "100 → 20"),
    ("j2cal",    "Joule → Calorie",       "Engineering",    "418.4 → 100"),
    ("cal2j",    "Calorie → Joule",       "Engineering",    "100 → 418.4"),
]

_seen = set()
CATEGORIES: list[str] = []
for _s, _l, _c, _d in CONVERSIONS:
    if _c not in _seen:
        _seen.add(_c); CATEGORIES.append(_c)

_BY_SLUG = {s: (s, l, c, d) for s, l, c, d in CONVERSIONS}

PAGE_SIZE = 12  # 2 cols * 6 rows


def is_supported(slug: str) -> bool:
    return (slug or "").lower() in _BY_SLUG


def page_count() -> int:
    return max(1, (len(CONVERSIONS) + PAGE_SIZE - 1) // PAGE_SIZE)


def build_page(page: int = 0) -> tuple[str, list[list[tuple[str, str]]]]:
    pages = page_count()
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(CONVERSIONS))
    sl = CONVERSIONS[start:end]
    text = (
        "<b>Universal Converter</b>\n"
        f"Page <b>{page+1}/{pages}</b>  •  {len(CONVERSIONS)} conversions\n\n"
        "Tap a conversion to see usage, or run:\n"
        "• <code>/convert &lt;type&gt; &lt;value&gt;</code>\n"
        "• Reply to a message with <code>/convert &lt;type&gt;</code>"
    )
    rows: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    for slug, label, _cat, _doc in sl:
        cur.append((label, f"cv:f:{slug}"))
        if len(cur) == 2:
            rows.append(cur); cur = []
    if cur:
        rows.append(cur)
    nav: list[tuple[str, str]] = []
    if page > 0:
        nav.append(("« Prev", f"cv:p:{page-1}"))
    nav.append((f"{page+1}/{pages}", "cv:noop"))
    if page < pages - 1:
        nav.append(("Next »", f"cv:p:{page+1}"))
    rows.append(nav)
    if pages > 2:
        rows.append([
            ("⏮ First", "cv:p:0"),
            ("Last ⏭", f"cv:p:{pages-1}"),
        ])
    return text, rows


def usage_for(slug: str) -> str:
    info = _BY_SLUG.get((slug or "").lower())
    if not info:
        return "Unknown conversion."
    s, label, cat, doc = info
    return (
        f"<b>{label}</b>  <i>({cat})</i>\n\n"
        f"<code>/convert {s} &lt;value&gt;</code>\n"
        f"Example: <i>{_html.escape(doc)}</i>"
    )


# ---------- conversion engine -------------------------------------------

_ROMAN = [
    (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
    (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
    (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
]
_ROMAN_MAP = {"I":1,"V":5,"X":10,"L":50,"C":100,"D":500,"M":1000}


def _strip(value: str) -> str:
    return (value or "").strip()


def _int_or_raise(s: str, base: int = 10) -> int:
    return int(_strip(s), base)


def _gray_to_bin(g: str) -> str:
    g = g.strip()
    if not g: raise ValueError("empty")
    out = [g[0]]
    for i in range(1, len(g)):
        out.append("1" if out[-1] != g[i] else "0")
    return "".join(out)


def _bin_to_gray(b: str) -> str:
    b = b.strip()
    if not b: raise ValueError("empty")
    out = [b[0]]
    for i in range(1, len(b)):
        out.append("1" if b[i-1] != b[i] else "0")
    return "".join(out)


def _dec_to_bcd(n: int) -> str:
    if n < 0: raise ValueError("BCD requires non-negative")
    return " ".join(format(int(d), "04b") for d in str(n))


def _bcd_to_dec(s: str) -> int:
    s = re.sub(r"\s+", "", s)
    if len(s) % 4 != 0:
        raise ValueError("BCD must be multiple of 4 bits")
    digits = []
    for i in range(0, len(s), 4):
        v = int(s[i:i+4], 2)
        if v > 9: raise ValueError("Invalid BCD nibble")
        digits.append(str(v))
    return int("".join(digits))


def _roman_to_dec(s: str) -> int:
    s = s.upper().strip()
    total, prev = 0, 0
    for ch in reversed(s):
        v = _ROMAN_MAP.get(ch)
        if v is None: raise ValueError(f"Bad roman char: {ch}")
        if v < prev: total -= v
        else: total += v; prev = v
    return total


def _dec_to_roman(n: int) -> str:
    if not (0 < n < 4000):
        raise ValueError("Roman range is 1..3999")
    out = []
    for v, sym in _ROMAN:
        while n >= v:
            out.append(sym); n -= v
    return "".join(out)


def _cidr_to_mask(c: int) -> str:
    if not (0 <= c <= 32): raise ValueError("/0../32 only")
    bits = (0xFFFFFFFF << (32 - c)) & 0xFFFFFFFF if c else 0
    return ".".join(str((bits >> (8 * (3 - i))) & 0xFF) for i in range(4))


def _mask_to_cidr(m: str) -> int:
    parts = [int(p) for p in m.strip().split(".")]
    if len(parts) != 4 or any(not (0 <= p <= 255) for p in parts):
        raise ValueError("Bad mask")
    bits = sum(bin(p).count("1") for p in parts)
    return bits


def _ip_to_bin(ip: str) -> str:
    parts = [int(p) for p in ip.strip().split(".")]
    if len(parts) != 4 or any(not (0 <= p <= 255) for p in parts):
        raise ValueError("Bad IP")
    return ".".join(format(p, "08b") for p in parts)


def _bin_to_ip(b: str) -> str:
    parts = re.split(r"[.\s]+", b.strip())
    if len(parts) != 4: raise ValueError("Need 4 binary octets")
    return ".".join(str(int(p, 2)) for p in parts)


def do_convert(slug: str, value: str) -> tuple[str | None, str | None]:
    """Returns (result, ai_explanation_prompt).

    - result is None if the slug is unknown (let the caller delegate to AI).
    - On user-input error, returns (error_msg, None).
    """
    slug = (slug or "").lower()
    v = _strip(value)
    try:
        if slug == "bin2dec":   r = str(int(v, 2))
        elif slug == "dec2bin": r = bin(int(v))[2:] if not v.startswith("-") else "-" + bin(-int(v))[2:]
        elif slug == "bin2hex": r = format(int(v, 2), "X")
        elif slug == "hex2bin": r = bin(int(v, 16))[2:]
        elif slug == "bin2oct": r = format(int(v, 2), "o")
        elif slug == "oct2bin": r = bin(int(v, 8))[2:]
        elif slug == "dec2hex": r = format(int(v), "X")
        elif slug == "hex2dec": r = str(int(v, 16))
        elif slug == "dec2oct": r = format(int(v), "o")
        elif slug == "oct2dec": r = str(int(v, 8))
        elif slug == "oct2hex": r = format(int(v, 8), "X")
        elif slug == "hex2oct": r = format(int(v, 16), "o")
        elif slug == "base":
            # value format: "<from> <to> <number>"
            parts = v.split()
            if len(parts) < 3:
                return ("Usage: /convert base <from> <to> <number>", None)
            fb, tb, num = int(parts[0]), int(parts[1]), parts[2]
            if not (2 <= fb <= 36 and 2 <= tb <= 36):
                return ("Bases must be in 2..36", None)
            n = int(num, fb)
            if tb == 10: r = str(n)
            else:
                digits = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                if n == 0: r = "0"
                else:
                    sign = "-" if n < 0 else ""
                    n = abs(n); out = ""
                    while n: out = digits[n % tb] + out; n //= tb
                    r = sign + out
        elif slug == "bin2gray": r = _bin_to_gray(v)
        elif slug == "gray2bin": r = _gray_to_bin(v)
        elif slug == "dec2bcd":  r = _dec_to_bcd(int(v))
        elif slug == "bcd2dec":  r = str(_bcd_to_dec(v))
        elif slug == "dec2xs3":  r = format(int(v) + 3, "04b") if int(v) <= 9 else " ".join(format(int(d) + 3, "04b") for d in str(int(v)))
        elif slug == "xs32dec":
            s = re.sub(r"\s+", "", v)
            if len(s) % 4 != 0: return ("Excess-3 needs nibbles of 4 bits", None)
            digits = []
            for i in range(0, len(s), 4):
                d = int(s[i:i+4], 2) - 3
                if d < 0 or d > 9: return ("Invalid Excess-3 nibble", None)
                digits.append(str(d))
            r = "".join(digits)
        elif slug == "ones":
            parts = v.split()
            if len(parts) != 2: return ("Usage: /convert ones <bits> <dec>", None)
            bits, n = int(parts[0]), int(parts[1])
            if n >= 0: r = format(n, f"0{bits}b")
            else:
                mag = format(-n, f"0{bits}b")
                r = "".join("1" if c == "0" else "0" for c in mag)
        elif slug == "twos":
            parts = v.split()
            if len(parts) != 2: return ("Usage: /convert twos <bits> <dec>", None)
            bits, n = int(parts[0]), int(parts[1])
            if n >= 0: r = format(n, f"0{bits}b")
            else:
                r = format((1 << bits) + n, f"0{bits}b")
        elif slug == "ascii":   r = " ".join(str(ord(c)) for c in v)
        elif slug == "unascii": r = "".join(chr(int(x)) for x in v.split())
        elif slug == "text2bin": r = " ".join(format(b, "08b") for b in v.encode("utf-8"))
        elif slug == "bin2text":
            parts = re.split(r"\s+", v.strip())
            r = bytes(int(p, 2) for p in parts).decode("utf-8", "replace")
        elif slug == "text2hex": r = v.encode("utf-8").hex().upper()
        elif slug == "hex2text": r = bytes.fromhex(re.sub(r"\s+", "", v)).decode("utf-8", "replace")
        elif slug == "b64enc":   r = base64.b64encode(v.encode("utf-8")).decode("ascii")
        elif slug == "b64dec":   r = base64.b64decode(v.encode("ascii")).decode("utf-8", "replace")
        elif slug == "urlenc":   r = urllib.parse.quote(v, safe="")
        elif slug == "urldec":   r = urllib.parse.unquote(v)
        elif slug == "htmlenc":  r = _html.escape(v)
        elif slug == "htmldec":  r = _html.unescape(v)
        elif slug == "md5":      r = hashlib.md5(v.encode("utf-8")).hexdigest()
        elif slug == "sha1":     r = hashlib.sha1(v.encode("utf-8")).hexdigest()
        elif slug == "sha256":   r = hashlib.sha256(v.encode("utf-8")).hexdigest()
        elif slug == "sha512":   r = hashlib.sha512(v.encode("utf-8")).hexdigest()
        elif slug == "ip2bin":   r = _ip_to_bin(v)
        elif slug == "bin2ip":   r = _bin_to_ip(v)
        elif slug == "cidr2mask":r = _cidr_to_mask(int(v.lstrip("/")))
        elif slug == "mask2cidr":r = f"/{_mask_to_cidr(v)}"
        elif slug == "deg2rad":  r = f"{math.radians(float(v)):.10g}"
        elif slug == "rad2deg":  r = f"{math.degrees(float(v)):.10g}"
        elif slug == "frac2dec":
            num, _, den = v.partition("/")
            r = f"{float(num) / float(den):.10g}"
        elif slug == "roman2dec": r = str(_roman_to_dec(v))
        elif slug == "dec2roman": r = _dec_to_roman(int(v))
        elif slug == "rgb2hex":
            parts = re.split(r"[\s,]+", v.strip())
            if len(parts) != 3: return ("Usage: /convert rgb2hex R G B", None)
            R, G, B = (max(0, min(255, int(p))) for p in parts)
            r = f"#{R:02X}{G:02X}{B:02X}"
        elif slug == "hex2rgb":
            s = v.strip().lstrip("#")
            if len(s) == 3: s = "".join(c*2 for c in s)
            if len(s) != 6: return ("Bad hex colour", None)
            r = " ".join(str(int(s[i:i+2], 16)) for i in (0, 2, 4))
        elif slug == "c2f":    r = f"{float(v) * 9/5 + 32:.4g}"
        elif slug == "f2c":    r = f"{(float(v) - 32) * 5/9:.4g}"
        elif slug == "c2k":    r = f"{float(v) + 273.15:.6g}"
        elif slug == "k2c":    r = f"{float(v) - 273.15:.6g}"
        elif slug == "km2mi":  r = f"{float(v) * 0.621371:.6g}"
        elif slug == "mi2km":  r = f"{float(v) * 1.609344:.6g}"
        elif slug == "kg2lb":  r = f"{float(v) * 2.2046226:.6g}"
        elif slug == "lb2kg":  r = f"{float(v) * 0.45359237:.6g}"
        elif slug == "freq2wl":r = f"{299792458 / float(v):.6g} m"
        elif slug == "wl2freq":r = f"{299792458 / float(v):.6g} Hz"
        elif slug == "db2ratio":r = f"{10 ** (float(v) / 10):.6g}"
        elif slug == "ratio2db":r = f"{10 * math.log10(float(v)):.6g}"
        elif slug == "j2cal":  r = f"{float(v) / 4.184:.6g}"
        elif slug == "cal2j":  r = f"{float(v) * 4.184:.6g}"
        else:
            return (None, None)  # unknown — let AI handle
    except ValueError as e:
        return (f"Invalid input: {e}", None)
    except Exception as e:
        return (f"Conversion failed: {e}", None)

    label = _BY_SLUG[slug][1]
    prompt = (
        f"Explain in 4-6 short lines how the conversion '{label}' works for "
        f"input value `{value}` resulting in `{r}`. Use plain English, "
        f"step-by-step, no markdown headings, no greetings. End with a single "
        f"line `Result: {r}`."
    )
    return (r, prompt)