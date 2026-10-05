#!/bin/sh
# 日食計算機.app/Contents/Resources/launch.sh（Contents/MacOS/launcher から実行される）
#
# 同梱の Python で計算機をバックグラウンドで起動し、ブラウザで開く。終了は画面右上の「終了」。
# アプリの中は署名済みで変更できないため、追加の暦やキャッシュは ~/Library/Application Support に、
# ログは ~/Library/Logs に置く。

RES="$(cd "$(dirname "$0")" && pwd)"

# ダウンロードしたままの場所で開くと、macOS はアプリを一時的な場所にコピーして実行する
# （App Translocation）。バックグラウンドで動き続ける計算機が途中で消えないよう、移動を求める
case "$RES" in
    */AppTranslocation/*)
        osascript -e 'display alert "「日食計算機」を「アプリケーション」フォルダに移動してから開いてください。" message "Finder で、ダウンロードした「日食計算機」をサイドバーの「アプリケーション」にドラッグし、そこから開きます。"'
        exit 0 ;;
esac

arch=$(uname -m)
# Apple シリコンの Mac では、Rosetta 経由で起動されても arm64 版を使う
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null)" = 1 ]; then arch=aarch64; fi
case "$arch" in
    arm64|aarch64) arch=aarch64 ;;
    *)             arch=x86_64 ;;
esac

export ECLIPSECALC_DATA_DIR="$HOME/Library/Application Support/SolarEclipseCalc"
export ECLIPSECALC_LOG="$HOME/Library/Logs/SolarEclipseCalc.log"
mkdir -p "$ECLIPSECALC_DATA_DIR" "$HOME/Library/Logs"
if [ -f "$ECLIPSECALC_LOG" ] && [ "$(wc -c < "$ECLIPSECALC_LOG")" -gt 1000000 ]; then
    mv -f "$ECLIPSECALC_LOG" "$ECLIPSECALC_LOG.old"
fi
cd "$ECLIPSECALC_DATA_DIR" || exit 1

# -E -s: 環境変数やユーザーの site-packages に影響されないようにする
# -B: 署名済みのアプリの中にバイトコードを書き込まない（事前にコンパイル済み）
nohup "$RES/python-macos-$arch/bin/python3.12" -E -s -B "$RES/app/run.py" --app \
    >> "$ECLIPSECALC_LOG" 2>&1 < /dev/null &
