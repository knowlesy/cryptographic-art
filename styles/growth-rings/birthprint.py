#!/usr/bin/env python3
"""
birthprint.py - a generative birth print with a keyed glyph cipher.

One script, three files out:
    <out>/<name>_print.pdf     vector artwork at print size
    <out>/<name>_preview.png   raster preview
    <out>/<name>_card.pdf      the "sealed card": how to decode the ring
    <out>/<name>_card_with_table.pdf   the same card plus the finished letter table

Run it:
    python birthprint.py --name oscar --date 20240317 --time 04:42 \
        --weight "3600 g" --coords "N 53.48, W 2.24" \
        --message "the quick brown fox" --size "A3 portrait"

Needs Python 3.9+ and:  pip install matplotlib numpy pillow
The same inputs always give the same artwork (no clock, no random seed).
"""
import argparse
import datetime
import hashlib
import io
import math
import os
import re
import sys
import textwrap
import unicodedata

import numpy as np
import matplotlib

matplotlib.use("Agg")
matplotlib.rcParams["pdf.fonttype"] = 42      # embed the type as TrueType outlines (print-shop friendly)
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.collections import LineCollection
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, PathPatch, Polygon
from matplotlib.path import Path
from PIL import Image, ImageFilter

VERSION = "1.0"
MM = 72.0 / 25.4  # points per millimetre

# ----------------------------------------------------------------------------
# colours
# ----------------------------------------------------------------------------
PAPER = "#F4ECD8"   # cream
INK = "#17235C"     # blueprint navy: glyphs, ticks, type
SIGNAL = "#F4511E"  # orange-red: the birth-time mark
PALETTE = ["#F4511E", "#1E5BF0", "#F5A300", "#00A078", "#7B45E6", "#10B4C8"]
#           orange-red cobalt     marigold   emerald    violet     turquoise
# No pink, by request. The red leans orange so that fine red lines on cream do not
# blend into salmon when seen from across a room.
# Pale partner of each ink for the tinted bands of style 3. Hand-picked rather than
# mixed, because a plain tint of vermilion or violet on cream drifts towards pink.
TINTS = {"#F4511E": "#F9D49A", "#1E5BF0": "#C8D5F1", "#F5A300": "#F7DE9E",
         "#00A078": "#BFE0C8", "#7B45E6": "#D3CDEB", "#10B4C8": "#BFE3E2"}


def _rgb(hexcol):
    h = hexcol.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _mix(a, b, t):
    """Solid mix of two hex colours (no transparency, print-safe)."""
    ra, rb = _rgb(a), _rgb(b)
    return tuple(ra[i] * (1 - t) + rb[i] * t for i in range(3))


# ----------------------------------------------------------------------------
# 1. normalising the inputs
# ----------------------------------------------------------------------------
class InputError(ValueError):
    pass


_SPECIAL = {"ß": "ss", "æ": "ae", "œ": "oe", "ø": "o", "đ": "d", "ð": "d", "ł": "l", "þ": "th", "ı": "i"}


def _letters_of(ch):
    """The plain a-z letters a single character stands for ('' if none)."""
    ch = ch.lower()
    if ch in _SPECIAL:
        return _SPECIAL[ch]
    base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)).lower()
    return "".join(c for c in base if "a" <= c <= "z")


def norm_name(raw):
    """Returns (letters, slots). letters = a-z only, used for the key and the
    ring bundles. slots = [(display_char, letter_or_None)] for the printed name."""
    raw = " ".join(str(raw).split())
    slots = []
    for ch in raw:
        let = _letters_of(ch)
        if len(let) == 1:
            slots.append((ch.lower(), let))
        elif len(let) > 1:
            slots.extend((c, c) for c in let)
        elif ch in " -'’" and slots and slots[-1][1] is not None:
            slots.append((ch.replace("’", "'"), None))
    while slots and slots[-1][1] is None:
        slots.pop()
    letters = "".join(l for _, l in slots if l)
    if not letters:
        raise InputError("name: needs at least one letter a-z")
    if len(letters) > 24:
        raise InputError("name: at most 24 letters (got %d)" % len(letters))
    return letters, slots


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def norm_date(raw):
    """Returns 'YYYYMMDD'. Accepts 20240317, 2024-03-17, 2024/3/17, 17 March 2024, March 17 2024,
    and 17/03/2024 only when it cannot be misread."""
    s = str(raw).strip().lower().replace(",", " ")
    y = m = d = None
    if re.fullmatch(r"\d{8}", s):
        y, m, d = int(s[:4]), int(s[4:6]), int(s[6:])
    else:
        a = re.fullmatch(r"(\d{4})\s*[-/. ]\s*(\d{1,2})\s*[-/. ]\s*(\d{1,2})", s)
        b = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3,9})\.?\s+(\d{4})", s)
        c = re.fullmatch(r"([a-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?\s+(\d{4})", s)
        e = re.fullmatch(r"(\d{1,2})\s*[-/. ]\s*(\d{1,2})\s*[-/. ]\s*(\d{4})", s)
        if a:
            y, m, d = int(a.group(1)), int(a.group(2)), int(a.group(3))
        elif e:
            # day-first or month-first? only accepted when just one reading is possible
            p, q, y = int(e.group(1)), int(e.group(2)), int(e.group(3))
            if p > 12 >= q:
                d, m = p, q
            elif q > 12 >= p:
                m, d = p, q
            else:
                raise InputError("date: %r could be day-month or month-day; write it year first, "
                                 "e.g. 20240317 or 2024-03-17" % raw)
        elif b and b.group(2)[:3] in _MONTHS:
            d, m, y = int(b.group(1)), _MONTHS[b.group(2)[:3]], int(b.group(3))
        elif c and c.group(1)[:3] in _MONTHS:
            m, d, y = _MONTHS[c.group(1)[:3]], int(c.group(2)), int(c.group(3))
    if y is None:
        raise InputError("date: write it year first, e.g. 20240317 or 2024-03-17 (got %r)" % raw)
    try:
        datetime.date(y, m, d)
    except ValueError:
        raise InputError("date: %r is not a real calendar date" % raw)
    return "%04d%02d%02d" % (y, m, d)


def norm_time(raw):
    """Returns 'HHMM' (24-hour). Accepts 04:42, 4:42, 0442, 4.42, 4:42 am, 4:42pm."""
    s = str(raw).strip().lower().replace(" ", "")
    ap = None
    k = re.search(r"(a\.?m\.?|p\.?m\.?)$", s)
    if k:
        ap = k.group(1)[0]
        s = s[:k.start()]
    a = re.fullmatch(r"(\d{1,2})[:.h](\d{2})(?:[:.]\d{2})?", s)
    b = re.fullmatch(r"(\d{1,2})(\d{2})", s)
    c = re.fullmatch(r"(\d{1,2})", s) if ap else None
    if a or b:
        hh, mi = int((a or b).group(1)), int((a or b).group(2))
    elif c:
        hh, mi = int(c.group(1)), 0
    else:
        raise InputError("time: write it like 04:42 (24-hour) or 4:42 am (got %r)" % raw)
    if ap:
        if not 1 <= hh <= 12:
            raise InputError("time: %r is not a valid 12-hour time" % raw)
        hh = hh % 12 + (12 if ap == "p" else 0)
    if not (0 <= hh <= 23 and 0 <= mi <= 59):
        raise InputError("time: %r is out of range" % raw)
    return "%02d%02d" % (hh, mi)


def norm_weight(raw):
    """Returns whole grams. Accepts 3600, '3600 g', '3,600g', '3.6 kg', '7 lb 15 oz'."""
    s = str(raw).strip().lower()
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s).replace(",", ".")
    lb = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:lbs?|pounds?)\.?\s*(?:(\d+(?:\.\d+)?)\s*(?:oz|ounces?)\.?)?", s)
    kg = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:kg|kgs|kilos?|kilograms?)", s)
    g = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:g|gr|grams?|gm)?", s)
    if lb:
        grams = float(lb.group(1)) * 453.59237 + float(lb.group(2) or 0) * 28.349523125
    elif kg:
        grams = float(kg.group(1)) * 1000.0
    elif g:
        grams = float(g.group(1))
        if grams < 200 and not re.search(r"[a-z]", s):
            raise InputError("weight: %r has no unit - write grams (3600 g) or kilograms (3.6 kg)" % raw)
    else:
        raise InputError("weight: write it like '3600 g', '3.6 kg' or '7 lb 15 oz' (got %r)" % raw)
    grams = int(math.floor(grams + 0.5))
    if not 200 <= grams <= 9999:
        raise InputError("weight: %d g is outside 200-9999 g" % grams)
    return grams


def norm_coords(raw):
    """Returns (lat, lon, lat_text, lon_text).
    Accepts 'N 53.48, W 2.24', 'N 53.48 W 2.24', '53.48N 2.24W', '53.48 north, 2.24 west', '53.48, -2.24'."""
    s = str(raw).strip().upper().replace("\u00b0", " ")
    s = re.sub(r"LATITUDE|LONGITUDE|LONG|LAT|LON|DEGREES|DEG", " ", s)
    for word, letter in (("NORTH", "N"), ("SOUTH", "S"), ("EAST", "E"), ("WEST", "W")):
        s = s.replace(word, letter)
    hint = "coordinates: write them like 'N 53.48, W 2.24' (got %r)" % raw
    if re.search(r"[A-Z]{2}|[A-DF-MO-RT-VX-Z]", s):
        raise InputError(hint)
    toks = re.findall(r"[NSEW]|[+-]?\d+(?:\.\d+)?", s)
    shape = "".join("L" if t in "NSEW" else "#" for t in toks)
    if shape == "L#L#":                                    # letter before each number
        pairs = [(toks[0], toks[1]), (toks[2], toks[3])]
    elif shape == "#L#L":                                  # letter after each number
        pairs = [(toks[1], toks[0]), (toks[3], toks[2])]
    elif shape == "##":                                    # bare numbers: latitude first, minus = south / west
        pairs = [("N" if float(toks[0]) >= 0 else "S", toks[0]), ("E" if float(toks[1]) >= 0 else "W", toks[1])]
    else:
        raise InputError(hint)
    vals = {}
    for hemi, num in pairs:
        kind = "lat" if hemi in "NS" else "lon"
        if kind in vals:
            raise InputError("coordinates: need one latitude (N/S) and one longitude (E/W)")
        vals[kind] = ((-1.0 if hemi in "SW" else 1.0) * abs(float(num)), "%s %s" % (hemi, num.lstrip("+-")))
    if len(vals) != 2:
        raise InputError("coordinates: need one latitude (N/S) and one longitude (E/W)")
    if abs(vals["lat"][0]) > 90 or abs(vals["lon"][0]) > 180:
        raise InputError("coordinates: out of range")
    return vals["lat"][0], vals["lon"][0], vals["lat"][1], vals["lon"][1]


