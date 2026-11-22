#!/usr/bin/env python3
# /// script
# requires-python = ">=3.8"
# dependencies = []
# ///
"""law.py - quote current Ontario and federal Canadian law from the official sources.

Sources
  on = Ontario e-Laws, https://www.ontario.ca/laws (consolidated statutes and regulations)
  ca = Justice Laws Website, https://laws-lois.justice.gc.ca (federal Acts and regulations)

Usage
  python law.py on 02c30 43                 Ontario statute s. 43 (code = e-Laws URL slug)
  python law.py on 02c30 "43(2)"            only subsection (2)
  python law.py on "O. Reg. 17/05" 35       Ontario regulation (same as: on r:050017 35)
  python law.py ca E-1.6 8 10               federal Act sections
  python law.py ca SOR/2012-36 2            federal regulation
  python law.py ca P-8.6 sch1:4.3           clause of a federal schedule (PIPEDA Schedule 1)
  python law.py on r:200522 sch1:2          item 2 of Schedule 1 of an Ontario law (sch1 = the whole schedule)
  python law.py ca E-15 schV:II:2           Schedule V, Part II, item 2 (for schedules numbered per Part)
  python law.py ca SI/97-5 order            the text of an order that has no numbered sections (also: preamble)
  python law.py on 02c30 --find "cooling-off period"     sections that contain a phrase
  python law.py on 02c30 --define "direct agreement"     a defined term
  python law.py on 02c30 --status           title, in-force status and currency only
Options
  --history   also print amendment-history lines     --fresh   skip the 3-hour cache
  --json      machine-readable output               --source  allow as-enacted source law (s23023 ...)
  --no-orders skip the commencement-order check (Ontario)
  The cache is in ~/.cache/law-lookup-cache (Windows: %TEMP%\\law-lookup-cache). Set LAW_CACHE_DIR to move it.
Exit codes
  0 ok  2 bad usage  3 fetch or parse failure  4 not found (law, section, phrase or definition)
  5 the requested text is not in force (not yet in force, repealed or revoked); for --find and --define,
    every match is in such text
  With several sections, 4 wins if any is missing, then 5 if any is not in force.

Text is quoted exactly as published. Anything the tool cannot confirm is printed as a CHECK line.
For Ontario text that is waiting to come into force on "a day to be named", the tool also lists the
commencement orders e-Laws has published for the amending Act (its Orders in Council tab; older orders may not be
listed there, so check The Ontario Gazette for those).
Standard library only (Python 3.8+).
"""
import copy
import datetime
import gzip
import hashlib
import html
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

VERSION = "2.4.1"
FOOTER = "Quoted from the official consolidation. This copy is not an official version. Research aid, not legal advice."
FOOTER_SOURCE = ("Quoted from as-enacted source law, which is not the current consolidated law. "
                 "This copy is not an official version. Research aid, not legal advice.")
UA = "curl/8.5.0"  # e-Laws only serves the full text to non-browser agents
CACHE_TTL = 3 * 3600


def _default_cache_dir():
    """LAW_CACHE_DIR if set. Windows: %TEMP%, which is per user. Elsewhere ~/.cache (or $XDG_CACHE_HOME), never the
    shared /tmp, where another user could plant files."""
    if os.environ.get("LAW_CACHE_DIR"):
        return os.environ["LAW_CACHE_DIR"]
    if os.name == "nt":
        return os.path.join(tempfile.gettempdir(), "law-lookup-cache")
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    if not os.path.isabs(base):         # no home folder: a per-user folder in the temp folder
        return os.path.join(tempfile.gettempdir(), f"law-lookup-cache-{os.getuid()}")
    return os.path.join(base, "law-lookup-cache")


CACHE_DIR = _default_cache_dir()
ELAWS_API = "https://www.ontario.ca/laws/api/v2/legislation/en/"   # the JSON service behind the e-Laws pages
MAX_ORDER_ACTS = 4   # amending Acts checked for commencement orders per lookup
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                      "august", "september", "october", "november", "december"], 1)}


class LawError(Exception):
    def __init__(self, msg, code=3):
        super().__init__(msg)
        self.code = code


# ---------------------------------------------------------------- fetching
_CACHE_OK = {}


def _cache_ready():
    """Create the cache folder, private to this user. On Linux and macOS, refuse to use one that someone else owns or
    can write to (the tool then just downloads every time)."""
    if CACHE_DIR not in _CACHE_OK:
        ok = False
        try:
            os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
            st = os.stat(CACHE_DIR)
            ok = os.name == "nt" or (st.st_uid == os.getuid() and not st.st_mode & 0o022)
        except (OSError, AttributeError):
            pass
        if not ok:
            print(f"law.py: not using the cache folder {CACHE_DIR} (it can't be created, or another user can write to "
                  f"it). Every lookup downloads again. Set LAW_CACHE_DIR to a private folder to fix this.", file=sys.stderr)
        _CACHE_OK[CACHE_DIR] = ok
    return _CACHE_OK[CACHE_DIR]


def _cache_path(url):
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest())   # a file name, not security


def _valid(kind, data):
    """True if a downloaded or cached body looks complete for its kind (guards against cut-off files)."""
    if kind == "html":
        return b"</html>" in data[-4096:].lower()
    if kind == "xml":
        return data.rstrip()[-40:].endswith((b"</Statute>", b"</Regulation>"))
    if kind == "json":
        try:
            json.loads(data.decode("utf-8"))
            return True
        except ValueError:
            return False
    if kind == "date":
        return bool(re.fullmatch(rb'\s*"?[A-Z][a-z]+ \d{1,2}, \d{4}"?\s*', data))
    return True


def _write_atomic(path, data):
    tmp = f"{path}.{os.getpid()}.tmp"
    if not _cache_ready():
        return False
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False


def cache_get(url, kind):
    """Return (text, mtime) for a complete cached copy younger than CACHE_TTL, else None."""
    path = _cache_path(url)
    if not _cache_ready():
        return None
    try:
        mt = os.path.getmtime(path)
        if time.time() - mt >= CACHE_TTL:
            return None
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if not _valid(kind, data):
        return None
    return data.decode("utf-8", "replace"), mt


_BUDGET = {"until": None}      # set by lookup(): every download of one lookup shares this limit
LOOKUP_BUDGET = 240


def fetch(url, fresh=False, timeout=60, kind="html"):
    """Return (text, fetched_at_epoch, from_cache). Only complete downloads are returned or cached."""
    if not fresh:
        hit = cache_get(url, kind)
        if hit:
            return hit[0], hit[1], True
    last, deadline = "unknown error", time.time() + 150
    if _BUDGET["until"]:
        deadline = min(deadline, _BUDGET["until"])
    for attempt in range(3):
        if time.time() >= deadline:
            last = "the time limit for this lookup was reached"
            break
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=max(5, min(timeout, deadline - time.time()))) as r:
                data = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    data = gzip.decompress(data)
            if _valid(kind, data):
                path = _cache_path(url)
                at = os.path.getmtime(path) if _write_atomic(path, data) else time.time()
                return data.decode("utf-8", "replace"), at, False
            last = "the download was cut off or was not the expected page"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise LawError(f"Nothing found at {url} (HTTP 404). Check the code.", 4)
            last = f"HTTP {e.code}"
            if e.code < 500 and e.code != 429:
                break
        except Exception as e:  # URLError, timeout, SSL, connection reset, bad gzip
            last = f"{type(e).__name__}: {e}"
        if attempt == 2 or time.time() > deadline:
            break
        time.sleep(2 + 3 * attempt)
    raise LawError(f"Could not fetch {url} ({last}). Try again, or open it in a browser.", 3)


def meta_get(url):
    """Facts recorded when the cached copy of url was downloaded (such as the currency date then)."""
    if not _cache_ready():
        return None
    try:
        with open(_cache_path(url) + ".meta", "r", encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def meta_put(url, info):
    _write_atomic(_cache_path(url) + ".meta", json.dumps(info).encode("utf-8"))


def fetch_paired(url, kind, currency_fn, fresh=False):
    """Fetch a law page together with the currency statement that was valid when it was downloaded.

    Returns (text, fetched_at, from_cache, currency_info). The currency is read first, then the page, so a page is
    never labelled current to a date later than the one published when it was fetched."""
    if not fresh:
        hit = cache_get(url, kind)
        m = meta_get(url)
        if hit and m and m.get("currency") and abs(m.get("page_mtime", 0) - hit[1]) < 2:   # no currency then: fetch again
            return hit[0], hit[1], True, m.get("currency")
    cur = currency_fn()
    t, at, cached = fetch(url, True, kind=kind)
    meta_put(url, {"page_mtime": at, "currency": cur})
    return t, at, cached, cur


def fetch_json(url, fresh=False, want=dict):
    t, _, _ = fetch(url, fresh, kind="json")
    try:
        v = json.loads(t)
    except ValueError:
        raise LawError(f"{url} did not return JSON. e-Laws may have changed its service.", 3)
    if want is not None and not isinstance(v, want):
        raise LawError(f"{url} returned an unexpected kind of data. e-Laws may have changed its service.", 3)
    return v


# ---------------------------------------------------------------- helpers
INLINE_TAGS = r"(?:a|b|i|u|em|strong|span|font|small|big|ins|del|abbr|cite|code|s|strike|var|q)"


def _sup(m):
    t = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).replace("\xa0", " ").strip()
    if not t:
        return ""
    if re.fullmatch(r"(?i)st|nd|rd|th|e|er|re|[e\u00e8]me|o|a", t):   # ordinals such as 1st or 1er
        return t
    return "^(" + t + ")"                                             # exponents: (1 + I)^(-N)


def clean(s):
    s = re.sub(r"(?is)<sup\b[^>]*>(.*?)</sup>", _sup, s)
    s = re.sub(r"(?is)<sub\b[^>]*>(.*?)</sub>", lambda m: re.sub(r"<[^>]+>", "", m.group(1)).strip(), s)
    s = re.sub(r"(?i)</?" + INLINE_TAGS + r"\b[^>]*>", "", s)   # inline markup can split a word or number
    s = re.sub(r"<[^>]+>", " ", s)                                # line breaks and block tags
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([.,;:)])", r"\1", s)   # e-Laws puts spaces before punctuation after links
    return s.replace("( ", "(")


def norm(s):
    s = re.sub("[\u00ad\u200b\u200c\u200d\u2060\ufeff]", "", s)        # soft hyphens and zero-width characters
    s = re.sub("[\u2009\u202f\u2007\u00a0]", " ", s).replace("\u2011", "-")
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip().lower()


def parse_date(s):
    """'November 17, 2026' or '17/11/2026' (d/m/y) or '2026-11-17' -> date, else None."""
    s = (s or "").strip()
    try:
        m = re.match(r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})", s)
        if m and m.group(1).lower() in MONTHS:
            return datetime.date(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)))
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})$", s)
        if m:
            return datetime.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
        if m:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:              # an impossible date such as November 31
        return None
    return None


def sec_tuple(s):
    try:
        return tuple(int(x) for x in s.split("."))
    except ValueError:
        return ()


def key_tuple(k):
    """'sch1:65.13' -> ('sch1', (65, 13)); '65.13' -> ('', (65, 13))."""
    pre, _, num = k.rpartition(":")
    return pre, sec_tuple(num)


