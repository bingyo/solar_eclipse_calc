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
    python cli.py --city 東京 --start 2026-01-01 --end 2056-01-01 --format json --detail

Exit status: 0 = done, 1 = the input could not be calculated (message on
stderr, or {"ok": false, ...} on stdout with --format json), 2 = wrong options.
"""
import argparse
import contextlib
import csv
import datetime as _dt
import io
import json
import os
import re
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PHENOMENA = ('moon', 'mercury', 'venus')
BODY_JA = {'moon': '日食', 'mercury': '水星の太陽面通過', 'venus': '金星の太陽面通過'}
TYPE_JA = {'total': '皆既日食', 'annular': '金環日食', 'hybrid': '金環皆既日食', 'partial': '部分日食'}
CONTACT_JA = {'C1': '第1接触', 'C2': '第2接触', 'MAX': '最大', 'C3': '第3接触', 'C4': '第4接触'}
AZ_NAMES = ['北', '北北東', '北東', '東北東', '東', '東南東', '南東', '南南東',
            '南', '南南西', '南西', '西南西', '西', '西北西', '北西', '北北西']
ORBIT_ARGS = ('epoch', 'a', 'e', 'i', 'raan', 'alt', 'perigee_alt', 'apogee_alt', 'sso', 'ltan')
ORBIT_ONLY = ('argp', 'm', 'no_j2')

EXAMPLES = '''観測者は次のどれか 1 つで指定します:
  軌道6要素     --epoch --a --e --i --raan（と --argp --m）
  計画中の軌道  --epoch と、大きさ --alt または --perigee-alt --apogee-alt、傾斜角 --i または --sso、
               軌道面 --raan または --ltan（軌道6要素の指定と組み合わせも可）
  TLE          --tle ファイル（- で標準入力）または --norad 番号（CelesTrak から最新の TLE を取得）
  静止衛星      --geo-lon 経度
  JPL Horizons --horizons ID（例 -170 = JWST）
  NASA SSCWeb  --sscweb ID（例 hinode。過去の軌道）
  地上の地点    --lat --lon（と --elev）または --city 地点名
  地球全体      --global
  JSON で指定   --request ファイル（Web API と同じ形。--format json の出力の "request" をそのまま使えます）

例:
  python cli.py --epoch 2027-07-30T12:00:00 --a 7058.1 --e 0.0012 --i 98.13 --raan 220.5 \\
      --argp 90 --m 45 --start 2027-07-25 --end 2027-08-10 --tz 9
  python cli.py --epoch 2027-01-01T00:00 --alt 680 --sso --ltan 18:00 --sweep 10 \\
      --start 2027-07-01 --end 2027-09-01
  python cli.py --norad 25544 --start 2026-10-01 --end 2027-10-01 --tz 9
  python cli.py --city 東京 --start 2026-01-01 --end 2056-01-01 --phenomena moon --tz 9 --detail
  python cli.py --global --start 2026-01-01 --end 2036-01-01 --format csv -o list.csv

生成 AI から使うときは --format json を付けてください（詳しくは docs/cli.md）。
終了コード: 0 = 計算した, 1 = 入力を計算できなかった, 2 = 引数の誤り
'''


class UsageError(Exception):
    pass


class InputError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(message)


def build_parser():
    ap = _Parser(
        prog='cli.py', allow_abbrev=False, epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='人工衛星・探査機・地上の地点・地球全体から見える日食と水星・金星の太陽面通過を計算します（画面と同じ計算）。')
    g = ap.add_argument_group('観測者: 軌道要素で指定した衛星（GCRS・J2000 赤道基準の平均要素。J2 永年摂動つき）')
    g.add_argument('--epoch', metavar='UTC', help='元期（軌道情報の日時, UTC）。例 2027-07-30T12:00:00')
    g.add_argument('--a', type=float, metavar='KM', help='軌道長半径 (km)。--e と組で指定')
    g.add_argument('--e', type=float, metavar='E', help='軌道離心率（0 以上 1 未満）')
    g.add_argument('--alt', type=float, metavar='KM', help='円軌道の高度 (km)（--a --e の代わり）')
    g.add_argument('--perigee-alt', type=float, metavar='KM', help='近地点高度 (km)。--apogee-alt と組で（--a --e の代わり）')
    g.add_argument('--apogee-alt', type=float, metavar='KM', help='遠地点高度 (km)')
    g.add_argument('--i', type=float, metavar='DEG', help='軌道傾斜角 (°)')
    g.add_argument('--sso', action='store_true', help='太陽同期軌道（傾斜角を軌道の大きさから自動で決める。--i の代わり）')
    g.add_argument('--raan', type=float, metavar='DEG', help='昇交点赤経 (°)')
    g.add_argument('--ltan', metavar='HH:MM', help='昇交点の地方時（平均太陽時。例 18:00 や 10.5。--raan の代わり）')
    g.add_argument('--argp', type=float, metavar='DEG', help='近地点引数 (°)（既定 0）')
    g.add_argument('--m', type=float, metavar='DEG', help='平均近点角 (°)（既定 0）')
    g.add_argument('--no-j2', action='store_true', help='地球の扁平 (J2) による軌道面の歳差を考慮しない')

    t = ap.add_argument_group('観測者: TLE（SGP4 で軌道を計算）')
    t.add_argument('--tle', metavar='FILE', help='TLE（2 行または名前付き 3 行）のファイル。- で標準入力から読む')
    t.add_argument('--norad', type=int, metavar='N', help='NORAD カタログ番号。CelesTrak から最新の TLE を取得する（要インターネット）')

    o = ap.add_argument_group('観測者: そのほかの人工衛星・探査機（--horizons と --sscweb は要インターネット）')
    o.add_argument('--geo-lon', type=float, metavar='DEG', help='理想静止衛星（高度 35,786 km）の経度 (°)。東経 +／西経 −')
    o.add_argument('--horizons', metavar='ID', help='JPL Horizons の天体 ID（例 -170 JWST、-21 SOHO、-125544 ISS）')
    o.add_argument('--horizons-step', type=int, default=60, metavar='MIN',
                   help='Horizons から取得する位置の刻み (分)（既定 60。地球近傍の衛星は 1〜2）')
    o.add_argument('--sscweb', metavar='ID', help='NASA SSCWeb の衛星 ID（例 hinode, iris, iss）。過去の軌道。--list-sscweb で一覧')

    gr = ap.add_argument_group('観測者: 地上の地点（WGS84）・地球全体')
    gr.add_argument('--lat', type=float, metavar='DEG', help='緯度 (°)。北緯 +／南緯 −')
    gr.add_argument('--lon', type=float, metavar='DEG', help='経度 (°)。東経 +／西経 −')
    gr.add_argument('--elev', type=float, metavar='M', help='標高 (m)（既定 0）')
    gr.add_argument('--city', metavar='NAME', help='よく使う地点の名前（例 東京、ルクソール）。--list-cities で一覧')
    gr.add_argument('--global', dest='global_', action='store_true',
                    help='地球全体: 世界のどこかで見える日食（最大食の地点・γ・中心食帯の幅など）と地心での太陽面通過')

    rq = ap.add_argument_group('JSON での指定')
    rq.add_argument('--request', metavar='FILE',
                    help='計算条件の JSON（- で標準入力）。{"observer": {...}, "start", "end", "phenomena", "settings", "step_deg"}。'
                         'ほかの観測者・期間・詳細設定の引数とは併用できません')

    ap.add_argument('--name', help='観測者の名前（表示用）')

    p = ap.add_argument_group('期間と現象')
    p.add_argument('--start', metavar='DATE', help='開始日（UTC）。例 2027-07-25')
    p.add_argument('--end', metavar='DATE', help='終了日（UTC）。例 2027-08-10')
    p.add_argument('--phenomena', metavar='LIST',
                   help='計算する現象をカンマ区切りで: moon（日食）, mercury, venus（既定 すべて）')
    p.add_argument('--sweep', type=float, metavar='STEP',
                   help='軌道要素・TLE の衛星のみ: 平均近点角（軌道上の位置）をこの刻み (°, 5〜90) で変えて一括計算する'
                        '（打ち上げ前や、TLE の元期から何か月も先の検討。期間は 1 年以内）')

    s = ap.add_argument_group('詳細設定')
    s.add_argument('--ephemeris', metavar='FILE', help='暦（既定 de440s.bsp = 1849〜2150 年。de440.bsp なら 1550〜2650 年）')
    s.add_argument('--delta-t', type=float, metavar='SEC', help='ΔT（TT−UT, 秒）を固定値で指定（既定は自動）')
    s.add_argument('--sun-radius', metavar='KM|iau2015|nasa',
                   help='太陽の半径。iau2015（既定 695,700 km）、nasa（959.63″ = 695,992 km）または km')
    s.add_argument('--atm', type=float, metavar='KM', help='人工衛星: 地球大気の遮蔽高度 (km)（既定 0）')
    s.add_argument('--min-sun-alt', type=float, metavar='DEG', help='地上: 太陽中心がこの高度以上で「見える」とする (°)（既定 0）')
    s.add_argument('--no-refraction', action='store_true', help='地上: 大気差を太陽高度に反映しない')
    s.add_argument('--include-invisible', action='store_true', help='見えない現象（夜間・地球の陰）も含める')

    out = ap.add_argument_group('出力')
    out.add_argument('--format', choices=('table', 'csv', 'json'), default='table',
                     help='出力形式（既定 table。生成 AI からは json）')
    out.add_argument('--detail', action='store_true',
                     help='各現象の接触（第1〜第4接触・最大）の時刻・太陽高度・位置角なども出力する')
    out.add_argument('--dry-run', action='store_true',
                     help='計算せず、入力の解釈（衛星の軌道の大きさ・周期・傾斜角など）だけを出力する')
    out.add_argument('--tz', default='0', metavar='HOURS',
                     help='表で使う時刻の UTC からのずれ。例 9 や +09:00 で日本時間（既定 UTC。CSV・JSON は常に UTC）')
    out.add_argument('-o', '--output', metavar='FILE', help='結果をファイルに保存する（既定は標準出力）')
    out.add_argument('-q', '--quiet', action='store_true', help='経過の表示（標準エラー出力）を出さない')
    out.add_argument('--list-cities', action='store_true', help='--city で使える地点の一覧を表示して終わる')
    out.add_argument('--list-sscweb', action='store_true', help='NASA SSCWeb の衛星 ID の一覧を表示して終わる（要インターネット）')
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
    args = build_parser().parse_args(argv)
    if args.list_cities or args.list_sscweb:
        return args
    args.tz_hours = _parse_tz(args.tz)
    modes = _modes(args)
    if len(modes) != 1:
        raise UsageError('観測者を 1 つだけ指定してください: 軌道要素（--epoch …）、TLE（--tle / --norad）、'
                         '静止衛星（--geo-lon）、JPL Horizons（--horizons）、NASA SSCWeb（--sscweb）、'
                         '地上の地点（--lat --lon / --city）、地球全体（--global）、JSON（--request）'
                         + (f'（指定されたもの: {", ".join(modes)}）' if modes else ''))
    args.mode = modes[0]
    if args.mode == 'request':
        clash = [k for k in ('name', 'start', 'end', 'phenomena', 'sweep', 'ephemeris', 'delta_t', 'sun_radius',
                             'atm', 'min_sun_alt', 'no_refraction', 'include_invisible') if _given(args, k)]
        if clash:
            raise UsageError(f'--request と {" ".join("--" + k.replace("_", "-") for k in clash)} は併用できません'
                             '（JSON の中で指定してください）')
        return args
    if args.mode == 'kepler':
        _check_orbit(args)
    else:
        extra = [f'--{k.replace("_", "-")}' for k in ORBIT_ONLY if _given(args, k)]
        if extra:
            raise UsageError(f'{" ".join(extra)} は軌道要素で指定した衛星だけで使えます')
    if _given(args, 'sweep') and args.mode not in ('kepler', 'tle'):
        raise UsageError('--sweep は軌道要素または TLE で指定した衛星だけで使えます')
    if args.mode == 'tle' and _given(args, 'tle') and _given(args, 'norad'):
        raise UsageError('--tle と --norad はどちらか一方だけ指定してください')
    if args.mode == 'ground':
        if _given(args, 'city') and (_given(args, 'lat') or _given(args, 'lon')):
            raise UsageError('--city と --lat/--lon はどちらか一方だけ指定してください')
        if not _given(args, 'city') and not (_given(args, 'lat') and _given(args, 'lon')):
            raise UsageError('地上の地点では --lat と --lon の両方を指定してください')
    if args.start is None or args.end is None:
        raise UsageError('期間を --start と --end で指定してください（例 --start 2027-07-25 --end 2027-08-10）')
    if args.sweep is not None and not 5 <= args.sweep <= 90:
        raise UsageError('--sweep（位相の刻み）は 5〜90° で指定してください')
    if args.sweep is not None and args.detail:
        raise UsageError('--detail は --sweep と同時には使えません')
    return args


def _check_orbit(args):
    if args.epoch is None:
        raise UsageError('軌道要素では元期 --epoch（軌道情報の日時, UTC）を指定してください')
    sizes = [n for n, ok in (('--a --e', _given(args, 'a') or _given(args, 'e')), ('--alt', _given(args, 'alt')),
                             ('--perigee-alt --apogee-alt',
                              _given(args, 'perigee_alt') or _given(args, 'apogee_alt'))) if ok]
    if len(sizes) != 1:
        raise UsageError('軌道の大きさは --a と --e、--alt、--perigee-alt と --apogee-alt のどれか 1 つで指定してください')
    if sizes[0] == '--a --e':
        if args.a is None or args.e is None:
            raise UsageError('軌道長半径 --a と軌道離心率 --e は両方指定してください（円軌道なら --e 0）')
        if not args.a > 0:
            raise UsageError('--a（軌道長半径）は正の値で指定してください')
        if not 0 <= args.e < 1:
            raise UsageError('--e（軌道離心率）は 0 以上 1 未満で指定してください')
    if sizes[0] == '--perigee-alt --apogee-alt' and (args.perigee_alt is None or args.apogee_alt is None):
        raise UsageError('--perigee-alt と --apogee-alt は両方指定してください')
    if _given(args, 'i') == args.sso:
        raise UsageError('軌道傾斜角は --i または --sso（太陽同期軌道）のどちらか一方で指定してください')
    if _given(args, 'raan') == _given(args, 'ltan'):
        raise UsageError('軌道面は --raan（昇交点赤経）または --ltan（昇交点の地方時）のどちらか一方で指定してください')
    if _given(args, 'ltan'):
        args.ltan_h = _parse_hours(args.ltan, '--ltan（昇交点の地方時）は 18:00 や 10.5 のように指定してください')


def _parse_hours(text, message):
    m = re.fullmatch(r'\s*([+-]?)(\d{1,2}):(\d{2})\s*', text)
    if m:
        h = int(m.group(2)) + int(m.group(3)) / 60
        return -h if m.group(1) == '-' else h
    try:
        return float(text)
    except ValueError:
        raise UsageError(message)


def _parse_tz(text):
    h = _parse_hours(text, '--tz は 9、+09:00、-5 のように UTC からのずれ（時間）で指定してください')
    if not -14 <= h <= 14:
        raise UsageError('--tz は -14〜+14 時間で指定してください')
    return h


# ---------------------------------------------------------------------------
# request
# ---------------------------------------------------------------------------
def _find_city(name):
    from eclipsecalc.presets import cities
    cs = cities()
    hit = [c for c in cs if c['name'] == name] or [c for c in cs if c['name'].startswith(name)]
    if len(hit) != 1:
        raise InputError(f'地点「{name}」が{"一つに決まりません" if hit else "見つかりません"}。'
                         '--list-cities で使える地点を確認するか、--lat --lon で指定してください')
    return hit[0]


def _read_text(path, what):
    try:
        if path == '-':
            return sys.stdin.read()
        with open(path, encoding='utf-8-sig', errors='replace') as f:
            return f.read()
    except OSError as exc:
        raise InputError(f'{what}のファイルを読めません: {exc}')


def _parse_tle(text):
    """TLE text (two lines, optionally preceded by a name) -> (line1, line2, name)."""
    lines = [s.strip() for s in text.lstrip('﻿').splitlines() if s.strip()]
    i1 = next((k for k, s in enumerate(lines) if s.startswith('1 ')), -1)
    if i1 < 0 or i1 + 1 >= len(lines) or not lines[i1 + 1].startswith('2 '):
        raise InputError('TLE の 1 行目（"1 "で始まる）と 2 行目（"2 "で始まる）が見つかりません')
    return lines[i1], lines[i1 + 1], lines[i1 - 1] if i1 > 0 else ''


def _celestrak(norad, name, log):
    from eclipsecalc.observers import fetch_tle_celestrak
    log(f'CelesTrak から NORAD {norad} の最新 TLE を取得しています…')
    try:
        nm, l1, l2 = fetch_tle_celestrak(norad)
    except Exception as exc:
        raise InputError(f'CelesTrak から NORAD {norad} の TLE を取得できませんでした: {exc}')
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
        l1, l2, nm = _parse_tle(_read_text(args.tle, 'TLE '))
        return {'type': 'tle', 'line1': l1, 'line2': l2, 'name': name or nm}
    if args.mode == 'geo':
        return {'type': 'geo', 'lon': args.geo_lon, 'name': name}
    if args.mode == 'horizons':
        return {'type': 'horizons', 'command': args.horizons, 'step_min': args.horizons_step, 'name': name}
    if args.mode == 'sscweb':
        return {'type': 'sscweb', 'id': args.sscweb, 'name': name}
    if args.mode == 'global':
        return {'type': 'global'}
    if args.city is not None:
        c = _find_city(args.city)
        return {'type': 'ground', 'lat': c['lat'], 'lon': c['lon'],
                'elevation_m': c['elevation_m'] if args.elev is None else args.elev, 'name': name or c['name']}
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
            s['sun_radius_km'] = presets.get(args.sun_radius.lower()) or float(args.sun_radius)
        except ValueError:
            raise InputError('--sun-radius は iau2015、nasa または km の数値で指定してください')
    if args.atm is not None:
        s['earth_atm_km'] = args.atm
    if args.min_sun_alt is not None:
        s['min_sun_alt_deg'] = args.min_sun_alt
    if args.no_refraction:
        s['refraction'] = False
    if args.include_invisible:
        s['include_invisible'] = True
    return s


def build_request(args, log):
    """-> the request in the web API's shape (also echoed in the JSON output)."""
    if args.mode == 'request':
        try:
            req = json.loads(_read_text(args.request, '計算条件の JSON '))
        except json.JSONDecodeError as exc:
            raise InputError(f'計算条件の JSON を読めません: {exc}')
        if not isinstance(req, dict) or not isinstance(req.get('observer'), dict):
            raise InputError('計算条件の JSON には "observer"（観測者のオブジェクト）が必要です')
        unknown = set(req) - {'observer', 'start', 'end', 'phenomena', 'settings', 'step_deg'}
        if unknown:
            raise InputError(f'計算条件の JSON に不明な項目があります: {", ".join(sorted(unknown))}')
        for k in ('start', 'end'):
            if not isinstance(req.get(k), str):
                raise InputError(f'計算条件の JSON に "{k}"（UTC の日付の文字列）が必要です')
        req = dict(req)
        req.setdefault('phenomena', list(PHENOMENA))
        req.setdefault('settings', {})
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
        raise InputError(f'現象は moon, mercury, venus から選んでください（指定: {", ".join(map(str, bad)) or "なし"}）')
    req['phenomena'] = ph
    if req.get('step_deg') is not None and req['observer'].get('type') not in ('kepler', 'tle'):
        raise InputError('平均近点角を変えた一括計算（step_deg）は軌道要素または TLE で指定した衛星だけで使えます')
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
        raise InputError(f'JPL 暦 {eph} を用意できませんでした: {exc}')
    dt = s.get('delta_t')
    return get_context(eph, None if dt in (None, '', 'auto') else float(dt))


