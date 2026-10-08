"""Eclipses and transits from the command line (the same calculation as the
web UI). Meant for people and for AI agents alike; see docs/cli.md.

The observer is chosen by the options that are given (exactly one kind):

    --epoch --a --e --i --raan [--argp --m]     satellite, six orbital elements
    --epoch --alt|--perigee-alt --apogee-alt --sso|--i --ltan|--raan
                                                satellite, planned orbit (altitude, SSO, LTAN)
    --tle FILE | --norad N                      satellite, TLE (SGP4)
    --geo-lon DEG                               ideal geostationary satellite
    --horizons ID [--horizons-step MIN]         spacecraft from JPL Horizons
    --sscweb ID                                 past orbit from NASA SSCWeb
    --lat --lon [--elev] | --city NAME          point on the ground
    --global                                    whole Earth (where on Earth an eclipse is seen)
    --request FILE                              a JSON request (same shape as the web API)

    python cli.py --epoch 2027-07-30T12:00:00 --a 7058.1 --e 0.0012 --i 98.13 \\
        --raan 220.5 --argp 90 --m 45 --start 2027-07-25 --end 2027-08-10 --tz 9
    python cli.py --city Tokyo --start 2026-01-01 --end 2056-01-01 --format json --detail --lang en

Messages, tables and help are in the language of --lang (ja, en, fr, ru, es,
zh, hi), else $ECLIPSECALC_LANG, else the language of the environment, else
English; the texts are the Japanese keys of eclipsecalc/i18n.py.

Exit status: 0 = done, 1 = the input could not be calculated (message on
stderr, or {"ok": false, ...} on stdout with --format json), 2 = wrong options.
"""
import argparse
import contextlib
import csv
import datetime as _dt
import io
import json
import math
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eclipsecalc import i18n  # noqa: E402
from eclipsecalc.i18n import tr  # noqa: E402

PHENOMENA = ('moon', 'mercury', 'venus')
# Japanese names (the keys of their translations)
BODY_JA = {'moon': '日食', 'mercury': '水星の太陽面通過', 'venus': '金星の太陽面通過'}
TYPE_JA = {'total': '皆既日食', 'annular': '金環日食', 'hybrid': '金環皆既日食', 'partial': '部分日食'}
CONTACT_JA = {'C1': '第1接触', 'C2': '第2接触', 'MAX': '最大', 'C3': '第3接触', 'C4': '第4接触'}
ORBIT_ARGS = ('epoch', 'a', 'e', 'i', 'raan', 'alt', 'perigee_alt', 'apogee_alt', 'sso', 'ltan')
ORBIT_ONLY = ('argp', 'm', 'no_j2')
LANG_ENV = 'ECLIPSECALC_LANG'


class UsageError(Exception):
    pass


class InputError(ValueError):
    pass


# ---------------------------------------------------------------------------
# language
# ---------------------------------------------------------------------------
def _lang_code(text):
    """'ja_JP.UTF-8', 'en-GB', 'fr' ... -> one of the tool's languages, or None."""
    code = re.split(r'[_.@-]', str(text or '').strip().lower())[0]
    return code if code in i18n.LANGUAGES else None


def system_lang():
    """The language of the environment (LANGUAGE / LC_ALL / LC_MESSAGES / LANG, else
    the display language of Windows), or None if it is not one of the tool's."""
    for k in ('LC_ALL', 'LC_MESSAGES', 'LANG'):
        v = os.environ.get(k)
        if v:
            if v.split('.')[0] in ('C', 'POSIX'):
                return None
            # as gettext: LANGUAGE (a list) comes first when a locale is set
            return next(filter(None, map(_lang_code, os.environ.get('LANGUAGE', '').split(':') + [v])), None)
    if sys.platform == 'win32':
        try:
            import ctypes
            import locale
            return _lang_code(locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage()))
        except Exception:
            return None
    return None


def choose_lang(argv):
    """--lang, else $ECLIPSECALC_LANG, else the language of the environment, else
    English (as the web UI). Read before parsing, so that --help and the errors of
    the parser are translated too."""
    for k, a in enumerate(argv):
        if a == '--':
            break
        if a == '--lang' and k + 1 < len(argv) and _lang_code(argv[k + 1]) == argv[k + 1]:
            return argv[k + 1]
        if a.startswith('--lang=') and _lang_code(a[7:]) == a[7:]:
            return a[7:]
    return _lang_code(os.environ.get(LANG_ENV)) or system_lang() or 'en'


def _wide():
    return i18n.current() in ('ja', 'zh')


def _sep():
    """Separator of items: 「A・B」 in Japanese, "A, B" elsewhere (as the web UI)."""
    return {'ja': '・', 'zh': '，'}.get(i18n.current(), ', ')


def _paren(s):
    return f'（{s}）' if _wide() else f' ({s})'


def _span(a, b):
    return {'ja': f'{a} 〜 {b}', 'zh': f'{a} ～ {b}'}.get(i18n.current(), f'{a} – {b}')


# The errors of argparse itself (Python's English) in the language of the tool
_ARGPARSE_ERRORS = [
    (r'unrecognized arguments: (?P<args>.*)', '不明な引数があります: {args}'),
    (r'argument (?P<arg>\S+): expected one argument', '{arg} には値を 1 つ指定してください'),
    (r'argument (?P<arg>\S+): invalid (?:float|int) value: (?P<value>.*)', '{arg} の値 {value} は数値ではありません'),
    (r'argument (?P<arg>\S+): invalid choice: (?P<value>.*?) \(choose from (?P<choices>.*)\)',
     '{arg} の値 {value} は使えません（{choices} のどれか）'),
    (r'argument (?P<arg>\S+): ignored explicit argument (?P<value>.*)', '{arg} には値を付けられません（{value}）'),
]


def _argparse_error(message):
    for pattern, text in _ARGPARSE_ERRORS:
        m = re.fullmatch(pattern, message)
        if m:
            return tr(text, **m.groupdict())
    return message


# ---------------------------------------------------------------------------
# options
# ---------------------------------------------------------------------------
class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(_argparse_error(message))


class _Formatter(argparse.RawDescriptionHelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):
        return super().add_usage(usage, actions, groups, tr('使い方: ') if prefix is None else prefix)


def _width(s):
    """Width on a terminal: 2 for wide (CJK) characters, 0 for combining marks."""
    return sum(0 if unicodedata.category(c) in ('Mn', 'Me', 'Cf')
               else 2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in s)


def _epilog():
    kinds = [
        (tr('軌道6要素'), tr('--epoch --a --e --i --raan（と --argp --m）')),
        (tr('計画中の軌道'), tr('--epoch と、大きさ --alt または --perigee-alt --apogee-alt、傾斜角 --i または --sso、\n'
                                '軌道面 --raan または --ltan（軌道6要素の指定と組み合わせも可）')),
        ('TLE', tr('--tle ファイル（- で標準入力）または --norad 番号（CelesTrak から最新の TLE を取得）')),
        (tr('静止衛星'), tr('--geo-lon 経度')),
        ('JPL Horizons', tr('--horizons ID（例 -170 = JWST）')),
        ('NASA SSCWeb', tr('--sscweb ID（例 hinode。過去の軌道）')),
        (tr('地上の地点'), tr('--lat --lon（と --elev）または --city 地点名')),
        (tr('地球全体'), '--global'),
        (tr('JSON で指定'), tr('--request ファイル（Web API と同じ形。--format json の出力の "request" をそのまま使えます）')),
    ]
    w = max(_width(k) for k, _ in kinds) + 2
    lines = [tr('観測者は次のどれか 1 つで指定します:')]
    for k, v in kinds:
        first, *rest = v.split('\n')
        lines.append(f'  {k}{" " * (w - _width(k))}{first}')
        lines += [' ' * (w + 2) + s for s in rest]
    return '\n'.join(lines + [
        '', tr('例:'),
        '  python cli.py --epoch 2027-07-30T12:00:00 --a 7058.1 --e 0.0012 --i 98.13 --raan 220.5 \\',
        '      --argp 90 --m 45 --start 2027-07-25 --end 2027-08-10 --tz 9',
        '  python cli.py --epoch 2027-01-01T00:00 --alt 680 --sso --ltan 18:00 --sweep 10 \\',
        '      --start 2027-07-01 --end 2027-09-01',
        '  python cli.py --norad 25544 --start 2026-10-01 --end 2027-10-01 --tz 9',
        f'  python cli.py --city {tr("東京")} --start 2026-01-01 --end 2056-01-01 --phenomena moon --tz 9 --detail',
        '  python cli.py --global --start 2026-01-01 --end 2036-01-01 --format csv -o list.csv',
        '',
        tr('生成 AI から使うときは --format json を付けてください（詳しくは docs/cli.md）。'),
        tr('終了コード: 0 = 計算した, 1 = 入力を計算できなかった, 2 = 引数の誤り'),
    ]) + '\n'