def parse_target(t):
    """'43' '43(2)' '43(2)(a)' 'sch1' 'sch1:4.3' '1:110' -> (key, subsection or None).
    The tool's own citations read back too: 's. 43(2)', 'Sch. 1', 'Sch. 1, 4.3', 'Sch. V, Part II, 2', 'Form 5.1'."""
    t = re.sub(r"(?i)^s\.\s*", "", t.strip())
    if t.lower() in ("order", "preamble"):       # text outside any numbered section (an unsectioned order, a preamble)
        return t.lower(), None
    t = re.sub(r"(?i)^sch(?:edule)?\.?\s*to\s*part\s*", "schpart", t)
    t = re.sub(r"(?i)^form\s*", "schform", t)
    t = re.sub(r"(?i)^sch(?:edule)?\.?\s*(?=[0-9a-z])", "sch", t)
    if t.lower().startswith("sch"):
        t = re.sub(r",\s*", ":", re.sub(r"(?i),\s*part\s*", ":", t)).rstrip(":")    # 'schvi:viii:' is a whole Part
    t = t.replace(" ", "")
    if re.fullmatch(r"(?i)sch(?:form|part)", t):
        raise LawError("Give the form or Part number, such as 'Form 5.1' or 'schpartxxv'.", 2)
    m = re.match(r"(?i)^sch(?:edule)?([0-9a-z]*(?:\.[0-9]+)*)"
                 r"(?::(?:part)?([ivxlc]+(?:\.\d+)*)(?=:\d|$)|:part(\d+(?:\.\d+)*)(?=:\d|$))?"
                 r"(?::(\d+(?:\.\d+)*))?((?:\([^)]+\))*)$", t)
    if m:
        sub = re.match(r"\(([^)]+)\)", m.group(5)) if m.group(5) else None
        prt = (m.group(2) or m.group(3) or "").lower()
        key = "sch" + m.group(1).lower() + (":" + prt + ":" if prt else "") + \
            ((":" if not prt else "") + m.group(4) if m.group(4) else "")
        return key, (sub.group(1) if sub and m.group(4) else None)
    m = re.match(r"^(\d+):(\d+(?:\.\d+)*)((?:\([^)]+\))*)$", t)       # 1:110 = Schedule 1, s. 110
    if m:
        sub = re.match(r"\(([^)]+)\)", m.group(3)) if m.group(3) else None
        return f"sch{m.group(1)}:{m.group(2)}", (sub.group(1) if sub else None)
    m = re.match(r"^(\d+(?:\.\d+)*)((?:\([^)]+\))*)$", t)
    if not m:
        raise LawError(f"Can't read section '{t}'. Use forms like 43, 43(2), 43.1, sch1 or sch1:4.3.", 2)
    sub = re.match(r"\(([^)]+)\)", m.group(2)) if m.group(2) else None
    return m.group(1), (sub.group(1) if sub else None)


def cite(t):
    """How a section or schedule item is printed: '43(2)' -> 's. 43(2)'; 'sch1' -> 'Sch. 1';
    'sch1:4.1.3' -> 'Sch. 1, 4.1.3'; 'schV:II:2' -> 'Sch. V, Part II, 2'; '1:110' -> 'Sch. 1, 110';
    'schform5.1' -> 'Form 5.1'."""
    t = str(t)
    if t in ("order", "preamble"):
        return t.capitalize()
    plain = "s. " + re.sub(r"(?i)^s\.\s*", "", t.strip())
    try:
        key, _ = parse_target(t)
    except LawError:
        return plain
    m = re.match(r"^sch([^:]*)(?::([^:]*):)?:?(.*)$", key)
    if not m:
        return plain
    name, part, item = m.group(1), m.group(2), m.group(3)
    if name.startswith("form"):
        out = ["Form " + name[4:].upper()]
    elif name.startswith("part"):
        out = ["Sch. to Part " + name[4:].upper()]
    else:
        out = ["Sch. " + name.upper()] if name else ["Schedule"]
    if part:
        out.append("Part " + part.upper())
    if item:
        paren = re.search(r"((?:\([^)]+\))+)$", t.replace(" ", ""))
        out.append(item + (paren.group(1) if paren else ""))
    return ", ".join(out)


DEF_TERM_RE = re.compile(r"""(["'])(.+?)\1""")
DEF_SEP_RE = re.compile(r"\s*(?:,\s*(?:or\s+|and\s+)?|or\s+|and\s+)")


def def_terms(tx):
    """Quoted terms a normalized definition row starts with: '"a" or "b" means ...' -> ['a', 'b']."""
    out, pos = [], 0
    while True:
        m = DEF_TERM_RE.match(tx, pos)
        if not m:
            return out
        out.append(m.group(2))
        sep = DEF_SEP_RE.match(tx, m.end())
        if not sep:
            return out
        pos = sep.end()


def flag(flags, level, where, text):
    item = {"level": level, "where": where, "text": text}
    if item not in flags:
        flags.append(item)


# ---------------------------------------------------------------- Ontario e-Laws
ON_BASES = {
    "section", "subsection", "paragraph", "subpara", "subsubpara", "clause", "subclause", "subsubclause",
    "definition", "firstdef", "defclause", "defsubclause", "defsubsubclause", "defsubsubsubclause",
    "defparagraph", "defsubpara", "headnote", "headnoteitalic", "heading1", "heading2", "heading3",
    "heading4", "headingx", "partnum", "partnumRepeal", "schedule", "note", "table", "footnote",
    "footnoteLeft", "equation", "equationind1", "equationind2", "paranoindt", "parawindt", "comment",
    "ConsolidationPeriod", "chapter", "shorttitle", "longtitle", "preamble", "assent", "regnumber",
    "regtitle", "version", "leadText", "Notice", "formRevoked", "toc", "TOCid", "TOCheadCenter",
    "TOCpartCenter", "TOCHeadCenter", "TOCPartCenter", "MsoNormal", "headnoteind", "subsubsubpara", "parawindt2",
    "parawindt3", "heading1x", "partnumRevoked", "ellipsis",
}
ON_HEADER = {"leadText", "shorttitle", "chapter", "regnumber", "regtitle", "ConsolidationPeriod", "comment",
             "version", "longtitle", "preamble", "assent"}
ON_HEADNOTE = {"headnote", "headnoteitalic", "headnoteind"}
ON_HEADING = {"partnum", "partnumRepeal", "partnumRevoked", "heading1", "heading1x", "heading2", "heading3", "heading4",
              "headingx"}
ON_DEF = {"definition", "firstdef"}
ON_DEF_CONT = {"defclause", "defsubclause", "defsubsubclause", "defsubsubsubclause", "defparagraph",
               "defsubpara", "clause", "subclause", "subsubclause", "paragraph", "subpara", "subsubpara",
               "note", "equation", "equationind1", "equationind2", "table"}
P_RE = re.compile(r'<p\s+class="([^"]+)"[^>]*>(.*?)</p>|<tr\b', re.S | re.I)
SEC_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?(?=\s|\(|$)")
SEC_RANGE_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s*[-\u2013]\s*(\d+(?:\.\d+)*)\.?(?=\s)")
SEC_LIST_RE = re.compile(r"^((?:\d+(?:\.\d+)*\.?,\s*)+\d+(?:\.\d+)*\.?)(?=\s)")    # "10., 10.1 Revoked: ..."
SEC_LEAD_RE = re.compile(r"^(\d+(?:\.\d+)*)")
ADD_NOTE_RE = re.compile(r"(?i)\bby adding the following (?:Parts?|Schedules?|headings?|sections?)\b")
REPEALED_RE = re.compile(r"(?i)^(?:\d+(?:\.\d+)*\.?(?:\s*(?:[-\u2013,]|and|to)\s*\d+(?:\.\d+)*\.?)*\s*)?(?:\([^)]+\)\s*)*"
                         r"\[?\s*(repealed|revoked|spent)\b")
SUB_RE = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s*)?\(([0-9]+(?:\.[0-9]+)*)\)")
NOTE_RE = re.compile(r"^Not\s?e\s*:", re.I)
CONTINGENT_RE = re.compile(r"(?i)\bOn the (?:(?:later|earlier) of the )?day\b\s*(.+?)\s*\bcomes? into force")
ADD_DEF_RE = re.compile(r"(?i)\badding the following definitions?\b")
PENDING_NOTE_RE = re.compile(r"(?i)day to be named|comes? into force|not yet in force|"
                             r"\bon [A-Z][a-z]+ \d{1,2}, \d{4}|is (?:repealed|revoked|amended)|are (?:repealed|revoked|amended)")


def on_code(code):
    """Return (kind, slug). kind is 'statute' or 'regulation'."""
    c = code.strip()
    m = re.match(r"(?i)^O\.?\s*Reg\.?\s*(\d+)\s*/\s*(\d{2})$", c)
    if m:
        return "regulation", f"{m.group(2)}{int(m.group(1)):04d}"
    m = re.match(r"(?i)^R\.?R\.?O\.?\s*1990,?\s*Reg\.?\s*(\d+)$", c)
    if m:
        return "regulation", f"90{int(m.group(1)):04d}"
    if c.lower().startswith("r:"):
        return "regulation", c[2:].strip()
    if re.match(r"^\d{6}$", c) or re.match(r"(?i)^r\d{5}$", c):
        return "regulation", c
    return "statute", c


def classify(cls):
    c = re.sub(r"-(e|f)$", "", cls)
    nif = False
    if len(c) > 1 and c[0] == "Y":
        nif, c = True, c[1:]
    if len(c) > 1 and c[0] == "P" and c[1:2].islower() and c[1:] in ON_BASES:
        c = c[1:]                      # P-prefix: source-law layout and editorial notes
    elif len(c) > 1 and c[0] == "S" and c[1:2].islower() and c[1:] in ON_BASES:
        c = "stext"                    # S-prefix: text inside forms, statements and formulas
    return c, nif


def elaws_currency(fresh=False):
    """Return (currency date as e-Laws states it, where it was read)."""
    try:                                   # plain text such as "November 18, 2026" (sometimes JSON-quoted)
        v, _, _ = fetch(ELAWS_API + "currency-date", fresh, kind="date")
        v = v.strip().strip('"').strip()
        if re.fullmatch(r"[A-Z][a-z]+ \d{1,2}, \d{4}", v) and parse_date(v):
            return v, "as displayed on e-Laws"
    except LawError:
        pass
    try:                                   # fallback: the How to use e-Laws page (can lag a day)
        t, _, _ = fetch("https://www.ontario.ca/laws/about-e-laws", fresh)
    except LawError:
        return None, None
    s = clean(re.sub(r"<(script|style)\b.*?</\1>", "", t, flags=re.S))
    m = re.search(r"current \(up-to-date\) to ([A-Z][a-z]+ \d{1,2}, \d{4}) \(e-Laws currency date\)", s)
    return (m.group(1), "stated on https://www.ontario.ca/laws/about-e-laws") if m else (None, None)


# ---------------------------------------------------------------- Ontario commencement orders
SEE_RE = re.compile(r"\(See:?\s*([^)]*)\)")
CHAPTER_RE = re.compile(r"\b((?:19|20)\d{2}), c\. (\d{1,3})\b")


def source_act_code(year, chapter):
    return f"s{int(year) % 100:02d}{int(chapter):03d}"


def parse_oic(d, code):
    """Pull the order date and the operative paragraphs out of an e-Laws commencement order."""
    if not isinstance(d, dict):
        raise LawError(f"e-Laws returned an unexpected record for order {code}.", 3)
    content = d.get("content") if isinstance(d.get("content"), str) else ""
    ordered, ops, started = None, [], False
    for m in P_RE.finditer(content):
        if m.group(1) is None:
            continue
        cls = re.sub(r"-(e|f)$", "", m.group(1))
        text = clean(m.group(2))
        if not text:
            continue
        om = re.match(r"(?i)^Ordered:\s*(.+)$", text)
        if om:
            ordered = om.group(1).strip()
            continue
        if cls == "preamble":
            started = True
            continue
        if not started or cls == "Pheading":
            continue
        if cls in ("number", "headingx", "section", "signature"):
            break                          # the minister's line, then any correction notice
        if cls.startswith("parawindt") and ops:
            ops[-1] += " " + text
        else:
            ops.append(text)
    if not ordered and d.get("dateFrom"):
        try:
            ordered = datetime.date.fromisoformat(str(d["dateFrom"])[:10]).strftime("%B %d, %Y").replace(" 0", " ")
        except ValueError:
            pass
    named = sorted({x for x in re.findall(r"([A-Z][a-z]+ \d{1,2}, \d{4}) is named as the day", " ".join(ops))},
                   key=lambda x: parse_date(x) or datetime.date.max)
    title = d.get("title") if isinstance(d.get("title"), str) else code
    return {"code": code, "title": title, "ordered": ordered, "named_days": named,
            "text": ops, "url": f"https://www.ontario.ca/laws/oic/{code}"}


