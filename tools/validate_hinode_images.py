"""Check eclipsecalc against the actual Hinode XRT / SOT pictures.

The press-release images of the Hinode team carry the UT of each exposure
in their file names.  For every image the centres of the lunar disk and of
the Sun are measured, and the Moon-Sun offset (arcsec, solar north up) is
compared with the computation for Hinode's orbit from NASA SSCWeb:

* XRT (2014-10-23, 2016-03-09, 2017-08-21): best-fitting image rotation
  (should equal the solar P angle) and orbit time-shift (seconds by which
  the actual satellite was ahead of the SSCWeb orbit; 1 s = 7.5 km).
* XRT 2012-11-13 (times only to the minute): distance of the measured Moon
  centres from the computed Moon track, which decides between the computed
  total eclipse and the magnitude 0.993 quoted in the press release.
* SOT 2006-11-08 Mercury transit: distance of Mercury's centre inside the
  solar limb in units of Mercury's radius (independent of the image scale).

Usage:  python tools/validate_hinode_images.py
Images (about 10 MB) are downloaded from isas.jaxa.jp / hinode.nao.ac.jp
into data/cache/hinode_images/ on the first run.
"""
import math
import os
import struct
import sys
import zlib

import numpy as np
from skyfield.vectorlib import VectorFunction

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from eclipsecalc.context import CACHE_DIR, get_context  # noqa: E402
from eclipsecalc.local import LocalSearch, Params  # noqa: E402
from eclipsecalc.net import download_file  # noqa: E402
from eclipsecalc.observers import SSCWebObserver  # noqa: E402
from eclipsecalc.timeutil import parse_utc  # noqa: E402

CTX = get_context()
IMG_DIR = CACHE_DIR / 'hinode_images'
XRT_PLATE = 1.0286          # arcsec / pixel of XRT at 2048 x 2048
ISAS = 'https://www.isas.jaxa.jp/home/solar/'
NAOJ = 'https://hinode.nao.ac.jp/uploads/2016/06/24/'


# --- image input ---------------------------------------------------------------
def fetch(url):
    path = IMG_DIR / url.rsplit('/', 1)[1].replace('%20', '_')
    if not path.exists():
        IMG_DIR.mkdir(parents=True, exist_ok=True)
        download_file(url, path, log=lambda *a: None)
    return path


def read_bmp(path):
    b = open(path, 'rb').read()
    off = struct.unpack_from('<I', b, 10)[0]
    w, h = struct.unpack_from('<ii', b, 18)
    bpp = struct.unpack_from('<H', b, 28)[0]
    rowlen = (w * bpp // 8 + 3) // 4 * 4
    a = np.frombuffer(b[off:off + rowlen * abs(h)], np.uint8).reshape(abs(h), rowlen)
    if bpp == 8:
        pal = np.frombuffer(b[54:54 + 1024], np.uint8).reshape(256, 4)[:, :3]
        img = pal[a[:, :w]].mean(axis=2)
    else:
        img = a[:, :w * 3].reshape(abs(h), w, 3).mean(axis=2)
    return img[::-1] if h > 0 else img


def read_png(path):
    b = open(path, 'rb').read()
    pos, idat, plte = 8, b'', None
    while pos < len(b):
        n = struct.unpack('>I', b[pos:pos + 4])[0]
        t, d = b[pos + 4:pos + 8], b[pos + 8:pos + 8 + n]
        pos += 12 + n
        if t == b'IHDR':
            w, h, _, ct = struct.unpack('>IIBB', d[:10])
        elif t == b'IDAT':
            idat += d
        elif t == b'PLTE':
            plte = np.frombuffer(d, np.uint8).reshape(-1, 3)
    raw = np.frombuffer(zlib.decompress(idat), np.uint8)
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    stride = w * ch
    out = np.zeros((h, stride), np.int32)
    prev = np.zeros(stride, np.int32)
    for y in range(h):
        f = raw[y * (stride + 1)]
        cur = raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)].astype(np.int32)
        if f == 2:
            cur = (cur + prev) & 255
        elif f in (1, 3, 4):
            cur = cur.copy()
            for x in range(stride):
                a = cur[x - ch] if x >= ch else 0
                c = prev[x - ch] if x >= ch else 0
                if f == 1:
                    cur[x] = (cur[x] + a) & 255
                elif f == 3:
                    cur[x] = (cur[x] + ((a + prev[x]) >> 1)) & 255
                else:
                    p = a + prev[x] - c
                    pa, pb, pc = abs(p - a), abs(p - prev[x]), abs(p - c)
                    cur[x] = (cur[x] + (a if pa <= pb and pa <= pc else prev[x] if pb <= pc else c)) & 255
        out[y] = prev = cur
    img = out.reshape(h, w, ch).astype(np.uint8)
    if ct == 3:
        img = plte[img[:, :, 0]]
    return img[:, :, :3].astype(float).mean(axis=2)