def build_parser():
    ap = _Parser(
        prog='cli.py', allow_abbrev=False, add_help=False, epilog=_epilog(), formatter_class=_Formatter,
        description=tr('人工衛星・探査機・地上の地点・地球全体から見える日食と水星・金星の太陽面通過を計算します（画面と同じ計算）。'))
    ap._optionals.title = tr('全般')
    ap.add_argument('-h', '--help', action='help', help=tr('この説明を表示して終わる'))
    g = ap.add_argument_group(tr('観測者: 軌道要素で指定した衛星（GCRS・J2000 赤道基準の平均要素。J2 永年摂動つき）'))
    g.add_argument('--epoch', metavar='UTC', help=tr('元期（軌道情報の日時, UTC）。例 2027-07-30T12:00:00'))
    g.add_argument('--a', type=float, metavar='KM', help=tr('軌道長半径 (km)。--e と組で指定'))
    g.add_argument('--e', type=float, metavar='E', help=tr('軌道離心率（0 以上 1 未満）'))
    g.add_argument('--alt', type=float, metavar='KM', help=tr('円軌道の高度 (km)（--a --e の代わり）'))
    g.add_argument('--perigee-alt', type=float, metavar='KM',
                   help=tr('近地点高度 (km)。--apogee-alt と組で（--a --e の代わり）'))
    g.add_argument('--apogee-alt', type=float, metavar='KM', help=tr('遠地点高度 (km)'))
    g.add_argument('--i', type=float, metavar='DEG', help=tr('軌道傾斜角 (°)'))
    g.add_argument('--sso', action='store_true',
                   help=tr('太陽同期軌道（傾斜角を軌道の大きさから自動で決める。--i の代わり）'))
    g.add_argument('--raan', type=float, metavar='DEG', help=tr('昇交点赤経 (°)'))
    g.add_argument('--ltan', metavar='HH:MM', help=tr('昇交点の地方時（平均太陽時。例 18:00 や 10.5。--raan の代わり）'))
    g.add_argument('--argp', type=float, metavar='DEG', help=tr('近地点引数 (°)（既定 0）'))
    g.add_argument('--m', type=float, metavar='DEG', help=tr('平均近点角 (°)（既定 0）'))
    g.add_argument('--no-j2', action='store_true', help=tr('地球の扁平 (J2) による軌道面の歳差を考慮しない'))

    t = ap.add_argument_group(tr('観測者: TLE（SGP4 で軌道を計算）'))
    t.add_argument('--tle', metavar='FILE', help=tr('TLE（2 行または名前付き 3 行）のファイル。- で標準入力から読む'))
    t.add_argument('--norad', type=int, metavar='N',
                   help=tr('NORAD カタログ番号。CelesTrak から最新の TLE を取得する（要インターネット）'))

    o = ap.add_argument_group(tr('観測者: そのほかの人工衛星・探査機（--horizons と --sscweb は要インターネット）'))
    o.add_argument('--geo-lon', type=float, metavar='DEG',
                   help=tr('理想静止衛星（高度 35,786 km）の経度 (°)。東経 +／西経 −'))
    o.add_argument('--horizons', metavar='ID', help=tr('JPL Horizons の天体 ID（例 -170 JWST、-21 SOHO、-125544 ISS）'))
    o.add_argument('--horizons-step', type=int, metavar='MIN',
                   help=tr('Horizons から取得する位置の刻み (分)（既定 60。地球近傍の衛星は 1〜2）'))
    o.add_argument('--sscweb', metavar='ID',
                   help=tr('NASA SSCWeb の衛星 ID（例 hinode, iris, iss）。過去の軌道。--list-sscweb で一覧'))

    gr = ap.add_argument_group(tr('観測者: 地上の地点（WGS84）・地球全体'))
    gr.add_argument('--lat', type=float, metavar='DEG', help=tr('緯度 (°)。北緯 +／南緯 −'))
    gr.add_argument('--lon', type=float, metavar='DEG', help=tr('経度 (°)。東経 +／西経 −'))
    gr.add_argument('--elev', type=float, metavar='M', help=tr('標高 (m)（既定 0）'))
    gr.add_argument('--city', metavar='NAME',
                    help=tr('よく使う地点の名前（例 東京、ルクソール。どの言語の名前でも可）。--list-cities で一覧'))
    gr.add_argument('--global', dest='global_', action='store_true',
                    help=tr('地球全体: 世界のどこかで見える日食（最大食の地点・γ・中心食帯の幅など）と地心での太陽面通過'))

    rq = ap.add_argument_group(tr('JSON での指定'))
    rq.add_argument('--request', metavar='FILE',
                    help=tr('計算条件の JSON（- で標準入力）。{"observer": {...}, "start", "end", "phenomena", '
                            '"settings", "step_deg"}。ほかの観測者・期間・詳細設定の引数とは併用できません'))

    ap.add_argument('--name', help=tr('観測者の名前（表示用）'))

    p = ap.add_argument_group(tr('期間と現象'))
    p.add_argument('--start', metavar='DATE', help=tr('開始日（UTC）。例 2027-07-25'))
    p.add_argument('--end', metavar='DATE', help=tr('終了日（UTC）。例 2027-08-10'))
    p.add_argument('--phenomena', metavar='LIST',
                   help=tr('計算する現象をカンマ区切りで: moon（日食）, mercury, venus（既定 すべて）'))
    p.add_argument('--sweep', type=float, metavar='STEP',
                   help=tr('軌道要素・TLE の衛星のみ: 平均近点角（軌道上の位置）をこの刻み (°, 5〜90) で変えて一括計算する'
                           '（打ち上げ前や、TLE の元期から何か月も先の検討。期間は 1 年以内）'))

    s = ap.add_argument_group(tr('詳細設定'))
    s.add_argument('--ephemeris', metavar='FILE',
                   help=tr('暦（既定 de440s.bsp = 1849〜2150 年。de440.bsp なら 1550〜2650 年）'))
    s.add_argument('--delta-t', type=float, metavar='SEC', help=tr('ΔT（TT−UT, 秒）を固定値で指定（既定は自動）'))
    s.add_argument('--sun-radius', metavar='KM|iau2015|nasa',
                   help=tr('太陽の半径。iau2015（既定 695,700 km）、nasa（959.63″ = 695,992 km）または km'))
    s.add_argument('--atm', type=float, metavar='KM', help=tr('人工衛星: 地球大気の遮蔽高度 (km)（既定 0）'))
    s.add_argument('--min-sun-alt', type=float, metavar='DEG',
                   help=tr('地上: 太陽中心がこの高度以上で「見える」とする (°)（既定 0）'))
    s.add_argument('--no-refraction', action='store_true', help=tr('地上: 大気差を太陽高度に反映しない'))
    s.add_argument('--include-invisible', action='store_true', help=tr('見えない現象（夜間・地球の陰）も含める'))

    out = ap.add_argument_group(tr('出力'))
    out.add_argument('--format', choices=('table', 'csv', 'json'), default='table',
                     help=tr('出力形式（既定 table。生成 AI からは json）'))
    out.add_argument('--detail', action='store_true',
                     help=tr('各現象の接触（第1〜第4接触・最大）の時刻・太陽高度・位置角なども出力する'))
    out.add_argument('--dry-run', action='store_true',
                     help=tr('計算せず、入力の解釈（衛星の軌道の大きさ・周期・傾斜角など）だけを出力する'))
    out.add_argument('--tz', default='0', metavar='HOURS',
                     help=tr('表で使う時刻の UTC からのずれ。例 9 や +09:00 で日本時間（既定 UTC。CSV・JSON は常に UTC）'))
    out.add_argument('--lang', choices=i18n.LANGUAGES, metavar='LANG',
                     help=tr('表示の言語: {langs}（既定は環境変数 {env}、なければ環境の言語、なければ英語）',
                             langs=', '.join(i18n.LANGUAGES), env=LANG_ENV))
    out.add_argument('-o', '--output', metavar='FILE', help=tr('結果をファイルに保存する（既定は標準出力）'))
    out.add_argument('-q', '--quiet', action='store_true', help=tr('経過の表示（標準エラー出力）を出さない'))
    out.add_argument('--list-cities', action='store_true', help=tr('--city で使える地点の一覧を表示して終わる'))
    out.add_argument('--list-sscweb', action='store_true',
                     help=tr('NASA SSCWeb の衛星 ID の一覧を表示して終わる（要インターネット）'))
    return ap


