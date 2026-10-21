"""Read the Hinode quick-look (QL) movies and full-Sun images of NAOJ.

https://hinode.nao.ac.jp/en/for-researchers/qlmovies/ has one page per day
(``movie_sirius/YYYY/MM/DD/hsc_qlYYYYMMDD.html``) listing XRT full-Sun images
(XRT_FFI*.png), XRT / SOT movies (``<INST>_<KIND>yyyymmddHHMMSS_HHMMSS.mpg``)
and SOT/SP scans.  Every movie frame and FFI image is an IDL plot whose title
carries the UT of the exposure (to the millisecond) and whose axes give the
helioprojective X / Y in arcsec.  This module

* lists the products of a day (``day_products``) and caches the files
  (``fetch``) under data/cache/hinode_ql/,
* decodes the movies with ffmpeg (``iter_frames``) and reads PNG files
  (``read_image``) without extra Python packages,
* reads the title time (``frame_time``) and the plot axes (``plot_axes``) by
  matching the IDL (Hershey) glyphs against templates embedded below,
* gives every frame of a movie with its time and axes (``movie_frames``).

Checked on 837 frames of 130 products of 2006-2026 (SOT/FG 640x480, XRT
480x480, FFI 512x512; 54 products not used for the templates): every title
was read, times increase and the first / last frame agree with the file name
to the second; 61 titles and the axis labels of 16 frames were checked by
eye.  Unreadable titles give None rather than a guess.  Caveats: the title
time is the exposure start (XRT exposures
last up to seconds); the axes come from the pointing in the headers and their
tick marks are on whole pixels, so one frame's scale is uncertain by ~0.2 %
(average over a movie).  The SOT/FG movie axes of 2006 are 1.4 % too small
(true arcsec = QL arcsec x 1.014, from Mercury's size and motion); the XRT
axes agree with the XRT plate scale (1.0286"/px) to 0.1 %.  The origin of the
axes follows the pointing: a few arcsec off in general, ~25" during eclipses
(the Sun sensor sees a partial Sun) and 1,886" in X in the XRT movies of
2019-07-02 21:43-22:13 (TR0-0020/0021), so measure the Sun's centre from its
limb.  The XRT QL images of 2025-2026 are turned by 0.7-1.6 deg from the solar
P angle (0.4 deg or less in 2017-2024).

Usage:  python tools/hinode_ql.py --selftest
        python tools/hinode_ql.py 2006-11-08            (list the products)
        python tools/hinode_ql.py URL-or-FILE           (times / axes of the frames)
"""
import datetime as dt
import html
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from eclipsecalc.context import CACHE_DIR  # noqa: E402
from eclipsecalc.net import download_file  # noqa: E402

QL_BASE = 'https://hinode.nao.ac.jp/en/for-researchers/qlmovies/movie_sirius'
QL_DIR = CACHE_DIR / 'hinode_ql'
PAUSE_S = 0.3                     # pause after every download (be polite)


# --- archive ------------------------------------------------------------------
def _date(d):
    if isinstance(d, str):
        d = dt.date.fromisoformat(d[:10])
    return d.date() if isinstance(d, dt.datetime) else d


def day_url(date):
    d = _date(date)
    return f'{QL_BASE}/{d:%Y/%m/%d}/hsc_ql{d:%Y%m%d}.html'


def _get(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    download_file(url, path, log=lambda *a: None)
    time.sleep(PAUSE_S)
    return path


def day_page(date):
    """HTML of a day page (cached in data/cache/hinode_ql/pages/); '' if the day has no page."""
    d = _date(date)
    path = QL_DIR / 'pages' / f'{d:%Y%m%d}.html'
    if not path.exists():
        try:
            _get(day_url(d), path)
        except Exception as exc:          # 404: no observations that day
            if '404' not in str(exc):
                raise
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'')
    return path.read_text(encoding='utf-8', errors='replace')


_FILE = re.compile(r'([A-Z]+)_([A-Z]+)(\d{8})(\d{6})(?:_(\d{6}))?\.(\w+)$')
_SPQL = re.compile(r'SPQL(\d{8})_(\d{6})_(\d+)\.png$')
_RANGE = re.compile(r'(\d\d:\d\d:\d\d)\s*-{1,2}\s*(\d\d:\d\d:\d\d)')
_PROG = re.compile(r'(?:ID:\s*)?([A-Z]{2}\d-[\dA-Z]+|\d{6}R?)\s*/\s*(?:IMG:|Slit_pos:)?\s*(\d+)')


def day_products(date):
    """Products listed on the QL page of ``date`` (date / datetime / 'YYYY-MM-DD').

    Each item: file, url, section (h3 title), inst ('XRT', 'FG', 'SP'), kind
    ('FFI', 'APM', 'GBM', ... 'SPQL'), ext, t0, t1 (naive UTC datetimes from the
    file name; t1 = t0 for images; t1 past midnight is moved to the next day),
    and when the page gives them: program (e.g. 'TR0-0100'), frames (number of
    images; for SP the slit positions), range (the page's 'hh:mm:ss-hh:mm:ss')."""
    d = _date(date)
    page = day_page(d)
    base = day_url(d).rsplit('/', 1)[0]
    out = []
    for sec in re.split(r'<a name=', page)[1:]:
        m = re.search(r'<h3>([^<]*)</h3>', sec)
        title = html.unescape(m.group(1)).strip() if m else ''
        for table in re.findall(r'<table.*?</table>', sec, re.S):
            heads = []
            for row in re.findall(r'<tr>(.*?)</tr>', table, re.S):
                ths = re.findall(r'<th>(.*?)</th>', row, re.S)
                if ths:
                    heads.append([html.unescape(re.sub('<[^>]+>', '', t)).strip() for t in ths])
                    continue
                for col, td in enumerate(re.findall(r'<td>(.*?)</td>', row, re.S)):
                    link = re.search(r'href="\./([^"]+)"', td)
                    item = link and _parse_file(link.group(1), base, title)
                    if not item:
                        continue
                    for h in heads:
                        cell = h[col] if col < len(h) else ''
                        if _RANGE.search(cell):
                            item['range'] = '-'.join(_RANGE.search(cell).groups())
                        elif _PROG.search(cell):
                            item['program'], n = _PROG.search(cell).groups()
                            item['frames'] = int(n)
                    out.append(item)
                heads = []
    return out