# --- geometry fits ---------------------------------------------------------------
def fit_center(x, y, R, c0, clip=2.5):
    """Centre of a circle of known radius R through the points (robust)."""
    cx, cy = c0
    keep = np.abs(np.hypot(x - cx, y - cy) - R) < 6.0
    if keep.sum() < 8:
        keep = np.ones(x.size, bool)
    for _ in range(20):
        for _ in range(10):
            dx, dy = x[keep] - cx, y[keep] - cy
            d = np.hypot(dx, dy)
            step, *_ = np.linalg.lstsq(np.c_[-dx / d, -dy / d], R - d, rcond=None)
            cx, cy = cx + step[0], cy + step[1]
        res = np.hypot(x - cx, y - cy) - R
        s = 1.4826 * np.median(np.abs(res[keep]))
        new = np.abs(res) < max(clip * s, 0.5)
        if np.array_equal(new, keep) or new.sum() < 5:
            break
        keep = new
    return cx, cy, int(keep.sum()), float(np.std(res[keep]))


def fit_circle(x, y):
    A = np.c_[2 * x, 2 * y, np.ones(x.size)]
    (cx, cy, c), *_ = np.linalg.lstsq(A, x * x + y * y, rcond=None)
    return cx, cy, math.sqrt(c + cx * cx + cy * cy)


