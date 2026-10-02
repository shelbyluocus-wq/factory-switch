# Run on macOS using Build-mac.command; builds for the host Python architecture.
import sys
import subprocess
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

# Intel cryptography may be built against Homebrew OpenSSL. Python's bundled
# OpenSSL has the same dylib filenames but can be older. Retain the libraries
# actually used by cryptography instead of letting filename deduplication choose
# Python's copy (which causes missing symbols such as SSL_get0_group_name).
import cryptography.hazmat.bindings._rust as rust_binding
linked = subprocess.check_output(['otool', '-L', rust_binding.__file__], text=True)
openssl = {}
for line in linked.splitlines()[1:]:
    source = line.strip().split(' (', 1)[0]
    library = Path(source)
    if library.is_absolute() and library.is_file() and library.name in ('libssl.3.dylib', 'libcrypto.3.dylib'):
        openssl[library.name] = str(library.resolve())
if openssl:
    if set(openssl) != {'libssl.3.dylib', 'libcrypto.3.dylib'}:
        raise SystemExit('Incomplete dynamically linked OpenSSL pair; refusing to bundle mismatched libraries.')
    a.binaries = [entry for entry in a.binaries if Path(entry[0]).name not in openssl]
    a.binaries += [(name, source, 'BINARY') for name, source in openssl.items()]
    print('Bundling cryptography-linked OpenSSL:', openssl)
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
