@echo off
chcp 65001 >nul
setlocal
rem 日食・太陽面通過 精密計算機の起動（Windows）
rem
rem 配布用パッケージ（tools\build_bundles.py で作成）には Python とライブラリが同梱されていて、そのまま起動します。
rem 同梱されていない場合（ソース一式）は、初回だけ uv（Python の実行環境の管理ツール）、Python 本体、
rem 必要なライブラリをこのフォルダの .runtime\ に準備します（ディスク使用量 約 200 MB）。
rem どちらもシステムの設定やほかの Python には手を加えません。不要になったらフォルダごと削除してください。
rem uv を更新するときは UV_VERSION と UV_SHA256（リリースページの .sha256）を書き換え、start.command も揃えます。
cd /d "%~dp0"

if exist "run.py" goto extracted
echo ZIP ファイルの中から直接起動しています。ZIP を右クリックして「すべて展開」し、
echo 展開したフォルダの中の start.bat を起動してください。
goto end
:extracted

set "RUNTIME=%~dp0.runtime"
set "BUNDLED_PY=%RUNTIME%\python-windows-x86_64\python.exe"
if not exist "%BUNDLED_PY%" goto setup
rem -E -s: 環境変数やユーザーの site-packages に影響されないようにする
"%BUNDLED_PY%" -E -s run.py %*
if errorlevel 1 goto end
exit /b 0

:setup
set "UV_VERSION=0.12.21"
set "UV_SHA256=5d223efa0bf00208c3853246af09420419dfbd352536aa6bb8163d6170e23890"
set "UV_URL=https://github.com/astral-sh/uv/releases/download/%UV_VERSION%/uv-x86_64-pc-windows-msvc.zip"
set "PYTHON_REQUEST=cpython-3.12-windows-x86_64-none"

set "UV_DIR=%RUNTIME%\uv-%UV_VERSION%"
set "UV_EXE=%RUNTIME%\uv-%UV_VERSION%\uv.exe"
set "VENV=%RUNTIME%\venv"
set "PY=%RUNTIME%\venv\Scripts\python.exe"

rem uv が使う場所をすべて .runtime\ の中にする
set "UV_CACHE_DIR=%RUNTIME%\cache"
set "UV_PYTHON_INSTALL_DIR=%RUNTIME%\python"
set "UV_MANAGED_PYTHON=1"
set "UV_PYTHON_INSTALL_BIN=0"
set "UV_PYTHON_INSTALL_REGISTRY=0"
rem 社内ネットワークの TLS 検査などに対応するため、OS の証明書ストアで検証する
set "UV_SYSTEM_CERTS=1"
set "VIRTUAL_ENV="

if exist "%UV_EXE%" goto have_uv
echo 初回の準備: uv %UV_VERSION% をダウンロードしています...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12; $zip = Join-Path $env:RUNTIME 'uv-download.zip'; $tmp = $env:UV_DIR + '.tmp'; New-Item -ItemType Directory -Force $env:RUNTIME | Out-Null; Invoke-WebRequest -UseBasicParsing -Uri $env:UV_URL -OutFile $zip; if ((Get-FileHash -Algorithm SHA256 $zip).Hash -ne $env:UV_SHA256) { Remove-Item $zip; throw 'SHA-256 mismatch' }; if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }; Expand-Archive -Force $zip $tmp; Remove-Item $zip; Move-Item $tmp $env:UV_DIR"
if errorlevel 1 goto fail_uv
if not exist "%UV_EXE%" goto fail_uv
:have_uv

if exist "%PY%" goto have_venv
echo 初回の準備: Python をダウンロードしています...
"%UV_EXE%" venv --quiet --python %PYTHON_REQUEST% "%VENV%"
if errorlevel 1 goto fail_python
:have_venv

"%UV_EXE%" pip install --quiet --python "%PY%" -r requirements.txt
if errorlevel 1 goto deps_failed
type nul > "%VENV%\.deps-ok"
goto run

:deps_failed
if not exist "%VENV%\.deps-ok" goto fail_deps
echo ライブラリの更新を確認できなかったため、インストール済みのライブラリで起動します。

:run
"%PY%" run.py %*
rem 正常に終了したとき（ブラウザの画面をすべて閉じたときなど）はウィンドウも閉じる。エラーのときは表示を残す
if errorlevel 1 goto end
exit /b 0

:fail_uv
echo.
echo uv をダウンロードできませんでした。インターネット接続を確認して、もう一度 start.bat を実行してください。
goto end

:fail_python
echo.
echo Python を準備できませんでした。インターネット接続を確認して、もう一度 start.bat を実行してください。
echo 繰り返し失敗する場合は .runtime フォルダを削除してからやり直してください。
goto end

:fail_deps
echo.
echo 必要なライブラリをインストールできませんでした。インターネット接続を確認して、もう一度 start.bat を実行してください。
goto end

:end
pause
