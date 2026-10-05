# Mac 版の署名と公証（Developer ID）

Mac 版（`日食計算機.app`）を Apple の Developer ID で署名し、Apple の公証（notarization）を受けると、ダウンロードした人はダブルクリック →「開く」だけで使えます（ターミナルや「プライバシーとセキュリティ」での許可は不要です）。
署名・公証は **Windows でも Mac でも** `tools/build_bundles.py` で行えます（[rcodesign](https://github.com/indygreg/apple-platform-rs) を自動でダウンロードして使います）。

必要なもの:

- Apple Developer Program の登録（個人の場合は本人がアカウント所有者）
- 証明書を作るための Mac（「キーチェーンアクセス」を使います。1 回だけ）
- ビルドする PC（uv と git。いつものビルド環境）

## 1. Developer ID Application 証明書を作る（Mac で 1 回だけ）

Developer ID 証明書を作れるのは、チームの「アカウント所有者（Account Holder）」だけです。

1. Mac で「キーチェーンアクセス」を開き、メニューの「キーチェーンアクセス」→「証明書アシスタント」→「認証局に証明書を要求…」を選びます。
2. 「ユーザのメールアドレス」と「通称」（名前）を入力し、「ディスクに保存」を選んで「続ける」を押します。`CertificateSigningRequest.certSigningRequest` が保存されます。
3. ブラウザで [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/certificates/list) を開き、「Certificates」の「＋」を押します。
4. 「Developer ID Application」を選んで「Continue」→ Profile Type は「G2 Sub-CA」のまま → 手順 2 のファイルをアップロード →「Continue」→「Download」で `developerID_application.cer` を保存します。
5. ダウンロードした `.cer` をダブルクリックして、キーチェーン（ログイン）に追加します。
6. キーチェーンアクセスの「自分の証明書」で「Developer ID Application: 名前 (チーム ID)」を右クリック →「書き出す」→ ファイルフォーマット「個人情報交換 (.p12)」で保存し、パスワードを設定します。
7. 書き出した `.p12` を、ビルドする PC に USB メモリなどで移します。

## 2. App Store Connect API キーを作る（公証用、1 回だけ）

1. [App Store Connect](https://appstoreconnect.apple.com/access/integrations/api) の「ユーザとアクセス」→「統合」→「App Store Connect API」を開きます（初回は「アクセスをリクエスト」が必要な場合があります）。
2. 「チームキー」で「＋」（API キーを生成）を押し、名前（例: notarize）とアクセス「Developer」を指定して生成します。
3. 「API キーをダウンロード」で `AuthKey_XXXXXXXXXX.p8` を保存します（**ダウンロードできるのは 1 回だけ**です）。
4. 同じ画面に表示される「Issuer ID」（UUID 形式）と、キーの「キー ID」（10 文字）を控えます。

## 3. 署名用の設定ファイルを置く（ビルドする PC）

リポジトリの**外**にフォルダを作り（例: `C:\Users\<名前>\mac-signing\`）、次のファイルを置きます。これらは秘密情報です。git に追加したり、人に渡したりしないでください。

| ファイル | 中身 |
|---|---|
| `developer_id_application.p12` | 手順 1 で書き出した証明書 |
| `p12-password.txt` | `.p12` のパスワードだけを書いたテキスト（改行なし） |
| `AuthKey_XXXXXXXXXX.p8` | 手順 2 でダウンロードした API キー |
| `signing.json` | 下の設定（[packaging/macos/signing.example.json](../packaging/macos/signing.example.json) をコピーして書き換え） |

```json
{
  "p12_file": "developer_id_application.p12",
  "p12_password_file": "p12-password.txt",
  "api_issuer_id": "（Issuer ID）",
  "api_key_id": "（キー ID）",
  "api_private_key_file": "AuthKey_XXXXXXXXXX.p8",
  "bundle_id": "jp.example.solareclipsecalc"
}
```

ファイル名は `signing.json` からの相対パスです。`bundle_id` はアプリを区別する名前で、自分のドメインを逆にした形（例: `jp.<名前>.solareclipsecalc`）にします。一度決めたら変えないでください。

## 4. 署名・公証してビルドする

```bash
python tools/build_bundles.py --target macos --sign C:\Users\<名前>\mac-signing\signing.json
```

Developer ID で署名したあと Apple に送って公証を受け、結果（チケット）をアプリに添付（staple）してから `dist/solar_eclipse_calc-<版>-macos.zip` を作ります。公証は通常数分で終わります。
署名だけ確かめたいときは `--no-notarize` を付けます。

## 5. Mac で確かめる

ZIP を Google Drive などに Web ブラウザでアップロードし、Mac の **Safari などのブラウザ**でダウンロードします。USB メモリで移すと「インターネットから入手」の印が付かず、確認になりません。リモートデスクトップの「Windows App」や、クラウドドライブ・メッセージアプリのアプリから保存すると、下の「開けません」になります。
ZIP をダブルクリックして展開し、「日食計算機」を「アプリケーション」フォルダに入れてダブルクリックします。
「インターネットからダウンロードされたアプリケーションです。開いてもよろしいですか？」と表示されて「開く」で起動できれば完了です。

## うまくいかないとき

- **`incorrect password given when decrypting PFX data`（パスワードで .p12 を開けません）と出る**: まず、`p12-password.txt` が `.p12` を書き出したときに設定したパスワードと一致しているか確認してください。書き出しの途中で求められる Mac のログインパスワードとは別のものです。次のコマンドで `Mac verify error: invalid password?` と出る場合は、パスワードが一致していません（Mac で書き出し直すか、`p12-password.txt` を直してください）。

  ```bash
  openssl pkcs12 -in developer_id_application.p12 -info -noout -passin file:p12-password.txt
  ```

  このコマンドでは開けるのに署名で同じエラーが出る場合は、`.p12` が新しい暗号形式（AES）で書き出されています。Git for Windows の openssl で旧形式に書き出し直してください（途中の `tmp.pem` には秘密鍵が入るので、終わったら削除します）。

  ```bash
  openssl pkcs12 -in developer_id_application.p12 -nodes -out tmp.pem
  openssl pkcs12 -export -legacy -in tmp.pem -out developer_id_application.p12
  ```

- **`not signed by Apple` / `Developer ID` と出る**: 「Developer ID Application」以外の証明書（Apple Development など）を書き出しています。手順 1 の 6 で選ぶ証明書を確認してください。
- **Mac で「アプリケーション"日食計算機"を開けません」と出る**（`open` では error -10810、`spctl --assess` では `File created by an AppSandbox, exec/open not allowed`）: ZIP をサンドボックス化されたアプリ（App Store 版の「Windows App」など）で Mac に保存したため、macOS が実行を禁止しています。`xattr -p com.apple.quarantine <ZIP>` の 3 つ目の項目が保存したアプリ名です。ブラウザでダウンロードし直すか、`xattr -dr com.apple.quarantine "/Applications/日食計算機.app"` で印を外してください。署名や公証の問題ではありません。
- **公証が `Invalid` になる**: 表示される submission ID で詳細を確認できます: `build/cache/rcodesign-0.29.0/rcodesign notary-log --api-key-file <API キーの JSON> <submission ID>`（API キーの JSON は `rcodesign encode-app-store-connect-api-key` で作ります）。

## 仕組み（メンテナンス用）

- `日食計算機.app/Contents/MacOS/launcher` は [packaging/macos/launcher.c](../packaging/macos/launcher.c) を zig（PyPI の `ziglang`）で arm64 / x86_64 向けにコンパイルした universal バイナリで、`Contents/Resources/launch.sh` を実行するだけです（Apple はアプリの本体に署名済みの Mach-O を求めるため）。
- [launch.sh](../packaging/macos/launch.sh) が CPU に合った同梱 Python で `run.py --app` をバックグラウンドで起動します。追加の暦やキャッシュは `~/Library/Application Support/SolarEclipseCalc`、ログは `~/Library/Logs/SolarEclipseCalc.log` に置きます（署名済みのアプリの中は変更しません）。
- 署名では、同梱 Python を含むすべての Mach-O（61 個）に hardened runtime とタイムスタンプを付け、`python3.12` には ctypes 用の `com.apple.security.cs.allow-unsigned-executable-memory` を付けます。
- ダウンロードしたままの場所（App Translocation）で開かれた場合は、「アプリケーション」フォルダへの移動を求めて終了します。
- 画面（ブラウザのタブ）は開いている間 `/api/page/stream`（Server-Sent Events）につなぎ続けます。タブやブラウザを閉じるとブラウザが接続を切るので、開いている画面がなくなって 10 秒たつと計算機は終了します（再読み込みではすぐにつなぎ直すため終了しません）。Windows の `start.bat` やソース一式の `start.command` でも同じです。
