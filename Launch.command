#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
trap 'echo "启动未完成，请查看上方错误。按回车关闭。"; read -r _' ERR
if [[ "$(uname -s)" != Darwin ]]; then
  echo "此启动器仅用于 macOS。"; exit 1
fi
if [[ ! -x .venv-mac/bin/python ]]; then
  python_bin=""
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null; then
      python_bin="$(command -v "$candidate")"; break
    fi
  done
  if [[ -z "$python_bin" ]]; then
    echo "请先安装 Python 3.11 或更新版本：https://www.python.org/downloads/macos/"
    echo "安装后重新打开 Launch.command。按回车关闭。"; read -r _; exit 1
  fi
  "$python_bin" -m venv .venv-mac
fi
if ! .venv-mac/bin/python -c 'import webview, cryptography, AppKit, WebKit' >/dev/null 2>&1 ||
   ! cmp -s requirements.txt .venv-mac/installed-requirements.txt; then
  .venv-mac/bin/python -m pip install -r requirements.txt
  cp requirements.txt .venv-mac/installed-requirements.txt
fi
exec .venv-mac/bin/python -X utf8 gui.py
