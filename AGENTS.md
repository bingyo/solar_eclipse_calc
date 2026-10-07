# AI エージェント向けの案内

このリポジトリは、日食と水星・金星の太陽面通過を高精度に計算するツールです（JPL DE440 暦。地上の地点、人工衛星、探査機、地球全体から）。

## 人から計算を頼まれたとき

画面（`run.py` の Web UI）は使わず、コマンドライン `cli.py` で計算してください。手順・入力の対応・出力の項目は [docs/cli.md](docs/cli.md) にあります。要点:

1. 人から受け取った情報（TLE、軌道要素、高度・太陽同期・昇交点の地方時、NORAD 番号、静止経度、探査機の ID、地点、期間）を、docs/cli.md の「入力情報と観測者の対応」に従って引数にします。TLE は要素を取り出さずに `--tle` でそのまま渡します。
2. まず `--dry-run --format json` で、周期・高度・傾斜角などの解釈が人の情報と合っているか確かめます。
3. `--format json` で計算し、終了コード（0 = 計算した、1 = 入力を計算できなかった、2 = 引数の誤り）と `ok` を確認します。`warnings` は必ず人に伝えます。
4. 時刻は UTC で返ります。人の地域の時刻に直して報告し、計算条件（出力の `request`）を添えます。同じ計算は `--request` で再現できます。

```bash
python cli.py --tle iss.txt --start 2026-10-01 --end 2027-10-01 --dry-run --format json
python cli.py --tle iss.txt --start 2026-10-01 --end 2027-10-01 --format json --detail
```

計算機自体を検証するときは `python tests/test_cli.py` と `python tests/test_validation.py` を実行します（すべて `PASS` になること）。

## 開発するとき

- 計算処理は `eclipsecalc/`、Web API は `eclipsecalc/server.py`、画面は `static/`。`cli.py` は `server.search` / `server.phase_sweep` を呼ぶだけなので、計算を変えたら画面と CLI の両方に反映されます。
- テストは `tests/` のスクリプトを `python tests/<名前>.py` で実行します（pytest は不要）。`test_hinode.py` とテストの一部は初回にインターネットが必要です。
- 文書・画面・メッセージは日本語です。