def _parse_file(f, base, title):
    m = _FILE.match(f)
    if m:
        inst, kind, ymd, a, b, ext = m.groups()
        t0 = dt.datetime.strptime(ymd + a, '%Y%m%d%H%M%S')
        t1 = dt.datetime.strptime(ymd + b, '%Y%m%d%H%M%S') if b else t0
        if t1 < t0:
            t1 += dt.timedelta(days=1)
    else:
        m = _SPQL.match(f)
        if not m:
            return None
        inst, kind, ext = 'SP', 'SPQL', 'png'
        t0 = t1 = dt.datetime.strptime(m.group(1) + m.group(2), '%Y%m%d%H%M%S')
    return dict(file=f, url=f'{base}/{f}', section=title, inst=inst, kind=kind, ext=ext, t0=t0, t1=t1)


def fetch(url):
    """Local copy of a QL file (data/cache/hinode_ql/YYYYMMDD/FILE); downloaded once."""
    m = re.search(r'/(\d{4})/(\d\d)/(\d\d)/([^/]+)$', url)
    if not m or not url.startswith(QL_BASE + '/'):
        raise ValueError(f'not a Hinode QL file URL: {url}')
    path = QL_DIR / ''.join(m.groups()[:3]) / m.group(4)
    return path if path.exists() else _get(url, path)


# --- decoding -------------------------------------------------------------------
def find_ffmpeg():
    """Path of ffmpeg: $FFMPEG, the PATH, or C:\\ffmpeg\\bin."""
    for cand in (os.environ.get('FFMPEG'), shutil.which('ffmpeg'), r'C:\ffmpeg\bin\ffmpeg.exe', '/c/ffmpeg/bin/ffmpeg'):
        if cand and os.path.isfile(cand):
            return cand
    raise FileNotFoundError('ffmpeg not found: set FFMPEG or add it to PATH')


def iter_frames(path):
    """Decoded frames of a movie (every coded frame, in order) as HxWx3 uint8 arrays."""
    cmd = [find_ffmpeg(), '-v', 'error', '-i', str(path), '-vsync', '0',
           '-f', 'image2pipe', '-vcodec', 'ppm', '-']
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    f = proc.stdout
    try:
        while True:
            head = b''
            while len(head.split()) < 4 or not head[-1:].isspace():
                c = f.read(1)
                if not c:
                    return
                head += c
            w, h = (int(v) for v in head.split()[1:3])
            buf = f.read(w * h * 3)
            if len(buf) < w * h * 3:
                return
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        f.close()
        proc.kill()
        proc.wait()


def read_image(path):
    """A PNG (grey / RGB / palette, 8 bit, not interlaced) as an HxWx3 uint8 array."""
    b = Path(path).read_bytes()
    pos, idat, plte = 8, b'', None
    while pos < len(b):
        n = struct.unpack('>I', b[pos:pos + 4])[0]
        t, d = b[pos + 4:pos + 8], b[pos + 8:pos + 8 + n]
        pos += 12 + n
        if t == b'IHDR':
            w, h, depth, ct, _, _, lace = struct.unpack('>IIBBBBB', d[:13])
            if depth != 8 or lace:
                raise ValueError('only 8-bit, non-interlaced PNG is supported')
        elif t == b'IDAT':
            idat += d
        elif t == b'PLTE':
            plte = np.frombuffer(d, np.uint8).reshape(-1, 3)
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    stride = w * ch
    raw = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(h, stride + 1)
    out = np.zeros((h, stride), np.uint8)
    prev = np.zeros(stride, np.int64)
    for y in range(h):
        f, cur = raw[y, 0], raw[y, 1:].astype(np.int64)
        if f == 1:
            cur = np.cumsum(cur.reshape(-1, ch), axis=0).ravel()
        elif f == 2:
            cur = cur + prev
        elif f in (3, 4):
            for x in range(stride):
                a = cur[x - ch] if x >= ch else 0
                c = prev[x - ch] if x >= ch else 0
                if f == 3:
                    cur[x] = (cur[x] + ((a + prev[x]) >> 1)) & 255
                else:
                    p = a + prev[x] - c
                    pa, pb, pc = abs(p - a), abs(p - prev[x]), abs(p - c)
                    cur[x] = (cur[x] + (a if pa <= pb and pa <= pc else prev[x] if pb <= pc else c)) & 255
        cur &= 255
        out[y] = prev = cur
    img = out.reshape(h, w, ch)
    if ct == 3:
        return plte[img[:, :, 0]]
    if ch in (1, 2):
        return np.repeat(img[:, :, :1], 3, axis=2)
    return img[:, :, :3].copy()


def write_png(path, img, zoom=1):
    """Save an HxW or HxWx3 uint8 array as PNG (crops / diagnostics), enlarged ``zoom`` times."""
    a = np.asarray(img, np.uint8)
    if a.ndim == 2:
        a = np.repeat(a[:, :, None], 3, axis=2)
    if zoom > 1:
        a = a.repeat(zoom, axis=0).repeat(zoom, axis=1)
    h, w = a.shape[:2]
    raw = b''.join(b'\0' + a[y].tobytes() for y in range(h))

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
                           + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


# --- plot layout ------------------------------------------------------------------
INK = 110                        # grey level separating the white plot text / lines from the black page


def grey(img):
    """Mean of the colour channels as float (white text and axes are ~255 in all of them)."""
    a = np.asarray(img)
    return a.astype(float).mean(axis=2) if a.ndim == 3 else a.astype(float)


def _longest_runs(mask):
    """Longest run of True per row: (length, start)."""
    out = np.zeros((mask.shape[0], 2), int)
    for i in np.nonzero(mask.sum(axis=1) >= 50)[0]:
        d = np.diff(np.r_[0, mask[i].astype(np.int8), 0])
        s, e = np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]
        k = int(np.argmax(e - s))
        out[i] = e[k] - s[k], s[k]
    return out


def plot_box(img):
    """Pixel (column / row) of the left, right, top and bottom axis lines of the plot."""
    m = grey(img) > INK
    rows, cols = _longest_runs(m), _longest_runs(m.T)
    lr, lc = rows[:, 0].max(), cols[:, 0].max()
    if lr < 100 or lc < 100:
        raise ValueError('no plot box found')
    r = np.nonzero(rows[:, 0] >= lr - 3)[0]
    c = np.nonzero(cols[:, 0] >= lc - 3)[0]
    return int(c.min()), int(c.max()), int(r.min()), int(r.max())


