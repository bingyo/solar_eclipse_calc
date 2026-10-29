"""Figures of the Hinode QL frames used in docs/hinode_validation.md (appendix).

For every pass (or transit) compared with eclipsecalc, one frame of the QL
movie of NAOJ (read with tools/hinode_ql.py) is saved as it is, with its
title (UT of the exposure start) and axes (helioprojective arcsec, solar
north up), next to the same frame with the computed lunar limb (cyan dots):
SSCWeb orbit without any time shift, image turned by the computed solar P
angle and the Moon placed relative to the centre of the X-ray Sun measured on
the frames of the movie without the Moon ('full'), or with the movie axes
(the satellite pointing) for movies of a part of the Sun ('axes').  Mercury's
frames are the ones nearest the computed contacts, and the transit of Venus of 2012 (section 1.4) is shown
by one frame, both without overlay.

Usage:  python tools/hinode_ql_figures.py [ONLY]   (ONLY: a prefix of the pass time, e.g. 2026-08-12)
Writes docs/images/hinode/*.jpg (JPEG through ffmpeg) and prints the list of
the frames (pass, movie, frame UT, image).  Needs ffmpeg and the movies
(downloaded into data/cache/hinode_ql/ on the first run).
"""
import datetime as dt
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import hinode_ql as q  # noqa: E402
import validate_hinode_ql as v  # noqa: E402
from eclipsecalc.local import Params  # noqa: E402
from validate_hinode_images import Predictor  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'docs', 'images', 'hinode')
CYAN, GAP = (0, 255, 255), (64, 64, 64)

