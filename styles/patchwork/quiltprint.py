#!/usr/bin/env python3
"""
quiltprint.py - a patchwork birth print with a keyed nine-point cipher.

One script, four files out:
    <out>/<name>_print.pdf     vector artwork at print size
    <out>/<name>_preview.png   raster preview
    <out>/<name>_card.pdf      the "sealed card": how to read the patches
    <out>/<name>_card_with_table.pdf   the same card plus the three finished grids

Run it:
    python quiltprint.py --name oscar --date 20240317 --time 04:42 \
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
from matplotlib.patches import Circle, Rectangle, Wedge
from PIL import Image

VERSION = "1.0"
MM = 72.0 / 25.4  # points per millimetre

# ----------------------------------------------------------------------------
# colours
# ----------------------------------------------------------------------------
PAPER = "#F4ECD8"   # cream, the same sheet as the other styles
SIGNAL = "#F4511E"  # orange-red: labels and answers on the card
# Three inks per palette, always darkest first. The order is part of the cipher: the card
# tells the reader to rank the inks by how dark they are, so the print can still be read
# from a grey photocopy. No pink, as in the other styles.
PALETTES = {1: ("harbour", ("#17235C", "#F4511E", "#F5A300")),   # navy, orange-red, marigold
            2: ("meadow", ("#1B4D3E", "#2A9D8F", "#E3B23C")),    # forest, teal, straw
            3: ("dusk", ("#2B2140", "#2F6FDE", "#F0A830"))}      # aubergine, cobalt, amber


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
    clue patches. slots = [(display_char, letter_or_None)] for the printed name."""
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


MAX_MESSAGE = 300  # characters incl. spaces, one patch each: about fifty words


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
        raise InputError("message: %d characters after tidying; the quilt holds %d" % (len(msg), MAX_MESSAGE))
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
        self.header, self.rows, self.row27 = mixed_alphabet(self.letters)
        self.numbers = [self.date, self.hhmm, str(self.grams)]      # one per ink, darkest first
        self.sums = [digit_sums(n) for n in self.numbers]
        self.skips = [s[-1] % 9 for s in self.sums]
        self.grids = fill_grids(self.row27, self.skips)             # grids[ink 0-2][point 0-8] = sign
        self.code = {sign: (ink, p) for ink in range(3) for p, sign in enumerate(self.grids[ink])}


# ----------------------------------------------------------------------------
# 2. the cipher
# ----------------------------------------------------------------------------
SPACE = " "     # the 27th sign; written as a dash on the card


def mixed_alphabet(letters):
    """The name shuffles the alphabet, the way a keyword does in a columnar cipher.
    Write the name across a line, skipping repeated letters. Write the rest of the
    alphabet under it in rows of the same width, then the space sign. Copy the columns
    out top to bottom, in the alphabetical order of their top letters.
    Returns (header, rows, the row of 27 signs)."""
    header = []
    for c in letters:
        if c not in header:
            header.append(c)
    rest = [c for c in "abcdefghijklmnopqrstuvwxyz" if c not in header] + [SPACE]
    k = len(header)
    rows = [rest[i:i + k] for i in range(0, len(rest), k)]
    row27 = []
    for col in sorted(range(k), key=lambda i: header[i]):
        row27.append(header[col])
        row27.extend(r[col] for r in rows if col < len(r))
    return header, rows, row27


def digit_sums(number):
    """Casting out nines by hand: add the digits, then the digits of the answer, until
    one digit is left. Returns every total on the way, e.g. '20240317' -> [19, 10, 1]."""
    totals, s = [], str(number)
    while True:
        totals.append(sum(int(c) for c in s))
        s = str(totals[-1])
        if len(s) == 1:
            return totals


def fill_grids(row27, skips):
    """Deal the row of 27 into three piles like a pack of cards, one pile per ink. Each
    pile goes round the nine points of its grid in order, starting after as many points
    as its number says to skip."""
    grids = []
    for ink in range(3):
        grid = [None] * 9
        for j, sign in enumerate(row27[ink::3]):
            grid[(skips[ink] + j) % 9] = sign
        grids.append(grid)
    return grids


def shown(sign):
    return "-" if sign == SPACE else sign


# ----------------------------------------------------------------------------
# 3. the patches (a square of side 1; x right, y down)
# ----------------------------------------------------------------------------
# The nine points of a patch, numbered 1-9 the way a page is read: the corners, the
# middles of the sides and the centre. One round shape sits on one of them.
POINTS = [(c * 0.5, r * 0.5) for r in range(3) for c in range(3)]
R_CORNER, R_SIDE, R_CENTRE = 0.92, 0.46, 0.38      # quarter circle, half circle, whole circle
# the slice of the circle that lies inside the patch, in degrees (drawn with y up)
_SLICE = {0: (270, 360), 1: (180, 360), 2: (180, 270), 3: (270, 450), 5: (90, 270),
          6: (0, 90), 7: (0, 180), 8: (90, 180)}


