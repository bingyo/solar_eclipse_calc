# Cloudflare で公開する

画面（Web UI）を [Cloudflare Containers](https://developers.cloudflare.com/containers/) で動かすと、PC にインストールしなくても URL を開くだけで使えます。
GitHub に push すると、GitHub Actions がコンテナのイメージを作って公開し直します。

- `Dockerfile` … 画面のコンテナ（Python 3.12、`requirements.txt`、JPL 暦 de440s と de440 を同梱）
- `cloudflare/` … すべての要求をコンテナに渡す Worker（`src/index.js`）と設定（`wrangler.jsonc`）
- `.github/workflows/deploy-cloudflare.yml` … イメージを作り、起動と計算を確かめてから公開

## 必要なもの

- Cloudflare の **Workers Paid プラン**（月 5 ドル。Containers は無料プランでは使えません）
- GitHub のリポジトリ（このリポジトリ）

PC に Node.js や Docker は要りません。

## はじめて公開する

1. **API トークンを作ります。** Cloudflare のダッシュボードで「My Profile → API Tokens → Create Token」を開き、テンプレート「Edit Cloudflare Workers」を選びます。「Account Resources」は公開するアカウントだけにします。
   初回の公開がコンテナの権限不足（Authentication error など）で失敗するときは、トークンを編集し、Account の権限に Containers の編集を加えます。
2. **アカウント ID を控えます。** ダッシュボードの「Workers & Pages」の右側、またはアカウントのホームの「Account ID」です。
3. **GitHub に登録します。** リポジトリの「Settings → Secrets and variables → Actions → New repository secret」で、次の 2 つを登録します。
   - `CLOUDFLARE_API_TOKEN` … 手順 1 のトークン
   - `CLOUDFLARE_ACCOUNT_ID` … 手順 2 の ID
4. **公開します。** リポジトリの「Actions → Deploy to Cloudflare → Run workflow」を押します。10 分ほどで終わります。
5. **開きます。** `https://solar-eclipse-calc.<アカウントのサブドメイン>.workers.dev/` です（Actions の記録の最後と、ダッシュボードの「Workers & Pages → solar-eclipse-calc」に出ます）。
   初回の公開の直後は、コンテナが使えるようになるまで数分かかることがあります。

その後は、`main` に計算・画面・設定の変更を push するたびに自動で公開し直します。
Secrets を登録していない間（フォークしたリポジトリなど）は、ワークフローは何もせずに終わります。

## 動き方

- コンテナは 1 台で、全員がそれを使います。計算は 1 つずつ順に行います（PC 版と同じ）。
- 最後の操作から 15 分たつとコンテナが止まり、その間は料金がかかりません。次に開いたときに起動します（数秒〜十数秒）。
- 止まると、計算した現象をサーバーは忘れます。画面は一覧の計算条件から計算し直すので、そのまま使えます。
- 暦は de440s（1849〜2150 年）と de440（1550〜2650 年）を同梱しています。「詳細設定 → 暦」で選べます。
- CelesTrak・JPL Horizons・NASA SSCWeb にはコンテナから接続します。

## 料金の目安

Workers Paid プランの月 5 ドルには、毎月メモリ 25 GiB 時間、CPU 375 vCPU 分、ディスク 200 GB 時間が含まれます（[料金](https://developers.cloudflare.com/containers/pricing/)）。
既定の `basic`（1/4 vCPU・メモリ 1 GiB・ディスク 4 GB）では、コンテナが起きている時間が月 25 時間ほどまでなら、ほぼ 5 ドルの範囲に収まります。

`max_instances` を 1 にしているので、1 か月ずっと計算し続けられても、追加の料金は 20 ドルほどが上限の目安です（メモリ約 6 ドル、CPU 約 13 ドル、ディスク約 1 ドル）。

## 使う人を限る

URL を知っていれば誰でも使えます。自分や知り合いだけで使うときは、ダッシュボードの「Zero Trust → Access → Applications」で `solar-eclipse-calc.<サブドメイン>.workers.dev` にアプリケーションを作り、許可するメールアドレスを登録します（50 人までは無料）。
Workers の設定画面の「Domains & Routes」から workers.dev に Access を有効にすることもできます。

## 設定を変える

`cloudflare/wrangler.jsonc` と `cloudflare/src/index.js` を変えて push します。

- 計算が遅いとき … `"instance_type"` を `"standard-1"`（1/2 vCPU・4 GiB）に。料金はメモリの分が 4 倍になります
- 止まるまでの時間 … `src/index.js` の `sleepAfter`
- 名前（URL）… `wrangler.jsonc` の `"name"`

`max_instances` は 1 のままにしてください。計算した現象をコンテナのメモリに覚えているため、2 台以上にすると、詳細を開いたときに別のコンテナに届いて計算し直しが増えます。

## 手元から公開する

Node.js 22 以降と Docker があれば、PC からも公開できます。

```bash
cd cloudflare
npm install
npx wrangler login
npx wrangler deploy
```

コンテナだけを手元で試すときは、リポジトリの直下で次のようにします（http://localhost:8080/ で開きます）。

```bash
docker build -t eclipsecalc .
docker run --rm -p 8080:8080 eclipsecalc
```

## やめる

ダッシュボードの「Workers & Pages → solar-eclipse-calc → Settings → Delete」で削除します（`cloudflare/` で `npx wrangler delete` でも削除できます）。
自動で公開し直さないように、GitHub の Secrets も削除するか、Actions でワークフローを無効にしてください。