MAX_MESSAGE = 150  # characters incl. spaces: up to 60 sit in one ring, 112 in two, 150 in three


def norm_message(raw):
    """Returns the message as a-z and single spaces. Punctuation is dropped."""
    out = []
    for ch in str(raw):
        if ch.isdigit():
            raise InputError("message: spell numbers out in words (found %r)" % ch)
        let = _letters_of(ch)
        if let:
            out.append(let)
        elif ch.isspace() or ch in "-–—/":
            out.append(" ")
    msg = " ".join("".join(out).split())
    if not msg:
        raise InputError("message: needs at least one letter")
    if len(msg) > MAX_MESSAGE:
        raise InputError("message: %d characters after tidying; the rings hold %d" % (len(msg), MAX_MESSAGE))
    return msg


_A_SIZES = {"a0": (841, 1189), "a1": (594, 841), "a2": (420, 594), "a3": (297, 420),
            "a4": (210, 297), "a5": (148, 210), "a6": (105, 148)}


def norm_size(raw):
    """Returns (width_mm, height_mm). Accepts 'A3 portrait', 'a2 landscape', '300x400', '11x14 in'."""
    s = str(raw).strip().lower()
    orient = "landscape" if "landscape" in s else ("portrait" if "portrait" in s else None)
    s2 = s.replace("landscape", "").replace("portrait", "").strip()
    if s2 in _A_SIZES:
        w, h = _A_SIZES[s2]
    else:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(mm|cm|in|inch|inches|\")?", s2)
        if not m:
            raise InputError("print size: write it like 'A3 portrait' or '300x400 mm' (got %r)" % raw)
        f = {"mm": 1.0, "cm": 10.0, None: 1.0}.get(m.group(3), 25.4)
        w, h = float(m.group(1)) * f, float(m.group(2)) * f
    if orient == "landscape" and h > w or orient == "portrait" and w > h:
        w, h = h, w
    if min(w, h) < 100 or max(w, h) > 1500:
        raise InputError("print size: between 100 mm and 1500 mm a side, please")
    return float(w), float(h)


class Spec:
    """Everything the print is made from, already normalised."""

    def __init__(self, name, date, time, weight, coords, message=None, size="A3 portrait"):
        self.letters, self.slots = norm_name(name)
        self.date = norm_date(date)
        self.hhmm = norm_time(time)
        self.grams = norm_weight(weight)
        self.lat, self.lon, self.lat_text, self.lon_text = norm_coords(coords)
        self.message = norm_message(message) if message is not None else None
        self.W, self.H = norm_size(size)
        self.blocks = key_blocks(self.letters, self.date, self.hhmm, self.grams)
        self.key = sum(self.blocks)
        self.perm = permutation(self.key)           # perm[letter 0-25] = glyph 0-25
        self.inv = [self.perm.index(g) for g in range(26)]

    def art_seed(self):
        return "|".join([self.letters, self.date, self.hhmm, str(self.grams), "%.5f" % self.lat, "%.5f" % self.lon])


# ----------------------------------------------------------------------------
# 2. the cipher
# ----------------------------------------------------------------------------
def key_blocks(letters, date8, hhmm, grams):
    """Four numbers, one per block. Each is made on its own, then they are
    added, so the order you take them in cannot matter."""
    name_number = int("".join("%02d" % (ord(c) - 96) for c in letters))   # a=01 ... z=26, side by side
    return [name_number, int(date8), int(hhmm), int(grams)]


def permutation(key):
    """Counting-out shuffle driven by the digits of the key.
    Put a finger on the first glyph. For each letter a..z: read the next digit
    of the key (wrapping round), move the finger forward that many unclaimed
    glyphs (wrapping round), give that glyph to the letter, strike it out, and
    rest the finger on the next unclaimed glyph."""
    digits = [int(c) for c in str(int(key))]
    remaining = list(range(26))
    pos = 0
    perm = []
    for i in range(26):
        pos = (pos + digits[i % len(digits)]) % len(remaining)
        perm.append(remaining.pop(pos))
        if remaining:
            pos %= len(remaining)
    return perm


# ----------------------------------------------------------------------------
# 3. the 26 glyphs (unit radius 1; y up; fixed "card order")
# ----------------------------------------------------------------------------
def _arc(r, a0, a1, w=0.20, cx=0.0, cy=0.0):
    return ("arc", cx, cy, r, a0, a1, w)


def _line(x0, y0, x1, y1, w=0.20):
    return ("line", x0, y0, x1, y1, w)


def _dot(x, y, r):
    return ("dot", x, y, r)


def _poly(pts, w=0.20):
    return ("poly", tuple((round(x, 4), round(y, 4)) for x, y in pts), w)


def _pol(r, deg):
    a = math.radians(deg)
    return (r * math.cos(a), r * math.sin(a))


def _wave(y0, amp, periods=1.5, half=0.84):
    return _poly([(-half + 2 * half * i / 60.0,
                   y0 + amp * math.sin(periods * 2 * math.pi * i / 60.0)) for i in range(61)])


def _build_glyphs():
    RO, RI, RD, WO, WI = 0.84, 0.42, 0.25, 0.21, 0.19
    ring = _arc(RO, 0, 360, WO)
    dome = _arc(RO, 0, 180, WO)
    cap_n = _arc(RO, 45, 135, WO)
    cap_s = _arc(RO, 225, 315, WO)
    inner = _arc(RI, 0, 360, WI)
    seed = _dot(0, 0, RD)

    def spoke(deg, r0=0.0):
        return _line(*_pol(r0, deg), *_pol(RO, deg), WI)

    spiral = _poly([((0.10 + 0.74 * i / 120.0) * math.cos(math.pi / 2 - i / 120.0 * 3.2 * math.pi),
                     (0.10 + 0.74 * i / 120.0) * math.sin(math.pi / 2 - i / 120.0 * 3.2 * math.pi))
                    for i in range(121)])
    rho, off = 1.06, 0.56
    la = math.degrees(math.asin(0.9 / rho))
    m1 = 0.82 * math.sin(math.radians(55)) / math.sin(math.radians(75))
    m2 = 0.82 * math.cos(math.radians(55)) + 0.82 * math.sin(math.radians(55)) / math.tan(math.radians(75))
    cap_e = _arc(RO, -45, 45, WO)
    cap_w = _arc(RO, 135, 225, WO)
    cup = _arc(RO, 180, 360, WO)
    bowl = _arc(RO, 135, 405, WO)
    return [
        ("ring", [ring]),
        ("cross", [spoke(0), spoke(90), spoke(180), spoke(270)]),
        ("span", [_line(-0.72, 0, 0.72, 0), _line(-0.72, -0.52, -0.72, 0.52), _line(0.72, -0.52, 0.72, 0.52)]),
        ("four seeds", [_dot(*_pol(0.6, d), 0.25) for d in (0, 90, 180, 270)]),
        ("wave", [_wave(0, 0.42)]),
        ("double wave", [_wave(0.36, 0.20), _wave(-0.36, 0.20)]),
        ("float", [_wave(-0.38, 0.22), _dot(0, 0.42, 0.27)]),
        ("spiral", [spiral]),
        ("leaf", [_arc(rho, -la, la, cx=-off), _arc(rho, 180 - la, 180 + la, cx=off)]),
        ("moon", [_arc(0.82, 55, 305), _arc(m1, 105, 255, cx=m2)]),
        ("bud", [_arc(0.5, 0, 360, cy=0.3), _line(0, -0.2, 0, -0.86)]),
        ("pod", [_arc(RO, 0, 360), _dot(-0.34, 0, 0.22), _dot(0.34, 0, 0.22)]),
        ("husk", [cap_e, cap_w]),
        ("bowl", [bowl]),
        ("cradle", [bowl, seed]),
        ("pearl", [cap_n, cap_s, seed]),
        ("canopy", [dome, spoke(270)]),
        ("tent", [dome, spoke(90)]),
        ("sunrise", [dome, spoke(0), spoke(180), seed]),
        ("burrow", [_arc(0.80, 0, 180, cy=-0.3), _dot(-0.42, -0.62, 0.22), _dot(0.42, -0.62, 0.22)]),
        ("rainbow", [_arc(0.80, 0, 180, cy=-0.38), _arc(0.46, 0, 180, cy=-0.38)]),
        ("pin", [cap_n, seed, spoke(90)]),
        ("anchor", [cap_s, spoke(90), spoke(270)]),
        ("lamp", [_arc(RO, -45, 225, WO), inner, spoke(90, RI)]),
        ("keel", [cap_s, spoke(270), spoke(0), spoke(180), seed]),
        ("boat", [cup, inner, spoke(0, RI), spoke(180, RI)]),
    ]


GLYPHS = _build_glyphs()                       # index 0-25 = glyph number 1-26 on the card
SPACE = [_dot(0, 0, 0.21)]                     # word space
_TRI = ((-0.42, 0.55), (0.55, 0.0), (-0.42, -0.55))             # clockwise, points the reading direction
START = [("area", _TRI), _poly(_TRI + (_TRI[0],), 0.30)]
CLS_SPACE, CLS_START = 26, 27
ALL_MARKS = [g for _, g in GLYPHS] + [SPACE, START]


def glyph_raster(prims, n=36, ext=1.3, ss=3):
    """Anti-aliased coverage image of a glyph (row 0 = top). Used for decoding."""
    m = n * ss
    c = (np.arange(m) + 0.5) / m * 2 * ext - ext
    X, Y = np.meshgrid(c, -c)
    px = 2 * ext / m
    cov = np.zeros((m, m))

    def seg(x0, y0, x1, y1):
        dx, dy = x1 - x0, y1 - y0
        t = np.clip(((X - x0) * dx + (Y - y0) * dy) / (dx * dx + dy * dy + 1e-12), 0, 1)
        return np.hypot(X - (x0 + t * dx), Y - (y0 + t * dy))

    for p in prims:
        kind = p[0]
        if kind == "dot":
            d = np.hypot(X - p[1], Y - p[2]) - p[3]
        elif kind == "line":
            d = seg(*p[1:5]) - p[5] / 2
        elif kind == "area":
            d = np.full_like(X, -9.0)
            pts = p[1]
            for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
                ln = math.hypot(x1 - x0, y1 - y0)
                d = np.maximum(d, ((X - x0) * (y1 - y0) - (Y - y0) * (x1 - x0)) / ln * -1.0)
        elif kind == "poly":
            d = np.full_like(X, 9.0)
            for (x0, y0), (x1, y1) in zip(p[1][:-1], p[1][1:]):
                d = np.minimum(d, seg(x0, y0, x1, y1))
            d = d - p[2] / 2
        else:
            _, cx, cy, r, a0, a1, w = p
            Xc, Yc = X - cx, Y - cy
            rad = np.hypot(Xc, Yc)
            if a1 - a0 >= 360:
                d = np.abs(rad - r) - w / 2
            else:
                ang = np.degrees(np.arctan2(Yc, Xc))
                inside = ((ang - a0) % 360) <= (a1 - a0)
                e0, e1 = _pol(r, a0), _pol(r, a1)
                d_end = np.minimum(np.hypot(Xc - e0[0], Yc - e0[1]), np.hypot(Xc - e1[0], Yc - e1[1]))
                d = np.where(inside, np.abs(rad - r), d_end) - w / 2
        cov = np.maximum(cov, np.clip(0.5 - d / px, 0, 1))
    return cov.reshape(n, ss, n, ss).mean(axis=(1, 3))