# (maximum of the pass, UT; QL file; 'full' = full-Sun movie, 'axes' = part of the Sun placed with the axes;
#  frame: 'max' = the measured frame nearest the maximum, 'cover' = the computed lunar limb nearest the field centre,
#  'first' = the first frame of the movie)
XRT_PASSES = (
    # 1.3: 2006-2016, full-Sun XRT movies
    ('2007-02-17T16:10:01', 'XRT_AMM20070217160100_162607.mpg'),
    ('2007-03-19T02:55:43', 'XRT_APM20070319024759_030354.mpg'),
    ('2009-07-22T00:58:03', 'XRT_TIM20090722004900_011313.mpg'),
    ('2011-01-04T07:48', 'XRT_TIM20110104073804_075739.mpg'),
    ('2011-01-04T09:16', 'XRT_TIM20110104090615_092549.mpg'),
    ('2011-01-04T10:45', 'XRT_TIM20110104104005_180111.mpg'),
    ('2011-06-01T20:41', 'XRT_TIM20110601203137_223344.mpg'),
    ('2011-06-01T22:11', 'XRT_TIM20110601203137_223344.mpg'),
    ('2011-11-25T04:15', 'XRT_TIM20111125041108_180838.mpg'),
    ('2011-11-25T06:05', 'XRT_TIM20111125041108_180838.mpg'),
    ('2011-11-25T07:56', 'XRT_TIM20111125041108_180838.mpg'),
    ('2012-05-20T21:41', 'XRT_TIM20120520060509_215306.mpg'),
    ('2012-05-21T00:32', 'XRT_TIM20120521002129_004606.mpg'),
    ('2012-05-21T02:01', 'XRT_TIM20120521014830_182237.mpg'),
    ('2012-11-13T20:24', 'XRT_APM20121113180133_235303.mpg'),
    ('2012-11-14T00:20', 'XRT_APM20121114001202_002939.mpg'),
    ('2013-05-09T21:50', 'XRT_AMM20130509213758_215127.mpg'),
    ('2013-05-09T21:50', 'XRT_TIM20130509213750_215119.mpg'),
    ('2013-05-10T01:48', 'XRT_AMM20130510013905_182335.mpg'),
    ('2013-05-10T01:48', 'XRT_TIM20130510013857_182407.mpg'),
    ('2013-05-10T03:28', 'XRT_AMM20130510013905_182335.mpg'),
    ('2013-05-10T03:28', 'XRT_TIM20130510013857_182407.mpg'),
    ('2013-11-03T11:29', 'XRT_AMM20131103060307_190806.mpg'),
    ('2013-11-03T11:29', 'XRT_TIM20131103060344_190844.mpg'),
    ('2014-04-29T07:27', 'XRT_TIM20140429060343_074328.mpg'),
    ('2014-10-23T21:53', 'XRT_APM20141023214500_220442.mpg'),
    ('2014-10-23T21:53', 'XRT_TIM20141023182245_220433.mpg'),
    ('2015-03-20T10:57', 'XRT_APM20150320085121_111120.mpg'),
    ('2015-09-13T05:55', 'XRT_BEM20150913054017_180659.mpg'),
    ('2015-09-13T07:46', 'XRT_BEM20150913054017_180659.mpg'),
    ('2016-03-09T00:08', 'XRT_BEM20160309000006_180416.mpg'),
    ('2016-03-09T04:20', 'XRT_BEM20160309000006_180416.mpg'),
    # 1.2: 2017-2026, the short-exposure (or single) movie of every pass in the table
    ('2017-08-21T16:57:32', 'XRT_APM20170821061950_185419.mpg'),
    ('2019-01-06T00:20:46', 'XRT_APM20190106000012_005922.mpg'),
    ('2019-12-26T03:44:16', 'XRT_APM20191226033017_035643.mpg'),
    ('2019-12-26T06:29:41', 'XRT_APM20191226061510_064436.mpg'),
    ('2020-06-21T09:16:03', 'XRT_APM20200621090843_092939.mpg'),
    ('2020-12-14T15:00:49', 'XRT_APM20201214121211_152944.mpg'),
    ('2021-06-10T11:23:43', 'XRT_APM20210610092259_114827.mpg'),
    ('2021-12-04T07:40:28', 'XRT_APM20211204030331_075942.mpg'),
    ('2022-10-25T09:06:51', 'XRT_APM20221025083311_092537.mpg'),
    ('2022-10-25T10:35:51', 'XRT_APM20221025102555_174519.mpg'),
    ('2022-10-25T12:03:13', 'XRT_APM20221025102555_174519.mpg'),
    ('2023-04-20T05:48:50', 'XRT_ATM20230420054226_060146.mpg'),
    ('2023-10-14T15:31:17', 'XRT_APM20231014150313_155739.mpg'),
    ('2024-04-08T17:00:05', 'XRT_APM20240408164110_171936.mpg'),
    ('2024-04-08T19:42:22', 'XRT_APM20240408172346_195845.mpg'),
    ('2024-10-02T16:35:12', 'XRT_APM20241002124345_180447.mpg'),
    ('2025-03-29T09:25:12', 'XRT_APM20250329033322_175013.mpg'),
    ('2025-03-29T10:52:26', 'XRT_APM20250329033322_175013.mpg'),
    ('2025-03-29T12:20:01', 'XRT_APM20250329033322_175013.mpg'),
    ('2025-09-21T17:47:04', 'XRT_APM20250921040327_213249.mpg'),
    ('2025-09-21T19:36:02', 'XRT_APM20250921040327_213249.mpg'),
    ('2026-08-12T17:53:49', 'XRT_APM20260812163330_183231.mpg'),
)
OTHER = (
    # 1.3: movies of a part of the Sun (SOT about 220", XRT 400-1,050"): the Moon placed with the axes
    ('2007-03-19T01:28', 'FG_GBM20070319012408_013233.mpg', 'axes', 'cover'),
    ('2007-03-19T02:55:43', 'FG_GBM20070319014414_030159.mpg', 'axes', 'cover'),
    ('2007-03-19T04:24', 'FG_GBM20070319042137_043343.mpg', 'axes', 'cover'),
    ('2008-02-07T03:25', 'FG_BLM20080207031600_033431.mpg', 'axes', 'cover'),
    ('2009-01-26T05:47', 'XRT_APM20090126053534_061156.mpg', 'axes', 'cover'),
    ('2009-07-22T05:06', 'XRT_APM20090722045846_055555.mpg', 'axes', 'cover'),
    ('2010-07-11T21:48', 'FG_GBM20100711214509_215513.mpg', 'axes', 'cover'),
    ('2015-03-20T08:03', 'FG_CAM20150320000040_235938.mpg', 'axes', 'cover'),
    # 1.2: the eclipse seen only from orbit, 1.5 min after its end (the Moon against the corona)
    ('2025-02-28T00:49:32', 'XRT_APM20250228005341_005736.mpg', 'full', 'first'),
)


