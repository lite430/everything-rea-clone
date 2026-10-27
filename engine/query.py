"""Everything-compatible search syntax parser and evaluator.

Implements the query grammar observed in everything.exe strings:
  - implicit AND ("a b"), OR ("a|b"), NOT ("!a"), grouping "( ... )"
  - exact phrase "..." and wildcards * ?
  - modifiers: case: nocase: wholeword: ww: nowholeword: regex: noregex:
    exact: noexact: matchpath: nomatchpath: path:
  - type filters: file: files: folder: folders: audio: video: pic: pictures:
    doc: exe: zip: compressed: ext:<list>
  - size:<range>  dm:/dc:/dr:/da:<date range> (today/yesterday/thisweek/...)
  - attrib:<flags> dupe: nodupe: childcount: count: root:  (subset)
"""

import os
import re
import time
from datetime import datetime, timedelta

# ---------------------------------------------------------------- constants

AUDIO_EXTS = ("aac;ac3;aif;aifc;aiff;ape;au;cda;dts;flac;it;m4a;m4b;m4p;mid;midi;"
              "mod;mp2;mp3;mpa;oga;ogg;opus;ra;rmi;s3m;sid;spc;tta;voc;wav;wma;wv;xm").split(";")
VIDEO_EXTS = ("3g2;3gp;3gp2;3gpp;amr;amv;asf;avi;bdmv;bik;d2v;divx;drc;evo;f4v;flc;fli;"
              "flv;hdmov;ifo;ivf;m1v;m2p;m2t;m2ts;m2v;m4b;m4p;m4v;mkv;mp2v;mp4;mp4v;mpe;"
              "mpeg;mpg;mpls;mpv2;mpv4;mov;mts;ogm;ogv;pss;pva;qt;ram;rm;rmm;rmvb;roq;"
              "rpm;smil;smk;swf;tp;tpr;ts;vob;vp6;webm;wm;wmp;wmv").split(";")
PIC_EXTS = ("ani;bmp;gif;ico;jpe;jpeg;jpg;pcx;png;psd;tga;tif;tiff;webp;wmf").split(";")
DOC_EXTS = ("c;cc;cpp;csv;doc;docx;htm;html;h;hh;ini;log;md;pdf;php;pl;py;ps;rtf;sql;"
            "tex;txt;xml;xsl;json;js;css;conf").split(";")
EXE_EXTS = ("bat;cmd;com;cpl;exe;msi;scr").split(";")
ZIP_EXTS = ("7z;ace;arj;bz2;cab;gz;gzip;img;iso;jar;lzh;nrg;rar;tar;taz;tgz;wim;"
            "z;zip;zst;xz;vhd;vhdx").split(";")

ATTR_LETTERS = {
    "r": 0x1, "h": 0x2, "s": 0x4, "d": 0x10, "a": 0x20, "n": 0x80,
    "t": 0x100, "l": 0x200, "p": 0x400, "c": 0x800, "o": 0x1000,
    "i": 0x2000, "e": 0x4000, "b": 0x8000,
}

SIZE_UNITS = {"b": 1, "kb": 1 << 10, "mb": 1 << 20, "gb": 1 << 30, "tb": 1 << 40}

WORD_RE = re.compile(r"[^A-Za-z0-9\u0080-\uffff]+")

# ---------------------------------------------------------------- tokenizer

class BlobTerm:
    """Plain lowercase substring term; resolvable via the volume name blob."""

    __slots__ = ("needle", "fast_blob")

    def __init__(self, needle):
        self.needle = needle
        self.fast_blob = needle.encode("utf-8", "replace")

    def __call__(self, e):
        return self.needle in e.lname


class Tok:
    def __init__(self, kind, val):
        self.kind = kind      # term, phrase, lparen, rparen, or, not
        self.val = val
    def __repr__(self):
        return "%s(%r)" % (self.kind, self.val)


