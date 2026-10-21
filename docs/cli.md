# コマンドライン（cli.py）の使い方 — 人と生成 AI のための手引き

`cli.py` は、画面（Web UI）と同じ計算をコマンドラインで行います。
人が軌道情報や地点を生成 AI（Claude Code などのコーディングエージェント）に渡すと、AI がこの文書に従って `cli.py` を実行し、結果を確かめて報告できるようにしています。

- 計算処理は画面と共通です（`eclipsecalc/server.py` の `search` / `phase_sweep` をそのまま呼びます）。同じ入力なら画面と同じ結果になります。
- 入力は引数でも JSON（`--request`）でも渡せます。`--format json` の出力には、計算に使った条件（`request`）がそのまま入っていて、`--request` に渡すと同じ計算を再現できます。
- 失敗すると終了コードが 0 以外になり、`--format json` なら失敗も JSON で返します。

## 実行のしかた

```bash
python cli.py --help
```

| 環境 | Python |
|---|---|
| Python と `requirements.txt` のライブラリがある | `python cli.py …` |
| ソース一式を `start.bat` / `start.command` で準備した | Windows: `.runtime\venv\Scripts\python cli.py …`、Mac・Linux: `.runtime/venv/bin/python cli.py …` |
| Windows の配布用パッケージ | `.runtime\python-windows-x86_64\python.exe -E -s cli.py …` |

- 作業フォルダはリポジトリ（`cli.py` のあるフォルダ）にしてください。
- 初回に暦（`data/de440s.bsp`、約 32 MB）がなければ自動でダウンロードします。
- ファイルやパイプへの出力（標準出力・標準エラー出力）は UTF-8 です（コンソールにはそのまま表示します）。Windows PowerShell 5.1 で出力を受け取って文字化けするときは、`-o ファイル`（常に UTF-8）で保存して読むか、先に `[Console]::OutputEncoding = [Text.Encoding]::UTF8` を実行します。環境変数 `PYTHONIOENCODING` を指定すると、その文字コードで出力します。
- 表示の言語は `--lang`（`ja` 日本語、`en` 英語、`fr` フランス語、`ru` ロシア語、`es` スペイン語、`zh` 中国語、`hi` ヒンディー語）で選びます。指定しなければ環境変数 `ECLIPSECALC_LANG`、なければ環境の言語（`LANG` などの環境変数、Windows の表示言語）、それもなければ英語です。ヘルプ・表・CSV の種類の列・経過の表示・エラー・`warnings` がその言語になります。JSON の項目名と、`type`・`body` などの値は言語によらず同じです（`type_ja` は常に日本語）。
- インターネットが必要なのは `--norad`（CelesTrak）、`--horizons`（JPL Horizons）、`--sscweb` と `--list-sscweb`（NASA SSCWeb）だけです。ほかの計算はオフラインでできます。

## 生成 AI が計算するときの手順

1. **観測者を決める**（下の表）。人から受け取った情報をなるべくそのままの形で渡し、AI が別の形式に換算しないようにします（換算の誤りを避けるため）。
2. **解釈を確かめる**: `--dry-run --format json` で、計算せずに観測者の解釈だけを出力します。`observer` の周期（`period_min`）・近地点/遠地点高度（`perigee_km` / `apogee_km`）・傾斜角（`i_deg`）・離心率（`e`）・昇交点赤経（`raan_deg`）・昇交点の地方時（`ltan_h`、軌道要素のみ）などを、人から受け取った情報と照らし合わせてください。TLE では 2 行目の値（傾斜角・昇交点赤経・離心率・近地点引数・平均近点角・平均運動）がそのまま出ます。期間の誤り（暦の範囲外など）もここでエラーになり、`warnings` には計算したときに出る注意が入ります（TLE の古さは、期間の端のうち元期から遠いほうで判定します）。
3. **計算する**: `--format json` で実行し、終了コードと `ok` を確認します。人の言語に合わせて `--lang`（例 `--lang en`）を付けると、`warnings` や `error` をそのまま伝えられます。`warnings` があれば必ず人に伝えます（例: TLE の元期から日数が経っていて誤差が大きい）。接触時刻まで必要なら `--detail` を付けます。
   TLE の元期から何週間も先の現象は、衛星が軌道上のどこにいるかがほとんど分からなくなるため（大気の抵抗や軌道変更）、`--sweep 10` も実行して、位置による結果の幅（時刻・回数・食分の範囲）を一緒に伝えてください。
