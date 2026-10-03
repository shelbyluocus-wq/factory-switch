from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
a = Analysis([str(root / 'gui.py')], pathex=[str(root)],
             datas=[(str(root / 'ui'), 'ui')] + collect_data_files('webview'),
             hiddenimports=['webview.platforms.winforms', 'webview.platforms.edgechromium'],
             excludes=['webview.platforms.cocoa', 'webview.platforms.gtk', 'webview.platforms.qt'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='Factory Switch',
          console=False, strip=False, upx=False, icon=str(root / 'ui' / 'app-icon.ico'))
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='Factory Switch')