_DATE_RE = re.compile(r'(-?\d{1,4})-(\d{1,2})-(\d{1,2})(?:[T ](\d{1,2}):(\d{2})(?::(\d{2}(?:\.\d*)?))?)?Z?')


def check_utc(text, what):
    """Reject dates the calculation would silently roll over (e.g. month 13)."""
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
        raise InputError(f'{what}「{text}」を解釈できません（UTC。例 2027-07-30 または 2027-07-30T12:00:00）')


def check_observer(ctx, spec):
    """Build the observer once to report input errors cleanly -> (description, observer)."""
    from eclipsecalc.observers import ObserverError, build_observer
    from eclipsecalc.timeutil import iso_from_jd, parse_utc
    if spec.get('type') == 'global':
        return {'kind': 'global', 'name': '地球全体'}, None
    if spec.get('type') == 'kepler':
        check_utc(spec.get('epoch', ''), '元期')
    try:
        obs = build_observer(ctx, spec, parse_time=lambda s: parse_utc(ctx, s))
    except ObserverError as exc:
        raise InputError(str(exc))
    except KeyError as exc:
        raise InputError(f'観測者（type: {spec.get("type")}）の指定に {exc} がありません')
    except Exception as exc:
        raise InputError(f'観測者（type: {spec.get("type")}）の指定を解釈できません: {exc}')
    d = obs.describe()
    if d.get('epoch_jd_tt'):
        d['epoch'] = iso_from_jd(ctx, d['epoch_jd_tt'])
    return d, obs