def point_radius(p):
    return R_CENTRE if p == 4 else (R_CORNER if p in (0, 2, 6, 8) else R_SIDE)


def draw_patch(ax, x, y, s, point, ink, page_h, ground=None, zorder=2):
    """One patch of side s with its top-left corner at (x, y), measured in millimetres from
    the top-left of the page. point None = an empty patch, marked with a small cross."""
    if ground is not None:
        ax.add_patch(Rectangle((x, page_h - y - s), s, s, fc=ground, ec="none", zorder=zorder - 1))
    if point is None:
        cx, cy, d = x + s / 2.0, page_h - (y + s / 2.0), 0.075 * s
        for sx in (1, -1):
            ax.add_line(Line2D([cx - d, cx + d], [cy - sx * d, cy + sx * d], color=ink,
                               lw=max(0.022 * s, 0.12) * MM, solid_capstyle="round", zorder=zorder))
        return
    px, py = POINTS[point]
    c, r = (x + px * s, page_h - (y + py * s)), point_radius(point) * s
    if point == 4:
        ax.add_patch(Circle(c, r, fc=ink, ec="none", zorder=zorder))
    else:
        ax.add_patch(Wedge(c, r, _SLICE[point][0], _SLICE[point][1], fc=ink, ec="none", zorder=zorder))


# ----------------------------------------------------------------------------
# 4. page layout (millimetres, origin top-left, y down; u = 1 mm on an A3 sheet)
# ----------------------------------------------------------------------------
MAX_PATCH = 34.0     # widest a patch may get, so a short message does not turn into a few huge shapes