def _given(args, k):
    v = getattr(args, k)
    return v is not None and v is not False      # 0 is a value (e.g. --raan 0)


def _modes(args):
    modes = []
    if any(_given(args, k) for k in ORBIT_ARGS):
        modes.append('kepler')
    if _given(args, 'tle') or _given(args, 'norad'):
        modes.append('tle')
    if _given(args, 'geo_lon'):
        modes.append('geo')
    if _given(args, 'horizons'):
        modes.append('horizons')
    if _given(args, 'sscweb'):
        modes.append('sscweb')
    if any(_given(args, k) for k in ('lat', 'lon', 'city')):
        modes.append('ground')
    if args.global_:
        modes.append('global')
    if _given(args, 'request'):
        modes.append('request')
    return modes


def parse_args(argv):
    argv = list(argv)
    for k in range(len(argv) - 2, -1, -1):      # argparse takes "--tz -05:00" for two options
        if argv[k] == '--tz' and re.fullmatch(r'-\d{1,2}:\d{2}', argv[k + 1]):
            argv[k:k + 2] = ['--tz=' + argv[k + 1]]
    args = build_parser().parse_args(argv)
    bad = next((k for k, v in vars(args).items() if isinstance(v, float) and not math.isfinite(v)), None)
    if bad:     # float() takes nan and inf, which would give empty results or misleading errors
        raise UsageError(tr('{arg} の値 {value} は数値ではありません',
                            arg='--' + bad.replace('_', '-'), value=repr(str(getattr(args, bad)))))
    if args.list_cities or args.list_sscweb:
        return args
    args.tz_hours = _parse_tz(args.tz)
    modes = _modes(args)
    if len(modes) != 1:
        raise UsageError(tr('観測者を 1 つだけ指定してください: 軌道要素（--epoch …）、TLE（--tle / --norad）、'
                            '静止衛星（--geo-lon）、JPL Horizons（--horizons）、NASA SSCWeb（--sscweb）、'
                            '地上の地点（--lat --lon / --city）、地球全体（--global）、JSON（--request）')
                         + (tr('（指定されたもの: {modes}）', modes=', '.join(modes)) if modes else ''))
    args.mode = modes[0]
    if args.mode == 'request':
        clash = [k for k in ORBIT_ONLY + ('elev', 'horizons_step', 'name', 'start', 'end', 'phenomena', 'sweep',
                                          'ephemeris', 'delta_t', 'sun_radius', 'atm', 'min_sun_alt',
                                          'no_refraction', 'include_invisible') if _given(args, k)]
        if clash:
            raise UsageError(tr('--request と {opts} は併用できません（JSON の中で指定してください）',
                                opts=' '.join('--' + k.replace('_', '-') for k in clash)))
        return args
    if args.mode == 'kepler':
        _check_orbit(args)
    else:
        extra = [f'--{k.replace("_", "-")}' for k in ORBIT_ONLY if _given(args, k)]
        if extra:
            raise UsageError(tr('{opts} は軌道要素で指定した衛星だけで使えます', opts=' '.join(extra)))
    if _given(args, 'elev') and args.mode != 'ground':
        raise UsageError(tr('--elev は地上の地点（--lat --lon / --city）だけで使えます'))
    if _given(args, 'horizons_step') and args.mode != 'horizons':
        raise UsageError(tr('--horizons-step は JPL Horizons（--horizons）だけで使えます'))
    if _given(args, 'sweep') and args.mode not in ('kepler', 'tle'):
        raise UsageError(tr('--sweep は軌道要素または TLE で指定した衛星だけで使えます'))
    if args.mode == 'tle' and _given(args, 'tle') and _given(args, 'norad'):
        raise UsageError(tr('--tle と --norad はどちらか一方だけ指定してください'))
    if args.mode == 'ground':
        if _given(args, 'city') and (_given(args, 'lat') or _given(args, 'lon')):
            raise UsageError(tr('--city と --lat/--lon はどちらか一方だけ指定してください'))
        if not _given(args, 'city') and not (_given(args, 'lat') and _given(args, 'lon')):
            raise UsageError(tr('地上の地点では --lat と --lon の両方を指定してください'))
    if args.start is None or args.end is None:
        raise UsageError(tr('期間を --start と --end で指定してください（例 --start 2027-07-25 --end 2027-08-10）'))
    if args.sweep is not None and not 5 <= args.sweep <= 90:
        raise UsageError(tr('--sweep（位相の刻み）は 5〜90° で指定してください'))
    if args.sweep is not None and args.detail:
        raise UsageError(tr('--detail は --sweep と同時には使えません'))
    return args


def _check_orbit(args):
    if args.epoch is None:
        raise UsageError(tr('軌道要素では元期 --epoch（軌道情報の日時, UTC）を指定してください'))
    sizes = [n for n, ok in (('--a --e', _given(args, 'a') or _given(args, 'e')), ('--alt', _given(args, 'alt')),
                             ('--perigee-alt --apogee-alt',
                              _given(args, 'perigee_alt') or _given(args, 'apogee_alt'))) if ok]
    if len(sizes) != 1:
        raise UsageError(tr('軌道の大きさは --a と --e、--alt、--perigee-alt と --apogee-alt のどれか 1 つで指定してください'))
    if sizes[0] == '--a --e':
        if args.a is None or args.e is None:
            raise UsageError(tr('軌道長半径 --a と軌道離心率 --e は両方指定してください（円軌道なら --e 0）'))
        if not args.a > 0:
            raise UsageError(tr('--a（軌道長半径）は正の値で指定してください'))
        if not 0 <= args.e < 1:
            raise UsageError(tr('--e（軌道離心率）は 0 以上 1 未満で指定してください'))
    if sizes[0] == '--perigee-alt --apogee-alt' and (args.perigee_alt is None or args.apogee_alt is None):
        raise UsageError(tr('--perigee-alt と --apogee-alt は両方指定してください'))
    if _given(args, 'i') == args.sso:
        raise UsageError(tr('軌道傾斜角は --i または --sso（太陽同期軌道）のどちらか一方で指定してください'))
    if _given(args, 'raan') == _given(args, 'ltan'):
        raise UsageError(tr('軌道面は --raan（昇交点赤経）または --ltan（昇交点の地方時）のどちらか一方で指定してください'))
    if _given(args, 'ltan'):
        args.ltan_h = _parse_hours(args.ltan, tr('--ltan（昇交点の地方時）は 18:00 や 10.5 のように指定してください'))