def preflight(req, obs):
    """For --dry-run: the checks the calculation would make on the period, and
    the warnings it would give (an old TLE is judged at the end of the period
    farthest from its epoch) -> list of warnings."""
    from fastapi import HTTPException

    from eclipsecalc import server
    kw = {k: req[k] for k in ('phenomena', 'observer', 'start', 'end', 'settings')}
    try:
        ctx, _, jd_a, jd_b, _ = server._parse_request(server.SearchRequest(**kw))
    except HTTPException as exc:
        raise InputError(str(exc.detail))
    if obs is not None and obs.kind == 'space' and (jd_b - jd_a) / 365.25 > 20:
        raise InputError('人工衛星の観測者では期間を 20 年以内にしてください')
    if req.get('step_deg') is not None and jd_b - jd_a > server.SWEEP_MAX_DAYS:
        raise InputError('位相を変えた一括計算では期間を 1 年以内にしてください')
    epoch = getattr(obs, 'epoch_jd', None)
    if epoch is None:
        return []
    return obs.warnings_for(ctx, jd_a if abs(jd_a - epoch) > abs(jd_b - epoch) else jd_b)


def calculate(req, debug=False):
    from fastapi import HTTPException

    from eclipsecalc import server
    kw = {k: req[k] for k in ('phenomena', 'observer', 'start', 'end', 'settings')}
    err = io.StringIO()
    try:
        # the web server prints tracebacks of unexpected errors; keep them out of the CLI's output
        with contextlib.redirect_stderr(err if not debug else sys.stderr):
            if req.get('step_deg') is not None:
                return server.phase_sweep(server.SweepRequest(**kw, step_deg=req['step_deg']))
            return server.search(server.SearchRequest(**kw))
    except HTTPException as exc:
        raise InputError(str(exc.detail))