def _ink_runs(v):
    """[start, end) of the runs of True in a 1-D boolean array."""
    d = np.diff(np.r_[0, np.asarray(v, np.int8), 0])
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]))


def title_band(img, box=None):
    """Rows [r0, r1) of the title line (the text line just above the plot box), or None."""
    box = box or plot_box(img)
    m = grey(img) > INK
    top = box[2]
    rows = m[max(0, top - 45):top - 1, box[0] + 1:box[1]].any(axis=1)
    runs = _ink_runs(rows)
    if not runs:
        return None
    a, b = runs[-1]
    return max(0, top - 45) + a, max(0, top - 45) + b


# --- glyph recognition -----------------------------------------------------------
# A glyph is the ink of one column run of a text line, cut to rows baseline-13 ..
# baseline+5 (the Hershey font of the plots is 12 px high with 4 px descenders).
GH, GUP, CW = 19, 13, 28
# Glyphs are compared as Gaussian-blurred (sigma 0.8 px) bitmaps by the cosine
# distance 1 - cos(angle), the best over shifts of +-2 columns and +-1 row; the
# blur makes the 1 px strokes tolerant to the sub-pixel placement of the text.
D_OK, MARGIN = 0.10, 0.015       # accept a glyph when its distance is below D_OK and the
                                 # next-best allowed character is at least MARGIN farther
_K = np.exp(-np.arange(-2, 3) ** 2 / (2 * 0.8 ** 2))
_K /= _K.sum()


def _blurvec(c):
    """Blurred, unit-length vector of a GH x CW canvas."""
    c = np.asarray(c, np.float32)
    p = np.pad(c, 2)
    c = sum(k * p[i:i + c.shape[0], 2:-2] for i, k in enumerate(_K))
    p = np.pad(c, ((0, 0), (2, 2)))
    c = sum(k * p[:, i:i + c.shape[1]] for i, k in enumerate(_K))
    v = c.ravel()
    return v / max(float(np.sqrt(v @ v)), 1e-9)


def _canvas(bm, dx=0, dy=0):
    c = np.zeros((GH, CW), bool)
    o = (CW - bm.shape[1]) // 2 + dx
    c[:, o:o + bm.shape[1]] = bm
    return np.roll(c, dy, axis=0)


def line_glyphs(m, r0, r1, c0=0, c1=None, baseline=None):
    """Glyphs of the text line in rows r0:r1 (columns c0:c1) of the ink mask m:
    list of (col0, col1, bitmap GH x width), and the baseline row."""
    runs = [(c0 + a, c0 + b) for a, b in _ink_runs(m[r0:r1, c0:c1].any(axis=0))]
    if not runs:
        return [], baseline
    if baseline is None:
        bottoms = [r0 + np.nonzero(m[r0:r1, a:b].any(axis=1))[0].max() for a, b in runs]
        vals, counts = np.unique(bottoms, return_counts=True)
        baseline = int(vals[np.argmax(counts)])
    top = baseline - GUP
    pad = np.zeros((GH, m.shape[1]), bool)
    lo, hi = max(top, 0), min(top + GH, m.shape[0])
    pad[lo - top:hi - top] = m[lo:hi]
    return [(a, b, pad[:, a:b]) for a, b in runs], baseline


class Font:
    """Glyph templates: one line per template, 'char width hexbits' (GH rows x width)."""

    def __init__(self, text):
        self.chars, self.maps = [], []
        for line in text.strip().splitlines():
            c, w, hx = line.split()
            w = int(w)
            bits = np.unpackbits(np.frombuffer(bytes.fromhex(hx), np.uint8))[:GH * w]
            self.chars.append(c)
            self.maps.append(bits.reshape(GH, w).astype(bool))
        P, owner = [], []
        for k, t in enumerate(self.maps):
            for dy in (-1, 0, 1):
                for dx in (-2, -1, 0, 1, 2):
                    if 0 <= (CW - t.shape[1]) // 2 + dx and (CW - t.shape[1]) // 2 + dx + t.shape[1] <= CW:
                        P.append(_blurvec(_canvas(t, dx, dy)))
                        owner.append(k)
        self.P = np.array(P, np.float32).reshape(-1, GH * CW)
        self.owner = np.array(owner, int)
        self.uchars = sorted(set(self.chars))
        self.cidx = np.array([self.uchars.index(c) for c in self.chars], int)

    def distances(self, bm):
        """Smallest distance of the glyph bitmap to each character: dict char -> d."""
        if bm.shape[1] > CW or not bm.any() or not len(self.P):
            return {}
        d = 1 - self.P @ _blurvec(_canvas(bm))
        best = np.full(len(self.uchars), 2.0)
        np.minimum.at(best, self.cidx[self.owner], d)
        return dict(zip(self.uchars, best.tolist()))

    def classify(self, bm):
        """(char, distance, margin) of the best single character."""
        d = self.distances(bm)
        if not d:
            return '?', 1.0, 0.0
        order = sorted(d, key=d.get)
        d2 = d[order[1]] if len(order) > 1 else 1.0
        return order[0], d[order[0]], d2 - d[order[0]]

    def split2(self, bm, top=6):
        """Best reading of a glyph run as two touching / overlapping characters:
        (chars, distance) or None."""
        w = bm.shape[1]
        if w > CW:
            return None
        g = _blurvec(_canvas(bm))
        cand = {'l': [], 'r': []}
        for k, t in enumerate(self.maps):
            wt = t.shape[1]
            if 2 <= wt < w:
                for side in 'lr':
                    part = bm[:, :wt] if side == 'l' else bm[:, w - wt:]
                    if part.any():
                        cand[side].append((1 - _blurvec(_canvas(part)) @ _blurvec(_canvas(t)), k))
        best = None
        for _, kl in sorted(cand['l'])[:top]:
            for _, kr in sorted(cand['r'])[:top]:
                tl, tr = self.maps[kl], self.maps[kr]
                if tl.shape[1] + tr.shape[1] < w:
                    continue
                c = np.zeros_like(bm)
                c[:, :tl.shape[1]] |= tl
                c[:, w - tr.shape[1]:] |= tr
                d = float(1 - _blurvec(_canvas(c)) @ g)
                if best is None or d < best[1]:
                    best = (self.chars[kl] + self.chars[kr], d)
        return best

    def read(self, glyphs, space=8):
        """Text of a line of glyphs and, per character of the text, (distance, margin,
        col0, col1) of its match (spaces: None).  A space is put where the gap between
        glyphs is wider than ``space`` px; '?' marks a glyph that matches nothing."""
        out, info, prev = '', [], None
        for a, b, bm in glyphs:
            if prev is not None and a - prev > space:
                out += ' '
                info.append(None)
            prev = b
            c, d, mg = self.classify(bm)
            if (d > D_OK or mg < MARGIN) and bm.shape[1] >= 7:
                sp = self.split2(bm)
                if sp and sp[1] < D_OK and sp[1] < d:
                    out += sp[0]
                    info += [(sp[1], MARGIN, a, b)] * len(sp[0])
                    continue
            out += c if d <= D_OK else '?'
            info.append((d, mg, a, b))
        return out, info