def _parse_hours(text, message):
    m = re.fullmatch(r'\s*([+-]?)(\d{1,2}):(\d{2})\s*', text)
    if m:
        if int(m.group(3)) >= 60:
            raise UsageError(message)
        h = int(m.group(2)) + int(m.group(3)) / 60
        return -h if m.group(1) == '-' else h
    try:
        h = float(text)
    except ValueError:
        raise UsageError(message)
    if not math.isfinite(h):
        raise UsageError(message)
    return h


def _parse_tz(text):
    h = _parse_hours(text, tr('--tz は 9、+09:00、-5 のように UTC からのずれ（時間）で指定してください'))
    if not -14 <= h <= 14:
        raise UsageError(tr('--tz は -14〜+14 時間で指定してください'))
    return h


# ---------------------------------------------------------------------------
# request
# ---------------------------------------------------------------------------
def _city_names(c):
    """The name of a preset city in every language (Japanese first)."""
    return [c['name']] + list(i18n.MESSAGES.get(c['name'], {}).values())


def _find_city(name):
    from eclipsecalc.presets import cities
    cs = cities()
    key = name.strip().casefold()
    hit = ([c for c in cs if key in (n.casefold() for n in _city_names(c))]
           or [c for c in cs if key and any(n.casefold().startswith(key) for n in _city_names(c))])
    if len(hit) != 1:
        raise InputError(tr('地点「{name}」が一つに決まりません。--list-cities で使える地点を確認するか、--lat --lon で指定してください'
                            if hit else
                            '地点「{name}」が見つかりません。--list-cities で使える地点を確認するか、--lat --lon で指定してください',
                            name=name))
    return hit[0]


def _read_text(path):
    if path == '-':
        return sys.stdin.read().lstrip('﻿')
    with open(path, encoding='utf-8-sig', errors='replace') as f:
        return f.read()


def _parse_tle(text):
    """TLE text (two lines, optionally preceded by a name) -> (line1, line2, name)."""
    lines = [s.strip() for s in text.lstrip('﻿').splitlines() if s.strip()]
    i1 = next((k for k, s in enumerate(lines) if s.startswith('1 ')), -1)
    if i1 < 0 or i1 + 1 >= len(lines) or not lines[i1 + 1].startswith('2 '):
        raise InputError(tr('TLE の 1 行目（"1 "で始まる）と 2 行目（"2 "で始まる）が見つかりません'))
    return lines[i1], lines[i1 + 1], lines[i1 - 1] if i1 > 0 else ''


def _celestrak(norad, name, log):
    from eclipsecalc.observers import ObserverError, fetch_tle_celestrak
    log(tr('CelesTrak から NORAD {norad} の最新 TLE を取得しています…', norad=norad))
    try:
        nm, l1, l2 = fetch_tle_celestrak(norad)
    except ObserverError as exc:        # e.g. no such (or a decayed) catalogue number
        raise InputError(str(exc))
    except Exception as exc:
        raise InputError(tr('CelesTrak から NORAD {norad} の TLE を取得できませんでした: {exc}', norad=norad, exc=exc))
    return {'type': 'tle', 'line1': l1, 'line2': l2, 'name': name or nm, 'norad': int(norad)}


def observer_from_args(args, log):
    """The observer as the web API's JSON spec."""
    name = args.name or ''
    if args.mode == 'kepler':
        spec = {'type': 'kepler', 'name': name, 'epoch': args.epoch,
                'argp_deg': args.argp or 0.0, 'm_deg': args.m or 0.0, 'j2': not args.no_j2}
        if args.a is not None:
            spec.update(a_km=args.a, e=args.e)
        elif args.alt is not None:
            spec.update(perigee_alt_km=args.alt, apogee_alt_km=args.alt)
        else:
            spec.update(perigee_alt_km=args.perigee_alt, apogee_alt_km=args.apogee_alt)
        spec.update(sso=True) if args.sso else spec.update(i_deg=args.i)
        spec.update(ltan_h=args.ltan_h) if _given(args, 'ltan') else spec.update(raan_deg=args.raan)
        return spec
    if args.mode == 'tle':
        if args.norad is not None:
            return _celestrak(args.norad, name, log)
        try:
            text = _read_text(args.tle)
        except OSError as exc:
            raise InputError(tr('TLE のファイルを読めません: {exc}', exc=exc))
        l1, l2, nm = _parse_tle(text)
        return {'type': 'tle', 'line1': l1, 'line2': l2, 'name': name or nm}
    if args.mode == 'geo':
        return {'type': 'geo', 'lon': args.geo_lon, 'name': name}
    if args.mode == 'horizons':
        return {'type': 'horizons', 'command': args.horizons,
                'step_min': 60 if args.horizons_step is None else args.horizons_step, 'name': name}
    if args.mode == 'sscweb':
        return {'type': 'sscweb', 'id': args.sscweb, 'name': name}
    if args.mode == 'global':
        return {'type': 'global'}
    if args.city is not None:
        c = _find_city(args.city)
        return {'type': 'ground', 'lat': c['lat'], 'lon': c['lon'],
                'elevation_m': c['elevation_m'] if args.elev is None else args.elev, 'name': name or tr(c['name'])}
    return {'type': 'ground', 'lat': args.lat, 'lon': args.lon, 'elevation_m': args.elev or 0.0, 'name': name}


def _settings_from_args(args):
    from eclipsecalc.constants import SUN_RADIUS_IAU2015, SUN_RADIUS_NASA
    s = {}
    if args.ephemeris:
        s['ephemeris'] = args.ephemeris
    if args.delta_t is not None:
        s['delta_t'] = args.delta_t
    if args.sun_radius:
        presets = {'iau2015': SUN_RADIUS_IAU2015, 'nasa': SUN_RADIUS_NASA}
        try:
            r = presets.get(args.sun_radius.lower()) or float(args.sun_radius)
        except ValueError:
            r = None
        if r is None or not 0 < r < math.inf:
            raise InputError(tr('--sun-radius は iau2015、nasa または km の数値で指定してください'))
        s['sun_radius_km'] = r
    if args.atm is not None:
        s['earth_atm_km'] = args.atm
    if args.min_sun_alt is not None:
        s['min_sun_alt_deg'] = args.min_sun_alt
    if args.no_refraction:
        s['refraction'] = False
    if args.include_invisible:
        s['include_invisible'] = True
    return s


def _not_json(name):
    raise ValueError(f'{name} is not a JSON value')


