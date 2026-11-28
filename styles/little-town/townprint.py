#!/usr/bin/env python3
"""
townprint.py - a birth print of a little town, with a cipher a child can read.

Every letter of the message is a house. Count its windows and look at its colour.

One script, four files out:
    <out>/<name>_print.pdf     vector artwork at print size
    <out>/<name>_preview.png   raster preview
    <out>/<name>_card.pdf      the "sealed card": how to read the houses
    <out>/<name>_card_with_table.pdf   the same card plus the finished chart

Run it:
    python townprint.py --name oscar --date 20240317 --time 04:42 \
        --weight "3600 g" --coords "N 53.48, W 2.24" \
        --message "the quick brown fox" --size "A3 portrait"

Needs Python 3.9+ and:  pip install matplotlib numpy pillow
The same inputs always give the same artwork (no clock, no random seed).
"""
import argparse
import datetime
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
from matplotlib.lines import Line2D
from matplotlib.patches import Circle, PathPatch, Polygon, Rectangle
from matplotlib.path import Path
from PIL import Image

VERSION = "1.0"
MM = 72.0 / 25.4  # points per millimetre

# ----------------------------------------------------------------------------
# colours
# ----------------------------------------------------------------------------
PAPER = "#F4ECD8"   # cream, the same sheet as the other styles
SIGNAL = "#F4511E"  # orange-red: labels and answers on the card
# Three house colours per palette, always darkest first. The order is part of the cipher:
# the card calls them colour 1, 2 and 3 from darkest to lightest, so the print can still be
# read from a grey photocopy. No pink, as in the other styles.
PALETTES = {1: ("harbour", ("#17235C", "#F4511E", "#F5A300")),   # navy, orange-red, marigold
            2: ("meadow", ("#1B4D3E", "#2A9D8F", "#E3B23C")),    # forest, teal, straw
            3: ("dusk", ("#2B2140", "#2F6FDE", "#F0A830"))}      # aubergine, cobalt, amber
