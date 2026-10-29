"""
Bad-words detection for the advisor's messages (French, English, Tunisian Darija in Latin
"arabizi" and in Arabic script).

    from profanity import scan
    scan("Vous êtes un c0nnard !")
    -> {"found": True, "level": 2, "count": 1, "words": ["c0nnard"], "categories": ["insult"],
        "masked": "Vous êtes un c*****d !"}

Design
  * Whole-token matching, never substring matching: "assistant", "classe", "Scunthorpe" or
    "contrat" are not flagged. Every pattern must match a complete word.
  * Robust to the usual tricks: accents ("enculé"), upper case, leet speak ("c0nnard", "sh1t"),
    stretched letters ("fuuuck"), masked letters ("f*ck", "m*rde"), spaced letters ("f u c k",
    "f.u.c.k"), Arabic script.
  * Levels: 1 = mild (unprofessional), 2 = vulgar or insulting, 3 = severe (sexual / family insults,
    threats). A message gets the level of its worst match.
  * Extra words: put one per line in `profanity_extra.txt` (next to this file) or in the file named by
    the PROFANITY_EXTRA_FILE environment variable. Format: `word` or `word,level` (default level 2).
    Lines starting with # are ignored.
  * Rules only: no model, no network, deterministic, fast. It reports; the caller decides what to do.
"""
from __future__ import annotations

import os
import re
import unicodedata
from typing import Iterable

# --------------------------------------------------------------------------
# Word lists. Each entry is (regex, level, category); the regex must match a WHOLE normalised
# token (lower case, accents removed). Latin-script patterns are also tried on a leet-decoded
# copy of the token; patterns marked darija keep their digits (7 = h, 9 = q, 3 = ain, 5 = kh).
# --------------------------------------------------------------------------
FR = [
    # mild
    (r"bordel", 1, "profanity"), (r"chier|chiante?s?|chiasse", 1, "profanity"), (r"foutre", 1, "profanity"),
    (r"debile?s?", 1, "insult"), (r"idiot(e|es)?s?", 1, "insult"), (r"imbecile?s?", 1, "insult"),
    (r"cretin(e|es)?s?", 1, "insult"), (r"abruti(e|es)?s?", 1, "insult"), (r"stupides?", 1, "insult"),
    (r"ordures?", 1, "insult"), (r"bouffon(ne)?s?", 1, "insult"), (r"couillon(ne)?s?", 1, "insult"),
    (r"andouilles?", 1, "insult"), (r"minables?", 1, "insult"), (r"degage[zr]?", 2, "rude"),
    # vulgar / insulting
    (r"merdes?|merdique?s?|merdeux", 2, "profanity"), (r"putain|putains", 2, "profanity"),
    (r"connard?s?|connasses?", 2, "insult"), (r"cons?|connes?", 2, "insult"),
    (r"salauds?", 2, "insult"), (r"batard(e)?s?", 2, "insult"), (r"garces?|petasses?", 2, "insult"),
    (r"pede?s?", 2, "slur"), (r"trouduc", 3, "insult"),
    # severe
    (r"pute?s?", 3, "insult"), (r"salopes?", 3, "insult"), (r"encule(e|es)?s?|enculer|enculeurs?", 3, "insult"),
    (r"niquer?s?|nique[zs]?", 3, "profanity"), (r"ntm|fdp", 3, "insult"),
]
FR_PHRASES = [
    (r"ta gueule|ferme (ta|la) (gueule|bouche)", 2, "rude"),
    (r"tais toi|taisez vous", 2, "rude"),
    (r"casse toi|cassez vous", 2, "rude"),
    (r"va te faire (voir|foutre|enculer)", 3, "insult"),
    (r"trou du cul", 3, "insult"),
    (r"vous (m|nous) faites chier", 2, "profanity"),
]
EN = [
    (r"damn(ed|it)?|goddamn|dammit|crap(py)?|wtf", 1, "profanity"), (r"stupid|dumb", 1, "insult"),
    (r"idiots?|morons?|jerks?|losers?|scum", 1, "insult"),
    (r"shit(s|ty|head|hole)?|bullshit|bitch(es|y)?|bastards?|assholes?|asses|ass|arse(hole)?s?", 2, "profanity"),
    (r"dick(head)?s?|pricks?|jackass(es)?|dumbass(es)?|stfu|gtfo", 2, "insult"),
    (r"fu+c?k+(s|ed|er|ers|ing|in|head|wit)?|fck(ing|er|ers)?|fuk(ing|er|ers)?|motherfuck(er|ers|ing)?",
     3, "profanity"),
    (r"cunts?|twats?|wankers?|bollocks|whores?|sluts?", 3, "insult"),
]
EN_PHRASES = [
    (r"shut up|shut your (mouth|face)", 2, "rude"),
    (r"piss off|go to hell|screw you|drop dead", 2, "insult"),
]
THREATS = [
    (r"je vais vous (tuer|frapper|casser|buter|defoncer)", 3, "threat"),
    (r"vous allez (le |me )?regretter", 2, "threat"),
    (r"i (will|ll|am going to|m going to) (kill|hurt|beat|find) you", 3, "threat"),
    (r"you will regret (this|it)", 2, "threat"),
]
# Tunisian Darija, arabizi (digits are letters here, so no leet decoding)
DARIJA = [
    (r"[9kq]a[7h]b[ae]h?s?", 3, "insult"),                        # 9a7ba, qahba
    (r"(sh|ch|s)[ae]rmou?[t7]a", 3, "insult"),                    # charmouta
    (r"nik(om+[ae]k|m[ao]k)", 3, "profanity"), (r"nay[ae]k|nayka", 2, "profanity"),   # bare "nik" is also a first name
    (r"zok(om+[ae]k|m[ao]k)", 3, "profanity"), (r"zok", 2, "profanity"),
    (r"z[ae]?b+[ie]?", 2, "profanity"), (r"t[iy]z+[iy]?", 2, "profanity"),
    (r"k[ae]?lb|kl[ae]b", 2, "insult"), ("[7h]allouf", 2, "insult"), (r"zam[ae]l", 2, "slur"),
    (r"[7h]m[ae]r", 1, "insult"), (r"bh[ie]m", 1, "insult"), (r"[7h]ayw[ae]n", 1, "insult"),
    (r"khra|5ra", 1, "profanity"), (r"y[ae]l3[ae]n", 1, "profanity"),
]
DARIJA_PHRASES = [(r"nik om+[ae]k|nik om+ek", 3, "profanity")]
# Arabic script
AR = [
    (r"قحب[ةه]?", 3, "insult"), (r"شرموط[ةه]?", 3, "insult"), (r"ني[كگ](مك|ومك|ها)?", 3, "profanity"),
    (r"زب|زبي", 2, "profanity"), (r"طيز", 2, "profanity"), (r"كلب|كلاب", 2, "insult"),
    (r"حلوف", 2, "insult"), (r"زامل", 2, "slur"), (r"حمار|بهيم|حيوان|غبي|غبيه", 1, "insult"),
    (r"خرا", 1, "profanity"), (r"يلعن", 1, "profanity"),
]

LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})
MASK_FILL = "aeioucknst"                  # letters tried in place of * (1 mask: full alphabet)
ALPHABET = "abcdefghijklmnopqrstuvwxyz"
SAFE_AFTER = {"pros", "pro"}              # "pros and cons" is not an insult
TOKEN_RE = re.compile(r"[^\W_]+(?:[*@$#]+[^\W_]*)*|[*@$#]+[^\W_]+")
SPACED_RE = re.compile(r"(?<![^\W_])(?:[a-zA-Z][ .\-_*]+){2,}[a-zA-Z](?![^\W_])")
LEVEL_NAME = {0: "none", 1: "mild", 2: "vulgar", 3: "severe"}


def _compile(rows: Iterable[tuple]) -> list[tuple]:
    return [(re.compile(rx), lvl, cat) for rx, lvl, cat in rows]


_LATIN = _compile(FR + EN)
_DARIJA = _compile(DARIJA)
_ARABIC = _compile(AR)
_PHRASES = _compile(FR_PHRASES + EN_PHRASES + DARIJA_PHRASES + THREATS)
_EXTRA: list[tuple] = []                  # loaded lazily from the extra-words file
_EXTRA_LOADED = False


def _load_extra() -> None:
    """Optional extra words file, loaded once. A missing or broken file never breaks detection."""
    global _EXTRA_LOADED
    if _EXTRA_LOADED:
        return
    _EXTRA_LOADED = True
    path = os.getenv("PROFANITY_EXTRA_FILE") or os.path.join(os.path.dirname(__file__), "profanity_extra.txt")
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                word, _, lvl = line.partition(",")
                level = int(lvl) if lvl.strip() in {"1", "2", "3"} else 2
                _EXTRA.append((re.compile(re.escape(_norm(word.strip()))), level, "custom"))
    except OSError:
        pass
    except Exception:                      # bad line: ignore the rest rather than break the call
        pass


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.lower().replace("’", "'"))
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.replace("\u0640", "")         # Arabic tatweel (stretching character)


def _variants(tok: str) -> tuple[list[str], list[str]]:
    """(plain forms, leet-decoded forms). Plain = as written plus stretched letters squeezed."""
    plain = [tok]
    for n in (1, 2):                       # "fuuuck" -> "fuck" ; "assss" -> "ass"
        sq = re.sub(r"(.)\1{2,}", r"\1" * n, tok)
        if sq not in plain:
            plain.append(sq)
    leet = [d for d in dict.fromkeys(v.translate(LEET) for v in plain) if d not in plain]
    return plain, leet