def build_request(args, log):
    """-> the request in the web API's shape (also echoed in the JSON output)."""
    if args.mode == 'request':
        try:
            req = json.loads(_read_text(args.request), parse_constant=_not_json)
        except OSError as exc:
            raise InputError(tr('計算条件の JSON のファイルを読めません: {exc}', exc=exc))
        except ValueError as exc:       # also NaN and Infinity (json.loads takes them, JSON does not)
            raise InputError(tr('計算条件の JSON を読めません: {exc}', exc=exc))
        if isinstance(req, dict) and isinstance(req.get('request'), dict):
            req = req['request']        # the whole output of --format json (or a result saved by the web UI)
        if not isinstance(req, dict) or not isinstance(req.get('observer'), dict):
            raise InputError(tr('計算条件の JSON には "observer"（観測者のオブジェクト）が必要です'))
        unknown = set(req) - {'observer', 'start', 'end', 'phenomena', 'settings', 'step_deg'}
        if unknown:
            raise InputError(tr('計算条件の JSON に不明な項目があります: {keys}', keys=', '.join(sorted(unknown))))
        for k in ('start', 'end'):
            if not isinstance(req.get(k), str):
                raise InputError(tr('計算条件の JSON に "{key}"（UTC の日付の文字列）が必要です', key=k))
        req = dict(req)
        req.setdefault('phenomena', list(PHENOMENA))
        req.setdefault('settings', {})
        if not isinstance(req['settings'], dict):
            raise InputError(tr('計算条件の JSON の "settings" はオブジェクトで指定してください'))
        step = req.get('step_deg')
        if step is not None and (isinstance(step, bool) or not isinstance(step, (int, float)) or not 5 <= step <= 90):
            raise InputError(tr('位相の刻みは 5〜90° で指定してください'))
        if req['observer'].get('type') == 'celestrak':
            o = req['observer']
            req['observer'] = _celestrak(o.get('norad'), o.get('name', ''), log)
    else:
        req = {'observer': observer_from_args(args, log), 'start': args.start, 'end': args.end,
               'phenomena': args.phenomena or ','.join(PHENOMENA), 'settings': _settings_from_args(args)}
        if args.sweep is not None:
            req['step_deg'] = args.sweep
    ph = req['phenomena']
    if isinstance(ph, str):
        ph = [x.strip() for x in ph.split(',') if x.strip()]
    bad = [x for x in ph if x not in PHENOMENA] if isinstance(ph, list) else ['?']
    if bad or not ph:
        raise InputError(tr('現象は moon, mercury, venus から選んでください（指定: {given}）',
                            given=', '.join(map(str, bad)) or tr('なし')))
    req['phenomena'] = list(dict.fromkeys(ph))      # each once (moon,moon would list every eclipse twice)
    if req.get('step_deg') is not None and req['observer'].get('type') not in ('kepler', 'tle'):
        raise InputError(tr('平均近点角を変えた一括計算（step_deg）は軌道要素または TLE で指定した衛星だけで使えます'))
    return req


# ---------------------------------------------------------------------------
# calculation
# ---------------------------------------------------------------------------
def _context(req):
    from eclipsecalc.context import DEFAULT_EPHEMERIS, ensure_ephemeris, get_context
    s = req['settings']
    eph = s.get('ephemeris') or DEFAULT_EPHEMERIS
    if not eph.endswith('.bsp'):
        eph += '.bsp'
    s['ephemeris'] = eph
    try:
        ensure_ephemeris(eph, log=lambda m: print(m, file=sys.stderr, flush=True))
    except Exception as exc:
        raise InputError(tr('JPL 暦 {name} を用意できませんでした: {exc}', name=eph, exc=exc))
    dt = s.get('delta_t')
    try:
        return get_context(eph, None if dt in (None, '', 'auto') else float(dt))
    except (TypeError, ValueError) as exc:
        raise InputError(tr('入力値を解釈できません: {exc}', exc=exc))


_DATE_RE = re.compile(r'(-?\d{1,4})-(\d{1,2})-(\d{1,2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d*)?))?)?Z?')
_DATE_ERRORS = {
    'start': '開始日「{text}」を解釈できません（UTC。例 2027-07-30 または 2027-07-30T12:00:00）',
    'end': '終了日「{text}」を解釈できません（UTC。例 2027-07-30 または 2027-07-30T12:00:00）',
    'epoch': '元期「{text}」を解釈できません（UTC。例 2027-07-30 または 2027-07-30T12:00:00）',
}


def check_utc(text, which):
    """Reject dates the calculation would silently roll over (e.g. month 13).
    ``which`` is 'start', 'end' or 'epoch'."""
    m = _DATE_RE.fullmatch(str(text).strip())
    ok = bool(m)
    if ok:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        hh, mi, ss = int(m.group(4) or 0), int(m.group(5) or 0), float(m.group(6) or 0)
        ok = 1 <= mo <= 12 and hh < 24 and mi < 60 and ss < 61
        if ok:
            leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
            ok = 1 <= d <= [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mo - 1]
    if not ok:
        raise InputError(tr(_DATE_ERRORS[which], text=text))


def check_observer(ctx, spec):
    """Build the observer once to report input errors cleanly -> (description, observer)."""
    from eclipsecalc.observers import ObserverError, build_observer
    from eclipsecalc.timeutil import iso_from_jd, parse_utc
    if spec.get('type') == 'global':
        return {'kind': 'global', 'name': tr('地球全体')}, None
    if spec.get('type') == 'kepler':
        check_utc(spec.get('epoch', ''), 'epoch')
    try:
        obs = build_observer(ctx, spec, parse_time=lambda s: parse_utc(ctx, s))
    except ObserverError as exc:
        raise InputError(str(exc))
    except KeyError as exc:
        raise InputError(tr('観測者（type: {type}）の指定に {key} がありません', type=spec.get('type'), key=exc))
    except Exception as exc:
        raise InputError(tr('観測者（type: {type}）の指定を解釈できません: {exc}', type=spec.get('type'), exc=exc))
    d = obs.describe()
    if d.get('epoch_jd_tt'):
        d['epoch'] = iso_from_jd(ctx, d['epoch_jd_tt'])
    return d, obs


@contextlib.contextmanager
def _api_errors():
    """Errors of the web API's request checks as input errors."""
    from fastapi import HTTPException
    from pydantic import ValidationError
    try:
        yield
    except HTTPException as exc:
        raise InputError(str(exc.detail))
    except ValidationError as exc:
        raise InputError(tr('入力値を解釈できません: {exc}', exc=exc))


def preflight(req, obs):
    """For --dry-run: the checks the calculation would make on the period, and
    the warnings it would give (an old TLE is judged at the end of the period
    farthest from its epoch) -> list of warnings."""
    from eclipsecalc import server
    from eclipsecalc.local import tle_decay
    from eclipsecalc.observers import ObserverError, TLEObserver
    kw = {k: req[k] for k in ('phenomena', 'observer', 'start', 'end', 'settings')}
    with _api_errors():
        ctx, _, jd_a, jd_b, _ = server._parse_request(server.SearchRequest(**kw))
    if obs is not None and obs.kind == 'space' and (jd_b - jd_a) / 365.25 > 20:
        raise InputError(tr('人工衛星の観測者では期間を 20 年以内にしてください'))
    if req.get('step_deg') is not None and jd_b - jd_a > server.SWEEP_MAX_DAYS:
        raise InputError(tr('位相を変えた一括計算では期間を 1 年以内にしてください'))
    warnings = []
    if isinstance(obs, TLEObserver):        # the re-entry that SGP4 predicts
        try:
            _, warnings = tle_decay(ctx, obs, jd_a, jd_b)
        except ObserverError as exc:
            raise InputError(str(exc))
    epoch = getattr(obs, 'epoch_jd', None)
    if epoch is None:
        return warnings
    return warnings + obs.warnings_for(ctx, jd_a if abs(jd_a - epoch) > abs(jd_b - epoch) else jd_b)


def calculate(req, debug=False):
    from eclipsecalc import server
    kw = {k: req[k] for k in ('phenomena', 'observer', 'start', 'end', 'settings')}
    err = io.StringIO()
    with _api_errors():
        # the web server prints tracebacks of unexpected errors; keep them out of the CLI's output
        with contextlib.redirect_stderr(err if not debug else sys.stderr):
            if req.get('step_deg') is not None:
                return server.phase_sweep(server.SweepRequest(**kw, step_deg=req['step_deg']))
            return server.search(server.SearchRequest(**kw))