def quilt_shape(n, wq, hq, cap):
    """Columns, rows and patch size for n patches in a space wq x hq: the biggest patch
    wins, then the fewest empty patches, then the shape closest to the space."""
    best = None
    for cols in range(1, n + 1):
        rows = -(-n // cols)
        cell = min(wq / cols, hq / rows, cap)
        score = (round(cell, 6), n - cols * rows, -abs(math.log((cols / rows) / (wq / hq))))
        if best is None or score > best[0]:
            best = (score, cols, rows, cell)
    return best[1:]


def layout(W, H, slots, n_chars):
    u = min(W / 297.0, H / 420.0)
    cx = W / 2.0
    n = len(slots)
    pitch = min(13.0 * u, 215.0 * u / max(n, 1))
    top, bottom, side = 42.0 * u, H - 88.0 * u, 31.0 * u
    cols, rows, cell = quilt_shape(n_chars, W - 2 * side, bottom - top, MAX_PATCH * u)
    return dict(
        u=u, W=W, H=H, cx=cx, margin=20.0 * u,
        cols=cols, rows=rows, cell=cell, qw=cols * cell, qh=rows * cell,
        qx=cx - cols * cell / 2.0, qy=top + (bottom - top - rows * cell) / 2.0,
        frame=2.2 * u,
        name_xs=[cx + (i - (n - 1) / 2.0) * pitch for i in range(n)], name_em=pitch / 0.9,
        clue_s=min(7.5 * u, 0.62 * pitch), clue_y=H - 75.0 * u,
        name_y=H - 54.0 * u, date_y=H - 42.0 * u,
    )


# ----------------------------------------------------------------------------
# 5. the artwork
# ----------------------------------------------------------------------------
def cloth(base=PAPER):
    """The second, slightly darker cloth of the chequered ground."""
    return _mix(base, "#C9A86A", 0.22)


def draw_quilt(ax, spec, L, inks):
    """The patches, read like a page, on a chequered ground, with a seam between each."""
    H, s, cols, rows = L["H"], L["cell"], L["cols"], L["rows"]
    oat, soft = cloth(), _mix(PAPER, inks[0], 0.45)
    for k in range(cols * rows):
        r, c = divmod(k, cols)
        x, y = L["qx"] + c * s, L["qy"] + r * s
        ground = oat if (r + c) % 2 else None
        if k < len(spec.message):
            ink, p = spec.code[spec.message[k]]
            draw_patch(ax, x, y, s, p, inks[ink], H, ground)
        else:
            draw_patch(ax, x, y, s, None, soft, H, ground)
    lw = 0.34 * L["u"] * MM
    for c in range(1, cols):
        x = L["qx"] + c * s
        ax.add_line(Line2D([x, x], [H - L["qy"], H - L["qy"] - L["qh"]], color=PAPER, lw=lw, zorder=3))
    for r in range(1, rows):
        y = H - (L["qy"] + r * s)
        ax.add_line(Line2D([L["qx"], L["qx"] + L["qw"]], [y, y], color=PAPER, lw=lw, zorder=3))


def ripple_count(grams):
    return max(2, int(grams / 100.0 + 0.5))


def stitch_centre(spec, L):
    """Where the quilting starts: the birthplace, with the quilt read as a map of the world
    (longitude 180 W to 180 E across, latitude 90 N to 90 S down)."""
    return (L["qx"] + (spec.lon + 180.0) / 360.0 * L["qw"],
            L["qy"] + (90.0 - spec.lat) / 180.0 * L["qh"])


def time_direction(spec):
    """Unit vector of the birth time on a 24-hour dial: midnight at the top, clockwise."""
    phi = 2 * math.pi * (int(spec.hhmm[:2]) * 60 + int(spec.hhmm[2:])) / 1440.0
    return math.sin(phi), -math.cos(phi)


def draw_stitching(ax, spec, L, inks):
    """Quilting lines, sewn in the colour of the sheet so that they only show where they
    cross an ink. Ripples spread from the birthplace, one for every hundred grams, and a
    single straight line of longer stitches points to the time of birth."""
    u, H = L["u"], L["H"]
    clip = Rectangle((L["qx"], H - L["qy"] - L["qh"]), L["qw"], L["qh"], transform=ax.transData)
    sx, sy = stitch_centre(spec, L)
    far = max(math.hypot(sx - x, sy - y) for x in (L["qx"], L["qx"] + L["qw"])
              for y in (L["qy"], L["qy"] + L["qh"]))
    n = ripple_count(spec.grams)
    for k in range(1, n + 1):
        ring = Circle((sx, H - sy), far * k / n, fc="none", ec=PAPER, lw=0.26 * u * MM,
                      ls=(0, (5.0, 3.6)), zorder=4)
        ring.set_clip_path(clip)
        ax.add_patch(ring)
    dx, dy = time_direction(spec)
    ray = Line2D([sx, sx + far * dx], [H - sy, H - (sy + far * dy)], color=PAPER, lw=0.44 * u * MM,
                 ls=(0, (7.0, 2.2)), zorder=4)
    ray.set_clip_path(clip)
    ax.add_line(ray)
    knot = Circle((sx, H - sy), 0.9 * u, fc=PAPER, ec=inks[0], lw=0.22 * u * MM, zorder=4)
    knot.set_clip_path(clip)
    ax.add_patch(knot)


def _fonts(serif=None, mono=None):
    base = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
    serif = serif or os.path.join(base, "STIXGeneral.ttf")
    mono = mono or os.path.join(base, "DejaVuSansMono.ttf")
    return font_manager.FontProperties(fname=serif), font_manager.FontProperties(fname=mono)


def draw_technical(ax, spec, L, inks, fonts, stitches=True):
    """Frame, row and column numbers, the time mark, corner read-outs, name, date."""
    u, W, H, cx = L["u"], L["W"], L["H"], L["cx"]
    serif, mono = fonts
    ink = inks[0]
    soft = _mix(PAPER, ink, 0.62)
    g, s = L["frame"], L["cell"]
    x0, y0, x1, y1 = L["qx"] - g, L["qy"] - g, L["qx"] + L["qw"] + g, L["qy"] + L["qh"] + g
    ax.add_patch(Rectangle((x0, H - y1), x1 - x0, y1 - y0, fc="none", ec=ink, lw=0.30 * u * MM, zorder=5))
    # row numbers down the left, column numbers along the top: they keep a reader's place
    step = 1 if s >= 4.0 * u else 5
    for r in range(L["rows"]):
        if (r + 1) % step == 0:
            ax.text(x0 - 2.0 * u, H - (L["qy"] + (r + 0.5) * s), "%02d" % (r + 1), ha="right", va="center",
                    fontproperties=mono, fontsize=2.3 * u * MM, color=soft, zorder=6)
    for c in range(L["cols"]):
        if (c + 1) % step == 0:
            ax.text(L["qx"] + (c + 0.5) * s, H - (y0 - 2.0 * u), "%02d" % (c + 1), ha="center", va="baseline",
                    fontproperties=mono, fontsize=2.3 * u * MM, color=soft, zorder=6)
    # the moment of birth: a dot on the frame where the long stitches leave the quilt
    if stitches:
        sx, sy = stitch_centre(spec, L)
        dx, dy = time_direction(spec)
        t = min(t for t in ((x1 - sx) / dx if dx > 1e-9 else None, (x0 - sx) / dx if dx < -1e-9 else None,
                            (y1 - sy) / dy if dy > 1e-9 else None, (y0 - sy) / dy if dy < -1e-9 else None)
                if t is not None)
        ax.add_patch(Circle((sx + t * dx, H - (sy + t * dy)), 1.3 * u, fc=ink, ec="none", zorder=5))

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

    # name (lowercase serif), its patches directly above as a clue, date beneath
    cs = L["clue_s"]
    for (ch, let), x in zip(spec.slots, L["name_xs"]):
        ax.text(x, H - L["name_y"], ch, ha="center", va="baseline", fontproperties=serif,
                fontsize=L["name_em"] * MM, color=ink, zorder=6)
        if let:
            i, p = spec.code[let]
            draw_patch(ax, x - cs / 2.0, L["clue_y"] - cs / 2.0, cs, p, inks[i], H, cloth(), zorder=5)
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


_PDF_META = {"Creator": "quiltprint.py " + VERSION, "Producer": "quiltprint.py", "CreationDate": None,
             "Title": "patchwork birth print"}


def _compose(spec, palette, fonts, bleed=0.0, stitches=True):
    inks = PALETTES[palette][1]
    L = layout(spec.W, spec.H, spec.slots, len(spec.message))
    fig, ax = new_page(spec.W, spec.H, PAPER, bleed)
    draw_quilt(ax, spec, L, inks)
    if stitches:
        draw_stitching(ax, spec, L, inks)
    draw_technical(ax, spec, L, inks, fonts, stitches)
    return fig


def render_print(spec, palette=1, pdf_path=None, png_path=None, dpi=150, fonts=None, background=True, bleed=0.0,
                 stitches=True):
    """Draw the print. The PDF is vector at print size (plus bleed on every side if asked,
    and with no cream fill if background=False, for printing on cream paper stock).
    The PNG preview always shows the trimmed sheet on cream. Returns a PIL image if no png_path."""
    fonts = fonts or _fonts()
    fig = _compose(spec, palette, fonts, 0.0, stitches)
    if pdf_path:
        fb = _compose(spec, palette, fonts, bleed, stitches) if bleed else fig
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
def read_back(img, spec, palette=1):
    """Read every patch out of a picture of the whole sheet, the way the card says to:
    which ink, and which of the nine points the round shape sits on. Returns one
    (ink, point) per patch, or None for an empty patch."""
    inks = PALETTES[palette][1]
    L = layout(spec.W, spec.H, spec.slots, len(spec.message))
    a = np.asarray(img.convert("RGB"), dtype=float) / 255.0
    k = a.shape[1] / L["W"]                                             # pixels per millimetre
    ref = np.array([_rgb(PAPER), cloth()] + [_rgb(c) for c in inks])   # two cloths, then the inks
    n = 14
    f = 0.07 + 0.86 * (np.arange(n) + 0.5) / n                          # sample points, clear of the seams
    fx, fy = np.meshgrid(f, f)
    shapes = [np.hypot(fx - px, fy - py) <= point_radius(p) for p, (px, py) in enumerate(POINTS)]
    found = []
    for i in range(L["cols"] * L["rows"]):
        r, c = divmod(i, L["cols"])
        xs = ((L["qx"] + (c + fx) * L["cell"]) * k).astype(int)
        ys = ((L["qy"] + (r + fy) * L["cell"]) * k).astype(int)
        seen = np.argmin(((a[ys, xs][:, :, None, :] - ref) ** 2).sum(-1), axis=-1)
        seen = np.where(seen < 2, 0, seen - 1)                          # 0 = cloth, 1-3 = an ink
        best, score = None, (seen == 0).mean()
        for ink in range(3):
            for p in range(9):
                m = (np.where(shapes[p], ink + 1, 0) == seen).mean()
                if m > score:
                    best, score = (ink, p), m
        found.append(best)
    return found


def verify(spec, palette=1, dpi=150, fonts=None, stitches=True, quiet=False):
    """Render the sheet, read the patches back out of the pixels, and turn them into text
    with the grids. Returns (wrong, total, text)."""
    img = render_print(spec, palette, dpi=dpi, fonts=fonts, stitches=stitches)
    found = read_back(img, spec, palette)
    want = [spec.code[ch] for ch in spec.message] + [None] * (len(found) - len(spec.message))
    wrong = sum(1 for f, w in zip(found, want) if f != w)
    text = "".join(spec.grids[f[0]][f[1]] if f else "" for f in found)
    if not quiet:
        print("  read back : %s" % text)
    return wrong, len(found), text


# ----------------------------------------------------------------------------
# 7. show-and-tell sheet
# ----------------------------------------------------------------------------
def draw_grid(ax, spec, ink_i, inks, x, y, s, px, py, page_h, ground, fp, size, color=SIGNAL):
    """The nine patches of one ink, laid out as the nine points are, each with its sign beneath."""
    for p in range(9):
        r, c = divmod(p, 3)
        gx, gy = x + c * px, y + r * py
        draw_patch(ax, gx, gy, s, p, inks[ink_i], page_h, ground)
        ax.text(gx + s / 2.0, page_h - (gy + s + 0.82 * size), shown(spec.grids[ink_i][p]).replace("-", "–"),
                ha="center", va="baseline", fontproperties=fp, fontsize=size * MM, color=color)


def patch_sheet(spec, path, palette=1, sentence="the quick brown fox jumps over the lazy dog", dpi=150, fonts=None):
    """The 27 patches with the signs they stand for under this key, and a test sentence."""
    serif, mono = fonts or _fonts()
    inks = PALETTES[palette][1]
    ink, oat = inks[0], cloth()
    W, H = 210.0, 206.0
    fig, ax = new_page(W, H)
    soft = _mix(PAPER, ink, 0.6)
    ax.text(16, H - 20, "the 27 patches", fontproperties=serif, fontsize=9 * MM, color=ink, va="baseline")
    ax.text(16, H - 27.5, "three inks x nine points  ·  sign shown = this key  ·  a dash is the space",
            fontproperties=mono, fontsize=2.7 * MM, color=soft, va="baseline")
    labels = ("date", "time", "weight")
    for i in range(3):
        x = 16.0 + i * 62.0
        ax.text(x, H - 40.0, "INK %d  ·  %s  ·  skip %d" % (i + 1, labels[i], spec.skips[i]),
                fontproperties=mono, fontsize=2.5 * MM, color=soft, va="baseline")
        draw_grid(ax, spec, i, inks, x, 45.0, 14.0, 17.0, 24.0, H, oat, serif, 6.2)
    yb = 45.0 + 3 * 24.0 + 6.0
    ax.add_line(Line2D([16, W - 16], [H - yb, H - yb], color=ink, lw=0.2 * MM))
    ax.text(16, H - (yb + 8.0), "test sentence", fontproperties=mono, fontsize=2.7 * MM, color=soft, va="baseline")
    lines, cur = [], ""
    for wd in sentence.split():
        if len(cur) + len(wd) + 1 > 14:
            lines.append(cur + " "); cur = wd
        else:
            cur = (cur + " " + wd) if cur else wd
    lines.append(cur)
    for li, line in enumerate(lines):
        y = yb + 14.0 + li * 20.0
        for ci, ch in enumerate(line):
            x = 18.0 + ci * 11.6
            i, p = spec.code[ch]
            draw_patch(ax, x, y, 10.6, p, inks[i], H, oat)
            ax.text(x + 5.3, H - (y + 14.6), shown(ch), fontproperties=mono, fontsize=2.8 * MM, color=soft, ha="center")
    fig.savefig(path, dpi=dpi, facecolor=PAPER, metadata={"Software": None})
    plt.close(fig)


# ----------------------------------------------------------------------------
# 8. the sealed card
# ----------------------------------------------------------------------------
def sum_text(number, totals):
    """The digit sums written out as a person would: '2+0+2+4+0+3+1+7 = 19   1+9 = 10   1+0 = 1'."""
    parts, s = [], str(number)
    for t in totals:
        parts.append("%s = %d" % ("+".join(s), t))
        s = str(t)
    return "   ".join(parts) + ("   and 9 counts as 0" if totals[-1] == 9 else "")


def render_card(spec, path, palette=1, with_table=False, fonts=None):
    """The sealed card as an A5 PDF: page 1 the method, page 2 a worked example on this
    print's own name and numbers. with_table adds a third page, the three finished grids."""
    serif, mono = fonts or _fonts()
    inks = PALETTES[palette][1]
    INK = inks[0]
    W, H, M = 148.0, 210.0, 12.0
    FS, LH = 2.45, 3.3
    CW = 0.61 * FS                                # width of one character of the mono face, with a little slack
    cols = int((W - 2 * M) / CW)
    white = "#FFFFFF"
    soft = _mix(white, INK, 0.62)
    dim = _mix(white, INK, 0.82)
    ground = cloth(white)
    L = layout(spec.W, spec.H, spec.slots, len(spec.message)) if spec.message else None

    def put(ax, x, y, text, size=FS, fp=mono, color=INK, ha="left"):
        ax.text(x, H - y, text, ha=ha, va="baseline", fontproperties=fp, fontsize=size * MM, color=color)

    def para(ax, y, text, indent=0, color=INK, first=None, x=M, width=cols):
        for i, line in enumerate(textwrap.wrap(text, width - indent)):
            if i == 0 and first:
                put(ax, x, y, first, color=SIGNAL)
            put(ax, x + indent * CW, y, line, color=color)
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

    def swatch(ax, x, y, ink_i, s=2.6):
        ax.add_patch(Rectangle((x, H - y - 0.25), s, s, fc=inks[ink_i], ec="none"))

    name = "".join(ch for ch, _ in spec.slots)
    d8 = spec.date
    seen = ("%s · %s · %s" % (d8[:4], d8[4:6], d8[6:]), "%s:%s" % (spec.hhmm[:2], spec.hhmm[2:]),
            "%d g" % spec.grams)
    pdf = PdfPages(path, metadata=dict(_PDF_META, Title="sealed card"))

    # ---- page 1: the method --------------------------------------------------------------
    fig, ax = new_page(W, H, white)
    top(ax, "the sealed card", "how to read the patches on the birth quilt of %s" % name)
    y = head(ax, 30.6, 1, "the patches")
    s, px, py = 8.4, 11.2, 12.2
    for p in range(9):
        r, c = divmod(p, 3)
        draw_patch(ax, M + c * px, y - 2.0 + r * py, s, p, INK, H, ground)
        put(ax, M + c * px + s / 2.0, y - 2.0 + r * py + s + 2.4, "%d" % (p + 1), size=2.2, color=dim, ha="center")
    tx = M + 3 * px + 2.4
    tw = int((W - M - tx) / CW)
    yt = para(ax, y, "Every patch is a square holding one round shape in one of three inks. Find the centre "
                     "of the round shape. It sits on one of nine points of the square, numbered the way you "
                     "read a page: 1 2 3 along the top, 4 5 6 across the middle, 7 8 9 along the bottom. A "
                     "whole circle has its centre on point 5. A half circle has its centre in the middle of "
                     "its straight edge, which lies along a side of the patch. A quarter circle has its centre "
                     "at its sharp corner, where its two straight edges meet, in a corner of the patch.",
              x=tx, width=tw) + 1.2
    put(ax, tx, yt, "The three inks, darkest first:")
    yt += LH + 0.5
    for i in range(3):
        swatch(ax, tx + i * 19.0, yt, i)
        put(ax, tx + i * 19.0 + 3.8, yt, "ink %d" % (i + 1))
    draw_patch(ax, tx + 57.0, yt - 3.0, 3.4, None, INK, H, ground)
    put(ax, tx + 62.0, yt, "empty patch")
    y = head(ax, max(y - 2.0 + 3 * py, yt) + 5.6, 2, "mix the alphabet")
    y = para(ax, y, "Write the large name across a line. Use its letters only, without spaces, hyphens or "
                    "accents, and leave out any letter you have already written. Under it write the rest of "
                    "the alphabet in rows of the same width, a to z, leaving out the letters of the name. Last "
                    "of all write a dash: it stands for the space between words. The last row may be short. "
                    "Now copy the columns out one "
                    "after another, each from top to bottom, taking the columns in the alphabetical order of "
                    "their top letters. You have a row of 27 signs: 26 letters and the dash.")
    y = head(ax, y + 2.2, 3, "fill the three grids")
    y = para(ax, y, "Deal the row into three piles, like a pack of cards: the first sign to ink 1, the second to "
                    "ink 2, the third to ink 3, the fourth to ink 1 again, and so on. Each ink gets nine signs. "
                    "Draw a square of nine points for each ink. A number printed on the sheet says where each "
                    "one starts:") + 0.8
    for i, text in enumerate(("the date under the name: year, month, day.",
                              "the time in the bottom-left corner: hours, then minutes.",
                              "the weight in the bottom-right corner: the number of grams.")):
        y = para(ax, y, text, indent=8, first="INK %d" % (i + 1))
    y = para(ax, y + 0.8, "Add up the digits of the number, every digit and nothing else. If the answer has "
                          "two digits, add those together, "
                          "and again if need be, until one digit is left. Count a 9 as 0. Skip that many points: "
                          "with 0 the first of the nine signs goes on point 1, with 1 it goes on point 2, with 8 "
                          "on point 9. The rest follow in order on the next points, and after point 9 comes "
                          "point 1.")
    y = head(ax, y + 2.2, 4, "check with the name")
    y = para(ax, y, "The small patches directly above the name spell the name, letter for letter. Read them "
                    "with your grids before you start on the quilt.")
    y = head(ax, y + 2.2, 5, "read the quilt")
    y = para(ax, y, "Read it like a page: the top row first, each row from left to right. The small numbers "
                    "down the left side count the rows. For each patch, the ink says which grid to look in "
                    "and the point the shape sits on says which sign to take. A dash is a space. A small "
                    "cross marks an empty patch after the end of the message.") + 0.8
    y = para(ax, y, "The cloth behind the shapes is chequered, and a thin seam runs between the patches. Read "
                    "each patch on its own: two shapes that touch across a seam are two patches.") + 0.8
    para(ax, y, "Nothing else on the sheet is part of the key or the message: not the latitude and longitude "
                "in the top corners, and not the lines of stitching.")
    pdf.savefig(fig, facecolor=white)
    plt.close(fig)

    # ---- page 2: worked example ----------------------------------------------------------
    fig, ax = new_page(W, H, white)
    top(ax, "worked example", "the same steps, done with the name and numbers on this print")
    y = head(ax, 30.6, 1, "the alphabet")
    ix = M + 8 * CW
    put(ax, M, y, "NAME", color=SIGNAL)
    put(ax, ix, y, "  ".join(spec.header), color=SIGNAL)
    y += LH
    rows = spec.rows if len(spec.rows) <= 6 else spec.rows[:4] + [None] + spec.rows[-1:]
    for row in rows:
        put(ax, ix, y, "  ".join(shown(c) for c in row) if row else ". . .", color=INK if row else soft)
        y += LH
    y = para(ax, y + 0.8, "Columns in the alphabetical order of their top letters: %s." % " ".join(
        sorted(spec.header))) + 0.6
    y = para(ax, y, "Copied out: %s" % " ".join(shown(c) for c in spec.row27)) + 0.6
    y = para(ax, y, "Dealt into three piles:") + 0.4
    for i in range(3):
        put(ax, M, y, "INK %d" % (i + 1), color=SIGNAL)
        swatch(ax, ix, y, i)
        put(ax, ix + 5.0, y, "  ".join(shown(c) for c in spec.row27[i::3]))
        y += LH + 0.4
    y = head(ax, y + 2.4, 2, "the three numbers")
    for i, label in enumerate(("DATE", "TIME", "WEIGHT")):
        put(ax, M, y, "INK %d" % (i + 1), color=SIGNAL)
        swatch(ax, ix, y, i)
        put(ax, ix + 5.0, y, "%-8s%s" % (label, seen[i]))
        put(ax, W - M, y, "skip %d" % spec.skips[i], color=SIGNAL, ha="right")
        put(ax, ix + 5.0, y + LH, sum_text(spec.numbers[i], spec.sums[i]), color=soft)
        y += 2 * LH + 0.9
    y = head(ax, y + 1.6, 3, "the grid for ink 1")
    s, px, py = 8.0, 10.6, 11.6
    draw_grid(ax, spec, 0, inks, M, y - 2.0, s, px, py, H, ground, mono, 2.6)
    nine = [shown(c) for c in spec.row27[0::3]]
    k = spec.skips[0]
    tx = M + 3 * px + 2.4
    tw = int((W - M - tx) / CW)
    if k == 0:
        how = ("The date says skip 0, so %s goes on point 1 and the other eight follow in order, ending with "
               "%s on point 9." % (nine[0], nine[8]))
    else:
        how = ("The date says skip %d, so %s goes on point %d%s. After point 9 comes point 1: the last sign, "
               "%s, lands on point %d." % (k, nine[0], k + 1,
                                           ", %s on point %d and so on" % (nine[1], k + 2) if k < 8 else "",
                                           nine[8], k))
    yt = para(ax, y, "The nine signs of ink 1 are %s." % " ".join(nine), x=tx, width=tw) + 0.8
    yt = para(ax, yt, how, x=tx, width=tw) + 0.8
    para(ax, yt, "Under each patch is the sign it stands for. Fill the grids for ink 2 and ink 3 in the same "
                 "way, each from its own nine signs and its own number.", x=tx, width=tw)
    y = head(ax, y - 2.0 + 3 * py + 3.6, 4, "check with the name")
    y = para(ax, y, "The patches printed directly above the name, left to right. Under each: its ink and its "
                    "point, then the letter.") + 1.2
    letters = spec.letters
    pitch = min(10.0, (W - 2 * M) / len(letters))
    kk = max(pitch / 10.0, 0.6)
    for i, c in enumerate(letters):
        ink_i, p = spec.code[c]
        gx = M + i * pitch
        draw_patch(ax, gx, y, 6.6 * kk, p, inks[ink_i], H, ground)
        put(ax, gx + 3.3 * kk, y + 6.6 * kk + 3.0, "%d·%d" % (ink_i + 1, p + 1), size=2.2 * max(kk, 0.8),
            color=dim, ha="center")
        put(ax, gx + 3.3 * kk, y + 6.6 * kk + 7.6, c, size=4.4 * max(kk, 0.7), fp=serif, color=SIGNAL, ha="center")
    y += 6.6 * kk + 12.4
    tail = "Now go and read the quilt."
    if L:
        blanks = L["cols"] * L["rows"] - len(spec.message)
        tail = "Now read the quilt: %d row%s of %d patch%s%s." % (
            L["rows"], "" if L["rows"] == 1 else "s", L["cols"], "" if L["cols"] == 1 else "es",
            "" if not blanks else ", the last %s empty" % ("one" if blanks == 1 else "%d" % blanks))
    para(ax, y, "When your own grids give these patches the letters of the name, the grids are right. " + tail)
    pdf.savefig(fig, facecolor=white)
    plt.close(fig)

    # ---- page 3 (only in the copy to be kept elsewhere): the finished grids --------------
    if with_table:
        fig, ax = new_page(W, H, white)
        top(ax, "the three grids", "the finished answer to step 3 for this print: keep it apart from the card")
        s, px, py = 10.6, 12.6, 20.0
        for i, label in enumerate(("date", "time", "weight")):
            x = M + i * 43.5
            put(ax, x, 36.0, "INK %d" % (i + 1), color=SIGNAL)
            swatch(ax, x + 6 * CW, 36.0, i)
            put(ax, x + 6 * CW + 4.2, 36.0, "%s, skip %d" % (label, spec.skips[i]), color=soft)
            draw_grid(ax, spec, i, inks, x, 41.0, s, px, py, H, ground, serif, 5.6)
        y = para(ax, 41.0 + 3 * py + 6.0, "Each grid is laid out as the nine points are. The sign under a patch "
                                         "is what that patch stands for on this print. The dash is the space "
                                         "between words.", color=soft)
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
    ap = argparse.ArgumentParser(description="Generate a patchwork birth print with a keyed nine-point cipher.")
    ap.add_argument("--name"); ap.add_argument("--date"); ap.add_argument("--time")
    ap.add_argument("--weight"); ap.add_argument("--coords"); ap.add_argument("--message")
    ap.add_argument("--size", default="A3 portrait", help="e.g. 'A3 portrait', 'A2', '300x400 mm'")
    ap.add_argument("--palette", type=int, default=1, choices=sorted(PALETTES),
                    help="1 harbour (default), 2 meadow, 3 dusk")
    ap.add_argument("--bleed", type=float, default=0.0, metavar="MM",
                    help="extra cream on every side of the PDF for a print shop to trim off, e.g. 3")
    ap.add_argument("--no-background", action="store_true",
                    help="leave the cream out of the PDF, for printing on cream paper stock")
    ap.add_argument("--no-stitches", action="store_true", help="leave out the quilting lines")
    ap.add_argument("--out", default="out", help="output folder")
    ap.add_argument("--dpi", type=int, default=150, help="resolution of the PNG preview")
    ap.add_argument("--serif-font", help="path to a .ttf/.otf for the name (default: bundled STIX)")
    ap.add_argument("--mono-font", help="path to a .ttf/.otf for the read-outs (default: bundled DejaVu Sans Mono)")
    ap.add_argument("--verify", action="store_true",
                    help="after rendering, read every patch back out of the picture and compare")
    ap.add_argument("--patch-sheet", action="store_true", help="also write the chart of all 27 patches as a PNG")
    ap.add_argument("--samples", action="store_true", help="also write a small PNG in each palette, to compare")
    args = ap.parse_args(argv)
    try:
        for f, p in (("name", "Name"), ("date", "Date of birth (YYYYMMDD)"), ("time", "Time of birth (HH:MM)"),
                     ("weight", "Birth weight (e.g. 3600 g)"), ("coords", "Coordinates (e.g. N 53.48, W 2.24)"),
                     ("message", "Message for the quilt (letters and spaces, up to %d characters)" % MAX_MESSAGE)):
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
    stitches = not args.no_stitches
    stem = os.path.join(args.out, spec.letters)
    pdf, png = stem + "_print.pdf", stem + "_preview.png"
    card, card2 = stem + "_card.pdf", stem + "_card_with_table.pdf"
    render_print(spec, args.palette, pdf, png, args.dpi, fonts, not args.no_background, args.bleed, stitches)
    render_card(spec, card, args.palette, False, fonts)
    render_card(spec, card2, args.palette, True, fonts)
    L = layout(spec.W, spec.H, spec.slots, len(spec.message))
    print("name    : %s" % spec.letters)
    print("message : %s  (%d of %d characters, %d row%s of %d)" % (
        spec.message, len(spec.message), MAX_MESSAGE, L["rows"], "" if L["rows"] == 1 else "s", L["cols"]))
    print("alphabet: %s" % " ".join(shown(c) for c in spec.row27))
    print("piles   : %s" % " | ".join(" ".join(shown(c) for c in spec.row27[i::3]) for i in range(3)))
    print("skips   : date %d, time %d, weight %d" % tuple(spec.skips))
    print("palette : %d %s" % (args.palette, PALETTES[args.palette][0]))
    print("wrote   : %s   (vector, %g x %g mm%s)" % (pdf, spec.W + 2 * args.bleed, spec.H + 2 * args.bleed,
                                                   ", incl. %g mm bleed" % args.bleed if args.bleed else ""))
    print("          %s" % png)
    print("          %s   (the sealed card: method + worked example)" % card)
    print("          %s   (same, plus the three finished grids: keep it elsewhere)" % card2)
    if args.patch_sheet:
        patch_sheet(spec, stem + "_patches.png", args.palette, fonts=fonts)
        print("          %s" % (stem + "_patches.png"))
    if args.samples:
        for pal in sorted(PALETTES):
            p = "%s_palette%d_%s.png" % (stem, pal, PALETTES[pal][0])
            render_print(spec, pal, None, p, 80, fonts, stitches=stitches)
            print("          %s" % p)
    if args.verify:
        print("read-back test (every patch read out of the finished picture):")
        wrong, total, text = verify(spec, args.palette, 150, fonts, stitches)
        print("  error rate: %d / %d patches = %.2f%%" % (wrong, total, 100.0 * wrong / total))
        if wrong or text != spec.message:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
