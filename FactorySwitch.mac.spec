# Run on macOS using Build-mac.command; builds for the host Python architecture.
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

if sys.platform != 'darwin':
    raise SystemExit('This application bundle must be built on macOS.')

root = Path(SPECPATH)
a = Analysis([str(root / 'gui.py')], pathex=[str(root)],
             datas=[(str(root / 'ui'), 'ui')] + collect_data_files('webview'),
             hiddenimports=['webview.platforms.cocoa'],
             excludes=['webview.platforms.winforms', 'webview.platforms.edgechromium',
                       'webview.platforms.gtk', 'webview.platforms.qt', 'clr', 'pythonnet'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Factory Switch',
          console=False, strip=False, upx=False)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Factory Switch')
app = BUNDLE(collection, name='Factory Switch.app',
             bundle_identifier='local.factory-account-switcher',
             info_plist={'CFBundleShortVersionString': '0.2.0',
                         'CFBundleVersion': '2',
                         'LSMinimumSystemVersion': '13.0',
                         'NSHighResolutionCapable': True,
                         'NSAppleEventsUsageDescription': '切换账号前，请求 Factory 正常退出以安全保存登录状态。'})