def _dig(d, *keys):
    """d[k1][k2]... where every level must be a dict; None if the shape is different."""
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def on_orders(act_code, fresh=False):
    """Commencement orders that e-Laws lists for one source Act (its Orders in Council tab; older orders may not be listed)."""
    base = ELAWS_API + "act-explanatory/statute/" + act_code
    d = fetch_json(base + "?title=x", fresh)
    hits = _dig(d, "hits", "hits")
    src = hits[0].get("_source") if isinstance(hits, list) and hits and isinstance(hits[0], dict) else None
    name = _dig(src, "act", "en")
    if not isinstance(name, str) or not name or str(_dig(src, "code") or "").lower() != act_code.lower():
        raise LawError(f"e-Laws did not return the Act {act_code}.", 4)
    d = fetch_json(base + "?title=" + urllib.parse.quote(name, safe=""), fresh)
    agg = _dig(d, "aggregations", "all", "oics")
    listed = _dig(agg, "hits", "hits", "hits")
    if not isinstance(agg, dict) or not isinstance(listed, list):
        raise LawError("e-Laws did not return its list of commencement orders.", 3)
    orders = []
    for h in listed:
        s = h.get("_source") if isinstance(h, dict) else None
        oc = str(_dig(s, "code") or "")
        if _dig(s, "act", "en") != name or not re.match(r"^o\d{6}$", oc):
            continue
        orders.append(parse_oic(fetch_json(ELAWS_API + "doc-search/oic/" + oc, fresh), oc))
    orders.sort(key=lambda o: parse_date(o["ordered"] or "") or datetime.date.min)
    count = agg.get("doc_count")
    return {"act_code": act_code, "act": re.sub(r"\s+,", ",", name), "url": f"https://www.ontario.ca/laws/statute/{act_code}",
            "listed": count if isinstance(count, int) else None, "orders": orders}


def attach_orders(res, enacted_by, cur_date, fresh=False):
    """List commencement orders for every Act that holds text waiting for 'a day to be named'."""
    texts = [f["text"] for f in res["flags"] if f["level"] in ("PENDING", "NOT_IN_FORCE")]
    texts += [n for n in res.get("header_notes") or []]
    for e in res.get("sections") or []:
        texts += [r["text"] for r in e.get("rows", []) if r.get("kind") == "note"]
    for d in res.get("definitions") or []:
        texts += [r["text"] for r in d.get("rows", []) if r.get("kind") == "note"]
    codes = []
    if res["status"].startswith("NOT YET IN FORCE") and enacted_by:
        codes.append(enacted_by)
    hist = []
    for e in (res.get("sections") or []) + (res.get("definitions") or []):
        hist += e.get("history") or []
    for hx in hist:                       # "2025, c. 24, Sched. 11, s. 5 (1-3) - not in force"
        for seg in hx.split(";"):
            hm = re.match(r"\s*((?:19|20)\d{2}), c\. (\d{1,3})\b", seg)
            if hm and re.search(r"(?i)\bnot in force\b", seg):
                code = source_act_code(hm.group(1), hm.group(2))
                if code not in codes:
                    codes.append(code)
    for tx in texts:
        if not (re.search(r"(?i)day to be named", tx) or CONTINGENT_RE.search(tx)):
            continue
        for see in SEE_RE.findall(tx):
            for y, c in CHAPTER_RE.findall(see):
                code = source_act_code(y, c)
                if code not in codes:
                    codes.append(code)
    if not codes:
        return
    if len(codes) > MAX_ORDER_ACTS:
        flag(res["flags"], "CHECK", "commencement orders", f"{len(codes)} amending Acts hold pending text; only the first "
             f"{MAX_ORDER_ACTS} were checked. Check the others' Orders in Council tab on e-Laws.")
    cd = parse_date(cur_date or "")
    today = datetime.date.today()
    short = norm(re.split(r",\s*(?:S\.O\.|R\.S\.O\.|R\.R\.O\.|O\.\s*Reg\.)", res.get("title") or "")[0]).strip()
    res["commencement_orders"] = []
    for code in codes[:MAX_ORDER_ACTS]:
        try:
            info = on_orders(code, fresh)
        except Exception as e:             # never let the order check hide the law text
            flag(res["flags"], "CHECK", "commencement orders", f"Could not list the commencement orders for {code} ({e}). "
                 f"Open https://www.ontario.ca/laws/statute/{code} and check its Orders in Council tab.")
            continue
        res["commencement_orders"].append(info)
        if info["listed"] is not None and info["listed"] > len(info["orders"]):
            flag(res["flags"], "CHECK", "commencement orders", f"e-Laws lists {info['listed']} orders for {info['act']} but only "
                 f"{len(info['orders'])} could be read. See {info['url']}.")
        for o in info["orders"]:
            od = parse_date(o["ordered"] or "")
            name = o["title"].title().replace("In Council", "in Council")
            o["mentions_law"] = bool(short) and short in norm(" ".join(o["text"]))
            about = " It names this law." if o["mentions_law"] else " It does not name this law; read it to be sure."
            if not od:
                flag(res["flags"], "CHECK", "commencement orders", f"Could not read the date of {name}. Read it: {o['url']}")
            elif not cd:
                flag(res["flags"], "CHECK", "commencement orders", f"{name} was made on {o['ordered']}. The e-Laws currency "
                     f"date could not be read, so it is unknown whether the text above reflects it.{about} Read it: {o['url']}")
            elif od >= cd:
                when = "on" if od == cd else "after"
                flag(res["flags"], "CHECK", "commencement orders", f"{name} was made on {o['ordered']}, {when} the e-Laws "
                     f"currency date ({cur_date}), so the text above may not show it yet.{about} Read it: {o['url']}")
            for nd in o["named_days"]:
                ndd = parse_date(nd)
                if ndd and ndd <= today and (not cd or cd < ndd):
                    when = ("which is after the e-Laws currency date" if cd else
                            "which has passed (the e-Laws currency date could not be read)")
                    flag(res["flags"], "CHECK", "commencement orders", f"{name} brings provisions into force on {nd}, {when}. "
                         f"Those provisions are in force now even if the text above still shows them as not in force."
                         f"{about} Read it: {o['url']}")
            if not o["text"]:
                flag(res["flags"], "CHECK", "commencement orders", f"Could not read what {name} does. Read it: {o['url']}")


def history_flags(flags, where, hx, today, prefix=""):
    if re.search(r"(?i)not in force", hx):
        flag(flags, "PENDING", where, f"{prefix}Amendment listed as not in force: {hx}")
    for dm in re.finditer(r"(\d{1,2}/\d{1,2}/\d{4})", hx):
        d = parse_date(dm.group(1))
        if d and d > today:
            flag(flags, "PENDING", where, f"{prefix}Amendment with a future in-force date: {hx}")
    if re.search(r"(?i)see Table of Public Statute Provisions", hx):
        flag(flags, "CHECK", where, f"{prefix}In-force date not shown; check the Table of Public Statute Provisions: {hx}")


def contingent_check(flags, where, text):
    """A change that waits for another provision: say which one, since the tool does not track it."""
    lm = re.search(r"(?i)\bOn the (later|earlier) of (.+)\bcomes? into force,", text)
    if lm:
        conds = [re.sub(r"(?i)^the day\s+", "", x.strip()) for x in re.split(r"(?i)\s+comes? into force\s+and\s+", lm.group(2))]
        flag(flags, "CHECK", where, f"This change takes effect on the {lm.group(1).lower()} of the days these come into force: "
             f"{'; '.join(conds)}. The tool does not check them. Look up their status before relying on the current wording.")
        return
    m = CONTINGENT_RE.search(text)
    if m:
        flag(flags, "CHECK", where, f"This change takes effect when another provision comes into force ({m.group(1)}). "
             f"The tool does not check that provision. Look up its status before relying on the current wording.")


def parse_elaws(t, source_law=False):
    if "og:description" not in t and "ConsolidationPeriod" not in t:
        raise LawError("e-Laws returned a page without law text (a script-only shell or an error page).", 3)
    meta = {"title": None, "under": None, "consolidation": None, "last_amendment": None, "notes": [], "aliases": {}}
    m = re.search(r"<title>(.*?)</title>", t, re.S)
    if m:
        meta["title"] = clean(m.group(1)).strip('"').replace(" | Ontario.ca", "").strip()
    units, cur, ctx, row, seen_section, sched, ranges = [], None, [], 0, False, None, []
    carry, carry_used = None, False          # a note that adds a pending Part or several sections
    heading_notes = []                       # notes that add a heading before a given section
    for mm in P_RE.finditer(t):
        if mm.group(1) is None:        # a <tr> tag: new table row
            row += 1
            continue
        raw_cls = re.sub(r"-(e|f)$", "", mm.group(1))
        base, nif = classify(mm.group(1))
        text = clean(mm.group(2))
        if not text or base == "MsoNormal" or base.startswith("TOC") or base == "toc":
            continue
        is_note = base in ("note", "Notice") or bool(NOTE_RE.match(text))
        if not seen_section and base == "preamble":
            units.append({"key": "preamble", "kind": "text", "text": text, "nif": nif, "row": row, "base": base, "ctx": []})
            continue
        if not seen_section and base in ON_HEADER | {"note", "Notice"} or (not seen_section and is_note):
            if base == "ConsolidationPeriod":
                meta["consolidation"] = text
            elif base == "comment":
                meta["last_amendment"] = text
            elif base == "leadText" and "Under:" in text:
                meta["under"] = text.split("Under:", 1)[1].strip()
            elif base == "leadText" and meta["title"] and re.match(r"(?:O\. Reg\.|R\.R\.O\.)", meta["title"]) \
                    and text.startswith(meta["title"] + ", "):
                meta["under"] = text[len(meta["title"]) + 2:].strip()     # some regulations omit "Under:"
            elif is_note:
                meta["notes"].append(text)
            continue
        if base == "schedule" and seen_section:
            sm = re.match(r"(?i)^schedule\s*([0-9]+(?:\.[0-9]+)*|[IVXLC]+(?:\.[0-9]+)*|[A-Z](?:\.[0-9]+)*)?(?!\w)", text)
            sched = ((sm.group(1) or "") if sm else "").lower()
            cur = "sch" + sched
            ctx = [text]
            carry = None
            units.append({"key": cur, "kind": "heading", "text": text, "nif": nif, "row": row, "base": base, "ctx": [text]})
            continue
        kind = "text"
        if base in ON_HEADNOTE:
            kind = "headnote"
        elif base in ON_HEADING:
            kind = "heading"
        elif base == "footnoteLeft":
            kind = "history"
        elif is_note:
            kind = "note"
        unparsed = False
        copy_note = None
        sec_start = base == "section" and re.match(r"\d", text)
        if not sec_start and base == "subsection" and seen_section and cur is not None:
            # e-Laws sometimes tags the first row of a section "subsection" (FBCSA s. 70 and s. 96):
            # a bold number other than the current section's, followed by "(1)", starts a new section
            nm = re.match(r"(\d+(?:\.\d+)*)\s*\(\d", text)
            if nm and nm.group(1) != cur.split(":")[-1] and re.match(r"(?:\s*<a\b[^>]*>\s*</a>)*\s*<strong>", mm.group(2)):
                sec_start = True
        if sec_start:
            rng = SEC_RANGE_RE.match(text)
            lst = None if rng else SEC_LIST_RE.match(text)
            sm = None if (rng or lst) else SEC_RE.match(text)
            if rng:                  # e.g. "65.13-65.21 Repealed: ..." or "74.-76 Repealed" covers a run of sections
                num = rng.group(1)
                pre = f"sch{sched}:" if sched is not None else ""
                ranges.append((pre + rng.group(1), pre + rng.group(2)))
            elif lst:                # e.g. "10., 10.1 Revoked: ..." covers each listed section
                nums = [x.strip().rstrip(".") for x in lst.group(1).split(",") if x.strip()]
                num = nums[0]
            elif sm:
                num = sm.group(1)
            else:                    # a number we can't read: keep it apart rather than merge it into the last section
                num, unparsed = SEC_LEAD_RE.match(text).group(1), True
            cur = (f"sch{sched}:{num}") if sched is not None else num
            for hn, target in list(heading_notes):
                if target == num:
                    hn["key"] = cur
                    heading_notes.remove((hn, target))
            if lst:
                for n in nums[1:]:
                    meta["aliases"][(f"sch{sched}:{n}") if sched is not None else n] = cur
            seen_section = True
            # headnotes and headings just above a section belong to that section (never a schedule heading)
            j = len(units) - 1
            while j >= 0 and units[j]["kind"] in ("headnote", "heading") and units[j].get("base") != "schedule":
                units[j]["key"] = cur
                units[j]["pre"] = True       # a heading above the section, shown as context only
                j -= 1
            if nif and j >= 0 and units[j]["kind"] == "note" and re.search(
                    r"(?i)adding the following section|section " + re.escape(num) + r"\b", units[j]["text"]):
                units[j]["key"] = cur
                if units[j] is carry:
                    carry_used = True
            if carry is not None:
                if not nif:
                    carry = None             # the added Part or run of sections has ended
                elif not carry_used:
                    carry["key"], carry_used = cur, True
                else:
                    copy_note = dict(carry, key=cur, carried=True) if carry["key"] != cur else None
        if kind == "heading":
            htext = ("[NOT YET IN FORCE] " if nif else "") + text
            ctx = [htext] if base in ("partnum", "partnumRepeal", "partnumRevoked", "headingx") else (ctx[:1] + [htext])
        unit = {"key": cur, "kind": kind, "text": text, "nif": nif, "row": row, "base": base, "ctx": list(ctx)}
        if unparsed:
            unit["unparsed"] = True
        if base not in ON_BASES and base != "stext":
            unit["unknown_class"] = raw_cls
        if copy_note:
            units.append(copy_note)          # the carried note goes just before the section it covers
        units.append(unit)
        if kind == "note" and ADD_NOTE_RE.search(text):
            carry, carry_used = unit, False
        hm = re.search(r"(?i)adding the following heading (?:immediately )?before section (\d+(?:\.\d+)*)", text) if kind == "note" else None
        if hm:
            heading_notes.append((unit, hm.group(1)))
    if not seen_section:
        raise LawError("Could not find any sections on the page. e-Laws may have changed its layout.", 3)
    meta["ranges"] = ranges
    return meta, units