# The trees get a soft colour of their own in each palette, paler than any house.
LEAVES = {1: "#8DB5A4", 2: "#B7C48C", 3: "#A99BC9"}               # sage, olive, lavender
# what a child would call the three house colours, for the card
COLOUR_NAMES = {1: ("dark blue", "orange", "yellow"), 2: ("dark green", "sea green", "yellow"),
                3: ("dark purple", "blue", "orange")}


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
    clue houses. slots = [(display_char, letter_or_None)] for the printed name."""
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


MAX_MESSAGE = 300  # characters incl. spaces, one house or tree each: about fifty words
MAX_WORD = 20      # letters in one word: a word is never split over two streets


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
        raise InputError("message: %d characters after tidying; the town holds %d" % (len(msg), MAX_MESSAGE))
    for word in msg.split(" "):
        if len(word) > MAX_WORD:
            raise InputError("message: the word %r has %d letters; a word can have %d at most"
                             % (word, len(word), MAX_WORD))
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
        self.words = self.message.split(" ") if self.message else None
        self.chart = fill_chart(self.letters[0])        # chart[colour 0-2][windows 1-9, less one] = letter
        self.code = {ch: (ink, n + 1) for ink in range(3) for n, ch in enumerate(self.chart[ink]) if ch}
        self.minutes = int(self.hhmm[:2]) * 60 + int(self.hhmm[2:])
        self.season = season_of(self.date, self.lat)
        # picks the roofs, the chimneys and where the birds fly: looks only, never the cipher
        self.seed = int(self.date) + sum((i + 1) * ord(c) for i, c in enumerate(self.letters))


# ----------------------------------------------------------------------------
# 2. the cipher
# ----------------------------------------------------------------------------
def fill_chart(first):
    """The chart has a row for each colour and a column for each number of windows: 27
    boxes. The alphabet goes in box by box, starting from the first letter of the name and
    carrying on from z to a. There are 26 letters, so the last box stays empty."""
    start = ord(first) - ord("a")
    run = [chr(ord("a") + (start + i) % 26) for i in range(26)] + [None]
    return [run[0:9], run[9:18], run[18:27]]


SEASONS = ("winter", "spring", "summer", "autumn")


def season_of(date, lat):
    """The season the trees are drawn in. North of the equator spring is March to May,
    summer June to August, and so on; south of it the seasons are the other way round."""
    q = (int(date[4:6]) % 12) // 3
    return SEASONS[(q + 2) % 4 if lat < 0 else q]


def _pick(seed, i, salt=0):
    """A fixed number from 0 to 1 for the i-th thing. It stands in for a roll of the dice,
    so the town looks hand-built but comes out the same every time."""
    v = (seed * 73856093) ^ ((i + 1) * 19349663) ^ ((salt + 1) * 83492791)
    v = (v * 2654435761) & 0xFFFFFFFF
    v ^= v >> 15
    v = (v * 2246822519) & 0xFFFFFFFF
    v ^= v >> 13
    return (v & 0xFFFFFF) / float(1 << 24)


# ----------------------------------------------------------------------------
# 3. houses and trees (a cell 1 wide and ASPECT tall; x right, heights up from the street)
# ----------------------------------------------------------------------------
ASPECT = 1.28                        # a cell is this many times as tall as it is wide
GROUND = 0.03                        # thickness of the street under a row
WALL_X0, WALL_X1, WALL_H = 0.04, 0.96, 0.86
WIN_X = (0.24, 0.50, 0.76)           # window columns, left to right
WIN_Y = (0.68, 0.44, 0.20)           # window rows, top to bottom
WIN_R = 0.095                        # half the side of a window
# which of the nine window places are open for each count: the spots on a dice, carried on to nine
LIT = {0: (), 1: (4,), 2: (0, 8), 3: (0, 4, 8), 4: (0, 2, 6, 8), 5: (0, 2, 4, 6, 8),
       6: (0, 2, 3, 5, 6, 8), 7: (0, 2, 3, 4, 5, 6, 8), 8: (0, 1, 2, 3, 5, 6, 7, 8), 9: tuple(range(9))}
_B = WALL_H - 0.006                  # a roof starts a hair inside the wall, so no gap shows
# roof outlines: (across, height above the top of the wall)
ROOFS = [[(fx, _B + up) for fx, up in roof] for roof in (
    [(0.0, 0.0), (1.0, 0.0), (0.50, 0.29)],                                                   # gable
    [(0.01, 0.0), (0.99, 0.0), (0.99, 0.08), (0.01, 0.08)],                                   # flat
    [(0.04, 0.0), (0.96, 0.0), (0.96, 0.08), (0.81, 0.08), (0.81, 0.155), (0.67, 0.155), (0.67, 0.23),
     (0.33, 0.23), (0.33, 0.155), (0.19, 0.155), (0.19, 0.08), (0.04, 0.08)],                 # stepped
    [(0.0, 0.0), (1.0, 0.0), (0.76, 0.23), (0.24, 0.23)],                                     # hipped
    [(0.5 - 0.46 * math.cos(math.pi * i / 16.0), 0.25 * math.sin(math.pi * i / 16.0))
     for i in range(17)],                                                                     # round
    [(0.01, 0.0), (0.99, 0.0), (0.99, 0.25), (0.01, 0.09)],                                   # lean-to
)]
GABLE = 0


def _ring(verts, codes, x0, y0, x1, y1, hole=False):
    """Add a rectangle to a path. A hole is wound the other way, so it is cut out of the shape
    and the paper shows through, with no cream ink laid on top."""
    pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if hole:
        pts.reverse()
    verts.extend(pts + [pts[0]])
    codes.extend([Path.MOVETO, Path.LINETO, Path.LINETO, Path.LINETO, Path.CLOSEPOLY])


def draw_house(ax, x, y, s, windows, ink, page_h, roof=GABLE, chimney=False, zorder=2):
    """One house in a cell s wide and ASPECT * s tall, with the cell's top-left corner at
    (x, y) in millimetres from the top-left of the page. windows 0 = a blank house, used
    on the card to show a colour."""
    bx, by = x, page_h - (y + ASPECT * s) + GROUND * s           # where the wall meets the street
    verts, codes = [], []
    _ring(verts, codes, bx + WALL_X0 * s, by, bx + WALL_X1 * s, by + WALL_H * s)
    for w in LIT[windows]:
        r, c = divmod(w, 3)
        _ring(verts, codes, bx + (WIN_X[c] - WIN_R) * s, by + (WIN_Y[r] - WIN_R) * s,
              bx + (WIN_X[c] + WIN_R) * s, by + (WIN_Y[r] + WIN_R) * s, hole=True)
    ax.add_patch(PathPatch(Path(verts, codes), fc=ink, ec="none", zorder=zorder))
    dark = _mix(ink, "#000000", 0.26)
    if chimney:
        ax.add_patch(Rectangle((bx + 0.70 * s, by + _B * s), 0.11 * s, 0.25 * s, fc=dark, ec="none", zorder=zorder))
    ax.add_patch(Polygon([(bx + fx * s, by + fy * s) for fx, fy in ROOFS[roof]], closed=True, fc=dark, ec="none",
                         zorder=zorder))


def tree_tones(palette, base=PAPER):
    """Leaf, wood and blossom: pale, so the trees sit back and the houses stand out."""
    leaf, ink = LEAVES[palette], PALETTES[palette][1][0]
    return _rgb(leaf), _mix(leaf, ink, 0.45), _mix(leaf, base, 0.80)


def draw_tree(ax, x, y, s, season, tones, page_h, size=0.5, zorder=2):
    """One tree in a cell, dressed for the season. size 0-1 makes it a little smaller or larger."""
    leaf, wood, blossom = tones
    k = 0.84 + 0.30 * size
    cx, by = x + 0.5 * s, page_h - (y + ASPECT * s) + GROUND * s
    if season == "winter":                                         # bare branches
        lw = 0.045 * s * MM
        top = 0.74 * k * s
        ax.add_line(Line2D([cx, cx], [by, by + top], color=wood, lw=lw, solid_capstyle="round", zorder=zorder))
        for h, reach in ((0.30, 0.20), (0.46, 0.16), (0.60, 0.11)):
            for side in (-1, 1):
                ax.add_line(Line2D([cx, cx + side * reach * k * s], [by + h * k * s, by + (h + reach * 0.9) * k * s],
                                   color=wood, lw=lw, solid_capstyle="round", zorder=zorder))
        return
    ax.add_patch(Rectangle((cx - 0.03 * s, by), 0.06 * s, 0.40 * k * s, fc=wood, ec="none", zorder=zorder))
    r = (0.22 if season == "autumn" else 0.26) * k * s
    cy = by + 0.58 * k * s
    ax.add_patch(Circle((cx, cy), r, fc=leaf, ec="none", zorder=zorder))
    if season == "spring":                                         # blossom
        for dx, dy in ((-0.42, 0.30), (0.36, 0.42), (0.02, -0.08), (-0.30, -0.44), (0.46, -0.30)):
            ax.add_patch(Circle((cx + dx * r, cy + dy * r), 0.17 * r, fc=blossom, ec="none", zorder=zorder))
    elif season == "autumn":                                       # leaves coming down
        for dx, h in ((-0.27, 0.26), (0.25, 0.17), (-0.16, 0.05), (0.31, 0.03)):
            ax.add_patch(Circle((cx + dx * k * s, by + h * k * s + 0.03 * s), 0.035 * k * s, fc=leaf, ec="none",
                                zorder=zorder))


# ----------------------------------------------------------------------------
# 4. page layout (millimetres, origin top-left, y down; u = 1 mm on an A3 sheet)
# ----------------------------------------------------------------------------
MAX_HOUSE = 34.0     # widest a house may get, so a short message does not turn into a few huge houses


def wrap_words(words, cols):
    """Break the words into streets no longer than cols cells, never splitting a word."""
    lines, cur, used = [], [], 0
    for w in words:
        if cur and used + 1 + len(w) > cols:
            lines.append(cur)
            cur, used = [], 0
        used += len(w) + (1 if cur else 0)
        cur.append(w)
    lines.append(cur)
    return lines


def town_shape(words, wt, ht, cap):
    """Cells per street, the words on each street and the cell width, for a space wt x ht:
    the biggest house wins, then the shape closest to the space, then the fewest trees."""
    longest = max(len(w) for w in words)
    n = sum(len(w) for w in words) + len(words) - 1
    best = None
    for cols in range(longest, n + 1):
        lines = wrap_words(words, cols)
        rows = len(lines)
        cell = min(wt / cols, ht / (rows * ASPECT), cap)
        score = (round(cell, 6), -abs(math.log((cols / (rows * ASPECT)) / (wt / ht))), -cols * rows)
        if best is None or score > best[0]:
            best = (score, cols, lines, cell)
    return best[1:]


def street(words, cols, row, last):
    """One street as a list of cols cells: a letter for a house, None for a tree. Spare
    cells become extra trees between the words, two at most in any gap, so the right-hand
    ends of the streets line up where they can. The last street is not stretched."""
    gaps = len(words) - 1
    spare = cols - sum(len(w) for w in words) - gaps
    extra = [0] * gaps
    if gaps and not last:
        for k in range(min(spare, 2 * gaps)):
            extra[(row + k) % gaps] += 1
    cells = []
    for j, w in enumerate(words):
        cells.extend(w)
        if j < gaps:
            cells.extend([None] * (1 + extra[j]))
    return cells + [None] * (cols - len(cells))


def layout(W, H, slots, words):
    u = min(W / 297.0, H / 420.0)
    cx = W / 2.0
    n = len(slots)
    pitch = min(13.0 * u, 215.0 * u / max(n, 1))
    top, bottom, side = 50.0 * u, H - 88.0 * u, 31.0 * u
    cols, lines, cell = town_shape(words, W - 2 * side, bottom - top, MAX_HOUSE * u)
    rows = len(lines)
    return dict(
        u=u, W=W, H=H, cx=cx, margin=20.0 * u,
        cols=cols, rows=rows, cell=cell, ch=ASPECT * cell, tw=cols * cell, th=rows * ASPECT * cell,
        tx=cx - cols * cell / 2.0, ty=top + (bottom - top - rows * ASPECT * cell) / 2.0,
        streets=[street(ws, cols, r, r == rows - 1) for r, ws in enumerate(lines)],
        name_xs=[cx + (i - (n - 1) / 2.0) * pitch for i in range(n)], name_em=pitch / 0.9,
        clue_s=min(7.5 * u, 0.62 * pitch), clue_y=H - 68.5 * u,
        name_y=H - 54.0 * u, date_y=H - 42.0 * u,
    )


# ----------------------------------------------------------------------------
# 5. the artwork
# ----------------------------------------------------------------------------
def draw_town(ax, spec, L, palette):
    """The streets, read like a page: a house for each letter, trees between the words."""
    H, s, ch = L["H"], L["cell"], L["ch"]
    inks, tones = PALETTES[palette][1], tree_tones(palette)
    for r, cells in enumerate(L["streets"]):
        y = L["ty"] + r * ch
        ax.add_patch(Rectangle((L["tx"] - 0.12 * s, H - (y + ch)), L["tw"] + 0.24 * s, GROUND * s,
                               fc=inks[0], ec="none", zorder=3))
        for c, sign in enumerate(cells):
            i = r * L["cols"] + c
            x = L["tx"] + c * s
            if sign is None:
                draw_tree(ax, x, y, s, spec.season, tones, H, _pick(spec.seed, i, 2))
            else:
                ink, n = spec.code[sign]
                draw_house(ax, x, y, s, n, inks[ink], H, int(_pick(spec.seed, i) * len(ROOFS)),
                           _pick(spec.seed, i, 1) < 0.38)


def flock_count(grams):
    return max(2, int(grams / 100.0 + 0.5))


def is_day(spec):
    return 360 <= spec.minutes < 1080


def sky_place(spec, L):
    """Where the sun or moon stands. The sun crosses the sky from left to right between
    6 in the morning and 6 in the evening; the moon does the same through the night."""
    u = L["u"]
    f = ((spec.minutes - (360 if is_day(spec) else 1080)) % 1440) / 720.0
    return (L["W"] * (0.24 + 0.52 * f), 37.0 * u - 21.0 * u * math.sin(math.pi * f))


def _halton(i, base):
    f, r = 1.0, 0.0
    while i > 0:
        f /= base
        r += f * (i % base)
        i //= base
    return r


def draw_sky(ax, spec, L, inks):
    """The sun or the moon at the time of birth, and one bird (by day) or one star (by
    night) for every hundred grams of the weight."""
    u, W, H = L["u"], L["W"], L["H"]
    sx, sy = sky_place(spec, L)
    R = 5.4 * u
    day = is_day(spec)
    if day:
        ax.add_patch(Circle((sx, H - sy), R, fc=inks[2], ec="none", zorder=4))
        for k in range(12):
            a = 2 * math.pi * k / 12.0
            ax.add_line(Line2D([sx + 1.36 * R * math.cos(a), sx + 1.78 * R * math.cos(a)],
                               [H - sy + 1.36 * R * math.sin(a), H - sy + 1.78 * R * math.sin(a)],
                               color=inks[2], lw=0.62 * u * MM, solid_capstyle="round", zorder=4))
    else:
        # a crescent: the part of one circle that lies outside another
        d, r2 = 0.46 * R, 0.84 * R
        xi = (d * d + R * R - r2 * r2) / (2 * d)
        a = math.acos(xi / R)
        b = math.atan2(R * math.sin(a), xi - d)
        tilt = math.radians(-28.0)
        pts = [(R * math.cos(t), R * math.sin(t)) for t in np.linspace(a, 2 * math.pi - a, 48)]
        pts += [(d + r2 * math.cos(t), r2 * math.sin(t)) for t in np.linspace(2 * math.pi - b, b, 40)]
        ax.add_patch(Polygon([(sx + px * math.cos(tilt) - py * math.sin(tilt),
                               H - sy + px * math.sin(tilt) + py * math.cos(tilt)) for px, py in pts],
                             closed=True, fc=inks[0], ec="none", zorder=4))
    x0, x1, y0, y1 = L["margin"] + 38.0 * u, W - L["margin"] - 38.0 * u, 9.0 * u, 43.0 * u
    wing = _mix(PAPER, inks[0], 0.72)
    placed, i = 0, 1 + spec.seed % 97
    while placed < flock_count(spec.grams):
        x, y = x0 + _halton(i, 2) * (x1 - x0), y0 + _halton(i, 3) * (y1 - y0)
        i += 1
        if math.hypot(x - sx, y - sy) < 2.5 * R:
            continue
        size = _pick(spec.seed, placed, 3)
        if day:                                                    # a bird: two curved wings
            w = (1.5 + 1.2 * size) * u
            ts = np.linspace(-1.0, 1.0, 25)
            ax.add_line(Line2D(x + ts * w, H - y + 0.42 * w * np.abs(np.sin(math.pi * ts)), color=wing,
                               lw=0.30 * u * MM, solid_capstyle="round", zorder=4))
        else:                                                      # a star: four points
            r = (0.9 + 1.0 * size) * u
            ax.add_patch(Polygon([(x + (r if k % 2 == 0 else 0.32 * r) * math.cos(math.pi * k / 4.0),
                                   H - y + (r if k % 2 == 0 else 0.32 * r) * math.sin(math.pi * k / 4.0))
                                  for k in range(8)], closed=True, fc=inks[2], ec="none", zorder=4))
        placed += 1


def _fonts(serif=None, mono=None):
    base = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    serif = serif or os.path.join(base, "STIXGeneral.ttf")
    mono = mono or os.path.join(base, "DejaVuSansMono.ttf")
    return font_manager.FontProperties(fname=serif), font_manager.FontProperties(fname=mono)


def draw_technical(ax, spec, L, inks, fonts):
    """Street numbers, corner read-outs, name, date."""
    u, W, H, cx = L["u"], L["W"], L["H"], L["cx"]
    serif, mono = fonts
    ink = inks[0]
    soft = _mix(PAPER, ink, 0.62)
    s = L["cell"]
    # street numbers down the left: they keep a reader's place
    for r in range(L["rows"]):
        ax.text(L["tx"] - 0.12 * s - 2.4 * u, H - (L["ty"] + (r + 1) * L["ch"] - 0.42 * s), "%02d" % (r + 1),
                ha="right", va="center", fontproperties=mono, fontsize=2.3 * u * MM, color=soft, zorder=6)

    # corner read-outs
    def readout(x, y, ha, label, value):
        ax.text(x, H - (y - 5.6 * u), label, ha=ha, va="baseline", fontproperties=mono,
                fontsize=2.3 * u * MM, color=soft, zorder=6)
        ax.text(x, H - y, value, ha=ha, va="baseline", fontproperties=mono,
                fontsize=3.7 * u * MM, color=ink, zorder=6)

    mg = L["margin"]
    readout(mg, mg + 6 * u, "left", "LAT", spec.lat_text + "°")
    readout(W - mg, mg + 6 * u, "right", "LON", spec.lon_text + "°")
    readout(mg, H - mg, "left", "TIME", "%s:%s" % (spec.hhmm[:2], spec.hhmm[2:]))
    readout(W - mg, H - mg, "right", "WEIGHT", "%d g" % spec.grams)

    # name (lowercase serif), its houses directly above as a clue, date beneath
    cs = L["clue_s"]
    for (ch, let), x in zip(spec.slots, L["name_xs"]):
        ax.text(x, H - L["name_y"], ch, ha="center", va="baseline", fontproperties=serif,
                fontsize=L["name_em"] * MM, color=ink, zorder=6)
        if let:
            i, n = spec.code[let]
            draw_house(ax, x - cs / 2.0, L["clue_y"] - ASPECT * cs, cs, n, inks[i], H, zorder=5)
    d = spec.date
    ax.text(cx, H - L["date_y"], "%s · %s · %s" % (d[:4], d[4:6], d[6:]), ha="center", va="baseline",
            fontproperties=mono, fontsize=4.4 * u * MM, color=ink, zorder=6)


def new_page(W, H, color=PAPER, bleed=0.0):
    fig = plt.figure(figsize=((W + 2 * bleed) / 25.4, (H + 2 * bleed) / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(-bleed, W + bleed)
    ax.set_ylim(-bleed, H + bleed)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor(color)
    return fig, ax


_PDF_META = {"Creator": "townprint.py " + VERSION, "Producer": "townprint.py", "CreationDate": None,
             "Title": "little town birth print"}


def _compose(spec, palette, fonts, bleed=0.0, sky=True):
    inks = PALETTES[palette][1]
    L = layout(spec.W, spec.H, spec.slots, spec.words)
    fig, ax = new_page(spec.W, spec.H, PAPER, bleed)
    draw_town(ax, spec, L, palette)
    if sky:
        draw_sky(ax, spec, L, inks)
    draw_technical(ax, spec, L, inks, fonts)
    return fig


def render_print(spec, palette=1, pdf_path=None, png_path=None, dpi=150, fonts=None, background=True, bleed=0.0,
                 sky=True):
    """Draw the print. The PDF is vector at print size (plus bleed on every side if asked,
    and with no cream fill if background=False, for printing on cream paper stock).
    The PNG preview always shows the trimmed sheet on cream. Returns a PIL image if no png_path."""
    fonts = fonts or _fonts()
    fig = _compose(spec, palette, fonts, 0.0, sky)
    if pdf_path:
        fb = _compose(spec, palette, fonts, bleed, sky) if bleed else fig
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
# 6. reading the picture back (used by --verify)
# ----------------------------------------------------------------------------
# places that are always wall on a house, whatever its windows: (across, up from the street)
WALL_PROBES = ((0.092, 0.32), (0.908, 0.32), (0.092, 0.56), (0.908, 0.56), (0.5, 0.818))


def read_back(img, spec, palette=1):
    """Read every cell out of a picture of the whole sheet, the way the card says to: is
    it a house, what colour is it, and how many windows does it have. Returns the streets
    as lists of (colour, windows), with None for a tree."""
    inks = PALETTES[palette][1]
    L = layout(spec.W, spec.H, spec.slots, spec.words)
    a = np.asarray(img.convert("RGB"), dtype=float) / 255.0
    k = a.shape[1] / L["W"]                                             # pixels per millimetre
    ref = np.array([_rgb(PAPER)] + list(tree_tones(palette)) + [_rgb(c) for c in inks])
    first_ink = len(ref) - 3

    def seen(x, y):
        px = a[min(int(y * k), a.shape[0] - 1), min(int(x * k), a.shape[1] - 1)]
        return int(np.argmin(((ref - px) ** 2).sum(-1)))

    s, d = L["cell"], 0.4 * WIN_R
    found = []
    for r in range(L["rows"]):
        base = L["ty"] + (r + 1) * L["ch"] - GROUND * s                 # the street this row stands on
        row = []
        for c in range(L["cols"]):
            x = L["tx"] + c * s
            votes = [seen(x + fx * s, base - fy * s) for fx, fy in WALL_PROBES]
            wall = max(sorted(set(votes)), key=votes.count)
            if wall < first_ink:
                row.append(None)
                continue
            windows = 0
            for wy in WIN_Y:
                for wx in WIN_X:
                    looks = [seen(x + (wx + dx) * s, base - (wy + dy) * s) for dx in (-d, 0, d) for dy in (-d, 0, d)]
                    windows += sum(1 for v in looks if v != wall) > 4
            row.append((wall - first_ink, windows))
        found.append(row)
    return found


def verify(spec, palette=1, dpi=150, fonts=None, sky=True, quiet=False):
    """Render the sheet, read the houses back out of the pixels, and turn them into text
    with the chart. Returns (wrong, total, text)."""
    L = layout(spec.W, spec.H, spec.slots, spec.words)
    # enough pixels for the smallest house, without making a huge picture of a big sheet
    dpi = max(dpi, min(60 * 25.4 / L["cell"], 7000 * 25.4 / max(spec.W, spec.H)))
    img = render_print(spec, palette, dpi=dpi, fonts=fonts, sky=sky)
    found = read_back(img, spec, palette)
    want = [[spec.code[sign] if sign else None for sign in cells] for cells in L["streets"]]
    wrong = sum(1 for fr, wr in zip(found, want) for f, w in zip(fr, wr) if f != w)
    words = []
    for row in found:
        word = ""
        for f in row + [None]:                                          # the end of a street ends a word
            if f is None:
                if word:
                    words.append(word)
                word = ""
            else:
                word += (spec.chart[f[0]][f[1] - 1] if 1 <= f[1] <= 9 else None) or "?"
    text = " ".join(words)
    if not quiet:
        print("  read back : %s" % text)
    return wrong, L["cols"] * L["rows"], text


# ----------------------------------------------------------------------------
# 7. show-and-tell sheet
# ----------------------------------------------------------------------------
def draw_chart(ax, spec, inks, x, y, bw, bh, page_h, fp, size, mono, rule, show=(0, 1, 2), houses=False,
               color=SIGNAL):
    """The chart: a row for each colour, darkest first, and a column for each number of
    windows. show = the rows whose letters are written in. houses = draw each box's house."""
    gut = 0.9 * bw                                # room on the left for a blank house in the row's colour
    hs = min(0.56 * bw, 0.62 * bh / ASPECT)
    ds = min(2.6, 0.24 * bw)                      # size of the row and column numbers
    for n in range(9):
        ax.text(x + gut + (n + 0.5) * bw, page_h - (y - 1.5), "%d" % (n + 1), ha="center", va="baseline",
                fontproperties=mono, fontsize=ds * MM, color=rule)
    for i in range(3):
        by = y + i * bh
        ls = min(0.5 * gut, 0.66 * bh / ASPECT)
        ax.text(x + 0.12 * gut, page_h - (by + bh / 2.0 + 0.36 * ds), "%d" % (i + 1), ha="center", va="baseline",
                fontproperties=mono, fontsize=ds * MM, color=rule)
        draw_house(ax, x + 0.28 * gut + (0.60 * gut - ls) / 2.0, by + (bh - ASPECT * ls) / 2.0, ls, 0, inks[i],
                   page_h)
        for n in range(9):
            bx = x + gut + n * bw
            ax.add_patch(Rectangle((bx, page_h - by - bh), bw, bh, fc="none", ec=rule, lw=0.16 * MM))
            sign = spec.chart[i][n]
            if sign is None:
                continue
            if houses:
                draw_house(ax, bx + (bw - hs) / 2.0, by + 0.07 * bh, hs, n + 1, inks[i], page_h)
            if i in show:
                ty = by + bh - 0.11 * bh if houses else by + bh / 2.0 + 0.34 * size
                ax.text(bx + bw / 2.0, page_h - ty, sign, ha="center", va="baseline", fontproperties=fp,
                        fontsize=size * MM, color=color)