def draw_glyph(ax, prims, x, y, radius, phi, color, page_h, zorder=6):
    """Draw a glyph as vector strokes. (x, y) in page mm with y down; phi is
    the clockwise angle from 'up' that the glyph's top points to."""
    cs, sn = math.cos(phi), math.sin(phi)

    def tr(gx, gy):
        return (x + radius * (gx * cs + gy * sn), page_h - (y + radius * (gx * sn - gy * cs)))

    for p in prims:
        kind = p[0]
        if kind == "dot":
            ax.add_patch(Circle(tr(p[1], p[2]), p[3] * radius, fc=color, ec="none", zorder=zorder))
            continue
        if kind == "area":
            ax.add_patch(Polygon([tr(a, b) for a, b in p[1]], closed=True, fc=color, ec="none", zorder=zorder))
            continue
        if kind == "line":
            pts, w = [tr(p[1], p[2]), tr(p[3], p[4])], p[5]
        elif kind == "poly":
            pts, w = [tr(a, b) for a, b in p[1]], p[2]
        else:
            _, cx, cy, r, a0, a1, w = p
            if a1 - a0 >= 360:
                ax.add_patch(Circle(tr(cx, cy), r * radius, fc="none", ec=color, lw=w * radius * MM, zorder=zorder))
                continue
            steps = max(8, int((a1 - a0) / 3))
            pts = [tr(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / steps)),
                      cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / steps))) for i in range(steps + 1)]
        ax.add_line(Line2D([q[0] for q in pts], [q[1] for q in pts], color=color, lw=w * radius * MM,
                           solid_capstyle="round", solid_joinstyle="round", zorder=zorder))


# ----------------------------------------------------------------------------
# 4. layout (page millimetres, origin top-left, y down)
# ----------------------------------------------------------------------------
RING_R = (97.0, 85.5, 74.0)              # radii of the glyph rings, outermost first (millimetres on A3)
ART_R = {1: 80.0, 2: 71.5, 3: 61.0}      # how far out the growth rings reach, by number of glyph rings
CROSS_R = {1: 88.0, 2: 78.5, 3: 67.2}    # where the crosshair stops short of the innermost glyph ring
ONE_RING_MAX = 60                        # longest message kept in a single ring
MIN_PITCH = 10.0                         # glyph centres never sit closer than this; below it, add a ring


def glyph_radius(n_slots, u):
    """Glyph size when the message sits in a single ring."""
    pitch = 2 * math.pi * RING_R[0] * u / n_slots
    return min(5.6 * u, 0.40 * pitch)


def ring_count(n_chars):
    """How many glyph rings a message of n characters needs."""
    if n_chars <= ONE_RING_MAX:
        return 1
    for k in (2, 3):
        if 2 * math.pi * sum(RING_R[:k]) / (n_chars + k) >= MIN_PITCH:
            return k
    return 3


def ring_slots(n_chars):
    """Slots in each ring, outermost first. Every ring is a full circle: its own start
    triangle, then its share of the message. The shares are in proportion to the rings'
    circumferences, so the glyphs are evenly pitched throughout."""
    k = ring_count(n_chars)
    total = n_chars + k
    if k == 1:
        return [total]
    quota = [total * r / sum(RING_R[:k]) for r in RING_R[:k]]
    slots = [int(q) for q in quota]
    for i in sorted(range(k), key=lambda i: (-(quota[i] - slots[i]), i))[:total - sum(slots)]:
        slots[i] += 1
    return slots


def glyph_size(slots, u):
    """Glyph radius, the same in every ring."""
    if len(slots) == 1:
        return glyph_radius(slots[0], u)
    pitch = min(2 * math.pi * RING_R[i] * u / n for i, n in enumerate(slots))
    return min(4.4 * u, 0.40 * pitch)


def layout(W, H, slots, n_rings=1):
    u = min(W / 297.0, H / 420.0)
    cx, cy = W / 2.0, H / 2.0 - 40.0 * u
    n = len(slots)
    pitch = min(18.0 * u, 215.0 * u / max(n, 1))
    xs = [cx + (i - (n - 1) / 2.0) * pitch for i in range(n)]
    rf = 121.0 * u
    return dict(
        u=u, W=W, H=H, cx=cx, cy=cy,
        r_ring0=8.0 * u, r_ring1=ART_R[n_rings] * u, r_cross=CROSS_R[n_rings] * u,
        r_glyph=RING_R[0] * u, r_rings=[r * u for r in RING_R],
        r_tick_in=108.0 * u, r_tick_out=114.0 * u,
        r_fid=rf, fid_r=1.6 * u,
        fiducials=[(cx, cy - rf), (cx + rf, cy), (cx, cy + rf), (cx - rf, cy)],   # N E S W
        name_pitch=pitch, name_xs=xs, name_em=pitch / 0.9,
        clue_y=cy + 148.0 * u, clue_r=min(4.2 * u, 0.30 * pitch),
        name_y=cy + 177.0 * u, date_y=cy + 195.0 * u,
        margin=20.0 * u,
    )


def ring_slot(L, k, n_slots, ring=0):
    """Centre and rotation of slot k of a ring (slot 0 = start mark at 12 o'clock, then clockwise)."""
    phi = 2 * math.pi * k / n_slots
    r = L["r_rings"][ring]
    return L["cx"] + r * math.sin(phi), L["cy"] - r * math.cos(phi), phi


# ----------------------------------------------------------------------------
# 5. the artwork
# ----------------------------------------------------------------------------
class Rng:
    """Tiny deterministic generator (splitmix64) seeded from the inputs."""
    M = (1 << 64) - 1

    def __init__(self, text):
        self.s = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")

    def uniform(self, a=0.0, b=1.0):
        self.s = (self.s + 0x9E3779B97F4A7C15) & self.M
        z = self.s
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & self.M
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & self.M
        z ^= z >> 31
        return a + (b - a) * ((z >> 11) / float(1 << 53))


def ring_model(spec, L):
    """One bundle of wavy growth rings per letter of the name.
    - bundles run from the centre outwards in the order of the letters
    - total number of rings = weight in hundreds of grams (a long name may need a few more,
      so that every letter still gets a visible bundle)
    - a letter late in the alphabet gets a thicker bundle
    - longitude, latitude and day-of-year set the phase of the three slow waves
    Returns (bundles, colours, ring spacing); each bundle is a list of closed curves (x, y)."""
    u = L["u"]
    letters = spec.letters
    n = len(letters)
    rng = Rng(spec.art_seed())
    pos = [ord(c) - 96 for c in letters]
    gapf = max(1.4, 2.3 - 0.08 * max(0, n - 6))            # long names: tighter gaps between bundles
    total = max(3 * n if n > 6 else 2 * n, min(64, int(round(spec.grams / 100.0))))
    room = round((L["r_ring1"] - L["r_ring0"]) / u * 56.0 / 72.0, 6)
    total = max(2 * n, min(total, int(room - gapf * (n - 1) + n)))  # never pack the rings too densely to print
    spare = total - 2 * n
    quota = [spare * p / float(sum(pos)) for p in pos]
    extra = [int(q) for q in quota]
    for i in sorted(range(n), key=lambda i: (-(quota[i] - extra[i]), i))[:spare - sum(extra)]:
        extra[i] += 1
    counts = [2 + e for e in extra]

    r0, r1 = L["r_ring0"], L["r_ring1"]
    step = (r1 - r0) / (sum(c - 1 for c in counts) + gapf * (n - 1))
    doy = datetime.date(int(spec.date[:4]), int(spec.date[4:6]), int(spec.date[6:])).timetuple().tm_yday
    slow = []
    for k, amp, ph in ((2, 0.030, math.radians(spec.lon)), (3, 0.022, math.radians(spec.lat)),
                       (5, 0.012, 2 * math.pi * doy / 365.25)):
        slow.append((k, amp * rng.uniform(0.65, 1.15), ph, rng.uniform(-0.9, 0.9)))

    theta = np.linspace(0, 2 * math.pi, 1081)
    bundles = []
    t = r0
    for i in range(n):
        m = counts[i]
        width = (m - 1) * step
        t_mid = t + width / 2.0
        lam = (12.0 + 2.2 * ((pos[i] * 5) % 13)) * u
        k_i = max(3, int(round(2 * math.pi * t_mid / lam)))
        amp_i = min(0.17 * max(width, 2 * step), 2 * math.pi * t_mid / k_i / 8.0, 2.6 * u)
        ph_i = rng.uniform(0, 2 * math.pi)
        tw_i = rng.uniform(-0.7, 0.7)
        curves = []
        for j in range(m):
            s = j / float(m - 1)
            tn = t + j * step
            r = np.full_like(theta, tn)
            for k, amp, ph, tw in slow:
                r = r + amp * tn * np.sin(k * theta + ph + tw * tn / r1)
            r = r + amp_i * (0.3 + 0.7 * math.sin(math.pi * s)) * np.sin(k_i * theta + ph_i + tw_i * (s - 0.5))
            curves.append((L["cx"] + r * np.sin(theta), L["cy"] - r * np.cos(theta)))
        bundles.append(curves)
        t += width + gapf * step
    start = sum(pos) % len(PALETTE)
    colors = [PALETTE[(start + i) % len(PALETTE)] for i in range(n)]
    return bundles, colors, step


STYLES = {1: "contour", 2: "solid", 3: "drafted"}


