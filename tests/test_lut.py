"""The .cube reader, checked without Qt:  uv run python tests/test_lut.py
Also locks in that a damaged header is refused with a reason, not an IndexError."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np  # noqa: E402
import gr4_lutsmith as gl  # noqa: E402

failures = []


def check(name, ok, detail=''):
    print(('ok    ' if ok else 'FAIL  ') + name + (f' — {detail}' if detail and not ok else ''))
    if not ok:
        failures.append(name)


def cube(text):
    f = tempfile.NamedTemporaryFile('w', suffix='.cube', delete=False, encoding='utf-8')
    f.write(text)
    f.close()
    return Path(f.name)


def rows3d(n):
    return '\n'.join(f'{r / (n - 1):.6f} {g / (n - 1):.6f} {b / (n - 1):.6f}'
                     for b in range(n) for g in range(n) for r in range(n))


def reads(text, label, **want):
    p = cube(text)
    try:
        lut = gl.Lut(p)
    except gl.Refused as exc:
        check(label, False, f'refused: {exc}')
        return None
    finally:
        p.unlink()
    for k, v in want.items():
        got = getattr(lut, k)
        ok = np.allclose(got, v) if isinstance(got, np.ndarray) else got == v
        check(f'{label}: {k}', bool(ok), repr(got))
    return lut


def refuses(text, label):
    p = cube(text)
    try:
        gl.Lut(p)
    except gl.Refused as exc:
        check(label, True)
        return str(exc)
    except Exception as exc:
        check(label, False, f'{type(exc).__name__}: {exc} (should be a Refused)')
    else:
        check(label, False, 'accepted')
    finally:
        p.unlink()
    return ''


# a good 3D and 1D file, with comments, a title, a domain and an unknown keyword
lut = reads('TITLE "film"\n# a comment\n\nLUT_3D_SIZE 2\nKAMERA X\n' + rows3d(2) + '\n',
            '3D file', kind='3D', n=2, title='film', ignored=['KAMERA'])
check('3D file: table shape', lut.table.shape == (2, 2, 2, 3), str(lut.table.shape))
identity1d = '\n'.join(f'{v:.6f} {v:.6f} {v:.6f}' for v in np.linspace(0, 1, 4))
reads(f'LUT_1D_SIZE 4\n{identity1d}\n', '1D file', kind='1D', n=33)

# lookup: identity in, identity out; outside the domain clamps; the mid-node hits its row exactly
lut = gl.Lut(cube('LUT_3D_SIZE 2\n' + rows3d(2) + '\n'))
pts = np.array([[0., 0., 0.], [1., 1., 1.], [0., 0.5, 1.]])
check('identity 3D: corners and a mid hit themselves', np.allclose(lut(pts), pts, atol=1e-9), str(lut(pts)))
check('identity 3D: interior interpolates to itself', np.allclose(lut(np.array([[0.3, 0.6, 0.9]])), [[0.3, 0.6, 0.9]], atol=1e-9))
check('identity 3D: outside clamps', np.allclose(lut(np.array([[-2., 2., 0.5]])), [[0., 1., 0.5]], atol=1e-9))
reads('LUT_3D_SIZE 2\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 2 2 2\n' + rows3d(2) + '\n',
      'domain 0..2', lo=(0., 0., 0.), hi=(2., 2., 2.))

# refusals: the ones the reader always had
base = 'LUT_3D_SIZE 2\n' + rows3d(2) + '\n'
refuses(base + rows3d(2)[-(3 * 8):] + '\n', 'extra data rows')
refuses('LUT_3D_SIZE 2\nLUT_1D_SIZE 2\n', 'combined 1D + 3D')
refuses('LUT_3D_SIZE 5\n' + rows3d(2) + '\n', 'row count does not match the size')
refuses('LUT_3D_SIZE 130\n' + rows3d(2) + '\n', '3D size above 129')
refuses('LUT_3D_SIZE 1\n0 0 0\n', '3D size below 2')
refuses('LUT_1D_SIZE 70000\n0 0 0\n', '1D size above 65536')
refuses('LUT_3D_SIZE 2\n0.5 0.5 0.5\n' + rows3d(2)[8:] + '\n', 'non-finite values', )
refuses('LUT_3D_SIZE 2\n' + '\n'.join(['2 0 0'] + rows3d(2).splitlines()[1:]) + '\n', 'output above 1.05')
refuses(base.replace('LUT_3D_SIZE 2', 'LUT_3D_SIZE'), 'size without a value')
refuses(base.replace('LUT_3D_SIZE 2', 'LUT_3D_SIZE two'), 'size that is not a number')
refuses(base + 'DOMAIN_MIN 0 0\n', 'domain with two values')
refuses(base + 'DOMAIN_MAX 0 0 0 0\n', 'domain with four values')
refuses(base + 'DOMAIN_MIN 0 x 0\n', 'domain that is not numbers')
refuses(base + 'LUT_3D_INPUT_RANGE 0.0\n', 'input range with one value')
refuses(base + 'LUT_3D_INPUT_RANGE 0.0 1.0 2.0\n', 'input range with three values')
refuses(base + 'LUT_1D_INPUT_RANGE low high\n', 'input range that is not numbers')
refuses('TITLE x\n0 0 0\n', 'no size at all')
refuses(base.replace('LUT_3D_SIZE 2\n', '', 1) + '1 2\n', 'data row with two numbers')

print('\n' + ('all checks passed' if not failures else f'{len(failures)} check(s) failed'))
sys.exit(1 if failures else 0)