def elaws_status(meta, today):
    """Return (status, flags) for the whole Act or regulation."""
    flags, status = [], "IN FORCE"
    for n in meta["notes"]:
        if re.search(r"(?i)this (act|regulation) is not yet in force", n):
            status = "NOT YET IN FORCE"
            flag(flags, "NOT_IN_FORCE", "whole law", n)
        m = re.search(r"(?i)this (act|regulation) was (repealed|revoked) on ([A-Z][a-z]+ \d{1,2}, \d{4})", n)
        if m:
            status = f"{m.group(2).upper()} on {m.group(3)}"
            flag(flags, "REPEALED", "whole law", n)
        m = re.search(r"(?i)this (act|regulation) is (repealed|revoked)(?: on (a day to be named|[A-Z][a-z]+ \d{1,2}, \d{4}))?", n)
        if m:
            d = parse_date(m.group(3) or "")
            if d and d <= today:
                flag(flags, "CHECK", "whole law", f"{n} That date has passed, but e-Laws still shows the text. Confirm the law is still in force.")
            else:
                flag(flags, "PENDING", "whole law", n)
            status = f"IN FORCE (but {m.group(2)} " + (f"on {m.group(3)})" if m.group(3) else "on a future event; see note)")
    c = meta.get("consolidation") or ""
    m = re.search(r"From (.+?) to (.+?)\.?$", c)
    if m and "currency date" not in m.group(2):
        flag(flags, "CHECK", "whole law", f"This consolidation ends on {m.group(2)}; it may not be the current version.")
    return status, flags


# ---------------------------------------------------------------- federal Justice Laws
FED_BLOCK = {"Subsection", "Paragraph", "Subparagraph", "Clause", "Subclause", "Subsubclause", "Definition",
             "Provision", "Item", "List", "FormulaGroup", "FormulaDefinition", "ContinuedSectionSubsection",
             "ContinuedParagraph", "ContinuedSubparagraph", "ContinuedClause", "ContinuedDefinition", "row",
             "MarginalNote", "HistoricalNote", "ReadAsText", "Oath", "Note", "TableGroup", "GroupHeading",
             "Formula", "FormulaConnector", "FormulaParagraph", "ContinuedFormulaParagraph", "Section"}
LIMS = "{http://justice.gc.ca/lims}"


def ca_code(code):
    """Return (kind, code) where kind is 'act' or 'reg'."""
    c = code.strip()
    if c.lower().startswith("r:"):
        c = c[2:].strip()
        kind = "reg"
    else:
        kind = "reg" if re.match(r"(?i)^(SOR|SI|C\.R\.C)", c) else "act"
    c = re.sub(r"^(SOR|SI)\s*/\s*", r"\1-", c, flags=re.I)
    c = re.sub(r"(?i)^C\.R\.C\.,?\s*c\.\s*(\d+)$", r"C.R.C.,_c._\1", c)
    return kind, c.upper() if not c.startswith("C.R.C") else c


MN = "\u00a7MN\u00a7"   # marks a marginal note (a headnote) inside rendered federal text
BARE_LABEL = re.compile(r"\s*(?:(?:\d+(?:\.\d+)*|\([\w.]+\))\s*)*")


def render_lines(e, skip=("Heading",)):
    """Flatten a Justice Laws element into lines. Punctuation is kept exactly as published."""
    lines, buf = [], [""]

    def flush():
        t = re.sub(r"\s+", " ", buf[0]).strip()
        if t and t != "History:":
            t = re.sub(r"(\S)\[(Repealed|Revoked)\b", r"\1 [\2", t)
            lines.append(re.sub(r"\s+([.,;:)])", r"\1", t))
        buf[0] = ""

    def walk(x):
        tag = x.tag
        if tag in FED_BLOCK:
            if not BARE_LABEL.fullmatch(buf[0]):   # keep a bare label such as '10.1' with the text after it
                flush()
            elif buf[0] and not buf[0].endswith(" "):
                buf[0] += " "
            if tag == "HistoricalNote":
                flush()
                buf[0] = "History: "
        if tag == "MarginalNote":
            flush()
            buf[0] = MN
        if tag in ("Label", "FormulaTerm"):
            buf[0] += (label_text(x) if tag == "Label" else "".join(x.itertext()).strip()) + " "
            return                                  # children are already in the text (footnote markers dropped)
        if tag == "entry":
            buf[0] += (" | " if buf[0].strip() else "") + (x.text or "")
        else:
            buf[0] += x.text or ""
        for c in x:
            if c.tag in skip:
                buf[0] += c.tail or ""
                continue
            walk(c)
            if c.tag == "HistoricalNoteSubItem":
                buf[0] += "; "
            buf[0] += c.tail or ""
        if tag in FED_BLOCK:
            if tag == "HistoricalNote":
                buf[0] = re.sub(r"[;\s]+$", "", buf[0])
            flush()

    walk(e)
    flush()
    return lines


NUM_LABEL_RE = re.compile(r"^\d+(?:\.\d+)*")


def label_text(lab):
    """Text of a Justice Laws Label without footnote markers such as <FootnoteRef>*</FootnoteRef>."""
    if lab is None:
        return ""
    return ((lab.text or "") + "".join(("" if c.tag == "FootnoteRef" else "".join(c.itertext())) + (c.tail or "")
                                       for c in lab)).strip()
FED_CONTAINERS = {"DocumentInternal", "BillPiece", "Group", "Part", "Division", "Subdivision", "Order"}


def fed_nif(el, today):
    for x in el.iter():
        if x.get("in-force") == "no":
            return True
        d = parse_date(x.get(LIMS + "inforce-start-date") or "")
        if d and d > today:
            return True
    return False


def item_label(el):
    """The number of a schedule item: its Label, a leading Emphasis, or a numbered title."""
    lb = el.find("Label")
    if lb is not None and label_text(lb):
        return label_text(lb)
    tx = el.find("Text")
    if tx is not None and not (tx.text or "").strip() and len(tx) and tx[0].tag == "Emphasis":
        return "".join(tx[0].itertext()).strip()
    tt = el.find("TitleText")
    if tt is not None:
        m = re.match(r"(\d+(?:\.\d+)*)\s", "".join(tt.itertext()).strip() + " ")
        if m:
            return m.group(1)
    return None


def label_clauses(lab):
    """'4.3.1' -> ['4.3.1']; '3. and 4' -> ['3', '4']; '7 to 9' -> ['7', '8', '9']; '(a)' or 'PART I' -> []."""
    if not lab or not NUM_LABEL_RE.match(lab):
        return []
    nums = re.findall(r"\d+(?:\.\d+)*", lab)
    if re.fullmatch(r"\d+\.?\s*to\s*\d+\.?", lab) and int(nums[1]) - int(nums[0]) < 200:
        return [str(n) for n in range(int(nums[0]), int(nums[1]) + 1)]
    if re.fullmatch(r"\d+(?:\.\d+)*\.?\s*to\s*\d+(?:\.\d+)*\.?", lab):
        return [nums[0], "~" + nums[1]]          # a decimal range: first item, then '~' and the last
    if re.fullmatch(r"[\d.]+(?:\s*(?:,|and)\s*[\d.]+)*\.?", lab):
        return [n.rstrip(".") for n in nums]
    return [NUM_LABEL_RE.match(lab).group(0)]


def parse_fed_schedule(sch, key, heading, today):
    """Every element of a federal schedule, in order, keyed by schedule; 'clause' is the item number it belongs to."""
    units = []
    st = {"clause": None, "also": [], "part": None, "ctx": [heading]}

    def set_clause(cl):
        ends = [c[1:] for c in cl[1:] if c.startswith("~")]
        st["clause"], st["also"] = cl[0], [c for c in cl[1:] if not c.startswith("~")]
        st["range"] = (cl[0], ends[0]) if ends else None

    def add(kind, text, nif):
        units.append({"key": key, "kind": kind, "text": text, "nif": nif, "ctx": list(st["ctx"]),
                      "clause": st["clause"], "clause_also": list(st["also"]), "part": st["part"],
                      "clause_range": st.get("range")})

    def walk(el):
        for ch in el:
            tag = ch.tag
            if tag == "ScheduleFormHeading":
                continue
            if tag in ("GroupHeading", "Heading"):
                txt = " ".join(x.strip() for x in ch.itertext() if x.strip())
                lab = item_label(ch) or ""
                pm = re.match(r"(?i)^part\s+((?:[IVXLC]+|\d+)(?:\.\d+)*)(?![\w.])", lab or txt)
                if pm:
                    st["part"], st["clause"], st["also"], st["range"] = pm.group(1).lower(), None, [], None
                else:
                    cl = label_clauses(lab or txt.split(" ")[0])
                    if cl:
                        set_clause(cl)
                st["ctx"] = [heading, txt]
                add("heading", txt, fed_nif(ch, today))
                continue
            if tag in FED_CONTAINERS:
                walk(ch)
                continue
            if tag in ("Provision", "Section", "Item", "Definition"):
                cl = label_clauses(item_label(ch))
                if cl:
                    set_clause(cl)
            nif = fed_nif(ch, today)
            for ln in render_lines(ch, skip=()):
                kind = "history" if ln.startswith("History:") else ("headnote" if ln.startswith(MN) else "text")
                add(kind, ln.replace(MN, ""), nif)

    walk(sch)
    return units


REF_RE = re.compile(r"(?i)\b(?:sections?|subsections?|paragraphs?|subparagraphs?|clauses?|items?)\s+"
                    r"(\d+(?:\.\d+)*(?:\s*\([^)]*\))*(?:\s*(?:,|and|or|to)\s+\d+(?:\.\d+)*(?:\s*\([^)]*\))*)*)")
SCHED_MENTION_RE = re.compile(r"(?i)\bSchedule\s+([IVXLC]+(?:\.\d+)*|\d+(?:\.\d+)*|[A-Z])?\s*to\s+the\s+(?:Act|Regulations?)\b")


def amendment_refs(r):
    """Provisions that an amendment not in force touches: '248' for a body section, 'schi:9.3' for a schedule item,
    'schi' for the schedule itself, and 'end~start' for a range."""
    refs, inside = set(), set()
    for at in r.iter("AmendedText"):
        inside.update(id(x) for x in at.iter())
    pre = ""

    def add(n):
        refs.add(f"{pre}:{n}" if pre else n)

    for el in r.iter():
        if el.tag == "AmendedText":
            for sec in el.iter("Section"):
                lb = label_text(sec.find("Label"))
                if lb:
                    add(lb)
            continue
        if el.tag != "Text" or id(el) in inside:
            continue
        tx = re.sub(r"\s+", " ", "".join(el.itertext()))
        sm = SCHED_MENTION_RE.search(tx)
        pre = ("sch" + (sm.group(1) or "").lower()) if sm else ""
        if pre:
            refs.add(pre)
        for mm in REF_RE.finditer(tx):
            g = re.sub(r"\([^)]*\)", "", mm.group(1))
            for part in re.split(r"\s*(?:,|\band\b|\bor\b)\s*", g):
                rng = re.match(r"\s*(\d+(?:\.\d+)*)\s+to\s+(\d+(?:\.\d+)*)", part)
                if rng:
                    add(rng.group(1))
                    add(rng.group(2))
                    refs.add((f"{pre}:" if pre else "") + rng.group(2) + "~" + rng.group(1))   # a range: end~start
                else:
                    n = re.match(r"\s*(\d+(?:\.\d+)*)", part)
                    if n:
                        add(n.group(1))
    return refs