class XRTImage:
    def __init__(self, path):
        self.im = read_bmp(path)
        self.h, self.w = self.im.shape
        self.YY, self.XX = np.mgrid[0:self.h, 0:self.w].astype(float)
        self.scale = XRT_PLATE * 2048.0 / self.w

    def _polar(self, c):
        return (np.hypot(self.XX - c[0], self.YY - c[1]),
                np.degrees(np.arctan2(self.YY - c[1], self.XX - c[0])) % 360)

    def moon_edge(self, sun, moon, band, limb_margin, level):
        """Per 2-degree sector, maximum-likelihood radius of the step between
        the dark lunar disk and the photon-sparse X-ray emission behind it."""
        cx, cy, R = moon
        r, th = self._polar(moon)
        ring = np.abs(r - R) < band
        bright = self.im > level
        pts = []
        for a0 in np.arange(0, 360, 2.0):
            a = math.radians(a0 + 1.0)
            xe, ye = cx + R * math.cos(a), cy + R * math.sin(a)
            if not (3 < xe < self.w - 3 and 3 < ye < self.h - 3):
                continue
            if math.hypot(xe - sun[0], ye - sun[1]) > sun[2] + limb_margin:
                continue
            m = ring & (th >= a0) & (th < a0 + 2.0)
            rr, nz = r[m], bright[m]
            if rr.size < 30:
                continue
            o = np.argsort(rr)
            rr, nz = rr[o], nz[o]
            if nz[rr > R + 3].size < 10 or nz[rr > R + 3].mean() < 0.12:
                continue
            k = np.cumsum(nz)[:-1]
            n = np.arange(1, rr.size)
            ko, no = nz.sum() - k, rr.size - n
            p_in = np.clip(k / n, 1e-9, 1 - 1e-9)
            p_out = np.clip(ko / no, 1e-9, 1 - 1e-9)
            ll = k * np.log(p_in) + (n - k) * np.log(1 - p_in) + ko * np.log(p_out) + (no - ko) * np.log(1 - p_out)
            ll[p_out <= p_in] = -np.inf
            j = int(np.argmax(ll))
            re = 0.5 * (rr[j] + rr[j + 1])
            pts.append((cx + re * math.cos(a), cy + re * math.sin(a)))
        return np.array(pts).reshape(-1, 2)

    def sun_limb(self, sun, moon, band, moon_gap):
        """X-ray limb = sharp inner edge of the off-limb brightening."""
        cx, cy, R = sun
        r, th = self._polar(sun)
        ring = (np.abs(r - R) < band) & (np.hypot(self.XX - moon[0], self.YY - moon[1]) > moon[2] + moon_gap)
        step = 1.0 if self.w >= 1024 else 0.5
        bins = np.arange(R - band, R + band + 0.01, step)
        rc = 0.5 * (bins[1:] + bins[:-1])
        w = max(4, int(round(8 / step * self.w / 1024)))
        pts = []
        for a0 in np.arange(0, 360, 4.0):
            m = ring & (th >= a0) & (th < a0 + 4.0)
            s, _ = np.histogram(r[m], bins, weights=self.im[m])
            n, _ = np.histogram(r[m], bins)
            if np.any(n < 2):
                continue
            prof = np.convolve(s / n, np.ones(3) / 3, mode='same')
            best = None
            for i in range(w, prof.size - w):
                inner, outer = prof[i - w:i].mean(), prof[i:i + w].mean()
                if outer > 12 and outer > 1.6 * max(inner, 1.0) and (best is None or outer - inner > best[0]):
                    best = (outer - inner, i)
            if best is None:
                continue
            g = np.gradient(prof)
            j = best[1] - 3 + int(np.argmax(g[best[1] - 3:best[1] + 3]))
            den = g[j - 1] - 2 * g[j] + g[j + 1] if 1 <= j < g.size - 1 else 0
            rad = rc[j] + (np.clip(0.5 * (g[j - 1] - g[j + 1]) / den, -1, 1) * step if den else 0.0)
            a = math.radians(a0 + 2.0)
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        return np.array(pts).reshape(-1, 2)

    def best_on_track(self, track_px, Rm, level):
        """Point of a candidate Moon-centre track that best matches the dark
        lunar disk (dark inside, bright just outside, within the image)."""
        sub = (slice(None, None, 4), slice(None, None, 4))
        X, Y, bright = self.XX[sub], self.YY[sub], self.im[sub] > level
        best = None
        for c in track_px:
            r = np.hypot(X - c[0], Y - c[1])
            inside, outside = r < Rm - 5, (r > Rm + 5) & (r < Rm + 25)
            if inside.sum() < 50 or outside.sum() < 50:
                continue
            score = bright[outside].mean() - bright[inside].mean()
            if best is None or score > best[0]:
                best = (score, c)
        return best[1]

    def measure(self, rb, rs, moon_guess, limb_margin=-10, moon_level=0.0, moon_gap=None, sun_band=None):
        """Moon/Sun centres for known angular radii; returns (E, N) offset of
        the Moon from the Sun in arcsec (image north up) and a 1-sigma error."""
        k = self.w / 1024
        Rm, Rs = rb / self.scale, rs / self.scale
        moon = (moon_guess[0], moon_guess[1], Rm)
        sun = (self.w / 2, self.h / 2, Rs)
        for it in range(8):
            mp = self.moon_edge(sun, moon, (120 if it == 0 else 40) * k, limb_margin, moon_level)
            mx, my, nm, rms_m = fit_center(mp[:, 0], mp[:, 1], Rm, moon[:2])
            moon = (mx, my, Rm)
            for _ in range(6):
                sp = self.sun_limb(sun, moon, sun_band or 40 * k, 15 * k if moon_gap is None else moon_gap)
                sx, sy, ns, rms_s = fit_center(sp[:, 0], sp[:, 1], Rs, sun[:2])
                sun = (sx, sy, Rs)
        err = self.scale * math.hypot(rms_m / math.sqrt(nm / 2), rms_s / math.sqrt(ns / 2))
        return np.array([-(mx - sx) * self.scale, -(my - sy) * self.scale]), err


