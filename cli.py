"""Eclipses and transits seen from a satellite given by its six orbital
elements, from the command line (the same calculation as the web UI's
"軌道要素 → 軌道6要素を入力").

    python cli.py --epoch 2027-07-30T12:00:00 --a 7058.1 --e 0.0012 --i 98.13 \\
        --raan 220.5 --argp 90 --m 45 --start 2027-07-25 --end 2027-08-10
    python cli.py ... --tz 9                  # times in JST (UTC+9)
    python cli.py ... --sweep 10              # every mean anomaly 10 deg apart
    python cli.py ... --format csv -o list.csv
"""
import argparse
import csv
import datetime as _dt
import io
import json
import os
import sys
import time
import unicodedata

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PHENOMENA = ('moon', 'mercury', 'venus')
BODY_JA = {'moon': '日食', 'mercury': '水星の太陽面通過', 'venus': '金星の太陽面通過'}
TYPE_JA = {'total': '皆既日食', 'annular': '金環日食', 'hybrid': '金環皆既日食', 'partial': '部分日食'}

EXAMPLES = '''例:
  python cli.py --epoch 2027-07-30T12:00:00 --a 7058.1 --e 0.0012 --i 98.13 --raan 220.5 \\
      --argp 90 --m 45 --start 2027-07-25 --end 2027-08-10 --tz 9
  平均近点角を 10° ずつ変えて一括計算（打ち上げ前の検討）: 上の --m の代わりに --sweep 10
  CSV で保存: --format csv -o list.csv（Excel で開ける UTF-8 BOM 付き）
'''


def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog='cli.py', allow_abbrev=False, epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description='軌道6要素で指定した人工衛星から見える日食・太陽面通過を計算します（画面の「軌道要素」と同じ計算）。')
    g = ap.add_argument_group('軌道6要素（GCRS・J2000 赤道基準の平均要素）')
    g.add_argument('--epoch', required=True, metavar='UTC',
                   help='元期（軌道情報の日時, UTC）。例 2027-07-30T12:00:00')
    g.add_argument('--a', type=float, required=True, metavar='KM', help='軌道長半径 (km)')
    g.add_argument('--e', type=float, required=True, metavar='E', help='軌道離心率（0 以上 1 未満）')
    g.add_argument('--i', type=float, required=True, metavar='DEG', help='軌道傾斜角 (°)')
    g.add_argument('--raan', type=float, required=True, metavar='DEG', help='昇交点赤経 (°)')
    g.add_argument('--argp', type=float, default=0.0, metavar='DEG', help='近地点引数 (°)（既定 0）')
    g.add_argument('--m', type=float, default=0.0, metavar='DEG', help='平均近点角 (°)（既定 0）')
    g.add_argument('--no-j2', action='store_true', help='地球の扁平 (J2) による軌道面の歳差を考慮しない')
    g.add_argument('--name', default='', help='衛星名（表示用）')

    p = ap.add_argument_group('期間と現象')
    p.add_argument('--start', required=True, metavar='DATE', help='開始日（UTC）。例 2027-07-25')
    p.add_argument('--end', required=True, metavar='DATE', help='終了日（UTC）。例 2027-08-10')
    p.add_argument('--phenomena', default='moon,mercury,venus', metavar='LIST',
                   help='計算する現象をカンマ区切りで: moon（日食）, mercury, venus（既定 すべて）')
    p.add_argument('--sweep', type=float, metavar='STEP',
                   help='平均近点角をこの刻み (°, 5〜90) で変えて一括計算する（期間は 1 年以内。--m は無視）')

    s = ap.add_argument_group('詳細設定')
    s.add_argument('--ephemeris', default=None, metavar='FILE', help='暦（既定 de440s.bsp。de440.bsp なら 1550〜2650 年）')
    s.add_argument('--delta-t', type=float, default=None, metavar='SEC', help='ΔT（TT−UT, 秒）を固定値で指定（既定は自動）')
    s.add_argument('--atm', type=float, default=0.0, metavar='KM', help='地球大気の遮蔽高度 (km)（既定 0）')
    s.add_argument('--include-invisible', action='store_true', help='地球の陰で見えない現象も含める')

    o = ap.add_argument_group('出力')
    o.add_argument('--format', choices=('table', 'csv', 'json'), default='table', help='出力形式（既定 table）')
    o.add_argument('--tz', type=float, default=0.0, metavar='HOURS',
                   help='表で使う時刻の UTC からのずれ（時間）。例 9 で日本時間（既定 0 = UTC。CSV・JSON は常に UTC）')
    o.add_argument('-o', '--output', metavar='FILE', help='結果をファイルに保存する（既定は画面に表示）')

    args = ap.parse_args(argv)
    if not args.a > 0:
        ap.error('--a（軌道長半径）は正の値で指定してください')
    if not 0 <= args.e < 1:
        ap.error('--e（軌道離心率）は 0 以上 1 未満で指定してください')
    args.phenomena = [x.strip() for x in args.phenomena.split(',') if x.strip()]
    bad = [x for x in args.phenomena if x not in PHENOMENA]
    if bad or not args.phenomena:
        ap.error(f'--phenomena は moon, mercury, venus から選んでください（指定: {", ".join(bad) or "なし"}）')
    if args.sweep is not None and not 5 <= args.sweep <= 90:
        ap.error('--sweep（位相の刻み）は 5〜90° で指定してください')
    return args


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