def fed_pending_hits(pending, key, clause=None, items_only=False):
    """Pending amendments that touch body section `key`, or schedule `key` (item `clause`).
    items_only: for an item, count only refs that name that item (not the schedule as a whole)."""
    target = f"{key}:{clause}" if clause else key
    tp, tt = key_tuple(target)
    out = []
    for p in pending:
        refs = p["refs"]
        hit = target in refs
        if key.startswith("sch"):
            hit = hit or (not clause and any(r == key or r.startswith(key + ":") for r in refs)) \
                or (clause and ((key in refs and not items_only) or any(r.startswith(f"{key}:{clause}.") for r in refs)))
        for r in refs:
            if "~" in r:
                end, start = r.split("~")
                pe, te = key_tuple(end)
                ts = sec_tuple(start)
                if pe == tp and ts and te and tt and ts <= tt <= te:
                    hit = True
        if hit:
            out.append(p)
    return out


def fed_sched_heading(s):
    """(label, originating reference, title) of a federal Schedule element."""
    sh = s.find("ScheduleFormHeading")
    lab = sh.find("Label") if sh is not None else None
    tt = sh.find("TitleText") if sh is not None else None
    orf = sh.find("OriginatingRef") if sh is not None else None
    return ("".join(lab.itertext()).strip() if lab is not None else "",
            " ".join("".join(orf.itertext()).split()) if orf is not None else "",
            "".join(tt.itertext()).strip() if tt is not None else "")


def fed_sched_key(label):
    """'SCHEDULE V' -> 'schv'; 'SCHEDULE 1' -> 'sch1'; 'SCHEDULE [to Part XXV]' -> 'schpartxxv'; 'SCHEDULE' -> 'sch'."""
    tp = re.match(r"(?i)^schedule\s*\[?\s*to\s+part\s+([^\]\s]+)", label)
    if tp:
        return "schpart" + tp.group(1).lower().rstrip(".")
    m = re.search(r"(?i)schedule\s*([0-9]+(?:\.[0-9]+)*|[IVXLCDM]+(?:\.[0-9]+)*|[A-Z](?:\.[0-9]+)*)?(?![\w.])", label)
    if m:
        return "sch" + (m.group(1) or "").lower()
    return "sch" + re.sub(r"[^\w.]+", "", label).lower().strip(".")


def is_def_provision(el):
    """A schedule Provision that is itself a definition: its text starts with the defined term."""
    t0 = el.find("Text") if el.tag == "Provision" else None
    if t0 is None or (t0.text or "").strip():
        return False
    first = next(iter(t0), None)
    return first is not None and first.tag == "DefinedTermEn"


def fed_sched_defs(sch):
    """(Part, item, Definition element) for every definition in a federal schedule."""
    out, st = [], {"part": None, "item": None}

    def walk(el):
        for ch in el:
            if ch.tag in ("GroupHeading", "Heading"):
                txt = " ".join(x.strip() for x in ch.itertext() if x.strip())
                lab = item_label(ch) or ""
                pm = re.match(r"(?i)^part\s+((?:[IVXLC]+|\d+)(?:\.\d+)*)(?![\w.])", lab or txt)
                if pm:
                    st["part"], st["item"] = pm.group(1).lower(), None
                else:
                    cl = label_clauses(lab or txt.split(" ")[0])
                    if cl:
                        st["item"] = cl[0]
                continue
            if ch.tag == "Definition" or is_def_provision(ch):
                out.append((st["part"], st["item"], ch))
                continue
            if ch.tag in ("Provision", "Section", "Item"):
                cl = label_clauses(item_label(ch))
                if cl:
                    st["item"] = cl[0]
            walk(ch)

    walk(sch)
    return out


def split_section_footnotes(sec):
    """Take out the footnotes that the section's own label points to (they concern the whole section), so that they
    can be shown with any subsection. Returns (a copy of the section without them, the footnotes)."""
    lab = sec.find("Label")
    refs = {f.get("idref") for f in lab.iter("FootnoteRef")} if lab is not None else set()
    if not refs:
        return sec, []
    sec = copy.deepcopy(sec)
    notes = []
    for parent in list(sec.iter()):
        i = 0
        while i < len(parent):
            c = parent[i]
            if c.tag != "Footnote" or c.get("id") not in refs:
                i += 1
                continue
            if c.tail:                           # keep any text that followed the footnote
                if i:
                    parent[i - 1].tail = (parent[i - 1].tail or "") + c.tail
                else:
                    parent.text = (parent.text or "") + c.tail
            parent.remove(c)
            c.tail = None
            notes.append(c)
    return sec, notes


def read_xml(xml_text):
    """Parse Justice Laws XML. Its files declare no DTD or entities, so any that do are refused (entity expansion)."""
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:
        raise LawError("Justice Laws returned XML with a DTD or entities, which the tool does not read.", 3)
    try:
        return ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise LawError(f"Justice Laws returned XML that could not be read ({e}).", 3) from None


def parse_justice(xml_text, today):
    root = read_xml(xml_text)
    idn = root.find("Identification")
    title = None
    if idn is not None:
        st = idn.find("ShortTitle")
        lt = idn.find("LongTitle")
        num = idn.find("InstrumentNumber")
        title = "".join((st if st is not None else lt).itertext()).strip() if (st is not None or lt is not None) else None
        if num is not None:
            title = f"{''.join(num.itertext()).strip()}, {title}"
    meta = {"title": title, "xml_current_date": root.get(LIMS + "current-date"), "pit_date": root.get(LIMS + "pit-date"),
            "in_force_attr": root.get("in-force"), "notes": []}
    units, pending, related = [], [], []
    body = root.find("Body")
    ctx = []
    if body is not None:
        for e in body:
            if e.tag == "Heading":
                txt = " ".join(x.strip() for x in e.itertext() if x.strip())
                lvl = e.get("level", "1")
                ctx = [txt] if lvl == "1" else (ctx[:int(lvl) - 1] + [txt])
            elif e.tag == "Section":
                key = label_text(e.find("Label")) or None
                rm = re.match(r"^(\d+(?:\.\d+)*)\s+(?:to|and)\s+(\d+(?:\.\d+)*)$", key or "")
                if rm:                   # "594 to 596" or "157 and 158": one Section element for several numbers
                    key = rm.group(1)
                    meta.setdefault("ranges", []).append((rm.group(1), rm.group(2)))
                mn = e.find("MarginalNote")
                head = "".join(mn.itertext()).strip() if mn is not None else ""
                e, secnotes = split_section_footnotes(e)
                body_el = ET.Element("Section")
                for c in e:
                    if c.tag != "MarginalNote":
                        body_el.append(c)
                nif = any(x.get("in-force") == "no" for x in e.iter())
                for x in e.iter():
                    d = parse_date(x.get(LIMS + "inforce-start-date") or "")
                    if d and d > today:
                        nif = True
                if head:
                    units.append({"key": key, "kind": "headnote", "text": head, "nif": nif, "ctx": list(ctx)})
                for ln in render_lines(body_el):
                    kind = "history" if ln.startswith("History:") else ("headnote" if ln.startswith(MN) else "text")
                    units.append({"key": key, "kind": kind, "text": ln.replace(MN, ""), "nif": nif, "ctx": list(ctx)})
                for fn in secnotes:              # a footnote on the whole section, such as its in-force note
                    for ln in render_lines(fn):
                        units.append({"key": key, "kind": "text", "text": ln, "nif": nif, "ctx": list(ctx), "secnote": True})
    if not units:                    # an order with no numbered sections (such as SI/97-5): its text is the "order" unit
        od = root.find("Order")
        for p in (od.findall("Provision") if od is not None else []):
            if p.find("Heading") is not None:
                continue
            nif = p.get("in-force") == "no" or (parse_date(p.get(LIMS + "inforce-start-date") or "") or today) > today
            for ln in render_lines(p):
                units.append({"key": "order", "kind": "text", "text": ln.replace(MN, ""), "nif": nif, "ctx": []})
    for s in root.findall("Schedule"):
        label, oref, ttl = fed_sched_heading(s)
        if ttl.upper() in ("AMENDMENTS NOT IN FORCE", "RELATED PROVISIONS"):
            dest = pending if ttl.upper().startswith("AMENDMENTS") else related
            for r in s.iter("RelatedOrNotInForce"):
                h = r.find("Heading")
                head = " ".join(x.strip() for x in h.itertext() if x.strip()) if h is not None else ""
                txt = " ".join(render_lines(r)).replace(MN, "")
                dest.append({"heading": head, "text": txt, "refs": sorted(amendment_refs(r))})
            continue
        key = fed_sched_key(label)
        heading = " ".join(x for x in (label, oref, ttl) if x)
        if key in meta.setdefault("sched_keys", []):
            meta.setdefault("sched_collisions", []).append(key)
        meta["sched_keys"].append(key)
        sunits = parse_fed_schedule(s, key, heading, today)
        units.append({"key": key, "kind": "heading", "text": heading, "nif": fed_nif(s, today), "ctx": [heading], "clause": None})
        units.extend(sunits)
        src_len = len(re.sub(r"\s+", "", "".join(x for c in s if c.tag != "ScheduleFormHeading" for x in c.itertext())))
        out_len = len(re.sub(r"\s+|History:", "", "".join(u["text"] for u in sunits)))
        if src_len and out_len < 0.97 * src_len:
            meta.setdefault("sched_short", {})[key] = (out_len, src_len)
        continue

    return meta, units, pending, related


def justice_currency(kind, code, fresh=False):
    url = f"https://laws-lois.justice.gc.ca/eng/{'acts' if kind == 'act' else 'regulations'}/{code}/index.html"
    try:
        t, _, _ = fetch(url, fresh, kind="html")
    except LawError:
        return None
    return justice_currency_line(t)


def justice_currency_line(html_text):
    """'Act current to 2026-10-29 and last amended on 2026-01-01' from a Justice Laws index page. Some laws (orders
    such as SI/97-5) show only 'Regulations are current to 2026-10-29'."""
    s = clean(re.sub(r"<(script|style)\b.*?</\1>", "", html_text, flags=re.S))
    m = re.search(r"(Act|Regulations?)\s+(?:is\s+|are\s+)?current to (\d{4}-\d{2}-\d{2})"
                  r"(?:\s*and last amended on (\d{4}-\d{2}-\d{2}))?", s)
    if not m:
        return None
    return f"{m.group(1)} current to {m.group(2)}" + (f" and last amended on {m.group(3)}" if m.group(3) else "")


# ---------------------------------------------------------------- lookup
def section_notes(units, key, rows, q=None):
    """Notes of section `key` that concern the whole section (repeals, added Parts or sections) or the term q,
    marked as coming from elsewhere in the section. Notes about other definitions are left out."""
    num = re.escape(key.split(":")[-1])
    out = []
    for v in units:
        if v["key"] != key or v["kind"] != "note" or any(v is r for r in rows) or not PENDING_NOTE_RE.search(v["text"]):
            continue
        names_q = bool(q) and note_names_term(v["text"], q)
        if q and not names_q and (ADD_DEF_RE.search(v["text"]) or re.search(r'definitions? of "', norm(v["text"]))):
            continue                                   # it adds or changes another definition
        if names_q or v.get("carried") or ADD_NOTE_RE.search(v["text"]) \
                or re.search(r"(?i)\bsections?\s+" + num + r"(?!\s*\(|\.\d|\d)", v["text"]):
            out.append(dict(v, elsewhere=True))
    return out


def note_names_term(text, q):
    return f'definition of "{q}"' in norm(text) or f"definitions of \"{q}\"" in norm(text)


def def_lead_note(units, i):
    """The note that adds definition i, when e-Laws prints it just above (or above a run of new definitions)."""
    u, j = units[i], i - 1
    while j >= 0 and units[j]["key"] == u["key"]:
        v = units[j]
        if v["kind"] == "note":
            m = ADD_DEF_RE.search(v["text"])
            if m and u["nif"] and (j == i - 1 or m.group(0).lower().endswith("definitions")):
                return v
            return None
        if not v["nif"] or v["kind"] != "text":
            return None
        j -= 1
    return None