_FONT = _LFONT = None


def font():
    """Templates of the title glyphs."""
    global _FONT
    if _FONT is None:
        _FONT = Font(GLYPHS)
    return _FONT


def label_font():
    """Templates of the tick-label glyphs (the axis labels use another Hershey font)."""
    global _LFONT
    if _LFONT is None:
        _LFONT = Font(LABEL_GLYPHS)
    return _LFONT


def title_glyphs(img, box=None):
    """Glyphs (col0, col1, bitmap) of the title line of a QL frame."""
    box = box or plot_box(img)
    band = title_band(img, box)
    return line_glyphs(grey(img) > INK, *band)[0] if band else []


def read_title(img, box=None):
    """Title text of a QL frame (best effort, '?' for unknown glyphs) and the
    per-character match info (see Font.read)."""
    return font().read(title_glyphs(img, box), space=8)


MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
DIGITS = '0123456789'
# the end of every title, read from the right: 'D-Mon-YYYY HH:MM:SS.mmm UT'
_TAIL = 'TUddd.dd:dd:dddddd-mmm-d'


def _pick(dist, allowed):
    """Best of the allowed characters: (char, distance, margin to the next allowed one)."""
    ds = sorted((dist.get(c, 2.0), c) for c in allowed)
    return ds[0][1], ds[0][0], (ds[1][0] if len(ds) > 1 else 2.0) - ds[0][0]


def _join(c1, c2):
    """One glyph from two neighbouring ones (a stroke broken by the MPEG noise)."""
    a, b = c1[0], c2[1]
    bm = np.zeros((GH, b - a), bool)
    bm[:, :c1[1] - a] |= c1[2]
    bm[:, c2[0] - a:] |= c2[2]
    return a, b, bm


def _slot_match(f, bm, slot):
    dist = f.distances(bm)
    if slot == 'd':
        return _pick(dist, DIGITS), dist
    c, d, _ = _pick(dist, slot)
    others = min((v for ch, v in dist.items() if ch != slot), default=2.0)
    return (c, d, others - d), dist


def _split(f, cell, left_allowed, right_allowed):
    """Read a glyph run as two touching characters (left one from left_allowed,
    right one from right_allowed): ((char, distance, margin) of the right one,
    right cell, left cell) or None."""
    a, b, bm = cell
    w = bm.shape[1]
    g = _blurvec(_canvas(bm))
    best = {}
    for kl, tl in enumerate(f.maps):
        if f.chars[kl] not in left_allowed or tl.shape[1] >= w:
            continue
        for kr, tr in enumerate(f.maps):
            if f.chars[kr] not in right_allowed or not w - 4 <= tl.shape[1] + tr.shape[1] <= w + 4:
                continue
            c = np.zeros_like(bm)
            c[:, :tl.shape[1]] |= tl
            c[:, w - tr.shape[1]:] |= tr
            d = float(1 - _blurvec(_canvas(c)) @ g)
            key = f.chars[kl] + f.chars[kr]
            if d < best.get(key, (2.0,))[0]:
                best[key] = (d, tl.shape[1], tr.shape[1])
    if not best:
        return None
    key = min(best, key=lambda k: best[k][0])
    d1, wl, wr = best[key]
    d2 = min((v[0] for k, v in best.items() if k[1] != key[1]), default=2.0)
    return (key[1], d1, d2 - d1), (b - wr, b, bm[:, w - wr:]), (a, a + wl, bm[:, :wl])


def read_time(glyphs):
    """Parse 'D-Mon-YYYY HH:MM:SS.mmm UT' from the right end of a title.

    Each glyph is compared only with the characters allowed at its place
    (digits, ':', '.', '-', the month names, 'U', 'T').  A glyph broken into
    pieces by the MPEG noise is joined, specks of noise are skipped and two
    touching digits are split.  Returns (datetime or None, list of (char,
    distance, margin) from the left); the datetime is None when any place is
    ambiguous (distance > D_OK or margin < MARGIN) or the date is invalid."""
    f = font()
    cells = list(glyphs)
    got, k = [], len(cells) - 1                     # (match, cell), read from the right
    good = lambda r: r[1] <= D_OK and r[2] >= MARGIN       # noqa: E731
    for i, slot in enumerate(_TAIL):
        while True:
            if k < 0:
                return None, [g[0] for g in got[::-1]]
            cell = cells[k]
            res, dist = _slot_match(f, cell[2], slot)
            if slot == 'm' or good(res):
                k -= 1
                break
            joined, n = cell, 0
            while k - n > 0 and joined[0] - cells[k - n - 1][1] <= 2 and n < 3 and not good(res):
                n += 1
                joined = _join(cells[k - n], joined)
                res2, dist2 = _slot_match(f, joined[2], slot)
                if good(res2):
                    cell, res, dist = joined, res2, dist2
            if good(res):
                cells[k - n:k + 1] = [cell]
                k -= n + 1
                break
            if cell[2].sum() <= 4 and k > 0:
                k -= 1                              # a speck of noise
                continue
            nxt = _TAIL[i + 1] if i + 1 < len(_TAIL) else 'd'
            if slot != 'm' and nxt != 'm' and cell[2].shape[1] >= 11:
                sp = _split(f, cell, DIGITS if nxt == 'd' else nxt, DIGITS if slot == 'd' else slot)
                if sp and good(sp[0]):
                    res, cell = sp[0], sp[1]
                    cells[k] = sp[2]                # the left glyph is read at the next place
                    break
            k -= 1
            break
        got.append((res if slot != 'm' else (None, dist, None), cell))
    # the month: its three glyphs are read together
    m3 = [g[0][1] for g in got if g[0][0] is None][::-1]
    scores = sorted((sum(dd.get(ch, 2.0) for dd, ch in zip(m3, mon)) / 3, mon) for mon in MONTHS)
    mon, md, mm = scores[0][1], scores[0][0], scores[1][0] - scores[0][0]
    letters = iter(mon[::-1])
    info = [(r if r[0] is not None else (next(letters), md, mm)) for r, _ in got]
    # a two-digit day: a digit one digit pitch to the left of the last one read
    year = [c for _, c in got[14:18]]
    pitch = np.mean(np.diff([0.5 * (c[0] + c[1]) for c in year[::-1]]))
    last = got[-1][1]
    day2 = None
    if k >= 0 and abs(0.5 * (last[0] + last[1] - cells[k][0] - cells[k][1]) - pitch) < 2.5:
        day2 = _pick(f.distances(cells[k][2]), DIGITS)
        if day2[1] <= D_OK:
            info.append(day2)
        else:
            day2 = None
    info = info[::-1]
    if any(d > D_OK or mg < MARGIN for _, d, mg in info):
        return None, info
    s = ''.join(c for c, _, _ in info)
    day, rest = (s[:2], s[2:]) if day2 else (s[:1], s[1:])
    y, hh, mi, ss, ms = rest[5:9], rest[9:11], rest[12:14], rest[15:17], rest[18:21]
    try:
        return dt.datetime(int(y), MONTHS.index(rest[1:4]) + 1, int(day), int(hh), int(mi), int(ss),
                           int(ms) * 1000), info
    except ValueError:
        return None, info