4. **報告する**: 時刻は UTC で返るので、人の地域の時刻に直して伝えます（日本時間 = UTC+9）。表で見せるときは `--format table --tz 9` が便利です。計算条件（出力の `request`）を残しておくと、`--request` で同じ計算を再現できます。

### 入力情報と観測者の対応

| 人から受け取る情報 | 引数 |
|---|---|
| TLE（`1 ` と `2 ` で始まる 2 行。名前の行があってもよい） | ファイルに保存して `--tle ファイル`、または標準入力から `--tle -` |
| NORAD カタログ番号・現在運用中の衛星（最新の軌道でよい場合） | `--norad 25544` |
| 軌道6要素（元期・軌道長半径・離心率・傾斜角・昇交点赤経・近地点引数・平均近点角） | `--epoch --a --e --i --raan --argp --m` |
| 計画中の軌道（高度・太陽同期・昇交点の地方時など） | `--epoch` と `--alt`（円軌道）または `--perigee-alt --apogee-alt`、`--i` または `--sso`、`--raan` または `--ltan` |
| 打ち上げ前で、軌道上の位置（位相）が決まっていない | 上の軌道要素に `--sweep 10`（平均近点角を 10° 刻みで一括計算） |
| TLE の元期から何週間も先の現象 | `--tle` に `--sweep 10`（TLE の平均近点角を 10° 刻みで置き換えて一括計算） |
| 静止衛星の経度 | `--geo-lon 140.7` |
| 探査機・宇宙望遠鏡（JWST、SOHO など） | `--horizons -170`（JPL Horizons の ID。位置の刻み `--horizons-step`（既定 60 分）は、地球の近くを回る衛星・月を回る探査機・地球や月の近くを通る探査機では位置の補間の誤差が 10 m 以下になるまで自動で細かくし、`warnings` で知らせます） |
| 科学衛星が過去に見た現象（ひので など） | `--sscweb hinode`（ID は `--list-sscweb` で確認） |
| 地上の地点（緯度・経度・標高） | `--lat --lon --elev`（地名しかなければ AI が緯度経度を調べ、出典とともに人に確認） |
| よく使う都市（東京、ルクソールなど） | `--city 東京`（7 言語のどの名前でも可。`--city Tokyo` など。一覧は `--list-cities`） |
| 「世界のどこで見られるか」 | `--global` |
| 観測画像（ひので・SDO など）の撮影時刻・画像で測った位置 | 観測者に `--phenomena` で現象を 1 つと `--at 時刻` または `--at-file ファイル`（[観測画像と比べる](#観測画像と比べる--at)） |

期間は `--start` `--end`（UTC の日付）、現象は `--phenomena moon,mercury,venus`（既定はすべて。`moon` が日食）で指定します。

### 軌道要素についての注意

- `--a --e --i --raan --argp --m` は **GCRS（J2000 赤道）基準の平均要素** として扱い、地球の扁平（J2）による永年摂動を加えて計算します（`--no-j2` で無効）。公表されている接触軌道要素（osculating elements）を入れても目安としては使えますが、TLE と同じ精度にはなりません。
- **TLE の要素を取り出して `--a` などに入れないでください。** TLE は SGP4 専用の平均要素（TEME 基準、平均運動は周回/日）なので、`--tle` でそのまま渡します。
- 高度は地球の赤道半径 6378.137 km からの高さです（`--alt 680` は軌道長半径 7058.137 km）。周期しか分からない場合は a = (μT²/4π²)^(1/3)（μ = 398600.4418 km³/s²）で求め、`--dry-run` で `period_min` が元の周期と一致するか確かめます。
- 元期・期間は **UTC** です。日本時間で与えられたら 9 時間引きます（2027-08-02 09:00 JST = 2027-08-02T00:00:00 UTC）。
- 角度はすべて度（°）、距離は km、標高は m です。経度は東経 +／西経 −、緯度は北緯 +／南緯 −。

### 計算できる範囲

| 項目 | 範囲 |
|---|---|
| 期間（既定の暦 de440s） | 1849-12-26〜2150-01-20。1550〜2650 年は `python tools/download_ephemeris.py de440` のあと `--ephemeris de440` |
| 人工衛星の期間 | 20 年以内（`--sweep` は 1 年以内） |
| TLE | 元期から離れるほど誤差が大きくなります（7 日超・30 日超で `warnings` に注意が入ります）。現象の数日前の TLE を使うと秒単位の精度になります。何週間も先なら `--sweep` で位置による幅を確かめます |
| JPL Horizons・SSCWeb | 各サービスが軌道データを提供している期間のみ。範囲外は `warnings` に入るか、エラーになります |

### 半径の選び方（`--sun-radius`、`--planet-radius`）

接触の時刻は太陽と月・惑星の半径で決まります（太陽の視半径が 1″ 違うと、接触は水星の太陽面通過で 10〜12 秒、金星で 13〜19 秒、地上の日食で 2〜3 秒、低軌道の衛星から見た日食で 0.2〜0.3 秒ずれます。`--detail` の `dt_drs`）。既定は **観測される縁** です。

| 引数 | 値 |
|---|---|
| `--sun-radius observed`（既定） | 1 AU で 959.96″ = 696,231 km。目に見える太陽の縁（周辺減光の変曲点）。ひので SOT が撮影した 2006 年の水星の太陽面通過の接触に最もよく合い（4 つの接触とも 1.3 秒以内）、ほかの観測（SDO/HMI 2012 年の金星の太陽面通過 959.90″、日食の光度変化 959.99″、SOHO/MDI 960.12″、PICARD/SODISM 959.86″）とも一致します |
| `--sun-radius nasa` | 959.63″ = 695,992 km（NASA・Espenak の日食予報の値。NASA の公表値と比べるとき） |
| `--sun-radius iau2015` | 695,700 km = 959.23″（IAU 2015 B3 の公称値。光学的深さ 1 の光球の半径で、見える縁より内側。太陽面通過の接触は水星で 8〜10 秒、金星で約 13 秒ずれます） |
| `--sun-radius 696000`、`--sun-radius 959.96as` | km の数値、または 1 AU での角半径（″。`as`・`″`・`"` を付ける）。単位のない 2,000 未満の数は角度の書き忘れとしてエラーにします |
| `--planet-radius observed`（既定） | 太陽の前に見える大きさ: 水星 2,439.4 km（大気がなく固体表面と同じ）、金星 6,131 km（雲頂と靄。Reale et al. 2015） |
| `--planet-radius iau` | IAU の固体表面: 水星 2,439.4 km、金星 6,051.8 km（金星の接触は既定より約 7 秒ずれます） |
| `--planet-radius nasa` | NASA の値: 1 AU で水星 3.36″、金星 8.41″（雲を含む） |
| `--planet-radius xray` | 軟X線（ひので XRT など）: 金星 6,202 km（上層大気）。水星は 2,439.4 km |

人工衛星から X 線・極紫外線で見るときは、地球の大気が太陽を隠す高さも `--atm` で指定します（目安: 可視光 0 km、軟X線 約 140 km、極紫外線 約 250 km）。表の見出しには使った半径が 1 行で出ます（例: `太陽半径 696,231 km（観測される縁, 1 AU で 959.96″）`）。JSON では `params` と `settings` の `sun_radius_km`・`mercury_radius_km`・`venus_radius_km`（km）です。出力の `request` の `settings` には、指定しなかった半径も既定の値で入るので、既定が変わっても `--request` で同じ計算を再現できます。既定を見える縁に変える前（2026 年 10 月より前）の `request` には半径がなく、当時の既定は太陽 695,700 km・金星 6,051.8 km（固体表面）だったので、その結果を再現するには `settings` に `"sun_radius_km": 695700, "venus_radius_km": 6051.8` を加えます（加えないと、太陽面通過の接触は約 9 秒、日食の接触は数秒変わります）。

## JSON での指定（`--request`）

Web API（`POST /api/search`、`/api/phase_sweep`）と同じ形です。ファイル、または `-`（標準入力）で渡します。`--format json` の出力（や画面の「結果を保存」のファイル）をそのまま渡すと、その中の `request` を使います。

```json
{
  "observer": {"type": "kepler", "epoch": "2027-07-30T12:00:00", "a_km": 7058.1, "e": 0.0012,
               "i_deg": 98.13, "raan_deg": 220.5, "argp_deg": 90, "m_deg": 45, "name": "テスト衛星"},
  "start": "2027-07-25",
  "end": "2027-08-10",
  "phenomena": ["moon", "mercury", "venus"],
  "settings": {}
}
```

```bash
python cli.py --request request.json --format json
```

`observer` の形（`name` はどれも省略可）:

| type | 項目 |
|---|---|
| `kepler` | `epoch`（UTC）、大きさ: `a_km` と `e`、または `perigee_alt_km` と `apogee_alt_km`、傾斜角: `i_deg` または `"sso": true`、軌道面: `raan_deg` または `ltan_h`（時。18.0 = 18:00）、`argp_deg`、`m_deg`、`j2`（既定 true） |
| `tle` | `line1`、`line2`（`m_deg` を加えると、平均近点角をその値に置き換えた TLE で計算します） |
| `celestrak` | `norad`（CelesTrak から最新の TLE を取得し、`request` には `tle` として記録されます） |
| `geo` | `lon` |
| `horizons` | `command`（ID）、`step_min` |
| `sscweb` | `id` |
| `ground` | `lat`、`lon`、`elevation_m` |
| `global` | （なし） |

`step_deg`（5〜90）を加えると、`kepler` と `tle` で平均近点角を変えた一括計算になります。
`times`（時刻の文字列の配列）を加えると、現象を探さずにその時刻の位置を計算します（`--at` と同じ。`measured`・`exposure_s` も使えます。`start`・`end` は省略でき、`phenomena` は 1 つ）。`--request` に `--at`・`--at-file`・`--exposure` を加えると、JSON の時刻の代わりにそれを使います。
`settings` の項目: `ephemeris`、`delta_t`（秒）、`sun_radius_km`、`moon_radius_ext_km`、`moon_radius_int_km`、`mercury_radius_km`、`venus_radius_km`、`min_sun_alt_deg`、`refraction`、`earth_atm_km`、`include_invisible`。省略すると画面の既定値です（半径は上の「半径の選び方」の既定。以前の `request` の半径は同じ節の注意を参照）。

## 出力（`--format json`）

成功したとき（終了コード 0）:

| 項目 | 内容 |
|---|---|
| `ok` | `true` |
| `lang` | メッセージ（`warnings`、既定の観測者名など）の言語（`--lang`） |
| `request` | 計算に使った条件（`--request` にそのまま渡せる） |
| `observer` | 観測者の解釈。衛星なら `period_min`、`perigee_km`、`apogee_km`、`epoch`（UTC）など。軌道要素なら `a_km`、`e`、`i_deg`、`raan_deg`、`argp_deg`、`m_deg`、`ltan_h`、`sso` も。TLE なら `norad`、`a_km`、`e`、`i_deg`、`raan_deg`、`argp_deg`、`m_deg`、`mean_motion_rev_per_day`、`bstar`（TLE の値。TEME 基準） |
| `events` | 現象の一覧（最大の時刻順。下の表） |
| `warnings` | 注意（文字列の配列）。人に伝えてください。TLE の古さの注意にある日数は、通常の計算では見つかった現象のうち元期から最も遠いものまでの日数、`--dry-run` と `--sweep` では期間の端のうち元期から遠いほうまでの日数です |
| `start`、`end`、`ephemeris`、`delta_t_mid_s`、`params` | 期間、暦、期間中央の ΔT（秒）、計算に使った半径などの設定 |
| `dry_run` | `--dry-run` のときだけ `true`（`events` はありません） |

この出力は、画面（`run.py`）の右上の「保存した結果を開く」で開くと、画面で一覧・詳細・地図を見られます（画面の「結果を保存」も同じ形のファイルを保存します）。人に結果を見せるときに使えます。

失敗したとき: `{"ok": false, "error": "メッセージ（--lang の言語）", "exit_code": 1 または 2}`（`-o` を付けていれば、終了コード 1 の失敗はそのファイルにも書きます）。終了コード 1 は入力を計算できなかった（地表より低い軌道、暦の範囲外、通信の失敗など）、2 は引数の誤りです。

### `events` の項目（地上の地点・人工衛星）

時刻はすべて UTC の ISO 8601 文字列（ミリ秒まで、末尾 `Z`）です。1972 年より前はうるう秒の UTC がないため UT（UT1 = TT − ΔT。日食のカタログと同じ）で、入力の日時も同じです。

| 項目 | 内容 |
|---|---|
| `body` | `moon`（日食）、`mercury`、`venus` |
| `type` / `type_ja` | `type_ja` は常に日本語。`partial` 部分日食、`total` 皆既日食、`annular` 金環日食、`hybrid` 金環皆既日食、`transit` 太陽面通過、`transit_grazing` 外接のみの太陽面通過 |
| `c1`、`max`、`c4` | 第1接触（欠け始め）、最大、第4接触（欠け終わり） |
| `magnitude` | 食分（太陽の直径のうち隠された割合。皆既日食では 1 以上）。太陽面通過では意味のある量ではありません（小さな惑星の円と太陽の円から同じ式で計算しているだけで、観測とは比べられません）。太陽面通過の位置は `min_sep_arcsec` と、`--detail` の `hpc_x`・`hpc_y`・`limb_dist` で見ます |
| `obscuration` | 食面積率（太陽の面積のうち隠された割合、0〜1） |
| `ratio` | 月（惑星）と太陽の視直径の比 |
| `min_sep_arcsec` | 最大時の中心間角距離（″）。太陽面通過の指標 |
| `duration_s` | 第1〜第4接触の時間（秒） |
| `central_duration_s` | 皆既・金環（太陽面通過では内接）の継続時間（秒）。衛星では周回で何回かに分かれることがあり、`internal_durations_s` にそれぞれ入ります（`n_internal` 回） |
| `vis_fraction` | 第1〜第4接触のうち観測者から太陽が見えている時間の割合（1 = 全経過、0 = 見えない）。地上は太陽が地平線（`min_sun_alt_deg`）より上、衛星は太陽が地球（＋`earth_atm_km`）に隠されていないこと |
| `visible_intervals` | 見えている時間帯 `[[開始, 終了], …]` |
| `visible_at_max`、`visible_max` | 最大の時刻に見えるか。見える範囲での最大（`time`、`magnitude`、`obscuration`） |
| `sun_alt_max`、`sun_az_max` | 地上: 最大時の太陽の高度・方位（°。方位は北から東回り） |
| `sat_lat_max`、`sat_lon_max`、`sat_alt_km_max` | 衛星: 最大時の衛星直下点の緯度・経度（°）と高度（km） |
| `saros` | サロス番号（日食のみ。遠くの人工衛星・探査機から見えても、地球上で日食が起きない月の現象は `null`） |
| `sun_diameter_arcsec`、`body_diameter_arcsec`、`delta_t_s` | 最大時の太陽・月（惑星）の視直径（″）、ΔT（秒） |
| `c1_cut`、`c4_cut` | `true` なら、計算期間（または軌道データのある期間）の端で切れていて、実際の接触はそれより前（後） |
| `warnings` | この現象についての注意（TLE の元期からの日数など） |
| `contacts` | `--detail` のときのみ。下の表 |
| `internal` | `--detail` のときのみ。皆既・金環（内接）の時間帯 `[[第2接触, 第3接触], …]` |

`contacts`（`--detail`）は接触ごとの値です。`label` は `C1`、`C2`、`MAX`、`C3`、`C4`（衛星では `C2`/`C3` が複数回あることがあります）。位置はどれも観測者から見た太陽の中心に対する月・惑星の中心の位置で、向きは 2 通りあります。

- **天の北が上**（`xi`、`eta`、`pa`）: その日の真の赤道の北が上。`xi` は東（空を見上げて左）が +、`eta` は北が +。
- **太陽の北が上**（`hpc_x`、`hpc_y`、`pa_solar`）: 太陽の自転軸の北が上。`hpc_x` は太陽の西（画像の右）が +、`hpc_y` は太陽の北が +。ひので・SDO などの太陽画像の座標（helioprojective、″）と同じ向きです。`hpc_x = −(xi cos P − eta sin P)`、`hpc_y = xi sin P + eta cos P`（P は `p_angle`）。

| 項目 | 内容 |
|---|---|
| `time`、`jd_tt`、`visible` | 時刻、TT のユリウス日、その時刻に見えるか |
| `magnitude`、`obscuration`、`sep_arcsec` | 食分、食面積率、中心間角距離（″） |
| `xi`、`eta` | 天の北が上の位置（″。上の説明） |
| `pa` | 位置角 P（太陽の中心から見た月・惑星の方向。天の北から東回り、°） |
| `p_angle` | 太陽の P 角（太陽の自転軸の北の位置角。天の北から東回り、°。IAU 2015 の自転軸 α₀ = 286.13°、δ₀ = 63.87°） |
| `hpc_x`、`hpc_y` | 太陽の北が上の位置（″。上の説明） |
| `pa_solar` | 太陽の北から東回りの位置角（°。`pa − p_angle`） |
| `limb_dist` | 太陽の縁から月・惑星の中心までの角距離（″。`sep_arcsec − rho_s`。負なら中心が太陽面の内側。太陽面通過の外接で +`rho_b`、内接で −`rho_b`） |
| `limb_depth` | 縁への入り込み（`(rho_s − sep_arcsec) / rho_b`。太陽面通過では外接で −1、中心が縁の上で 0、内接で +1。金環日食の内接でもほぼ +1、皆既日食の内接では (2·rho_s − rho_b) / rho_b） |
| `dt_drs`、`dt_drb` | 太陽、月（惑星）の視半径が 1″ 大きいとき、その接触の時刻が変わる秒数（秒/″。`MAX` と期間の端で切れた接触は `null`）。第1・第4接触ではどちらも `1/f′`、太陽面通過・金環の第2・第3接触では `dt_drs = 1/f′`、`dt_drb = −1/f′`、皆既の第2・第3接触では符号が逆（`f′` は中心間と半径の和・差の変化率、″/秒）。例: 太陽面通過で `dt_drs = −11.9` なら、太陽が 0.1″ 大きいと第1接触は 1.2 秒早い。接触をかすめる現象では大きな値になります |
| `rho_s`、`rho_b` | 太陽、月（惑星）の視半径（″） |
| `sun_alt`、`sun_az`、`v_angle` | 地上: 太陽高度・方位（°）、天頂角 V（天頂方向から測った位置角、°） |
| `sat_lat`、`sat_lon`、`sat_alt_km`、`earth_sep_deg`、`earth_radius_deg`、`dist_earth_km` | 衛星: 衛星直下点、衛星から見た太陽と地球中心の離角（°）、地球の視半径（°）、地心距離（km） |

表（`--format table --detail`）では、各現象の接触の時刻・X・Y（太陽の北が上）・縁からの距離（太陽面通過では食分の代わり）・太陽半径 +1″ あたりの時刻の変化（`dt_drs`）が並び、人工衛星では見えている時間帯（周回ごとの「夜」の合間）も出ます。`--format csv --detail` は接触ごとに 1 行で、列は `event_max_utc`（現象の最大の時刻）、`label`、`time_utc`、`hpc_x`、`hpc_y`、`pa`、`pa_solar`、`p_angle`、`sep_arcsec`、`limb_dist`、`limb_depth`、`magnitude`、`dt_drs`、`dt_drb`、`visible`（1 = 見える、0 = 見えない）です。

### `events` の項目（地球全体 `--global`）

日食は `kind: "global"` で、次の項目を持ちます（太陽面通過は地球中心から見た上の表の形です）。

| 項目 | 内容 |
|---|---|
| `max` | 最大食の時刻（影の軸が地球中心に最も近づく時刻） |
| `magnitude` | 最大食の地点での食分（中心食は月と太陽の視直径比。NASA の公表値と同じ定義） |
| `gamma` | γ（影の軸と地球中心の最短距離。地球赤道半径単位、北が +） |
| `ge_lat`、`ge_lon`、`sun_alt` | 最大食の地点と、そこでの太陽高度 |
| `central_duration_s`、`path_width_km` | 最大食の地点での中心食の継続時間（秒）、中心食帯の幅（km） |
| `central`、`noncentral` | 中心食か。影の軸が地球を外れる皆既・金環は `noncentral: true` |
| `p1`、`p4` | 地球上のどこかで部分食が始まる・終わる時刻 |
| `c_begin`、`c_end` | 中心食（皆既・金環）が地球上で始まる・終わる時刻 |

### 一括計算（`--sweep` / `step_deg`）の出力

`events` の代わりに `groups`（現象ごと）があります。`observer` は指定したとおりの軌道です（TLE なら `m_deg` が TLE 本来の平均近点角）。

| 項目 | 内容 |
|---|---|
| `date`、`body` | 地心での合（新月・内合）の時刻、現象 |
| `n_phases`、`n_visible` | 計算した位相の数、見える位相の数 |
| `count_min`、`count_max` | 1 つの現象で見える回数の範囲（低軌道では周回ごとに繰り返し遭遇します） |
| `mag_min`、`mag_max`、`central_phases` | 最も深い食の食分の範囲、皆既・金環になる平均近点角の一覧 |
| `time_first`、`time_last` | 最大の時刻の範囲 |
| `rows` | 位相ごとの結果（`m_deg`、`count`、`best`＝最も深い現象、`events`） |

## 観測画像と比べる（`--at`）

ひので・SDO などの画像には撮影時刻（ミリ秒まで）があり、座標は太陽の北が上の太陽中心からの角距離（″）です。`--at` は、現象を探さずに、指定した時刻の月・惑星の位置を同じ向きで計算します（`--detail` の接触と同じ計算）。観測者と `--phenomena` の現象 1 つを指定します（`--global` は使えません）。

```bash
python cli.py --sscweb hinode --phenomena mercury --start 2006-11-08 --end 2006-11-09 \
    --at '8-Nov-2006 19:13:37.498 UT' --format json
```

2006 年 11 月 8 日の水星の太陽面通過で、ひので SOT の G バンド動画のこのコマでは水星は太陽の東の縁に入りかけています。結果の `at[0]` は `hpc_x` −852.1″、`hpc_y` −459.2″、`limb_dist` −1.05″（中心は縁の 1″ 内側）、`limb_depth` +0.21 です。

- **時刻の形**（大文字・小文字は区別せず、常に UTC）: ISO 8601（`2017-08-21T16:53:59.204Z`。`T` の代わりに空白、`Z` なし、`+00:00` も可）、`2017/08/21 16:53:59.204`、SolarSoft・ひのでのクイックルックの形（`21-Aug-2017 16:53:59.204 UT`、`21-AUG-2017 16:53:59.204`）。SDO（JSOC）の `2017.08.21_16:54:36.204_TAI` は TAI として読みます（末尾の `_TAI` がないと、UTC と 37 秒違うため受け付けません）。うるう秒（`2016-12-31T23:59:60.5`）も書けます。1972 年より前は UT です（ほかの入力と同じ）。
- 複数の時刻は `--at` にカンマ区切りで並べるか、`--at` を繰り返すか、`--at-file` で渡します。2,000 個まで、すべて 3 日以内に収めます。`--start` `--end` は省略でき、指定すると時刻がその期間内にあるかを確かめます。
- `--at-file ファイル`（`-` で標準入力）は 1 行に `時刻 [X Y]` です。`X Y` は画像で測った位置（太陽の北が上、″）で、書くと計算との差 O−C（`oc_x`、`oc_y`）と、それを動きの向きの時間に直した `oc_dt`（秒。正なら観測が計算より先に進んでいる）が出ます。区切りは空白・タブ・カンマ、`#` から行末までは注釈です。

  ```text
  # time                       X (″)    Y (″)
  8-Nov-2006 19:13:37.498 UT   -852.0   -459.0
  2006-11-08T19:14:00.000Z
  ```

- `--exposure 秒` は露光時間です。画像の時刻が露光の開始のとき、その半分を足した露光の中央で計算します。
- `--dry-run` では計算せず、時刻の解釈（`at_times`: `input`、`time`、`jd_tt`）と注意だけを出します。
- 人工衛星の軌道を NASA SSCWeb・JPL Horizons から取得するときは、その時刻の軌道データが必要です（ない時刻はエラー）。TLE は元期からの日数の注意が `warnings` に入ります。
- `--sun-radius` と `--planet-radius` は `limb_dist`・`limb_depth` に効きます（位置 `hpc_x`・`hpc_y` は半径によりません）。画像の目盛り（1 ピクセルの角度）や指向の誤差は計算に含まれないので、O−C には画像側の誤差も入ります。

出力（`--format json`）は `at`（時刻ごとの行）、`body`、`observer`、`warnings`、`params`、`ephemeris`、`delta_t_mid_s`、`exposure_s`、`request`（`times` などを含み、`--request` で同じ計算を再現できます）です。`at` の行の項目:

| 項目 | 内容 |
|---|---|
| `input`、`time`、`jd_tt` | 入力の文字列、計算した時刻（UTC、ミリ秒。`--exposure` があれば露光の中央）、TT のユリウス日 |
| `hpc_x`、`hpc_y` | 太陽の北が上の位置（″。`hpc_x` は太陽の西 = 画像の右が +） |
| `hpc_vx`、`hpc_vy`、`sep_rate` | 位置の動き（″/秒）、中心間角距離の変化（″/秒） |
| `p_angle`、`pa`、`pa_solar`、`xi`、`eta` | 太陽の P 角、天の北からの位置角、太陽の北からの位置角（°）、天の北が上の位置（″） |
| `sep_arcsec`、`limb_dist`、`limb_depth`、`rho_s`、`rho_b`、`rho_b_int` | 中心間角距離、縁からの距離、縁への入り込み（上の `contacts` の表）、視半径（″。`rho_b_int` は皆既・金環の内接に使う月の視半径で、惑星では `rho_b` と同じ） |
| `magnitude`、`obscuration`、`visible`、`vis` | 食分、食面積率、太陽が見えるか（地上は地平線の上、衛星は地球に隠されていない）、その余裕（°。正なら見える。地上は太陽高度 − `min_sun_alt_deg`、衛星は地球の縁（＋`earth_atm_km`）から太陽の中心までの離角） |
| `sun_alt`、`sun_az`、`parallactic`、`v_angle` | 地上: 太陽高度・方位、天頂の方向の位置角（°。天の北から東回り）、天頂角 V（°） |
| `sat_lat`、`sat_lon`、`sat_alt_km`、`earth_sep_deg`、`earth_pa`、`earth_radius_deg`、`earth_atm_deg`、`dist_earth_km` | 衛星: 衛星直下点・高度、太陽と地球中心の離角（°）、地球の中心の方向の位置角（°。天の北から東回り）、地球と大気（`earth_atm_km`）の視半径（°）、地心距離（km） |
| `measured_x`、`measured_y`、`oc_x`、`oc_y`、`oc_dt` | 測った位置を渡した行のみ: 測った位置、O−C（″）、O−C の時間（秒） |

`--format csv` は同じ項目の 1 行 1 時刻（時刻の列は `time_utc`、`visible` は 1/0）、`--format table` は時刻（`--tz` の時刻帯）・X・Y・縁からの距離・深さ・位置角・P 角・見えるか（と O−C）の表です。

画面の Web API では、計算した現象（`/api/search` などの `id`）について `POST /api/event/{id}/at` に `{"times": ["8-Nov-2006 19:13:37.498 UT"], "measured": [[-852.0, -459.0]], "exposure_s": 0.1}`（`measured`・`exposure_s` は省略可。`measured` の要素は `null` でもよい）を送ると、`{"rows": [上の行], "warnings": []}` が返ります。地球全体の日食（`kind: "global"`）には使えません（400）。

## 検証のしかた

計算を検証したいときは、次の方法があります。

- **計算機自体の検証**: `python tests/test_validation.py`（NASA の公表値との比較）、`python tests/test_cli.py`（CLI が画面と同じ結果を返すこと、`--at` が接触と同じ位置を返すこと）、`python tests/test_geometry.py`（P 角・太陽の北が上の位置・接触の時刻の半径への感度・うるう秒など）、`python tests/test_orbit_planning.py`、`python tests/test_hinode.py`（「ひので」の実観測との比較。2006 年の水星の太陽面通過の接触は画像から測った時刻と 1.5 秒以内。要インターネット）。どれも全項目が `PASS` になることを確かめます。
- **観測画像との比較**: 画像の撮影時刻を `--at` で渡し、画像で測った位置を `--at-file` の `X Y` に書くと、O−C（″）と時間の差 `oc_dt`（秒）が出ます（[観測画像と比べる](#観測画像と比べる--at)）。
- **地球全体の結果**: `--global` の日食の時刻・γ・食分・中心食の継続時間は、NASA の日食カタログ（Espenak & Meeus）と比べられます（一致の程度は [README](../README.ja.md) の「精度と検証」）。
- **地上の結果**: 同じ日食を `--global` で計算し、その地点が中心食帯の中にあるか、最大の時刻が `p1`〜`p4` に入っているかなどで整合を確かめられます。
- **衛星の結果**: 衛星の位置の誤差がそのまま時刻の誤差になります（低軌道で沿軌道 7.5 km ≒ 1 秒）。TLE なら元期が現象に近いもので計算し直して差を見ます。打ち上げ前や TLE の元期から何週間も先なら、`--sweep` で位相による結果の幅を示します。
- **入力の解釈**: `--dry-run` の `observer` を人から受け取った情報と照合します。
