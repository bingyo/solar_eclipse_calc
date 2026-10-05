#!/bin/sh
# 日食・太陽面通過 精密計算機の起動（macOS / Linux）
#
# macOS ではこのファイルをダブルクリックすると「ターミナル」で起動します（Linux では sh start.command）。
# 配布用パッケージ（tools/build_bundles.py で作成）には Python とライブラリが同梱されていて、そのまま起動します。
# 同梱されていない場合（ソース一式）は、初回だけ uv（Python の実行環境の管理ツール）、Python 本体、
# 必要なライブラリをこのフォルダの .runtime/ に準備します（ディスク使用量 約 200 MB）。
# どちらもシステムの設定やほかの Python には手を加えません。不要になったらフォルダごと削除してください。
# uv を更新するときは UV_VERSION と各 SHA-256（リリースページの .sha256）を書き換え、start.bat も揃えます。

UV_VERSION=0.12.21
PYTHON_VERSION=3.12

cd "$(dirname "$0")" || exit 1
RUNTIME="$PWD/.runtime"

fail() {
    printf '\n%s\n' "$1"
    if [ -t 0 ]; then
        printf 'Enter キーを押すと終了します。'
        read -r _
    fi
    exit 1
}

case "$(uname -s)" in
    Darwin) os=macos ;;
    Linux)  os=linux ;;
    *)      os=unknown ;;
esac
arch=$(uname -m)
# Apple シリコンの Mac では、Rosetta 経由で起動されても arm64 版を使う
if [ "$os" = macos ] && [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ]; then arch=aarch64; fi
case "$arch" in
    arm64|aarch64) arch=aarch64 ;;
    amd64|x86_64)  arch=x86_64 ;;
esac

BUNDLED_PY="$RUNTIME/python-$os-$arch/bin/python3.12"

# ダウンロードした ZIP から展開したファイルには「インターネットから入手」の印（quarantine 属性）が付き、
# 署名のない start.command や同梱の Python は「壊れているため開けません」と止められる。
# 初回にターミナルから sh start.command で起動したときにこのフォルダの印を外し、
# 次からはダブルクリックで起動できるようにする
if [ "$os" = macos ] && { xattr -p com.apple.quarantine "$PWD/start.command" ||
                          xattr -p com.apple.quarantine "$BUNDLED_PY"; } >/dev/null 2>&1; then
    xattr -dr com.apple.quarantine "$PWD"
    [ -x "$PWD/start.command" ] || chmod +x "$PWD/start.command"
fi

if [ -f "$BUNDLED_PY" ]; then
    [ -x "$BUNDLED_PY" ] || chmod +x "$BUNDLED_PY"
    # -E -s: 環境変数やユーザーの site-packages に影響されないようにする
    "$BUNDLED_PY" -E -s run.py "$@" || fail '終了しました。'
    exit 0
fi

UV_DIR="$RUNTIME/uv-$UV_VERSION"
UV="$UV_DIR/uv"
VENV="$RUNTIME/venv"
PY="$VENV/bin/python"

download() {  # url output
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 --proto '=https' -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -O "$2" "$1"
    else
        echo 'curl または wget が必要です。'
        return 1
    fi
}

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1"
    else
        shasum -a 256 "$1"
    fi | awk '{print $1}'
}

install_uv() {
    case "$os-$arch" in
        macos-aarch64) target=aarch64-apple-darwin;      sum=b88bda573e566ef9bced66b155fe0408626fbbc053aee1c30ba686f0728c9447 ;;
        macos-x86_64)  target=x86_64-apple-darwin;       sum=2b336763b396ec6afa20c5a8b083538ca7402445b868311979d740a4344c17d8 ;;
        linux-x86_64)  target=x86_64-unknown-linux-gnu;  sum=23f02075b652bb1df64178cfae41b5caf160822e720e2663568f3f5d63bc52c0 ;;
        linux-aarch64) target=aarch64-unknown-linux-gnu; sum=030b69227b40af8c1981b7301793dc66e71ed3c796ea8688209dd268bd91ec51 ;;
        *) echo "この OS（$(uname -s) $(uname -m)）には対応していません。"; return 1 ;;
    esac
    archive="$RUNTIME/uv-download.tar.gz"
    tmp="$UV_DIR.tmp"
    mkdir -p "$RUNTIME" || return 1
    download "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$target.tar.gz" "$archive" || return 1
    if [ "$(sha256 "$archive")" != "$sum" ]; then
        echo 'ダウンロードしたファイルのチェックサムが一致しません。'
        rm -f "$archive"
        return 1
    fi
    rm -rf "$tmp" && mkdir -p "$tmp" &&
        tar -xzf "$archive" -C "$tmp" --strip-components 1 &&
        rm -f "$archive" &&
        mv "$tmp" "$UV_DIR"
}

if [ ! -x "$UV" ]; then
    echo "初回の準備: uv $UV_VERSION をダウンロードしています..."
    install_uv && [ -x "$UV" ] ||
        fail 'uv をダウンロードできませんでした。インターネット接続を確認して、もう一度実行してください。'
fi

# uv が使う場所をすべて .runtime/ の中にする
export UV_CACHE_DIR="$RUNTIME/cache"
export UV_PYTHON_INSTALL_DIR="$RUNTIME/python"
export UV_MANAGED_PYTHON=1
export UV_PYTHON_INSTALL_BIN=0
# 社内ネットワークの TLS 検査などに対応するため、OS の証明書ストアで検証する
export UV_SYSTEM_CERTS=1
unset VIRTUAL_ENV

if [ ! -x "$PY" ]; then
    echo '初回の準備: Python をダウンロードしています...'
    "$UV" venv --quiet --python "$PYTHON_VERSION" "$VENV" ||
        fail 'Python を準備できませんでした。インターネット接続を確認して、もう一度実行してください。
繰り返し失敗する場合は .runtime フォルダを削除してからやり直してください。'
fi

if "$UV" pip install --quiet --python "$PY" -r requirements.txt; then
    : > "$VENV/.deps-ok"
elif [ -f "$VENV/.deps-ok" ]; then
    echo 'ライブラリの更新を確認できなかったため、インストール済みのライブラリで起動します。'
else
    fail '必要なライブラリをインストールできませんでした。インターネット接続を確認して、もう一度実行してください。'
fi

"$PY" run.py "$@" || fail '終了しました。'