def _dur(s):
    if s is None:
        return '—'
    if s < 60:
        return f'{s:.0f}秒'
    if s < 3600:
        m = int(s // 60)
        return f'{m}分{round(s - 60 * m):02d}秒'
    h = int(s // 3600)
    return f'{h}時間{round((s - 3600 * h) / 60):02d}分'


def _latlon(lat, lon):
    if lat is None:
        return '—'
    return f'{"北緯" if lat >= 0 else "南緯"} {abs(lat):.2f}°・{"東経" if lon >= 0 else "西経"} {abs(lon):.2f}°'


def _vis(e):
    f = e.get('vis_fraction') or 0
    if f >= 0.999:
        return '全経過'
    if f > 0:
        return f'一部 {round(f * 100)}%（地球に隠される）'
    return '地球に隠される'


def _category(e):
    return TYPE_JA.get(e['type'], e['type']) if e['body'] == 'moon' else BODY_JA[e['body']]


def _observer_text(o):
    s = (f'{o["name"]}（軌道要素・高度 {o["perigee_km"]:.0f}〜{o["apogee_km"]:.0f} km・周期 {o["period_min"]:.1f} 分'
         f'・傾斜角 {o["i_deg"]:.2f}°・昇交点赤経 {o["raan_deg"]:.2f}°')
    if o.get('ltan_h') is not None:
        t = round(o['ltan_h'] * 60) % 1440
        s += f'・昇交点の地方時 {t // 60:02d}:{t % 60:02d}'
    return s + f'・元期 {_time(o.get("epoch"), 0, "%Y-%m-%d %H:%M:%S")} UTC）'


def _header(res, args, extra=''):
    out = [f'観測者: {_observer_text(res["observer"])}',
           f'期間: {res["start"]} 〜 {res["end"]}（UTC）・暦 {res["ephemeris"]}'
           + (f'・ΔT 約 {res["delta_t_mid_s"]:.1f} 秒' if res.get('delta_t_mid_s') is not None else '')
           + f'・計算 {res["elapsed_s"]:.1f} 秒' + extra,
           f'時刻: {_tz_label(args.tz)}']
    out += [f'注意: {w}' for w in res.get('warnings') or []]
    return out


def search_table(res, args):
    evs = res['events']
    lines = _header(res, args) + ['', f'{len(evs)} 件の現象が見つかりました' if evs
                                  else 'この期間に見られる現象はありません']
    if not evs:
        return lines
    head = ['日付', '種類', '欠け始め', '最大', '欠け終わり', '食分/中心間', '食面積率', '継続時間', '見え方',
            '最大時の衛星直下点', '高度', 'サロス']
    rows = []
    for e in evs:
        moon = e['body'] == 'moon'
        rows.append([
            _time(e['max'], args.tz, '%Y-%m-%d'), _category(e),
            _time(e['c1'], args.tz), _time(e['max'], args.tz), _time(e['c4'], args.tz),
            f'{e["magnitude"]:.3f}' if moon else f'{e["min_sep_arcsec"]:.1f}"',
            f'{100 * e["obscuration"]:.1f}%' if moon and e.get('obscuration') is not None else '—',
            _dur(e.get('duration_s')), _vis(e),
            _latlon(e.get('sat_lat_max'), e.get('sat_lon_max')),
            f'{e["sat_alt_km_max"]:.0f} km' if e.get('sat_alt_km_max') is not None else '—',
            e.get('saros') or '—'])
    return lines + [''] + _table(head, rows, right={5, 6, 7, 10, 11})


def sweep_table(res, args):
    groups = res['groups']
    n = len(res['phases'])
    lines = _header(res, args, f'・平均近点角を {res["step_deg"]:g}° ずつ変えた {n} 通り')
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
            same_day = _time(g['time_first'], args.tz, '%Y%m%d') == _time(g['time_last'], args.tz, '%Y%m%d')
            last_fmt = '%H:%M:%S' if same_day else '%m-%d %H:%M:%S'
            span = f'{_time(g["time_first"], args.tz, "%m-%d %H:%M:%S")} 〜 {_time(g["time_last"], args.tz, last_fmt)}'
        else:
            span = '—'
        rows.append([
            _time(g['date'], args.tz, '%Y-%m-%d'), BODY_JA[g['body']], f'{g["n_visible"]} / {g["n_phases"]}',
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
        rows.append([e['max'][:10], _category(e), e['max'], e.get('c1'), e.get('c4'), e.get('magnitude'),
                     e.get('obscuration'), e.get('ratio'), e.get('min_sep_arcsec'), None,
                     e.get('central_duration_s'), e.get('duration_s'), None, None, None,
                     e.get('sun_alt_max'), e.get('vis_fraction'), e.get('saros'), e.get('sat_lat_max'),
                     e.get('sat_lon_max'), e.get('sat_alt_km_max')])
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


# ---------------------------------------------------------------------------
def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except Exception:
            pass
    args = parse_args(argv)

    def log(message):
        print(message, file=sys.stderr, flush=True)

    from fastapi import HTTPException

    from eclipsecalc import server
    from eclipsecalc.context import DEFAULT_EPHEMERIS, ensure_ephemeris, get_context
    from eclipsecalc.timeutil import parse_utc

    ephemeris = args.ephemeris or DEFAULT_EPHEMERIS
    if not ephemeris.endswith('.bsp'):
        ephemeris += '.bsp'
    try:
        ensure_ephemeris(ephemeris, log=log)
    except Exception as exc:
        log(f'エラー: JPL 暦 {ephemeris} を用意できませんでした: {exc}')
        return 1
    try:
        parse_utc(get_context(ephemeris, args.delta_t), args.epoch)
    except Exception:
        log(f'エラー: 元期「{args.epoch}」を解釈できません（例 2027-07-30T12:00:00）')
        return 1

    observer = {'type': 'kepler', 'name': args.name, 'epoch': args.epoch, 'a_km': args.a, 'e': args.e,
                'i_deg': args.i, 'raan_deg': args.raan, 'argp_deg': args.argp, 'm_deg': args.m,
                'j2': not args.no_j2}
    req = dict(phenomena=args.phenomena, observer=observer, start=args.start, end=args.end,
               settings={'ephemeris': ephemeris, 'delta_t': args.delta_t, 'earth_atm_km': args.atm,
                         'include_invisible': args.include_invisible})
    t0 = time.time()
    log('計算しています…' if args.sweep is None else
        f'平均近点角を {args.sweep:g}° ずつ変えて計算しています（時間がかかります）…')
    try:
        if args.sweep is None:
            res = server.search(server.SearchRequest(**req))
        else:
            res = server.phase_sweep(server.SweepRequest(**req, step_deg=args.sweep))
    except HTTPException as exc:
        log(f'エラー: {exc.detail}')
        return 1
    log(f'完了（{time.time() - t0:.1f} 秒）')

    if args.format == 'json':
        text = json.dumps(res, ensure_ascii=False, indent=1) + '\n'
    elif args.format == 'csv':
        text = _csv_text(search_csv(res) if args.sweep is None else sweep_csv(res))
        for w in res.get('warnings') or []:
            log(f'注意: {w}')
    else:
        text = '\n'.join(search_table(res, args) if args.sweep is None else sweep_table(res, args)) + '\n'

    if args.output:
        # CSV for Excel: UTF-8 with BOM and CRLF, like the web UI's download
        csv_out = args.format == 'csv'
        with open(args.output, 'w', encoding='utf-8-sig' if csv_out else 'utf-8',
                  newline='\r\n' if csv_out else None) as f:
            f.write(text)
        log(f'保存しました: {args.output}')
    else:
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
