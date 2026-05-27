"""Stylish text generator — 49 Unicode font styles with previewed buttons,
in-place panel editing, and tap-to-copy output."""
from __future__ import annotations

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# In-memory store: cb_id -> source text
_STORE: dict[str, str] = {}
_ORDER: list[str] = []
_MAX = 500


def _store(text: str) -> str:
    import hashlib
    cid = hashlib.md5(text.encode("utf-8")).hexdigest()[:10]
    if cid not in _STORE:
        _STORE[cid] = text
        _ORDER.append(cid)
        if len(_ORDER) > _MAX:
            old = _ORDER.pop(0)
            _STORE.pop(old, None)
    return cid


# ---------- Style transforms ----------
ASCII_UPPER = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
ASCII_LOWER = "abcdefghijklmnopqrstuvwxyz"
ASCII_DIGIT = "0123456789"


def _map(text: str, upper: str, lower: str, digit: str = ASCII_DIGIT) -> str:
    out = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(upper[ord(ch) - 65])
        elif "a" <= ch <= "z":
            out.append(lower[ord(ch) - 97])
        elif "0" <= ch <= "9" and digit:
            out.append(digit[ord(ch) - 48])
        else:
            out.append(ch)
    return "".join(out)


def s_bold(t):          return _map(t, "𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙", "𝐚𝐛𝐜𝐝𝐞𝐟𝐠𝐡𝐢𝐣𝐤𝐥𝐦𝐧𝐨𝐩𝐪𝐫𝐬𝐭𝐮𝐯𝐰𝐱𝐲𝐳", "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗")
def s_italic(t):        return _map(t, "𝐴𝐵𝐶𝐷𝐸𝐹𝐺𝐻𝐼𝐽𝐾𝐿𝑀𝑁𝑂𝑃𝑄𝑅𝑆𝑇𝑈𝑉𝑊𝑋𝑌𝑍", "𝑎𝑏𝑐𝑑𝑒𝑓𝑔ℎ𝑖𝑗𝑘𝑙𝑚𝑛𝑜𝑝𝑞𝑟𝑠𝑡𝑢𝑣𝑤𝑥𝑦𝑧", "")
def s_bolditalic(t):    return _map(t, "𝑨𝑩𝑪𝑫𝑬𝑭𝑮𝑯𝑰𝑱𝑲𝑳𝑴𝑵𝑶𝑷𝑸𝑹𝑺𝑻𝑼𝑽𝑾𝑿𝒀𝒁", "𝒂𝒃𝒄𝒅𝒆𝒇𝒈𝒉𝒊𝒋𝒌𝒍𝒎𝒏𝒐𝒑𝒒𝒓𝒔𝒕𝒖𝒗𝒘𝒙𝒚𝒛", "")
def s_script(t):        return _map(t, "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵", "𝒶𝒷𝒸𝒹ℯ𝒻ℊ𝒽𝒾𝒿𝓀𝓁𝓂𝓃ℴ𝓅𝓆𝓇𝓈𝓉𝓊𝓋𝓌𝓍𝓎𝓏", "")
def s_boldscript(t):    return _map(t, "𝓐𝓑𝓒𝓓𝓔𝓕𝓖𝓗𝓘𝓙𝓚𝓛𝓜𝓝𝓞𝓟𝓠𝓡𝓢𝓣𝓤𝓥𝓦𝓧𝓨𝓩", "𝓪𝓫𝓬𝓭𝓮𝓯𝓰𝓱𝓲𝓳𝓴𝓵𝓶𝓷𝓸𝓹𝓺𝓻𝓼𝓽𝓾𝓿𝔀𝔁𝔂𝔃", "")
def s_fraktur(t):       return _map(t, "𝔄𝔅ℭ𝔇𝔈𝔉𝔊ℌℑ𝔍𝔎𝔏𝔐𝔑𝔒𝔓𝔔ℜ𝔖𝔗𝔘𝔙𝔚𝔛𝔜ℨ", "𝔞𝔟𝔠𝔡𝔢𝔣𝔤𝔥𝔦𝔧𝔨𝔩𝔪𝔫𝔬𝔭𝔮𝔯𝔰𝔱𝔲𝔳𝔴𝔵𝔶𝔷", "")
def s_boldfraktur(t):   return _map(t, "𝕬𝕭𝕮𝕯𝕰𝕱𝕲𝕳𝕴𝕵𝕶𝕷𝕸𝕹𝕺𝕻𝕼𝕽𝕾𝕿𝖀𝖁𝖂𝖃𝖄𝖅", "𝖆𝖇𝖈𝖉𝖊𝖋𝖌𝖍𝖎𝖏𝖐𝖑𝖒𝖓𝖔𝖕𝖖𝖗𝖘𝖙𝖚𝖛𝖜𝖝𝖞𝖟", "")
def s_double(t):        return _map(t, "𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ", "𝕒𝕓𝕔𝕕𝕖𝕗𝕘𝕙𝕚𝕛𝕜𝕝𝕞𝕟𝕠𝕡𝕢𝕣𝕤𝕥𝕦𝕧𝕨𝕩𝕪𝕫", "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡")
def s_mono(t):          return _map(t, "𝙰𝙱𝙲𝙳𝙴𝙵𝙶𝙷𝙸𝙹𝙺𝙻𝙼𝙽𝙾𝙿𝚀𝚁𝚂𝚃𝚄𝚅𝚆𝚇𝚈𝚉", "𝚊𝚋𝚌𝚍𝚎𝚏𝚐𝚑𝚒𝚓𝚔𝚕𝚖𝚗𝚘𝚙𝚚𝚛𝚜𝚝𝚞𝚟𝚠𝚡𝚢𝚣", "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿")
def s_sans(t):          return _map(t, "𝖠𝖡𝖢𝖣𝖤𝖥𝖦𝖧𝖨𝖩𝖪𝖫𝖬𝖭𝖮𝖯𝖰𝖱𝖲𝖳𝖴𝖵𝖶𝖷𝖸𝖹", "𝖺𝖻𝖼𝖽𝖾𝖿𝗀𝗁𝗂𝗃𝗄𝗅𝗆𝗇𝗈𝗉𝗊𝗋𝗌𝗍𝗎𝗏𝗐𝗑𝗒𝗓", "𝟢𝟣𝟤𝟥𝟦𝟧𝟨𝟩𝟪𝟫")
def s_sansbold(t):      return _map(t, "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭", "𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇", "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵")
def s_sansitalic(t):    return _map(t, "𝘈𝘉𝘊𝘋𝘌𝘍𝘎𝘏𝘐𝘑𝘒𝘓𝘔𝘕𝘖𝘗𝘘𝘙𝘚𝘛𝘜𝘝𝘞𝘟𝘠𝘡", "𝘢𝘣𝘤𝘥𝘦𝘧𝘨𝘩𝘪𝘫𝘬𝘭𝘮𝘯𝘰𝘱𝘲𝘳𝘴𝘵𝘶𝘷𝘸𝘹𝘺𝘻", "")
def s_sansbolditalic(t):return _map(t, "𝘼𝘽𝘾𝘿𝙀𝙁𝙂𝙃𝙄𝙅𝙆𝙇𝙈𝙉𝙊𝙋𝙌𝙍𝙎𝙏𝙐𝙑𝙒𝙓𝙔𝙕", "𝙖𝙗𝙘𝙙𝙚𝙛𝙜𝙝𝙞𝙟𝙠𝙡𝙢𝙣𝙤𝙥𝙦𝙧𝙨𝙩𝙪𝙫𝙬𝙭𝙮𝙯", "")
def s_fullwidth(t):     return _map(t, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz", "0123456789")
def s_smallcaps(t):     return _map(t, "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘqʀꜱᴛᴜᴠᴡxʏᴢ", "")
def s_superscript(t):
    sup = {"A":"ᴬ","B":"ᴮ","C":"ᶜ","D":"ᴰ","E":"ᴱ","F":"ᶠ","G":"ᴳ","H":"ᴴ","I":"ᴵ","J":"ᴶ","K":"ᴷ","L":"ᴸ","M":"ᴹ","N":"ᴺ","O":"ᴼ","P":"ᴾ","Q":"Q","R":"ᴿ","S":"ˢ","T":"ᵀ","U":"ᵁ","V":"ⱽ","W":"ᵂ","X":"ˣ","Y":"ʸ","Z":"ᶻ",
           "a":"ᵃ","b":"ᵇ","c":"ᶜ","d":"ᵈ","e":"ᵉ","f":"ᶠ","g":"ᵍ","h":"ʰ","i":"ⁱ","j":"ʲ","k":"ᵏ","l":"ˡ","m":"ᵐ","n":"ⁿ","o":"ᵒ","p":"ᵖ","q":"q","r":"ʳ","s":"ˢ","t":"ᵗ","u":"ᵘ","v":"ᵛ","w":"ʷ","x":"ˣ","y":"ʸ","z":"ᶻ",
           "0":"⁰","1":"¹","2":"²","3":"³","4":"⁴","5":"⁵","6":"⁶","7":"⁷","8":"⁸","9":"⁹"}
    return "".join(sup.get(c, c) for c in t)
def s_subscript(t):
    sub = {"a":"ₐ","e":"ₑ","h":"ₕ","i":"ᵢ","j":"ⱼ","k":"ₖ","l":"ₗ","m":"ₘ","n":"ₙ","o":"ₒ","p":"ₚ","r":"ᵣ","s":"ₛ","t":"ₜ","u":"ᵤ","v":"ᵥ","x":"ₓ",
           "0":"₀","1":"₁","2":"₂","3":"₃","4":"₄","5":"₅","6":"₆","7":"₇","8":"₈","9":"₉"}
    return "".join(sub.get(c.lower(), c) for c in t)
def s_circled(t):       return _map(t, "ⒶⒷⒸⒹⒺⒻⒼⒽⒾⒿⓀⓁⓂⓃⓄⓅⓆⓇⓈⓉⓊⓋⓌⓍⓎⓏ", "ⓐⓑⓒⓓⓔⓕⓖⓗⓘⓙⓚⓛⓜⓝⓞⓟⓠⓡⓢⓣⓤⓥⓦⓧⓨⓩ", "⓪①②③④⑤⑥⑦⑧⑨")
def s_negcircled(t):    return _map(t, "🅐🅑🅒🅓🅔🅕🅖🅗🅘🅙🅚🅛🅜🅝🅞🅟🅠🅡🅢🅣🅤🅥🅦🅧🅨🅩", "🅐🅑🅒🅓🅔🅕🅖🅗🅘🅙🅚🅛🅜🅝🅞🅟🅠🅡🅢🅣🅤🅥🅦🅧🅨🅩", "")
def s_squared(t):       return _map(t, "🄰🄱🄲🄳🄴🄵🄶🄷🄸🄹🄺🄻🄼🄽🄾🄿🅀🅁🅂🅃🅄🅅🅆🅇🅈🅉", "🄰🄱🄲🄳🄴🄵🄶🄷🄸🄹🄺🄻🄼🄽🄾🄿🅀🅁🅂🅃🅄🅅🅆🅇🅈🅉", "")
def s_negsquared(t):    return _map(t, "🅰🅱🅲🅳🅴🅵🅶🅷🅸🅹🅺🅻🅼🅽🅾🅿🆀🆁🆂🆃🆄🆅🆆🆇🆈🆉", "🅰🅱🅲🅳🅴🅵🅶🅷🅸🅹🅺🅻🅼🅽🅾🅿🆀🆁🆂🆃🆄🆅🆆🆇🆈🆉", "")
def s_parenthesized(t): return _map(t, "🄐🄑🄒🄓🄔🄕🄖🄗🄘🄙🄚🄛🄜🄝🄞🄟🄠🄡🄢🄣🄤🄥🄦🄧🄨🄩", "⒜⒝⒞⒟⒠⒡⒢⒣⒤⒥⒦⒧⒨⒩⒪⒫⒬⒭⒮⒯⒰⒱⒲⒳⒴⒵", "")
def s_inverted(t):
    inv = {"a":"ɐ","b":"q","c":"ɔ","d":"p","e":"ǝ","f":"ɟ","g":"ƃ","h":"ɥ","i":"ᴉ","j":"ɾ","k":"ʞ","l":"l","m":"ɯ","n":"u","o":"o","p":"d","q":"b","r":"ɹ","s":"s","t":"ʇ","u":"n","v":"ʌ","w":"ʍ","x":"x","y":"ʎ","z":"z",
           "A":"∀","B":"ᗺ","C":"Ɔ","D":"ᗡ","E":"Ǝ","F":"Ⅎ","G":"ᗐ","H":"H","I":"I","J":"ſ","K":"ʞ","L":"˥","M":"W","N":"N","O":"O","P":"Ԁ","Q":"Ό","R":"ᴚ","S":"S","T":"⊥","U":"∩","V":"Λ","W":"M","X":"X","Y":"⅄","Z":"Z",
           "?":"¿","!":"¡",".":"˙",",":"'"}
    return "".join(inv.get(c, c) for c in t)[::-1]
def s_mirror(t):
    mir = {"a":"ɒ","b":"d","c":"ↄ","d":"b","e":"ɘ","f":"ʇ","g":"ǫ","h":"ʜ","i":"i","j":"į","k":"ʞ","l":"l","m":"m","n":"n","o":"o","p":"q","q":"p","r":"ɿ","s":"ƨ","t":"ƚ","u":"u","v":"v","w":"w","x":"x","y":"y","z":"z",
           "A":"A","B":"ᙠ","C":"Ɔ","D":"ᗡ","E":"Ǝ","F":"ꟻ","G":"Ꭾ","H":"H","I":"I","J":"Ⴑ","K":"ꓘ","L":"⅃","M":"M","N":"N","O":"O","P":"ꟼ","Q":"Ọ","R":"Я","S":"Ƨ","T":"T","U":"U","V":"V","W":"W","X":"X","Y":"Y","Z":"Z"}
    return "".join(mir.get(c, c) for c in t)[::-1]
def s_strike(t):        return "".join(c + "\u0336" for c in t)
def s_underline(t):     return "".join(c + "\u0332" for c in t)
def s_doubleunder(t):   return "".join(c + "\u0333" for c in t)
def s_overline(t):      return "".join(c + "\u0305" for c in t)
def s_slash(t):         return "".join(c + "\u0338" for c in t)
def s_wide(t):          return " ".join(s_fullwidth(t))
def s_squiggle(t):      return "".join(c + "\u0334" for c in t)
def s_zalgo(t):
    import random
    marks = ["\u0300","\u0301","\u0302","\u0303","\u0304","\u0305","\u0306","\u0307","\u0308","\u0309","\u030a","\u0316","\u0317","\u0318","\u0319","\u031c","\u031d","\u031e","\u031f","\u0320","\u0321","\u0322","\u0323","\u0324","\u0325","\u0326","\u0327","\u0328"]
    out = []
    for c in t:
        out.append(c)
        for _ in range(random.randint(2, 6)):
            out.append(random.choice(marks))
    return "".join(out)
def s_brackets(t):      return "「" + t + "」"
def s_thickbrackets(t): return "【" + t + "】"
def s_corners(t):       return "『" + t + "』"
def s_diamond(t):       return "♦" + t + "♦"
def s_star(t):          return "★彡" + t + "彡★"
def s_heart(t):         return "♥" + t + "♥"
def s_arrow(t):         return "➳" + t + "➳"
def s_dotted(t):        return "•".join(t)
def s_dash(t):          return "-".join(t)
def s_underscore(t):    return "_".join(t)
def s_clap(t):          return " 👏 ".join(t.split())
def s_spaced(t):        return " ".join(t)
def s_reverse(t):       return t[::-1]
def s_upper(t):         return t.upper()
def s_lower(t):         return t.lower()
def s_alternating(t):
    out = []
    for i, c in enumerate(t):
        out.append(c.upper() if i % 2 == 0 else c.lower())
    return "".join(out)
def s_random_case(t):
    import random
    return "".join(c.upper() if random.random() < 0.5 else c.lower() for c in t)


STYLES: list[tuple[str, callable]] = [
    ("Bold", s_bold),
    ("Italic", s_italic),
    ("Bold Italic", s_bolditalic),
    ("Script", s_script),
    ("Bold Script", s_boldscript),
    ("Fraktur", s_fraktur),
    ("Bold Fraktur", s_boldfraktur),
    ("Double Struck", s_double),
    ("Monospace", s_mono),
    ("Sans", s_sans),
    ("Sans Bold", s_sansbold),
    ("Sans Italic", s_sansitalic),
    ("Sans Bold Italic", s_sansbolditalic),
    ("Fullwidth", s_fullwidth),
    ("Small Caps", s_smallcaps),
    ("Superscript", s_superscript),
    ("Subscript", s_subscript),
    ("Circled", s_circled),
    ("Neg Circled", s_negcircled),
    ("Squared", s_squared),
    ("Neg Squared", s_negsquared),
    ("Parenthesized", s_parenthesized),
    ("Inverted", s_inverted),
    ("Mirror", s_mirror),
    ("Strikethrough", s_strike),
    ("Underline", s_underline),
    ("Double Underline", s_doubleunder),
    ("Overline", s_overline),
    ("Slashed", s_slash),
    ("Wide", s_wide),
    ("Squiggle", s_squiggle),
    ("Zalgo", s_zalgo),
    ("Brackets", s_brackets),
    ("Thick Brackets", s_thickbrackets),
    ("Corners", s_corners),
    ("Diamond", s_diamond),
    ("Star", s_star),
    ("Heart", s_heart),
    ("Arrow", s_arrow),
    ("Dotted", s_dotted),
    ("Dashed", s_dash),
    ("Underscored", s_underscore),
    ("Clap", s_clap),
    ("Spaced", s_spaced),
    ("Reverse", s_reverse),
    ("UPPER", s_upper),
    ("lower", s_lower),
    ("AlTeRnAtInG", s_alternating),
    ("RaNdOm", s_random_case),
]


# ---------- UI ----------
_PER_PAGE = 16  # 8 rows of 2


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _btn_label(name: str, fn) -> str:
    """Render the style name IN its own style so the button previews it."""
    try:
        out = fn(name)
        if not out or len(out) > 48:
            return name
        return out
    except Exception:
        return name


def _kb(cid: str, page: int = 0) -> InlineKeyboardMarkup:
    pages = (len(STYLES) + _PER_PAGE - 1) // _PER_PAGE
    page = page % pages
    start = page * _PER_PAGE
    chunk = STYLES[start:start + _PER_PAGE]
    rows, row = [], []
    for i, (name, fn) in enumerate(chunk):
        idx = start + i
        row.append(InlineKeyboardButton(
            _btn_label(name, fn), callback_data=f"st:s:{cid}:{idx}:{page}"))
        if len(row) == 2:
            rows.append(row); row = []
    if row: rows.append(row)
    if pages > 1:
        rows.append([
            InlineKeyboardButton("« Prev", callback_data=f"st:p:{cid}:{(page-1) % pages}"),
            InlineKeyboardButton(f"{page+1}/{pages}", callback_data="st:noop"),
            InlineKeyboardButton("Next »", callback_data=f"st:p:{cid}:{(page+1) % pages}"),
        ])
    rows.append([InlineKeyboardButton("Close", callback_data="st:close")])
    return InlineKeyboardMarkup(rows)


def _back_kb(cid: str, page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("« Back to styles", callback_data=f"st:p:{cid}:{page}"),
        InlineKeyboardButton("Close", callback_data="st:close"),
    ]])