def attach_details(res):
    from eclipsecalc import server
    for e in res['events']:
        if e.get('kind') == 'global':
            continue
        d = server.event_detail(e['id'])
        e['contacts'] = d['contacts']
        e['internal'] = d['internal']


def _strip_ids(v):
    """Without the ids of the events (they refer to the memory of this process).
    The observer keeps its own "id" (the satellite of NASA SSCWeb)."""
    if isinstance(v, dict):
        return {k: x if k in ('observer', 'request') else _strip_ids(x) for k, x in v.items() if k != 'id'}
    if isinstance(v, list):
        return [_strip_ids(x) for x in v]
    return v


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def _table(head, rows, right=()):
    widths = [max(_width(str(r[k])) for r in [head] + rows) for k in range(len(head))]

    def line(r):
        cells = []
        for k, v in enumerate(r):
            pad = ' ' * (widths[k] - _width(str(v)))
            cells.append(pad + str(v) if k in right else str(v) + pad)
        return '  '.join(cells).rstrip()
    return [line(head), '  '.join('-' * w for w in widths)] + [line(r) for r in rows]


def _time(iso, tz, fmt='%H:%M:%S'):
    if not iso:
        return '—'
    t = _dt.datetime.fromisoformat(iso.replace('Z', '+00:00')) + _dt.timedelta(hours=tz)
    return t.strftime(fmt)


def _time_on(iso, day, tz, fmt='%H:%M:%S'):
    """``_time`` with the month and day in front when ``iso`` falls on another day than ``day``."""
    if iso and day and _time(iso, tz, '%Y%m%d') != _time(day, tz, '%Y%m%d'):
        fmt = '%m-%d ' + fmt
    return _time(iso, tz, fmt)


def _tz_label(tz):
    if tz == 0:
        return 'UTC'
    h, m = divmod(round(abs(tz) * 60), 60)
    return f'UTC{"+" if tz > 0 else "-"}{h}' + (f':{m:02d}' if m else '')


