#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
trap 'echo "打包未完成，请查看上方错误。按回车关闭。"; read -r _' ERR
if [[ "$(uname -s)" != Darwin ]]; then
  echo "必须在 Mac 上打包，不能从 Windows 交叉生成 .app。"; exit 1
fi
if [[ ! -x .venv-mac/bin/python ]]; then
  echo "请先运行一次 Launch.command，安装依赖后关闭切号器，再运行此脚本。"
  read -r _; exit 1
fi
.venv-mac/bin/python -m pip install -r requirements.txt 'pyinstaller>=6.0,<7'
.venv-mac/bin/python -X utf8 -m unittest -v
.venv-mac/bin/python -m PyInstaller --noconfirm FactorySwitch.mac.spec
echo "已生成 dist/Factory Switch.app（当前 Mac 架构：$(uname -m)）。"
echo "此构建未做开发者签名和公证。请在本机检查启动、钥匙串授权和双账号切换。"
open dist
