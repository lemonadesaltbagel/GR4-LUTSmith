"""The firmware container codec, checked without Qt or numpy:  uv run python tests/test_container.py
Builds containers by hand, decodes them back, and walks the rebuild path that a look's
bytes take: patch, re-zero, re-emit the touched frames, verify."""
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import gr4_lutsmith as gl  # noqa: E402

failures = []

HEAD = b'RICOH\0\0\0' + b'\0' * 120          # the container header: the magic and nothing else
PAD = b'z' * 40                                   # every hand-built stream starts with literals (decode refuses files under 160 bytes)

def pad160(b):
    return b + b'\0' * (160 - len(b)) if len(b) < 160 else b


def check(name, ok, detail=''):
    print(('ok    ' if ok else 'FAIL  ') + name + (f' — {detail}' if detail and not ok else ''))
    if not ok:
        failures.append(name)


def raises_refused(fn, label, why=''):
    try:
        fn()
    except gl.Refused as exc:
        check(f'{label}', why in str(exc), f'refused with: {exc}')
    except Exception as exc:
        check(label, False, f'{type(exc).__name__}: {exc} (should be a Refused)')
    else:
        check(label, False, 'accepted')


# ---------------------------------------------------------------- the 32-bit sum (alignment, and 0 means untouched)
check('word sum of aligned zeros is 0', gl.word_sum(b'\0' * 8) == 0)
check('word sum adds little-endian words', gl.word_sum(struct.pack('<III', 1, 2, 0xffffffff)) == (3 + 0xffffffff) % 2**32)
raises_refused(lambda: gl.word_sum(b'\0' * 5), 'word sum refuses a length that is not a multiple of 4', 'aligned')

# ---------------------------------------------------------------- literal frames: decode(encode(data)) == data
def literal_roundtrip(data, label):
    out, frames, used = gl.decode(HEAD + gl.literal_frames(PAD + data) + b'\0\0')
    check(f'{label}: decodes to itself', out == PAD + data)
    whole = len(frames) == 1 and frames[0][3] == len(PAD) + len(data)
    check(f'{label}: frames cover the payload', whole or len(frames) * gl.LITERAL_CHUNK >= len(PAD) + len(data), str(frames))
    return out[len(PAD):], frames, used


raises_refused(lambda: gl.decode(HEAD + b'\0\0'), 'a container that is all header')
literal_roundtrip(b'x' * 32, 'a short payload')
literal_roundtrip(bytes(range(256)) * 40, 'exactly one chunk (0x6000)')
literal_roundtrip(b'A' * (gl.LITERAL_CHUNK + 1), 'a byte past the chunk boundary')
out, frames, used = literal_roundtrip(b'B' * (gl.LITERAL_CHUNK * 2 + 5), 'three chunks')
check('chunks reassemble in order', out == b'B' * (gl.LITERAL_CHUNK * 2 + 5))

# ---------------------------------------------------------------- hand-built LZ77: references and their limits
def lz_stream(*items):
    """Pack literals and ('ref', distance, count) references into flag-word frames."""
    frames, cur_flags, cur_bits, cur_bytes = bytearray(), 0, 0, bytearray()

    def flush():
        nonlocal cur_flags, cur_bits, cur_bytes
        if cur_bits:
            frames.extend(struct.pack('>H', cur_flags))
            frames.extend(cur_bytes)
            cur_flags, cur_bits, cur_bytes = 0, 0, bytearray()

    for item in items:
        if cur_bits == 16:
            flush()
        if isinstance(item, int):
            cur_bytes.append(item)
        else:
            _, distance, count = item
            length = count - 3
            first, ext = min(length, 7), bytearray()
            if first == 7:
                rest = length - first
                while rest >= 255:
                    ext.append(255)
                    rest -= 255
                ext.append(rest)
            a = ((distance >> 8) << 3) | first
            cur_bytes.extend([a, distance & 0xff, *ext])
            cur_flags |= 1 << (15 - cur_bits)
        cur_bits += 1
    flush()
    return bytes(frames)


# 'abc' then copy 3 from distance 3 -> 'abcabc'; and distance 1, count 5 -> 'aaaaa' overlapping
stream = lz_stream(*PAD, *b'abc', ('ref', 3, 3))
data = struct.pack('>H', len(stream)) + stream
out, frames, used = gl.decode(HEAD + data + b'\0\0')
check('a reference repeats three bytes', out == PAD + b'abcabc', repr(out))
stream = lz_stream(*PAD, *b'a', ('ref', 1, 5))
data = struct.pack('>H', len(stream)) + stream
out, _, _ = gl.decode(HEAD + data + b'\0\0')
check('an overlapping reference fills the run', out == PAD + b'aaaaaa', repr(out))
stream = lz_stream(*PAD, *b'ab', ('ref', 0, 3)) + lz_stream(*b'cd')
data = struct.pack('>H', len(stream)) + stream
out, _, _ = gl.decode(HEAD + data + b'\0\0')
check('a zero distance ends the copy and the stream continues', out == PAD + b'abcd', repr(out))
stream = lz_stream(*PAD, *b'ab', ('ref', 2, 300))
data = struct.pack('>H', len(stream)) + stream
out, _, _ = gl.decode(HEAD + data + b'\0\0')
check('a length past 9 walks the extension bytes', out == PAD + b'ab' * 151, f'{len(out)} bytes')