def frame_time(img, box=None):
    """UT of a QL movie frame / FFI image read from its title ('8-Nov-2006
    19:10:32.190 UT') as a naive datetime with milliseconds; None when the title
    cannot be read with confidence."""
    try:
        return read_time(title_glyphs(img, box))[0]
    except ValueError:
        return None


# --- plot axes ---------------------------------------------------------------------
NUMBER = '-0123456789.'


def read_numbers(glyphs):
    """Tick labels in a run of glyphs: list of (value, col0, col1); several when
    crowded labels touch ('-1400-1200-1000'); [] if any glyph is ambiguous."""
    f, text = label_font(), ''
    glyphs = [g for g in glyphs if g[2].sum() > 2]           # drop specks of noise
    for _, _, bm in glyphs:
        c, d, mg = _pick(f.distances(bm), NUMBER)
        if d > D_OK or mg < MARGIN:
            return []
        text += c
    out = []
    for m in re.finditer(r'-?\d+(?:\.\d+)?', text):
        if m.end() - m.start() == 1 and text[m.start()] == '-':
            continue
        out.append((float(m.group(0)), glyphs[m.start()][0], glyphs[m.end() - 1][1]))
    return out if ''.join(re.findall(r'-?\d+(?:\.\d+)?', text)) == text else []


def _groups(glyphs, gap):
    groups, cur = [], []
    for g in glyphs:
        if cur and g[0] - cur[-1][1] > gap:
            groups.append(cur)
            cur = []
        cur.append(g)
    return groups + ([cur] if cur else [])


def label_glyphs(img, box=None):
    """Glyph groups of the tick labels: {'x': [glyphs, ...] (left to right, below the
    box), 'y': [(glyphs, centre row), ...] (top to bottom, right-aligned left of it)}."""
    box = box or plot_box(img)
    L, R, T, B = box
    m = grey(img) > INK
    out = {'x': [], 'y': []}
    c0, c1 = max(L - 40, 0), min(R + 40, m.shape[1])
    rows = _ink_runs(m[B + 2:B + 40, c0:c1].any(axis=1))
    if rows:
        glyphs, _ = line_glyphs(m, B + 2 + rows[0][0], B + 2 + rows[0][1], c0, c1)
        out['x'] = _groups(glyphs, 8)
    ink = np.nonzero(m[T - 1:B + 2, :L - 1].any(axis=0))[0]
    if ink.size:
        right = int(ink.max()) + 1                   # the labels are right-aligned here
        for r0, r1 in _ink_runs(m[:, right - 8:right].any(axis=1)):
            if not T - 3 <= 0.5 * (r0 + r1) <= B + 3 or not 5 <= r1 - r0 <= 20:
                continue
            glyphs, _ = line_glyphs(m, r0, r1, max(right - 60, 0), right)
            grp = _groups(glyphs, 6)
            if grp and grp[-1][-1][1] >= right - 1:
                out['y'].append((grp[-1], 0.5 * (r0 + r1 - 1)))
    return out


def _tick(prof, centre, half=6):
    """Sub-pixel position of the strongest tick near ``centre`` in a 1-D contrast profile."""
    lo, hi = int(max(centre - half, 2)), int(min(centre + half + 1, prof.size - 2))
    if hi <= lo:
        return None, 0.0
    i = lo + int(np.argmax(prof[lo:hi]))
    y0, y1, y2 = prof[i - 1], prof[i], prof[i + 1]
    den = y0 - 2 * y1 + y2
    return i + (0.5 * (y0 - y2) / den if den < 0 else 0.0), float(y1)


def _contrast(strip):
    """Tick contrast along a strip (rows = positions along the axis)."""
    v = strip.mean(axis=1)
    pad = np.pad(v, 3, mode='edge')
    bg = np.median(np.stack([pad[i:i + v.size] for i in (0, 1, 5, 6)]), axis=0)
    return v - bg