def _fullmatch(rows: list[tuple], word: str):
    for rx, lvl, cat in rows:
        if rx.fullmatch(word):
            return lvl, cat
    return None


def _match_token(tok: str):
    """(level, category) when the normalised token is a bad word, else None."""
    t = tok.rstrip("*")
    if not t:
        return None
    if re.search(r"[\u0600-\u06ff]", t):
        return _fullmatch(_ARABIC, t) or _fullmatch(_EXTRA, t)
    if "*" in t:                           # masked letters: f*ck, m*rde, f**k
        n = t.count("*")
        if n > 2:
            return None
        pool = ALPHABET if n == 1 else MASK_FILL
        cands = [t]
        for _ in range(n):
            cands = [c.replace("*", ch, 1) for c in cands for ch in pool]
        hits = [h for h in (_fullmatch(_LATIN, c) for c in cands) if h]
        return max(hits) if hits else None
    plain, leet = _variants(t)
    hits = []
    for v in plain:                        # darija patterns keep their digits, so no leet decoding for them
        hits += [h for h in (_fullmatch(_LATIN, v), _fullmatch(_DARIJA, v), _fullmatch(_EXTRA, v)) if h]
    for v in leet:
        hits += [h for h in (_fullmatch(_LATIN, v), _fullmatch(_EXTRA, v)) if h]
    return max(hits) if hits else None


def _mask(raw: str) -> str:
    """Never echo the bad word itself: keep the first and last letter of each word."""
    return " ".join(w if len(w) <= 2 else w[0] + "*" * (len(w) - 2) + w[-1] for w in raw.split())


def scan(text: str) -> dict:
    """Look for bad words in `text`. Never raises."""
    empty = {"found": False, "level": 0, "count": 0, "words": [], "categories": [], "masked": text or ""}
    if not text or not text.strip():
        return empty
    try:
        return _scan(text)
    except Exception:                      # detection must never break a call
        return empty


def _scan(text: str) -> dict:
    _load_extra()
    toks = [(m.start(), m.end(), m.group(0)) for m in TOKEN_RE.finditer(text)]
    for m in SPACED_RE.finditer(text):     # "f u c k" / "f.u.c.k" -> one virtual token
        joined = re.sub(r"[ .\-_*]", "", m.group(0))
        toks.append((m.start(), m.end(), joined))
    toks.sort(key=lambda x: (x[0], -x[1]))
    normed = [_norm(raw) for _, _, raw in toks]

    found: list[tuple[int, int, str, int, str]] = []      # start, end, raw, level, category
    claimed: list[tuple[int, int]] = []

    # 1) multi-word phrases, on the sequence of tokens joined by single spaces
    flat = " ".join(re.sub(r"[*@$#]", "", n) for n in normed)
    offsets, pos = [], 0
    for n in normed:
        offsets.append(pos)
        pos += len(re.sub(r"[*@$#]", "", n)) + 1
    for rx, lvl, cat in _PHRASES:
        for m in re.finditer(rf"(?<![^\W_])(?:{rx.pattern})(?![^\W_])", flat):
            idx = [i for i, o in enumerate(offsets) if o < m.end() and o + len(re.sub(r"[*@$#]", "", normed[i])) > m.start()]
            if idx:
                s, e = toks[idx[0]][0], toks[idx[-1]][1]
                found.append((s, e, text[s:e], lvl, cat))
                claimed.append((s, e))

    # 2) single words
    for i, (s, e, raw) in enumerate(toks):
        if any(cs <= s and e <= ce for cs, ce in claimed):
            continue
        hit = _match_token(normed[i])
        if not hit:
            continue
        if normed[i] in ("con", "cons") and any(p in SAFE_AFTER for p in normed[max(0, i - 2):i]):
            continue                       # "pros and cons"
        found.append((s, e, raw, hit[0], hit[1]))

    # one entry per span (a spaced-letters virtual token overlaps its own letters)
    uniq: dict[tuple[int, int], tuple] = {}
    for item in sorted(found, key=lambda x: -x[3]):
        if not any(item[0] < ue and us < item[1] for us, ue in uniq):
            uniq[(item[0], item[1])] = item
    hits = sorted(uniq.values(), key=lambda x: x[0])
    if not hits:
        return empty

    masked, last = [], 0
    for s, e, raw, _, _ in hits:
        masked += [text[last:s], _mask(raw)]
        last = e
    masked.append(text[last:])
    return {"found": True, "level": max(h[3] for h in hits), "count": len(hits),
            "words": [_mask(h[2]) for h in hits],
            "categories": sorted({h[4] for h in hits}), "masked": "".join(masked)}


def is_clean(text: str) -> bool:
    return not scan(text)["found"]