def draw_rings(ax, spec, L, style):
    u, H, cx, cy = L["u"], L["H"], L["cx"], L["cy"]
    bundles, colors, step = ring_model(spec, L)

    def band(a, b, da, db, fill):
        """Filled strip between closed curves a (inner) and b (outer), each nudged radially by da / db mm."""
        def nudge(c, d):
            x, y = c
            r = np.hypot(x - cx, y - cy)
            k = (r + d) / r
            return cx + (x - cx) * k, cy + (y - cy) * k
        (xa, ya), (xb, yb) = nudge(a, da), nudge(b, db)
        verts = np.concatenate([np.column_stack([xb, H - yb]), np.column_stack([xa[::-1], H - ya[::-1]])])
        codes = np.full(len(verts), Path.LINETO)
        codes[0] = codes[len(xb)] = Path.MOVETO
        ax.add_patch(PathPatch(Path(verts, codes), fc=fill, ec="none", zorder=2))

    for curves, col in zip(bundles, colors):
        m = len(curves)
        if style == 2:
            # solid ink with the inner rings left as bare paper: nothing is printed in the gaps,
            # so it works the same on white paper (cream printed) and on cream paper stock
            half = min(0.25 * u, 0.2 * step)
            for j in range(m - 1):
                band(curves[j], curves[j + 1], half if j > 0 else 0.0, -half if j + 1 < m - 1 else 0.0, col)
            continue
        if style == 3:
            band(curves[0], curves[-1], 0.0, 0.0, TINTS[col])
        for j, (x, y) in enumerate(curves):
            edge = j in (0, m - 1)
            if style == 1:
                ax.add_line(Line2D(x, H - y, color=col, lw=(1.05 if edge else 0.42) * u * MM, zorder=3,
                                   solid_joinstyle="round"))
            elif edge:
                ax.add_line(Line2D(x, H - y, color=col, lw=0.95 * u * MM, zorder=3))
            else:
                dash = [(None, None), (7.0, 2.2), (1.0, 2.0), (7.0, 2.0, 1.0, 2.0)][j % 4]
                ln = Line2D(x, H - y, color=col, lw=0.40 * u * MM, zorder=3, dash_capstyle="butt")
                if dash[0] is not None:
                    ln.set_dashes([d * u * MM * 0.42 for d in dash])
                ax.add_line(ln)


def _fonts(serif=None, mono=None):
    base = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    serif = serif or os.path.join(base, "STIXGeneral.ttf")
    mono = mono or os.path.join(base, "DejaVuSansMono.ttf")
    return font_manager.FontProperties(fname=serif), font_manager.FontProperties(fname=mono)


def draw_technical(ax, spec, L, fonts):
    """Tick ring, crosshair, registration dots, corner read-outs, name, date."""
    u, W, H, cx, cy = L["u"], L["W"], L["H"], L["cx"], L["cy"]
    serif, mono = fonts
    soft = _mix(PAPER, INK, 0.62)

    def P(r, phi):
        return (cx + r * math.sin(phi), H - (cy - r * math.cos(phi)))

    # tick ring: a 24-hour dial, one tick every ten minutes
    segs, lws = [], []
    for i in range(144):
        phi = 2 * math.pi * i / 144.0
        if i % 36 == 0:
            ln, lw = 6.0, 0.36
        elif i % 6 == 0:
            ln, lw = 4.0, 0.28
        else:
            ln, lw = 2.0, 0.18
        segs.append([P(L["r_tick_out"], phi), P(L["r_tick_out"] - ln * u, phi)])
        lws.append(lw * u * MM)
    ax.add_collection(LineCollection(segs, colors=INK, linewidths=lws, capstyle="butt", zorder=4))
    ax.add_patch(Circle((cx, H - cy), L["r_tick_out"], fc="none", ec=INK, lw=0.28 * u * MM, zorder=4))
    # the moment of birth on the dial
    minutes = int(spec.hhmm[:2]) * 60 + int(spec.hhmm[2:])
    phi_t = 2 * math.pi * minutes / 1440.0
    a, b = P(L["r_tick_in"] - 2.2 * u, phi_t), P(L["r_tick_out"] + 2.4 * u, phi_t)
    ax.add_line(Line2D([a[0], b[0]], [a[1], b[1]], color=SIGNAL, lw=0.9 * u * MM, solid_capstyle="butt", zorder=5))
    a, b = P(3.2 * u, phi_t), P(L["r_cross"], phi_t)
    ax.add_line(Line2D([a[0], b[0]], [a[1], b[1]], color=SIGNAL, lw=0.30 * u * MM, zorder=5))
    # crosshair (broken where the glyph ring passes) and its four registration dots
    for q in range(4):
        phi = q * math.pi / 2
        for ra, rb in ((3.2 * u, L["r_cross"]), (L["r_tick_in"] - 2.0 * u, L["r_fid"] - 3.4 * u)):
            a, b = P(ra, phi), P(rb, phi)
            ax.add_line(Line2D([a[0], b[0]], [a[1], b[1]], color=INK, lw=0.20 * u * MM, zorder=4))
    ax.add_patch(Circle((cx, H - cy), 1.5 * u, fc="none", ec=INK, lw=0.20 * u * MM, zorder=4))
    for fx, fy in L["fiducials"]:
        ax.add_patch(Circle((fx, H - fy), L["fid_r"], fc=INK, ec="none", zorder=4))

    # corner read-outs
    def readout(x, y, ha, label, value):
        ax.text(x, H - (y - 5.6 * u), label, ha=ha, va="baseline", fontproperties=mono,
                fontsize=2.3 * u * MM, color=soft, zorder=6)
        ax.text(x, H - y, value, ha=ha, va="baseline", fontproperties=mono,
                fontsize=3.7 * u * MM, color=INK, zorder=6)

    mg = L["margin"]
    readout(mg, mg + 6 * u, "left", "LAT", spec.lat_text + "°")
    readout(W - mg, mg + 6 * u, "right", "LON", spec.lon_text + "°")
    readout(mg, H - mg, "left", "TIME", "%s:%s" % (spec.hhmm[:2], spec.hhmm[2:]))
    readout(W - mg, H - mg, "right", "WEIGHT", "%d g" % spec.grams)

    # name (lowercase serif), its glyph clue directly above, date beneath
    for (ch, let), x in zip(spec.slots, L["name_xs"]):
        ax.text(x, H - L["name_y"], ch, ha="center", va="baseline", fontproperties=serif,
                fontsize=L["name_em"] * MM, color=INK, zorder=6)
        if let:
            draw_glyph(ax, GLYPHS[spec.perm[ord(let) - 97]][1], x, L["clue_y"], L["clue_r"], 0.0, INK, H)
    d = spec.date
    ax.text(cx, H - L["date_y"], "%s · %s · %s" % (d[:4], d[4:6], d[6:]), ha="center", va="baseline",
            fontproperties=mono, fontsize=4.4 * u * MM, color=INK, zorder=6)


def draw_message_ring(ax, spec, L):
    msg = spec.message
    slots = ring_slots(len(msg))
    g = glyph_size(slots, L["u"])
    i = 0
    for ring, n_slots in enumerate(slots):
        x, y, phi = ring_slot(L, 0, n_slots, ring)
        draw_glyph(ax, START, x, y, g, phi, INK, L["H"])
        for k in range(1, n_slots):
            ch = msg[i]
            i += 1
            x, y, phi = ring_slot(L, k, n_slots, ring)
            prims = SPACE if ch == " " else GLYPHS[spec.perm[ord(ch) - 97]][1]
            draw_glyph(ax, prims, x, y, g, phi, INK, L["H"])


def new_page(W, H, color=PAPER, bleed=0.0):
    fig = plt.figure(figsize=((W + 2 * bleed) / 25.4, (H + 2 * bleed) / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(-bleed, W + bleed)
    ax.set_ylim(-bleed, H + bleed)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor(color)
    return fig, ax


_PDF_META = {"Creator": "birthprint.py " + VERSION, "Producer": "birthprint.py", "CreationDate": None,
             "Title": "birth print"}


def _compose(spec, style, fonts, bleed=0.0):
    L = layout(spec.W, spec.H, spec.slots, ring_count(len(spec.message)))
    fig, ax = new_page(spec.W, spec.H, PAPER, bleed)
    draw_rings(ax, spec, L, style)
    draw_technical(ax, spec, L, fonts)
    draw_message_ring(ax, spec, L)
    return fig


def render_print(spec, style=2, pdf_path=None, png_path=None, dpi=150, fonts=None, background=True, bleed=0.0):
    """Draw the print. The PDF is vector at print size (plus bleed on every side if asked,
    and with no cream fill if background=False, for printing on cream paper stock).
    The PNG preview always shows the trimmed sheet on cream. Returns a PIL image if no png_path."""
    fonts = fonts or _fonts()
    fig = _compose(spec, style, fonts)
    if pdf_path:
        if bleed:
            fb = _compose(spec, style, fonts, bleed)
        else:
            fb = fig
        if background:
            fb.savefig(pdf_path, format="pdf", facecolor=PAPER, metadata=_PDF_META)
        else:
            fb.savefig(pdf_path, format="pdf", facecolor="none", transparent=True, metadata=_PDF_META)
        if fb is not fig:
            plt.close(fb)
    img = None
    if png_path:
        fig.savefig(png_path, format="png", dpi=dpi, facecolor=PAPER, metadata={"Software": None})
    else:
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=dpi, facecolor=PAPER, metadata={"Software": None})
        buf.seek(0)
        img = Image.open(buf).convert("RGB")
    plt.close(fig)
    return img


# ----------------------------------------------------------------------------
# 6. reading a photograph back (used by --verify and --decode)
# ----------------------------------------------------------------------------
PATCH_N, PATCH_EXT = 36, 1.3
RING_FIT_MIN = 0.80        # how well an inner circle must match glyphs to count as a ring of them


def _gauss(a, sigma):
    k = int(3 * sigma) + 1
    x = np.arange(-k, k + 1)
    g = np.exp(-x * x / (2.0 * sigma * sigma))
    g /= g.sum()
    a = np.apply_along_axis(lambda v: np.convolve(v, g, mode="same"), 0, a)
    return np.apply_along_axis(lambda v: np.convolve(v, g, mode="same"), 1, a)


def _unit(a):
    a = a - a.mean(axis=-1, keepdims=True)
    return a / np.sqrt((a * a).sum(axis=-1, keepdims=True) + 1e-9)


_TEMPLATES = None
BLUR_LEVELS = (0.05, 0.09, 0.13, 0.17, 0.21, 0.26)     # template softness, in glyph radii


def templates():
    """Each mark at several blur levels, as unit vectors: shape (levels, 28, N*N)."""
    global _TEMPLATES
    if _TEMPLATES is None:
        px = 2 * PATCH_EXT / PATCH_N
        raw = [glyph_raster(p, PATCH_N, PATCH_EXT) for p in ALL_MARKS]
        _TEMPLATES = np.stack([np.stack([_unit(_gauss(r, sig / px).ravel()) for r in raw]) for sig in BLUR_LEVELS])
    return _TEMPLATES


def homography(src, dst):
    A, b = [], []
    for (x, y), (p, q) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -p * x, -p * y]); b.append(p)
        A.append([0, 0, 0, x, y, 1, -q * x, -q * y]); b.append(q)
    h = np.linalg.solve(np.array(A, float), np.array(b, float))
    return np.append(h, 1.0).reshape(3, 3)