def tokenize(s):
    toks = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            toks.append(Tok("lparen", c)); i += 1; continue
        if c == ")":
            toks.append(Tok("rparen", c)); i += 1; continue
        if c == "|":
            toks.append(Tok("or", c)); i += 1; continue
        if c == "!":
            toks.append(Tok("not", c)); i += 1; continue
        if c == '"':
            j = s.find('"', i + 1)
            if j < 0:
                j = n
            toks.append(Tok("phrase", s[i + 1:j]))
            i = j + 1
            continue
        # a bare term runs to the next space/paren/or/not delimiter
        # regex: terms swallow everything up to whitespace (parens included)
        j = i
        if s[i:].lower().startswith("regex:"):
            while j < n and not s[j].isspace():
                j += 1
        else:
            while j < n and not s[j].isspace() and s[j] not in '()|"!':
                j += 1
        toks.append(Tok("term", s[i:j]))
        i = j
    return toks

# ---------------------------------------------------------------- parsers

def parse_size(v):
    v = v.strip().lower().replace(" ", "")
    for u in ("tb", "gb", "mb", "kb", "b"):
        if v.endswith(u):
            num = v[:-len(u)]
            try:
                return float(num) * SIZE_UNITS[u]
            except ValueError:
                return None
    try:
        return float(v)
    except ValueError:
        return None


def _week_start(dt):
    return dt - timedelta(days=dt.weekday())


def _month_start(dt):
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _year_start(dt):
    return dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


DATE_CONSTANTS = {}


def _build_date_constants():
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    DATE_CONSTANTS.update({
        "today": today,
        "yesterday": today - timedelta(days=1),
        "tomorrow": today + timedelta(days=1),
        "thisweek": _week_start(now),
        "lastweek": _week_start(now) - timedelta(weeks=1),
        "nextweek": _week_start(now) + timedelta(weeks=1),
        "thismonth": _month_start(now),
        "lastmonth": _month_start(now.replace(day=1) - timedelta(days=1)),
        "nextmonth": _month_start(now.replace(day=28) + timedelta(days=5)),
        "thisyear": _year_start(now),
        "lastyear": _year_start(now).replace(year=now.year - 1),
        "nextyear": _year_start(now).replace(year=now.year + 1),
    })


_build_date_constants()


def parse_date_token(v):
    """-> datetime or None"""
    v = v.strip().lower()
    if v in DATE_CONSTANTS:
        return DATE_CONSTANTS[v]
    m = re.match(r"^(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?$", v)
    if m:
        y = int(m.group(1))
        mo = int(m.group(2) or 1)
        d = int(m.group(3) or 1)
        try:
            return datetime(y, mo, d)
        except ValueError:
            return None
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", v)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    # last<N><unit> / past<N><unit>
    m = re.match(r"^(?:last|past)(\d+)(hour|hours|day|days|week|weeks|month|months|year|years)$", v)
    if m:
        num = int(m.group(1)); unit = m.group(2).rstrip("s")
        mul = {"hour": timedelta(hours=1), "day": timedelta(days=1),
               "week": timedelta(weeks=1), "month": timedelta(days=30),
               "year": timedelta(days=365)}[unit]
        return datetime.now() - num * mul
    return None


def parse_date_range(v):
    """'a-b' | '<a' | '>a' | '>=a' | '<=a' | 'a' -> (lo, hi) datetimes (lo inclusive, hi exclusive day+1)."""
    v = v.strip()
    for op in ("<=", ">=", "<", ">", "="):
        if v.startswith(op):
            d = parse_date_token(v[len(op):])
            if d is None:
                return None
            if op in (">", ">="):
                return (d, None)
            if op in ("<", "<="):
                return (None, d + timedelta(days=1))
            return (d, d + timedelta(days=1))
    if "-" in v:
        a, _, b = v.partition("-")
        da, db = parse_date_token(a), parse_date_token(b)
        if da and db:
            return (da, db + timedelta(days=1))
        return None
    d = parse_date_token(v)
    if d:
        return (d, d + timedelta(days=1))
    return None