def _dur(s, precise=False):
    if s is None:
        return '—'
    r = round(s, 1) if precise else round(s)       # round first: 59.6 s is "1 min 00 s", not "60 s"
    if r < 60:
        return tr('{s}秒', s=f'{r:.1f}' if precise else f'{r:.0f}')
    if r < 3600:
        m = int(r // 60)
        return tr('{m}分{s}秒', m=m, s=f'{r - 60 * m:04.1f}' if precise else f'{round(r - 60 * m):02d}')
    h, m = divmod(round(s / 60), 60)
    return tr('{h}時間{m}分', h=h, m=f'{m:02d}')


def _latlon(lat, lon, digits=2):
    if lat is None:
        return '—'
    lat, lon = round(lat, digits) + 0.0, round(lon, digits) + 0.0     # no "南緯 0.00°" for -0.0001
    return (tr('北緯 {v}°' if lat >= 0 else '南緯 {v}°', v=f'{abs(lat):.{digits}f}') + _sep()
            + tr('東経 {v}°' if lon >= 0 else '西経 {v}°', v=f'{abs(lon):.{digits}f}'))


def _az(az):
    return i18n.COMPASS[i18n.current()][round((az % 360) / 22.5) % 16]


def _f(v, fmt, unit=''):
    return '—' if v is None else f'{v:{fmt}}{unit}'


def _vis(e, ground):
    f = e.get('vis_fraction') or 0
    if f >= 0.999:
        return tr('全経過')
    if f > 0:
        part = tr('一部 {p}%', p=round(f * 100))
        if not ground:
            return part + tr('（地球に隠される）')
        iv = e.get('visible_intervals') or []
        if len(iv) > 1:
            return part
        return part + (tr('（日の入り帯食）') if iv and iv[0][0] == e.get('c1') else tr('（日の出帯食）'))
    return tr('地平線の下') if ground else tr('地球に隠される')


def _category(e, grazing=False):
    """「皆既日食」, 「水星の太陽面通過」 ... (with 「（外接のみ）」 for a grazing transit if ``grazing``)."""
    if e['body'] == 'moon':
        return tr(TYPE_JA.get(e['type'], e['type']))
    return tr(BODY_JA[e['body']]) + (tr('（外接のみ）') if grazing and e.get('type') == 'transit_grazing' else '')


def observer_text(o):
    kind, model = o.get('kind'), o.get('model')
    if kind == 'global':
        return tr('地球全体')
    if kind == 'geocenter':     # {"type": "geocenter"} of the web API
        return o['name']
    if kind == 'ground':
        return o['name'] + _paren(_latlon(o['lat'], o['lon'], 4) + _sep()
                                  + tr('標高 {v} m', v=round(o['elevation_m'])))
    if model == 'fixed':
        items = [tr('地球固定位置'), _latlon(o['lat'], o['lon']), tr('高度 {v} km', v=f'{o["height_km"]:,.0f}')]
    elif model == 'horizons':
        items = [f'JPL Horizons {o["command"]}', tr('位置の刻み {v} 分', v=o['step_min'])]
    else:
        items = [{'tle': 'TLE/SGP4', 'kepler': tr('軌道要素'), 'sscweb': 'NASA SSCWeb'}.get(model, model)]
        if o.get('perigee_km') is not None:
            items += [tr('高度 {lo}〜{hi} km', lo=f'{o["perigee_km"]:,.0f}', hi=f'{o["apogee_km"]:,.0f}'),
                      tr('周期 {p} 分', p=f'{o["period_min"]:.1f}')]
        if model == 'tle' and o.get('i_deg') is not None:
            items += [tr('傾斜角 {v}°', v=f'{o["i_deg"]:.2f}'), tr('離心率 {v}', v=f'{o["e"]:.5f}')]
        if model == 'kepler':
            items += [tr('傾斜角 {v}°', v=f'{o["i_deg"]:.2f}'), tr('昇交点赤経 {v}°', v=f'{o["raan_deg"]:.2f}')]
            if o.get('ltan_h') is not None:
                t = round(o['ltan_h'] * 60) % 1440
                items.append(tr('昇交点の地方時 {v}', v=f'{t // 60:02d}:{t % 60:02d}'))
            if o.get('sso'):
                items.append(tr('太陽同期'))
    if o.get('epoch'):
        items.append(tr('元期 {v}', v=_time(o['epoch'], 0, '%Y-%m-%d %H:%M:%S') + ' UTC'))
    return o['name'] + _paren(_sep().join(items))


def _header(res, args, extra=()):
    period = [tr('期間: {start} 〜 {end}（UTC）', start=res['start'], end=res['end']),
              tr('暦 {name}', name=res['ephemeris'])]
    if res.get('delta_t_override') is not None:
        period.append(tr('ΔT {v} 秒（手動）', v=f'{res["delta_t_override"]:.1f}'))
    elif res.get('delta_t_mid_s') is not None:
        period.append(tr('ΔT 約 {v} 秒（期間中央）', v=f'{res["delta_t_mid_s"]:.1f}'))
    period += [tr('計算 {v} 秒', v=f'{res["elapsed_s"]:.1f}'), *extra]
    out = [tr('観測者: {obs}', obs=observer_text(res['observer'])), _sep().join(period),
           tr('時刻: {tz}', tz=_tz_label(args.tz_hours))]
    return out + [tr('注意: {w}', w=w) for w in res.get('warnings') or []]


def _contacts_table(e, tz, ground):
    head = [tr('接触'), tr('時刻'), tr('食分'), tr('中心間'), tr('位置角 P')]
    head += ([tr('天頂角 V'), tr('太陽高度'), tr('太陽方位'), tr('見えるか')] if ground
             else [tr('衛星直下点'), tr('衛星高度'), tr('見えるか')])
    rows = []
    for c in e.get('contacts') or []:
        row = [tr(CONTACT_JA[c['label']]) if c['label'] in CONTACT_JA else c['label'],
               _time_on(c['time'], e['max'], tz, '%H:%M:%S.%f')[:-3],
               _f(c.get('magnitude'), '.4f'), _f(c.get('sep_arcsec'), '.1f', '"'), _f(c.get('pa'), '.1f', '°')]
        if ground:
            row += [_f(c.get('v_angle'), '.1f', '°'), _f(c.get('sun_alt'), '.2f', '°'),
                    _f(c.get('sun_az'), '.1f', '°') + (f' {_az(c["sun_az"])}' if c.get('sun_az') is not None else '')]
        else:
            row += [_latlon(c.get('sat_lat'), c.get('sat_lon')), _f(c.get('sat_alt_km'), ',.0f', ' km')]
        rows.append(row + ['○' if c.get('visible') else '×'])
    return _table(head, rows, right={2, 3, 4, 5, 6})


def search_table(res, args):
    evs = res['events']
    kind = res['observer']['kind']
    lines = _header(res, args) + ['', tr('{n} 件の現象が見つかりました', n=len(evs)) if evs
                                  else tr('この期間に見られる現象はありません')]
    if not evs:
        return lines
    tz = args.tz_hours
    if kind == 'global':
        head = [tr(k) for k in ('日付', '種類', '始まり', '最大', '終わり', '食分/中心間', 'γ', '中心食/経過時間',
                                '中心食帯の幅', '最大食の地点', 'サロス')]
        rows = []
        for e in evs:
            if e.get('kind') == 'global':
                rows.append([_time(e['max'], tz, '%Y-%m-%d'), _category(e) + (tr('（非中心）') if e.get('noncentral') else ''),
                             _time_on(e.get('p1'), e['max'], tz), _time(e['max'], tz),
                             _time_on(e.get('p4'), e['max'], tz),
                             f'{e["magnitude"]:.4f}', f'{e["gamma"]:+.4f}',
                             '—' if e['type'] == 'partial' else _dur(e['central_duration_s'], True),
                             _f(e.get('path_width_km'), '.0f', ' km'), _latlon(e.get('ge_lat'), e.get('ge_lon')),
                             e.get('saros') or '—'])
            else:   # transits: contacts seen from the Earth's centre
                rows.append([_time(e['max'], tz, '%Y-%m-%d'), _category(e, True), _time_on(e['c1'], e['max'], tz),
                             _time(e['max'], tz), _time_on(e['c4'], e['max'], tz), f'{e["min_sep_arcsec"]:.1f}"', '—',
                             _dur(e['duration_s']), '—', tr('（地球中心から見た値）'), '—'])
        lines += [''] + _table(head, rows, right={5, 6, 7, 8, 10})
        lines += ['', tr('日食の始まり・終わりは地球上のどこかで部分食が始まる・終わる時刻、'
                         'γ は影の軸と地球中心の距離（地球赤道半径単位）です。')]
        return lines
    ground = kind == 'ground'
    head = [tr(k) for k in ('日付', '種類', '始まり', '最大', '終わり', '食分/中心間', '食面積率', '継続時間')]
    head += ([tr('最大時の太陽'), tr('見え方'), tr('サロス')] if ground
             else [tr('見え方'), tr('最大時の衛星直下点'), tr('高度'), tr('サロス')])
    rows = []
    for e in evs:
        moon = e['body'] == 'moon'
        dur = _dur(e.get('duration_s'))
        if moon and (e.get('central_duration_s') or 0) > 0:
            v = {'c': _dur(e['central_duration_s'], True), 'd': dur}
            dur = tr('皆既 {c}／全体 {d}', **v) if e['type'] == 'total' else tr('金環 {c}／全体 {d}', **v)
        row = [_time(e['max'], tz, '%Y-%m-%d'), _category(e, True),
               _time_on(e['c1'], e['max'], tz), _time(e['max'], tz), _time_on(e['c4'], e['max'], tz),
               f'{e["magnitude"]:.3f}' if moon else f'{e["min_sep_arcsec"]:.1f}"',
               f'{100 * e["obscuration"]:.1f}%' if moon and e.get('obscuration') is not None else '—', dur]
        if ground:
            alt, az = e.get('sun_alt_max'), e.get('sun_az_max')
            row += [tr('高度 {v}°', v=f'{alt:.0f}') + _sep() + _az(az) if alt is not None else '—', _vis(e, True)]
        else:
            row += [_vis(e, False), _latlon(e.get('sat_lat_max'), e.get('sat_lon_max')),
                    _f(e.get('sat_alt_km_max'), ',.0f', ' km')]
        rows.append(row + [e.get('saros') or '—'])
    right = {5, 6, 7, len(head) - 1} | (set() if ground else {len(head) - 2})
    lines += [''] + _table(head, rows, right=right)
    if args.detail:
        for e in evs:
            lines += ['', '■ ' + tr('{date} {what}（最大 {time}）', date=_time(e['max'], tz, '%Y-%m-%d'),
                                    what=_category(e, True), time=_time(e['max'], tz))]
            lines += _contacts_table(e, tz, ground)
            if len(e.get('internal') or []) > 1:
                lines.append(tr('衛星の運動により皆既・金環（内接）が {n} 回に分かれます', n=len(e['internal'])))
    return lines


def sweep_table(res, args):
    groups = res['groups']
    tz = args.tz_hours
    lines = _header(res, args, [tr('平均近点角を {step}° ずつ変えた {n} 通り', step=f'{res["step_deg"]:g}',
                                   n=len(res['phases']))])
    if res['observer'].get('model') == 'tle':
        lines.append(tr('TLE の平均近点角（元期での衛星の位置）は {m}° です。'
                        'TLE どおりの位置での結果は --sweep を付けずに計算できます。', m=f'{res["observer"]["m_deg"]:.2f}'))
    lines += ['', tr('{n} 件の現象（衛星が軌道上のどこにいるかで結果が変わります）', n=len(groups)) if groups
              else tr('この期間には、どの位相でも見られる現象がありません。')]
    if not groups:
        return lines
    head = [tr(k) for k in ('日付', '現象', '見える位相', '見える回数', '最も深い食の食分', '皆既・金環になる位相',
                            '最大の時刻の範囲')]
    rows = []
    for g in groups:
        moon = g['body'] == 'moon'
        c = len(g['central_phases'])
        if g['time_first']:
            span = _span(_time(g['time_first'], tz, '%m-%d %H:%M:%S'), _time_on(g['time_last'], g['time_first'], tz))
        else:
            span = '—'
        rows.append([
            _time(g['date'], tz, '%Y-%m-%d'), tr(BODY_JA[g['body']]), f'{g["n_visible"]} / {g["n_phases"]}',
            tr('{n} 回', n=g['count_max']) if g['count_min'] == g['count_max']
            else tr('{a}〜{b} 回', a=g['count_min'], b=g['count_max']),
            _span(f'{g["mag_min"]:.3f}', f'{g["mag_max"]:.3f}') if moon and g['mag_min'] is not None else '—',
            '—' if not moon else f'{c} / {g["n_phases"]}' + _paren(f'{round(100 * c / g["n_phases"])}%') if c
            else tr('なし'),
            span])
    lines += [''] + _table(head, rows, right={2, 3, 4})
    lines += ['', tr('位相（平均近点角）ごとの結果は --format csv または --format json で出力できます。')]
    return lines


def search_csv(res):
    # same columns as the web UI's 「一覧をCSV保存」, plus the sub-satellite point at maximum
    rows = [['date_utc', 'type', 'max_utc', 'c1_utc', 'c4_utc', 'magnitude', 'obscuration', 'ratio',
             'min_sep_arcsec', 'gamma', 'central_duration_s', 'duration_s', 'path_width_km', 'ge_lat',
             'ge_lon', 'sun_alt_max', 'visible_fraction', 'saros', 'sat_lat_max', 'sat_lon_max',
             'sat_alt_km_max']]
    for e in res['events']:
        rows.append([e['max'][:10], _category(e), e['max'], e.get('c1') or e.get('p1'), e.get('c4') or e.get('p4'),
                     e.get('magnitude'), e.get('obscuration'), e.get('ratio'), e.get('min_sep_arcsec'),
                     e.get('gamma'), e.get('central_duration_s'), e.get('duration_s'), e.get('path_width_km'),
                     e.get('ge_lat'), e.get('ge_lon'), e.get('sun_alt_max', e.get('sun_alt')),
                     e.get('vis_fraction'), e.get('saros'), e.get('sat_lat_max'), e.get('sat_lon_max'),
                     e.get('sat_alt_km_max')])
    return rows


def sweep_csv(res):
    # same columns as the web UI's CSV of a phase sweep
    rows = [['date_utc', 'phenomenon', 'mean_anomaly_deg', 'visible_count', 'type', 'max_utc', 'magnitude',
             'obscuration', 'min_sep_arcsec', 'central_duration_s', 'duration_s', 'visible_fraction']]
    for g in res['groups']:
        for r in g['rows']:
            e = r['best'] or {}
            rows.append([g['date'][:10], tr(BODY_JA[g['body']]), r['m_deg'], r['count'],
                         _category(e) if e else None, e.get('max'), e.get('magnitude'), e.get('obscuration'),
                         e.get('min_sep_arcsec'), e.get('central_duration_s'), e.get('duration_s'),
                         e.get('vis_fraction')])
    return rows


def _csv_text(rows):
    buf = io.StringIO()
    csv.writer(buf, lineterminator='\n').writerows([['' if v is None else v for v in r] for r in rows])
    return buf.getvalue()


def _json_text(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1) + '\n'


def _emit(text, args, csv_out=False):
    if args.output:
        # CSV for Excel: UTF-8 with BOM and CRLF, like the web UI's download
        try:
            with open(args.output, 'w', encoding='utf-8-sig' if csv_out else 'utf-8',
                      newline='\r\n' if csv_out else None) as f:
                f.write(text)
        except OSError as exc:
            raise InputError(tr('結果を保存できません: {exc}', exc=exc))
        if not args.quiet:
            print(tr('保存しました: {path}', path=args.output), file=sys.stderr, flush=True)
    else:
        sys.stdout.write(text)


# ---------------------------------------------------------------------------
def run(args):
    from eclipsecalc import __version__

    def log(message):
        if not args.quiet:
            print(message, file=sys.stderr, flush=True)

    if args.list_cities:
        from eclipsecalc.presets import cities
        for c in cities():
            print(f'{tr(c["name"])}\t{c["lat"]}\t{c["lon"]}\t{c["elevation_m"]}')
        return 0
    if args.list_sscweb:
        from eclipsecalc.observers import ObserverError, ssc_satellites
        try:
            sats = ssc_satellites()
        except ObserverError as exc:
            raise InputError(str(exc))
        for s in sats:
            print(f'{s["id"]}\t{s["name"]}\t{s["start"][:10]}\t{s["end"][:10]}')
        return 0

    req = build_request(args, log)
    check_utc(req['start'], 'start')
    check_utc(req['end'], 'end')
    ctx = _context(req)
    observer, obs = check_observer(ctx, req['observer'])
    base = {'ok': True, 'tool': 'solar_eclipse_calc cli.py', 'version': __version__, 'lang': i18n.current(),
            'request': req}
    if args.dry_run:
        warnings = preflight(req, obs)
        if args.format == 'json':
            _emit(_json_text(dict(base, dry_run=True, observer=observer, warnings=warnings)), args)
        else:
            _emit(tr('観測者: {obs}', obs=observer_text(observer)) + '\n'
                  + tr('期間: {start} 〜 {end}（UTC）', start=req['start'], end=req['end']) + _sep()
                  + tr('現象: {list}', list=', '.join(req['phenomena'])) + '\n'
                  + ''.join(tr('注意: {w}', w=w) + '\n' for w in warnings)
                  + tr('計算条件（--request で使える JSON）:') + '\n' + _json_text(req), args)
        return 0

    t0 = time.time()
    sweep = req.get('step_deg') is not None
    log(tr('計算しています…') if not sweep else
        tr('平均近点角を {step}° ずつ変えて計算しています（時間がかかります）…', step=f'{req["step_deg"]:g}'))
    res = calculate(req, debug=os.environ.get('ECLIPSECALC_DEBUG') == '1')
    if args.detail and not sweep:
        attach_details(res)
    res = _strip_ids(res)
    log(tr('完了（{v} 秒）', v=f'{time.time() - t0:.1f}'))

    if args.format == 'json':
        _emit(_json_text(dict(base, **res)), args)
        return 0
    if args.format == 'csv':
        for w in res.get('warnings') or []:      # the table shows them in its header
            log(tr('注意: {w}', w=w))
        _emit(_csv_text(sweep_csv(res) if sweep else search_csv(res)), args, csv_out=True)
    else:
        _emit('\n'.join(sweep_table(res, args) if sweep else search_table(res, args)) + '\n', args)
    return 0


def _utf8_streams():
    """Text from and to a file or a pipe in UTF-8 (on Windows it would otherwise be the
    ANSI code page, which cannot hold most of the languages); a console shows any text."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if os.environ.get('PYTHONIOENCODING') or stream.isatty():
                stream.reconfigure(errors='replace')
            else:
                stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass


def main(argv=None):
    _utf8_streams()
    argv = sys.argv[1:] if argv is None else argv
    token = i18n.set_lang(choose_lang(argv))
    try:
        code = _main(argv)
        sys.stdout.flush()      # a closed pipe shows up here rather than at exit
        return code
    except BrokenPipeError:     # the reader stopped early (... | head): not an error of the calculation
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    finally:
        i18n.reset_lang(token)


def _main(argv):
    json_out = '--format=json' in argv or any(a == '--format' and b == 'json' for a, b in zip(argv, argv[1:]))
    args = None

    def fail(message, code):
        if json_out:
            text = _json_text({'ok': False, 'error': message, 'exit_code': code})
            sys.stdout.write(text)
            if args is not None and args.output:       # do not leave an earlier result in the -o file
                try:
                    with open(args.output, 'w', encoding='utf-8') as f:
                        f.write(text)
                except OSError:
                    pass
        print(('cli.py: ' if code == 2 else '') + tr('エラー: {message}', message=message), file=sys.stderr, flush=True)
        if code == 2:
            print(tr('使い方は python cli.py --help を見てください'), file=sys.stderr)
        return code

    try:
        args = parse_args(argv)
    except UsageError as exc:
        return fail(str(exc), 2)
    try:
        return run(args)
    except InputError as exc:
        return fail(str(exc), 1)
    except BrokenPipeError:
        raise
    except Exception as exc:  # keep the promise of a JSON answer with --format json
        if os.environ.get('ECLIPSECALC_DEBUG') == '1':
            import traceback
            traceback.print_exc()
        return fail(tr('予期しないエラー: {exc}', exc=repr(exc)), 1)


if __name__ == '__main__':
    sys.exit(main())