def chart_sheet(spec, path, palette=1, sentence="the quick brown fox jumps over the lazy dog", dpi=150, fonts=None):
    """The 26 houses with the letters they stand for under this name, and a test sentence."""
    serif, mono = fonts or _fonts()
    inks = PALETTES[palette][1]
    ink = inks[0]
    W, H = 210.0, 218.0
    fig, ax = new_page(W, H)
    soft = _mix(PAPER, ink, 0.6)
    ax.text(16, H - 20, "the 26 houses", fontproperties=serif, fontsize=9 * MM, color=ink, va="baseline")
    ax.text(16, H - 27.5, "three colours x nine window counts  ·  letters shown = this name  ·  a tree is a space",
            fontproperties=mono, fontsize=2.7 * MM, color=soft, va="baseline")
    draw_chart(ax, spec, inks, 16.0, 42.0, 17.8, 26.0, H, serif, 6.2, mono, soft, houses=True)
    yb = 42.0 + 3 * 26.0 + 7.0
    ax.add_line(Line2D([16, W - 16], [H - yb, H - yb], color=ink, lw=0.2 * MM))
    ax.text(16, H - (yb + 8.0), "test sentence", fontproperties=mono, fontsize=2.7 * MM, color=soft, va="baseline")
    s = 11.0
    tones = tree_tones(palette)
    for li, words in enumerate(wrap_words(sentence.split(), 15)):
        y = yb + 13.0 + li * 22.0
        ax.add_patch(Rectangle((17.0, H - (y + ASPECT * s)), 15 * s + 2.0, GROUND * s, fc=ink, ec="none"))
        for ci, sign in enumerate(street(words, 15, li, True)[:len(" ".join(words))]):
            x = 18.0 + ci * s
            if sign is None:
                draw_tree(ax, x, y, s, spec.season, tones, H)
            else:
                i, n = spec.code[sign]
                draw_house(ax, x, y, s, n, inks[i], H)
                ax.text(x + s / 2.0, H - (y + ASPECT * s + 3.6), sign, fontproperties=mono, fontsize=2.8 * MM,
                        color=soft, ha="center")
    fig.savefig(path, dpi=dpi, facecolor=PAPER, metadata={"Software": None})
    plt.close(fig)