def parse_size_range(v):
    """'>10mb' '<1gb' '1kb-1mb' '10mb' -> (lo, hi) floats."""
    v = v.strip()
    for op in ("<=", ">=", "<", ">", "="):
        if v.startswith(op):
            s = parse_size(v[len(op):])
            if s is None:
                return None
            if op in (">", ">="):
                return (s, None)
            if op in ("<", "<="):
                return (None, s)
            return (s, s)
    if "-" in v:
        a, _, b = v.partition("-")
        sa, sb = parse_size(a), parse_size(b)
        if sa is not None and sb is not None:
            return (sa, sb)
        return None
    s = parse_size(v)
    if s is None:
        return None
    return (s, s)

# ---------------------------------------------------------------- predicates

class Query:
    """Compiled query: tree of predicate callables over Entry objects."""

    def __init__(self, text):
        self.text = text
        self.tokens = tokenize(text)
        parsed = self._parse_or(self.tokens, [0])
        self.tree = self._compile(parsed, {"case": False, "wholeword": False,
                                           "matchpath": False, "regex": False,
                                           "exact": False})

    # -- recursive descent: or_expr := and_expr ('|' and_expr)*
    def _parse_or(self, toks, pos):
        alts = [self._parse_and(toks, pos)]
        while pos[0] < len(toks) and toks[pos[0]].kind == "or":
            pos[0] += 1
            alts.append(self._parse_and(toks, pos))
        return ("or", alts) if len(alts) > 1 else alts[0]

    def _parse_and(self, toks, pos):
        items = []
        while pos[0] < len(toks):
            t = toks[pos[0]]
            if t.kind in ("rparen", "or"):
                break
            if t.kind == "not":
                pos[0] += 1
                items.append(("not", self._parse_unary(toks, pos)))
                continue
            items.append(self._parse_unary(toks, pos))
        return ("and", items) if len(items) > 1 else (items[0] if items else ("all",))

    def _parse_unary(self, toks, pos):
        if pos[0] >= len(toks):
            return ("all",)
        t = toks[pos[0]]
        if t.kind == "not":
            pos[0] += 1
            return ("not", self._parse_unary(toks, pos))
        if t.kind == "lparen":
            pos[0] += 1
            node = self._parse_or(toks, pos)
            if pos[0] < len(toks) and toks[pos[0]].kind == "rparen":
                pos[0] += 1
            return node
        pos[0] += 1
        return ("term", t.val, t.kind)

    # -- compile to predicate
    def _compile(self, node, mods):
        kind = node[0]
        if kind == "or":
            subs = [self._compile(x, dict(mods)) for x in node[1]]
            return ("or", subs)
        if kind == "and":
            subs = [self._compile(x, dict(mods)) for x in node[1]]
            return ("and", subs)
        if kind == "not":
            return ("not", self._compile(node[1], dict(mods)))
        if kind == "all":
            return ("all",)
        return self._compile_term(node[1], node[2], mods)

    def _compile_term(self, val, tkind, mods):
        low = val.lower()

        # modifier prefixes
        for m in ("wholeword", "ww", "nowholeword", "nocase", "case", "nomatchpath",
                  "matchpath", "noregex", "regex", "noexact", "exact"):
            if low.startswith(m + ":"):
                key = m if m not in ("ww",) else "wholeword"
                flag = not m.startswith("no")
                rest = val[len(m) + 1:]
                if not rest:
                    mods[key] = flag
                    return ("all",)
                mods2 = dict(mods)
                mods2[key] = flag
                return self._compile_term(rest, tkind, mods2)

        # search-target prefix: path:
        target_name = True
        if low.startswith("path:"):
            val = val[5:]
            low = val.lower()
            target_name = False
            mods["matchpath"] = True
            if not val:
                return ("all",)

        # filters
        def filt(pred):
            return ("filter", pred)

        if low in ("file:", "files:"):
            return filt(lambda e: not e.is_dir)
        if low in ("folder:", "folders:", "dir:", "dirs:"):
            return filt(lambda e: e.is_dir)
        if low == "audio:":
            return filt(lambda e: (not e.is_dir) and e.ext in AUDIO_EXTS)
        if low == "video:":
            return filt(lambda e: (not e.is_dir) and e.ext in VIDEO_EXTS)
        if low in ("pic:", "pics:", "picture:", "pictures:"):
            return filt(lambda e: (not e.is_dir) and e.ext in PIC_EXTS)
        if low == "doc:":
            return filt(lambda e: (not e.is_dir) and e.ext in DOC_EXTS)
        if low == "exe:":
            return filt(lambda e: (not e.is_dir) and e.ext in EXE_EXTS)
        if low in ("zip:", "compressed:"):
            return filt(lambda e: (not e.is_dir) and e.ext in ZIP_EXTS)

        if low.startswith("ext:"):
            exts = [x.lower().lstrip(".") for x in val[4:].split(";") if x]
            return filt(lambda e: e.ext in exts)

        if low.startswith("size:"):
            rng = parse_size_range(val[5:])
            if rng is None:
                return ("all",)
            lo, hi = rng
            def size_pred(e, lo=lo, hi=hi):
                return (lo is None or e.size >= lo) and (hi is None or e.size <= hi)
            return filt(size_pred)

        for pfx, attr in (("dm:", "modified"), ("dc:", "created")):
            if low.startswith(pfx):
                rng = parse_date_range(val[3:])
                if rng is None:
                    return ("all",)
                lo, hi = rng
                lo_s = time.mktime(lo.timetuple()) if lo else None
                hi_s = time.mktime(hi.timetuple()) if hi else None
                def date_pred(e, lo_s=lo_s, hi_s=hi_s, attr=attr):
                    v = getattr(e, attr)
                    return (lo_s is None or v >= lo_s) and (hi_s is None or v < hi_s)
                return filt(date_pred)

        if low.startswith(("dr:", "da:")):
            return ("all",)  # date-recently-changed / accessed are not indexed (same as Everything)

        if low.startswith(("attrib:", "attributes:")):
            spec = val.split(":", 1)[1]
            want = 0
            want_not = 0
            for part in spec.split(";"):
                part = part.strip().lower()
                neg = part.startswith("!")
                if neg:
                    part = part[1:]
                bits = ATTR_LETTERS.get(part, 0)
                if neg:
                    want_not |= bits
                else:
                    want |= bits
            def attr_pred(e, want=want, want_not=want_not):
                return (not want or (e.attrs & want) == want) and \
                       (not want_not or not (e.attrs & want_not))
            return filt(attr_pred)

        if low == "dupe:":
            return ("dupe", False)
        if low.startswith("dupe:"):
            return ("dupe", True)  # dupe:name — name only
        if low == "nodupe:":
            return ("all",)
        if low.startswith("list:"):
            label = val[5:]
            return filt(lambda e, l=label: e.vol == l)

        if low.startswith(("childcount:", "count:", "filecount:", "foldercount:")):
            return ("all",)  # requires folder aggregation; reserved

        if low.startswith("root:"):
            path = val[5:].lower().rstrip("\\") + "\\"
            return filt(lambda e, p=path: e.path.lower().startswith(p) and
                        len(e.path.lower()) > len(p))

        # ---- plain / phrase / wildcard / regex term
        if tkind == "phrase":
            if mods.get("case"):
                return ("term_str", val, True)
            return ("term_str", val.lower(), False)

        if mods.get("regex"):
            flags = 0 if mods.get("case") else re.IGNORECASE
            try:
                rx = re.compile(val, flags)
            except re.error:
                return ("all",)
            target_path = mods.get("matchpath")
            def rx_pred(e, rx=rx, tp=target_path):
                s = e.path if tp else e.name
                return rx.search(s) is not None
            return filt(rx_pred)

        # term ending with \ -> path prefix search (Everything behavior)
        if val.endswith("\\"):
            p = val.rstrip("\\").lower() + "\\"
            return filt(lambda e, p=p: e.path.lower().startswith(p))

        # wildcard support: only meaningful chars
        if "*" in val or "?" in val:
            pattern = "^" + re.escape(val).replace("\\*", ".*").replace("\\?", ".") + "$"
            flags = 0 if mods.get("case") else re.IGNORECASE
            try:
                rx = re.compile(pattern, flags)
            except re.error:
                return ("all",)
            target_path = mods.get("matchpath")
            def wc_pred(e, rx=rx, tp=target_path):
                s = e.path if tp else e.name
                return rx.match(s) is not None
            return filt(wc_pred)

        if mods.get("wholeword"):
            flags = 0 if mods.get("case") else re.IGNORECASE
            rx = re.compile(r"\b" + re.escape(val) + r"\b", flags)
            def ww_pred(e, rx=rx):
                return rx.search(e.name) is not None
            return filt(ww_pred)

        if mods.get("exact"):
            if mods.get("case"):
                return filt(lambda e, v=val: e.name == v)
            return filt(lambda e, v=low: e.lname == v)

        # plain substring (the default fast path)
        return ("term_sub", low if not mods.get("case") else val, bool(mods.get("case")),
                bool(mods.get("matchpath")))

    # ---------------------------------------------------------------- eval
    def execute(self, entries, limit=None, offset=0):
        """Filter an iterable of entries; returns list of entries."""
        terms, filters = self._plan(self.tree)
        out = []
        for e in entries:
            ok = True
            for t in terms:
                if not t(e):
                    ok = False
                    break
            if ok:
                for f in filters:
                    if not f(e):
                        ok = False
                        break
            if ok:
                out.append(e)
        if self._has_dupe:
            out = self._dupes(out, self._dupe_name_only)
        if offset or limit is not None:
            out = out[offset:] if limit is None else out[offset:offset + limit]
        return out

    def _plan(self, node):
        """Flatten into (substring_terms, other_predicates) for fast-first eval."""
        terms = []
        filters = []

        def walk(nd):
            if not isinstance(nd, tuple):
                return
            if nd[0] == "term_sub":
                needle, cs, mp = nd[1], nd[2], nd[3]
                if not cs and not mp:
                    terms.append(BlobTerm(needle))
                elif cs:
                    terms.append(lambda e, n=needle: n in e.name)
                elif mp:
                    terms.append(lambda e, n=needle: n in e.path.lower())
                else:
                    terms.append(lambda e, n=needle: n in e.lname)
                return
            if nd[0] == "term_str":
                needle, cs = nd[1], nd[2]
                if cs:
                    terms.append(lambda e, n=needle: n in e.name)
                else:
                    terms.append(BlobTerm(needle))
                return
            if nd[0] == "filter":
                filters.append(nd[1])
                return
            if nd[0] == "dupe":
                self._dupe_name_only = nd[1]
                self._has_dupe = True
                return
            if nd[0] == "and":
                for x in nd[1]:
                    walk(x)
                return
            # or / not subtrees evaluate per-entry
            pred = self._subtree(nd)
            filters.append(pred)

        self._has_dupe = False
        self._dupe_name_only = False
        walk(node)
        return terms, filters

    def _subtree(self, node):
        """Compile any subtree to a single predicate (for or/not nodes)."""
        kind = node[0]
        if kind == "and":
            subs = [self._subtree(x) for x in node[1]]
            def all_pred(e, subs=subs):
                return all(p(e) for p in subs)
            return all_pred
        if kind == "or":
            subs = [self._subtree(x) for x in node[1]]
            def any_pred(e, subs=subs):
                return any(p(e) for p in subs)
            return any_pred
        if kind == "not":
            inner = self._subtree(node[1])
            return lambda e, i=inner: not i(e)
        if kind == "all":
            return lambda e: True
        # leaves
        terms, filters = [], []
        sub = Query.__new__(Query)
        sub.tree = node
        terms, filters = sub._plan(node)
        def leaf_pred(e, terms=terms, filters=filters):
            for t in terms:
                if not t(e):
                    return False
            for f in filters:
                if not f(e):
                    return False
            return True
        return leaf_pred

    @staticmethod
    def _dupes(lst, name_only=False):
        seen = {}
        for e in lst:
            k = (e.lname,) if name_only else (e.lname, e.size, e.is_dir)
            seen.setdefault(k, []).append(e)
        return [e for k, es in seen.items() if len(es) > 1 for e in es]