def movie_url(fname):
    d = fname.split('_')[1][3:11]
    return f'{q.QL_BASE}/{d[:4]}/{d[4:6]}/{d[6:8]}/{fname}'


def jpeg(path, img, quality=4):
    """Save an HxWx3 uint8 array as JPEG with ffmpeg (full chroma, so that the thin dots keep their colour)."""
    h, w = img.shape[:2]
    subprocess.run([q.find_ffmpeg(), '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{w}x{h}',
                    '-i', '-', '-pix_fmt', 'yuvj444p', '-q:v', str(quality), path],
                   input=np.ascontiguousarray(img, np.uint8).tobytes(), check=True)


def dots(img, c, r, box, colour=CYAN, spacing=8.0):
    """Dots (2x2 px) every `spacing` px on the circle of radius r px around c = (col, row), inside the plot box."""
    L, R, T, B = box
    for a in np.arange(0.0, 2 * np.pi, spacing / r):
        x, y = int(round(c[0] + r * np.cos(a))), int(round(c[1] + r * np.sin(a)))
        if L + 2 <= x < R - 2 and T + 2 <= y < B - 2:
            img[y:y + 2, x:x + 2] = colour


def side_by_side(*imgs):
    h = max(i.shape[0] for i in imgs)
    parts = []
    for k, i in enumerate(imgs):
        if k:
            parts.append(np.full((h, 8, 3), GAP, np.uint8))
        parts.append(np.pad(i, ((0, h - i.shape[0]), (0, 0), (0, 0))))
    return np.concatenate(parts, axis=1)


def image_name(tmax, fname):
    return f"{tmax[:10].replace('-', '')}_{tmax[11:13]}{tmax[14:16]}_{fname.split('_')[0]}_{fname.split('_')[1][:3]}.jpg"


def pass_figure(tmax, fname, mode='full', pick='max'):
    """Save the figure of one pass; dict(time of the frame, image file, ...)."""
    t_max = dt.datetime.fromisoformat(tmax)
    frames = q.movie_frames(movie_url(fname))
    pred = Predictor('moon', v.utc(t_max - dt.timedelta(minutes=45)), v.utc(t_max + dt.timedelta(minutes=45)))
    c1, c4 = v.pass_window(pred, t_max)
    margin = dt.timedelta(minutes=8)
    sel = [f for f in frames if f[0] and f[2] and c1 - margin <= f[0] <= c4 + margin]
    if not sel:
        raise ValueError(f'{fname}: no frame in the pass {tmax}')
    d = pred.details([v.utc(t) for t, _, _ in sel])
    near = d['sep_arcsec'] <= d['rho_s'] + d['rho_b'] + 20
    if mode == 'full':
        _, sun_hpc, ncal = v.calibrate(sel, d, list(np.nonzero(~near)[0]))
        if not ncal:
            raise ValueError(f'{fname}: no frame without the Moon to measure the Sun centre')
    else:
        sun_hpc = (0.0, 0.0)
    if pick == 'first':
        j = 0
    elif pick == 'cover':
        L, R, T, B = sel[0][2].box
        mid = np.array([(L + R) / 2, (T + B) / 2])
        score = [abs(np.hypot(*(np.array(v.predicted_px(d, k, sel[k][2], sun_hpc)[0]) - mid))
                     - v.predicted_px(d, k, sel[k][2], sun_hpc)[2]) for k in range(len(sel))]
        j = int(np.argmin(score))
    else:
        on = np.nonzero(d['sep_arcsec'] < d['rho_s'] + d['rho_b'])[0]
        cand = on if on.size else np.arange(len(sel))
        j = int(cand[np.argmin([abs((sel[k][0] - t_max).total_seconds()) for k in cand])])
    t, img, ax = sel[j]
    moon, _, Rm, _, _ = v.predicted_px(d, j, ax, sun_hpc)
    over = img.copy()
    dots(over, moon, Rm, ax.box)
    name = image_name(tmax, fname)
    os.makedirs(OUT, exist_ok=True)
    jpeg(os.path.join(OUT, name), side_by_side(img, over))
    return dict(tmax=tmax, file=fname, url=movie_url(fname), frame=v.hms(t), image=name, c1=v.hms(c1), c4=v.hms(c4),
                magnitude=float(d['magnitude'][j]) if 'magnitude' in d else None)