# ---------------------------------------------------------------- damaged containers are refused, with where
raises_refused(lambda: gl.decode(b'short'), 'a file that is too short')
raises_refused(lambda: gl.decode(b'RICOH\0\0\0' + b'\x01' * 120 + b'\x80\x1e' + b'E' * 30), 'no frames and no terminator', 'terminator')
ref3 = lz_stream(*PAD, ('ref', 3, 3))
raises_refused(lambda: gl.decode(pad160(HEAD + struct.pack('>H', 200) + b'ab' + b'\0\0')),
               'a frame that claims more than the file has', 'invalid frame')
raises_refused(lambda: gl.decode(pad160(HEAD + struct.pack('>H', len(ref3) - 1) + ref3[:-1] + b'\0\0')),
               'a reference missing its second byte', 'truncated reference')
ref12 = lz_stream(*PAD, ('ref', 3, 12))
raises_refused(lambda: gl.decode(pad160(HEAD + struct.pack('>H', len(ref12) - 1) + ref12[:-1] + b'\0\0')),
               'a length extension cut off', 'truncated length')
raises_refused(lambda: gl.decode(pad160(HEAD + struct.pack('>H', 6) + lz_stream(('ref', 5, 3)) + b'\0\0')),
               'a reference before the start of the output', 'before start')
refbig = lz_stream(*PAD, ('ref', 1, 40))
raises_refused(lambda: gl.decode(pad160(HEAD + struct.pack('>H', len(refbig)) + refbig + b'\0\0'), limit=16),
               'output past the size limit', 'size limit')

# ---------------------------------------------------------------- sections: what the patcher walks between
def section(tag, body, extra=b'\0' * 4):
    return tag.encode().ljust(8, b'\0')[:8] + extra + struct.pack('<I', len(body)) + body


payload = section('ONE', b'1' * 8) + section('TWO', b'2' * 3)
secs = gl.sections(payload)
check('two sections read back', [s[0] for s in secs] == ['ONE', 'TWO'] and secs[1][2] - secs[1][1] == 3, str(secs))
check('a section ending at the payload end', gl.sections(section('ONE', b'1' * 8))[-1][0] == 'ONE')
check('a lowercase tag stops the walk', gl.sections(b'one\0\0\0\0\0\0\0\0' + b'\0' * 4 + struct.pack('<I', 4) + b'1234') == [])
check('a size past the payload stops the walk', gl.sections(section('ONE', b'1') + b'\0\0')[-1][0] == 'ONE')

# ---------------------------------------------------------------- a whole container, patched and rebuilt
def build_container(payload):
    body = gl.literal_frames(payload) + b'\0\0'      # the terminator counts into the encoded size
    trailer = bytearray(gl.FOOTER_MAGIC + b'\0' * 4 + struct.pack('<II', len(body), len(payload)) + b'\0' * 4)
    data = bytearray(HEAD) + body + trailer
    struct.pack_into('<I', data, len(data) - 4, (-gl.word_sum(data)) & 0xffffffff)
    return bytes(data)


# the payload itself must sum to zero: one word carries the balance
payload = b'\x01\0\0\0' + struct.pack('<I', 0xffffffff)
container = build_container(payload)
check('a hand-built container passes its own checks', gl.check_container(container) == payload)
decoded, frames, used = gl.decode(container)
body = len(container) - gl.HEADER - 2 - 20
check('the decode stops at the terminator', used == gl.HEADER + body + 2, str(used))

# patch one byte, re-zero, rebuild: the result must decode to the patched payload and pass every check
patched = bytearray(payload)
patched[0] = 2
patched[-1] = (patched[-1] - 1) & 0xff                     # keep the word sum at zero
patched = gl.rezero(bytes(patched))
out, reemitted = gl.rebuild(container, frames, used, patched, gl.differences(payload, patched))
again, _, used2 = gl.decode(out)
check('the rebuilt container decodes to the patched payload', again == patched)
check('the rebuilt container passes its own checks', gl.check_container(out, again, used2) == patched)
check('one frame was re-emitted', reemitted == 1, str(reemitted))

bigger = b'C' * 40 + struct.pack('<I', 0)                    # a longer payload so truncating stays above the floor
raises_refused(lambda: gl.check_container(build_container(bigger)[:-1]),
               'a container missing its footer', 'footer')
broken = bytearray(build_container(payload))
broken[-1] ^= 0xff
raises_refused(lambda: gl.check_container(bytes(broken)), 'a wrong trailing checksum', 'checksum')
broken = bytearray(build_container(payload))
broken[-12] ^= 0xff                                        # the encoded size
raises_refused(lambda: gl.check_container(bytes(broken)), 'a size field that does not match', 'size fields')

# ---------------------------------------------------------------- write_atomic: refuse to overwrite, verify what it wrote
with tempfile.TemporaryDirectory() as d:
    out_dir = Path(d) / 'new' / 'dir'
    target = gl.write_atomic(b'\x01\x02', out_dir)
    check('write_atomic creates the folder and the file', target == out_dir / 'fwdc248b.bin' and target.read_bytes() == b'\x01\x02')
    raises_refused(lambda: gl.write_atomic(b'\x03', out_dir), 'write_atomic refuses an existing file', 'already exists')
    check('the refused write left the old bytes', target.read_bytes() == b'\x01\x02')
    check('no temp file left behind', [p.name for p in out_dir.iterdir()] == ['fwdc248b.bin'], str(list(out_dir.iterdir())))

print('\n' + ('all checks passed' if not failures else f'{len(failures)} check(s) failed'))
sys.exit(1 if failures else 0)