def section_rows(units, key):
    if re.match(r"^sch[^:]*$", key):                 # a whole schedule: its heading and every item in it
        return [u for u in units if u["key"] == key or (u["key"] or "").startswith(key + ":")]
    return [u for u in units if u["key"] == key]


def note_skips_subsection(text, num, sub):
    """True when a note names only other subsections of section num (so it says nothing about subsection sub)."""
    if not num:
        return False
    n = re.escape(num.split(":")[-1])
    if re.search(r"(?i)\bsections?\s+" + n + r"(?!\s*\(|\.\d|\d)", text):
        return False                                   # about the whole section
    found = False
    for m in re.finditer(r"(?i)\bsubsections?\s+" + n + r"\s*((?:\(\s*[\w.]+\s*\)\s*(?:,|and|or|to)?\s*)+)", text):
        labels = re.findall(r"\(\s*([\w.]+)\s*\)", m.group(1))
        found = True
        if sub in labels:
            return False
        if re.search(r"\bto\b", m.group(1)) and len(labels) >= 2 and sec_tuple(labels[0]) and sec_tuple(labels[-1]) \
                and sec_tuple(sub) and sec_tuple(labels[0]) <= sec_tuple(sub) <= sec_tuple(labels[-1]):
            return False
    return found


def subsection_rows(rows, sub, num=None):
    """Keep the headnote, the rows of subsection (sub), the history lines, and the section's notes that may concern it."""
    out, cur, pending_head = [], None, []
    skipped, live_since = None, False        # last note dropped as naming another subsection; in-force text after it
    for u in rows:
        m = SUB_RE.match(u["text"])
        if u["kind"] == "headnote":
            pending_head.append(u)
            continue
        if u.get("secnote"):
            out.append(u)                       # a footnote on the whole section, such as its in-force note
            pending_head = []
            continue
        if m and (u.get("base") in (None, "section", "subsection")):
            cur = m.group(1)
        if cur == sub or u["kind"] == "history":
            if skipped is not None and u["kind"] != "history" and u.get("nif") and not live_since:
                out.append(dict(skipped, elsewhere=True))     # the note whose new text brings this subsection in
                skipped = None
            out.extend(pending_head)
            out.append(u)
        elif u["kind"] == "note":
            if note_skips_subsection(u["text"], num, sub):
                skipped, live_since = u, False
            else:
                out.append(dict(u, elsewhere=True))   # a note on the whole section, or one that may touch this part
        if u["kind"] == "text" and not u.get("nif") and skipped is not None:
            live_since = True
        pending_head = []
    if not any(u["kind"] not in ("history", "headnote") and not u.get("elsewhere") and not u.get("secnote") for u in out):
        return []                       # the subsection itself was not found (history lines and notes do not count)
    return out


def lookup(src, code, targets=(), find=None, define=None, status_only=False, history=False,
           fresh=False, allow_source=False, orders=True):
    """Look up a law. Returns a result dict (see format_text). Raises LawError."""
    for v, what in ((find, "phrase"), (define, "term")):
        if v is not None and len(v) > 500:
            raise LawError(f"The {what} is too long (over 500 characters). Use a shorter one.", 2)
    outer = _BUDGET["until"] is None
    if outer:
        _BUDGET["until"] = time.time() + LOOKUP_BUDGET
    try:
        res = _lookup(src, code, targets, find, define, status_only, history, fresh, allow_source)
        if orders and src == "on":
            attach_orders(res, res.get("enacted_by"), res.get("currency_date"), fresh or not res.get("page_from_cache"))
        return res
    finally:
        if outer:
            _BUDGET["until"] = None