# --- predictions -----------------------------------------------------------------
class _Shifted(VectorFunction):
    """Satellite orbit evaluated ``shift_s`` seconds later (along-track shift)."""
    center = 399
    ephemeris = None

    def __init__(self, base, shift_s):
        self.base, self.shift, self.target = base, shift_s / 86400.0, base.target

    def _at(self, t):
        return self.base._at(CTX.ts.tt_jd(t.whole, t.tt_fraction + self.shift))


class Predictor:
    def __init__(self, body, a, b, params=None):
        self.body, self.p = body, params or Params(include_invisible=True)
        self.a, self.b = parse_utc(CTX, a), parse_utc(CTX, b)
        self.obs = SSCWebObserver(CTX, 'hinode', 'Hinode')
        self.obs.survey(self.a, self.b)
        self.obs.prepare(CTX, [(self.a - 0.01, self.b + 0.01)])
        self.base = self.obs.vf

    def details(self, times, shift=0.0):
        self.obs.vf = _Shifted(self.base, shift) if shift else self.base
        ls = LocalSearch(CTX, self.obs, self.body, self.p, self.a, self.b)
        return ls.details(np.array([parse_utc(CTX, t) for t in times]) - ls.jd0)


def solar_P(jd_tt):
    """Position angle of the solar rotation axis (Meeus, ch. 29)."""
    T = (jd_tt - 2451545.0) / 36525
    L0 = 280.46646 + 36000.76983 * T
    M = math.radians(357.52911 + 35999.05029 * T)
    C = (1.914602 - 0.004817 * T) * math.sin(M) + 0.019993 * math.sin(2 * M) + 0.000289 * math.sin(3 * M)
    om = math.radians(125.04 - 1934.136 * T)
    lam = math.radians(L0 + C - 0.00569 - 0.00478 * math.sin(om))
    eps = math.radians(23.4392911 + 0.00256 * math.cos(om))
    K = math.radians(73.6667 + 1.3958333 * (jd_tt - 2396758.0) / 36525)
    return math.degrees(math.atan(-math.cos(lam) * math.tan(eps)) +
                        math.atan(-math.cos(lam - K) * math.tan(math.radians(7.25))))


def rotate(v, deg):
    a = math.radians(deg)
    return np.array([math.cos(a) * v[0] - math.sin(a) * v[1], math.sin(a) * v[0] + math.cos(a) * v[1]])


