"""The command line's own checks, with no Qt:  uv run python tests/test_cli.py
The fits run with a cp1252 console (the default on Western Windows) to make sure the
ΔE00 in the progress lines and the names in the messages survive it."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
failures = []


def check(name, ok, detail=''):
    print(('ok    ' if ok else 'FAIL  ') + name + (f' — {detail}' if detail and not ok else ''))
    if not ok:
        failures.append(name)


def cli(*args, console='utf-8'):
    return subprocess.run([sys.executable, str(ROOT / 'gr4_lutsmith.py'), *args],
                          capture_output=True, encoding='utf-8', errors='replace', cwd=ROOT,
                          env=dict(os.environ, PYTHONIOENCODING=console))


r = cli('--version')
check('--version', r.returncode == 0, r.stderr[-300:])
r = cli('slots')
check('slots', r.returncode == 0 and 'High Contrast' in r.stdout, r.stderr[-300:])

with tempfile.TemporaryDirectory(prefix='gr4-lutsmith-cli-') as d:
    r = cli('fit', 'examples/film-1d.cube', '--iters', '2', '-o', str(Path(d) / 'fit'), console='cp1252')
    check('fit survives a cp1252 console (ΔE00 in every progress line)',
          r.returncode == 0 and 'Traceback' not in r.stderr, r.stderr[-400:])
    r = cli('fit', 'examples/film-1d.cube', '--name', 'Плёнка ハ', console='cp1252')
    check('a non-ASCII refusal is worded, not a UnicodeEncodeError',
          r.returncode == 1 and r.stderr.startswith('refused:') and 'Traceback' not in r.stderr,
          r.stderr[-400:])

print('\n' + ('all checks passed' if not failures else f'{len(failures)} check(s) failed'))
sys.exit(1 if failures else 0)