def _lookup(src, code, targets=(), find=None, define=None, status_only=False, history=False,
            fresh=False, allow_source=False):
    today = datetime.date.today()
    res = {"source": src, "code": code, "tool_version": VERSION, "retrieved": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "flags": [], "sections": [], "matches": None, "definitions": None, "pending_amendments": []}
    if src == "on":
        kind, slug = on_code(code)
        source_law = bool(re.match(r"(?i)^[sr]\d{5}$", slug))
        url = f"https://www.ontario.ca/laws/{kind}/{urllib.parse.quote(slug)}"
        t, at, cached, cur = fetch_paired(url, "html", lambda: list(elaws_currency(True)), fresh)
        try:
            meta, units = parse_elaws(t, source_law)
        except LawError:
            if not cached:
                raise
            t, at, cached, cur = fetch_paired(url, "html", lambda: list(elaws_currency(True)), True)   # the cached copy was bad
            meta, units = parse_elaws(t, source_law)
        status, flags = elaws_status(meta, today)
        cur_date, cur_where = (cur if isinstance(cur, list) and len(cur) == 2 else (None, None))
        em = re.search(r'class="chapter[^"]*"[^>]*>\s*<a href="[^"]*/laws/statute/([sS]\d{5})\b', t)
        res.update({"url": url, "title": meta["title"], "under": meta["under"], "status": status,
                    "consolidation": meta["consolidation"], "last_amendment": meta["last_amendment"],
                    "header_notes": meta["notes"], "currency_date": cur_date, "page_from_cache": cached,
                    "enacted_by": em.group(1).lower() if em else None,
                    "currency": f"e-Laws currency date: {cur_date} ({cur_where})" if cur_date else None})
        res["flags"].extend(flags)
        cd = parse_date(cur_date or "")
        fm = re.search(r"From ([A-Z][a-z]+ \d{1,2}, \d{4})", meta["consolidation"] or "")
        fd = parse_date(fm.group(1)) if fm else None
        if cd and fd and fd > cd:
            res["currency"] += f"; this law's consolidation starts {fm.group(1)}, so it is current to that date"
        if cd and (today - cd).days > 21:
            flag(res["flags"], "CHECK", "currency", f"The e-Laws currency date is {(today - cd).days} days old. Very recent changes may be missing.")
        if not cur_date:
            flag(res["flags"], "CHECK", "currency", "Could not read the e-Laws currency date. See https://www.ontario.ca/laws/about-e-laws.")
        if source_law:
            res["source_law"] = True
            if res.get("currency"):
                res["currency"] += " (this applies to the consolidated laws, not to this as-enacted text)"
            res["status"] = "AS-ENACTED SOURCE LAW (not a consolidated, current version)"
            flag(res["flags"], "CHECK", "whole law", "This is source law as passed, not the current consolidated law. "
                 "Do not quote it as current law; use the consolidated code instead.")
            if (targets or find or define) and not allow_source:
                raise LawError("This code is as-enacted source law. Use the consolidated code, or add --source to read it anyway.", 2)
    elif src == "ca":
        kind, c = ca_code(code)
        url = f"https://laws-lois.justice.gc.ca/eng/XML/{urllib.parse.quote(c)}.xml"
        t, at, cached, cur = fetch_paired(url, "xml", lambda: justice_currency(kind, c, True), fresh)
        meta, units, pending, related = parse_justice(t, today)
        status = "IN FORCE" if meta["in_force_attr"] != "no" else "NOT IN FORCE"
        res.update({"url": url, "title": meta["title"], "under": None, "status": status,
                    "consolidation": None, "last_amendment": None, "header_notes": [],
                    "currency": cur})
        cm = re.search(r"current to (\d{4}-\d{2}-\d{2})", cur or "")
        if cm and (today - parse_date(cm.group(1))).days > 45:
            flag(res["flags"], "CHECK", "currency", f"Justice Laws says current to {cm.group(1)}. Changes after that date may be missing.")
        if not cur:
            flag(res["flags"], "CHECK", "currency", f"Could not read the 'current to' line from the Justice Laws page. "
                 f"The XML file date is {meta['xml_current_date']}, which can lag the published currency date.")
        if meta["in_force_attr"] == "no":
            flag(res["flags"], "NOT_IN_FORCE", "whole law", "Justice Laws marks this law as not in force.")
        lam = re.search(r"last amended on (\d{4}-\d{2}-\d{2})", cur or "")
        pit = parse_date(meta.get("pit_date") or "")
        if lam and pit and pit < parse_date(lam.group(1)):
            flag(res["flags"], "CHECK", "currency", f"The XML file is dated {meta['pit_date']}, older than the published "
                 f"last amendment ({lam.group(1)}). Re-run with --fresh, or read the Justice Laws page.")
        res["pending_amendments"] = [{"heading": p["heading"], "refs": p["refs"]} for p in pending]
    else:
        raise LawError("Source must be 'on' (Ontario) or 'ca' (federal).", 2)
    res["fetched"] = datetime.datetime.fromtimestamp(at).strftime("%Y-%m-%d %H:%M") + (" (cached copy)" if cached else "")
    if status_only:
        return res

    # --find
    if find:
        q = norm(find)
        hits, order = {}, []
        for u in units:
            if u["key"] is None or u["kind"] == "history":
                continue
            tx = norm(u["text"])
            i = tx.find(q)
            if i < 0:
                continue
            k = u["key"] + ((":" + u["part"] + ":" if u.get("part") else ":") + u["clause"]
                            if src == "ca" and u.get("clause") and u["key"].startswith("sch") else "")
            body = u["kind"] != "note"
            if k not in hits:
                order.append(k)
                hits[k] = {"section": k, "snippet": u["text"][max(0, i - 70): i + len(q) + 70],
                           "not_in_force": True, "in_force_body": False, "in_note": False}
            h = hits[k]
            if body and not u["nif"]:
                if not h["in_force_body"]:          # show the in-force wording when there is one
                    h["snippet"] = u["text"][max(0, i - 70): i + len(q) + 70]
                h["in_force_body"], h["not_in_force"] = True, False
            if not body:
                h["in_note"] = True
            elif u["nif"]:
                h["nif_body"] = True
        for h in hits.values():
            h["only_in_note"] = h["in_note"] and not h["in_force_body"] and not h.pop("nif_body", False)
            if h["only_in_note"]:
                h["not_in_force"] = False
        res["matches"] = [hits[k] for k in order]
        if not res["matches"] and src == "ca":
            for p in pending:
                tx = norm(p["text"])
                i = tx.find(q)
                if i >= 0:
                    res["matches"].append({"section": f"(amendment not in force: {p['heading'].lstrip('— ').strip()})",
                                           "snippet": p["text"][max(0, i - 70): i + len(q) + 70], "not_in_force": True,
                                           "in_force_body": False, "in_note": False, "only_in_note": False})
        for h in res["matches"]:
            if h["not_in_force"]:
                flag(res["flags"], "NOT_IN_FORCE", h['section'] if h['section'].startswith('(') else cite(h['section']),
                     "Match is only in text that is not yet in force.")
            elif h.get("only_in_note"):
                flag(res["flags"], "CHECK", h['section'] if h['section'].startswith('(') else cite(h['section']),
                     "Match is only in an editorial note (usually about a "
                     "future change), not in the law's current wording.")
        return res

    # --define
    if define:
        q = norm(define).strip('"')
        out = []
        if src == "on":
            for i, u in enumerate(units):
                if u["base"] not in ON_DEF:
                    continue
                tx = norm(u["text"])
                if q not in def_terms(tx):
                    continue
                rows = []
                lead = def_lead_note(units, i)
                if lead is not None:
                    rows.append(lead)
                rows.append(u)
                for j in range(i + 1, len(units)):
                    v = units[j]
                    if v["key"] != u["key"] or v["kind"] in ("headnote", "heading", "history") \
                            or v["base"] in ("section", "subsection", "schedule"):
                        break
                    if v["base"] in ON_DEF and re.match(r"""["']""", norm(v["text"])):
                        break                            # the next defined term
                    if v["kind"] == "note" and not note_names_term(v["text"], q) and ADD_DEF_RE.search(v["text"]):
                        break                            # it introduces the next definition
                    rows.append(v)
                out.append({"section": u["key"], "rows": rows + section_notes(units, u["key"], rows, q),
                            "history": [v["text"] for v in units if v["key"] == u["key"] and v["kind"] == "history"]})
            pat = re.compile(r'"' + re.escape(q) + r'"\s*(?:,[^,]*,\s*)?(?:means|includes|has the same meaning)')
            have = {d["section"] for d in out}
            for i, u in enumerate(units):       # provisions that give the term a meaning of their own
                if not u["key"] or u["key"] in have or u["kind"] != "text" or u["base"] in ON_DEF \
                        or not pat.search(norm(u["text"])):
                    continue
                rows = [u]
                for v in units[i + 1:]:
                    if v["key"] != u["key"] or v["kind"] in ("headnote", "heading", "history", "note") \
                            or v["base"] in ("section", "subsection", "schedule"):
                        break
                    rows.append(v)
                out.append({"section": u["key"], "rows": rows + section_notes(units, u["key"], rows, q), "own_meaning": True,
                            "history": [v["text"] for v in units if v["key"] == u["key"] and v["kind"] == "history"]})
                have.add(u["key"])
        else:
            root = read_xml(t)
            body = root.find("Body")
            for sec in (body.iter("Section") if body is not None else []):
                lab = sec.find("Label")
                for d in sec.iter("Definition"):
                    terms = [norm("".join(x.itertext())) for x in d.iter("DefinedTermEn")]
                    if q in terms:
                        nif = fed_nif(d, today)
                        out.append({"section": label_text(lab) or "?",
                                    "rows": [{"text": ln.replace(MN, ""), "nif": nif, "kind": "text"} for ln in render_lines(d)]})
            for sch in root.findall("Schedule"):
                label, _, ttl = fed_sched_heading(sch)
                if ttl.upper() in ("AMENDMENTS NOT IN FORCE", "RELATED PROVISIONS"):
                    continue
                sk = fed_sched_key(label)
                for part, item, d in fed_sched_defs(sch):
                    if q in [norm("".join(x.itertext())) for x in d.iter("DefinedTermEn")]:
                        nif = fed_nif(d, today)
                        out.append({"section": sk + (f":{part}:" if part else (":" if item else "")) + (item or ""),
                                    "fed_sched": (sk, item),
                                    "rows": [{"text": ln.replace(MN, ""), "nif": nif, "kind": "text"} for ln in render_lines(d)]})
            for d in out:
                if d["rows"] and re.match(re.escape(q) + r"\s*\[(?:repealed|revoked)\b", norm(d["rows"][0]["text"])):
                    d["repealed"] = True
            if not out:
                defines = re.compile(re.escape(q) + r"\s+(?:means|includes|has the (?:same )?meaning)\b")
                mentions = []
                for p in pending:
                    tx = norm(p["text"])
                    m = defines.search(tx)
                    if m:
                        res["pending_only"] = True
                        i = max(0, m.start() - 60)
                        flag(res["flags"], "NOT_IN_FORCE", "definition", f'"{define}" is defined only in an amendment not yet in '
                             f"force ({p['heading'].lstrip('— ').strip()}): ..." + p["text"][i:i + 300] + "...")
                    elif q in tx:
                        mentions.append(p["heading"].lstrip("— ").strip())
                if mentions:
                    flag(res["flags"], "CHECK", "definition", f'"{define}" also appears in amendments not yet in force: '
                         + "; ".join(mentions[:6]) + ("; ..." if len(mentions) > 6 else "") + ".")
            for d in out:
                named, other = [], 0
                sk_it = d.get("fed_sched")
                for p in (fed_pending_hits(pending, sk_it[0], sk_it[1]) if sk_it else fed_pending_hits(pending, d["section"])):
                    if q in norm(p["text"]):
                        named.append(p)
                    else:
                        other += 1
                for p in named:
                    flag(res["flags"], "PENDING", cite(d['section']), f"An amendment not yet in force names this term "
                         f"({p['heading']}): " + p["text"][:300] + ("..." if len(p["text"]) > 300 else ""))
                if other:
                    flag(res["flags"], "CHECK", cite(d['section']), f"{other} other amendment(s) not yet in force change "
                         f"{cite(d['section'])} without naming this term. Look up {cite(d['section'])} to see them.")
        res["definitions"] = out
        counts = {}
        for d in out:
            counts[d["section"]] = counts.get(d["section"], 0) + 1
        seen = {}
        for d in out:
            seen[d["section"]] = seen.get(d["section"], 0) + 1
            d["label"] = cite(d['section']) + (f" (entry {seen[d['section']]} of {counts[d['section']]})"
                                                 if counts[d["section"]] > 1 else "")
            where = d["label"]
            body = [r for r in d["rows"] if r.get("kind") != "note"]
            if d.get("repealed"):
                d["only_pending"] = True
                flag(res["flags"], "REPEALED", where, "This definition is repealed. It is not current law.")
            elif body and all(r.get("nif") for r in body):
                d["only_pending"] = True
                flag(res["flags"], "NOT_IN_FORCE", where, "This whole definition is not yet in force. It is not current law.")
            elif any(r.get("nif") for r in body):
                flag(res["flags"], "NOT_IN_FORCE", where, "Part of this definition is not yet in force (marked).")
            if d.get("own_meaning"):
                flag(res["flags"], "CHECK", where, "This provision gives the term its own meaning for part of the law; "
                     "check which provisions it covers.")
            for hx in d.get("history") or []:
                history_flags(res["flags"], where, hx, today, prefix="Elsewhere in this section: ")
            for r in d["rows"]:
                if r.get("kind") == "note" and PENDING_NOTE_RE.search(r["text"]):
                    flag(res["flags"], "PENDING", where, ("Elsewhere in this section: " if r.get("elsewhere") else "") + r["text"])
                    contingent_check(res["flags"], where, r["text"])
        if not out and not res.get("pending_only"):
            flag(res["flags"], "CHECK", "definition", f'No definition of "{define}" found. Try --find.')
        return res

    # sections
    for tgt in targets:
        key, sub = parse_target(tgt)
        clause = part = None
        merged_parts = []
        if key.startswith("sch") and (":" not in key or key.endswith(":")) and "(" in tgt:
            flag(res["flags"], "CHECK", cite(tgt), "The part in brackets was ignored: a whole schedule or Part has no "
                 "subsections. Give an item number, such as sch1:2(1).")
        if src == "ca" and key.startswith("sch") and ":" in key:
            key, clause = key.split(":", 1)
            if ":" in clause:
                part, clause = clause.split(":", 1)
        if src == "ca" and key in (meta.get("sched_collisions") or []):
            flag(res["flags"], "CHECK", cite(tgt), "Two schedules of this law have labels that give the same short name, "
                 "so this lookup may mix them. Check the schedule labels on Justice Laws.")
        if src == "ca" and key in (meta.get("sched_short") or {}) and not clause:
            o, n = meta["sched_short"][key]
            flag(res["flags"], "CHECK", cite(tgt), f"Only {o} of {n} characters of this schedule could be laid out. "
                 "Some of it may be missing below; read it on Justice Laws.")
        rows = section_rows(units, key)
        if not rows and src == "on" and key in (meta.get("aliases") or {}):
            ak = meta["aliases"][key]
            rows = section_rows(units, ak)
            if rows:
                flag(res["flags"], "CHECK", cite(tgt), f"Shown under {cite(ak)}, which e-Laws prints together with this "
                     f"section (one line for several section numbers).")
        if not rows and key_tuple(key)[1]:
            pk, tk = key_tuple(key)
            for a, b in meta.get("ranges", []):
                (pa, ta), (pb, tb) = key_tuple(a), key_tuple(b)
                if src == "ca" and ta and tb and tk:   # federal numbers sort as decimals: 70.61 < 70.7 < 70.8
                    dk = lambda n: (int(n.partition(".")[0]), n.partition(".")[2])
                    ta, tb, tk = dk(a), dk(b), dk(key)
                if pa == pk == pb and ta and tb and ta <= tk <= tb:
                    rows = section_rows(units, a)
                    flag(res["flags"], "CHECK", cite(tgt), f"Shown under the range {a}-{b}, which covers this section.")
                    break
        if clause or part:
            def in_item(u):
                if not clause:
                    return True
                if any(c and (c == clause or c.startswith(clause + ".")) for c in [u.get("clause")] + list(u.get("clause_also") or [])):
                    return True
                rg = u.get("clause_range")
                return bool(rg and sec_tuple(rg[0]) and sec_tuple(rg[1]) and sec_tuple(clause)
                            and sec_tuple(rg[0]) <= sec_tuple(clause) <= sec_tuple(rg[1]))
            rows = [u for u in rows if in_item(u) and (part is None or u.get("part") == part)]
            if clause and any(u.get("clause_range") and u.get("clause") != clause for u in rows if u["kind"] == "text"):
                rg = next(u["clause_range"] for u in rows if u.get("clause_range"))
                flag(res["flags"], "CHECK", cite(tgt), f"Shown under the range {rg[0]} to {rg[1]}, which covers this item.")
            parts = []
            for u in rows:
                if u.get("part") not in parts:
                    parts.append(u.get("part"))
            if clause and part is None and len([x for x in parts if x]) > 1:
                flag(res["flags"], "CHECK", cite(tgt), f"This schedule numbers its items separately in each Part, so items "
                     f"numbered {clause} from Parts {', '.join(x.upper() for x in parts if x)} are all shown. To pick one, "
                     f"add the Part, for example {tgt.split(':')[0]}:{parts[-1].upper()}:{clause}.")
                merged, last = [], object()
                merged_parts = [x for x in parts if x]
                for u in rows:
                    if u.get("part") != last:
                        last = u.get("part")
                        merged.append({"key": key, "kind": "heading", "text": (u.get("ctx") or [""])[-1], "nif": False,
                                       "ctx": u.get("ctx"), "clause": clause})
                    merged.append(u)
                rows = merged
        entry = {"target": tgt, "found": bool(rows), "context": [], "headnote": None, "rows": [], "history": []}
        if not rows:
            inpending = [p for p in (pending if src == "ca" else []) if (f"{key}:{clause}" if clause else key) in p["refs"]]
            if inpending:
                entry["only_pending"] = True
                flag(res["flags"], "NOT_IN_FORCE", cite(tgt), "This section exists only in AMENDMENTS NOT IN FORCE ("
                     + "; ".join(p["heading"] for p in inpending) + "). It is not current law.")
            else:
                flag(res["flags"], "CHECK", cite(tgt), "Not found in this law. Check the number, or search with --find.")
            res["sections"].append(entry)
            continue
        if sub:
            sr = subsection_rows(rows, sub, key)
            if sr:
                rows = sr
            else:
                flag(res["flags"], "CHECK", cite(tgt), f"Subsection ({sub}) was not found in this section; showing the whole "
                     f"section. Check the subsection number.")
        entry["context"] = next((u.get("ctx") for u in rows if u["kind"] == "text"), rows[0].get("ctx")) or []
        for u in rows:
            if u["kind"] == "history":
                entry["history"].append(u["text"])
                history_flags(res["flags"], cite(tgt), u["text"], today)
                continue
            if u["kind"] == "heading" and u.get("pre") and not key.startswith("sch"):
                continue
            if u["kind"] == "headnote" and not any(r["kind"] == "text" for r in entry["rows"]) \
                    and not re.match(r"^sch[^:]*$", key):
                entry["headnote"] = u["text"]      # the last headnote before the text is the section's own
                continue
            entry["rows"].append({"text": u["text"], "nif": u["nif"], "kind": u["kind"], "row": u.get("row"), "base": u.get("base"),
                                  "elsewhere": bool(u.get("elsewhere"))})
            if u.get("unparsed"):
                flag(res["flags"], "CHECK", cite(tgt), f"The section number in \"{u['text'][:60]}\" could not be read "
                     f"cleanly. Compare this text with the official page before relying on it.")
            if u.get("unknown_class"):
                flag(res["flags"], "CHECK", cite(tgt), f"e-Laws used a layout this tool does not know "
                     f"({u['unknown_class']}). The words are shown, but their place in the section may be wrong. "
                     f"Compare with the official page.")
            if u["kind"] == "note" and PENDING_NOTE_RE.search(u["text"]):
                flag(res["flags"], "PENDING", cite(tgt), ("Elsewhere in this section: " if u.get("elsewhere") else "") + u["text"])
                contingent_check(res["flags"], cite(tgt), u["text"])
        first = next((r["text"] for r in entry["rows"] if r["kind"] == "text"), "")
        if merged_parts:                      # one item number in several Parts: judge each Part on its own
            firsts, seen = [], False
            for r in entry["rows"]:
                if r["kind"] == "heading":
                    seen = False
                elif r["kind"] == "text" and not seen:
                    firsts.append(r["text"])
                    seen = True
            gone = [merged_parts[i].upper() for i, x in enumerate(firsts[:len(merged_parts)]) if REPEALED_RE.match(x)]
            if gone and len(gone) == len(merged_parts):
                entry["only_pending"] = True
                flag(res["flags"], "REPEALED", cite(tgt), "In every Part shown, this item is repealed. It is not current law.")
            elif gone:
                flag(res["flags"], "CHECK", cite(tgt), f"In Part {', '.join(gone)} this item is repealed; in the other Parts "
                     f"shown it is not. Make sure you are reading the right Part.")
        elif key.startswith("sch") and not clause and (src == "ca" or ":" not in key):
            firsts = {}                       # a whole schedule or Part: repealed only if every item in it is
            for u in rows:
                if u["kind"] == "text":
                    firsts.setdefault((u.get("key"), u.get("part"), u.get("clause")), u["text"])
            gone = [x for x in firsts.values() if REPEALED_RE.match(x)]
            if firsts and len(gone) == len(firsts):
                entry["only_pending"] = True
                flag(res["flags"], "REPEALED", cite(tgt), f"Everything in this schedule is repealed or revoked (\"{first[:90]}\"). "
                     f"It is not current law.")
            elif gone:
                flag(res["flags"], "CHECK", cite(tgt), f"{len(gone)} of the {len(firsts)} items shown "
                     f"{'is' if len(gone) == 1 else 'are'} repealed or revoked (their text says so); the others are not.")
        elif REPEALED_RE.match(first):
            starts = [first] + [r["text"] for r in entry["rows"] if r["kind"] == "text" and r["text"] != first
                                and SUB_RE.match(r["text"])]
            if sub or not SUB_RE.match(first) or all(REPEALED_RE.match(x) for x in starts):
                entry["only_pending"] = True
                flag(res["flags"], "REPEALED", cite(tgt), f"The text shown says this provision is repealed or revoked "
                     f"(\"{first[:90]}\"). It is not current law.")
            else:
                gone = ["(" + SUB_RE.match(x).group(1) + ")" for x in starts if REPEALED_RE.match(x) and SUB_RE.match(x)] or ["(1)"]
                names = gone[0] if len(gone) == 1 else ", ".join(gone[:-1]) + " and " + gone[-1]
                flag(res["flags"], "CHECK", cite(tgt), f"Subsection{'s' if len(gone) > 1 else ''} {names} of this section "
                     f"{'are' if len(gone) > 1 else 'is'} repealed or revoked; the other subsections shown are not.")
        if any(c.startswith("[NOT YET IN FORCE]") for c in entry["context"]) and not all(
                r["nif"] for r in entry["rows"] if r["kind"] == "text"):
            flag(res["flags"], "CHECK", cite(tgt), "A heading above this text is not yet in force (marked in the context line).")
        if entry.get("only_pending"):
            pass
        elif entry["rows"] and all(r["nif"] for r in entry["rows"] if r["kind"] != "note"):
            entry["only_pending"] = True
            flag(res["flags"], "NOT_IN_FORCE", cite(tgt), "All text shown for this section is not yet in force.")
        elif any(r["nif"] for r in entry["rows"]):
            flag(res["flags"], "NOT_IN_FORCE", cite(tgt), "Some text shown is not yet in force (marked [NOT YET IN FORCE]). Unmarked text is the current law.")
        if src == "ca":
            base = key
            for p in fed_pending_hits(pending, key, clause):
                if clause and key.startswith("sch") and not fed_pending_hits([p], key, clause, items_only=True):
                    flag(res["flags"], "CHECK", cite(tgt), f"An amendment not yet in force changes this schedule "
                         f"({p['heading']}); it does not name item {clause}, but read it: " + p["text"][:200]
                         + ("..." if len(p["text"]) > 200 else ""))
                    continue
                flag(res["flags"], "PENDING", cite(tgt), f"An amendment not yet in force mentions this "
                     f"{'schedule' if key.startswith('sch') else 'section'} ({p['heading']}): "
                     + p["text"][:300] + ("..." if len(p["text"]) > 300 else ""))
            for p in related:
                if base in p["refs"]:
                    flag(res["flags"], "CHECK", cite(tgt), f"A related (transitional) provision mentions this section ({p['heading']}).")
        res["sections"].append(entry)
    return res