class Axes:
    """Pixel <-> helioprojective arcsec mapping of a QL plot (from its tick marks and labels).

    X = ax + bx * col,  Y = ay + by * row  (col, row: 0-based pixel indices of the
    frame array; by < 0 because rows increase downwards).  Attributes: box
    (left, right, top, bottom axis-line pixels), scale_x / scale_y (arcsec per
    pixel), ticks_x / ticks_y (lists of (value, pixel, from_tick_mark)), rms_x /
    rms_y (fit residuals in px), square (True if one axis had a single label and
    took the scale of the other)."""

    def __init__(self, box, ticks_x, ticks_y):
        self.box, self.ticks_x, self.ticks_y = box, ticks_x, ticks_y
        self.square = min(len(ticks_x), len(ticks_y)) < 2
        if len(ticks_x) >= 2:
            (self.bx, self.ax), self.rms_x = self._fit(ticks_x)
        if len(ticks_y) >= 2:
            (self.by, self.ay), self.rms_y = self._fit(ticks_y)
        if len(ticks_x) < 2:
            self.bx, self.rms_x = -self.by, 0.0
            self.ax = ticks_x[0][0] - self.bx * ticks_x[0][1]
        if len(ticks_y) < 2:
            self.by, self.rms_y = -self.bx, 0.0
            self.ay = ticks_y[0][0] - self.by * ticks_y[0][1]
        self.scale_x, self.scale_y = abs(self.bx), abs(self.by)

    @staticmethod
    def _fit(ticks):
        """Straight-line fit value(pixel); ticks more than 1 px off are dropped (from 4 on)."""
        v = np.array([t[0] for t in ticks], float)
        p = np.array([t[1] for t in ticks], float)
        keep = np.ones(v.size, bool)
        while True:
            coef = np.polyfit(p[keep], v[keep], 1)
            res = np.abs((v - np.polyval(coef, p)) / coef[0])
            worst = int(np.argmax(np.where(keep, res, -1)))
            if keep.sum() < 4 or res[worst] < 1.0:
                break
            keep[worst] = False
        return coef, float(np.sqrt(np.mean(res[keep] ** 2)))

    def to_arcsec(self, col, row):
        return self.ax + self.bx * np.asarray(col, float), self.ay + self.by * np.asarray(row, float)

    def to_pixel(self, X, Y):
        return (np.asarray(X, float) - self.ax) / self.bx, (np.asarray(Y, float) - self.ay) / self.by

    def __repr__(self):
        sq = ' (square)' if self.square else ''
        return (f'Axes(box={self.box}, {self.scale_x:.5f}"/px x {self.scale_y:.5f}"/px{sq}, '
                f'X(col)={self.ax:.3f}{self.bx:+.6f}*col, Y(row)={self.ay:.3f}{self.by:+.6f}*row, '
                f'ticks {len(self.ticks_x)}+{len(self.ticks_y)}, rms {self.rms_x:.2f}/{self.rms_y:.2f} px)')


def plot_axes(img, box=None):
    """Axes of a QL frame from its tick labels (read with the label templates), each
    placed at its major tick mark (found inside the box on both opposite axes).
    With only one readable label on an axis the scale of the other axis is used
    (square pixels; Axes.square = True).  None when that is not possible."""
    box = box or plot_box(img)
    L, R, T, B = box
    a = np.asarray(img)
    g = a.min(axis=2).astype(float) if a.ndim == 3 else a.astype(float)
    # IDL major ticks are 2 % of the plot size across the axis, minor ones half of that;
    # the contrast is taken only where the major ticks reach
    tx, ty = max(4, int(round(0.02 * (B - T)))), max(4, int(round(0.02 * (R - L))))
    nx, ny = (tx + 1) // 2 + 1, (ty + 1) // 2 + 1
    labels = label_glyphs(img, box)
    prof_x = _contrast(g[B - tx:B - nx + 1, :].T) + _contrast(g[T + nx:T + tx + 1, :].T)
    prof_y = _contrast(g[:, L + ny:L + ty + 1]) + _contrast(g[:, R - ty:R - ny + 1])
    ticks = {}
    for axis, prof, lo, hi in (('x', prof_x, L, R), ('y', prof_y, T, B)):
        edge = prof[[lo, hi]].copy()
        prof[max(lo - 1, 0):lo + 2] = prof[hi - 1:hi + 2] = 0          # the other axis lines
        ticks[axis] = []
        for item in labels[axis]:
            if axis == 'x':
                found = [(v, 0.5 * (c0 + c1 - 1)) for v, c0, c1 in read_numbers(item)]
            else:
                found = [(v, item[1]) for v, _, _ in read_numbers(item[0])][:1]
            for v, centre in found:
                p, k = _tick(prof, centre)
                if p is not None and k > 30:
                    ticks[axis].append((v, p, True))
                elif min(abs(centre - lo), abs(centre - hi)) <= 6 and max(edge) > 30:
                    ticks[axis].append((v, float(lo if abs(centre - lo) < abs(centre - hi) else hi), True))
                else:
                    ticks[axis].append((v, centre, False))
    nx, ny = len(ticks['x']), len(ticks['y'])
    if min(nx, ny) < 1 or max(nx, ny) < 2:
        return None
    return Axes(box, ticks['x'], ticks['y'])


# --- whole movies ---------------------------------------------------------------------
def _axes_dict(ax):
    if ax is None:
        return None
    ticks = lambda tt: [[float(v), float(p), bool(m)] for v, p, m in tt]      # noqa: E731
    return dict(box=list(ax.box), ticks_x=ticks(ax.ticks_x), ticks_y=ticks(ax.ticks_y))


def _axes_from(d):
    return None if d is None else Axes(tuple(d['box']), [tuple(t) for t in d['ticks_x']], [tuple(t) for t in d['ticks_y']])


def local_path(url_or_path):
    """Local file of a QL URL (downloaded once) or an existing local path."""
    s = str(url_or_path)
    return fetch(s) if s.startswith('http') else Path(s)


def movie_frames(url_or_path, images=True, refresh=False):
    """Every frame of a QL movie (or the one image of an FFI / PNG) as a list of
    (time, img, axes): time = frame_time() (None if unreadable), img = HxWx3
    uint8 array (None with images=False), axes = plot_axes() (None if unreadable).
    The times and axes are cached in FILE.frames.json next to the movie."""
    path = local_path(url_or_path)
    meta = path.with_name(path.name + '.frames.json')
    cached = None
    if meta.exists() and not refresh:
        try:
            cached = json.loads(meta.read_text())
        except ValueError:
            cached = None
    if cached is not None and not images:
        return [(dt.datetime.fromisoformat(c['time']) if c['time'] else None, None, _axes_from(c['axes']))
                for c in cached]
    frames = [read_image(path)] if path.suffix.lower() == '.png' else iter_frames(path)
    out, rows = [], []
    for k, img in enumerate(frames):
        if cached is not None and k < len(cached):
            c = cached[k]
            t, ax = (dt.datetime.fromisoformat(c['time']) if c['time'] else None), _axes_from(c['axes'])
        else:
            box = plot_box(img)
            t, ax = frame_time(img, box), plot_axes(img, box)
        rows.append(dict(time=t.isoformat(timespec='milliseconds') if t else None, axes=_axes_dict(ax)))
        out.append((t, img if images else None, ax))
    if cached is None or len(cached) != len(rows):
        meta.write_text(json.dumps(rows))
    return out


