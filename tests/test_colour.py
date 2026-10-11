"""The colour math, checked against published constants:  uv run python tests/test_colour.py
CIEDE2000 comes from the test pairs in Sharma, Wu & Dalal 2005 (the paper's own
companion data); the Lab anchors are the sRGB D65 primaries."""
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


# ---------------------------------------------------------------- sRGB transfer: piecewise, and each side inverts
x = np.linspace(0, 1, 101)
check('encode inverts decode', np.allclose(gl.srgb_enc(gl.srgb_dec(x)), x, atol=1e-7), str(np.abs(gl.srgb_enc(gl.srgb_dec(x)) - x).max()))
check('the linear segment meets the power segment at 0.04045',
      abs(gl.srgb_dec(np.array([0.04045]))[0] - 0.04045 / 12.92) < 1e-12)
check('the other boundary lands where the segments meet',
      abs(float(gl.srgb_enc(np.array([0.0031308]))[0]) - 0.040449936) < 1e-9)
check('0 and 1 stay put', np.allclose(gl.srgb_dec(np.array([0., 1.])), [0., 1.], atol=1e-9)
      and np.allclose(gl.srgb_enc(np.array([0., 1.])), [0., 1.], atol=1e-9))

# ---------------------------------------------------------------- Lab: the anchors every colour book prints
lab = lambda rgb: gl.lab(np.array([rgb], float))[0]
check('white is L*100, a and b 0', np.allclose(lab([1., 1., 1.]), [100., 0., 0.], atol=1e-4), str(lab([1., 1., 1.])))
check('black is all zero', np.allclose(lab([0., 0., 0.]), [0., 0., 0.]), str(lab([0., 0., 0.])))
check('red is L 53.24, a 80.09, b 67.20', np.allclose(lab([1., 0., 0.]), [53.2408, 80.0925, 67.2032], atol=1e-3), str(lab([1., 0., 0.])))
check('blue is L 32.30, a 79.19, b -107.86', np.allclose(lab([0., 0., 1.]), [32.2970, 79.1875, -107.8602], atol=1e-3), str(lab([0., 0., 1.])))

# ---------------------------------------------------------------- CIEDE2000 against the paper's pairs
pairs = [
    ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
    ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
    ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.1736, 0.5854), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2972, 0.0000), 1.0000),
    ((50.0000, 2.5000, 0.0000), (73.0000, 25.0000, -18.0000), 27.1492),
    ((50.0000, 2.5000, 0.0000), (61.0000, -5.0000, 29.0000), 22.8977),
    ((50.0000, 2.5000, 0.0000), (56.0000, -27.0000, -3.0000), 31.9030),
    ((50.0000, 2.5000, 0.0000), (58.0000, 24.0000, 15.0000), 19.4535),
]
worst = max(abs(float(gl.de00(np.array([a]), np.array([b]))[0]) - want) for a, b, want in pairs)
check('the Sharma, Wu & Dalal pairs to their 4th decimal', worst < 5e-5, str(worst))
l1, l2, _ = pairs[5]
check('the measure is symmetric', abs(float(gl.de00(np.array([l1]), np.array([l2]))[0])
                                      - float(gl.de00(np.array([l2]), np.array([l1]))[0])) < 1e-9)
check('a colour differs from itself by 0', float(gl.de00(np.array([l1]), np.array([l1]))[0]) == 0.0)

# ---------------------------------------------------------------- an identity LUT rides the whole pipeline unchanged
def cube_file(text):
    f = tempfile.NamedTemporaryFile('w', suffix='.cube', delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


rows = '\n'.join(f'{r:.6f} {g:.6f} {b:.6f}' for b in (0, 1) for g in (0, 1) for r in (0, 1))
lut = gl.Lut(cube_file('LUT_3D_SIZE 2\n' + rows + '\n'))
y = gl.grid(2)
check('an identity LUT through apply_lut is the input', np.allclose(gl.apply_lut(lut, y, 'srgb'), np.clip(y, 0, 1), atol=1e-9))
out, keep = gl.adobe_lut_on_srgb(lut, np.array([[0.5, 0.5, 0.5], [0., 1., 0.]]))
check('greys survive the Adobe detour unchanged', np.allclose(out[0], [0.5, 0.5, 0.5], atol=1e-6), str(out[0]))
check('colours stay in gamut on the way', bool(keep.all()), str(keep))

print('\n' + ('all checks passed' if not failures else f'{len(failures)} check(s) failed'))
sys.exit(1 if failures else 0)
