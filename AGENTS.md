# AI エージェント向けの案内

このリポジトリは、日食と水星・金星の太陽面通過を高精度に計算するツールです（JPL DE440 暦。地上の地点、人工衛星、探査機、地球全体から）。

## 人から計算を頼まれたとき

画面（`run.py` の Web UI）は使わず、コマンドライン `cli.py` で計算してください。手順・入力の対応・出力の項目は [docs/cli.md](docs/cli.md) にあります。要点:

1. 人から受け取った情報（TLE、軌道要素、高度・太陽同期・昇交点の地方時、NORAD 番号、静止経度、探査機の ID、地点、期間）を、docs/cli.md の「入力情報と観測者の対応」に従って引数にします。TLE は要素を取り出さずに `--tle` でそのまま渡します。
2. まず `--dry-run --format json` で、周期・高度・傾斜角などの解釈が人の情報と合っているか確かめます。
3. `--format json` で計算し、終了コード（0 = 計算した、1 = 入力を計算できなかった、2 = 引数の誤り）と `ok` を確認します。人の言語に合わせて `--lang`（ja, en, fr, ru, es, zh, hi）を付けると、`warnings` と `error` がその言語になります。`warnings` は必ず人に伝えます。TLE の元期から何週間も先の現象なら、`--sweep 10` で衛星の位置による結果の幅も計算して伝えます。
4. 時刻は UTC で返ります。人の地域の時刻に直して報告し、計算条件（出力の `request`）を添えます。同じ計算は `--request` で再現できます。

```bash
python cli.py --tle iss.txt --start 2026-10-01 --end 2027-10-01 --dry-run --format json
python cli.py --tle iss.txt --start 2026-10-01 --end 2027-10-01 --format json --detail
```

計算機自体を検証するときは `python tests/test_cli.py` と `python tests/test_validation.py` を実行します（すべて `PASS` になること）。

## 開発するとき

- 計算処理は `eclipsecalc/`、Web API は `eclipsecalc/server.py`、画面は `static/`。`cli.py` は `server.search` / `server.phase_sweep` を呼ぶだけなので、計算を変えたら画面と CLI の両方に反映されます。
- `Dockerfile` と `cloudflare/` は画面を Cloudflare Containers で公開するためのもので、`main` への push で `.github/workflows/deploy-cloudflare.yml` が公開し直します（[docs/cloudflare.md](docs/cloudflare.md)）。コンテナは `run.py` をそのまま動かすので、実行時に新しいファイルやディレクトリが要るようにしたら `Dockerfile` と `.dockerignore` に加えます。
- テストは `tests/` のスクリプトを `python tests/<名前>.py` で実行します（pytest は不要）。`test_hinode.py` とテストの一部は初回にインターネットが必要です。
- 文書・画面・メッセージは日本語です。ただし `README.md` は英語で、日本語版は `README.ja.md` です（片方を変えたらもう片方も合わせ、画面例は日本語版が `docs/images/`、英語版が `docs/images/en/`）。画面は英語・フランス語・ロシア語・スペイン語・中国語・ヒンディー語にも切り替えられます。画面の文言は `t('日本語')` で書き、`static/i18n.js` の `I18N` に 6 言語の訳を加えます（`index.html` の固定の文言は自動で、マークアップを含む部分は `HTML_BLOCKS` で翻訳）。Web API のエラー・注意は `eclipsecalc/i18n.py` の `tr()` と `MESSAGES` で、画面の言語（`X-Lang` ヘッダー）に合わせます。`cli.py` の文言も `tr('日本語')` で書き、`MESSAGES` に 6 言語の訳を加えます（言語は `--lang`。訳の漏れは `tests/test_cli.py` が確かめます）。