# ---------------------------------------------------------------- output
def fmt_rows(rows):
    out, last_row = [], None
    for r in rows:
        txt = ("[NOT YET IN FORCE] " if r.get("nif") else "") + ("[ELSEWHERE IN THIS SECTION] " if r.get("elsewhere") else "") + r["text"]
        if r.get("base") == "table" and r.get("row") is not None and r.get("row") == last_row and out:
            out[-1] += " | " + r["text"]
            continue
        out.append(txt)
        last_row = r.get("row") if r.get("base") == "table" else None
    return out


def crown_notice(res):
    """The acknowledgment Ontario asks for when its laws are reproduced (ontario.ca/page/copyright-information):
    '© King's Printer for Ontario, <year>'. The year is that of the consolidation shown, else the law's own year."""
    if res.get("source") != "on":
        return None          # federal law: the Reproduction of Federal Law Order (SI/97-5) asks for no notice
    m = re.search(r"From \w+ \d{1,2}, (\d{4})", res.get("consolidation") or "") \
        or re.search(r"\b(?:S\.O\.|R\.S\.O\.)\s*(\d{4})|O\. Reg\. \d+/(\d{2})\b", res.get("title") or "")
    year = None
    if m:
        year = next(g for g in m.groups() if g)
        if len(year) == 2:
            year = ("19" if int(year) > 50 else "20") + year
    return "Ontario law text © King's Printer for Ontario" + (f", {year}." if year else ".")


def format_text(res, history=False):
    L = [f"SOURCE: {res['url']}", f"TITLE: {res.get('title')}"]
    if res.get("under"):
        L.append(f"UNDER: {res['under']}")
    L.append(f"STATUS: {res['status']}")
    if res.get("consolidation"):
        L.append("CONSOLIDATION: " + re.sub(r"^Consolidation Period:\s*", "", res["consolidation"]))
    if res.get("currency"):
        L.append(f"CURRENCY: {res['currency']}")
    if res.get("last_amendment"):
        L.append("LAST AMENDMENT: " + re.sub(r"^Last amendment:\s*", "", res["last_amendment"]))
    for n in res.get("header_notes") or []:
        L.append(f"NOTE: {n}")
    if res.get("pending_amendments"):
        heads = []
        for p in res["pending_amendments"]:
            h = p["heading"].lstrip("— ").strip()
            if h not in heads:
                heads.append(h)
        L.append("AMENDMENTS NOT IN FORCE on this Act's page: " + "; ".join(heads))
    L.append(f"FETCHED: {res['fetched']}  (tool {res['tool_version']})")
    L.append("-" * 70)
    if res.get("matches") is not None:
        if not res["matches"]:
            L.append("No sections contain that phrase.")
        for h in res["matches"]:
            tag = (" [only in text NOT YET IN FORCE]" if h["not_in_force"] else " [only in a note]" if h.get("only_in_note")
                   else " [also in a note]" if h["in_note"] else "")
            L.append(f"{h['section'] if h['section'].startswith('(') else cite(h['section'])}{tag}: ...{h['snippet']}...")
    elif res.get("definitions") is not None:
        for d in res["definitions"]:
            L.append(f"== {'provision giving the term its own meaning' if d.get('own_meaning') else 'definition'} in "
                     f"{d.get('label') or 's. ' + d['section']} ==")
            L.extend(fmt_rows(d["rows"]))
            if history and d.get("history"):
                L.append("   History of the section: " + " | ".join(d["history"]))
            L.append("")
    else:
        for e in res["sections"]:
            if not e["found"]:
                L.append(f"[{cite(e['target'])} not found" + (": it exists only in amendments that are not in force]" if e.get("only_pending") else "]"))
                L.append("")
                continue
            head = f"== {cite(e['target'])}" + (f" ({e['headnote']})" if e.get("headnote") else "") + " =="
            L.append(head)
            if e.get("context"):
                L.append("   [" + " > ".join(e["context"]) + "]")
            L.extend(fmt_rows(e["rows"]))
            if history and e["history"]:
                L.append("   History: " + " | ".join(e["history"]))
            L.append("")
    for info in res.get("commencement_orders") or []:
        L.append(f"COMMENCEMENT ORDERS on e-Laws for {info['act']} ({info['url']}, Orders in Council tab; older orders "
                 f"may not be listed there):")
        if not info["orders"]:
            L.append("   none listed")
        for o in info["orders"]:
            name = o["title"].title().replace("In Council", "in Council")
            L.append(f"   {name}, ordered {o['ordered'] or '(date not read)'}" + ("  [names this law]" if o.get("mentions_law") else "")
                     + f"  {o['url']}")
            for op in o["text"]:
                L.append(f"      {op}")
        L.append("")
    if res["flags"]:
        L.append("CHECK BEFORE RELYING ON THIS:")
        order = {"NOT_IN_FORCE": 0, "REPEALED": 0, "PENDING": 1, "CHECK": 2}
        for f in sorted(res["flags"], key=lambda f: order.get(f["level"], 3)):
            label = {"NOT_IN_FORCE": "NOT IN FORCE", "REPEALED": "REPEALED/REVOKED", "PENDING": "PENDING CHANGE",
                     "CHECK": "CHECK"}.get(f["level"], f["level"])
            loc = f["where"] if f["where"].startswith("(") else f"({f['where']})"
            L.append(f"- {label} {loc}: {f['text']}")
        if any(f["level"] == "PENDING" for f in res["flags"]):
            if res["source"] == "on":
                L.append("- Pending text comes into force by a commencement order or on a date it names. Orders made after"
                         " the currency date may not show in the text yet; the COMMENCEMENT ORDERS list above (if any) shows"
                         " what e-Laws has published. Older proclamations may not be on that tab; check The Ontario Gazette"
                         " (https://www.ontario.ca/ontario-gazette).")
            else:
                L.append("- A pending change could come into force after the currency date. Check the Justice Laws"
                         " page and the Canada Gazette, Part II, for an order fixing its coming into force.")
    L.append(FOOTER_SOURCE if res.get("source_law") else FOOTER)
    if crown_notice(res):
        L.append(crown_notice(res))
    return "\n".join(L)


def exit_code(res):
    if res.get("definitions") is not None and not res["definitions"]:
        return 5 if res.get("pending_only") else 4
    if res.get("matches") is not None and not res["matches"]:
        return 4
    if res.get("matches") and all(h.get("not_in_force") for h in res["matches"]):
        return 5
    if res.get("sections") and any(not e["found"] for e in res["sections"]):
        return 5 if any(e.get("only_pending") for e in res["sections"] if not e["found"]) else 4
    if res.get("sections") and any(e.get("only_pending") for e in res["sections"]):
        return 5
    if res.get("definitions") and all(d.get("only_pending") for d in res["definitions"]):
        return 5
    if any(f["level"] in ("REPEALED",) for f in res["flags"]) or res["status"].startswith(("NOT", "REPEALED", "REVOKED")):
        return 5
    return 0


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = list(argv)
    opts = {"find": None, "define": None, "status": False, "history": False, "fresh": False, "json": False, "source": False,
            "no-orders": False}
    pos = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--find", "--define"):
            if i + 1 >= len(args):
                print(__doc__)
                return 2
            opts[a[2:]] = args[i + 1]
            i += 2
            continue
        if a in ("--status", "--history", "--fresh", "--json", "--source", "--no-orders"):
            opts[a[2:]] = True
        elif a in ("-h", "--help"):
            print(__doc__)
            return 0
        elif a == "--version":
            print(VERSION)
            return 0
        elif a.startswith("--"):
            print(f"Unknown option {a}. Run with --help for the options.", file=sys.stderr)
            return 2
        else:
            pos.append(a)
        i += 1
    if len(pos) < 2:
        print(__doc__)
        return 2
    src, code, targets = pos[0].lower(), pos[1], pos[2:]
    if not (targets or opts["find"] or opts["define"] or opts["status"]):
        opts["status"] = True
    try:
        res = lookup(src, code, targets, opts["find"], opts["define"], opts["status"], opts["history"],
                     opts["fresh"], opts["source"], not opts["no-orders"])
    except LawError as e:
        if opts["json"]:
            print(json.dumps({"error": str(e), "exit_code": e.code}))
        else:
            print(f"ERROR: {e}")
        return e.code
    print(json.dumps(dict(res, copyright=crown_notice(res)), ensure_ascii=False, indent=1) if opts["json"]
          else format_text(res, opts["history"]))
    return exit_code(res)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