def attach_details(res):
    from eclipsecalc import server
    for e in res['events']:
        if e.get('kind') == 'global':
            continue
        d = server.event_detail(e['id'])
        e['contacts'] = d['contacts']
        e['internal'] = d['internal']


def _strip_ids(v):
    if isinstance(v, dict):
        return {k: _strip_ids(x) for k, x in v.items() if k != 'id'}
    if isinstance(v, list):
        return [_strip_ids(x) for x in v]
    return v


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def _width(s):
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in s)


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


def _tz_label(tz):
    if tz == 0:
        return 'UTC'
    h, m = divmod(round(abs(tz) * 60), 60)
    return f'UTC{"+" if tz > 0 else "-"}{h}' + (f':{m:02d}' if m else '')


def _dur(s, precise=False):
    if s is None:
        return '—'
    if s < 60:
        return f'{s:.1f}秒' if precise else f'{s:.0f}秒'
    if s < 3600:
        m = int(s // 60)
        return f'{m}分{s - 60 * m:04.1f}秒' if precise else f'{m}分{round(s - 60 * m):02d}秒'
    h = int(s // 3600)
    return f'{h}時間{round((s - 3600 * h) / 60):02d}分'


def _latlon(lat, lon, digits=2):
    if lat is None:
        return '—'
    lat, lon = round(lat, digits) + 0.0, round(lon, digits) + 0.0     # no "南緯 0.00°" for -0.0001
    return (f'{"北緯" if lat >= 0 else "南緯"} {abs(lat):.{digits}f}°・'
            f'{"東経" if lon >= 0 else "西経"} {abs(lon):.{digits}f}°')


def _az(az):
    return AZ_NAMES[round((az % 360) / 22.5) % 16]


def _f(v, fmt, unit=''):
    return '—' if v is None else f'{v:{fmt}}{unit}'


def _vis(e, ground):
    f = e.get('vis_fraction') or 0
    if f >= 0.999:
        return '全経過'
    if f > 0:
        if not ground:
            return f'一部 {round(f * 100)}%（地球に隠される）'
        iv = e.get('visible_intervals') or []
        if len(iv) > 1:
            return f'一部 {round(f * 100)}%'
        return f'一部 {round(f * 100)}%（{"日の入り帯食" if iv and iv[0][0] == e.get("c1") else "日の出帯食"}）'
    return '地平線の下' if ground else '地球に隠される'


def _category(e):
    return TYPE_JA.get(e['type'], e['type']) if e['body'] == 'moon' else BODY_JA[e['body']]


def observer_text(o):
    kind, model = o.get('kind'), o.get('model')
    if kind == 'global':
        return '地球全体'
    if kind == 'ground':
        return f'{o["name"]}（{_latlon(o["lat"], o["lon"], 4)}・標高 {round(o["elevation_m"])} m）'
    if model == 'fixed':
        return f'{o["name"]}（地球固定位置・{_latlon(o["lat"], o["lon"])}・高度 {o["height_km"]:,.0f} km）'
    if model == 'horizons':
        return f'{o["name"]}（JPL Horizons {o["command"]}・位置の刻み {o["step_min"]} 分）'
    label = {'tle': 'TLE/SGP4', 'kepler': '軌道要素', 'sscweb': 'NASA SSCWeb'}.get(model, model)
    s = f'{o["name"]}（{label}'
    if o.get('perigee_km') is not None:
        s += f'・高度 {o["perigee_km"]:,.0f}〜{o["apogee_km"]:,.0f} km・周期 {o["period_min"]:.1f} 分'
    if model == 'tle' and o.get('i_deg') is not None:
        s += f'・傾斜角 {o["i_deg"]:.2f}°・離心率 {o["e"]:.5f}'
    if model == 'kepler':
        s += f'・傾斜角 {o["i_deg"]:.2f}°・昇交点赤経 {o["raan_deg"]:.2f}°'
        if o.get('ltan_h') is not None:
            t = round(o['ltan_h'] * 60) % 1440
            s += f'・昇交点の地方時 {t // 60:02d}:{t % 60:02d}'
        if o.get('sso'):
            s += '・太陽同期'
    if o.get('epoch'):
        s += f'・元期 {_time(o["epoch"], 0, "%Y-%m-%d %H:%M:%S")} UTC'
    return s + '）'


def _header(res, args, extra=''):
    out = [f'観測者: {observer_text(res["observer"])}',
           f'期間: {res["start"]} 〜 {res["end"]}（UTC）・暦 {res["ephemeris"]}'
           + (f'・ΔT 約 {res["delta_t_mid_s"]:.1f} 秒' if res.get('delta_t_mid_s') is not None else '')
           + f'・計算 {res["elapsed_s"]:.1f} 秒' + extra,
           f'時刻: {_tz_label(args.tz_hours)}']
    return out + [f'注意: {w}' for w in res.get('warnings') or []]


def _contacts_table(e, tz, ground):
    head = ['接触', '時刻', '食分', '中心間', '位置角 P']
    head += ['天頂角 V', '太陽高度', '太陽方位', '見える'] if ground else ['衛星直下点', '衛星高度', '見える']
    rows = []
    for c in e.get('contacts') or []:
        row = [CONTACT_JA.get(c['label'], c['label']), _time(c['time'], tz, '%H:%M:%S.%f')[:-3],
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
    lines = _header(res, args) + ['', f'{len(evs)} 件の現象が見つかりました' if evs
                                  else 'この期間に見られる現象はありません']
    if not evs:
        return lines
    tz = args.tz_hours
    if kind == 'global':
        head = ['日付', '種類', '始まり', '最大', '終わり', '食分/中心間', 'γ', '中心食/経過時間', '中心食帯の幅',
                '最大食の地点', 'サロス']
        rows = []
        for e in evs:
            if e.get('kind') == 'global':
                rows.append([_time(e['max'], tz, '%Y-%m-%d'), e['type_ja'] + ('（非中心）' if e.get('noncentral') else ''),
                             _time(e.get('p1'), tz), _time(e['max'], tz), _time(e.get('p4'), tz),
                             f'{e["magnitude"]:.4f}', f'{e["gamma"]:+.4f}',
                             '—' if e['type'] == 'partial' else _dur(e['central_duration_s'], True),
                             _f(e.get('path_width_km'), '.0f', ' km'), _latlon(e.get('ge_lat'), e.get('ge_lon')),
                             e.get('saros') or '—'])
            else:   # transits: contacts seen from the Earth's centre
                rows.append([_time(e['max'], tz, '%Y-%m-%d'), _category(e), _time(e['c1'], tz),
                             _time(e['max'], tz), _time(e['c4'], tz), f'{e["min_sep_arcsec"]:.1f}"', '—',
                             _dur(e['duration_s']), '—', '（地球中心から見た値）', '—'])
        lines += [''] + _table(head, rows, right={5, 6, 7, 8, 10})
        lines += ['', '日食の始まり・終わりは地球上のどこかで部分食が始まる・終わる時刻、'
                  'γ は影の軸と地球中心の距離（地球赤道半径単位）です。']
        return lines
    ground = kind == 'ground'
    head = ['日付', '種類', '欠け始め', '最大', '欠け終わり', '食分/中心間', '食面積率', '継続時間']
    head += ['最大時の太陽', '見え方', 'サロス'] if ground else ['見え方', '最大時の衛星直下点', '高度', 'サロス']
    rows = []
    for e in evs:
        moon = e['body'] == 'moon'
        dur = _dur(e.get('duration_s'))
        if moon and (e.get('central_duration_s') or 0) > 0:
            dur = f'{"皆既" if e["type"] == "total" else "金環"} {_dur(e["central_duration_s"], True)}／全体 {dur}'
        row = [_time(e['max'], tz, '%Y-%m-%d'), _category(e),
               _time(e['c1'], tz), _time(e['max'], tz), _time(e['c4'], tz),
               f'{e["magnitude"]:.3f}' if moon else f'{e["min_sep_arcsec"]:.1f}"',
               f'{100 * e["obscuration"]:.1f}%' if moon and e.get('obscuration') is not None else '—', dur]
        if ground:
            alt, az = e.get('sun_alt_max'), e.get('sun_az_max')
            row += [f'高度 {alt:.0f}°・{_az(az)}' if alt is not None else '—', _vis(e, True)]
        else:
            row += [_vis(e, False), _latlon(e.get('sat_lat_max'), e.get('sat_lon_max')),
                    _f(e.get('sat_alt_km_max'), ',.0f', ' km')]
        rows.append(row + [e.get('saros') or '—'])
    right = {5, 6, 7, len(head) - 1} | (set() if ground else {len(head) - 2})
    lines += [''] + _table(head, rows, right=right)
    if args.detail:
        for e in evs:
            lines += ['', f'■ {_time(e["max"], tz, "%Y-%m-%d")} {_category(e)}（最大 {_time(e["max"], tz)}）']
            lines += _contacts_table(e, tz, ground)
            if len(e.get('internal') or []) > 1:
                lines.append(f'衛星の運動により皆既・金環（内接）が {len(e["internal"])} 回に分かれます')
    return lines


def sweep_table(res, args):
    groups = res['groups']
    tz = args.tz_hours
    lines = _header(res, args, f'・平均近点角を {res["step_deg"]:g}° ずつ変えた {len(res["phases"])} 通り')
    if res['observer'].get('model') == 'tle':
        lines.append(f'TLE の平均近点角（元期での衛星の位置）は {res["observer"]["m_deg"]:.2f}° です。'
                     'TLE どおりの位置での結果は --sweep を付けずに計算できます。')
    lines += ['', f'{len(groups)} 件の現象（衛星が軌道上のどこにいるかで結果が変わります）' if groups
              else 'この期間には、どの位相でも見られる現象がありません']
    if not groups:
        return lines
    head = ['日付', '現象', '見える位相', '見える回数', '最も深い食の食分', '皆既・金環になる位相', '最大の時刻の範囲']
    rows = []
    for g in groups:
        moon = g['body'] == 'moon'
        c = len(g['central_phases'])
        if g['time_first']:
            same_day = _time(g['time_first'], tz, '%Y%m%d') == _time(g['time_last'], tz, '%Y%m%d')
            last_fmt = '%H:%M:%S' if same_day else '%m-%d %H:%M:%S'
            span = f'{_time(g["time_first"], tz, "%m-%d %H:%M:%S")} 〜 {_time(g["time_last"], tz, last_fmt)}'
        else:
            span = '—'
        rows.append([
            _time(g['date'], tz, '%Y-%m-%d'), BODY_JA[g['body']], f'{g["n_visible"]} / {g["n_phases"]}',
            f'{g["count_max"]} 回' if g['count_min'] == g['count_max'] else f'{g["count_min"]}〜{g["count_max"]} 回',
            f'{g["mag_min"]:.3f} 〜 {g["mag_max"]:.3f}' if moon and g['mag_min'] is not None else '—',
            '—' if not moon else f'{c} / {g["n_phases"]}（{round(100 * c / g["n_phases"])}%）' if c else 'なし',
            span])
    lines += [''] + _table(head, rows, right={2, 3, 4})
    lines += ['', '位相（平均近点角）ごとの結果は --format csv または --format json で出力できます。']
    return lines


def search_csv(res):
    # same columns as the web UI's 「一覧をCSV保存」, plus the sub-satellite point at maximum
    rows = [['date_utc', 'type', 'max_utc', 'c1_utc', 'c4_utc', 'magnitude', 'obscuration', 'ratio',
             'min_sep_arcsec', 'gamma', 'central_duration_s', 'duration_s', 'path_width_km', 'ge_lat',
             'ge_lon', 'sun_alt_max', 'visible_fraction', 'saros', 'sat_lat_max', 'sat_lon_max',
             'sat_alt_km_max']]
    for e in res['events']:
        cat = e['type_ja'] if e.get('kind') == 'global' else _category(e)
        rows.append([e['max'][:10], cat, e['max'], e.get('c1') or e.get('p1'), e.get('c4') or e.get('p4'),
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
            rows.append([g['date'][:10], BODY_JA[g['body']], r['m_deg'], r['count'],
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
        with open(args.output, 'w', encoding='utf-8-sig' if csv_out else 'utf-8',
                  newline='\r\n' if csv_out else None) as f:
            f.write(text)
        if not args.quiet:
            print(f'保存しました: {args.output}', file=sys.stderr, flush=True)
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
            print(f'{c["name"]}\t{c["lat"]}\t{c["lon"]}\t{c["elevation_m"]}')
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
    check_utc(req['start'], '開始日')
    check_utc(req['end'], '終了日')
    ctx = _context(req)
    observer, obs = check_observer(ctx, req['observer'])
    base = {'ok': True, 'tool': 'solar_eclipse_calc cli.py', 'version': __version__, 'request': req}
    if args.dry_run:
        warnings = preflight(req, obs)
        if args.format == 'json':
            _emit(_json_text(dict(base, dry_run=True, observer=observer, warnings=warnings)), args)
        else:
            _emit(f'観測者: {observer_text(observer)}\n期間: {req["start"]} 〜 {req["end"]}（UTC）'
                  f'・現象: {", ".join(req["phenomena"])}\n' + ''.join(f'注意: {w}\n' for w in warnings)
                  + '計算条件（--request で使える JSON）:\n' + _json_text(req), args)
        return 0

    t0 = time.time()
    sweep = req.get('step_deg') is not None
    log('計算しています…' if not sweep else
        f'平均近点角を {req["step_deg"]:g}° ずつ変えて計算しています（時間がかかります）…')
    res = calculate(req, debug=os.environ.get('ECLIPSECALC_DEBUG') == '1')
    if args.detail and not sweep:
        attach_details(res)
    res = _strip_ids(res)
    log(f'完了（{time.time() - t0:.1f} 秒）')

    if args.format == 'json':
        _emit(_json_text(dict(base, **res)), args)
        return 0
    if args.format == 'csv':
        for w in res.get('warnings') or []:      # the table shows them in its header
            log(f'注意: {w}')
        _emit(_csv_text(sweep_csv(res) if sweep else search_csv(res)), args, csv_out=True)
    else:
        _emit('\n'.join(sweep_table(res, args) if sweep else search_table(res, args)) + '\n', args)
    return 0


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except Exception:
            pass
    argv = sys.argv[1:] if argv is None else argv
    json_out = '--format=json' in argv or any(a == '--format' and b == 'json' for a, b in zip(argv, argv[1:]))

    def fail(message, code):
        if json_out:
            sys.stdout.write(_json_text({'ok': False, 'error': message, 'exit_code': code}))
        print(f'{"cli.py: " if code == 2 else ""}エラー: {message}', file=sys.stderr, flush=True)
        if code == 2:
            print('使い方は python cli.py --help を見てください', file=sys.stderr)
        return code

    try:
        args = parse_args(argv)
    except UsageError as exc:
        return fail(str(exc), 2)
    try:
        return run(args)
    except InputError as exc:
        return fail(str(exc), 1)
    except Exception as exc:  # keep the promise of a JSON answer with --format json
        if os.environ.get('ECLIPSECALC_DEBUG') == '1':
            import traceback
            traceback.print_exc()
        return fail(f'予期しないエラー: {exc!r}', 1)


if __name__ == '__main__':
    sys.exit(main())