def warp(Hm, x, y):
    d = Hm[2, 0] * x + Hm[2, 1] * y + Hm[2, 2]
    return (Hm[0, 0] * x + Hm[0, 1] * y + Hm[0, 2]) / d, (Hm[1, 0] * x + Hm[1, 1] * y + Hm[1, 2]) / d


def bilinear(img, x, y):
    h, w = img.shape
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0 = np.floor(x).astype(np.intp)
    y0 = np.floor(y).astype(np.intp)
    fx, fy = x - x0, y - y0
    return (img[y0, x0] * (1 - fx) * (1 - fy) + img[y0, x0 + 1] * fx * (1 - fy)
            + img[y0 + 1, x0] * (1 - fx) * fy + img[y0 + 1, x0 + 1] * fx * fy)


def find_sheet(photo):
    """Corners (TL, TR, BR, BL) of the cream sheet in a photo, in pixels.
    Looks for the largest bright region; if it fills the frame the image is
    taken to be the sheet itself (a scan or the preview PNG)."""
    w, h = photo.size
    f = max(w, h) / 360.0
    small = photo.convert("L").resize((max(8, int(round(w / f))), max(8, int(round(h / f)))), Image.BOX)
    a = np.asarray(small, float)
    hist = np.bincount(a.astype(int).ravel(), minlength=256).astype(float)
    tot, s_all = hist.sum(), (hist * np.arange(256)).sum()
    best, thr, w0, s0 = -1.0, 128, 0.0, 0.0
    for t in range(255):
        w0 += hist[t]; s0 += hist[t] * t
        if w0 == 0 or w0 == tot:
            continue
        m0, m1 = s0 / w0, (s_all - s0) / (tot - w0)
        v = w0 * (tot - w0) * (m0 - m1) ** 2
        if v > best:
            best, thr = v, t
    mask = a > thr
    hh, ww = mask.shape
    if mask.mean() > 0.80 or a.std() < 12:
        return [(0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)]
    # the light is rarely even: fit a smooth brightness surface to the paper found so
    # far, divide it out, and threshold again so that a dim corner is not lost
    yy, xx = np.mgrid[0:hh, 0:ww]
    X, Y = xx / float(ww) - 0.5, yy / float(hh) - 0.5
    basis = np.stack([np.ones_like(X), X, Y, X * X, X * Y, Y * Y], axis=-1)
    keep = mask.copy()
    for _ in range(3):
        coef = np.linalg.lstsq(basis[keep], a[keep], rcond=None)[0]
        surface = basis @ coef
        surface = np.maximum(surface, 0.6 * np.median(surface[keep]))    # never trust the fit far off the paper
        keep = mask & (a > 0.88 * surface)
        if keep.sum() < 50:
            break
    else:
        mask = a > 0.62 * surface
    seen = np.zeros_like(mask)
    best_comp = []
    for sy, sx in zip(*np.nonzero(mask)):
        if seen[sy, sx]:
            continue
        stack, comp = [(sy, sx)], []
        seen[sy, sx] = True
        while stack:
            y, x = stack.pop()
            comp.append((y, x))
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < hh and 0 <= nx < ww and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        if len(comp) > len(best_comp):
            best_comp = comp
    ys = np.array([c[0] for c in best_comp], float)
    xs = np.array([c[1] for c in best_comp], float)
    idx = [np.argmin(xs + ys), np.argmax(xs - ys), np.argmax(xs + ys), np.argmin(xs - ys)]
    sx, sy = w / float(ww), h / float(hh)
    return [((xs[i] + 0.5) * sx, (ys[i] + 0.5) * sy) for i in idx]


def find_registration(ink, H0, L, half_mm=13.0):
    """Refine the four registration dots; returns mm->pixel homography built on them."""
    u = L["u"]
    step, half = 0.25 * u, half_mm * u
    offs = np.arange(-half, half + step / 2, step)
    kr = int(math.ceil(2.9 * u / step))
    ky, kx = np.mgrid[-kr:kr + 1, -kr:kr + 1]
    kd = np.hypot(ky, kx) * step
    disc = (kd <= L["fid_r"]).astype(float)
    annulus = ((kd > L["fid_r"] * 1.15) & (kd <= 2.9 * u)).astype(float)
    kern = disc / disc.sum() - annulus / annulus.sum()
    pts = []
    for fx, fy in L["fiducials"]:
        X, Y = np.meshgrid(fx + offs, fy + offs)
        win = bilinear(ink, *warp(H0, X, Y))
        n = win.shape[0] - 2 * kr
        resp = np.zeros((n, n))
        for i in range(2 * kr + 1):
            for j in range(2 * kr + 1):
                if kern[i, j] != 0.0:
                    resp += kern[i, j] * win[i:i + n, j:j + n]
        iy, ix = np.unravel_index(np.argmax(resp), resp.shape)
        py, px = iy + kr, ix + kr
        yy, xx = np.mgrid[0:win.shape[0], 0:win.shape[1]]
        near = np.hypot(yy - py, xx - px) * step <= 2.5 * u
        bg = np.median(win[np.hypot(yy - py, xx - px) * step > 3.0 * u])
        wgt = np.clip(win - (bg + 0.4 * (win[near].max() - bg)), 0, None) * near
        if wgt.sum() <= 0:
            return None
        mx = fx + offs[0] + step * (wgt * xx).sum() / wgt.sum()
        my = fy + offs[0] + step * (wgt * yy).sum() / wgt.sum()
        pts.append(warp(H0, mx, my))
    return homography(L["fiducials"], pts)


def _patches(ink, Hm, centres, radius, dx=0.0, dy=0.0):
    """Sample upright N x N patches for glyph cells. centres = [(x, y, phi)]."""
    c = (np.arange(PATCH_N) + 0.5) / PATCH_N * 2 * PATCH_EXT - PATCH_EXT
    GX, GY = np.meshgrid(c + dx, -c + dy)
    cen = np.array(centres, float)
    cs, sn = np.cos(cen[:, 2])[:, None, None], np.sin(cen[:, 2])[:, None, None]
    X = cen[:, 0][:, None, None] + radius * (GX[None] * cs + GY[None] * sn)
    Y = cen[:, 1][:, None, None] + radius * (GX[None] * sn - GY[None] * cs)
    v = bilinear(ink, *warp(Hm, X, Y))
    return v.reshape(len(centres), -1)


def _classify(ink, Hm, centres, radius, jitter=0.0, allow=None):
    """Best-matching mark for each cell. Returns (class, score, margin, runner-up).
    The photo's softness is estimated once for all cells (the template blur level
    that fits best overall); each cell may also slide a little to settle on its mark."""
    T = templates()
    shifts = [(0.0, 0.0)]
    if jitter:
        shifts += [(a * jitter, b * jitter) for a in (-1, 0, 1) for b in (-1, 0, 1) if (a, b) != (0, 0)]
    P = np.stack([_patches(ink, Hm, centres, radius, dx, dy) for dx, dy in shifts])      # shifts, cells, N*N
    flat = P.std(axis=2) < 0.012
    S = np.einsum("spn,lcn->lspc", _unit(P), T, optimize=True)                           # levels, shifts, cells, classes
    S[:, flat] = 0.0
    if allow is not None:
        S[:, :, ~np.asarray(allow, bool)] = -1.0          # cells x classes that may not be chosen
    level = int(S.max(axis=3).max(axis=1).sum(axis=1).argmax())
    Sl = S[level]
    pick = Sl.max(axis=2).argmax(axis=0)                                                 # best shift per cell
    sc = Sl[pick, np.arange(len(centres))]                                               # cells, classes
    order = np.argsort(sc, axis=1)
    rows = np.arange(len(centres))
    return order[:, -1], sc[rows, order[:, -1]], sc[rows, order[:, -1]] - sc[rows, order[:, -2]], order[:, -2]