def mean_scale(frames):
    """Movie-mean arcsec/px in X and Y and their standard errors, from the per-frame
    axes of movie_frames() (one frame's tick marks are on whole pixels: ~0.2 %)."""
    sx = np.array([ax.scale_x for _, _, ax in frames if ax is not None])
    sy = np.array([ax.scale_y for _, _, ax in frames if ax is not None])
    n = max(len(sx), 1)
    return float(sx.mean()), float(sy.mean()), float(sx.std() / np.sqrt(n)), float(sy.std() / np.sqrt(n))


def check_movie(product, frames=None):
    """Consistency of a product's frames: dict(n, parsed, increasing, first_ok,
    last_ok, axes_ok) -- first / last frame time (to the second) must equal the
    start / end time in the file name, the times must strictly increase."""
    frames = frames if frames is not None else movie_frames(product['url'], images=False)
    times = [t for t, _, _ in frames]
    ok = [t for t in times if t]
    return dict(n=len(frames), parsed=len(ok), increasing=all(b > a for a, b in zip(ok, ok[1:])),
                first_ok=bool(times[0]) and times[0].replace(microsecond=0) == product['t0'],
                last_ok=bool(times[-1]) and times[-1].replace(microsecond=0) == product['t1'],
                axes_ok=sum(ax is not None for _, _, ax in frames))


# Templates cut from QL titles of 2006-2026 read by eye (tools: Font, line_glyphs).
GLYPHS = """
- 10 00000000000000000000ffc0000000000000000000000000
. 2 000000d000
. 2 000000b000
. 2 000000e000
. 2 0000007000
. 3 000000000e800000
/ 10 004020080401008020100402008040100802010040200000
/ 9 00808040402020201010080804040402000000000000
/ 11 002008010040080200801004008020040100400802004010000000
0 9 00000f8844140a030180c050482220e0000000000000
0 8 000018668181818181818181427c0000000000
0 8 00001c224141818181814141213e0000000000
0 8 00007c84828281818181824244380000000000
0 8 00003e42818181818181814126180000000000
0 9 0000030644120a030180c050482211f0000000000000
1 4 00179111111111000000
1 3 009e492492400000
2 8 00007e42810102040408102040ff0000000000
2 9 0000070c6412080404020202060407fc000000000000
2 9 0000070c4412080808040404040407fc000000000000
2 8 00007c42820202040810202040ff0000000000
2 9 00000f884410080808080808040407fc000000000000
3 8 00007f0204040e0101010181e21c0000000000
3 8 00007e0408081c0201010182847c0000000000
3 8 00007f0204081c0301010181827e0000000000
3 9 00001fc04040203c02008060502620e0000000000000
4 9 0000010180c0a09088447fc100804020000000000000
5 9 00000fc40402e18802008040302620e0000000000000
5 8 00007e404080fe8101010181827e0000000000
5 8 00007e404078460101018181621c0000000000
5 9 00001fc8040201f882008040302423f0000000000000
5 8 00007e4080b8c60101018181c23c0000000000
5 9 00001f880403c11802008040302620e0000000000000
6 8 00003e424080bcc28281414224180000000000
6 8 00001c62828098e482818182427c0000000000
6 7 0001f418103f4283060c14c60000000000
6 8 00001c22424098a4c2814142223c0000000000
7 8 0000ff01020204040808101020200000000000
7 9 00003fe0201010080804040202010100000000000000
8 8 00007e4141423c2641818181631c0000000000
8 8 00003c4281816e7c83818181817e0000000000
8 8 00007e8282423c4482818182c23c0000000000
8 9 00001fc8241208f84c414060301610f0000000000000
9 8 00003e4141818141330d0141621c0000000000
9 8 00007c82818183c33d02028464180000000000
9 8 000018244281814325190242447c0000000000
9 7 0000c2683060c166340c184f8000000000
9 8 00003c42418183432519024264180000000000
9 7 0001f4183060c17e040c24470000000000
: 2 002c00e000
: 2 000d007000
: 2 000e00b000
: 2 001c00d000
: 3 0000b8000e800000
A 9 000002010140a05044223f9048280c04000000000000
A 10 00000040100a02812044210fe40902806010000000000000
A 10 00000080201405012088211fc40902806010000000000000
B 8 0000fe81818183fc8381818183fc0000000000
C 9 00000f88280c020100804020282220e0000000000000
D 8 0000fc82818181818181818284f80000000000
F 8 0000ff80808080f88080808080800000000000
G 9 0000070444140601008043e0282211f0000000000000
G 9 000007cc140c0201008041d028131070000000000000
G 8 00003e418180808080878141221c0000000000
G 9 00000382260a0601008041d0281208f8000000000000
G 9 00000f88240c0201008043d0282220e0000000000000
J 6 0000410410410618614cc000000000
J 7 0000081020408103060c24c60000000000
M 9 000020301c1e0f0745a2ca6532988c44000000000000
M 10 0000080601c0f03c1685a264994651886210000000000000
N 9 000020381c0d06431188c260b0381c04000000000000
N 8 000081c1c1a191918989858383810000000000
O 9 00000f8828140a030180c060482220e0000000000000
O 10 000000e0c440902806018060140902208fc0000000000000
O 10 000001f08240a02806018060140882110380000000000000
O 9 0000070444140a030180c060482211f0000000000000
R 8 0000fe81818183fc8484828281810000000000
S 8 00003c428180c0300e010181827e0000000000
S 8 00007e818040601806010181621c0000000000
T 9 00003fe1008040201008040201008040000000000000
U 8 00008181818181818181818146380000000000
U 9 00002030180c06030180c060282260c0000000000000
V 9 000020301412090444220a0502808040000000000000
X 8 00008142422418181818244242810000000000
X 9 000020282411105010080a0888241404000000000000
_ 10 000000000000000000000000000000003ff0000000000000
a 7 000000000751c183060c14674000000000
a 7 00000000000fa183060a1227c000000000
a 8 00000000001927418181814325190000000000
a 7 000000000349a183050a12634000000000
b 7 0002040817314183060c1c570000000000
b 8 000080808080fc828181818284fc0000000000
c 7 0000000007114181020414470000000000
c 7 0000000003092181010212430000000000
d 7 00000810205fc183060c142fc000000000
d 7 000008102751c183060c14674000000000
e 7 000000000309a183fe0212430000000000
e 8 0000000000182641ff80404324180000000000
e 8 0000000000003e4281ff8041213e0000000000
e 8 00000000001c224281ff8041221c0000000000
g 7 000000000359c183060c14674081663000
h 7 0002040813296183060c18304000000000
h 6 00082082ec71861861861000000000
i 2 2c2aaaa000
k 7 0002040810614491434448504000000000
l 1 3ffc00
m 13 00000000000000004c72945861820c106083041820c1040000000000000000
n 7 0000000017316183060c18304000000000
n 6 000000000ff1861861861000000000
n 7 00000000002fe183060c18304000000000
n 6 00000002ec71861861861000000000
o 7 0000000007114183060c14470000000000
o 8 00000000001824428281814224180000000000
o 7 00000000001f4183060c142f8000000000
o 8 00000000001c22418181814126180000000000
o 8 00000000003844828181818244380000000000
p 7 0000000017314183060c1c572040810000
p 8 000000000000fc8281818182c4bc8080808000
p 7 000000001632428306142c962040810000
r 5 0000005f1084210840000000
r 6 000000027a30c20820820000000000
s 6 00000000ec6181e061c4e000000000
s 7 000000000790c140701c14278000000000
t 5 0008427c842108420c000000
u 7 000000001060c183060c14674000000000
u 6 0000000218618618618dd000000000
v 7 000000001060a24450a141020000000000
y 8 0000000000414122221414140808101020c000
y 8 00000000008141422224141408081020408000
"""