# ----------------------------------------------------------------------------
# 8. the sealed card
# ----------------------------------------------------------------------------
def render_card(spec, path, palette=1, with_table=False, fonts=None, sky=True):
    """The sealed card as an A5 PDF, written for a child of about eight: page 1 the method
    with an empty chart to fill in, page 2 a worked example on this print's own name.
    with_table adds a third page, the finished chart."""
    serif, mono = fonts or _fonts()
    inks = PALETTES[palette][1]
    INK = inks[0]
    W, H, M = 148.0, 210.0, 12.0
    FS, LH = 2.7, 3.65
    CW = 0.61 * FS                                # width of one character of the mono face, with a little slack
    cols = int((W - 2 * M) / CW)
    white = "#FFFFFF"
    soft = _mix(white, INK, 0.62)
    dim = _mix(white, INK, 0.82)
    tones = tree_tones(palette, white)
    L = layout(spec.W, spec.H, spec.slots, spec.words) if spec.words else None

    def put(ax, x, y, text, size=FS, fp=mono, color=INK, ha="left"):
        ax.text(x, H - y, text, ha=ha, va="baseline", fontproperties=fp, fontsize=size * MM, color=color)

    def para(ax, y, text, color=INK, x=M, width=cols):
        for line in textwrap.wrap(text, width):
            put(ax, x, y, line, color=color)
            y += LH
        return y

    def head(ax, y, number, title):
        put(ax, M, y, "%s" % number, size=3.3, color=SIGNAL)
        put(ax, M + 5.0, y, title.upper(), size=2.9)
        return y + LH + 1.6

    def top(ax, title, sub):
        put(ax, M, 16.0, title, size=7.0, fp=serif)
        put(ax, M, 21.4, sub, size=2.45, color=soft)
        ax.add_line(Line2D([M, W - M], [H - 24.4, H - 24.4], color=INK, lw=0.2 * MM))

    name = "".join(ch for ch, _ in spec.slots)
    first = spec.letters[0]
    bw = (W - 2 * M) / 9.9
    pdf = PdfPages(path, metadata=dict(_PDF_META, Title="sealed card"))

    # ---- page 1: the method --------------------------------------------------------------
    fig, ax = new_page(W, H, white)
    top(ax, "the sealed card", "how to read the secret message in the little town of %s" % name)
    y = head(ax, 31.4, 1, "the houses")
    s, pitch = 7.6, 10.4
    for n in range(1, 10):
        hx = M + (n - 1) * pitch
        draw_house(ax, hx, y - 2.6, s, n, INK, H)
        put(ax, hx + s / 2.0, y - 2.6 + ASPECT * s + 3.4, "%d" % n, size=2.5, color=dim, ha="center")
    y += ASPECT * s + 5.6
    y = para(ax, y, "Every house is one letter. To find it you need two things: how many windows the house "
                    "has, and what colour its walls are.") + 1.0
    y = para(ax, y, "Count the windows: 1 to 9. They sit like the spots on a dice. Point at each one as you "
                    "count. Roofs and chimneys are just for show.") + 1.0
    y = para(ax, y, "There are three colours. Look at the walls, not the roof.") + 0.4
    for i in range(3):
        hx = M + i * 42.0
        draw_house(ax, hx, y - 1.4, 5.4, 0, inks[i], H)
        put(ax, hx + 7.4, y + 1.6, "colour %d" % (i + 1))
        put(ax, hx + 7.4, y + 1.6 + 0.9 * LH, COLOUR_NAMES[palette][i], size=2.45, color=soft)
    y += ASPECT * 5.4 + 3.2
    draw_tree(ax, M, y - 3.2, 6.2, spec.season, tones, H)
    tx = M + 8.6
    y = para(ax, y, "A tree is a gap between two words. Two trees side by side are still one gap. A word never "
                    "carries on to the next street.", x=tx, width=int((W - M - tx) / CW))
    y = head(ax, y + 2.6, 2, "fill in the chart")
    y = para(ax, y, "Use a pencil. Find the big name at the bottom of the print. Write its first letter in the "
                    "first box, at the top left. Carry on with the alphabet, one letter in each box: along "
                    "the top row, then the middle row, then the bottom row. After z, carry on with a, b, c. "
                    "Stop when all 26 letters are in. The last box stays empty.") + 5.4
    put(ax, M + 0.9 * bw, y - 5.2, "windows", size=2.45, color=dim)
    draw_chart(ax, spec, inks, M, y, bw, 7.8, H, serif, 4.4, mono, soft, show=())
    y = head(ax, y + 3 * 7.8 + 6.0, 3, "check it with the name")
    y = para(ax, y, "The little houses just above the big name spell the name. Take the first one. Its colour "
                    "tells you the row. Its windows tell you how far along. Is that box the first letter of "
                    "the name? Try them all. If they spell the name, your chart is right.")
    y = head(ax, y + 2.6, 4, "read the town")
    y = para(ax, y, "The streets are numbered down the left side. Start at street 1 and read each street from "
                    "left to right, one house at a time, in the same way.") + 1.0
    para(ax, y, "After each street, say its words out loud. If a word looks wrong, count its windows again.")
    pdf.savefig(fig, facecolor=white)
    plt.close(fig)

    # ---- page 2: worked example ----------------------------------------------------------
    fig, ax = new_page(W, H, white)
    top(ax, "worked example", "the same steps, done with the name on this print")
    y = head(ax, 31.4, "A", "the chart")
    zi = (25 - (ord(first) - ord("a"))) % 26                            # the box z lands in
    if first == "a":
        wrap = ("This name starts with a, so the alphabet runs straight through to z, in the box for "
                "colour 3 with 8 windows.")
    else:
        wrap = ("Look out for z: it goes in the box for colour %d with %d window%s, and a goes in the very "
                "next box." % (zi // 9 + 1, zi % 9 + 1, "" if zi % 9 == 0 else "s"))
    y = para(ax, y, "The big name on this print is %s. It starts with %s, so %s goes in the first box. Here "
                    "is the top row, done for you. The other two rows are yours to fill in. %s"
             % (name, first, first, wrap)) + 5.4
    put(ax, M + 0.9 * bw, y - 5.2, "windows", size=2.45, color=dim)
    draw_chart(ax, spec, inks, M, y, bw, 8.6, H, serif, 4.6, mono, soft, show=(0,))
    y = head(ax, y + 3 * 8.6 + 6.8, "B", "check it with the name")
    y = para(ax, y, "These are the little houses printed above the name, from left to right. Under each one "
                    "is its colour, then its number of windows, then its letter.") + 1.6
    letters = spec.letters
    pitch = min(11.0, (W - 2 * M) / len(letters))
    kk = max(pitch / 11.0, 0.55)
    for i, c in enumerate(letters):
        ink_i, n = spec.code[c]
        gx = M + i * pitch
        draw_house(ax, gx, y, 7.0 * kk, n, inks[ink_i], H)
        hy = y + ASPECT * 7.0 * kk
        put(ax, gx + 3.5 * kk, hy + 3.4, "%d·%d" % (ink_i + 1, n), size=2.3 * max(kk, 0.8), color=dim, ha="center")
        put(ax, gx + 3.5 * kk, hy + 8.4, c, size=4.6 * max(kk, 0.7), fp=serif, color=SIGNAL, ha="center")
    y += ASPECT * 7.0 * kk + 14.4
    tail = ""
    if L:
        tail = " There %s %d street%s to read." % ("is" if L["rows"] == 1 else "are", L["rows"],
                                                   "" if L["rows"] == 1 else "s")
    y = para(ax, y, "When your chart gives these houses the letters of the name, it is right. Now go and "
                    "read the town." + tail)
    y = head(ax, y + 3.2, "C", "the rest of the picture")
    clock = "%s:%s" % (spec.hhmm[:2], spec.hhmm[2:])
    if sky:
        if is_day(spec):
            y = para(ax, y, "The sun shows the time of day. It comes up on the left at 6 in the morning and "
                            "goes down on the right at 6 in the evening. On this print it stands at %s."
                     % clock) + 1.2
        else:
            y = para(ax, y, "The moon shows the time of night. It comes up on the left at 6 in the evening and "
                            "goes down on the right at 6 in the morning. On this print it stands at %s."
                     % clock) + 1.2
        y = para(ax, y, "Count the %s in the sky. There is one for every 100 grams of the weight: %d of them "
                        "for %d g." % ("birds" if is_day(spec) else "stars", flock_count(spec.grams),
                                       spec.grams)) + 1.2
    y = para(ax, y, "The trees are dressed for %s, the season of the birthday." % spec.season) + 1.2
    para(ax, y, "The two numbers in the top corners say where in the world the town is. The date, the time "
                "and the weight are printed too. None of them is part of the secret message.")
    pdf.savefig(fig, facecolor=white)
    plt.close(fig)

    # ---- page 3 (only in the copy to be kept elsewhere): the finished chart --------------
    if with_table:
        fig, ax = new_page(W, H, white)
        top(ax, "the finished chart", "the answer to step 2 for this print: keep it apart from the card")
        put(ax, M + 0.9 * bw, 32.8, "windows", size=2.45, color=dim)
        draw_chart(ax, spec, inks, M, 38.0, bw, 21.0, H, serif, 5.4, mono, soft, houses=True)
        para(ax, 38.0 + 3 * 21.0 + 7.0, "Each box shows a house and, under it, the letter that house stands for "
                                        "on this print. A tree is a gap between words.", color=soft)
        pdf.savefig(fig, facecolor=white)
        plt.close(fig)
    pdf.close()


# ----------------------------------------------------------------------------
# 9. command line
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
    ap = argparse.ArgumentParser(description="Generate a little-town birth print with a count-the-windows cipher.")
    ap.add_argument("--name"); ap.add_argument("--date"); ap.add_argument("--time")
    ap.add_argument("--weight"); ap.add_argument("--coords"); ap.add_argument("--message")
    ap.add_argument("--size", default="A3 portrait", help="e.g. 'A3 portrait', 'A2', '300x400 mm'")
    ap.add_argument("--palette", type=int, default=1, choices=sorted(PALETTES),
                    help="1 harbour (default), 2 meadow, 3 dusk")
    ap.add_argument("--bleed", type=float, default=0.0, metavar="MM",
                    help="extra cream on every side of the PDF for a print shop to trim off, e.g. 3")
    ap.add_argument("--no-background", action="store_true",
                    help="leave the cream out of the PDF, for printing on cream paper stock")
    ap.add_argument("--no-sky", action="store_true", help="leave out the sun or moon and the birds or stars")
    ap.add_argument("--out", default="out", help="output folder")
    ap.add_argument("--dpi", type=int, default=150, help="resolution of the PNG preview")
    ap.add_argument("--serif-font", help="path to a .ttf/.otf for the name (default: bundled STIX)")
    ap.add_argument("--mono-font", help="path to a .ttf/.otf for the read-outs (default: bundled DejaVu Sans Mono)")
    ap.add_argument("--verify", action="store_true",
                    help="after rendering, read every house back out of the picture and compare")
    ap.add_argument("--chart-sheet", action="store_true", help="also write the chart of all 26 houses as a PNG")
    ap.add_argument("--samples", action="store_true", help="also write a small PNG in each palette, to compare")
    args = ap.parse_args(argv)
    try:
        for f, p in (("name", "Name"), ("date", "Date of birth (YYYYMMDD)"), ("time", "Time of birth (HH:MM)"),
                     ("weight", "Birth weight (e.g. 3600 g)"), ("coords", "Coordinates (e.g. N 53.48, W 2.24)"),
                     ("message", "Message for the town (letters and spaces, up to %d characters)" % MAX_MESSAGE)):
            _ask(args, f, p)
        spec = Spec(args.name, args.date, args.time, args.weight, args.coords, args.message, args.size)
    except InputError as e:
        print("Input problem - " + str(e), file=sys.stderr)
        return 2
    if not 0 <= args.bleed <= 20:
        print("Input problem - bleed: between 0 and 20 mm", file=sys.stderr)
        return 2
    os.makedirs(args.out, exist_ok=True)
    fonts = _fonts(args.serif_font, args.mono_font)
    sky = not args.no_sky
    stem = os.path.join(args.out, spec.letters)
    pdf, png = stem + "_print.pdf", stem + "_preview.png"
    card, card2 = stem + "_card.pdf", stem + "_card_with_table.pdf"
    render_print(spec, args.palette, pdf, png, args.dpi, fonts, not args.no_background, args.bleed, sky)
    render_card(spec, card, args.palette, False, fonts, sky)
    render_card(spec, card2, args.palette, True, fonts, sky)
    L = layout(spec.W, spec.H, spec.slots, spec.words)
    print("name    : %s" % spec.letters)
    print("message : %s  (%d of %d characters, %d street%s, houses %.1f mm wide)" % (
        spec.message, len(spec.message), MAX_MESSAGE, L["rows"], "" if L["rows"] == 1 else "s", L["cell"]))
    for i in range(3):
        print("%s: %s" % ("colour %d" % (i + 1), " ".join(c or "." for c in spec.chart[i])))
    if sky:
        print("sky     : %s at %s:%s, %d %s" % ("sun" if is_day(spec) else "moon", spec.hhmm[:2], spec.hhmm[2:],
                                               flock_count(spec.grams), "birds" if is_day(spec) else "stars"))
    print("trees   : %s" % spec.season)
    print("palette : %d %s" % (args.palette, PALETTES[args.palette][0]))
    print("wrote   : %s   (vector, %g x %g mm%s)" % (pdf, spec.W + 2 * args.bleed, spec.H + 2 * args.bleed,
                                                   ", incl. %g mm bleed" % args.bleed if args.bleed else ""))
    print("          %s" % png)
    print("          %s   (the sealed card: method + worked example)" % card)
    print("          %s   (same, plus the finished chart: keep it elsewhere)" % card2)
    if args.chart_sheet:
        chart_sheet(spec, stem + "_chart.png", args.palette, fonts=fonts)
        print("          %s" % (stem + "_chart.png"))
    if args.samples:
        for pal in sorted(PALETTES):
            p = "%s_palette%d_%s.png" % (stem, pal, PALETTES[pal][0])
            render_print(spec, pal, None, p, 80, fonts, sky=sky)
            print("          %s" % p)
    if args.verify:
        print("read-back test (every house read out of the finished picture):")
        wrong, total, text = verify(spec, args.palette, 150, fonts, sky)
        print("  error rate: %d / %d houses and trees = %.2f%%" % (wrong, total, 100.0 * wrong / total))
        if wrong or text != spec.message:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