def decode_photo(photo, spec, verbose=False):
    """Read the message ring or rings (and the name clue) back out of a photo of the print.
    spec carries the four key blocks and the print size; its message is not used."""
    photo = photo.convert("RGB")
    L = layout(spec.W, spec.H, spec.slots)
    u = L["u"]
    corners = find_sheet(photo)
    H0 = homography([(0, 0), (spec.W, 0), (spec.W, spec.H), (0, spec.H)], corners)
    arr = np.asarray(photo, float) / 255.0
    ink_img = Image.fromarray(np.uint8(np.clip((1.0 - arr.min(axis=2)) * 255.0, 0, 255)))
    ppm = math.hypot(corners[1][0] - corners[0][0], corners[1][1] - corners[0][1]) / spec.W   # pixels per mm
    ink = np.asarray(ink_img, float) / 255.0
    H1 = find_registration(ink, H0, L)                 # wide search from the sheet corners ...
    if H1 is not None:
        H2 = find_registration(ink, H1, L, 5.0)        # ... then a tight second pass
        H1 = H2 if H2 is not None else H1
    registered = H1 is not None
    if H1 is None:
        H1 = H0
    info = dict(registered=registered, px_per_mm=ppm)
    blurred = {}

    def source(cell_radius):
        """The ink image, pre-smoothed so that sampling a patch does not alias."""
        step_px = 2 * PATCH_EXT * cell_radius / PATCH_N * ppm
        key = int(round(0.45 * step_px * 2)) if step_px > 1.3 else 0      # half-pixel buckets
        if key not in blurred:
            blurred[key] = ink if key == 0 else np.asarray(
                ink_img.filter(ImageFilter.GaussianBlur(key / 2.0)), float) / 255.0
        return blurred[key]

    def ring_fit(ring, lo, hi, multi):
        """How many slots does this ring have? Try every count, keep the best fit.
        Returns (slots, fit 0..1, slot 0 looks like the start triangle)."""
        scores, starts = {}, {}
        for n in range(lo, hi + 1):
            g = min((4.4 if multi else 5.6) * u, 0.40 * 2 * math.pi * L["r_rings"][ring] / n)
            cells = [ring_slot(L, k, n, ring) for k in range(n)]
            cls, sc, _, _ = _classify(source(g), H1, cells, g)
            starts[n] = bool(cls[0] == CLS_START)
            scores[n] = float(sc.mean()) + (0.02 if starts[n] else 0.0)
        top = max(scores.values())
        best = max(n for n, v in scores.items() if v >= top - 0.07)
        return best, scores[best], starts[best]

    # is there a second ring of glyphs inside the first, and a third inside that?
    cap = [int(2 * math.pi * r / MIN_PITCH) + 2 for r in RING_R]
    n2, f2, s2 = ring_fit(1, 26, cap[1], True)
    multi = f2 >= RING_FIT_MIN and s2
    slots = [0]
    if multi:
        slots.append(n2)
        n3, f3, s3 = ring_fit(2, 30, cap[2], True)
        if f3 >= RING_FIT_MIN and s3:
            slots.append(n3)
    slots[0], fit, _ = ring_fit(0, 2, cap[0], multi)
    g = glyph_size(slots, u) if multi else glyph_radius(slots[0], u)
    text, glyphs, margins, runners, scores_all = "", [], [], [], []
    for ring, n_slots in enumerate(slots):
        cells = [ring_slot(L, k, n_slots, ring) for k in range(n_slots)]
        allow = np.ones((n_slots, 28), bool)
        allow[0, :] = False
        allow[0, CLS_START] = True                # slot 0 is always the start mark ...
        allow[1:, CLS_START] = False              # ... and nothing else ever is
        cls, sc, margin, runner = _classify(source(g), H1, cells, g, jitter=0.14, allow=allow)
        text += "".join(" " if c == CLS_SPACE else chr(97 + spec.inv[c]) for c in cls[1:])
        glyphs += [int(c) for c in cls]
        margins += [9.0] + [float(m) for m in margin[1:]]
        runners += [int(c) for c in runner]
        scores_all += [float(v) for v in sc]
    info.update(n_slots=slots[0], slots=slots, min_score=min(scores_all),
                min_margin=min(margins), glyphs=glyphs, margins=margins, fit=fit, runner=runners,
                inner_fit=(float(f2), float(f3) if multi else None))
    # the clue above the name: a second, independent check of the key
    cl = [(x, L["clue_y"], 0.0) for (ch, let), x in zip(spec.slots, L["name_xs"]) if let]
    allow = np.ones((len(cl), 28), bool)
    allow[:, 26:] = False
    ccls, _, cmargin, _ = _classify(source(L["clue_r"]), H1, cl, L["clue_r"], jitter=0.2, allow=allow)
    info["clue"] = "".join(chr(97 + spec.inv[c]) if c < 26 else "?" for c in ccls)
    info["clue_margin"] = float(cmargin.min())
    return text, info


# ----------------------------------------------------------------------------
# 7. a simulated phone photo (for the robustness test)
# ----------------------------------------------------------------------------
TIERS = {
    # name: (photo size, corner wobble, tilt deg, blur sigma px, motion px, noise, jpeg quality, light falloff)
    "easy": ((1800, 2400), 0.03, 2.0, (0.8, 1.3), 0, 2.0, (80, 92), 0.12),
    "typical": ((1500, 2000), 0.06, 4.0, (1.2, 1.9), 3, 4.0, (65, 85), 0.25),
    "rough": ((1200, 1600), 0.09, 7.0, (1.7, 2.5), 6, 6.0, (45, 65), 0.38),
}