# Templates of the tick-label digits (another font than the titles), cut likewise.
LABEL_GLYPHS = """
- 9 000000000000000000007fc000000000000000000000
- 8 000000000000000000ff000000000000000008
0 7 000000018c90c183060a142f8000000000
0 7 0000000791214283060c24860000000000
0 6 00000031286186186145e000000800
0 7 00000003c890c183050a12430000000000
0 7 0000000388a14283060c244f8000000008
0 7 000000000fa14283060c184f8000000000
1 3 0002e92492400000
2 7 0000000388a102041041041fc000000000
2 6 00000003f86104210843f000000000
2 7 000000018c9081040821841fc000000000
2 7 00000007d8a102082081041fc000000000
2 7 00000003889081043041041fc000000000
2 6 00000033384108210843f000000000
3 6 000000fc20841c106187e000000000
3 6 00000003f0821c104187e000000000
3 7 00000007c102081c040c284f8000000000
3 6 000000fc2086041061ccc000000000
4 7 000000008306144893f840810000000000
5 6 000000fe083c8c106187e000000000
5 6 00000003f820f8104187e000000000
5 7 00000007c8207e84040c2c470000000000
5 7 00000007c82058cc0408182f8000000000
6 7 000000018c9040f98a0a144f0000000000
6 7 00000003c8904ce50a0a22c20000000000
6 6 000000313820fa186185e000000000
6 7 00000001849140bd8a0a14478000000000
7 7 0000000fe0810408204102080000000000
7 7 0000000fe0410208104082040000000000
8 7 0000000398a1227d0a0c184f8000000000
8 6 000000fe18777a1861c4e000000000
8 7 000000000fa1427c9a14183f8000000000
8 7 0000000388d0a23c860c142f8000000000
9 6 00000033386185334189e000000000
9 7 000000010d90c1464c70244f8000000000
9 6 0000007a186185f041c8c000000000
"""


# --- command line -------------------------------------------------------------------
_SELFTEST = [   # (file, frame, UT read by eye, an axis label value read by eye: (axis, value))
    ('FG_GBM20061108191032_191701.mpg', 0, '2006-11-08T19:10:32.190', ('x', -870)),
    ('FG_GBM20061108191032_191701.mpg', 29, '2006-11-08T19:13:37.498', ('y', -440)),
    ('FG_GBM20100115191101_231106.mpg', 0, '2010-01-15T19:11:01.970', None),
    ('FG_GBM20150913000034_050039.mpg', 4, '2015-09-13T04:00:39.092', ('x', 450)),
    ('XRT_TIM20080801054501_174433.mpg', 1, '2008-08-01T10:05:32.980', None),
    ('XRT_TIM20110104202226_211103.mpg', 1, '2011-01-04T20:22:59.425', None),
    ('XRT_FFI20170821165359.png', 0, '2017-08-21T16:53:59.204', ('x', -1000)),
    ('XRT_FFI20191226040025.png', 0, '2019-12-26T04:00:25.426', ('y', 1000)),
    ('XRT_AMM20190106042017_152341.mpg', 0, '2019-01-06T04:20:17.356', None),
    ('XRT_APM20211204030338_061812.mpg', 2, '2021-12-04T06:18:12.301', ('x', -1000)),
    ('XRT_APM20260812191806_192659.mpg', 0, '2026-08-12T19:18:06.207', ('x', -2000)),
]


def selftest():
    """Read frames whose title and axes were checked by eye (about 1.5 MB of downloads)."""
    fails = 0
    for f, k, want, lab in _SELFTEST:
        d = re.search(r'(\d{8})', f).group(1)
        prod = {p['file']: p for p in day_products(f'{d[:4]}-{d[4:6]}-{d[6:]}')}[f]
        frames = movie_frames(prod['url'], refresh=True)
        t, img, ax = frames[k]
        chk = check_movie(prod, frames)
        good = (t is not None and t.isoformat(timespec='milliseconds') == want and chk['parsed'] == chk['n']
                and chk['increasing'] and chk['first_ok'] and chk['last_ok'] and chk['axes_ok'] == chk['n'])
        if lab:
            ticks = ax.ticks_x if lab[0] == 'x' else ax.ticks_y
            good = good and any(v == lab[1] for v, _, _ in ticks)
        fails += not good
        print('PASS' if good else 'FAIL', f, k, t, chk, ax)
    print('PASS' if not fails else f'FAIL ({fails})')
    return not fails


if __name__ == '__main__':
    arg = sys.argv[1] if len(sys.argv) > 1 else '--selftest'
    if arg == '--selftest':
        sys.exit(0 if selftest() else 1)
    if re.match(r'\d{4}-\d\d-\d\d$', arg):
        for p in day_products(arg):
            print(f"{p['file']:40s} {p['t0']:%H:%M:%S}-{p['t1']:%H:%M:%S} {p.get('program', ''):9s} "
                  f"{p.get('frames', '')!s:>5s}  {p['section']}")
    else:
        for k, (t, _, ax) in enumerate(movie_frames(arg, images=False)):
            print(k, t, ax)
