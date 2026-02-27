"""Text preprocessing and crash prevention for Eloquence synthesis.

Contains regex-based crash prevention patterns ported from the IBMTTS NVDA
add-on and text normalization utilities.  Provides a single public entry
point -- ``preprocess()`` -- that applies the appropriate fixes for a given
Eloquence voice.
"""

import re
import unicodedata

# ---------------------------------------------------------------------------
# Crash prevention dictionaries
# ---------------------------------------------------------------------------
# Each dictionary maps a compiled regex to a replacement string.  _resub()
# applies them in insertion order, which matters for the date parser pair.

english_fixes = {
    re.compile(r"(\w+)\.([a-zA-Z]+)"): r"\1 dot \2",
    re.compile(r"([a-zA-Z0-9_]+)@(\w+)"): r"\1 at \2",
    # Mc prefix split crash (covers McName and McDONALD)
    re.compile(r"\b(Mc)\s+([A-Z][a-z]|[A-Z][A-Z]+)"): r"\1\2",
    # Date parser bug: "03 Marble" misread as "march ble" (abbreviated first)
    re.compile(
        r"\b(\d+) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
        r"([a-z]+)"
    ): r"\1  \2\3",
    # Undo double-space for actual full month names
    re.compile(
        r"\b(\d+)  (January|February|March|April|May|June|July|August"
        r"|September|October|November|December)\b"
    ): r"\1 \2",
    # caesure / cæsure crash
    re.compile(r"c(ae|\xe6)sur(e)?", re.I): r"seizur",
    # h' + r/v + e crash
    re.compile(r"\b(|\d+|\W+)h'(r|v)[e]", re.I): r"\1h \2e",
    # Consonant cluster + hhes + word continuation (variant 1)
    re.compile(
        r"\b(\w+[bdfhjlmnqrvz])(h[he]s)([abcdefghjklmnopqrstvwy]\w+)\b",
        re.I,
    ): r"\1 \2\3",
    # Consonant cluster + hhes + "iron" (variant 2)
    re.compile(
        r"\b(\w+[bdfhjlmnqrvz])(h[he]s)(iron+[degins]?)", re.I
    ): r"\1 \2\3",
    # Apostrophe + consonant + hhes + word (variant 3, from IBMTTS)
    re.compile(
        r"\b(\w+'{1,}[bcdfghjklmnpqrstvwxyz])'*(h+[he]s)"
        r"([abcdefghijklmnopqrstvwy]\w+)\b",
        re.I,
    ): r"\1 \2\3",
    # Consonant + apostrophe + hhes + word (variant 4, from IBMTTS)
    re.compile(
        r"\b(\w+[bcdfghjklmnpqrstvwxyz])('{1,}h+[he]s)"
        r"([abcdefghijklmnopqrstvwy]\w+)\b",
        re.I,
    ): r"\1 \2\3",
    # Time-like + ordinal suffix (e.g. "2:30th")
    re.compile(r"(\d):(\d\d[snrt][tdh])", re.I): r"\1 \2",
    # Multiple apostrophe consonant clusters
    re.compile(
        r"\b([bcdfghjklmnpqrstvwxz]+)'([bcdefghjklmnpqrstvwxz']+)"
        r"'([drtv][aeiou]?)",
        re.I,
    ): r"\1 \2 \3",
    # "you're'd" contractions
    re.compile(r"\b(you+)'(re)+'([drv]e?)", re.I): r"\1 \2 \3",
    # recosp / uncosp etc.
    re.compile(r"(re|un|non|anti)cosp", re.I): r"\1kosp",
    # EUR codes + digits
    re.compile(r"(EUR[A-Z]+)(\d+)", re.I): r"\1 \2",
    # tzsche (multi-group version from IBMTTS)
    re.compile(
        r"\b(\d+|\W+)?(\w+_+)?(_+)?([bcdfghjklmnpqrstvwxz]+)?(\d+)?"
        r"t+z[s]che",
        re.I,
    ): r"\1 \2 \3 \4 \5 tz sche",
    # juar + long suffix
    re.compile(r"(juar)([a-z']{9,})", re.I): r"\1 \2",
}

french_fixes = {
    re.compile(r"([a-zA-Z0-9_]+)@(\w+)"): r"\1 arobase \2",
    # "tranquille" crash: anquill -> anqill
    re.compile(r"(?<=anq)uil(?=l)", re.I): r"i",
    # "quil" at word boundary crash
    re.compile(r"quil(?=\W)", re.I): r"kil",
}

spanish_fixes = {
    re.compile(r"([a-zA-Z0-9_]+)@(\w+)"): r"\1 arroba \2",
    # Euro/dollar amounts with thousands separators
    re.compile(r"([\u20ac$]\d{1,3})((\s\d{3})+\.\d{2})"): r"\1 \2",
    # Ordinal feminine marker after long numbers
    re.compile(r"(\d{12,}[123679])(\xaa)"): r"\1 \2",
}

german_fixes = {
    re.compile(r"dane-ben", re.I): r"dane- ben",
    re.compile(r"dage-gen", re.I): r"dage- gen",
    # Compound word crash: audio/video-en... (space, not `0 — backticks stripped)
    re.compile(
        r"(audio|video)(-)(en[bcdfghjklmnpqrsvwxz][a-z]+)", re.I
    ): r"\1 \3",
    # Compound word crash: macro-en...
    re.compile(r"(macro)(-)(en[a-z]+)", re.I): r"\1 \3",
}

# ---------------------------------------------------------------------------
# Voice ID constants (from _eloquence.langs)
# ---------------------------------------------------------------------------
_ENGLISH_IDS = (65536, 65537)       # enu, eng
_SPANISH_IDS = (131072, 131073)     # esp, esm
_FRENCH_IDS = (196608, 196609)      # fra, frc
_GERMAN_IDS = (262144,)             # deu
_CHINESE_ID = (393216,)             # chs
_KOREAN_ID = (655360,)              # kor
_ASIAN_IDS = (393216, 524288, 655360)  # chs, jpn, kor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _strip_accents(s):
    """Remove combining marks, leaving base characters."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def _normalize_text(s):
    """Normalize text by removing characters outside the MBCS encoding page.

    Tries to preserve accented characters that fall within the active MBCS
    code page and replaces others with their closest ASCII equivalent or
    ``?`` as a last resort.
    """
    result = []
    for c in s:
        try:
            cc = c.encode("mbcs").decode("mbcs")
        except UnicodeEncodeError:
            cc = _strip_accents(c)
            try:
                cc.encode("mbcs")
            except UnicodeEncodeError:
                cc = "?"
        result.append(cc)
    return "".join(result)


def _resub(dct, s):
    """Apply every regex/replacement pair in *dct* to *s* in order."""
    for pattern, replacement in dct.items():
        s = pattern.sub(replacement, s)
    return s


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def preprocess(text, voice_id):
    """Apply crash prevention fixes and text normalization for *voice_id*."""
    # CHS and KOR get English fixes (they render embedded English text)
    if voice_id in _ENGLISH_IDS + _CHINESE_ID + _KOREAN_ID:
        text = _resub(english_fixes, text)
    elif voice_id in _SPANISH_IDS:
        text = _resub(spanish_fixes, text)
    elif voice_id in _FRENCH_IDS:
        text = _resub(french_fixes, text)
    if voice_id in _GERMAN_IDS:
        text = _resub(german_fixes, text)
    # Asian languages use multi-byte characters that would be corrupted
    if voice_id not in _ASIAN_IDS:
        text = _normalize_text(text)
    return text