def xrt_shift_fit(title, span, images, opts=None):
    pred = Predictor('moon', *span)
    times = [t for _, t in images]
    d0 = pred.details(times)
    P = solar_P(parse_utc(CTX, times[len(times) // 2]))
    M, W = [], []
    for i, (url, t) in enumerate(images):
        img = XRTImage(fetch(url))
        off = rotate((d0['xi'][i], d0['eta'][i]), P)
        guess = (img.w / 2 - off[0] / img.scale, img.h / 2 - off[1] / img.scale)
        m, err = img.measure(d0['rho_b'][i], d0['rho_s'][i], guess, **(opts or {}).get(i, {}))
        M.append(m)
        W.append(1.0 / err)
    M, W = np.array(M), np.array(W)
    rows = []
    for s in np.arange(-30.0, 16.01, 1.0):
        d = pred.details(times, s)
        Pv = np.c_[d['xi'], d['eta']]
        th = math.atan2(np.sum(W ** 2 * (Pv[:, 0] * M[:, 1] - Pv[:, 1] * M[:, 0])),
                        np.sum(W ** 2 * (Pv[:, 0] * M[:, 0] + Pv[:, 1] * M[:, 1])))
        res = M - np.array([rotate(v, math.degrees(th)) for v in Pv])
        rows.append((s, math.degrees(th), np.sum((W[:, None] * res) ** 2), res))
    k = int(np.argmin([r[2] for r in rows]))
    sl = slice(max(k - 2, 0), k + 3)
    a, b, _ = np.polyfit([r[0] for r in rows[sl]], [r[2] for r in rows[sl]], 2)
    best = -b / (2 * a)
    print(f'{title}: orbit time-shift {best:+.1f} s (actual Hinode ahead of the SSCWeb orbit by '
          f'{best * 7.5:+.0f} km), image rotation {rows[k][1]:+.2f} deg vs solar P {P:+.2f} deg, '
          f'max residual {np.abs(rows[k][3]).max():.1f}"')
    return best


def track_test_2012():
    pred = Predictor('moon', '2012-11-13T20:00', '2012-11-13T20:50')
    imgs = [(ISAS + 'eclipse20121114/121113_Eclipse_2019UT.bmp', '2012-11-13T20:19:30'),
            (ISAS + 'eclipse20121114/121113_Eclipse_2032UT.bmp', '2012-11-13T20:32:30')]
    P = solar_P(parse_utc(CTX, '2012-11-13T20:25'))
    tt = [f'2012-11-13T20:{m:02d}:{s:02d}' for m in range(12, 40) for s in range(0, 60, 2)]
    d = pred.details(tt)
    track = np.array([rotate(v, P) for v in zip(d['xi'], d['eta'])])
    sep_min = np.hypot(track[:, 0], track[:, 1]).min()
    dg = pred.details([t for _, t in imgs])
    offs = []
    for i, (url, t) in enumerate(imgs):
        img = XRTImage(fetch(url))
        Rm = dg['rho_b'][i] / img.scale
        guess = img.best_on_track(np.c_[img.w / 2 - track[:, 0] / img.scale,
                                        img.h / 2 - track[:, 1] / img.scale], Rm, 10.0)
        m, _ = img.measure(dg['rho_b'][i], dg['rho_s'][i], guess, moon_level=10.0)
        k = int(np.argmin(np.hypot(*(track - m).T)))
        tang = track[min(k + 1, len(track) - 1)] - track[max(k - 1, 0)]
        nrm = np.array([-tang[1], tang[0]]) / np.linalg.norm(tang)
        nrm *= -np.sign(np.dot(nrm, track[k]))          # + = toward the Sun centre
        offs.append(float(np.dot(m - track[k], nrm)))
    rs, rb = dg['rho_s'][0], dg['rho_b'][0]
    sep_obs = sep_min - np.mean(offs)
    print(f'2012-11-13 track: computed minimum separation {sep_min:.1f}", measured track offsets '
          f'{offs[0]:+.1f}" / {offs[1]:+.1f}" (mean {np.mean(offs):+.1f}") -> observed minimum ~{sep_obs:.0f}"; '
          f'(totality needs < {rb - rs:.0f}"; magnitude 0.993 would mean {rb - rs + 2 * rs * 0.007:.0f}")')


def mercury_2006():
    """Mercury's centre distance inside the solar limb, in Mercury radii."""
    def measure(path, guess, mirror=False, r_fixed=None):
        im = read_png(path)
        if mirror:
            im = im[:, ::-1]
        h, w = im.shape
        k = np.ones(5) / 5
        sm = np.apply_along_axis(lambda v: np.convolve(v, k, 'same'), 0,
                                 np.apply_along_axis(lambda v: np.convolve(v, k, 'same'), 1, im))
        dark = np.percentile(sm, 3)
        mx, my, mr = guess
        ly, lx = [], []
        for y in range(10, h - 10, 4):
            if abs(y - my) < 1.6 * mr:
                continue
            row = sm[y]
            thr = dark + 0.5 * (np.percentile(row, 90) - dark)
            idx = np.nonzero((row[:-1] < thr) & (row[1:] >= thr))[0]
            if idx.size:
                i = idx[0]
                lx.append(i + (thr - row[i]) / (row[i + 1] - row[i]))
                ly.append(y)
        ly, lx = np.array(ly, float), np.array(lx)
        coef = np.polyfit(ly, lx, 2)
        limb = np.poly1d(coef)
        YY, XX = np.mgrid[0:h, 0:w]
        phot = np.median(sm[(np.abs(YY - my) < 2 * mr) & (XX > limb(YY) + 15) & (np.hypot(XX - mx, YY - my) > 1.4 * mr)])
        thr = dark + 0.5 * (phot - dark)
        px, py = [], []
        for a in np.radians(np.arange(0, 360, 2)):
            rr = np.arange(0.4 * mr, 1.6 * mr, 0.25)
            x, y = mx + rr * math.cos(a), my + rr * math.sin(a)
            if x.min() < 1 or y.min() < 1 or x.max() > w - 2 or y.max() > h - 2 or not np.all(x[-8:] > limb(y[-8:]) + 6):
                continue
            v = sm[np.round(y).astype(int), np.round(x).astype(int)]
            idx = np.nonzero((v[:-1] < thr) & (v[1:] >= thr))[0]
            if idx.size:
                i = idx[-1]
                r = rr[i] + 0.25 * (thr - v[i]) / (v[i + 1] - v[i])
                xe, ye = mx + r * math.cos(a), my + r * math.sin(a)
                if xe > limb(ye) + 4:
                    px.append(xe)
                    py.append(ye)
        px, py = np.array(px), np.array(py)
        if r_fixed:
            cx, cy, _, _ = fit_center(px, py, r_fixed, (mx, my))
            r = r_fixed
        else:
            cx, cy, r = fit_circle(px, py)
        yy = np.linspace(0, h, 4001)
        dist = np.hypot(limb(yy) - cx, yy - cy).min()
        return (1 if cx > limb(cy) else -1) * dist / r, r

    pred = Predictor('mercury', '2006-11-08T18:00', '2006-11-09T01:30')
    shots = [(NAOJ + 'mercury_sot_191441%202.png', '2006-11-08T19:14:41', (630, 460, 90), False),
             (NAOJ + 'mercury_sot_191302%202.png', '2006-11-08T19:13:02', (400, 460, 90), False),
             (NAOJ + 'SOT_M_t3_Red%202.png', '2006-11-09T00:08:02', (734, 660, 90), True)]
    d = pred.details([s[1] for s in shots])
    pred_dr = (d['rho_s'] - d['sep_arcsec']) / d['rho_b']
    r_fixed = None
    print('2006-11-08 Mercury (SOT): centre inside the limb in Mercury radii, measured vs computed for Hinode')
    for (url, t, guess, mirror), p in zip(shots, pred_dr):
        dr, r = measure(fetch(url), guess, mirror, r_fixed)
        r_fixed = r_fixed or r
        print(f'  {t[11:]}  measured {dr:+.2f}  computed {p:+.2f}')


if __name__ == '__main__':
    xrt_shift_fit('2014-10-23 XRT', ('2014-10-23T21:40', '2014-10-23T22:05'), [
        (ISAS + 'eclipse20141023/ImgBW/20141023_20_XRTEclipse_214833.bmp', '2014-10-23T21:48:33'),
        (ISAS + 'eclipse20141023/ImgBW/20141023_20_XRTEclipse_215333.bmp', '2014-10-23T21:53:33'),
        (ISAS + 'eclipse20141023/ImgBW/20141023_20_XRTEclipse_215813.bmp', '2014-10-23T21:58:13')],
        opts={1: dict(limb_margin=-4, moon_gap=2, sun_band=18)})
    # The Sun's limb is hidden at maximum (00:08:07), and Hinode's pointing
    # drifts by ~25" during the eclipse, so only the two partial phases are used.
    xrt_shift_fit('2016-03-09 XRT', ('2016-03-08T23:55', '2016-03-09T00:20'), [
        (ISAS + 'eclipse20160309/ImgBW/XRTEclipse_20160309_000324.bmp', '2016-03-09T00:03:24'),
        (ISAS + 'eclipse20160309/ImgBW/XRTEclipse_20160309_001407.bmp', '2016-03-09T00:14:07')])
    xrt_shift_fit('2017-08-21 XRT', ('2017-08-21T16:45', '2017-08-21T17:10'), [
        (ISAS + 'eclipse20170821/ImgBW/Eclipse_Start+2min_170821_165232_BW.bmp', '2017-08-21T16:52:32'),
        (ISAS + 'eclipse20170821/ImgBW/Eclipse_Minimum_170821_165732_BW.bmp', '2017-08-21T16:57:32'),
        (ISAS + 'eclipse20170821/ImgBW/Eclipse_End-2min_170821_170302_BW.bmp', '2017-08-21T17:03:02')])
    track_test_2012()
    mercury_2006()