def fake_photo(sheet, tier, rs):
    """Turn a clean render into something like a handheld phone photo:
    keystone skew, tilt, uneven light, colour cast, blur, shake, sensor noise, JPEG."""
    (pw, ph), wob, tilt, blur, motion, noise, jq, fall = TIERS[tier]
    sw, sh = sheet.size
    if sw > sh:
        pw, ph = ph, pw                      # a landscape sheet gets a landscape photo
    scale = min(pw / sw, ph / sh) * rs.uniform(0.74, 0.86)
    w, h = sw * scale, sh * scale
    pre = sheet.resize((max(2, int(round(w))), max(2, int(round(h)))), Image.LANCZOS)
    ang = math.radians(rs.uniform(-tilt, tilt))
    ox = pw / 2 + rs.uniform(-0.03, 0.03) * pw
    oy = ph / 2 + rs.uniform(-0.03, 0.03) * ph
    dst = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x = sx * w / 2 + rs.uniform(-wob, wob) * w
        y = sy * h / 2 + rs.uniform(-wob, wob) * w
        dst.append((ox + x * math.cos(ang) - y * math.sin(ang), oy + x * math.sin(ang) + y * math.cos(ang)))
    # keep the whole sheet inside the frame, as a person taking the photo would
    m = 0.025 * min(pw, ph)
    f = 1.0
    for x, y in dst:
        for v, c, lim in ((x, ox, pw), (y, oy, ph)):
            if v < m:
                f = min(f, (c - m) / (c - v))
            elif v > lim - m:
                f = min(f, (lim - m - c) / (v - c))
    dst = [(ox + (x - ox) * f, oy + (y - oy) * f) for x, y in dst]
    src = [(0, 0), (pre.size[0], 0), (pre.size[0], pre.size[1]), (0, pre.size[1])]
    co = homography(dst, src).ravel()[:8]
    warped = pre.transform((pw, ph), Image.PERSPECTIVE, tuple(co), Image.BICUBIC)
    mask = Image.new("L", pre.size, 255).transform((pw, ph), Image.PERSPECTIVE, tuple(co), Image.BILINEAR)
    base = np.array(rs.choice(3) == np.arange(3), float)   # one of three table colours
    table = (np.array([[70, 52, 40], [48, 50, 56], [92, 84, 74]], float).T @ base)
    bg = np.ones((ph, pw, 3)) * table + rs.normal(0, 7, (ph // 8 + 1, pw // 8 + 1, 1)).repeat(8, 0).repeat(8, 1)[:ph, :pw]
    a = np.asarray(mask, float)[..., None] / 255.0
    img = np.asarray(warped, float) * a + bg * (1 - a)
    yy, xx = np.mgrid[0:ph, 0:pw]
    th = rs.uniform(0, 2 * math.pi)
    ramp = ((xx - pw / 2) * math.cos(th) + (yy - ph / 2) * math.sin(th)) / (0.5 * math.hypot(pw, ph))
    light = 1.0 - fall * (0.5 + 0.5 * ramp) - 0.5 * fall * ((xx - pw / 2) ** 2 + (yy - ph / 2) ** 2) / (pw * pw + ph * ph) * 4
    cast = 1.0 + rs.uniform(-0.06, 0.06, 3)
    img = img * light[..., None] * cast
    im = Image.fromarray(np.uint8(np.clip(img, 0, 255)))
    im = im.filter(ImageFilter.GaussianBlur(rs.uniform(*blur)))
    if motion:
        k = int(rs.randint(1, motion + 1))
        if k > 1:
            d = rs.uniform(0, math.pi)
            arr = np.asarray(im, float)
            acc = np.zeros_like(arr)
            for i in range(k):
                t = i - (k - 1) / 2.0
                acc += np.roll(np.roll(arr, int(round(t * math.sin(d))), 0), int(round(t * math.cos(d))), 1)
            im = Image.fromarray(np.uint8(np.clip(acc / k, 0, 255)))
    arr = np.asarray(im, float) + rs.normal(0, noise, (ph, pw, 3))
    im = Image.fromarray(np.uint8(np.clip(arr, 0, 255)))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=int(rs.randint(jq[0], jq[1] + 1)))
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def verify(spec, sheet, per_tier=20, seed=1, save_example=None, quiet=False):
    """Photograph-and-read-back test. Returns (wrong characters, total characters, rows)."""
    rs = np.random.RandomState(seed)
    rows, wrong_all, total_all = [], 0, 0
    for tier in TIERS:
        wrong = bad = clue_bad = 0
        worst = 9.0
        for i in range(per_tier):
            photo = fake_photo(sheet, tier, rs)
            if save_example and tier == "rough" and i == 0:
                photo.save(save_example, quality=90)
            text, info = decode_photo(photo, spec)
            target = spec.message
            errs = sum(a != b for a, b in zip(text.ljust(len(target), "#"), target)) + max(0, len(text) - len(target))
            wrong += errs
            bad += errs > 0
            clue_bad += info["clue"] != spec.letters
            worst = min(worst, info["min_margin"])
        total = per_tier * len(spec.message)
        rows.append((tier, per_tier, wrong, total, bad, clue_bad, worst))
        wrong_all += wrong
        total_all += total
        if not quiet:
            print("  %-8s %3d photos   wrong characters %d / %d   photos misread %d   clue misread %d   "
                  "thinnest margin %.2f" % (tier, per_tier, wrong, total, bad, clue_bad, worst))
    return wrong_all, total_all, rows


# ----------------------------------------------------------------------------
# 8. show-and-tell sheets
# ----------------------------------------------------------------------------
def glyph_sheet(spec, path, sentence="the quick brown fox jumps over the lazy dog", dpi=150, fonts=None):
    """The 26 glyphs in card order, with the letters they stand for under this key, and a test sentence."""
    serif, mono = fonts or _fonts()
    W, H = 210.0, 252.0
    fig, ax = new_page(W, H)
    soft = _mix(PAPER, INK, 0.6)
    ax.text(16, H - 20, "the 26 glyphs", fontproperties=serif, fontsize=9 * MM, color=INK, va="baseline")
    ax.text(16, H - 27.5, "card order 1-26  ·  letter shown = this key (%s)" % spec.key,
            fontproperties=mono, fontsize=2.7 * MM, color=soft, va="baseline")
    cols, cell = 7, 26.0
    x0, y0 = 27.0, 47.0
    for i in range(28):
        r, c = divmod(i, cols)
        x, y = x0 + c * cell, y0 + r * 30.0
        draw_glyph(ax, ALL_MARKS[i], x, y, 7.0, 0.0, INK, H)
        if i < 26:
            ax.text(x - 9.5, H - (y - 9.5), "%02d" % (i + 1), fontproperties=mono, fontsize=2.4 * MM, color=soft)
            ax.text(x, H - (y + 15.5), chr(97 + spec.inv[i]), fontproperties=serif, fontsize=6.2 * MM,
                    color=SIGNAL, ha="center")
        else:
            ax.text(x, H - (y + 14.5), "space" if i == 26 else "start", fontproperties=mono,
                    fontsize=2.5 * MM, color=soft, ha="center")
    yb = y0 + 4 * 30.0 + 4
    ax.add_line(Line2D([16, W - 16], [H - yb + 12, H - yb + 12], color=INK, lw=0.2 * MM))
    ax.text(16, H - (yb - 3), "test sentence", fontproperties=mono, fontsize=2.7 * MM, color=soft, va="baseline")
    words = sentence.split()
    lines, cur = [], ""
    for wd in words:
        if len(cur) + len(wd) + (1 if cur else 0) > 15:
            lines.append(cur); cur = wd
        else:
            cur = (cur + " " + wd) if cur else wd
    lines.append(cur)
    for li, line in enumerate(lines):
        y = yb + 12 + li * 22.0
        for ci, ch in enumerate(line):
            x = 24.0 + ci * 11.6
            prims = SPACE if ch == " " else GLYPHS[spec.perm[ord(ch) - 97]][1]
            draw_glyph(ax, prims, x, y, 4.6, 0.0, INK, H)
            if ch != " ":
                ax.text(x, H - (y + 10.2), ch, fontproperties=mono, fontsize=2.8 * MM, color=soft, ha="center")
    fig.savefig(path, dpi=dpi, facecolor=PAPER)
    plt.close(fig)


# ----------------------------------------------------------------------------
# 9. the sealed card
# ----------------------------------------------------------------------------
def deal_trace(key):
    """The counting-out shuffle again, step by step, in glyph numbers 1-26 (for the card).
    Each step: (letter, digit, glyph the finger starts on, glyphs counted through, wrapped past 26?)."""
    digits = [int(c) for c in str(int(key))]
    remaining = list(range(26))
    pos, steps = 0, []
    for i in range(26):
        d = digits[i % len(digits)]
        n = len(remaining)
        passed = [remaining[(pos + k) % n] + 1 for k in range(1, d + 1)]
        steps.append((chr(97 + i), d, remaining[pos] + 1, passed, pos + d >= n))
        pos = (pos + d) % n
        remaining.pop(pos)
        if remaining:
            pos %= len(remaining)
    return steps


def render_card(spec, path, with_table=False, fonts=None):
    """The sealed card as an A5 PDF: page 1 the method, page 2 a worked example on this
    print's own numbers. with_table adds a third page, the finished letter table."""
    serif, mono = fonts or _fonts()
    W, H, M = 148.0, 210.0, 12.0
    slots = ring_slots(len(spec.message)) if spec.message else [0]
    rings = len(slots)
    FS, LH = 2.45, (3.42 if rings == 1 else 3.14)      # the wording for several rings runs a few lines longer
    cols = int((W - 2 * M) / (0.602 * FS))
    white = "#FFFFFF"
    soft = _mix(white, INK, 0.62)
    dim = _mix(white, INK, 0.82)

    def put(ax, x, y, text, size=FS, fp=mono, color=INK, ha="left"):
        ax.text(x, H - y, text, ha=ha, va="baseline", fontproperties=fp, fontsize=size * MM, color=color)

    def para(ax, y, text, indent=0, color=INK, first=None):
        lines = textwrap.wrap(text, cols - indent)
        for i, line in enumerate(lines):
            if i == 0 and first:
                put(ax, M, y, first, color=SIGNAL)
            put(ax, M + indent * 0.602 * FS, y, line, color=color)
            y += LH
        return y

    def head(ax, y, number, title):
        put(ax, M, y, "%d" % number, size=3.0, color=SIGNAL)
        put(ax, M + 5.0, y, title.upper(), size=2.6)
        return y + LH + 1.4

    def top(ax, title, sub):
        put(ax, M, 16.0, title, size=7.0, fp=serif)
        put(ax, M, 21.4, sub, color=soft)
        ax.add_line(Line2D([M, W - M], [H - 24.4, H - 24.4], color=INK, lw=0.2 * MM))

    shown = "".join(ch for ch, _ in spec.slots)
    d8 = spec.date
    date_txt = "%s · %s · %s" % (d8[:4], d8[4:6], d8[6:])
    pdf = PdfPages(path, metadata=dict(_PDF_META, Title="sealed card"))

    # ---- page 1: the method --------------------------------------------------------------
    fig, ax = new_page(W, H, white)
    top(ax, "the sealed card", "how to read the %s of glyphs on the birth print of %s" % (
        "ring" if rings == 1 else "rings", shown))
    y = head(ax, 30.6, 1, "the 26 glyphs, in card order")
    cell = (W - 2 * M) / 13.0
    for i in range(26):
        r, c = divmod(i, 13)
        gx, gy = M + (c + 0.5) * cell, y + 3.4 + r * 13.5
        draw_glyph(ax, GLYPHS[i][1], gx, gy, 3.4, 0.0, INK, H)
        put(ax, gx, gy + 7.3, "%d" % (i + 1), size=2.3, color=dim, ha="center")
    y += 2 * 13.5 + 3.4
    draw_glyph(ax, SPACE, M + 2.0, y - 0.8, 2.4, 0.0, INK, H)
    put(ax, M + 5.5, y, "a small dot: the space between words")
    tx = M + (70.0 if rings == 1 else 62.0)
    draw_glyph(ax, START, tx, y - 0.8, 2.0, 0.0, INK, H)
    put(ax, tx + 3.5, y, "a solid triangle: the start" + ("" if rings == 1 else " of a ring"))
    y = head(ax, y + 6.8, 2, "make the key")
    y = para(ax, y, "Four things printed on the sheet each become a number. Add the four numbers together; the "
                    "total is the key. The order you add them in makes no difference, and a zero at the front "
                    "of a number changes nothing.") + 1.0
    for label, text in (
            ("NAME", "the large name. Letters only: ignore spaces, hyphens and accents. Write each letter as its "
                     "two-digit place in the alphabet (a = 01, b = 02 ... z = 26) and run the digits together "
                     "into one long number."),
            ("DATE", "under the name: year, month, day. Run the eight digits together."),
            ("TIME", "bottom-left corner, 24-hour clock. Drop the colon: hours then minutes."),
            ("WEIGHT", "bottom-right corner. The number of grams.")):
        y = para(ax, y, text, indent=8, first=label)
    y = head(ax, y + 3.4, 3, "deal the glyphs to the letters")
    for text in (
            "Write the digits of the key in a row. Put a finger on glyph 1.",
            "For the letter a, read the first digit and step your finger forward that many glyphs. The glyph "
            "it is resting on is not counted: the next one is 'one'. For 0, stay where you are. The glyph now "
            "under your finger stands for a. Strike it out, then move your finger on to the next glyph that "
            "is not struck out.",
            "For b use the second digit, for c the third, and so on, always starting from where your finger "
            "is resting. z takes the one glyph left over; it needs no digit.",
            "Rules. Struck-out glyphs are never counted or rested on. After glyph 26 comes glyph 1, both when "
            "counting and when moving your finger on. After the last digit of the key, go back to its first "
            "digit. Near the end, when few glyphs are left, keep stepping round and round them until the "
            "digit is used up: a glyph is counted every time your finger arrives on it, the one you set out "
            "from included.",
            "Before you read the %s, check your table: the glyphs directly above the name spell the name, "
            "letter for letter." % ("ring" if rings == 1 else "rings")):
        y = para(ax, y, text) + 0.8
    if rings == 1:
        y = head(ax, y + 2.4, 4, "read the ring")
        how = ("The message runs round the ring of glyphs just inside the tick marks. Start at the solid "
               "triangle at the top and read clockwise until you are back at the triangle. The top of every "
               "glyph points outwards, away from the centre, so turn the sheet as you read. A small dot is "
               "a space.")
    else:
        y = head(ax, y + 2.4, 4, "read the rings")
        how = ("The message runs round the %s rings of glyphs just inside the tick marks. Read the outermost "
               "ring first: start at its solid triangle at the top and go clockwise until you are back at "
               "that triangle. Then read the next ring in the same way, from its own triangle%s. Join the "
               "rings up as you go: a ring can stop in the middle of a word and the next one carries it on. "
               "The top of every glyph points outwards, away from the centre, so turn the sheet as you read. "
               "A small dot is a space." % ("two" if rings == 2 else "three",
                                           "" if rings == 2 else ", and the innermost ring last"))
    y = para(ax, y, how) + 0.8
    para(ax, y, "Nothing else on the sheet is part of the key or the message: not the latitude and longitude in "
                "the top corners, not the orange line, not the four larger dots outside the ring.")
    pdf.savefig(fig, facecolor=white)
    plt.close(fig)

    # ---- page 2: worked example ----------------------------------------------------------
    fig, ax = new_page(W, H, white)
    top(ax, "worked example", "the same steps, done with the numbers on this print")
    y = head(ax, 30.6, 1, "the key")
    xr = W - M
    letters = spec.letters
    for a in range(0, len(letters), 20):
        chunk = letters[a:a + 20]
        put(ax, M, y, "NAME" if a == 0 else "", color=SIGNAL)
        put(ax, M + 8 * 0.602 * FS, y, "  ".join(chunk))
        put(ax, M + 8 * 0.602 * FS, y + LH, " ".join("%02d" % (ord(c) - 96) for c in chunk), color=soft)
        y += 2 * LH + 0.6
    if len(letters) <= 12:
        y -= LH + 0.6                      # short name: its number sits on the same line as the digit pairs
    rows = (("", "", str(spec.blocks[0])), ("DATE", date_txt, str(spec.blocks[1])),
            ("TIME", "%s:%s" % (spec.hhmm[:2], spec.hhmm[2:]), str(spec.blocks[2])),
            ("WEIGHT", "%d g" % spec.grams, str(spec.blocks[3])))
    for label, seen, number in rows:
        put(ax, M, y, label, color=SIGNAL)
        put(ax, M + 8 * 0.602 * FS, y, seen)
        put(ax, xr, y, number, ha="right")
        y += LH + 0.5
    wk = max(len(str(spec.key)), 6) * 0.602 * FS
    ax.add_line(Line2D([xr - wk - 3.0, xr], [H - y + 2.2, H - y + 2.2], color=INK, lw=0.2 * MM))
    put(ax, xr - wk - 5.0, y + 1.6, "+", ha="right", color=soft)
    put(ax, M, y + 1.6, "KEY", color=SIGNAL)
    put(ax, xr, y + 1.6, str(spec.key), ha="right", color=SIGNAL)
    y = head(ax, y + 10.0, 2, "the digits, in a row")
    y = para(ax, y, " ".join(str(spec.key)) + "   (then round again from the first)") + 0.5
    y = head(ax, y + 3.4, 3, "the deals")
    steps = deal_trace(spec.key)
    first_wrap = next((i for i, st in enumerate(steps) if st[4]), 5)
    count = max(6, min(9, first_wrap + 2))
    if len(letters) > 12:
        count = max(4, min(count, first_wrap + 1) - 1)     # a long name needs the room for its own digits
    put(ax, M, y, "letter  digit  finger on   counts through", color=soft)
    put(ax, xr - 9.0, y, "lands on", color=soft, ha="right")
    y += LH + 0.8
    def deal_row(y, step):
        letter, d, start, passed, wrapped = step
        land = passed[-1] if passed else start
        put(ax, M, y, "  %s       %d       %2d       %s" % (letter, d, start,
                                                           " ".join(str(p) for p in passed) if passed else "(stays)"))
        put(ax, xr - 9.0, y, "glyph %d" % land, ha="right")
        draw_glyph(ax, GLYPHS[land - 1][1], xr - 3.2, y - 0.9, 2.1, 0.0, INK, H)
        return y + LH + 1.7

    for st in steps[:count]:
        y = deal_row(y, st)
    wl = next((st for st in steps[:count] if st[4]), None)
    note = "Carry on in the same way."
    if wl:
        skipped = [g for g in range(1, 27) if g not in wl[3] and
                   ((g > wl[2]) or (g < (wl[3][-1] if wl[3] else wl[2])))]
        note = ("Look at %s: the count runs past glyph 26 and carries on at glyph 1%s. " % (
            wl[0], ", stepping over %s, which %s already struck out" % (
                " and ".join(str(g) for g in skipped), "is" if len(skipped) == 1 else "are") if skipped else "")) + note
    y = para(ax, y + 0.3, note) + 1.6
    # a late deal, where the count laps the few glyphs that are left
    lap = next((i for i in range(count, 25) if steps[i][1] >= 26 - i), None)
    if lap is not None:
        left = 26 - lap
        put(ax, M, y, "  ...", color=soft)
        y = deal_row(y + LH + 0.6, steps[lap])
        st = steps[lap]
        y = para(ax, y + 0.3, "Look at %s: only %d glyphs are left, so a count of %d goes %s round them%s." % (
            st[0], left, st[1], "right" if st[1] < 2 * left else "more than once",
            " and lands back on the glyph the finger started on" if st[3][-1] == st[2] else
            ", counting the starting glyph as it passes")) + 0.4
    y = para(ax, y, "End check: when you reach z, one glyph is left. On this print it is glyph %d." % (
        spec.perm[25] + 1))
    y = head(ax, y + 3.6, 4, "check with the name")
    y = para(ax, y, "The glyphs printed directly above the name, left to right, with their card numbers:") + 4.6
    pitch = min(10.0, (W - 2 * M) / len(letters))
    k = pitch / 10.0
    for i, c in enumerate(letters):
        g = spec.perm[ord(c) - 97]
        gx = M + (i + 0.6) * pitch
        draw_glyph(ax, GLYPHS[g][1], gx, y, 3.0 * max(k, 0.6), 0.0, INK, H)
        put(ax, gx, y + 6.8, "%d" % (g + 1), size=2.2 * max(k, 0.8), color=dim, ha="center")
        put(ax, gx, y + 11.8, c, size=4.4 * max(k, 0.7), fp=serif, color=SIGNAL, ha="center")
    y += 18.5
    tail = "Now go and read the ring."
    if spec.message and rings == 1:
        ng, ns = len(spec.message.replace(" ", "")), spec.message.count(" ")
        tail = "Now read the ring: after the triangle it holds %d glyph%s and %d space%s." % (
            ng, "" if ng == 1 else "s", ns, "" if ns == 1 else "s")
    elif spec.message:
        parts, at = [], 0
        for i, n in enumerate(slots):
            chunk = spec.message[at:at + n - 1]
            at += n - 1
            ns = chunk.count(" ")
            parts.append("ring %d holds %d glyphs and %d space%s" % (i + 1, len(chunk) - ns, ns, "" if ns == 1 else "s"))
        tail = "Now read the rings, outermost first. After its triangle, " + "; ".join(parts) + "."
    para(ax, y - 1.5, "When your own table gives these glyphs the letters of the name, the table is right. " + tail)
    pdf.savefig(fig, facecolor=white)
    plt.close(fig)

    # ---- page 3 (only in the copy to be kept elsewhere): the finished table --------------
    if with_table:
        fig, ax = new_page(W, H, white)
        top(ax, "the letter table", "the finished answer to step 3 for this print: keep it apart from the card")
        y0 = 44.0
        cw = (W - 2 * M) / 6.0
        for g in range(26):
            r, c = divmod(g, 6)
            gx, gy = M + (c + 0.5) * cw, y0 + r * 31.0
            draw_glyph(ax, GLYPHS[g][1], gx, gy, 5.2, 0.0, INK, H)
            put(ax, gx - 8.5, gy - 6.5, "%d" % (g + 1), size=2.3, color=dim)
            put(ax, gx, gy + 14.0, chr(97 + spec.inv[g]), size=6.2, fp=serif, color=SIGNAL, ha="center")
        put(ax, M, H - 14.0, "glyphs in card order; the letter under each is what it stands for on this print.",
            color=soft)
        pdf.savefig(fig, facecolor=white)
        plt.close(fig)
    pdf.close()


# ----------------------------------------------------------------------------
# 10. command line
# ----------------------------------------------------------------------------
def _ask(args, field, prompt):
    v = getattr(args, field)
    if v is None:
        if not sys.stdin.isatty():
            raise InputError("missing --%s" % field)
        v = input(prompt + ": ").strip()
        setattr(args, field, v)
    return v


def main(argv=None):
    ap = argparse.ArgumentParser(description="Generate a birth print with a keyed glyph cipher.")
    ap.add_argument("--name"); ap.add_argument("--date"); ap.add_argument("--time")
    ap.add_argument("--weight"); ap.add_argument("--coords"); ap.add_argument("--message")
    ap.add_argument("--size", default="A3 portrait", help="e.g. 'A3 portrait', 'A2', '300x400 mm'")
    ap.add_argument("--style", type=int, default=2, choices=sorted(STYLES),
                    help="2 solid (default), 1 contour, 3 drafted")
    ap.add_argument("--bleed", type=float, default=0.0, metavar="MM",
                    help="extra cream on every side of the PDF for a print shop to trim off, e.g. 3")
    ap.add_argument("--no-background", action="store_true",
                    help="leave the cream out of the PDF, for printing on cream paper stock")
    ap.add_argument("--out", default="out", help="output folder")
    ap.add_argument("--dpi", type=int, default=150, help="resolution of the PNG preview")
    ap.add_argument("--serif-font", help="path to a .ttf/.otf for the name (default: bundled STIX)")
    ap.add_argument("--mono-font", help="path to a .ttf/.otf for the read-outs (default: bundled DejaVu Sans Mono)")
    ap.add_argument("--verify", type=int, nargs="?", const=20, metavar="N",
                    help="after rendering, simulate N phone photos per difficulty tier and read each one back")
    ap.add_argument("--decode", metavar="PHOTO", help="read the message out of a photo of the print (give the same "
                    "name, date, time, weight and size; no message needed)")
    ap.add_argument("--glyph-sheet", action="store_true", help="also write the 26-glyph chart as a PNG")
    ap.add_argument("--samples", action="store_true", help="also write a small PNG of each style, to compare")
    args = ap.parse_args(argv)
    try:
        for f, p in (("name", "Name"), ("date", "Date of birth (YYYYMMDD)"), ("time", "Time of birth (HH:MM)"),
                     ("weight", "Birth weight (e.g. 3600 g)")):
            _ask(args, f, p)
        if args.decode:
            spec = Spec(args.name, args.date, args.time, args.weight, args.coords or "N 0, E 0", None, args.size)
            text, info = decode_photo(Image.open(args.decode), spec)
            print("message : %s" % text)
            print("clue    : %s  (%s)" % (info["clue"], "matches the name" if info["clue"] == spec.letters
                                          else "does NOT match the name - check the details or the photo"))
            return 0
        _ask(args, "coords", "Coordinates (e.g. N 53.48, W 2.24)")
        _ask(args, "message", "Message for the ring (letters and spaces, up to %d characters)" % MAX_MESSAGE)
        spec = Spec(args.name, args.date, args.time, args.weight, args.coords, args.message, args.size)
    except InputError as e:
        print("Input problem - " + str(e), file=sys.stderr)
        return 2
    if not 0 <= args.bleed <= 20:
        print("Input problem - bleed: between 0 and 20 mm", file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)
    fonts = _fonts(args.serif_font, args.mono_font)
    stem = os.path.join(args.out, spec.letters)
    pdf, png = stem + "_print.pdf", stem + "_preview.png"
    card, card2 = stem + "_card.pdf", stem + "_card_with_table.pdf"
    render_print(spec, args.style, pdf, png, args.dpi, fonts, not args.no_background, args.bleed)
    render_card(spec, card, False, fonts)
    render_card(spec, card2, True, fonts)
    print("name    : %s" % spec.letters)
    n_rings = ring_count(len(spec.message))
    print("message : %s  (%d of %d characters, %d ring%s)" % (spec.message, len(spec.message), MAX_MESSAGE,
                                                             n_rings, "" if n_rings == 1 else "s"))
    print("key     : %s = %s" % (" + ".join(str(b) for b in spec.blocks), spec.key))
    print("style   : %d %s" % (args.style, STYLES[args.style]))
    print("wrote   : %s   (vector, %g x %g mm%s)" % (pdf, spec.W + 2 * args.bleed, spec.H + 2 * args.bleed,
                                                   ", incl. %g mm bleed" % args.bleed if args.bleed else ""))
    print("          %s" % png)
    print("          %s   (the sealed card: method + worked example)" % card)
    print("          %s   (same, plus the finished letter table: keep it elsewhere)" % card2)
    if args.glyph_sheet:
        glyph_sheet(spec, stem + "_glyphs.png", fonts=fonts)
        print("          %s" % (stem + "_glyphs.png"))
    if args.samples:
        for st in sorted(STYLES):
            p = "%s_style%d_%s.png" % (stem, st, STYLES[st])
            render_print(spec, st, None, p, 80, fonts)
            print("          %s" % p)
    if args.verify:
        print("photo test (%d simulated phone photos per tier):" % args.verify)
        sheet = render_print(spec, args.style, dpi=150, fonts=fonts)
        wrong, total, _ = verify(spec, sheet, args.verify)
        print("  error rate: %d / %d characters = %.2f%%" % (wrong, total, 100.0 * wrong / total))
        if wrong:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
