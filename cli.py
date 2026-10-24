"""Eclipses and transits from the command line (the same calculation as the
web UI). Meant for people and for AI agents alike; see docs/cli.md.

The observer is chosen by the options that are given (exactly one kind):

    --epoch --a --e --i --raan [--argp --m]     satellite, six orbital elements
        [--n --raan-rate --argp-rate]           (secular rates given with them; else J2)
    --epoch --alt|--perigee-alt --apogee-alt --sso|--i --ltan|--raan
                                                satellite, planned orbit (altitude, SSO, LTAN)
    --tle FILE | --norad N                      satellite, TLE (SGP4)
    --geo-lon DEG                               ideal geostationary satellite
    --horizons ID [--horizons-step MIN]         spacecraft from JPL Horizons
    --sscweb ID                                 past orbit from NASA SSCWeb
    --ccsds FILE [--ccsds-object NAME] [--station NAME=LAT,LON[,M]]
                                                CCSDS OEM / OPM / OMM / OCM / TDM (KVN or XML)
    --lat --lon [--elev] | --city NAME          point on the ground
    --global                                    whole Earth (where on Earth an eclipse is seen)
    --request FILE                              a JSON request (same shape as the web API)

    python cli.py --epoch 2027-07-30T12:00:00 --a 7058.1 --e 0.0012 --i 98.13 \\
        --raan 220.5 --argp 90 --m 45 --start 2027-07-25 --end 2027-08-10 --tz 9
    python cli.py --city Tokyo --start 2026-01-01 --end 2056-01-01 --format json --detail --lang en

With --at TIME[,TIME...] or --at-file FILE (and one --phenomena) it computes,
instead of searching, the position of the body on the solar disk at those times
(e.g. of images: solar-north-up X / Y in arcsec, distance from the limb, O-C of
measured positions), as POST /api/event/{id}/at of the web API:

    python cli.py --sscweb hinode --phenomena mercury --at "8-Nov-2006 19:13:37.498 UT"

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

from eclipsecalc import constants as C  # noqa: E402
from eclipsecalc import i18n  # noqa: E402
from eclipsecalc.i18n import tr  # noqa: E402

PHENOMENA = ('moon', 'mercury', 'venus')
# earth: the Earth's shadow on a satellite (the times at which the Earth hides the Sun); not a default
ALL_PHENOMENA = PHENOMENA + ('earth',)
# --sun-radius and --planet-radius (as the presets of the web UI, /api/info)
SUN_RADIUS = {'observed': C.SUN_RADIUS_OBSERVED, 'nasa': C.SUN_RADIUS_NASA, 'iau2015': C.SUN_RADIUS_IAU2015}
PLANET_RADIUS = {'observed': (C.MERCURY_RADIUS, C.VENUS_RADIUS_VISIBLE), 'iau': (C.MERCURY_RADIUS, C.VENUS_RADIUS),
                 'nasa': (C.MERCURY_RADIUS_NASA, C.VENUS_RADIUS_NASA), 'xray': (C.MERCURY_RADIUS, C.VENUS_RADIUS_XRAY)}
# The names of the radii in the header of the tables (Japanese keys; the first that matches)
SUN_RADIUS_NAMES = ((C.SUN_RADIUS_OBSERVED, '観測される縁'), (C.SUN_RADIUS_NASA, 'NASA'),
                    (C.SUN_RADIUS_IAU2015, 'IAU 2015 の光球'))
PLANET_RADIUS_NAMES = {
    'mercury': ((C.MERCURY_RADIUS, 'IAU の固体表面、大気がなく見える大きさと同じ'), (C.MERCURY_RADIUS_NASA, 'NASA')),
    'venus': ((C.VENUS_RADIUS_VISIBLE, '見える大きさ: 雲頂と靄'), (C.VENUS_RADIUS, 'IAU の固体表面'),
              (C.VENUS_RADIUS_NASA, 'NASA'), (C.VENUS_RADIUS_XRAY, '軟X線')),
}
# Japanese names (the keys of their translations)
BODY_JA = {'moon': '日食', 'mercury': '水星の太陽面通過', 'venus': '金星の太陽面通過'}
TYPE_JA = {'total': '皆既日食', 'annular': '金環日食', 'hybrid': '金環皆既日食', 'partial': '部分日食'}
CONTACT_JA = {'C1': '第1接触', 'C2': '第2接触', 'MAX': '最大', 'C3': '第3接触', 'C4': '第4接触'}
ORBIT_ARGS = ('epoch', 'a', 'e', 'i', 'raan', 'alt', 'perigee_alt', 'apogee_alt', 'sso', 'ltan')
ORBIT_ONLY = ('argp', 'm', 'n', 'raan_rate', 'argp_rate', 'no_j2')
# the secular rates and their keys in the web API's observer
RATE_ARGS = (('n', 'n_rev_day'), ('raan_rate', 'raan_rate_deg_day'), ('argp_rate', 'argp_rate_deg_day'))
CCSDS_ONLY = ('ccsds_object', 'station', 'stations')
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
        (tr('軌道6要素'), tr('--epoch --a --e --i --raan（と --argp --m、永年変化率 --n --raan-rate --argp-rate）')),
        (tr('計画中の軌道'), tr('--epoch と、大きさ --alt または --perigee-alt --apogee-alt、傾斜角 --i または --sso、\n'
                                '軌道面 --raan または --ltan（軌道6要素の指定と組み合わせも可）')),
        ('TLE', tr('--tle ファイル（- で標準入力）または --norad 番号（CelesTrak から最新の TLE を取得）')),
        (tr('静止衛星'), tr('--geo-lon 経度')),
        ('JPL Horizons', tr('--horizons ID（例 -170 = JWST）')),
        ('NASA SSCWeb', tr('--sscweb ID（例 hinode。過去の軌道）')),
        ('CCSDS', tr('--ccsds ファイル（OEM・OPM・OMM・OCM・TDM。KVN・XML。TDM には観測局の位置 --station を添える）')),
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
        '  python cli.py --sscweb hinode --phenomena mercury --at "8-Nov-2006 19:13:37.498 UT"',
        '  python cli.py --ccsds orbit.oem --start 2027-08-01 --end 2027-08-03 --phenomena moon',
        '  python cli.py --ccsds track.tdm --station USUDA=36.1325,138.3628,1456 --start 2027-08-01 --end 2027-08-03',
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
    g.add_argument('--n', type=float, metavar='REV/DAY',
                   help=tr('平均運動 (周回/日)。平均近点角の変化率。省略すると軌道長半径から J2 の式で計算'))
    g.add_argument('--raan-rate', type=float, metavar='DEG/DAY', help=tr('昇交点赤経の変化率 (°/日)。省略すると J2 の式で計算'))
    g.add_argument('--argp-rate', type=float, metavar='DEG/DAY', help=tr('近地点引数の変化率 (°/日)。省略すると J2 の式で計算'))
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

    c = ap.add_argument_group(tr('観測者: CCSDS のメッセージ（OEM・OPM・OMM・OCM・TDM）'))
    c.add_argument('--ccsds', action='append', metavar='FILE',
                   help=tr('CCSDS の軌道データメッセージ（OEM・OPM・OMM・OCM）または追跡データメッセージ（TDM）のファイル'
                           '（KVN・XML。OMM は CelesTrak の JSON・CSV も可。- で標準入力）。繰り返すと複数のファイルを合わせて使う'
                           '（TDM と同じ衛星の OPM・OMM・TLE を一緒に渡すと、それを軌道決定の初期値にする）'))
    c.add_argument('--ccsds-object', metavar='NAME',
                   help=tr('ファイルに複数の天体があるとき、使う天体の名前・国際標識・NORAD 番号（TDM では衛星の参加者名）'))
    c.add_argument('--station', action='append', metavar='NAME=LAT,LON[,M]',
                   help=tr('TDM の観測局の位置（名前=緯度,経度,標高 m）。TDM には観測局の位置が書かれていないため、'
                           '参加者（PARTICIPANT_n）のうち衛星以外の局ごとに指定する。繰り返し可'))
    c.add_argument('--stations', metavar='FILE',
                   help=tr('TDM の観測局の位置のファイル。1 行に「名前 緯度 経度 標高(m)」。# から行末までは注釈'))

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
                   help=tr('計算する現象をカンマ区切りで: moon（日食）, mercury, venus（既定 この 3 つ）、'
                           'earth（人工衛星が地球の影に入る時間。季節・周回ごとの入りと出・長さ）'))
    p.add_argument('--sweep', type=float, metavar='STEP',
                   help=tr('軌道要素・TLE の衛星のみ: 平均近点角（軌道上の位置）をこの刻み (°, 5〜90) で変えて一括計算する'
                           '（打ち上げ前や、TLE の元期から何か月も先の検討。期間は 1 年以内）'))

    a = ap.add_argument_group(tr('時刻を指定した位置（観測画像と比べる）'))
    a.add_argument('--at', action='append', metavar='UTC',
                   help=tr('この時刻（UTC）の太陽面上の位置などを計算する（現象は探さない。--phenomena で現象を 1 つ指定）。'
                           'カンマ区切りで複数、繰り返しも可。例 2017-08-21T16:53:59.204Z、'
                           '"21-Aug-2017 16:53:59.204 UT"（ひので・SDO の画像の時刻の形）'))
    a.add_argument('--at-file', metavar='FILE',
                   help=tr('時刻のファイル（- で標準入力）。1 行に「時刻 [X Y]」。X Y は画像で測った位置'
                           '（太陽の北が上、″）で、計算との差 O−C を出す。# から行末までは注釈'))
    a.add_argument('--exposure', type=float, metavar='SEC',
                   help=tr('露光時間 (秒)。時刻にその半分を足した露光の中央で計算する（画像の時刻が露光の開始のとき）'))

    s = ap.add_argument_group(tr('詳細設定'))
    s.add_argument('--ephemeris', metavar='FILE',
                   help=tr('暦（既定 de440s.bsp = 1849〜2150 年。de440.bsp なら 1550〜2650 年）'))
    s.add_argument('--delta-t', type=float, metavar='SEC', help=tr('ΔT（TT−UT, 秒）を固定値で指定（既定は自動）'))
    s.add_argument('--sun-radius', metavar='observed|nasa|iau2015|KM|ARCSECas',
                   help=tr('太陽の半径: observed（既定。観測される縁、1 AU で 959.96″ = 696,231 km）、'
                           'nasa（959.63″ = 695,992 km）、iau2015（光球 695,700 km = 959.23″）、km の数値、'
                           'または 959.96as のように 1 AU での角半径 (″)'))
    s.add_argument('--planet-radius', choices=tuple(PLANET_RADIUS), metavar='observed|iau|nasa|xray',
                   help=tr('水星・金星の半径: observed（既定。太陽の前に見える大きさ: 水星 2,439.4 km、'
                           '金星 6,131 km = 雲頂と靄）、iau（固体表面: 金星 6,051.8 km）、'
                           'nasa（1 AU で 3.36″・8.41″）、xray（軟X線: 金星 6,202 km）'))
    s.add_argument('--atm', type=float, metavar='KM',
                   help=tr('人工衛星: 地球大気の遮蔽高度 (km)（既定 0。目安は可視光 0、軟X線 約 140 km、'
                           '極紫外線 約 250 km）'))
    s.add_argument('--atm-levels', metavar='KM,KM,...',
                   help=tr('地球の影（--phenomena earth）で比べる遮蔽高度 (km) をカンマ区切りで（既定 0,140,250。'
                           '--atm の値も加える）'))
    s.add_argument('--min-sun-alt', type=float, metavar='DEG',
                   help=tr('地上: 太陽中心がこの高度以上で「見える」とする (°)（既定 0）'))
    s.add_argument('--no-refraction', action='store_true', help=tr('地上: 大気差を太陽高度に反映しない'))
    s.add_argument('--include-invisible', action='store_true', help=tr('見えない現象（夜間・地球の陰）も含める'))

    out = ap.add_argument_group(tr('出力'))
    out.add_argument('--format', choices=('table', 'csv', 'json'), default='table',
                     help=tr('出力形式（既定 table。生成 AI からは json）'))
    out.add_argument('--detail', action='store_true',
                     help=tr('各現象の接触（第1〜第4接触・最大）の時刻・位置（太陽の北が上の X・Y）・太陽高度なども出力する'
                             '（CSV では接触ごとに 1 行）'))
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
    if _given(args, 'ccsds'):
        modes.append('ccsds')
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
                            'CCSDS（--ccsds）、地上の地点（--lat --lon / --city）、地球全体（--global）、JSON（--request）')
                         + (tr('（指定されたもの: {modes}）', modes=', '.join(modes)) if modes else ''))
    args.mode = modes[0]
    args.at_mode = bool(args.at or args.at_file)
    if args.at_mode and (args.sweep is not None or args.detail):
        raise UsageError(tr('--at と --at-file は --sweep・--detail と同時には使えません'))
    if args.mode == 'request':
        clash = [k for k in ORBIT_ONLY + CCSDS_ONLY + ('elev', 'horizons_step', 'name', 'start', 'end', 'phenomena', 'sweep',
                                          'ephemeris', 'delta_t', 'sun_radius', 'planet_radius', 'atm', 'atm_levels',
                                          'min_sun_alt', 'no_refraction', 'include_invisible') if _given(args, k)]
        if clash:
            raise UsageError(tr('--request と {opts} は併用できません（JSON の中で指定してください）',
                                opts=' '.join('--' + k.replace('_', '-') for k in clash)))
        return args
    if _given(args, 'exposure') and not args.at_mode:
        raise UsageError(tr('--exposure は --at または --at-file と一緒に指定してください'))
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
    if args.mode != 'ccsds' and any(_given(args, k) for k in CCSDS_ONLY):
        raise UsageError(tr('{opts} は CCSDS のメッセージ（--ccsds）だけで使えます',
                            opts=' '.join('--' + k.replace('_', '-') for k in CCSDS_ONLY if _given(args, k))))
    if args.mode == 'ccsds':
        _station_items(args)
    if _given(args, 'sweep') and args.mode not in ('kepler', 'tle', 'ccsds'):
        raise UsageError(tr('--sweep は軌道要素・TLE・OMM（--ccsds）で指定した衛星だけで使えます'))
    if args.mode == 'tle' and _given(args, 'tle') and _given(args, 'norad'):
        raise UsageError(tr('--tle と --norad はどちらか一方だけ指定してください'))
    if args.mode == 'ground':
        if _given(args, 'city') and (_given(args, 'lat') or _given(args, 'lon')):
            raise UsageError(tr('--city と --lat/--lon はどちらか一方だけ指定してください'))
        if not _given(args, 'city') and not (_given(args, 'lat') and _given(args, 'lon')):
            raise UsageError(tr('地上の地点では --lat と --lon の両方を指定してください'))
    if args.at_mode:
        if args.mode == 'global':
            raise UsageError(tr('--at と --at-file は --global では使えません（地上の地点・人工衛星・探査機を指定してください）'))
        if len({x.strip() for x in (args.phenomena or '').split(',') if x.strip()}) != 1:
            raise UsageError(tr('--at と --at-file では現象を --phenomena で 1 つ指定してください（moon, mercury, venus のどれか）'))
        if (args.start is None) != (args.end is None):      # optional: the times must then be in the period
            raise UsageError(tr('期間を --start と --end で指定してください（例 --start 2027-07-25 --end 2027-08-10）'))
        return args
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
    if _given(args, 'n') and not args.n > 0:
        raise UsageError(tr('--n（平均運動）は正の値で指定してください'))


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


def _station_items(args):
    """--station NAME=LAT,LON[,M] -> [{'name', 'lat', 'lon', 'elevation_m'}] (checked with the other options)."""
    rows = []
    for item in args.station or []:
        name, sep, rest = item.rpartition('=')
        try:
            vals = [float(x) for x in rest.replace(' ', '').split(',')]
        except ValueError:
            vals = []
        if not sep or not name.strip() or len(vals) not in (2, 3) or not all(map(math.isfinite, vals)):
            raise UsageError(tr('--station は 名前=緯度,経度,標高(m) の形で指定してください（例 USUDA=36.1325,138.3628,1456）'))
        rows.append({'name': name.strip(), 'lat': vals[0], 'lon': vals[1],
                     'elevation_m': vals[2] if len(vals) == 3 else 0.0})
    return rows


def _station_rows(args):
    """--station NAME=LAT,LON[,M] and --stations FILE -> [{'name', 'lat', 'lon', 'elevation_m'}]."""
    rows = _station_items(args)
    if args.stations:
        from eclipsecalc.observers import ObserverError
        from eclipsecalc.od import parse_stations
        try:
            text = _read_text(args.stations)
        except OSError as exc:
            raise InputError(tr('観測局のファイルを読めません: {exc}', exc=exc))
        try:
            rows += [{'name': n, 'lat': la, 'lon': lo, 'elevation_m': h} for n, la, lo, h in parse_stations(text).values()]
        except ObserverError as exc:
            raise InputError(str(exc))
    return rows


def _ccsds_spec(args, name):
    texts = []
    for path in args.ccsds:
        try:
            texts.append(_read_text(path))
        except OSError as exc:
            raise InputError(tr('CCSDS のメッセージのファイルを読めません: {exc}', exc=exc))
    spec = {'type': 'ccsds', 'text': texts[0] if len(texts) == 1 else texts, 'name': name}
    if args.ccsds_object:
        spec['object'] = args.ccsds_object
    stations = _station_rows(args)
    if stations:
        spec['stations'] = stations
    return spec


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
        spec.update({key: getattr(args, k) for k, key in RATE_ARGS if _given(args, k)})
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
    if args.mode == 'ccsds':
        return _ccsds_spec(args, name)
    if args.mode == 'global':
        return {'type': 'global'}
    if args.city is not None:
        c = _find_city(args.city)
        return {'type': 'ground', 'lat': c['lat'], 'lon': c['lon'],
                'elevation_m': c['elevation_m'] if args.elev is None else args.elev, 'name': name or tr(c['name'])}
    return {'type': 'ground', 'lat': args.lat, 'lon': args.lon, 'elevation_m': args.elev or 0.0, 'name': name}


_RADIUS_RE = re.compile(r'\s*(\d+(?:\.\d*)?|\.\d+)\s*(km|as|arcsec|″|")?\s*', re.I)


def sun_radius_km(text):
    """--sun-radius: a preset, km, or the angular radius at 1 AU in arcsec (959.96as) -> km."""
    key = str(text).strip().lower()
    if key in SUN_RADIUS:
        return SUN_RADIUS[key]
    m = _RADIUS_RE.fullmatch(key)
    unit = (m.group(2) or 'km').lower() if m else None
    v = float(m.group(1)) if m else 0.0
    r = v if unit == 'km' else v / C.RAD2ARCSEC * C.AU_KM
    # a bare number below 2000 is an angle given without "as" (the Sun is ~696,000 km)
    if not m or not v > 0 or (unit == 'km' and r < 2000):
        raise InputError(tr('--sun-radius は observed、nasa、iau2015、km の数値、または 959.96as のように '
                            '1 AU での角半径（″）で指定してください'))
    return r


def _settings_from_args(args):
    s = {}
    if args.ephemeris:
        s['ephemeris'] = args.ephemeris
    if args.delta_t is not None:
        s['delta_t'] = args.delta_t
    if args.sun_radius:
        s['sun_radius_km'] = sun_radius_km(args.sun_radius)
    if args.planet_radius:
        s['mercury_radius_km'], s['venus_radius_km'] = PLANET_RADIUS[args.planet_radius]
    if args.atm is not None:
        s['earth_atm_km'] = args.atm
    if args.atm_levels is not None:
        from eclipsecalc.earth_shadow import check_levels
        try:
            s['shadow_atm_levels_km'] = check_levels(args.atm_levels)
        except ValueError as exc:
            raise InputError(f'--atm-levels: {exc}')
    if args.min_sun_alt is not None:
        s['min_sun_alt_deg'] = args.min_sun_alt
    if args.no_refraction:
        s['refraction'] = False
    if args.include_invisible:
        s['include_invisible'] = True
    return s


RADIUS_SETTINGS = ('sun_radius_km', 'moon_radius_ext_km', 'moon_radius_int_km', 'mercury_radius_km', 'venus_radius_km')


def _with_radii(settings):
    """Write the default radii into ``settings`` where none is given, so that the echoed request
    reproduces the calculation after a change of the defaults (requests written before 2026-10
    lack them and meant 695,700 km and the solid Venus: docs/cli.md)."""
    from eclipsecalc.local import Params
    default = Params()
    for k in RADIUS_SETTINGS:
        if settings.get(k) in (None, ''):
            settings[k] = getattr(default, k)
    return settings


def _mean_elements_only(spec):
    from eclipsecalc.ccsds import mean_elements_only
    return mean_elements_only(spec)


def _not_json(name):
    raise ValueError(f'{name} is not a JSON value')


def _at_inputs(args):
    """The times of --at and --at-file -> (times, measured [X, Y] or None for each, or None if none
    was measured).  A line of the file is "TIME [X Y]" (also separated by commas or tabs)."""
    times, measured = [], []
    for a in args.at or []:
        for s in a.split(','):
            if s.strip():
                times.append(s.strip())
                measured.append(None)
    if args.at_file:
        try:
            text = _read_text(args.at_file)
        except OSError as exc:
            raise InputError(tr('時刻のファイルを読めません: {exc}', exc=exc))
        for line in text.splitlines():
            parts = line.split('#', 1)[0].replace(',', ' ').replace('\t', ' ').split()
            if not parts:
                continue
            xy = None
            if len(parts) >= 3:
                try:
                    xy = [float(parts[-2]), float(parts[-1])]
                except ValueError:
                    pass
            if xy and all(map(math.isfinite, xy)):
                times.append(' '.join(parts[:-2]))
                measured.append(xy)
            else:                       # the whole line is the time (parse_obs_time names it if it is not one)
                times.append(' '.join(parts))
                measured.append(None)
    if not times:
        raise InputError(tr('--at または --at-file に時刻がありません'))
    return times, (measured if any(m is not None for m in measured) else None)


def build_request(args, log):
    """-> the request in the web API's shape (also echoed in the JSON output).  With times (--at,
    --at-file, or "times" in the JSON) it is that of server.at_times: the period is optional."""
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
        unknown = set(req) - {'observer', 'start', 'end', 'phenomena', 'settings', 'step_deg',
                              'times', 'measured', 'exposure_s'}
        if unknown:
            raise InputError(tr('計算条件の JSON に不明な項目があります: {keys}', keys=', '.join(sorted(unknown))))
        req = dict(req)
        if args.at_mode:                # the times of the options replace those of the JSON
            req['times'], req['measured'] = _at_inputs(args)
        if args.exposure is not None:
            req['exposure_s'] = args.exposure
        at = req.get('times') is not None
        if not at and req.get('exposure_s') is not None:
            raise InputError(tr('--exposure は --at または --at-file と一緒に指定してください'))
        for k in ('start', 'end'):      # with times the period may be left out (both)
            if not isinstance(req.get(k), str) and not (at and req.get('start') is None and req.get('end') is None):
                raise InputError(tr('計算条件の JSON に "{key}"（UTC の日付の文字列）が必要です', key=k))
        req.setdefault('phenomena', list(PHENOMENA))
        req.setdefault('settings', {})
        if not isinstance(req['settings'], dict):
            raise InputError(tr('計算条件の JSON の "settings" はオブジェクトで指定してください'))
        req['settings'] = _with_radii(dict(req['settings']))
        step = req.get('step_deg')
        if step is not None and (isinstance(step, bool) or not isinstance(step, (int, float)) or not 5 <= step <= 90):
            raise InputError(tr('位相の刻みは 5〜90° で指定してください'))
        if req['observer'].get('type') == 'celestrak':
            o = req['observer']
            req['observer'] = _celestrak(o.get('norad'), o.get('name', ''), log)
    else:
        req = {'observer': observer_from_args(args, log), 'start': args.start, 'end': args.end,
               'phenomena': args.phenomena or ','.join(PHENOMENA), 'settings': _with_radii(_settings_from_args(args))}
        if args.sweep is not None:
            req['step_deg'] = args.sweep
        if args.at_mode:
            req['times'], req['measured'] = _at_inputs(args)
            req['exposure_s'] = args.exposure
            if args.start is None:
                del req['start'], req['end']
    ph = req['phenomena']
    if isinstance(ph, str):
        ph = [x.strip() for x in ph.split(',') if x.strip()]
    bad = [x for x in ph if x not in ALL_PHENOMENA] if isinstance(ph, list) else ['?']
    if bad or not ph:
        raise InputError(tr('現象は moon, mercury, venus, earth から選んでください（指定: {given}）',
                            given=', '.join(map(str, bad)) or tr('なし')))
    req['phenomena'] = list(dict.fromkeys(ph))      # each once (moon,moon would list every eclipse twice)
    if 'earth' in req['phenomena']:
        if req.get('step_deg') is not None:
            raise InputError(tr('地球の影は、位相を変えた一括計算では計算できません（影に入る時期と長さは軌道上の位置によらないため、'
                                '一括計算をやめて計算してください）'))
        if req['observer'].get('type') in ('ground', 'global'):
            raise InputError(tr('地球の影は人工衛星・探査機から見たときだけ計算できます（観測者を「人工衛星」にしてください）'))
        if args.format == 'csv' and len(req['phenomena']) > 1:
            raise InputError(tr('CSV では地球の影（earth）とほかの現象を一緒に出力できません。--phenomena earth だけで計算してください'))
    if req.get('step_deg') is not None and req['observer'].get('type') not in ('kepler', 'tle') and not (
            req['observer'].get('type') == 'ccsds' and _mean_elements_only(req['observer'])):
        raise InputError(tr('--sweep は軌道要素・TLE・OMM（--ccsds）で指定した衛星だけで使えます') if args.sweep is not None
                         else tr('平均近点角を変えた一括計算（step_deg）は軌道要素・TLE・OMM で指定した衛星だけで使えます'))
    if req.get('times') is not None:
        if req.get('step_deg') is not None:
            raise InputError(tr('--at と --at-file は --sweep・--detail と同時には使えません'))
        if len(req['phenomena']) != 1:
            raise InputError(tr('時刻を指定した位置の計算では、現象（moon, mercury, venus）を 1 つだけ選んでください'))
        if req['observer'].get('type') == 'global':
            raise InputError(tr('時刻を指定した位置の計算は、地上の地点・人工衛星・探査機（または地心）から見た'
                                '現象だけでできます（地球全体の日食ではできません）'))
        for k in ('measured', 'exposure_s'):
            if req.get(k) is None:
                req.pop(k, None)
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
    from eclipsecalc.observers import EphemerisObserver, ObserverError, PropagatedObserver, SGP4Observer
    kw = {k: req[k] for k in ('phenomena', 'observer', 'start', 'end', 'settings')}
    with _api_errors():
        ctx, _, jd_a, jd_b, _ = server._parse_request(server.SearchRequest(**kw))
    if obs is not None and obs.kind == 'space' and (jd_b - jd_a) / 365.25 > 20:
        raise InputError(tr('人工衛星の観測者では期間を 20 年以内にしてください'))
    if req.get('step_deg') is not None and jd_b - jd_a > server.SWEEP_MAX_DAYS:
        raise InputError(tr('位相を変えた一括計算では期間を 1 年以内にしてください'))
    if 'earth' in req['phenomena'] and obs is not None:
        from eclipsecalc.earth_shadow import check_period
        try:
            check_period(obs, jd_a, jd_b)
        except ObserverError as exc:
            raise InputError(str(exc))
    warnings = list(getattr(obs, 'notes', None) or [])
    if isinstance(obs, SGP4Observer):        # the re-entry that SGP4 predicts
        try:
            _, w = tle_decay(ctx, obs, jd_a, jd_b)
        except ObserverError as exc:
            raise InputError(str(exc))
        warnings += w
    elif isinstance(obs, (EphemerisObserver, PropagatedObserver)):      # the period the orbit data cover
        try:
            _, _, w = obs.limit_period(ctx, jd_a, jd_b, 0.0)
        except ObserverError as exc:
            raise InputError(str(exc))
        warnings += [x for x in w if x not in warnings]
    epoch = getattr(obs, 'epoch_jd', None)
    if epoch is None:
        return warnings
    more = obs.warnings_for(ctx, jd_a if abs(jd_a - epoch) > abs(jd_b - epoch) else jd_b)
    return warnings + [x for x in more if x not in warnings]


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
    """An ISO UTC time (ms) in the time zone, rounded to the second unless ``fmt`` has %f; a
    time in a leap second (23:59:60.500Z) keeps its second 60."""
    if not iso:
        return '—'
    leap = iso[16:19] == ':60'
    if leap:            # datetime cannot hold it: count it as second 59 and write 60
        iso = iso[:17] + '59' + iso[19:]
    t = _dt.datetime.fromisoformat(iso.replace('Z', '+00:00')) + _dt.timedelta(hours=tz)
    if '%f' not in fmt:
        carry = not leap and iso[17:19] == '59' and iso[19:21] >= '.5'
        t = (t + _dt.timedelta(microseconds=500_000)).replace(microsecond=0)
        if carry and _leap_minute(iso):      # 23:59:59.5 rounds to the leap second, not to 00:00:00
            t, leap = t - _dt.timedelta(seconds=1), True
        leap = leap and t.second == 59
    return t.strftime(fmt.replace('%S', '@S@')).replace('@S@', '60' if leap else f'{t.second:02d}')


def _leap_minute(iso):
    """Whether the UTC minute of ``iso`` ends with a leap second."""
    from eclipsecalc.context import get_context
    from eclipsecalc.timeutil import _leap_minute as leap_minute
    return leap_minute(get_context().ts, *(int(v) for v in (iso[0:4], iso[5:7], iso[8:10], iso[11:13], iso[14:16])))


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
    elif model == 'ccsds':
        items = _ccsds_items(o)
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
            # a sun-synchronous inclination is of the equator of date (the given one of the J2000 equator)
            inc = o['i_date_deg'] if o.get('sso') and o.get('i_date_deg') is not None else o['i_deg']
            items += [tr('傾斜角 {v}°', v=f'{inc:.2f}'), tr('昇交点赤経 {v}°', v=f'{o["raan_deg"]:.2f}')]
            if o.get('ltan_h') is not None:
                t = round(o['ltan_h'] * 60) % 1440
                items.append(tr('昇交点の地方時 {v}', v=f'{t // 60:02d}:{t % 60:02d}'))
            if o.get('sso'):
                items.append(tr('太陽同期'))
            src = o.get('rate_source') or {}        # only the rates given with the elements
            for key, text in (('n_rev_day', '平均運動 {v} 周回/日'), ('raan_rate_deg_day', '昇交点赤経の変化率 {v}°/日'),
                              ('argp_rate_deg_day', '近地点引数の変化率 {v}°/日')):
                if src.get(key) == 'input':
                    items.append(tr(text, v=f'{o[key]:.10g}'))
    if o.get('epoch'):
        items.append(tr('元期 {v}', v=_time(o['epoch'], 0, '%Y-%m-%d %H:%M:%S') + ' UTC'))
    return o['name'] + _paren(_sep().join(items))


def _ccsds_items(o):
    """The interpretation of a CCSDS message in the header of the tables."""
    msg = o.get('message', '')
    items = [' '.join(x for x in ('CCSDS', msg, o.get('version') or '') if x)]
    if msg == 'OMM':
        items[0] += ' (SGP4)'
    if o.get('perigee_km') is not None:
        items.append(tr('高度 {lo}〜{hi} km', lo=f'{o["perigee_km"]:,.0f}', hi=f'{o["apogee_km"]:,.0f}'))
    if o.get('period_min'):
        items.append(tr('周期 {p} 分', p=f'{o["period_min"]:.1f}'))
    if o.get('i_deg') is not None:
        items.append(tr('傾斜角 {v}°', v=f'{o["i_deg"]:.2f}'))
    if o.get('start') and o.get('end'):
        items.append(tr('軌道データ {period}', period=_span(_time(o['start'], 0, '%Y-%m-%d %H:%M'),
                                                         _time(o['end'], 0, '%Y-%m-%d %H:%M')) + ' UTC'))
        items.append(tr('状態 {n} 個', n=o.get('n_states')))
    if o.get('maneuvers'):
        items.append(tr('軌道制御 {n} 回', n=o['maneuvers']))
    od = o.get('od')
    if od:
        rms = [f'{k} {v["rms"]:.3g} {v["unit"]}' for k, v in od.get('types', {}).items() if v.get('rms') is not None]
        items.append(tr('TDM から軌道決定（観測 {n} 件、残差 RMS {rms}）', n=od.get('n_used'), rms=', '.join(rms)))
    return items


def _model(value, names):
    """The name of the preset radius ``value`` is (Japanese key), else 「指定値」."""
    return next((n for v, n in names if abs(value - v) < 0.05), '指定値')


def radius_line(params, phenomena):
    """The radii used, in one line: 「太陽半径 696,231 km（観測される縁, 1 AU で 959.96″）」 and
    those of the planets among ``phenomena``."""
    rs = params['sun_radius_km']
    items = [tr('太陽半径 {km} km（{model}, 1 AU で {arcsec}″）', km=f'{rs:,.0f}', model=tr(_model(rs, SUN_RADIUS_NAMES)),
                arcsec=f'{rs / C.AU_KM * C.RAD2ARCSEC:.2f}')]
    for body, text in (('mercury', '水星の半径 {km} km（{model}）'), ('venus', '金星の半径 {km} km（{model}）')):
        if body in phenomena:
            r = params[f'{body}_radius_km']
            items.append(tr(text, km=f'{r:,.1f}', model=tr(_model(r, PLANET_RADIUS_NAMES[body]))))
    return _sep().join(items)


def _header(res, args, extra=(), phenomena=()):
    period = [tr('期間: {start} 〜 {end}（UTC）', start=res['start'], end=res['end']),
              tr('暦 {name}', name=res['ephemeris'])]
    if res.get('delta_t_override') is not None:
        period.append(tr('ΔT {v} 秒（手動）', v=f'{res["delta_t_override"]:.1f}'))
    elif res.get('delta_t_mid_s') is not None:
        period.append(tr('ΔT 約 {v} 秒（期間中央）', v=f'{res["delta_t_mid_s"]:.1f}'))
    period += [tr('計算 {v} 秒', v=f'{res["elapsed_s"]:.1f}'), *extra]
    out = [tr('観測者: {obs}', obs=observer_text(res['observer'])), _sep().join(period),
           radius_line(res['params'], phenomena), tr('時刻: {tz}', tz=_tz_label(args.tz_hours))]
    return out + [tr('注意: {w}', w=w) for w in res.get('warnings') or []]


def _contacts_table(e, tz, ground):
    # a transit has no meaningful magnitude: the distance from the limb instead
    moon = e['body'] == 'moon'
    head = [tr('接触'), tr('時刻'), tr('食分') if moon else tr('縁からの距離'), tr('中心間'), tr('位置角 P'), 'X', 'Y']
    head += [tr('縁からの距離')] * moon + [tr('太陽半径 +1″ あたり（秒）')]
    n = len(head)
    head += ([tr('天頂角 V'), tr('太陽高度'), tr('太陽方位'), tr('見えるか')] if ground
             else [tr('衛星直下点'), tr('衛星高度'), tr('見えるか')])
    rows = []
    for c in e.get('contacts') or []:
        limb = _f(c.get('limb_dist'), '+.1f' if moon else '+.2f', '"')
        row = [tr(CONTACT_JA[c['label']]) if c['label'] in CONTACT_JA else c['label'],
               _time_on(c['time'], e['max'], tz, '%H:%M:%S.%f')[:-3],
               _f(c.get('magnitude'), '.4f') if moon else limb, _f(c.get('sep_arcsec'), '.1f', '"'),
               _f(c.get('pa'), '.1f', '°'), _f(c.get('hpc_x'), '.1f', '"'), _f(c.get('hpc_y'), '.1f', '"')]
        row += [limb] * moon + [_f(c.get('dt_drs'), '+.1f')]
        if ground:
            row += [_f(c.get('v_angle'), '.1f', '°'), _f(c.get('sun_alt'), '.2f', '°'),
                    _f(c.get('sun_az'), '.1f', '°') + (f' {_az(c["sun_az"])}' if c.get('sun_az') is not None else '')]
        else:
            row += [_latlon(c.get('sat_lat'), c.get('sat_lon')), _f(c.get('sat_alt_km'), ',.0f', ' km')]
        rows.append(row + ['○' if c.get('visible') else '×'])
    return _table(head, rows, right=set(range(2, n + 2)))


def _visible_intervals(e, tz):
    """The parts of an event seen from a spacecraft (between the orbit nights), in one line."""
    iv = e.get('visible_intervals') or []
    if not iv:
        return []
    fmt = '%H:%M:%S.%f'
    return [tr('見えている時間帯（{n} 回）: {list}', n=len(iv), list=', '.join(
        _span(_time_on(a, e['max'], tz, fmt)[:-3], _time_on(b, e['max'], tz, fmt)[:-3]) for a, b in iv))]


def search_table(res, args, phenomena=PHENOMENA):
    lines = _event_table(res, args, phenomena)
    if res.get('earth_shadow') is not None:
        lines += shadow_table(res, args)
    return lines


def _event_table(res, args, phenomena):
    evs = res['events']
    kind = res['observer']['kind']
    lines = _header(res, args, phenomena=phenomena)
    if not any(p in PHENOMENA for p in phenomena):      # the Earth's shadow only
        return lines
    lines += ['', tr('{n} 件の現象が見つかりました', n=len(evs)) if evs else tr('この期間に見られる現象はありません')]
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
            if kind == 'space':
                lines += _visible_intervals(e, tz)
        lines += ['', tr('X・Y は太陽の北を上にした太陽の中心からの位置（″。X は太陽の西 = 画像の右、Y は太陽の北）、'
                         '位置角 P は天の北から東回り、縁からの距離は太陽の縁から月・惑星の中心までの角距離'
                         '（負なら中心が太陽面の内側）、太陽半径 +1″ あたりは太陽の視半径が 1″ 大きいときの'
                         '接触の時刻の変化（秒）です。')]
    return lines


def sweep_table(res, args, phenomena=PHENOMENA):
    groups = res['groups']
    tz = args.tz_hours
    lines = _header(res, args, [tr('平均近点角を {step}° ずつ変えた {n} 通り', step=f'{res["step_deg"]:g}',
                                   n=len(res['phases']))], phenomena)
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


def _clock(iso, tz, ref=None):
    """A time of the Earth's shadow: the date and time in the time zone, the date left out when it
    is that of ``ref``."""
    return _time_on(iso, ref, tz, '%H:%M:%S') if ref else _time(iso, tz, '%Y-%m-%d %H:%M')


def shadow_table(res, args):
    """The Earth's shadow on the satellite (--phenomena earth): per height of the absorbing
    atmosphere, its seasons and, with --detail, every pass for the height of --atm."""
    sh, tz = res['earth_shadow'], args.tz_hours
    h0 = sh['atm_km']
    lines = ['', '■ ' + tr('地球の影（衛星から見て太陽が地球に隠れる時間。遮蔽高度 {h} km で太陽の中心が隠れている時間）',
                          h=f'{h0:g}')]
    levels = sh['levels']
    if not any(lv['n'] for lv in levels):
        return lines + [tr('この期間には、どの遮蔽高度でも地球の影に入りません。')]
    head = [tr(k) for k in ('遮蔽高度', '影に入る回数', '最長', '最長の日', '影の合計', '影の時間の割合', '季節の数')]
    rows = []
    for lv in levels:
        rows.append([f'{lv["atm_km"]:g} km' + (' *' if lv['atm_km'] == h0 else ''), tr('{n} 回', n=f'{lv["n"]:,}'),
                     _dur(lv['max_s']), _time(lv['max_at'], tz, '%Y-%m-%d') if lv['max_at'] else '—',
                     _dur(lv['total_s']), f'{100 * lv["fraction"]:.1f}%', len(lv['seasons'])])
    lines += _table(head, rows, right={1, 2, 4, 5, 6})
    head = [tr(k) for k in ('遮蔽高度', '季節の始まり', '季節の終わり', '日数', '影に入る回数', '最長', '最長の日', '最短')]
    rows = []
    for lv in levels:
        for s in lv['seasons']:
            rows.append([f'{lv["atm_km"]:g} km', ('… ' if s['cut_start'] else '') + _clock(s['start'], tz),
                         _clock(s['end'], tz) + (' …' if s['cut_end'] else ''), f'{s["days"]:.1f}',
                         tr('{n} 回', n=f'{s["n"]:,}'), _dur(s['max_s']), _time(s['max_at'], tz, '%Y-%m-%d'),
                         _dur(s['min_s'])])
    if rows:
        lines += ['', tr('影の季節（数日以上あいだが空くまでを 1 つの季節とします）:')] + _table(head, rows, right={3, 4, 5, 7})
    lines += ['', tr('* は --atm の遮蔽高度（周回ごとの値はこの高さ）、… は期間の端で途切れていることを示します。'
                     '遮蔽高度は、地球の縁からその高さまでの大気が太陽を隠すとする高さです'
                     '（目安: 可視光 0 km、軟X線 約 140 km、極紫外線 約 250 km）。')]
    if not args.detail:
        return lines + [tr('周回ごとの影に入る・出る時刻は --detail、--format csv または --format json で出力できます。')]
    head = [tr(k) for k in ('日付', '欠け始め', '中心が隠れる', '完全に隠れる', '出始め', '中心が出る', '全部出る',
                            '長さ', '深さ', '影の中央の衛星直下点')]
    rows = []
    for p in sh['passes']:
        ref = p['partial_start']
        rows.append([_time(ref, tz, '%Y-%m-%d'), _clock(ref, tz, ref), _clock(p['start'], tz, ref) if p['start'] else '—',
                     _clock(p['total_start'], tz, ref) if p['total_start'] else '—',
                     _clock(p['total_end'], tz, ref) if p['total_end'] else '—',
                     _clock(p['end'], tz, ref) if p['end'] else '—', _clock(p['partial_end'], tz, ref),
                     _dur(p['duration_s']) if p['duration_s'] is not None else '—', f'{p["depth_deg"]:+.2f}°',
                     _latlon(p['sat_lat'], p['sat_lon'])])
    lines += ['', tr('周回ごとの地球の影（遮蔽高度 {h} km、{n} 回）:', h=f'{h0:g}', n=f'{len(rows):,}')]
    lines += _table(head, rows, right={7, 8})
    lines += ['', tr('欠け始め・全部出るは太陽の一部、中心が隠れる・出るは太陽の中心、完全に隠れる・出始めは太陽の全体が'
                     '地球（＋遮蔽高度）に隠れる・出る時刻、長さは中心が隠れている時間、深さは影の中央で太陽の中心が'
                     '地球の縁（＋遮蔽高度）より内側にある角度です（負なら中心は隠れない）。')]
    return lines


SHADOW_CSV = ('start', 'end', 'duration_s', 'partial_start', 'partial_end', 'partial_s', 'total_start', 'total_end',
              'total_s', 'mid', 'depth_deg', 'sat_lat', 'sat_lon', 'sat_alt_km', 'cut_start', 'cut_end')


def shadow_csv(res):
    # one row per pass for the height of --atm (as the web UI's CSV of the Earth's shadow)
    sh = res['earth_shadow']
    rows = [['atm_km'] + [k + '_utc' if k in ('start', 'end', 'partial_start', 'partial_end', 'total_start',
                                              'total_end', 'mid') else k for k in SHADOW_CSV]]
    for p in sh['passes']:
        rows.append([sh['atm_km']] + [int(p[k]) if k.startswith('cut_') else p.get(k) for k in SHADOW_CSV])
    return rows


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


CONTACT_CSV = ('label', 'time', 'hpc_x', 'hpc_y', 'pa', 'pa_solar', 'p_angle', 'sep_arcsec', 'limb_dist', 'limb_depth',
               'magnitude', 'dt_drs', 'dt_drb', 'visible')


def contacts_csv(res):
    # --detail: one row per contact of each event (the events of the whole Earth have none)
    rows = [['event_max_utc'] + [k.replace('time', 'time_utc') for k in CONTACT_CSV]]
    for e in res['events']:
        for c in e.get('contacts') or []:
            rows.append([e['max']] + [int(c[k]) if k == 'visible' else c.get(k) for k in CONTACT_CSV])
    return rows


AT_CSV = ('input', 'time', 'jd_tt', 'p_angle', 'hpc_x', 'hpc_y', 'hpc_vx', 'hpc_vy', 'pa', 'pa_solar', 'xi', 'eta',
          'sep_arcsec', 'sep_rate', 'limb_dist', 'limb_depth', 'rho_s', 'rho_b', 'rho_b_int', 'magnitude',
          'obscuration', 'visible', 'vis')
AT_CSV_EXTRAS = ('sun_alt', 'sun_az', 'parallactic', 'v_angle', 'sat_lat', 'sat_lon', 'sat_alt_km',
                 'earth_sep_deg', 'earth_pa', 'earth_radius_deg', 'earth_atm_deg', 'dist_earth_km',
                 'measured_x', 'measured_y', 'oc_x', 'oc_y', 'oc_dt')


def at_csv(res):
    rows = res['at']
    keys = list(AT_CSV) + [k for k in AT_CSV_EXTRAS if any(k in r for r in rows)]
    return [[k.replace('time', 'time_utc') for k in keys]] + [
        [int(r[k]) if k == 'visible' else r.get(k) for k in keys] for r in rows]


def at_table(res, args):
    tz = args.tz_hours
    kind = res['observer']['kind']
    rows_in = res['at']
    items = [tr(BODY_JA[res['body']]), tr('暦 {name}', name=res['ephemeris'])]
    if res.get('delta_t_override') is not None:
        items.append(tr('ΔT {v} 秒（手動）', v=f'{res["delta_t_override"]:.1f}'))
    else:
        items.append(tr('ΔT {v} 秒', v=f'{res["delta_t_mid_s"]:.1f}'))
    items.append(tr('計算 {v} 秒', v=f'{res["elapsed_s"]:.1f}'))
    when = tr('時刻: {tz}', tz=_tz_label(tz))
    if res.get('exposure_s'):
        when += _sep() + tr('露光の中央（露光時間 {s} 秒の半分を足した時刻）', s=f'{res["exposure_s"]:g}')
    lines = [tr('観測者: {obs}', obs=observer_text(res['observer'])), _sep().join(items),
             radius_line(res['params'], [res['body']]), when]
    lines += [tr('注意: {w}', w=w) for w in res.get('warnings') or []]
    measured = any('oc_x' in r for r in rows_in)
    head = [tr('時刻'), 'X', 'Y', tr('縁からの距離'), tr('深さ'), tr('位置角（太陽の北から）'), tr('太陽の P 角')]
    head += [tr('太陽高度')] if kind == 'ground' else [tr('衛星直下点'), tr('衛星高度')] if kind == 'space' else []
    head += [tr('見えるか')]
    n = len(head)
    head += ['O−C X', 'O−C Y', tr('O−C 時間（秒）')] if measured else []
    rows = []
    for r in rows_in:
        row = [_time(r['time'], tz, '%Y-%m-%d %H:%M:%S.%f')[:-3], _f(r['hpc_x'], '.2f', '"'), _f(r['hpc_y'], '.2f', '"'),
               _f(r['limb_dist'], '+.2f', '"'), _f(r['limb_depth'], '+.3f'), _f(r['pa_solar'], '.2f', '°'),
               _f(r['p_angle'], '.2f', '°')]
        if kind == 'ground':
            row.append(_f(r.get('sun_alt'), '.2f', '°'))
        elif kind == 'space':
            row += [_latlon(r.get('sat_lat'), r.get('sat_lon')), _f(r.get('sat_alt_km'), ',.0f', ' km')]
        row.append('○' if r['visible'] else '×')
        if measured:
            row += [_f(r.get('oc_x'), '+.2f', '"'), _f(r.get('oc_y'), '+.2f', '"'), _f(r.get('oc_dt'), '+.2f')]
        rows.append(row)
    lines += [''] + _table(head, rows, right=set(range(1, len(head))) - {n - 1})
    lines += ['', tr('X・Y は太陽の北を上にした太陽の中心からの位置（″。X は太陽の西 = 画像の右、Y は太陽の北。'
                     'ひので・SDO などの画像の座標と同じ向き）、縁からの距離は太陽の縁から中心までの角距離'
                     '（負なら中心が太陽面の内側）、深さは −1 で外接、0 で中心が縁の上、+1 で内接、'
                     'P 角は太陽の自転軸の北の位置角（天の北から東回り）です。')]
    if measured:
        lines.append(tr('O−C は測った位置 − 計算の位置、O−C 時間は動きの向きに測った位置が計算より先にある時間です'
                        '（正なら観測が計算より進んでいる）。'))
    return lines


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
    at = req.get('times') is not None
    if not at or req.get('start') is not None:
        check_utc(req['start'], 'start')
        check_utc(req['end'], 'end')
    ctx = _context(req)
    observer, obs = check_observer(ctx, req['observer'])
    base = {'ok': True, 'tool': 'solar_eclipse_calc cli.py', 'version': __version__, 'lang': i18n.current(),
            'request': req}
    if at:
        return run_at(args, req, ctx, observer, obs, base, log)
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
        rows = (sweep_csv(res) if sweep else shadow_csv(res) if res.get('earth_shadow') is not None
                else contacts_csv(res) if args.detail else search_csv(res))
        _emit(_csv_text(rows), args, csv_out=True)
    else:
        table = sweep_table if sweep else search_table
        _emit('\n'.join(table(res, args, req['phenomena'])) + '\n', args)
    return 0


def run_at(args, req, ctx, observer, obs, base, log):
    """--at / --at-file (or "times" in the JSON): the geometry at the times, without an event."""
    from eclipsecalc import server
    from eclipsecalc.observers import ObserverError
    from eclipsecalc.timeutil import iso_from_jd, parse_utc
    with _api_errors():
        jd, _ = server.parse_at_request(ctx, server.AtRequest(**{k: req[k] for k in ('times', 'measured', 'exposure_s')
                                                          if k in req}))
    if req.get('start') is not None:      # a period given as well: the times must be in it
        a, b = parse_utc(ctx, req['start']), parse_utc(ctx, req['end'])
        bad = next((t for t, j in zip(req['times'], jd) if not a <= j <= b), None)
        if bad is not None:
            raise InputError(tr('時刻「{text}」が期間（{start}〜{end}）の外です', text=bad.strip(),
                                start=req['start'], end=req['end']))
    if args.dry_run:
        try:
            warnings = server.at_observer_checks(ctx, obs, jd)
        except ObserverError as exc:
            raise InputError(str(exc))
        times = [{'input': t, 'time': iso_from_jd(ctx, j), 'jd_tt': float(j)} for t, j in zip(req['times'], jd)]
        if args.format == 'json':
            _emit(_json_text(dict(base, dry_run=True, observer=observer, warnings=warnings, at_times=times)), args)
        else:
            _emit(tr('観測者: {obs}', obs=observer_text(observer)) + '\n'
                  + tr('現象: {list}', list=', '.join(req['phenomena'])) + '\n'
                  + ''.join(tr('時刻 {input} → {time}', input=t['input'], time=t['time']) + '\n' for t in times)
                  + ''.join(tr('注意: {w}', w=w) + '\n' for w in warnings)
                  + tr('計算条件（--request で使える JSON）:') + '\n' + _json_text(req), args)
        return 0
    t0 = time.time()
    log(tr('計算しています…'))
    kw = {k: req[k] for k in ('phenomena', 'observer', 'settings', 'times', 'measured', 'exposure_s') if k in req}
    with _api_errors():
        with contextlib.redirect_stderr(io.StringIO() if os.environ.get('ECLIPSECALC_DEBUG') != '1' else sys.stderr):
            res = server.at_times(server.AtSearchRequest(**kw))
    log(tr('完了（{v} 秒）', v=f'{time.time() - t0:.1f}'))
    if args.format == 'json':
        _emit(_json_text(dict(base, **res)), args)
    elif args.format == 'csv':
        for w in res.get('warnings') or []:
            log(tr('注意: {w}', w=w))
        _emit(_csv_text(at_csv(res)), args, csv_out=True)
    else:
        _emit('\n'.join(at_table(res, args)) + '\n', args)
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