def _extract_text(update: Update, args_text: str) -> str:
    if args_text.strip():
        return args_text.strip()
    rep = update.effective_message.reply_to_message
    if rep and (rep.text or rep.caption):
        return (rep.text or rep.caption).strip()
    return ""


def _panel_text(text: str) -> str:
    return (
        f"<b>✨ Stylish Text</b>\n"
        f"Source: <code>{_esc(text)}</code>\n\n"
        f"Pick a style — the styled result appears right here (tap to copy)."
    )


async def cmd_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.effective_message.text or ""
    parts = raw.split(None, 1)
    args_text = parts[1] if len(parts) > 1 else ""
    text = _extract_text(update, args_text)
    if not text:
        await update.effective_message.reply_text(
            "Usage: /style <text>  — or reply to a message with /style\n"
            "Example: /style Hello World"
        )
        return
    if len(text) > 200:
        await update.effective_message.reply_text("Text too long (max 200 chars).")
        return
    cid = _store(text)
    await update.effective_message.reply_text(
        _panel_text(text), parse_mode="HTML", reply_markup=_kb(cid, 0),
    )


async def cb_style(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    data = q.data or ""
    try:
        if data == "st:noop":
            await q.answer(); return
        if data == "st:close":
            await q.answer()
            try: await q.message.delete()
            except Exception: pass
            return
        parts = data.split(":")
        if parts[1] == "p":
            cid, page = parts[2], int(parts[3])
            text = _STORE.get(cid)
            if not text:
                await q.answer("Session expired. Run /style again.", show_alert=True); return
            await q.answer()
            try:
                await q.edit_message_text(
                    _panel_text(text), parse_mode="HTML", reply_markup=_kb(cid, page),
                )
            except Exception:
                pass
            return
        if parts[1] == "s":
            cid = parts[2]; idx = int(parts[3])
            page = int(parts[4]) if len(parts) > 4 else 0
            text = _STORE.get(cid)
            if not text:
                await q.answer("Session expired. Run /style again.", show_alert=True); return
            name, fn = STYLES[idx]
            try:
                styled = fn(text)
            except Exception:
                await q.answer("Style could not be applied right now.", show_alert=True); return
            await q.answer(f"{name} ✓ tap text to copy")
            body = (
                f"<b>✨ {_esc(name)}</b>\n"
                f"Source: <code>{_esc(text)}</code>\n\n"
                f"<code>{_esc(styled)}</code>\n\n"
                f"<i>👆 Tap the styled text above to copy.</i>"
            )
            try:
                await q.edit_message_text(
                    body, parse_mode="HTML", reply_markup=_back_kb(cid, page),
                )
            except Exception:
                try:
                    await q.message.reply_text(
                        body, parse_mode="HTML", reply_markup=_back_kb(cid, page))
                except Exception:
                    await q.message.reply_text(styled)
            return
    except Exception:
        try: await q.answer("This action could not be completed right now.", show_alert=True)
        except Exception: pass


def register(app: Application):
    app.add_handler(CommandHandler("style", cmd_style))
    app.add_handler(CallbackQueryHandler(cb_style, pattern=r"^st:"))
