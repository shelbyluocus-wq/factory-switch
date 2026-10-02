"""Run only the frozen app's import/resource check, never real account operations."""
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent.parent
relative = ('dist/Factory Switch.app/Contents/MacOS/Factory Switch'
            if sys.platform == 'darwin' else 'dist/Factory Switch/Factory Switch.exe')
app = root / relative
if not app.is_file():
    raise SystemExit(f'Missing packaged executable: {relative}')
subprocess.run([str(app), '--smoke-test'], check=True, timeout=90)
print('Frozen application native imports and bundled UI resources: OK')