def raw_figure(time, fname, tag):
    """The frame of a movie nearest `time` (UT), as it is."""
    t0 = dt.datetime.fromisoformat(time)
    t, img, _ = min((f for f in q.movie_frames(movie_url(fname)) if f[0]), key=lambda f: abs((f[0] - t0).total_seconds()))
    name = f"{time[:10].replace('-', '')}_{tag}.jpg"
    os.makedirs(OUT, exist_ok=True)
    jpeg(os.path.join(OUT, name), img)
    return dict(tmax=time, file=fname, url=movie_url(fname), frame=v.hms(t), image=name)


def mercury_figures():
    """Frames of the 2006-11-08 transit of Mercury nearest the computed contacts (default solar radius)."""
    pred = Predictor('mercury', '2006-11-08T18:00', '2006-11-09T01:30', Params(include_invisible=True))
    out = []
    for name, path, labels in v.MERCURY_MOVIES:
        frames = [f for f in q.movie_frames(f'{q.QL_BASE}/{path}') if f[0]]
        c = v.computed_contacts(pred, frames[0][0], frames[-1][0])
        picks = []
        for lab, kind in zip(labels, ('ext', 'int')):
            tc = c[kind]
            picks.append((lab, tc, min(frames, key=lambda f: abs((f[0] - tc).total_seconds()))))
        picks.sort(key=lambda p: p[1])
        fname = path.rsplit('/', 1)[1]
        img_name = f'20061108_mercury_{name}.jpg'
        os.makedirs(OUT, exist_ok=True)
        jpeg(os.path.join(OUT, img_name), side_by_side(*(f[1] for _, _, f in picks)))
        out.append(dict(tmax=name, file=fname, url=movie_url(fname), image=img_name,
                        frame=' / '.join(f'{lab} {v.hms(tc)} -> {v.hms(f[0])}' for lab, tc, f in picks)))
    return out


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ''
    q.find_ffmpeg()
    rows = mercury_figures() if '2006-11-08'.startswith(only) else []
    for tmax, fname in XRT_PASSES:
        if tmax.startswith(only):
            rows.append(pass_figure(tmax, fname))
    for tmax, fname, mode, pick in OTHER:
        if tmax.startswith(only):
            rows.append(pass_figure(tmax, fname, mode, pick))
    if '2012-06-06'.startswith(only[:10]):
        rows.append(raw_figure('2012-06-06T01:00', 'FG_GBM20120606001502_011840.mpg', 'venus_sot'))   # 1.4
    for r in rows:
        print(f"{r['tmax']}  {r['file']}  frame {r['frame']}  -> docs/images/hinode/{r['image']}", flush=True)


if __name__ == '__main__':
    main()
