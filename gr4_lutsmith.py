#!/usr/bin/env python3
"""GR4-LUTSmith: put the look of a .cube LUT into your RICOH GR IV (firmware 1.11).

Fits the LUT to the camera's own look processing (a colour matrix, tone
curves, per-hue colour sectors and, with --after-curves, a colour mix after
the curves) and writes a firmware file that carries the result as an extra
Image Control look. With --turns, several fits from different starting points
are run and the good ones kept. Starts from the official RICOH firmware file
you downloaded yourself. A file is written only if every check passes.

Usage
  python3 gr4_lutsmith.py make     LUT.cube OFFICIAL_fwdc248b.bin [--space srgb|adobe] [--slot SLOT] [--photo DNG] [--no-hue] [--after-curves] [--turns N] [--seed S] [-o DIR]
  python3 gr4_lutsmith.py fit      LUT.cube [--space srgb|adobe] [--slot SLOT] [--photo DNG] [--no-hue] [--after-curves] [--turns N] [--seed S] [-o DIR]   (no firmware needed)
  python3 gr4_lutsmith.py build    DIR/look.json [DIR2/look.json ...] OFFICIAL_fwdc248b.bin [-o DIR]
                                   (one look per slot: high-contrast, cinema-yellow, cinema-green)
  python3 gr4_lutsmith.py preview  DIR/look.json PHOTO.DNG [-o sheet.jpg]   (needs rawpy, pillow)
  python3 gr4_lutsmith.py slots                                   (the looks yours can replace)
  python3 gr4_lutsmith.py check    ANY_fwdc248b.bin
  python3 gr4_lutsmith.py selftest OFFICIAL_fwdc248b.bin

Python 3.8 or newer with numpy. --photo and preview also need rawpy and pillow.
"""
import argparse
import base64
import binascii
import hashlib
import json
import math
import struct
import sys
import zlib
from pathlib import Path

VERSION = '2.0.2'

OFFICIAL_SHA256 = 'a2f664dfca034059eb0fd6e18ab08684c326b4a034d85c164dad7e1ec9b5655f'
OFFICIAL_BYTES = 38648776

OTHER_KNOWN = {
    '4c04b48c65897f2edd028df0f5e04666ca241488c81502bae0f34e040ee454eb': 'GR4-MonoUnlock 1.0.0 (monochrome looks unlocked)',
}

# Built-in example: examples/film-1d.look.json must build to exactly this image (selftest).
SELFTEST_LOOK = 'examples/film-1d.look.json'
SELFTEST_SHA256 = 'd201add28bc3ab738bc92fcfdaafa3847bc881bfd7eecb6b7e8f331d1289f052'

HEADER, MAGIC, FOOTER_MAGIC = 128, b'RICOH\0\0\0', b'\xa5\x5a\x5a\xa5'
MAX_DISTANCE = 0x2000
LITERAL_CHUNK = 0x6000


class Refused(Exception):
    pass


class Stopped(Exception):
    """A fit stopped on request (STOP)."""


STOP = None             # a callable that returns True to stop a running fit (used by the app; the CLI leaves it None)


def check_stop():
    if STOP is not None and STOP():
        raise Stopped('stopped')


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def word_sum(data):
    if len(data) % 4:
        raise Refused('data is not 4-byte aligned')
    return sum(w for (w,) in struct.iter_unpack('<I', data)) & 0xffffffff


def decode(data, limit=256 * 1024 * 1024):
    if len(data) < 160 or data[:8] != MAGIC:
        raise Refused('not a RICOH firmware container')
    out = bytearray()
    frames = []
    cursor = HEADER
    while True:
        start = cursor
        if cursor + 2 > len(data):
            raise Refused('container: missing frame terminator')
        descriptor = int.from_bytes(data[cursor:cursor + 2], 'big')
        cursor += 2
        if descriptor == 0:
            return bytes(out), frames, cursor
        size = descriptor & 0x7fff
        end = cursor + size
        if not size or end > len(data):
            raise Refused(f'container: invalid frame at {start:#x}')
        output_start = len(out)
        if descriptor & 0x8000:
            out += data[cursor:end]
            cursor = end
        else:
            while cursor < end:
                if cursor + 2 > end:
                    raise Refused(f'container: truncated flags at {cursor:#x}')
                flags = int.from_bytes(data[cursor:cursor + 2], 'big')
                cursor += 2
                for bit in range(15, -1, -1):
                    if cursor == end:
                        break
                    if not flags & (1 << bit):
                        out.append(data[cursor])
                        cursor += 1
                        continue
                    if cursor + 2 > end:
                        raise Refused('container: truncated reference')
                    a, b = data[cursor], data[cursor + 1]
                    cursor += 2
                    distance = ((a >> 3) << 8) | b
                    length = a & 7
                    if length == 7:
                        while True:
                            if cursor >= end:
                                raise Refused('container: truncated length')
                            extension = data[cursor]
                            cursor += 1
                            length += extension
                            if extension != 255:
                                break
                    if distance == 0:
                        break
                    if distance > len(out):
                        raise Refused('container: reference before start of output')
                    count = length + 3
                    if distance >= count:
                        out += out[-distance:len(out) - distance + count]
                    else:
                        pattern = bytes(out[-distance:])
                        out += (pattern * ((count + distance - 1) // distance))[:count]
                if len(out) > limit:
                    raise Refused('container: decoded size limit exceeded')
        frames.append((start, end, output_start, len(out)))


def check_container(data, payload=None, consumed=None):
    if payload is None:
        payload, _, consumed = decode(data)
    if data[-20:-16] != FOOTER_MAGIC:
        raise Refused('container: footer magic missing')
    enc, dec = struct.unpack_from('<II', data, len(data) - 12)
    if enc != consumed - HEADER or dec != len(payload):
        raise Refused('container: size fields do not match the content')
    if word_sum(data) or word_sum(payload):
        raise Refused('container: checksum mismatch')
    return payload


def sections(payload):
    out, pos = [], 0
    while pos + 16 <= len(payload):
        tag = payload[pos:pos + 8].rstrip(b'\0')
        if not tag or not tag.isalnum() or not tag.isupper():
            break
        size = struct.unpack_from('<I', payload, pos + 12)[0]
        if pos + 16 + size > len(payload):
            break
        out.append((tag.decode(), pos + 16, pos + 16 + size))
        pos += 16 + size
    return out


def differences(a, b, block=1 << 16):
    if len(a) != len(b):
        raise Refused('length mismatch')
    out = []
    for pos in range(0, len(a), block):
        x, y = a[pos:pos + block], b[pos:pos + block]
        if x != y:
            out += [pos + i for i in range(len(x)) if x[i] != y[i]]
    return out


def rezero(payload):
    buf = bytearray(payload)
    last = struct.unpack_from('<I', buf, len(buf) - 4)[0]
    struct.pack_into('<I', buf, len(buf) - 4, (last - word_sum(buf)) & 0xffffffff)
    return bytes(buf)


def literal_frames(data):
    out = bytearray()
    for pos in range(0, len(data), LITERAL_CHUNK):
        chunk = data[pos:pos + LITERAL_CHUNK]
        out += struct.pack('>H', 0x8000 | len(chunk)) + chunk
    return bytes(out)


def rebuild(source, frames, consumed, patched, changed):
    body = bytearray()
    reemitted = 0
    for (c0, c1, d0, d1) in frames:
        if any(d0 <= c < d1 or c < d0 < c + MAX_DISTANCE for c in changed):
            body += literal_frames(patched[d0:d1])
            reemitted += 1
        else:
            body += source[c0:c1]
    body += b'\0\0'
    trailer = bytearray(source[consumed:])
    struct.pack_into('<II', trailer, len(trailer) - 12, len(body), len(patched))
    struct.pack_into('<I', trailer, len(trailer) - 4, 0)
    padding = bytes((-HEADER - len(body) - len(trailer)) % 4)
    out = bytearray(source[:HEADER] + body + padding + trailer)
    struct.pack_into('<I', out, len(out) - 4, (-word_sum(out)) & 0xffffffff)
    return bytes(out), reemitted


# ------------------------------------------------------------------ firmware data
# Fixed changes (make the extra look slot available, colour processing and default
# slider settings for it) and where the look itself goes. Exported from the
# builds that were tested on a GR IV 1.11.
_DATA = '''
eNrtmduOHDcOht+lr9uAJOqYVzGMQMeNsY4dOM4iQOB334+a6eqpHiNXuVhgZwrF0YgUSUnkL6rmr8v6+Occl5/ev/cmm+j89WL4
CW7K5Xqxmaal+eH6zM90FpWw9XJr5qr8EKxJEf6UXnqc4aLN8tS88VO8XnrJN34v6cTPyTHIlmO8rSd+KYw3NtdnvrGpvuBbI/Bt
6ze+beXEt8ar0+Xgl3riO2euFymHfiln/WKYX5BDf5CzftH1CfbQH+xZv/fYz3f/8oN/3jM+3eeXHuYX9vi7//nB/6jzr+YYX815
fNL5tXDwWzjzs65Prwe/1wd+xr+xDv5YL/nOWI2fvrrv4Wn8Kk/NG1/ctn/jY//Ed7p+fZRj/KgnvgSNj3GMn+M8Xgp8k3p45ptU
wku+j8zfhHLwQz3xg8anC/nGdyGd+DEyP1mHflln/TErfxz6ZZz1k05s6t2/9ODfXv8wDvthnO2XPf7uf3rwv+j+5H6Mz/00ngRk
fao9+NWe+TYw/xYPfotnvvPY7+3g93bilx1/rREBz+NbWSe+xjf2193+iZ80vlspx/hiT/ys+DLKMX6U8/himd+Sw/6Ss/2i8bHs
oX/Zl/rFGMUXTYPun/DBpqfmM9/q/rrWb3zXyonvdP8wc/CLPfFF8yPKMT7Kebxofvhy2PflbN9rfkR76I/2rD9ofqd8jE/5PD6q
f3kc/Dwe+Bo/1R386s78pPHT0sFv6cQvVte/JvDteXwq9cRX/7Bf7/ZPfGcVv0I5xgOgL/my4y8c43s4j5dsFJQO+2Od7W98HePQ
P8ZZf9D8Xnf/1oN/UfHHpt5u8ZFKO/F1fWwoBz/YEz/p+vp1jPfrPD5pfErIN76EdOJv/PXj0O/HWX/R+ZNkN370L8d7Y3R+qRz8
VM58q+uT58HP88x3Gp9VDn6VO5/c9dv+8rYnE7R+yMaO3YRvfeB8TaIFxj6675066nVn+kFn8a87ScsfdeZTJ9gsacO38c0aG9R9
Dvqxm4eA5nelKjE+eI2PXNpu3gVQ28DZZwGbn2VvAkkROvsSngXMrXkXUIijAvqhQI6KsmycicOELSD5uXkTEN2kvxPwOosfCny4
Xj59+fLvy09/XX6t375+/JPCz1JkPG2cAb/OP6//xsjl94+f//VpqpL+x9f/0LBeVwT41NNfu37+shbdiYzfJeNz5ycKzV1eIgoW
AbfWmM67eJ3uCiVC5m3s6eRFsfPBuBSNq8k4kEsPQZFGpdc57qeRoeoAXmEZI6tbqvF9GK9VLHgRYubA7yYgF3iiVI7xZWKlPZpR
CE0+AW3TpBZNmoNTMpgcptHNzYzLphqww3DamNJor2CKWFPjNLU2U8cy1Q7TMNIKL3835tOjN72C1nOaji8jNTO6MwN9EzidyM6Z
zETXSt2snlkK/ogG5SzMYoE8HmLMTpbDTetSs66DpDZaid5Ko+xc0wphzTZaP731LF1I3YaeQCAkQ7exRsu0KRGbTTnaROQn222O
1Js4m9FB3FIcU79OTDqwMVXwMdqKjhaWbbXZtrJtZFgHrPtYtrtOjhc7kBv4PvF91mHnqnb6ZKkALartctOxwM503WCiPDpn63J2
YdxnUjs4nR7ESapOenTCnz5M52t1fnE58ZbirDsqJBfAohiniy27yMGa/HDAmUu6RK4BHxyWbTi9tBS/OJSTK4PfcGtcrrbo6hqu
eu9arq4N4xpOUZu5zoT7pO2qG9gYzbmxghuS3cS3OQquVrdCc6t0t8Zwyw7OX97ShU0WDWJOArEliR2BDeRwDziah2BAWAwRLyJp
Ec5VZEX+YBsjkVKjeN1SdARwPeQloWcinC2XKpGhsXaJ00u0XVLwkjK/u6prksSR85yG1QhHLKfi0LNPbzMCoJE2FB6MqyxizUlq
G8JWCMe9tNCFsktaJ1LQRSqyFdx5SpHeudtxCAy8GLFR6C4ZQ0jBJBPJGZdMpkJZLtM0Wfrg66pJ1uBvEEIh+ff57QYaoWT//Xr5
rX78+gpQSPj8ClDCPoNfAkpmVU8vs6ZQRtTs2VauhZUQqQAG8ScNqGoAR4ff4Q/6BqAyGUMyylyGzeBFBh+8WcbrzYiEJlSegIap
eA/QBAUdZgUY+VSMz1RYhToNm74ig50NRoMXEPBLden1mrsGeoIvADPgzMUsUKsFACE0QKsDWJPf4EC0gBTgET2gFQEv5hgrZ0nj
7zFNXAMZwMvr/TuYxPw1zxLgBqwZDRsOhScwwxfWHPACyECAgn8F8KzMpbJeFZuEl2m5m4Zsc4CXlpOzap1nBr4NgG7SNwHBCcCt
wUuiM4CF5g5RK0BF6oIiZLilagEWEuDEazM5UQGlASBxX3VRb7jsKwDUss1CcQ5okbC2jmSrgs5MtiVjO8DTtaAw2c4CyCTARY8k
0Itkw9QASMAPktNpBpgFcHRAo7kw2waOCLgwbZfg44krDVAIBSCornI5aCR4H951AAI7bhZgi7zlRkuyu6fEJk9BOQFVxREiLtDR
iXJ+fFjiuxfvNGmdBORJfYmZhB2OZB3UEAF8o3KatF0nUTMhS8IOLV8nicqSJV4N4965wWiyLqme1AccOE9IWJEKqFR4VSEdKy2A
I4xrhVRsJO7QBOZvEpFVkE7adsCkowMseUroSkIDGp2E7RO5hYzVlLBIk9gAFGZIdt5E4rOVAzAaZXC1rTJaEqbIq0AAILAunAm8
tInwaQAETq/pHC8AgU4sygSMpuc3wDWDAgd8AGunH5sz05SZ4WV4gNEERCfzmpU+7E7SdrbIy5gzoNiy8eGX+vmOGeSWv/VtyGgc
CAbUfPx9+a66Ps3+Tb+IaTEVRYua1UmMdC+xoteLyKve/fng3vvhpuznz18+H6hmdhH/7ZePX8cD4hXrd4V6Qrx9ZDyWUGk/8XjC
w+MfHnl43MNjH563n7//yftJxxMfnn96P96e81P2k48nPTz/9H78jz2X79+fAeF3hZDP849vX+snIMtc9bi9Oo7lK1VhuHr94h5A
1mv0IV05pSHckq7ZxnilWglX/YR/LRLKtUTu3EW/IVrjotLglHJnveq393LlYLcC9TZCI3dsa6n9oSWGqzJpc3VH0ilEarmerlqQ
I++4dUBLUS+t6tFDGqpfdSh2PRpEv3DrvUZ70uZmtSVZrXMuoo2yyEH1WzO0KHWq2cvu4UC96od2bQemBmUhgFb11seglBhRGlQm
b5m85fMeyxVBaYSiICj1TmlibNieBzQp5dqsdxmrdMswVSgRpNQVpUH1CCeQDYC8UlGdPuz27g/qj36gVKqzCyGplaCzC9GqPDWg
0qD9cVuJuvIhbZ17hYN+P4VG9Up3HlpUMuvctSBRKru9rXBmKc2qJ29bZXtSnGooovJlz7TouoUS1VbZ8vot1Wrtq9Ru6oJS2dRv
GjZVK9ysdztvSbUVra4St1urVC1Ci1JJSrcGq/Pl5rplNCYpGFUbpaLSsvuL9jvdqahfT5Tunu2P05iE7h5dn8g1bFO1Qkm46eam
zU3qg8t7bNmSRduicQLdbbvbdred3XS3ZffrTkFVD1e8TdWW/vdCqfovcUvGLbmtS9p69irJ9oFidFP/Rt/oa6rl5acv395g/w32
32D/Dfbf6P8J7GvtX3+dX+vPx/9w3oOjRPe7GDSCCM0P1/fv7IZxB1xd3zkv2qf/H37nnQawzTl9+PD9v05jG/U=
'''
DATA = json.loads(zlib.decompress(base64.b64decode(''.join(_DATA.split()))))
LOOK = DATA['look']

# Hue sectors: the changes that turn them on and the place that takes the look's hue-sector matrices (both
# with their original bytes, which are checked).
_HUE = '''
eNrt0TtOxDAQgOG7uE4xfttcZZUicRJpCxqgQELcHe8kGGYEKy14u/FXOIks28r/prbz67qoh9PJQArRxDCoDFMA550alEk5Q66P
47AvcNpcX+Bd+nWBjh5sAhgUmNvUjeKxZy5zuszu2Di7+q6912n/7kvZ8Ox2oDW3H/jDBcJx4F9nNY6Depxens5lfa5/fL9ccPVy
E8pNYiITGM84xoqrZjQ1mendQ1AFzc3E9O4hqAWVZmZ69xDUipamML17CGpDa7MwvXsIQsPFUYGWuE8PQeh9wKdvJe7TQxDaoK8B
VPcegtAWmYaP3j0EoR2yjWF69xCE9sg1lvlnDzW+fwAfTUfe
'''
HUE = json.loads(zlib.decompress(base64.b64decode(''.join(_HUE.split()))))

# Colour after the curves: the changes that turn it on, the place that takes the look's matrices (both with
# their original bytes, which are checked), and the camera's own values for each Hue step.
_POST = '''
eNrtk7tuwzAMRf9FMw1c0RRN91cCD64faIYufQAFivx7JctJIAHJlKJLfYZLmhR4IZvfbj1+LbN7OhwYpoE9k1PojBDEkZtt9OhjOFBu
aMXuN4jJzQbfBSg4NmB5LGnQlAcBIydlySqWVaes/V5/3uvzXl/3OmNss+GB3Ov48Xaclvd4Qdm8/oL5v8brRrggFW0FV9QPSrD+cw/f
beiFUHHje8SVci+fS1peSz+mFxiZZ4urloQ8gl5fKKiDUAq2vGdqA1MKzjmKrCnK8XhTnI8DmmJC8tBcTQynH7SJIAU=
'''
POST = json.loads(zlib.decompress(base64.b64decode(''.join(_POST.split()))))

# Other slots for a personal look: the changes for each slot, the room for the looks' data, each slot's name in
# every language and its menu icon, all checked against the original (icons by a fingerprint of their pixels).
_SLOTS = '''
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
z2HWC4+fNtuT2+92Zz5qjguYOameEvfndJanlvO9uP7VHn9ujndu/zm+Zz6zQHHWH49jrn/O5+J6zqJ+7vO93OyHPI65/jmf7Fa/OOtn
jsfVvrO/unTaQc7Hz+eY+TPMcRcD28tZz9POsR3gcq76mf0UERe1wyxvm+/fE7/g+J4uZz3rOS7gKeP++V4rn37258r5pLOe9az3NscD
22nO5+L+XM/20vGsZz3fM5lVvyMftlPu7D+W8/FnPWu97Oeo5ybyiWf/gfqhJ/N78J/1qmiel2Y/nP0+ifa4ePzUc14L87kuj3oubM/m
OMxB9LO0xuExr3H/UNN+hfm9yPNnF/1sji8Tznk78jie9sDM78VZ/8WJfnat/jrsJb//7EexnvMyz1+lnvn0ab9509TO9s+zv9tZL1yu
Musxz4HK9VHN+V5X5vEyp9M67c4cr2y280zn+2I834vn1cbtP+28m/Wc531llvehPkY+s355Pr0s5zfSVTvni5JPvMT9TLP9mfPONds9
zXa6ZhWyPazze3xfm/Ognu3E8ynXk5v1oifU4f4chF1t01662Z6W+8cc97jao+fdZ38O/bSrjDe4H5vZIwoPP55H+9mf4xpgDJNmPmy2
Zrnr7B95lsNdZ39O+rTPjDe4H5v5fnUWyF9ne3F/TmznuT4nfmQ8Y+b7NbXwIs9vB3628/3NddoNrrBWGe+Nf4dLzBf6HIemnHaD86nc
HxhHTPveu8DPcxyaeNqN+bZ3nfXI4wIahP5Sk8DP7qwHthtcnjL/vsbFvB/6zYGfuX8Yc9oNzicnntfnv80sRxLrFLYza76a44fzsfy+
q1+4t+qZ1welnrg3lXP8XLM/90vUczpxK7drn/aE8R/DgDz7+6v13NdP+4h7I+OJfI6bZMQ6RZ3955713GcBwvxe4vmAcdbsr9Gd/YfH
N+MEnm/5/fWaN2Z/5nXKHAfWnvMU44CVD/ervQ451inBi/VXW+uDox8y7OF1GK/z1jqln+OT202VMx+e3xnHXPeyy+ZxvcX2hdutze/x
e7n5d8YxZdqVMN/P32d/5vx6P9enLfPyZNknxiMHzl52g+czXsdN+1Z5Ppjjgtdxa51qz3oxs2BRMQ6Y+czyxtl//LSndb6/u0R/1qv9
7OO6v8znxb72BezsRyOfePZn3je4p13hfDKvf3kdMtfteL766b7xuNSoFCKdndOwc97h2bAqgX799TfjCxZPa9/6gsfT5xe/8Ov/9M2H
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
JK4oriAuLy739XrzanTVdRVx/dLt8fU6r4uutq4qrl+6Pb5e59XputbVxPVLt8fX67xuuvq6LnH90u3x9TouDKpl1GyFsyX+87TH1+u4
jKZL8fXQEv952uPrdVyz/velzusXb4+v13E91f9/7vb4eh3Xu/X/S7fH1+u4fnb9/7nt8fU6rj+7/n9ue3y9jusXr/932uPrJS799fov
eX34RjuVYZnqssGN5T98/Pztp58+/+rbj7/5dm3E8laqHR7oIl1hXV5cTlxWXE82Slxf/3v7v0RXXFcQ1y/dHl+v88p0pXVFcf3S7fH/
s4tNhgvB/fqbYRiIdvxfvGiFrryuJK5fuqG+XudV6SrryuL6pdvj63Veja66riKuX7o9vl7nddHV1lXF9Uu3x9frvDpd17qauH7p9vh6
nddNV1/XJa5fuj2+Xsc1V0/3urq4fun2+Hodlx7/7XXsLa5fuj2+Xsc1tzT2f3Jf4Zduj6/XcU1G3d5ckv/90u3x9Tquyajb3Ea52fdL
t8fX67ieGJ+Sa/pntgdvvKCD97nxMpy3PDXU1+u4nqigkoT6CzfU1+u8nqig75GkZztwh49OqV//+j998+Fz/+3vviufO5H7Bwf+b77t
5frwFx9udxF3nc8PkEr+U+fUOKn6uVWWHGDq5/LxO0jNDv6q0T3ESPWt3fNb5f7cf/ib7z5+/7dIKHffOPVNDN9gJCL01IHeSpDpjxFh
UZ5jTfo13zOejDKGHz/+h4cSf/gL8jFxlB7FAqRVMBQjEYs2H4l6j/0eJKvA3CEhOOTlf19+O/Iu33336Q+o4fjw3/zbD//t3/vv/vv/
4Ztf/Y//k9IG/QfHlP/if/5f/te//w/+4T/6x//bP/mn/+x//z/+z//rn/+Lf/mXf/V//6v/5//91/+m1Hb1+zfffvx3f/vdb7//9Lt/
/8OPn3/6/R/+7o//AWrgu/79bz5/+ze1/Ijl8/GbDz99//v+w8f7YydZyr/9gLKAv/8fevsWvv2//fT9b8oPH8v38Ps/Lb8r3/cfkdX/
zz790Olvf//bj/inX/3lw+9/heT9v/eYyz8o33/8EX/5Rx+/n789Zrxy+wc/fabv//NP341v/cuffvxxfOcvoTLG3/7q2/IRn/ANPuGh
TI9Foe+IEv0TKtFr5f0X75T3H/cffksF+cc/9P63X1Azb7zLYyH+8nf8LPGGj+WDP/30w99iIpT0X4n3Xk98+QEvVcW/fvllf+jftxff
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
'''
SLOT_DATA = json.loads(zlib.decompress(base64.b64decode(''.join(_SLOTS.split()))))
SLOT_NAMES = {'high-contrast': 'High Contrast', 'cinema-yellow': 'Cinema (Yellow)', 'cinema-green': 'Cinema (Green)',
              'vivid': 'Vivid', 'positive-film': 'Positive Film', 'negative-film': 'Negative Film',
              'bleach-bypass': 'Bleach Bypass', 'retro': 'Retro', 'soft-monotone': 'Soft Monotone',
              'hard-monotone': 'Hard Monotone', 'hi-contrast-bw': 'Hi-Contrast B&W',
              'mono-standard': 'Standard (monochrome)', 'mono-soft': 'Soft (monochrome)',
              'mono-solid': 'Solid (monochrome)'}
SLOT_ORDER = list(SLOT_NAMES)        # looks are placed in this order, so a set of looks always builds the same file
# what each slot replaces, whether it works in movie mode, and the extra sliders it offers
SLOT_INFO = {
    'high-contrast': ('the hidden monochrome High Contrast look', True, '-'),
    'cinema-yellow': ("RICOH's Cinema (Yellow) look", True, 'Saturation, Hue'),
    'cinema-green': ("RICOH's Cinema (Green) look", True, 'Saturation, Hue'),
    'vivid': ("RICOH's Vivid look", True, 'Saturation, Hue'),
    'positive-film': ("RICOH's Positive Film look", True, 'Saturation, Hue'),
    'negative-film': ("RICOH's Negative Film look", True, 'Saturation, Hue'),
    'bleach-bypass': ("RICOH's Bleach Bypass look", True, 'Saturation, Hue'),
    'retro': ("RICOH's Retro look", True, 'Saturation, Hue'),
    'soft-monotone': ("RICOH's Soft Monotone look", False, '-'),
    'hard-monotone': ("RICOH's Hard Monotone look", True, '-'),
    'hi-contrast-bw': ("RICOH's Hi-Contrast B&W look", True, '-'),
    'mono-standard': ('the hidden monochrome Standard look', True, '-'),
    'mono-soft': ('the hidden monochrome Soft look', False, '-'),
    'mono-solid': ('the hidden monochrome Solid look', True, '-'),
}


def slot_line(name):
    return f'{SLOT_NAMES[name]} (replaces {SLOT_INFO[name][0]})'


def list_slots(log=print):
    log(f'{"--slot":16s} {"shown as":24s} {"replaces":42s} {"movie":5s}  {"--name up to":13s} {"--icon":7s} extra sliders')
    for name in SLOT_ORDER:
        what, movie, extra = SLOT_INFO[name]
        shown = SLOT_NAMES[name] + (' (default)' if name == DEFAULT_SLOT else '')
        ns = SLOT_DATA['names']['slots'].get(name)
        most = f'{max(r[5] for r in ns["languages"])} characters' if ns else '- (fixed)'
        own_icon = 'yes' if name in SLOT_DATA['icons']['slots'] else 'no'
        log(f'{name:16s} {shown:24s} {what:42s} {"yes" if movie else "no":5s}  {most:13s} {own_icon:7s} {extra}')
    log('Your look opens with every slider at 0 in every slot; a slot you do not use keeps its own look.')
DEFAULT_SLOT = 'high-contrast'


def matrix_hex(look):
    mat = look['matrix_q13']
    if any(abs(sum(r) - 8192) > 2 for r in mat) or max(abs(x) for r in mat for x in r) > 0x7fff:
        raise Refused('look matrix must be 3x3, Q13, rows summing to 8192')
    return struct.pack('<9h', *[x for r in mat for x in r]).hex()


def distinct_curves(curves):
    if len(curves) != 3 or any(len(c) != 256 or min(c) < 0 or max(c) > 16383 for c in curves):
        raise Refused('curves must be three lists of 256 values in 0..16383')
    distinct = []
    for c in curves:
        if list(c) not in distinct:
            distinct.append(list(c))
    if len(distinct) > 3:
        raise Refused('at most three different channel curves are supported')
    return distinct


def look_changes(look):
    """(offset, old, new, what) for one look: matrix, curve table selection, curve(s)."""
    out = [(LOOK['matrix'][0], LOOK['matrix'][1], matrix_hex(look), 'look colour matrix')]
    curves = look.get('curves')
    if not curves:                                   # no look curve: the colour look's plain tone curve
        for off, old in LOOK['select']:
            out.append((off, old, struct.pack('<I', LOOK['select_none']).hex(), 'use the plain tone curve'))
        return out
    distinct = distinct_curves(curves)
    if len(distinct) == 1:
        buf, bufs = LOOK['single'], [LOOK['single']]
    elif len(distinct) == 2:
        buf, bufs = LOOK['pair'], [LOOK['pair'], LOOK['single']]
    else:
        buf, bufs = LOOK['pair'], [LOOK['pair'], LOOK['single'], LOOK['third']]
    for off, old in LOOK['select']:
        out.append((off, old, struct.pack('<I', buf['set']).hex(), 'use the look\'s tone curves'))
    for k, (b, c) in enumerate(zip(bufs, distinct)):
        out.append((b['curve_off'], b['curve_old'], struct.pack('<256H', *c).hex(), f'tone curve {k + 1}'))
    ptrs = [bufs[distinct.index(list(c))]['curve'] for c in curves]
    if len(distinct) >= 2:
        out.append((LOOK['pair']['chan_off'], LOOK['pair']['chan_old'], struct.pack('<III', *ptrs).hex(),
                    'tone curve per colour channel'))
    return out


def hue_table_hex(q, old):
    """The look's hue-sector matrices in the size of the place that takes them."""
    import numpy as np
    q = np.array(q, int)
    if q.shape != (12, 3, 3) or np.abs(q).max() > 2047 or (q.sum(2) != 1024).any():
        raise Refused('hue sectors must be 12 matrices of 3x3 (Q10, |value| <= 2047, rows summing to 1024)')
    copies = len(old) // 2 // (12 * 9 * 4)
    table = np.repeat(q[:, None], copies, axis=1).astype('<i4').tobytes()
    if len(table) * 2 != len(old):
        raise Refused('internal: hue-sector data size')
    return table.hex()


def hue_changes(look):
    """(offset, old, new, what) for the look's 12 hue-sector matrices."""
    q = (look.get('hue_sectors') or {}).get('matrices_q10')
    if not q:
        return []
    off, old = HUE['matrices']
    return [(o, a, b, 'hue sectors') for o, a, b in HUE['fixed']] + [(off, old, hue_table_hex(q, old), 'hue sectors')]


def post_matrix_rows(target):
    """The look's values for a target matrix, exact after the camera's own rounding."""
    import itertools
    import numpy as np
    Y = np.array(HUE_YCC, np.int64)
    Yi = np.linalg.inv(Y.astype(float))
    rows = []
    for t in target:
        r0 = np.rint(8192 * np.asarray(t, float) @ Yi).astype(np.int64)
        for d in itertools.product(range(-2, 3), repeat=3):
            r = r0 + np.array(d)
            if (((r @ Y + 4096) >> 13) == t).all():
                rows.append(r)
                break
        else:
            raise Refused('internal: colour matrix after the curves cannot be represented')
    return np.array(rows)


def post_check(p):
    import numpy as np
    p = np.array(p, int)
    if p.shape != (3, 3) or (p.sum(1) != 512).any():
        raise Refused('colour matrix after the curves must be 3x3 with rows summing to 512')
    t = post_targets(p)
    if t.min() < -256 or t.max() > 255:
        raise Refused('colour matrix after the curves is out of the camera\'s range')
    return p


def post_data(p):
    """The look's nine camera matrices for colour after the curves (one per Hue step)."""
    return b''.join(struct.pack('<9i', *post_matrix_rows(t).ravel().tolist()) for t in post_targets(p))


def post_changes(look, data=None):
    """(offset, old, new, what) for the look's colour matrix after the curves (data: the slot's places)."""
    data = data or POST
    p = look.get('post_matrix_q9')
    if not p:
        return []
    p = post_check(p)
    places, data = data, post_data(p)
    off, old = places['matrices']
    if len(data) * 2 != len(old):
        raise Refused('internal: colour matrix data size')
    return [(o, a, b, 'colour matrix after the curves') for o, a, b in places['fixed']] + \
        [(off, old, data.hex(), 'colour matrix after the curves')]


DATA_WHAT = 'make the look slot available with its colour processing and defaults'


def slot_changes(look, name):
    """(offset, old, new, what) for a look on Cinema (Yellow) or Cinema (Green): the look, neutral slider defaults
    and its colour after the curves."""
    s, label = SLOT_DATA['slots'][name], SLOT_NAMES[name]
    out = [(o, a, b, f'{label}: neutral slider defaults') for o, a, b in s['defaults']]
    out.append((s['matrix'][0], s['matrix'][1], matrix_hex(look), f'{label}: look colour matrix'))
    so, sold = s['curve_switch']
    curves = look.get('curves')
    if not curves:
        out.append((so, sold, '0000', f'{label}: use the plain tone curve'))
    else:
        distinct = distinct_curves(curves)
        out.append((so, sold, '0100', f'{label}: use the look\'s tone curves (Color Tone has no effect)'))
        for k, c in enumerate(distinct):
            out.append((s['curves'][k][0], s['curves'][k][1], struct.pack('<256H', *c).hex(), f'{label}: tone curve {k + 1}'))
        po, pold = s['curve_pointers']
        ptrs = [s['curve_va'][distinct.index(list(c))] for c in curves]
        out.append((po, pold, struct.pack('<III', *ptrs).hex(), f'{label}: tone curve per colour channel'))
    q = (look.get('hue_sectors') or {}).get('matrices_q10')
    if q:
        off, old = s['hue']['table']
        out.append((off, old, hue_table_hex(q, old), f'{label}: hue sectors'))
    else:
        out += [(o, a, b, f'{label}: no hue sectors') for o, a, b in s['hue']['off']]
    return out + post_changes(look, s['post'])


class Space:
    """Room for the looks' data, filled in order; changes() gives the edits."""

    def __init__(self, segments):
        self.segs = [dict(off=o, old=bytes.fromhex(h), at=va, data=bytearray.fromhex(h), used=0)
                     for o, h, va in segments]

    def free(self):
        return sum(len(g['old']) - g['used'] for g in self.segs)

    def put(self, data, what='data'):
        n = (len(data) + 3) // 4 * 4
        for g in self.segs:
            if g['used'] + n <= len(g['old']):
                at = g['at'] + g['used']
                g['data'][g['used']:g['used'] + len(data)] = data
                g['used'] += n
                return at
        raise Refused(f'not enough room for these looks together ({what}: {len(data)} bytes, '
                      f'{self.free()} left); use fewer slots, or looks without hue sectors or colour after the curves')

    def put_at(self, at, data):
        g = next(g for g in self.segs if g['at'] <= at < g['at'] + g['used'])
        g['data'][at - g['at']:at - g['at'] + len(data)] = data

    def changes(self):
        return [(g['off'], g['old'][:g['used']].hex(), bytes(g['data'][:g['used']]).hex(), 'data for the looks')
                for g in self.segs if g['used']]


def extra_space(look, name):
    """Bytes of room a look in this slot needs."""
    z = SLOT_DATA['templates']['sizes']
    n = 0
    if look.get('curves'):
        n += z['curve'] * len(distinct_curves(look['curves'])) + z['curves_head']
    if (look.get('hue_sectors') or {}).get('matrices_q10') and 'hue_table' not in SLOT_DATA['extra'][name]:
        n += z['hue'] + z['hue_links'] + z['hue_head']
    if look.get('post_matrix_q9'):
        n += z['after']
    return n


def extra_changes(look, name, space):
    """(offset, old, new, what) for a look on one of the further slots."""
    s, label, tpl = SLOT_DATA['extra'][name], SLOT_NAMES[name], SLOT_DATA['templates']
    out = [(o, a, b, f'{label}: neutral slider defaults') for o, a, b in s['defaults']]
    out += [(o, a, b, f'{label}: a colour look with your colours only') for o, a, b in s['fixed']]
    out.append((s['matrix'][0], s['matrix'][1], matrix_hex(look), f'{label}: look colour matrix'))

    def rows(t, at, what):
        for o, a, base in s['rows'][t]:
            new = base if at is None else struct.pack('<I', at).hex()
            if new != a:
                out.append((o, a, new, f'{label}: {what}'))
    curves = look.get('curves')
    if curves:
        distinct = distinct_curves(curves)
        at = [space.put(struct.pack('<256H', *c), 'tone curve') for c in distinct]
        ptrs = [at[distinct.index(list(c))] for c in curves]
        rows('curves', space.put(bytes.fromhex(tpl['curves_head']) + struct.pack('<III', *ptrs), 'tone curves'),
             'use the look\'s tone curves')
    else:
        rows('curves', None, 'use the plain tone curve')
    q = (look.get('hue_sectors') or {}).get('matrices_q10')
    if q and 'hue_table' in s:                       # this slot has its own room for hue sectors
        off, old = s['hue_table']
        out.append((off, old, hue_table_hex(q, old), f'{label}: hue sectors'))
    elif q:
        z = tpl['sizes']
        table = space.put(bytes.fromhex(hue_table_hex(q, '00' * z['hue'])), 'hue sectors')
        links = space.put(struct.pack(f'<{z["hue_links"] // 4}I', *[table] * (z['hue_links'] // 4)), 'hue sectors')
        rows('hue', space.put(bytes.fromhex(tpl['hue_head']) + struct.pack('<I', links)
                              + bytes.fromhex(tpl['hue_tail']), 'hue sectors'), 'hue sectors')
    else:
        rows('hue', None, 'no hue sectors')
    p = look.get('post_matrix_q9')
    if p:
        data = post_data(post_check(p))
        at = space.put(bytes(tpl['after_head'] + len(data)), 'colour after the curves')
        links = tpl['after_links']
        space.put_at(at, struct.pack(f'<{len(links)}I', *[at + d for d in links]) + data)
        rows('colour', at, 'colour after the curves')
    else:
        rows('colour', None, 'colour setting')
    return out


# The camera's menu languages and the characters each one shows. ASCII shows in all of them; other characters show
# when the camera is set to a language that uses them.
CAMERA_LANGUAGES = ('Czech', 'Danish', 'Dutch', 'English', 'Finnish', 'French', 'German', 'Greek', 'Hungarian',
                    'Italian', 'Japanese', 'Korean', 'Polish', 'Portuguese', 'Russian', 'Chinese-S', 'Chinese-T',
                    'Spanish', 'Swedish', 'Thai', 'Turkish')
LATIN_LANGUAGES = ('Czech', 'Danish', 'Dutch', 'English', 'Finnish', 'French', 'German', 'Hungarian', 'Italian',
                   'Polish', 'Portuguese', 'Spanish', 'Swedish', 'Turkish')
CJK = ('Japanese', 'Chinese-S', 'Chinese-T')
SCRIPTS = (((0x00a0, 0x024f), LATIN_LANGUAGES, 'accented Latin'), ((0x0370, 0x03ff), ('Greek',), 'Greek'),
           ((0x0400, 0x04ff), ('Russian',), 'Cyrillic'), ((0x0e00, 0x0e7f), ('Thai',), 'Thai'),
           ((0x3040, 0x30ff), ('Japanese',), 'Japanese kana'), ((0x31f0, 0x31ff), ('Japanese',), 'Japanese kana'),
           ((0xff66, 0xff9f), ('Japanese',), 'Japanese kana'), ((0x3400, 0x4dbf), CJK, 'Chinese characters'),
           ((0x4e00, 0x9fff), CJK, 'Chinese characters'), ((0x1100, 0x11ff), ('Korean',), 'Korean'),
           ((0x3130, 0x318f), ('Korean',), 'Korean'), ((0xac00, 0xd7a3), ('Korean',), 'Korean'),
           ((0x3000, 0x303f), CJK + ('Korean',), 'Asian punctuation'),
           ((0xff00, 0xff65), CJK + ('Korean',), 'full-width characters'))
LOCALES = {'cs': 'Czech', 'da': 'Danish', 'nl': 'Dutch', 'en': 'English', 'fi': 'Finnish', 'fr': 'French',
           'de': 'German', 'el': 'Greek', 'hu': 'Hungarian', 'it': 'Italian', 'ja': 'Japanese', 'ko': 'Korean',
           'pl': 'Polish', 'pt': 'Portuguese', 'ru': 'Russian', 'es': 'Spanish', 'sv': 'Swedish', 'th': 'Thai',
           'tr': 'Turkish'}


def system_language():
    """The camera language to assume: the computer's language when the camera has it, else English."""
    import locale
    import os
    code = (os.environ.get('LC_ALL') or os.environ.get('LANG') or (locale.getlocale()[0] or '')).replace('-', '_')
    low = code.lower()
    if low.startswith('zh'):
        return 'Chinese-T' if any(t in low for t in ('tw', 'hk', 'mo', 'hant')) else 'Chinese-S'
    return LOCALES.get(low[:2], 'English')


def scan_name(name):
    """({script label: camera languages that show it} for every non-ASCII character, characters no camera language
    shows)."""
    found, bad = {}, []
    for c in name:
        if ' ' <= c <= '~':
            continue
        hit = next((sc for sc in SCRIPTS if sc[0][0] <= ord(c) <= sc[0][1]), None)
        if hit is None:
            bad.append(c)
        else:
            found.setdefault(hit[2], set()).update(hit[1])
    return found, ''.join(sorted(set(bad)))


def name_scripts(name):
    """{script label: camera languages that show it} for every non-ASCII character; refuses characters no camera
    language shows."""
    found, bad = scan_name(name)
    if bad:
        raise Refused(f'the camera cannot show {bad!r} in any of its languages; use letters '
                      'and characters of one of the camera\'s languages')
    return found


def name_slot_limit(slot):
    """The longest name the slot takes, or None when it cannot be renamed."""
    s = SLOT_DATA['names']['slots'].get(slot)
    return max(r[5] for r in s['languages']) if s else None


def name_report(name, slot, camera_language='English'):
    """The name check as data (the app words it in the user's language): problem (a refusal: 'fixed', 'space',
    'characters', 'length') with its detail, the scripts used and the camera languages showing all of them, whether
    it needs the user's confirmation (question) and whether it may be cut off (long)."""
    most = name_slot_limit(slot)
    out = dict(problem=None, detail=None, most=most, scripts=[], languages=[], question=False, long=False)
    if most is None:
        return dict(out, problem='fixed')
    if not name or name != name.strip():
        return dict(out, problem='space')
    found, bad = scan_name(name)
    if bad:
        return dict(out, problem='characters', detail=bad)
    if len(name) > most:
        return dict(out, problem='length', detail=len(name))
    width = sum(2 if 0x1100 <= ord(c) and not 0xff61 <= ord(c) <= 0xffdc else 1 for c in name)
    both = set(CAMERA_LANGUAGES)
    for langs in found.values():
        both &= langs
    return dict(out, scripts=list(found), languages=[l for l in CAMERA_LANGUAGES if l in both] if found else [],
                question=bool(found) and camera_language not in both, long=width > SLOT_DATA['names']['display_hint'])


def check_name(name, slot, camera_language='English'):
    """(notes, question) for naming a slot. A question means the name may not show with this camera language and
    needs the user's confirmation; problems are refused."""
    r = name_report(name, slot, camera_language)
    if r['problem'] == 'fixed':
        raise Refused(f'{SLOT_NAMES[slot]} cannot be renamed (its name is shared with another look)')
    if r['problem'] == 'space':
        raise Refused('the name must not be empty or start/end with a space')
    if r['problem'] == 'characters':
        name_scripts(name)
    if r['problem'] == 'length':
        raise Refused(f'{SLOT_NAMES[slot]} takes names of up to {r["most"]} characters ({name!r} has {len(name)})')
    notes, question = [], None
    if r['long']:
        notes.append(f'long names may be cut off on some screens')
    if r['scripts']:
        kinds = ', '.join(r['scripts'])
        if r['question']:
            where = ('no single camera language shows all of them' if not r['languages'] else
                     'they show when the camera is set to ' + ', '.join(r['languages']))
            question = (f'This name uses {kinds}. Your camera language is {camera_language}, so they may not show; '
                        f'{where}.')
        else:
            notes.append(f'uses {kinds}: shown with the camera set to {camera_language}; '
                         'if you switch to a language that does not use them, they may not show')
    return notes, question


def settle_name(name, slot, camera_language, accepted, log=print):
    """Show the notes on a name and, when it may not show with this camera language, get the user's answer
    (accepted beforehand, or asked at the terminal). Returns True when the name was accepted after a question."""
    notes, question = check_name(name, slot, camera_language)
    for n in notes:
        log(f'note on the name: {n}')
    if not question:
        return False
    log(question)
    if accepted:
        log('using it anyway, as accepted')
        return True
    if sys.stdin.isatty():
        try:
            ans = input('Use this name anyway? Type y to continue: ').strip().lower()
        except EOFError:
            ans = ''
        if ans in ('y', 'yes'):
            return True
    raise Refused('name not used; choose another name, set --camera-language to the camera\'s language, '
                  'or add --accept-name to use it anyway')


def name_changes(look, slot):
    """(offset, old, new, what) giving the slot the look's name in every language."""
    name = look.get('name')
    if not name:
        return []
    check_name(name, slot)                       # refusals only; the question was answered when the look was made
    nd, label = SLOT_DATA['names'], SLOT_NAMES[slot]
    s = nd['slots'][slot]
    host = next(r for r in s['languages'] if r[0] == s['host'])
    out = []
    for lang, rec_off, rec_old, text_off, text_old, room, text_at in s['languages']:
        here = len(name) <= room
        body = name.encode('utf-16-le')
        if here:
            out.append((text_off, text_old, (body + bytes(len(text_old) // 2 - len(body))).hex(), f'{label}: name ({lang})'))
        at = text_at if here else host[6]
        out.append((rec_off, rec_old, struct.pack('<II', nd['length_base'] + len(name), at).hex(), f'{label}: name ({lang})'))
    return out


ICON_WHAT = 'own icon'


def make_icon(src, size):
    """Any picture -> an RGBA icon of exactly `size` (w, h): the centre of a non-square picture, resized with clean
    transparent edges. Needs Pillow."""
    try:
        from PIL import Image
    except ImportError:
        raise Refused('--icon needs pillow: uv sync --extra photo (or python3 -m pip install pillow)')
    try:
        img = Image.open(src).convert('RGBA')
    except (OSError, ValueError) as exc:
        raise Refused(f'cannot read the icon picture {src}: {exc}')
    w, h = size
    scale = max(w / img.width, h / img.height)        # cover the icon, then keep its centre
    nw, nh = max(w, round(img.width * scale)), max(h, round(img.height * scale))
    big = img.convert('RGBa').resize((nw, nh), Image.LANCZOS)   # premultiplied: no dark fringes
    left, top = (nw - w) // 2, (nh - h) // 2
    return big.crop((left, top, left + w, top + h)).convert('RGBA')


def icon_record(src, slot):
    """The look's icon for this slot, as stored in look.json."""
    place = SLOT_DATA['icons']['slots'].get(slot)
    if place is None:
        raise Refused(f'{SLOT_NAMES[slot]} cannot take an own icon (its icon is shared with another look)')
    icon = make_icon(src, (place[1], place[2]))
    return dict(w=place[1], h=place[2], source=Path(src).name, rgba=base64.b64encode(icon.tobytes()).decode())


def icon_image(record):
    from PIL import Image
    return Image.frombytes('RGBA', (record['w'], record['h']), base64.b64decode(record['rgba']))


def icon_changes(look, slot):
    """(offset, fingerprint of the original, new, what) repainting the slot's menu icon."""
    rec = look.get('icon')
    if not rec:
        return []
    place = SLOT_DATA['icons']['slots'].get(slot)
    if place is None:
        raise Refused(f'{SLOT_NAMES[slot]} cannot take an own icon (its icon is shared with another look)')
    off, w, h, digest = place
    data = base64.b64decode(rec['rgba'])
    if (rec['w'], rec['h']) != (w, h) or len(data) != w * h * 4:
        raise Refused(f'the icon must be {w}x{h} for {SLOT_NAMES[slot]}; make the look again with --icon')
    return [(off, dict(sha256=digest, bytes=w * h * 4), data.hex(), f'{SLOT_NAMES[slot]}: {ICON_WHAT}')]


def look_slot(look):
    name = look.get('slot') or DEFAULT_SLOT
    if name not in SLOT_NAMES:
        raise Refused(f'unknown slot {name!r}; choose one of: {", ".join(SLOT_NAMES)}')
    return name


def shared_space(names):
    """The room the slots without their own storage share (less when High Contrast carries a look)."""
    pool = SLOT_DATA['pool']
    return Space(pool['always'] + ([] if DEFAULT_SLOT in names else pool['without-high-contrast']))


def room(looks):
    """{'need': bytes these looks take from the shared room, 'free': bytes available} (looks: look dicts with 'slot')."""
    looks = [looks] if isinstance(looks, dict) else list(looks)
    names = [look_slot(lk) for lk in looks]
    return dict(need=sum(extra_space(lk, n) for n, lk in zip(names, looks) if n in SLOT_DATA['extra']),
                free=shared_space(names).free())


def all_changes(looks):
    """Every change for one look, or several looks in different slots."""
    looks = [looks] if isinstance(looks, dict) else list(looks)
    names = [look_slot(lk) for lk in looks]
    if len(set(names)) != len(names):
        raise Refused('two looks for the same slot: ' + ', '.join(sorted({n for n in names if names.count(n) > 1})))
    out, taken = [], set()
    space = shared_space(names)
    by_name = dict(zip(names, looks))
    need = room(looks)['need']
    if need > space.free():
        raise Refused(f'these looks need {need} bytes of room together, {space.free()} are available'
                      + ('' if DEFAULT_SLOT not in names else ' (a look on High Contrast uses part of it)')
                      + '; use fewer slots, or looks without hue sectors or colour after the curves')
    for name in SLOT_ORDER:
        lk = by_name.get(name)
        if lk is None:
            continue
        if name == DEFAULT_SLOT:
            post = post_changes(lk)
            taken |= {o for o, _, _, _ in post}
            out += look_changes(lk) + hue_changes(lk) + post
        elif name in SLOT_DATA['slots']:
            out += slot_changes(lk, name)
        else:
            out += extra_changes(lk, name, space)
    out += space.changes()
    for name in SLOT_ORDER:
        if name in by_name:
            out += name_changes(by_name[name], name) + icon_changes(by_name[name], name)
    unlock = set(SLOT_DATA['unlock'])                # without a High Contrast look, that slot stays as it was
    fixed = [(o, a, b, DATA_WHAT) for o, a, b in DATA['fixed']
             if (o in unlock or DEFAULT_SLOT in names) and o not in taken]
    return fixed + out


def make_firmware(source, looks, log=print):
    digest = sha256(source)
    if digest != OFFICIAL_SHA256 or len(source) != OFFICIAL_BYTES:
        if digest in OTHER_KNOWN:
            raise Refused(f'this is {OTHER_KNOWN[digest]}; use the official RICOH file')
        raise Refused('input is not the official RICOH GR IV 1.11 fwdc248b.bin '
                      f'(SHA-256 {digest}, expected {OFFICIAL_SHA256})')
    log('input: official RICOH GR IV 1.11 firmware (SHA-256 verified)')
    log('decoding container ...')
    payload, frames, consumed = decode(source)
    check_container(source, payload, consumed)
    secs = sections(payload)
    rtos = next((s for s in secs if s[0] == 'RTOS'), None)
    if rtos is None:
        raise Refused('payload: RTOS section not found')
    buf = bytearray(payload)
    items = []
    for off, old, new, what in all_changes(looks):
        if isinstance(old, dict):                    # checked by fingerprint: take the original bytes from the file
            have = bytes(buf[off:off + old['bytes']])
            if hashlib.sha256(have).hexdigest() != old['sha256']:
                raise Refused(f'unexpected original bytes at {off:#x} ({what})')
            old = have.hex()
        items.append((off, old, new, what))
    icon_sec = next((s for s in secs if s[0] == SLOT_DATA['icons']['section']), None)
    spans = sorted((o, o + len(bytes.fromhex(a))) for o, a, _, _ in items)
    if any(b0 < a1 for (_, a1), (b0, _) in zip(spans, spans[1:])):
        raise Refused('internal: overlapping changes')
    for off, old, new, what in items:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        have = bytes(buf[off:off + len(old)])
        if have != old or len(new) != len(old):
            raise Refused(f'unexpected original bytes at {off:#x} ({what})')
        icon = what.endswith(ICON_WHAT) and icon_sec and icon_sec[1] <= off and off + len(new) <= icon_sec[2]
        if not (rtos[1] <= off and off + len(new) <= rtos[2]) and not icon:
            raise Refused(f'change at {off:#x} is outside the RTOS section')
        buf[off:off + len(new)] = new
    log(f'  {len(items)} changes applied ({sum(w != DATA_WHAT for *_, w in items)} carry your look)')
    patched = rezero(bytes(buf))
    log('rebuilding container ...')
    changed = differences(payload, patched)
    out, reemitted = rebuild(source, frames, consumed, patched, changed)
    log('verifying result ...')
    again, _, used = decode(out)
    if again != patched:
        raise Refused('verification: rebuilt container does not decode to the patched firmware')
    check_container(out, again, used)
    if out[:HEADER] != source[:HEADER]:
        raise Refused('verification: header changed')
    allowed = {i for off, old, _, _ in items for i in range(off, off + len(bytes.fromhex(old)))}
    allowed |= set(range(len(payload) - 4, len(payload)))
    stray = [i for i in changed if i not in allowed]
    if stray:
        raise Refused(f'verification: unexpected changes at {[hex(i) for i in stray[:8]]}')
    if sections(again) != secs:
        raise Refused('verification: section layout changed')
    open_tags = {'RTOS'} | ({icon_sec[0]} if icon_sec and any(w.endswith(ICON_WHAT) for *_, w in items) else set())
    for tag, a, b in secs:
        if tag not in open_tags and again[a:b] != payload[a:b]:
            raise Refused(f'verification: section {tag} changed')
    log(f'verified: {len(changed)} payload bytes changed, {reemitted} frames re-encoded, '
        f'all other sections identical' + (' (icons changed in the icon section only)' if len(open_tags) > 1 else ''))
    return out


def write_atomic(data, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / 'fwdc248b.bin'
    if target.exists():
        raise Refused(f'{target} already exists; choose another output directory or remove it')
    tmp = target.with_name('.fwdc248b.bin.tmp')
    tmp.write_bytes(data)
    if sha256(tmp.read_bytes()) != sha256(data):
        tmp.unlink()
        raise Refused('write verification failed')
    tmp.rename(target)
    return target


def inspect_firmware(data):
    """What a fwdc248b.bin is, as data: sha256, bytes, valid (container) and error, kind ('official', 'known',
    'unknown' or 'lutsmith'), name (for official/known), and for a GR4-LUTSmith file its looks: one dict per slot
    with slot, hue (hue sectors), post (colour after the curves), name (shown name when renamed), icon (own icon) and,
    for High Contrast, matrix."""
    digest = sha256(data)
    names = {OFFICIAL_SHA256: 'official RICOH GR IV 1.11 (unmodified)', **OTHER_KNOWN}
    out = dict(sha256=digest, bytes=len(data), valid=False, error=None, kind=None, name=None, looks=[])
    try:
        payload = check_container(data)
    except Refused as exc:
        return dict(out, error=str(exc))
    out['valid'] = True
    if digest in names:
        return dict(out, kind='official' if digest == OFFICIAL_SHA256 else 'known', name=names[digest])

    def has(changes):
        return all(payload[o:o + len(bytes.fromhex(b))] == bytes.fromhex(b) for o, _, b in changes)
    unlock = set(SLOT_DATA['unlock'])
    if not has([c for c in DATA['fixed'] if c[0] in unlock]):
        return dict(out, kind='unknown')
    looks = []
    post = has(POST['fixed'])
    post_rows = {o for o, _, _ in POST['fixed']} if post else set()
    if has([c for c in DATA['fixed'] if c[0] not in unlock and c[0] not in post_rows]):
        m = struct.unpack_from('<9h', payload, LOOK['matrix'][0])
        looks.append(dict(slot=DEFAULT_SLOT, matrix=[list(m[i:i + 3]) for i in (0, 3, 6)], hue=has(HUE['fixed']),
                          post=post))
    for name, sd in SLOT_DATA['slots'].items():
        if has(sd['defaults']):
            off, old = sd['hue']['table']
            hue = payload[off:off + len(old) // 2].hex() != old and not has(sd['hue']['off'])
            looks.append(dict(slot=name, hue=hue, post=has(sd['post']['fixed'])))
    for name, sd in SLOT_DATA['extra'].items():
        sign = sd['defaults'] + sd['fixed']          # the slot's own look-independent changes
        if sign and has(sign):
            def moved(t):
                return any(payload[o:o + 4].hex() != base for o, _, base in sd['rows'][t])
            ht = sd.get('hue_table')
            hue = moved('hue') or (ht is not None and payload[ht[0]:ht[0] + len(ht[1]) // 2].hex() != ht[1])
            looks.append(dict(slot=name, hue=hue, post=moved('colour')))

    def shown_name(slot):
        """The slot's English name as stored, when it was renamed."""
        ns = SLOT_DATA['names']['slots'].get(slot)
        if not ns:
            return None
        rows = {r[6]: r for r in ns['languages']}
        en = next(r for r in ns['languages'] if r[0] == 'English')
        if payload[en[1]:en[1] + 8].hex() == en[2]:
            return None
        length, at = struct.unpack_from('<II', payload, en[1])
        if at not in rows:
            return '?'
        off = rows[at][3]
        return payload[off:off + 2 * (length - SLOT_DATA['names']['length_base'])].decode('utf-16-le', 'replace')

    def own_icon(slot):
        place = SLOT_DATA['icons']['slots'].get(slot)
        if not place:
            return False
        off, w, h, digest = place
        return hashlib.sha256(payload[off:off + w * h * 4]).hexdigest() != digest
    for lk in looks:
        lk['name'], lk['icon'] = shown_name(lk['slot']), own_icon(lk['slot'])
    return dict(out, kind='lutsmith', looks=looks)


def identify(data, log=print):
    r = inspect_firmware(data)
    log(f'SHA-256  {r["sha256"]}')
    log(f'bytes    {r["bytes"]}')
    if not r['valid']:
        log(f'container: INVALID - {r["error"]}')
        return 2
    log('container: valid (size fields, footer, both checksums)')
    if r['kind'] in ('official', 'known'):
        log(f'file     {r["name"]}')
        return 0
    if r['kind'] == 'unknown':
        log('file     unknown - not the official 1.11 file and not made by GR4-LUTSmith')
        return 1

    def yn(x):
        return 'yes' if x else 'no'
    found = []
    for lk in r['looks']:
        f = (f'{SLOT_NAMES[lk["slot"]]} (' + (f'look matrix {lk["matrix"]}; ' if 'matrix' in lk else '')
             + f'hue sectors: {yn(lk["hue"])}; colour after the curves: {yn(lk["post"])})')
        found.append(f + (f' named "{lk["name"]}"' if lk['name'] else '') + (' with its own icon' if lk['icon'] else ''))
    log('file     a GR4-LUTSmith firmware; ' + ('personal looks in: ' + '; '.join(found) if found else
                                                 'no personal look (hidden looks unlocked only)'))
    return 0


# ------------------------------------------------------------------ LUT files
class Lut:
    """A .cube LUT (3D, or 1D expanded per channel), values as an (n,n,n,3) array [b][g][r]."""

    def __init__(self, path):
        import numpy as np
        text = Path(path).read_text(errors='replace')
        self.sha256 = hashlib.sha256(text.encode()).hexdigest()
        head, rows, self.ignored = {}, [], []
        for line in text.splitlines():
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            tok = line.split()
            if tok[0][0].isalpha():
                key = tok[0].upper()
                if key == 'TITLE':
                    head['TITLE'] = line[5:].strip().strip('"')
                elif key in ('LUT_3D_SIZE', 'LUT_1D_SIZE'):
                    head[key] = int(tok[1])
                elif key in ('DOMAIN_MIN', 'DOMAIN_MAX'):
                    head[key] = tuple(map(float, tok[1:4]))
                elif key in ('LUT_3D_INPUT_RANGE', 'LUT_1D_INPUT_RANGE'):
                    head['DOMAIN_MIN'], head['DOMAIN_MAX'] = (float(tok[1]),) * 3, (float(tok[2]),) * 3
                else:
                    self.ignored.append(tok[0])
                continue
            if len(tok) != 3:
                raise Refused(f'{path}: invalid data row {line[:40]!r}')
            rows.append(tuple(map(float, tok)))
        v = np.array(rows, float)
        if not len(v) or not np.isfinite(v).all():
            raise Refused(f'{path}: no data or non-finite values')
        self.lo = np.array(head.get('DOMAIN_MIN', (0., 0., 0.)))
        self.hi = np.array(head.get('DOMAIN_MAX', (1., 1., 1.)))
        if 'LUT_3D_SIZE' in head and 'LUT_1D_SIZE' in head:
            raise Refused(f'{path}: combined 1D + 3D LUTs are not supported')
        if 'LUT_3D_SIZE' in head:
            n = head['LUT_3D_SIZE']
            if not 2 <= n <= 129 or len(v) != n ** 3:
                raise Refused(f'{path}: LUT_3D_SIZE {n} but {len(v)} rows')
            self.kind, self.n, self.table = '3D', n, v.reshape(n, n, n, 3)
        elif 'LUT_1D_SIZE' in head:
            n = head['LUT_1D_SIZE']
            if not 2 <= n <= 65536 or len(v) != n:
                raise Refused(f'{path}: LUT_1D_SIZE {n} but {len(v)} rows')
            g = np.linspace(0, 1, 33)
            xs = np.linspace(0, 1, n)
            x = grid(33)
            out = np.stack([np.interp(x[:, c], xs, v[:, c]) for c in range(3)], 1)
            self.kind, self.n, self.table = '1D', 33, out.reshape(33, 33, 33, 3)
        else:
            raise Refused(f'{path}: no LUT_3D_SIZE or LUT_1D_SIZE')
        self.title = head.get('TITLE', '')
        if v.min() < -0.05 or v.max() > 1.05:
            raise Refused(f'{path}: output range {v.min():.2f}..{v.max():.2f} is not 0..1; '
                          'log or scene-referred LUTs are not supported')

    def __call__(self, rgb):
        """Trilinear lookup; inputs clamped to the LUT's domain."""
        import numpy as np
        n = self.n
        p = np.clip((rgb - self.lo) / (self.hi - self.lo), 0, 1) * (n - 1)
        a = np.minimum(np.floor(p).astype(int), n - 2)
        f = p - a
        out = 0
        for dr in (0, 1):
            for dg in (0, 1):
                for db in (0, 1):
                    w = ((f[:, 0] if dr else 1 - f[:, 0]) * (f[:, 1] if dg else 1 - f[:, 1])
                         * (f[:, 2] if db else 1 - f[:, 2]))
                    out = out + w[:, None] * self.table[a[:, 2] + db, a[:, 1] + dg, a[:, 0] + dr]
        return np.clip(out, 0, 1)


def grid(n):
    import numpy as np
    g = np.linspace(0, 1, n)
    b, gg, r = np.meshgrid(g, g, g, indexing='ij')
    return np.stack([r.ravel(), gg.ravel(), b.ravel()], 1)


# ------------------------------------------------------------------ colour
SRGB_TO_ADOBE = [[366 / 512, 146 / 512, 0], [0, 1, 0], [0, 21 / 512, 491 / 512]]
ADOBE_GAMMA = 563 / 256
XYZ = [[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]]
WHITE = [0.95047, 1.0, 1.08883]


def srgb_dec(x):
    import numpy as np
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def srgb_enc(x):
    import numpy as np
    x = np.clip(x, 0, 1)
    return np.where(x <= 0.0031308, 12.92 * x, 1.055 * x ** (1 / 2.4) - 0.055)


def adobe_lut_on_srgb(lut, y):
    """Apply an Adobe RGB LUT to sRGB-encoded colours, result in sRGB (colorimetric, via linear light)."""
    import numpy as np
    m = np.array(SRGB_TO_ADOBE)
    ya = np.clip(srgb_dec(y) @ m.T, 0, 1) ** (1 / ADOBE_GAMMA)
    lin = (lut(ya) ** ADOBE_GAMMA) @ np.linalg.inv(m).T
    keep = (lin >= -1e-3).all(1) & (lin <= 1 + 1e-3).all(1)
    return srgb_enc(lin), keep


def lab(rgb):
    import numpy as np
    xyz = srgb_dec(rgb) @ np.array(XYZ).T / np.array(WHITE)
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])], 1)


def de00(l1, l2):
    """CIEDE2000 (Sharma, Wu & Dalal 2005)."""
    import numpy as np
    L1, a1, b1 = l1.T
    L2, a2, b2 = l2.T
    C1, C2 = np.hypot(a1, b1), np.hypot(a2, b2)
    Cb = (C1 + C2) / 2
    G = 0.5 * (1 - np.sqrt(Cb ** 7 / (Cb ** 7 + 25 ** 7)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360
    dLp, dCp = L2 - L1, C2p - C1p
    dh = h2p - h1p
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(C1p * C2p == 0, 0, dh)
    dHp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(dh / 2))
    Lbp, Cbp = (L1 + L2) / 2, (C1p + C2p) / 2
    hs = h1p + h2p
    hbp = np.where(C1p * C2p == 0, hs, np.where(np.abs(h1p - h2p) <= 180, hs / 2,
                                                np.where(hs < 360, (hs + 360) / 2, (hs - 360) / 2)))
    T = (1 - 0.17 * np.cos(np.radians(hbp - 30)) + 0.24 * np.cos(np.radians(2 * hbp))
         + 0.32 * np.cos(np.radians(3 * hbp + 6)) - 0.20 * np.cos(np.radians(4 * hbp - 63)))
    dth = 30 * np.exp(-((hbp - 275) / 25) ** 2)
    Rc = 2 * np.sqrt(Cbp ** 7 / (Cbp ** 7 + 25 ** 7))
    Sl = 1 + 0.015 * (Lbp - 50) ** 2 / np.sqrt(20 + (Lbp - 50) ** 2)
    Sc, Sh = 1 + 0.045 * Cbp, 1 + 0.015 * Cbp * T
    Rt = -np.sin(np.radians(2 * dth)) * Rc
    return np.sqrt((dLp / Sl) ** 2 + (dCp / Sc) ** 2 + (dHp / Sh) ** 2 + Rt * (dCp / Sc) * (dHp / Sh))


# ------------------------------------------------------------------ camera model
def base_curve(name):
    import numpy as np
    return np.array(DATA['curves'][name], float) / 16384


def inverse(c, y):
    """Inverse of a base curve on its rising part: output (0..1) -> curve index."""
    import numpy as np
    top = int(np.argmax(c >= 1.0)) if (c >= 1.0).any() else 255
    xs, ys = c[:top + 1], np.arange(256.)[:top + 1]
    keep = np.concatenate([[True], np.diff(xs) > 0])
    return np.interp(np.clip(y, 0, 1), xs[keep], ys[keep])


def to_index(y):
    """Camera output of its neutral rendering (0..1) -> linear signal in curve-index units."""
    return inverse(base_curve('neutral'), y)


def compose(curve, base):
    """The camera's composition of a look curve after the base curve (integer, as the firmware)."""
    out = []
    for v in base:
        i, f = v >> 6, v & 0x3f
        if i > 0xfe:
            out.append(int(curve[255]))
        else:
            p = (int(curve[i + 1]) - int(curve[i])) * f
            out.append(int(curve[i]) + ((p + 0x3f) >> 6 if p < 0 else p >> 6))
    return out


def exported(curves):
    """The camera's final per-channel tone curves (10-bit) for a look."""
    import numpy as np
    base = [int(x) for x in DATA['curves']['slot']]
    src = [base] * 3 if not curves else [compose(c, base) for c in curves]
    return [np.minimum(np.array(s) >> 4, 1023) for s in src]


def render(v, look, bounds=None):
    """Linear signal (N,3, index units) -> what the camera writes (0..1), firmware-exact curves."""
    import numpy as np
    M = np.array(look['matrix_q13'], float) / 8192
    z = v @ M.T
    q = (look.get('hue_sectors') or {}).get('matrices_q10')
    if q:
        z = hue_apply_q(z, np.array(q), HUE_BOUNDS if bounds is None else bounds)
    ex = exported(look.get('curves'))
    out = np.stack([np.interp(np.clip(z[:, c], 0, 255), np.arange(256.), ex[c]) / 1023 for c in range(3)], 1)
    p = look.get('post_matrix_q9')
    if p:
        out = np.clip(out @ post_effective(p).T, 0, 1)
    return out


# ------------------------------------------------------------------ colour after the tone curves
# The camera can mix the colours once more after the tone curves: a 3x3 matrix P whose rows sum to 1, so greys
# stay grey. Film looks often treat colours differently in shadows and highlights; a mix after the curves can
# follow that where the colour matrix before them cannot. Every Hue step is set, each with the camera's own Hue
# change on top of the look.
POST_LIMIT = 0.98         # the camera's values beyond this are penalised while fitting (keeps them in range)
POST_PHOTO_WEIGHTS = (0.5, 2.0, 8.0)    # photo and grey weights tried, so everyday content can count for more
POST_WARM_LR = 1e-3                     # smaller steps when continuing from a fitted look


def post_hue(h):
    """The camera's own values for Hue step h (0..8, 4 = Hue 0)."""
    import numpy as np
    a, s, d = POST['hue'][h]
    return np.array([[8192, 0, 0], [0, a, s], [0, -s, d]], np.int64)


def post_targets(p_q9):
    """The camera's nine matrices (one per Hue step) for P; the look is shown at Hue 0 (step 4), the other
    steps are kept within the camera's range."""
    import numpy as np
    P = np.array(p_q9, float) / 512
    Y = np.array(HUE_YCC, np.int64)
    out = []
    for h in range(9):
        H = post_hue(h) @ Y / 8192                             # the camera's own matrix at this Hue step
        exact = H @ P
        t = np.rint(exact).astype(int)
        for r in range(3):                                     # rows keep their sums: greys stay grey
            d = int(np.rint(H[r].sum())) - t[r].sum()
            while d:
                j = int(np.argmax((exact[r] - t[r]) * np.sign(d)))
                t[r, j] += np.sign(d)
                d -= np.sign(d)
        out.append(t if h == 4 else np.clip(t, -256, 255))
    return np.array(out)


def post_effective(p_q9):
    """P as the camera really applies it at Hue 0, after rounding."""
    import numpy as np
    return np.linalg.solve(np.array(HUE_YCC, float), post_targets(p_q9)[4].astype(float))


def post_quantise(P):
    import numpy as np
    q = np.rint(np.asarray(P) * 512).astype(int)
    for r in range(3):
        q[r, r] += 512 - q[r].sum()
    t = post_targets(q)
    if t.min() < -256 or t.max() > 255:
        raise Refused(f'colour matrix after the curves is out of the camera\'s range ({t.min()}..{t.max()})')
    return q


# ------------------------------------------------------------------ fitting
START_SPREAD = dict(matrix=0.1, curve=0.25, after=0.03)   # how far a random start (turns, --seed) may lie
CHANNELS = {'single': [0, 0, 0], 'rg-b': [0, 0, 1], 'r-gb': [0, 1, 1], 'rb-g': [0, 1, 0], 'rgb': [0, 1, 2]}


def _softplus(x):
    import numpy as np
    return np.logaddexp(0, x)


class Fitter:
    """Gradient descent (Adam) on a perceptual colour difference.

    Model: out = P . C[ T(M v) ] with M a white-preserving 3x3 matrix, T the slot's
    base curve, C one, two or three free non-decreasing curves (curve mode) and, with
    post, P a colour mix after the curves (rows sum to 1; otherwise P = I). Loss per colour: a
    CIE94/CIEDE2000-style weighted Lab distance in the target's lightness/chroma/hue
    frame, sqrt'ed so it behaves like ΔE; plus a smoothness penalty on the curves.
    """

    def __init__(self, v, target, mode='single', smooth=50.0, weight=None, post=False):
        import numpy as np
        self.v, self.mode, self.smooth, self.post = v, mode, smooth, post
        w = np.ones(len(v)) if weight is None else np.asarray(weight, float)
        self.w = w / w.sum()                                          # per-colour weights, sum 1
        self.chan = CHANNELS[mode]
        lt = lab(target)
        self.lt = lt
        C = np.hypot(lt[:, 1], lt[:, 2])
        self.u = np.where(C[:, None] > 1e-6, lt[:, 1:] / np.maximum(C, 1e-6)[:, None], [1.0, 0.0])
        self.h = np.stack([-self.u[:, 1], self.u[:, 0]], 1)
        self.SL = 1 + 0.015 * (lt[:, 0] - 50) ** 2 / np.sqrt(20 + (lt[:, 0] - 50) ** 2)
        self.SC, self.SH = 1 + 0.045 * C, 1 + 0.015 * C
        self.T = base_curve('slot')
        self.Tslope = np.diff(self.T)

    def curves(self, raw):
        import numpy as np
        return np.cumsum(_softplus(raw), 1)

    def loss_grad(self, theta, raw, phi=None):
        import numpy as np
        v, N = self.v, len(self.v)
        M = np.concatenate([theta, 1 - theta.sum(1, keepdims=True)], 1)
        z = v @ M.T
        zc = np.clip(z, 0, 255)
        i = np.minimum(np.floor(zc).astype(int), 254)
        f = zc - i
        b = self.T[i] * (1 - f) + self.T[i + 1] * f
        mz = (z > 0) & (z < 255)
        Ck = self.curves(raw)
        Cc = Ck[self.chan]                                           # (3,256)
        idx = np.clip(b * 256, 0, 255)
        j = np.minimum(np.floor(idx).astype(int), 254)
        g = idx - j
        rows = np.arange(3)[None, :]
        c0, c1 = Cc[rows, j], Cc[rows, j + 1]
        out = c0 * (1 - g) + c1 * g
        mi = (b * 256 > 0) & (b * 256 < 255)
        cur = out
        if phi is not None:                                          # colour matrix after the curves
            P = np.concatenate([phi, 1 - phi.sum(1, keepdims=True)], 1)
            out = cur @ P.T
        # Lab of the prediction
        x = np.clip(out, 0, 1)
        lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
        dlin = np.where(x <= 0.04045, 1 / 12.92, 2.4 / 1.055 * ((x + 0.055) / 1.055) ** 1.4) * ((out > 0) & (out < 1))
        t = lin @ np.array(XYZ).T / np.array(WHITE)
        big = t > 216 / 24389
        F = np.where(big, np.cbrt(np.maximum(t, 1e-12)), (24389 / 27 * t + 16) / 116)
        dF = np.where(big, 1 / 3 * np.maximum(t, 1e-12) ** (-2 / 3), 24389 / 27 / 116)
        lp = np.stack([116 * F[:, 1] - 16, 500 * (F[:, 0] - F[:, 1]), 200 * (F[:, 1] - F[:, 2])], 1)
        d = lp - self.lt
        dL = d[:, 0]
        dC = (d[:, 1:] * self.u).sum(1)
        dH = (d[:, 1:] * self.h).sum(1)
        q = (dL / self.SL) ** 2 + (dC / self.SC) ** 2 + (dH / self.SH) ** 2
        e = np.sqrt(q + 1e-8)
        loss = (self.w * e).sum()
        w = self.w / (2 * e)
        gL = w * 2 * dL / self.SL ** 2
        gab = (w * 2)[:, None] * ((dC / self.SC ** 2)[:, None] * self.u + (dH / self.SH ** 2)[:, None] * self.h)
        gLab = np.concatenate([gL[:, None], gab], 1)
        J = np.array([[0, 116, 0], [500, -500, 0], [0, 200, -200]], float)
        gF = gLab @ J
        gt = gF * dF / np.array(WHITE)
        glin = gt @ np.array(XYZ)
        gout = glin * dlin
        gphi = None
        if phi is not None:
            gphi = np.stack([(gout * (cur[:, 0:1] - cur[:, 2:3])).sum(0), (gout * (cur[:, 1:2] - cur[:, 2:3])).sum(0)], 1)
            Y = np.array(HUE_YCC, float) / 256
            YP = Y @ P
            over = np.maximum(np.abs(YP) - POST_LIMIT, 0) * np.sign(YP)
            gP = Y.T @ (2 * HUE_RANGE * over)
            gphi += gP[:, :2] - gP[:, 2:3]
            loss_pen = HUE_RANGE * (over ** 2).sum()
            gout = gout @ P
        # through the look curves
        gC = np.zeros_like(Ck)
        for c in range(3):
            k = self.chan[c]
            np.add.at(gC[k], j[:, c], gout[:, c] * (1 - g[:, c]))
            np.add.at(gC[k], j[:, c] + 1, gout[:, c] * g[:, c])
        gidx = gout * (c1 - c0) * mi
        gz = gidx * 256 * self.Tslope[i] * mz
        gtheta = np.stack([(gz * (v[:, 0:1] - v[:, 2:3])).sum(0), (gz * (v[:, 1:2] - v[:, 2:3])).sum(0)], 1)
        # smoothness penalty
        D = Ck[:, 2:] - 2 * Ck[:, 1:-1] + Ck[:, :-2]
        pen = self.smooth * (D ** 2).sum(1).mean()
        gD = 2 * self.smooth * D / len(Ck)
        gC[:, 2:] += gD
        gC[:, 1:-1] -= 2 * gD
        gC[:, :-2] += gD
        sig = 1 / (1 + np.exp(-raw))
        graw = sig * np.cumsum(gC[:, ::-1], 1)[:, ::-1]
        if phi is not None:
            pen += loss_pen
        return loss + pen, loss, gtheta, graw, gphi

    def run(self, target, iters=1500, lr=5e-3, log=None, init=None, rng=None):
        """init: continue from a fitted look (same curve mode); rng: a random start (a different
        starting point for each seed) instead of the classic one; neither: the classic start."""
        import numpy as np
        v = self.v
        if init is not None:                         # continue from a fitted look (same curve mode)
            M0 = np.array(init['matrix_q13'], float) / 8192
            theta = M0[:, :2].copy()
            first = [self.chan.index(k) for k in range(max(self.chan) + 1)]
            C0 = np.array(init['curves'], float)[first] / 16384
            dd = np.clip(np.diff(np.concatenate([np.zeros((len(C0), 1)), C0], 1), axis=1), 1e-6, None)
            raw = np.log(np.expm1(dd))
            return self._descend(theta, raw, iters, lr, log)
        # least-squares start for the matrix, identity start for the curves
        inv_t = inverse(self.T, target)
        theta = np.zeros((3, 2))
        xr = v[:, :2] - v[:, 2:3]
        for c in range(3):
            theta[c] = np.linalg.lstsq(xr, inv_t[:, c] - v[:, 2], rcond=None)[0]
        if not np.isfinite(theta).all() or np.abs(theta).max() > 3:
            theta = np.array([[1., 0.], [0., 1.], [0., 0.]])
        ident = np.arange(256) / 256.0
        dd = np.clip(np.diff(np.concatenate([[0.0], ident])), 1e-6, None)
        raw = np.tile(np.log(np.expm1(dd)), (max(self.chan) + 1, 1))
        phi = None
        if rng is not None:                      # a random start around the classic one
            theta = theta + rng.normal(0, START_SPREAD['matrix'], theta.shape)
            n = max(self.chan) + 1
            g = np.exp(rng.normal(0, START_SPREAD['curve'], n))[:, None]      # a random gamma per curve
            dd = np.clip(np.diff(np.concatenate([np.zeros((n, 1)), np.tile(ident, (n, 1)) ** g], 1), axis=1), 1e-6, None)
            raw = np.log(np.expm1(dd))
            if self.post:
                phi = np.array([[1., 0.], [0., 1.], [0., 0.]]) + rng.normal(0, START_SPREAD['after'], (3, 2))
        return self._descend(theta, raw, iters, lr, log, phi)

    def _descend(self, theta, raw, iters, lr, log, phi=None):
        import numpy as np
        if self.post and phi is None:
            phi = np.array([[1., 0.], [0., 1.], [0., 0.]])
        elif not self.post:
            phi = None
        params = (theta, raw) + ((phi,) if self.post else ())
        state = [[np.zeros_like(p), np.zeros_like(p)] for p in params]
        hist = []
        for it in range(iters):
            if it % 25 == 0:
                check_stop()
            total, loss, gt, gr, gp_ = self.loss_grad(theta, raw, phi)
            a = lr * 0.5 * (1 + math.cos(math.pi * it / iters))
            for p, gp, st in zip(params, (gt, gr, gp_), state):
                st[0] = 0.9 * st[0] + 0.1 * gp
                st[1] = 0.999 * st[1] + 0.001 * gp ** 2
                mh, vh = st[0] / (1 - 0.9 ** (it + 1)), st[1] / (1 - 0.999 ** (it + 1))
                p -= a * mh / (np.sqrt(vh) + 1e-8)
            if it % max(1, iters // 10) == 0 or it == iters - 1:
                hist.append([it, round(float(loss), 4)])
                if log:
                    log(f'    step {it:5d}  loss {loss:.4f}')
        M = np.concatenate([theta, 1 - theta.sum(1, keepdims=True)], 1)
        q = np.round(M * 8192).astype(int)
        q[:, 2] = 8192 - q[:, 0] - q[:, 1]
        Ck = np.clip(np.round(self.curves(raw) * 16384), 0, 16383).astype(int)
        Ck = np.maximum.accumulate(Ck, axis=1)
        out = dict(matrix_q13=q.tolist(), curve_mode=self.mode,
                   curves=[Ck[k].tolist() for k in self.chan], history=hist)
        if self.post:
            out['post_matrix_q9'] = post_quantise(np.concatenate([phi, 1 - phi.sum(1, keepdims=True)], 1)).tolist()
        return out


# ------------------------------------------------------------------ hue sectors
# The camera can change colours separately in 12 ranges of hue, each with its own 3x3 matrix, after the
# look matrix and before the tone curves. The fit keeps neighbouring sectors joined (no gaps between
# colours) and greys unchanged, and keeps colours near a sector edge smooth (HUE_BUDGET).
HUE_BOUNDS = [359.8, 27.4, 62.2, 92.6, 112.0, 142.8, 179.2, 211.4, 238.6, 267.2, 306.0, 326.5]
HUE_SIGMA = [1.0, 2.5, 2.5, 1.0, 1.0, 2.5, 2.5, 2.0, 1.0, 2.0, 5.0, 3.0]
HUE_YCC = [[77, 150, 29], [-43, -85, 128], [128, -107, -21]]          # /256; also for colour after the curves
HUE_KINKS = (0.3, 1, 3, 10, 30, 100)          # seam penalty, weakest first; the first within budget is used
HUE_BUDGET = dict(photo_p99=0.3, photo_max=1.0, lattice_p99=1.0)
HUE_PHOTO_WEIGHTS = (0.5, 2.0, 8.0)          # weights tried for the photo
HUE_RIDGE, HUE_LIMIT, HUE_RANGE = 0.02, 1.85, 1e3   # size penalty; matrix range limit


def _ycc():
    import numpy as np
    S = np.array(HUE_YCC, float) / 256
    return S, np.linalg.inv(S)


def hue_coords(z, bounds):
    """Sector index and position within the sector for each colour z (N,3)."""
    import numpy as np
    S, _ = _ycc()
    bounds = np.asarray(bounds, float)
    ycc = z @ S.T
    ang = np.degrees(np.arctan2(ycc[:, 2], ycc[:, 1])) % 360
    g = np.argmin((ang[:, None] - bounds[None, :]) % 360, 1)
    th0, th1 = np.radians(bounds[g]), np.radians(bounds[(g + 1) % 12])
    det = np.cos(th0) * np.sin(th1) - np.sin(th0) * np.cos(th1)
    cb, cr = ycc[:, 1], ycc[:, 2]
    return g, (cb * np.sin(th1) - cr * np.cos(th1)) / det, (cr * np.cos(th0) - cb * np.sin(th0)) / det


def hue_matrices(D, bounds=None):
    """Per-sector 3x3 matrices for the parameters D (12,3)."""
    import numpy as np
    S, _ = _ycc()
    bounds = HUE_BOUNDS if bounds is None else bounds
    out = []
    for g in range(12):
        th0, th1 = np.radians(bounds[g]), np.radians(bounds[(g + 1) % 12])
        U = np.array([[np.cos(th0), np.cos(th1)], [np.sin(th0), np.sin(th1)]])
        out.append(np.eye(3) + np.stack([D[g], D[(g + 1) % 12]], 1) @ np.linalg.inv(U) @ S[1:])
    return np.array(out)


def hue_quantise(mats):
    import numpy as np
    q = np.rint(mats * 1024).astype(int)
    for g in range(12):
        for r in range(3):
            q[g, r, r] += 1024 - q[g, r].sum()
    if np.abs(q).max() > 2047:
        raise Refused(f'hue-sector matrix value {np.abs(q).max()} is out of range')
    return q


def hue_apply_q(z, q, bounds):
    import numpy as np
    g, _, _ = hue_coords(z, bounds)
    return np.einsum('nij,nj->ni', q[g] / 1024, z)


def hue_kinks():
    """Linear map from the parameters to the smoothness penalty at each sector edge."""
    import numpy as np
    _, S_inv = _ycc()
    th = np.radians(HUE_BOUNDS)
    perp = (S_inv @ np.stack([np.zeros_like(th), -np.sin(th), np.cos(th)])).T
    cols = []
    for k in range(36):
        e = np.zeros(36)
        e[k] = 1
        M = hue_matrices(e.reshape(12, 3))
        cols.append(np.concatenate([(M[b] - M[b - 1]) @ perp[b] for b in range(12)]))
    return np.array(cols).T * np.repeat(HUE_SIGMA, 3)[:, None]


class HueFitter:
    """Adam on the same loss as Fitter, with the look's matrix and curves fixed; parameters D (12,3)."""

    def __init__(self, v, target, weight, look):
        import numpy as np
        self.f = Fitter(v, target, look['curve_mode'] if look.get('curves') else 'single', weight=weight)
        M = np.array(look['matrix_q13'], float) / 8192
        self.z = v @ M.T
        self.g, self.a, self.b = hue_coords(self.z, HUE_BOUNDS)
        cv = look.get('curves')
        self.C = np.array(cv, float) / 16384 if cv else np.tile(np.arange(256) / 256.0, (3, 1))
        pq = look.get('post_matrix_q9')
        self.P = post_effective(pq) if pq else None
        K = hue_kinks()
        self.KtK = K.T @ K
        self.B = np.array([hue_matrices(e.reshape(12, 3)).ravel() - hue_matrices(np.zeros((12, 3))).ravel()
                           for e in np.eye(36)]).T
        self.M0 = hue_matrices(np.zeros((12, 3))).ravel()
        self.kink = HUE_KINKS[0]

    def loss_grad(self, D):
        import numpy as np
        f = self.f
        z = self.z + self.a[:, None] * D[self.g] + self.b[:, None] * D[(self.g + 1) % 12]
        zc = np.clip(z, 0, 255)
        i = np.minimum(np.floor(zc).astype(int), 254)
        fr = zc - i
        bb = f.T[i] * (1 - fr) + f.T[i + 1] * fr
        mz = (z > 0) & (z < 255)
        idx = np.clip(bb * 256, 0, 255)
        j = np.minimum(np.floor(idx).astype(int), 254)
        gg = idx - j
        rows = np.arange(3)[None, :]
        c0, c1 = self.C[rows, j], self.C[rows, j + 1]
        out = c0 * (1 - gg) + c1 * gg
        mi = (bb * 256 > 0) & (bb * 256 < 255)
        if self.P is not None:
            out = out @ self.P.T
        x = np.clip(out, 0, 1)
        lin = np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)
        dlin = np.where(x <= 0.04045, 1 / 12.92, 2.4 / 1.055 * ((x + 0.055) / 1.055) ** 1.4) * ((out > 0) & (out < 1))
        t = lin @ np.array(XYZ).T / np.array(WHITE)
        big = t > 216 / 24389
        F = np.where(big, np.cbrt(np.maximum(t, 1e-12)), (24389 / 27 * t + 16) / 116)
        dF = np.where(big, 1 / 3 * np.maximum(t, 1e-12) ** (-2 / 3), 24389 / 27 / 116)
        lp = np.stack([116 * F[:, 1] - 16, 500 * (F[:, 0] - F[:, 1]), 200 * (F[:, 1] - F[:, 2])], 1)
        d = lp - f.lt
        dL, dC, dH = d[:, 0], (d[:, 1:] * f.u).sum(1), (d[:, 1:] * f.h).sum(1)
        e = np.sqrt((dL / f.SL) ** 2 + (dC / f.SC) ** 2 + (dH / f.SH) ** 2 + 1e-8)
        loss = (f.w * e).sum()
        w = f.w / (2 * e)
        gLab = np.concatenate([(w * 2 * dL / f.SL ** 2)[:, None],
                               (w * 2)[:, None] * ((dC / f.SC ** 2)[:, None] * f.u + (dH / f.SH ** 2)[:, None] * f.h)], 1)
        gout = ((gLab @ np.array([[0, 116, 0], [500, -500, 0], [0, 200, -200]], float)) * dF / np.array(WHITE)
                @ np.array(XYZ)) * dlin
        if self.P is not None:
            gout = gout @ self.P
        gz = gout * (c1 - c0) * mi * 256 * f.Tslope[i] * mz
        gD = np.zeros((12, 3))
        np.add.at(gD, self.g, self.a[:, None] * gz)
        np.add.at(gD, (self.g + 1) % 12, self.b[:, None] * gz)
        dv = D.ravel()
        pen = self.kink * (dv @ self.KtK @ dv) / 12 + HUE_RIDGE * (dv @ dv) / 12
        gpen = self.kink * 2 / 12 * (self.KtK @ dv) + HUE_RIDGE * 2 / 12 * dv
        mv = self.M0 + self.B @ dv
        over = np.maximum(np.abs(mv) - HUE_LIMIT, 0)
        pen += HUE_RANGE * (over ** 2).sum()
        gpen += self.B.T @ (HUE_RANGE * 2 * over * np.sign(mv))
        return loss + pen, loss, gD + gpen.reshape(12, 3)

    def run(self, iters=800, lr=0.02):
        import numpy as np
        D = np.zeros((12, 3))
        m1, m2 = np.zeros_like(D), np.zeros_like(D)
        for it in range(iters):
            if it % 25 == 0:
                check_stop()
            total, loss, gD = self.loss_grad(D)
            a = lr * 0.5 * (1 + math.cos(math.pi * it / iters))
            m1 = 0.9 * m1 + 0.1 * gD
            m2 = 0.999 * m2 + 0.001 * gD ** 2
            D -= a * (m1 / (1 - 0.9 ** (it + 1))) / (np.sqrt(m2 / (1 - 0.999 ** (it + 1))) + 1e-8)
        return D


def hue_seam_risk(look, y, held):
    """How much colours near sector edges could change (ΔE00), to keep them smooth."""
    import numpy as np
    out = {}
    for name, pts in (('lattice', y), ('photo', held[0] if held else None)):
        if pts is None:
            continue
        v = to_index(pts)
        nom = lab(np.clip(render(v, look), 0, 1))
        e = np.max([de00(nom, lab(np.clip(render(v, look, np.array(HUE_BOUNDS) + sg * np.array(HUE_SIGMA)), 0, 1)))
                    for sg in (-1, 1)], 0)
        out[name] = dict(p99=round(float(np.percentile(e, 99)), 3), max=round(float(e.max()), 3))
    return out


def hue_within_budget(sr):
    ok = sr['lattice']['p99'] <= HUE_BUDGET['lattice_p99']
    if 'photo' in sr:
        ok = ok and sr['photo']['p99'] <= HUE_BUDGET['photo_p99'] and sr['photo']['max'] <= HUE_BUDGET['photo_max']
    return ok


def more_levers_ok(new, old):
    """Accept more levers only for a clear gain (whole range or ColorChecker) with nothing worse."""
    def f(s):
        return dict(e=s['e'], cc=s['cc'], grey=s['grey'], h=s.get('h'))
    n, o = f(new), f(old)
    gain = n['e'] < o['e'] * (1 - PAIR_GAIN) or n['cc'] < o['cc'] * (1 - PAIR_GAIN)
    no_worse = ((o['h'] is None or n['h'] <= o['h'] + HELD_TOL) and n['grey'] <= o['grey'] + GREY_TOL
                and n['cc'] <= o['cc'] + CC_TOL and n['e'] <= o['e'] * (1 + RANGE_TOL))
    return gain and no_worse


def fit_hue(look, v, t, weight, n_lut, y, ty, held, lut, space, iters=800, log=print):
    """Hue sectors on top of a fitted look (matrix and curves kept). Returns the hue_sectors data
    to add to the look, or None when they do not clearly help."""
    import numpy as np

    def summary(lk):
        ev = everyday(lk, lut, space)
        return dict(e=evaluate(lk, y, ty)['de00_mean'], cc=ev['colorchecker']['de00_mean'],
                    grey=ev['greys']['de00_mean'], h=evaluate(lk, *held)['de00_mean'] if held else None)
    base = summary(look)
    candidates = []
    for pw in (HUE_PHOTO_WEIGHTS if held else HUE_PHOTO_WEIGHTS[:1]):
        w = np.asarray(weight, float).copy()
        w[n_lut:] *= pw / PHOTO_WEIGHT
        hf = HueFitter(v, t, w / w.sum(), look)
        for kink in HUE_KINKS:
            hf.kink = kink
            D = hf.run(iters)
            try:
                q = hue_quantise(hue_matrices(D))
            except Refused:
                continue
            cand = dict(look, hue_sectors=dict(matrices_q10=q.tolist()))
            sr = hue_seam_risk(cand, y, held)
            if not hue_within_budget(sr):
                continue
            s = summary(cand)
            ok = more_levers_ok(s, base)
            log(f'  hue     ΔE00 {s["e"]:.3f}' + (f'  photo {s["h"]:.3f}' if s['h'] is not None else '')
                + f'  ColorChecker {s["cc"]:.3f}  greys {s["grey"]:.3f}' + ('' if ok else '  (not better)'))
            if ok:
                candidates.append((s['e'], dict(matrices_q10=q.tolist(), photo_weight=pw, seam_weight=kink,
                                                seam_risk=sr)))
            break
    if not candidates:
        log('  hue sectors: no clear gain, not used')
        return None
    return min(candidates, key=lambda c: c[0])[1]


# ------------------------------------------------------------------ scoring
HUES = {'red': (330, 30), 'orange/skin': (30, 75), 'yellow': (75, 105), 'green': (105, 180),
        'cyan': (180, 240), 'blue': (240, 290), 'purple/magenta': (290, 330)}


def score(pred, target):
    import numpy as np
    lt, lp = lab(target), lab(np.clip(pred, 0, 1))
    e = de00(lt, lp)
    C = np.hypot(lt[:, 1], lt[:, 2])
    h = np.degrees(np.arctan2(lt[:, 2], lt[:, 1])) % 360

    def band(mk):
        return round(float(e[mk].mean()), 3) if mk.sum() >= 20 else None
    r = dict(colours=int(len(e)), de00_mean=round(float(e.mean()), 3), de00_p95=round(float(np.percentile(e, 95)), 3),
             brightness_shift=round(float((lp[:, 0] - lt[:, 0]).mean()), 2),
             hue={k: band((((h >= a) | (h < b)) if a > b else ((h >= a) & (h < b))) & (C >= 10))
                  for k, (a, b) in HUES.items()})
    r['verdict'] = 'good' if r['de00_mean'] < 1 and r['de00_p95'] < 2 else \
        'approximate' if r['de00_mean'] < 2 and r['de00_p95'] < 4 else 'poor'
    return r


def evaluate(look, y, target):
    return score(render(to_index(y), look), target)


def unchanged(y, target):
    """How far the LUT is from the camera's neutral rendering (no look at all)."""
    return score(neutral(to_index(y)), target)


# ------------------------------------------------------------------ photos (optional)
def photo_signal(dng, max_side=400):
    """Linear signal of a DNG in the camera model's units (needs rawpy)."""
    import numpy as np
    try:
        import rawpy
    except ImportError:
        raise Refused('--photo / preview need rawpy and pillow: python3 -m pip install rawpy pillow')
    with rawpy.imread(str(dng)) as r:
        img = r.raw_image_visible.astype(np.float32)
        s = r.sizes
        black = np.array(r.black_level_per_channel, np.float32)
        white = float(r.white_level)
        cl = r.raw_colors_visible
        wb = np.array(r.camera_whitebalance[:3], float)
    if s.crop_height and s.crop_width:
        t, l = s.crop_top_margin, s.crop_left_margin
        img, cl = img[t:t + s.crop_height, l:l + s.crop_width], cl[t:t + s.crop_height, l:l + s.crop_width]
    img = (img - black[cl]) / (white - black[cl])
    h, w = img.shape[0] // 2 * 2, img.shape[1] // 2 * 2
    q = img[:h, :w].reshape(h // 2, 2, w // 2, 2).transpose(0, 2, 1, 3).reshape(h // 2, w // 2, 4)
    qc = cl[:2, :2].reshape(4)
    rgb = np.stack([q[..., [k for k in range(4) if qc[k] in idx]].mean(-1) for idx in ([0], [1, 3], [2])], -1)
    f = max(1, int(np.ceil(max(rgb.shape[:2]) / max_side)))
    H, W = rgb.shape[0] // f * f, rgb.shape[1] // f * f
    rgb = rgb[:H, :W].reshape(H // f, f, W // f, f, 3).mean((1, 3))
    yy, xx = np.mgrid[0:rgb.shape[0], 0:rgb.shape[1]]
    r2 = ((yy - rgb.shape[0] / 2) / (rgb.shape[0] / 2)) ** 2 + ((xx - rgb.shape[1] / 2) / (rgb.shape[1] / 2)) ** 2
    cam = rgb * (wb / wb[1]) * np.exp(0.18 * r2)[..., None]
    return cam @ (np.array(DATA['camera_matrix']) / 8192).T * 100.0


def neutral(v):
    """The camera's neutral rendering of a linear signal (no look)."""
    import numpy as np
    T = base_curve('neutral')
    return np.stack([np.interp(np.clip(v[..., c], 0, 255), np.arange(256.), T) for c in range(3)], -1)


def apply_lut(lut, y, space):
    if space == 'adobe':
        return adobe_lut_on_srgb(lut, y)[0]
    return lut(y)


# ------------------------------------------------------------------ fit command
PAIR_GAIN, HELD_TOL, PHOTO_GAIN = 0.10, 0.02, 0.5
PHOTO_WEIGHT = 0.5      # the photo counts half as much as the LUT's own colours, however many pixels it has
GREY_WEIGHT = 0.5       # neutral greys count half as much too: a colour cast on greys is the most visible error
GREY_TOL, CC_TOL = 0.10, 0.10            # more levers must not make greys or the ColorChecker worse by more;
                                         # these, RANGE_TOL and HELD_TOL are also the margins when comparing results
GREY_GAIN, RANGE_TOL = 0.5, 0.05         # ... and count as a gain when greys improve by 0.5 (range within 5 %)
EVERYDAY_CC, EVERYDAY_GREY, EVERYDAY_CAST = 2.0, 2.0, 2.5
# "good on everyday colours": ColorChecker mean < 2 and greys < 2 (a small visible difference at most),
# and no visible colour cast on any grey (largest a*b* difference < 2.5)

# X-Rite ColorChecker Classic, widely published sRGB values (8-bit): everyday reference colours
COLORCHECKER = [
    ('dark skin', 115, 82, 68), ('light skin', 194, 150, 130), ('blue sky', 98, 122, 157),
    ('foliage', 87, 108, 67), ('blue flower', 133, 128, 177), ('bluish green', 103, 189, 170),
    ('orange', 214, 126, 44), ('purplish blue', 80, 91, 166), ('moderate red', 193, 90, 99),
    ('purple', 94, 60, 108), ('yellow green', 157, 188, 64), ('orange yellow', 224, 163, 46),
    ('blue', 56, 61, 150), ('green', 70, 148, 73), ('red', 175, 54, 60), ('yellow', 231, 199, 31),
    ('magenta', 187, 86, 149), ('cyan', 8, 133, 161), ('white', 243, 243, 242), ('neutral 8', 200, 200, 200),
    ('neutral 6.5', 160, 160, 160), ('neutral 5', 122, 122, 121), ('neutral 3.5', 85, 85, 85), ('black', 52, 52, 52)]


def greys(n=64):
    import numpy as np
    x = np.linspace(0.02, 1.0, n)
    return np.stack([x, x, x], 1)


def everyday(look, lut, space):
    """Scores on the ColorChecker and on a neutral grey ramp (with the largest colour cast on the greys)."""
    import numpy as np
    out = {}
    for name, y in (('colorchecker', np.array([c[1:] for c in COLORCHECKER], float) / 255), ('greys', greys())):
        t = apply_lut(lut, y, space)
        lt, lp = lab(t), lab(np.clip(render(to_index(y), look), 0, 1))
        e = de00(lt, lp)
        r = dict(de00_mean=round(float(e.mean()), 3), de00_max=round(float(e.max()), 3))
        if name == 'colorchecker':
            r['worst'] = COLORCHECKER[int(e.argmax())][0]
            r['skin'] = round(float(e[:2].mean()), 3)
        else:
            r['cast_max'] = round(float(np.hypot(*(lp[:, 1:] - lt[:, 1:]).T).max()), 2)
        out[name] = r
    return out


def fit_lut(path, space='srgb', curve='auto', photo=None, iters=1500, log=print, hue=True, post=False, seed=None):
    """One turn of fitting. seed None: the classic start; a number: a random start (reproducible).
    With post, the result also carries 'after_curves': the version with colour after the curves, when it
    is better than the usual one on at least one measure."""
    import numpy as np
    rng = None if seed is None else np.random.default_rng(seed)
    lut = Lut(path)
    if lut.lo.tolist() == [0, 0, 0] and lut.hi.tolist() == [1, 1, 1]:
        y = grid(lut.n)
    else:
        y = grid(33)
    if space == 'adobe':
        t, keep = adobe_lut_on_srgb(lut, y)
        y, t = y[keep], t[keep]
    else:
        t = lut(y)
    log(f'{Path(path).name}: {lut.kind} LUT, {len(y)} colours, {space}'
        + (f' (ignored: {", ".join(sorted(set(lut.ignored)))})' if lut.ignored else ''))
    ty = t
    gy = greys()
    v = np.concatenate([to_index(y), to_index(gy)])
    t = np.concatenate([t, apply_lut(lut, gy, space)])
    weight = np.concatenate([np.ones(len(y)) / len(y), np.full(len(gy), GREY_WEIGHT / len(gy))])
    held = None
    n_fixed = len(v)
    if photo:
        img = photo_signal(photo)
        top = np.zeros(img.shape[:2], bool)
        top[: img.shape[0] // 2] = True
        pv, top = img.reshape(-1, 3), top.ravel()
        yb = neutral(pv)
        ok = (yb.max(1) < 0.98) & (yb.min(1) > 0.01)
        tb = apply_lut(lut, yb, space)
        n_photo = int((ok & top).sum())
        v = np.concatenate([v, pv[ok & top]])
        t = np.concatenate([t, tb[ok & top]])
        weight = np.concatenate([weight, np.full(n_photo, PHOTO_WEIGHT / max(n_photo, 1))])
        held = (yb[ok & ~top], tb[ok & ~top])
        log(f'  photo: {int((ok & top).sum())} pixels join the fit, {int((ok & ~top).sum())} are held out')

    def one(mode, post=False, photo_weight=PHOTO_WEIGHT, init=None):
        w = weight
        if photo_weight != PHOTO_WEIGHT:                 # the photo and the greys together (everyday content)
            w = np.asarray(weight, float).copy()
            w[n_fixed - len(gy):] *= photo_weight / PHOTO_WEIGHT
        try:
            look = Fitter(v, t, mode, weight=w, post=post).run(t, iters=iters, init=init, rng=None if init else rng,
                                                              lr=5e-3 if init is None else POST_WARM_LR)
        except Refused as exc:
            log(f'  {mode} + colour after the curves: {exc}')
            return None
        e = evaluate(look, y, ty)['de00_mean']
        h = evaluate(look, *held)['de00_mean'] if held else None
        ev = everyday(look, lut, space)
        log(f'  {mode + (f" + after (photo {photo_weight}{"" if init else ", from scratch"})" if post else ""):7s} ΔE00 {e:.3f}' + (f'  photo {h:.3f}' if h is not None else '')
            + f'  ColorChecker {ev["colorchecker"]["de00_mean"]:.3f}  greys {ev["greys"]["de00_mean"]:.3f}')
        return dict(e=e, h=h, cc=ev['colorchecker']['de00_mean'], grey=ev['greys']['de00_mean'], look=look)
    def better(new, old):
        """More levers (curves, colour after the curves) only when they clearly help (whole range,
        everyday colours or greys) and make nothing worse (photo, greys, ColorChecker, whole range)."""
        gain = (new['e'] < old['e'] * (1 - PAIR_GAIN) or new['cc'] < old['cc'] * (1 - PAIR_GAIN)
                or new['grey'] <= old['grey'] - GREY_GAIN)
        no_worse = ((old['h'] is None or new['h'] <= old['h'] + HELD_TOL) and new['grey'] <= old['grey'] + GREY_TOL
                    and new['cc'] <= old['cc'] + CC_TOL and new['e'] <= old['e'] * (1 + RANGE_TOL))
        return gain and no_worse
    if curve == 'auto':
        use = one('single')
        p = min((one(m) for m in ('rg-b', 'r-gb', 'rb-g')), key=lambda r: r['e'])
        if better(p, use):
            use = p
        t3 = one('rgb')
        if better(t3, use):
            use = t3
    elif curve == 'pair':
        use = min((one(m) for m in ('rg-b', 'r-gb', 'rb-g')), key=lambda r: r['e'])
    else:
        use = one(curve)

    def with_hue(lk):
        if hue:
            sectors = fit_hue(lk, v, t, weight, n_fixed, y, ty, held, lut, space, log=log)
            if sectors:
                lk = dict(lk, hue_sectors=sectors)
        return lk

    def summary(lk):
        ev = everyday(lk, lut, space)
        return dict(e=evaluate(lk, y, ty)['de00_mean'], h=evaluate(lk, *held)['de00_mean'] if held else None,
                    cc=ev['colorchecker']['de00_mean'], grey=ev['greys']['de00_mean'], look=lk)
    look = with_hue(use['look'])
    alt = None
    if post:                    # a colour matrix after the curves: a second version when it improves anything
        found = []
        starts = [(pw, use['look']) for pw in (POST_PHOTO_WEIGHTS if held else POST_PHOTO_WEIGHTS[:1])]
        for pw, init in starts + [(PHOTO_WEIGHT, None)]:      # from the fitted look, and once from scratch
            p = one(use['look']['curve_mode'], post=True, photo_weight=pw, init=init)
            if p:
                found.append(p)
        pool = [p for p in found if better(p, use)] or [p for p in found if changes(p, use)[0]]
        if pool:
            final, base = summary(with_hue(min(pool, key=lambda r: r['e'])['look'])), summary(look)
            gained, lost = changes(final, base)
            if gained:
                alt = dict(look=final['look'], recommended=better(final, base), better=gained, worse=lost)
        if alt:
            log(f'  colour after the curves: better {", ".join(alt["better"])}'
                + (f'; worse {", ".join(alt["worse"])}' if alt['worse'] else '')
                + ('  (recommended)' if alt['recommended'] else ''))
        else:
            log('  colour after the curves: no gain, not kept')

    def record(lk):
        scores = dict(fit=evaluate(lk, y, ty), unchanged=unchanged(y, ty), **everyday(lk, lut, space))
        if held:
            scores['photo'] = evaluate(lk, *held)
            scores['photo_unchanged'] = unchanged(*held)
        scores['fit_score'] = fit_score(lk, v, t, weight)
        ok, why = decide(scores)
        return dict(tool=f'GR4-LUTSmith {VERSION}', seed=seed,
                    lut=dict(file=Path(path).name, sha256=lut.sha256,
                             kind=lut.kind, title=lut.title, space=space),
                    matrix_q13=lk['matrix_q13'], curve_mode=lk['curve_mode'], curves=lk['curves'],
                    hue_sectors=lk.get('hue_sectors'), post_matrix_q9=lk.get('post_matrix_q9'),
                    scores=scores, decision=dict(build=ok, reason=why), fit=dict(iters=iters, history=lk['history']))
    out = record(look)
    if alt:
        out['after_curves'] = dict(record(alt['look']), recommended=alt['recommended'],
                                   compared=dict(better=alt['better'], worse=alt['worse']))
    return out


def fit_score(look, v, t, weight):
    """What the fit itself aims at: the weighted average ΔE00 over the LUT's colours, the greys and the part
    of the photo used for fitting (the other part of the photo stays a fair check)."""
    import numpy as np
    w = np.asarray(weight, float)
    return round(float((w * de00(lab(t), lab(np.clip(render(v, look), 0, 1)))).sum() / w.sum()), 4)


MEASURES = (('e', 'whole range'), ('cc', 'ColorChecker'), ('grey', 'greys'), ('h', 'photo'))


def changes(new, old):
    """Measures that got clearly better / worse: by more than RANGE_TOL (relative), CC_TOL, GREY_TOL or HELD_TOL."""
    tol = dict(e=old['e'] * RANGE_TOL, cc=CC_TOL, grey=GREY_TOL, h=HELD_TOL)
    better = [name for k, name in MEASURES if old.get(k) is not None and new[k] < old[k] - tol[k]]
    worse = [name for k, name in MEASURES if old.get(k) is not None and new[k] > old[k] + tol[k]]
    return better, worse


def decide(scores):
    """Build when the LUT's whole colour range fits, or when everyday colours do."""
    f = scores['fit']
    if f['verdict'] != 'poor':
        return True, f'fit {f["verdict"]}'
    cc, gr = scores['colorchecker'], scores['greys']
    p, p0 = scores.get('photo'), scores.get('photo_unchanged')
    photo_ok = p is None or (p['verdict'] != 'poor' and p['de00_mean'] <= p0['de00_mean'])
    no_cast = gr['de00_mean'] < EVERYDAY_GREY and gr['cast_max'] < EVERYDAY_CAST
    if cc['de00_mean'] < EVERYDAY_CC and no_cast and photo_ok:
        return True, (f'everyday colours fit (ColorChecker {cc["de00_mean"]}, greys {gr["de00_mean"]}, '
                      f'largest cast {gr["cast_max"]}); only extreme saturated colours deviate')
    if p and p['verdict'] == 'good' and p['de00_mean'] <= PHOTO_GAIN * p0['de00_mean'] and no_cast:
        return True, f'fit poor on extreme colours, but good on your photo ({p["de00_mean"]} vs {p0["de00_mean"]})'
    return False, (f'fit poor; ColorChecker {cc["de00_mean"]}, greys {gr["de00_mean"]} '
                   f'(largest cast {gr["cast_max"]})'
                   + (f'; photo {p["de00_mean"]} vs unchanged {p0["de00_mean"]}' if p else ''))


def describe(look, log=print):
    s = look['scores']
    f = s['fit']
    log(f'  result: {f["verdict"].upper()}  (ΔE00 {f["de00_mean"]}, p95 {f["de00_p95"]}; '
        f'without the look {s["unchanged"]["de00_mean"]})')
    if 'photo' in s:
        log(f'  on your photo: ΔE00 {s["photo"]["de00_mean"]} (without the look {s["photo_unchanged"]["de00_mean"]})')
    worst = sorted(((v, k) for k, v in f['hue'].items() if v is not None), reverse=True)[:3]
    log('  hardest colours: ' + ', '.join(f'{k} {v}' for v, k in worst))
    cc, gr = s.get('colorchecker'), s.get('greys')
    if cc and gr:
        log(f'  everyday colours: ColorChecker ΔE00 {cc["de00_mean"]} (skin {cc["skin"]}, worst {cc["worst"]}); '
            f'greys {gr["de00_mean"]} (largest cast {gr["cast_max"]})')
    hs = look.get('hue_sectors')
    log(f'  for: {SLOT_NAMES.get(look.get("slot") or DEFAULT_SLOT, "?")}'
        + (f' (shown as "{look["name"]}")' if look.get('name') else '')
        + (f', own icon from {look["icon"].get("source", "a picture")}' if look.get('icon') else '')
        + f'; curves: {look["curve_mode"]}; '
        f'hue sectors: {"yes" if hs else "no"}; '
        f'colour after the curves: {"yes" if look.get("post_matrix_q9") else "no"}; decision: {look["decision"]["reason"]}')


TURNS_DIR = 'turns'           # every kept result gets a folder here when there is more than one


def measures(rec):
    sc = rec['scores']
    return dict(e=sc['fit']['de00_mean'], cc=sc['colorchecker']['de00_mean'], grey=sc['greys']['de00_mean'],
                h=sc['photo']['de00_mean'] if 'photo' in sc else None)


def keep(kept, new):
    """The list of results worth keeping. A new result joins unless a kept one is at least as good on every
    measure; kept results that the new one beats on every measure leave the list.
    Returns (list, joined, results that left)."""
    m = measures(new)
    if any(not changes(m, measures(k))[0] for k in kept):
        return kept, False, []
    stay = [k for k in kept if changes(measures(k), m)[0]]
    return stay + [new], True, [k for k in kept if not any(k is x for x in stay)]


def result_name(rec):
    return f'turn-{rec["turn"]}' + ('-after-curves' if rec.get('post_matrix_q9') else '')


def recommended(kept):
    """The kept result the fit itself scores best (see fit_score)."""
    return min(kept, key=lambda r: r['scores']['fit_score'])


def show_kept(kept, log=print):
    best = recommended(kept)
    log(f'\n  kept results ({len(kept)}), ΔE00, lower is closer to the LUT:')
    log(f'    {"result":24s} {"whole range":>11s} {"ColorChecker":>12s} {"greys":>6s} {"photo":>6s} {"fit score":>9s}')
    for r in sorted(kept, key=lambda r: r['scores']['fit_score']):
        m = measures(r)
        log(f'    {result_name(r):24s} {m["e"]:11.2f} {m["cc"]:12.2f} {m["grey"]:6.2f} '
            + (f'{m["h"]:6.2f}' if m['h'] is not None else f'{"-":>6s}')
            + f' {r["scores"]["fit_score"]:9.3f}' + ('   recommended' if r is best else ''))
    if len(kept) > 1:
        log('  each kept result is better than every other one on at least one measure; the recommended one has\n'
            '  the best fit score. Preview them on your own photo and pick the one you like.')


def run_turns(a, n, state, post, log=print):
    """Run n more turns (post: with colour after the curves) and update state (kept list, history, seeds)."""
    import secrets
    for _ in range(n):
        k = len(state['history_turns']) + 1
        if a.seed is not None:
            seed = a.seed + k - 1
        else:
            seed = None if k == 1 else secrets.randbelow(2 ** 31)
        log(f'\nturn {k} ({"classic start" if seed is None else f"random start, seed {seed}"}'
            + (', with colour after the curves)' if post else ')'))
        res = fit_lut(a.lut, a.space, a.curve, a.photo, a.iters, log=log, hue=not a.no_hue, post=post, seed=seed)
        alt = res.pop('after_curves', None)
        state['history_turns'].append(dict(seed=seed, after_curves=post))
        for rec in (res, alt):
            if rec is None:
                continue
            for x in ('recommended', 'compared'):
                rec.pop(x, None)
            rec['turn'] = k
            rec['slot'] = a.slot
            if a.name:
                rec['name'] = a.name
                rec['camera_language'] = a.camera_language
                if state.get('name_accepted'):
                    rec['name_accepted'] = True
            if state.get('icon'):
                rec['icon'] = state['icon']
            state['kept'], joined, left = keep(state['kept'], rec)
            state['history'].append(dict(result=result_name(rec), seed=seed, kept_when_found=joined,
                                         fit_score=rec['scores']['fit_score'], **measures(rec)))
            log(f'  {result_name(rec)}: ' + ('kept' if joined else 'not kept (an earlier result is at least as good '
                                                                   'on every measure)'))
            for x in left:
                log(f'  {result_name(x)} leaves the list ({result_name(rec)} is better on every measure)')


def ask_more_turns(post):
    """Ask for more turns when someone is at the terminal. Returns (number, with colour after the curves);
    0 means stop. A plain number runs turns as the run started (post); 'a' adds turns with colour after the curves,
    'b' turns without it (before the curves only)."""
    if not sys.stdin.isatty():
        return 0, post
    while True:
        try:
            ans = input('\nMore turns? Type a number (e.g. 2), add "a" for turns with colour after the curves (2a) '
                        'or "b" for turns without it (2b); or press Enter to keep these results: ').strip().lower()
        except EOFError:
            return 0, post
        if not ans:
            return 0, post
        kind = ans[-1] if ans[-1] in 'ab' else ''
        num = ans[:-1].strip() if kind else ans
        if num.isdigit():
            return int(num), (post if not kind else kind == 'a')
        print('please type a whole number, optionally followed by a or b, or press Enter')


def check_look(look):
    """Refuse a look whose values the camera cannot take (a look.json may come from someone else)."""
    matrix_hex(look)
    if look.get('curves'):
        distinct_curves(look['curves'])
        struct.pack('<768H', *[x for c in look['curves'] for x in c])
    hue_changes(look)
    if look.get('post_matrix_q9'):
        post_check(look['post_matrix_q9'])
    slot = look_slot(look)
    if look.get('name') is not None:
        if not isinstance(look['name'], str):
            raise TypeError('the name must be text')
        check_name(look['name'], slot)
    if look.get('icon'):
        rec = look['icon']
        if len(base64.b64decode(rec['rgba'], validate=True)) != int(rec['w']) * int(rec['h']) * 4:
            raise ValueError('the icon has the wrong size')
        icon_changes(look, slot)


def load_look(path):
    try:
        look = json.loads(Path(path).read_text(encoding='utf-8'))
        if isinstance(look, dict) and 'look' in look and 'matrix_q13' not in look:   # files of earlier tools
            look = dict(look['look'], scores=look.get('scores'))
        if not isinstance(look, dict) or 'matrix_q13' not in look:
            raise Refused(f'{path}: not a look file')
        check_look(look)
    except Refused as exc:
        raise Refused(str(exc) if str(exc).startswith(str(path)) else f'{path}: {exc}')
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, struct.error, binascii.Error) as exc:
        raise Refused(f'{path}: not a valid look file ({exc})')
    return look


def adapt_look(look, slot=None, name=None, no_name=False, icon=None, no_icon=False):
    """A look (e.g. one someone shared) with your own slot, name and icon. The colour data stays as it is."""
    look = dict(look)
    if (look.get('lut') or {}).get('file'):                  # earlier versions kept the maker's folders
        look['lut'] = dict(look['lut'], file=Path(look['lut']['file']).name)
    if slot:
        look['slot'] = slot
    slot = look_slot(look)
    if no_name or name is not None:
        for k in ('name', 'camera_language', 'name_accepted'):
            look.pop(k, None)
    if name:
        look['name'] = name
    if no_icon:
        look.pop('icon', None)
    elif icon:
        look['icon'] = icon_record(icon, slot)
    if look.get('icon') and slot not in SLOT_DATA['icons']['slots']:
        raise Refused(f'{SLOT_NAMES[slot]} cannot take an own icon (its icon is shared with another look); '
                      'add --no-icon')
    return look


def find_lut(look, near):
    """The look's LUT on this computer, if it is there (same file name and contents): next to look.json, in the
    current folder, or where earlier versions recorded it."""
    lut = look.get('lut') or {}
    name = lut.get('file')
    if not name:
        return None
    for cand in (Path(near).parent / Path(name).name, Path(Path(name).name), Path(name)):
        try:
            if cand.is_file() and (not lut.get('sha256') or Lut(cand).sha256 == lut['sha256']):
                return cand
        except (Refused, OSError, ValueError):
            pass                                     # a different or unreadable file of that name
    return None


# ------------------------------------------------------------------ preview
def preview(look, dng, out, lut_path=None, space='srgb'):
    import numpy as np
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise Refused('preview needs pillow: python3 -m pip install rawpy pillow')
    v = photo_signal(dng, max_side=1200)
    flat = v.reshape(-1, 3)
    panels = [neutral(flat)]
    labels = ['camera, no look']
    if lut_path and Path(lut_path).exists():
        panels.append(apply_lut(Lut(lut_path), panels[0], space))
        labels.append('LUT')
    panels.append(render(flat, look))
    labels.append('camera with the look')
    ims = [Image.fromarray((np.clip(p.reshape(v.shape), 0, 1) * 255 + 0.5).astype(np.uint8)) for p in panels]
    w, h = ims[0].size
    sheet = Image.new('RGB', (len(ims) * w + 8 * (len(ims) - 1), h + 28), (24, 24, 24))
    d = ImageDraw.Draw(sheet)
    for k, (im, text) in enumerate(zip(ims, labels)):
        sheet.paste(im, (k * (w + 8), 28))
        d.text((k * (w + 8) + 6, 8), text, fill=(230, 230, 230))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=92)
    return out


# ------------------------------------------------------------------ main
def need_numpy():
    try:
        import numpy  # noqa: F401
    except ImportError:
        raise Refused('this command needs numpy: python3 -m pip install numpy')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='gr4_lutsmith.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--version', action='version', version=VERSION)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def lut_args(p):
        p.add_argument('lut', type=Path, help='.cube file (3D or 1D)')
        p.add_argument('--space', choices=('srgb', 'adobe'), default='srgb',
                       help='colour space the LUT was made for (default srgb)')
        p.add_argument('--photo', type=Path, help='a DNG from your GR IV: judges the fit on real content (needs rawpy)')
        p.add_argument('--curve', choices=('auto', 'single', 'pair', 'rgb'), default='auto', help=argparse.SUPPRESS)
        p.add_argument('--no-hue', action='store_true', help='do not use hue sectors (colour changes per hue)')
        p.add_argument('--slot', choices=tuple(SLOT_NAMES), default=DEFAULT_SLOT,
                       help='which Image Control look to replace with yours (default high-contrast)')
        p.add_argument('--camera-language', choices=CAMERA_LANGUAGES, default=system_language(),
                       help='the language your camera is set to (default: this computer\'s language, else English); '
                            'used to check that --name shows')
        p.add_argument('--accept-name', action='store_true', help='use --name even if it may not show with the '
                                                                  'camera language')
        p.add_argument('--name', help='show the slot under this name; plain ASCII shows in every camera language '
                                     '(see `slots` for the longest name each slot takes)')
        p.add_argument('--icon', type=Path, help='a picture for the slot\'s menu icon (needs pillow); the centre of a '
                                                'non-square picture is used')
        p.add_argument('--turns', type=int, default=1,
                       help='how many fits to run; each extra turn starts from a new random point (default 1)')
        p.add_argument('--seed', type=int, help='run turns from this seed (repeats a turn shown earlier)')
        p.add_argument('--after-curves', action='store_true',
                       help='also try a colour mix after the tone curves; keeps a second version when it helps')
        p.add_argument('--iters', type=int, default=1500, help=argparse.SUPPRESS)
    m = sub.add_parser('make', help='LUT + official firmware -> firmware with the look')
    lut_args(m)
    m.add_argument('input', type=Path, help='official RICOH fwdc248b.bin (GR IV 1.11)')
    m.add_argument('--force', action='store_true', help='build even if the fit is poor')
    m.add_argument('-o', '--output-dir', type=Path)
    f = sub.add_parser('fit', help='fit a LUT only (writes look.json)')
    lut_args(f)
    f.add_argument('-o', '--output-dir', type=Path)
    b = sub.add_parser('build', help='look.json file(s) + official firmware -> firmware with the look(s)')
    b.add_argument('looks', type=Path, nargs='+', metavar='look.json',
                   help='one look per slot (each look.json says its slot)')
    b.add_argument('input', type=Path, help='official RICOH fwdc248b.bin (GR IV 1.11)')
    b.add_argument('--slot', choices=tuple(SLOT_NAMES), help='put the look on this slot instead of the one its '
                                                              'look.json says (one look only)')
    b.add_argument('--name', help='show the slot under this name instead of the look.json\'s (one look only)')
    b.add_argument('--no-name', action='store_true', help='keep the slot\'s own name (one look only)')
    b.add_argument('--icon', type=Path, help='use this picture as the slot\'s icon (one look only; needs pillow)')
    b.add_argument('--no-icon', action='store_true', help='keep the slot\'s own icon (one look only)')
    b.add_argument('--camera-language', choices=CAMERA_LANGUAGES,
                   help='the language your camera is set to (default: the one saved with the look, else this '
                        'computer\'s)')
    b.add_argument('--accept-name', action='store_true', help='use the looks\' names even if they may not show '
                                                              'with the camera language')
    b.add_argument('-o', '--output-dir', type=Path)
    p = sub.add_parser('preview', help='render a DNG with the look (needs rawpy, pillow)')
    p.add_argument('look', type=Path)
    p.add_argument('photo', type=Path)
    p.add_argument('--lut', type=Path, help='the .cube file, to show it next to the camera (found automatically '
                                            'when it is next to look.json)')
    p.add_argument('-o', '--output', type=Path)
    sub.add_parser('slots', help='list the Image Control looks yours can replace (--slot)')
    c = sub.add_parser('check', help='identify a fwdc248b.bin and validate its container')
    c.add_argument('input', type=Path)
    s = sub.add_parser('selftest', help='build the bundled example and compare with the verified result')
    s.add_argument('input', type=Path)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)
    if sys.version_info < (3, 8):
        print('Python 3.8 or newer is required', file=sys.stderr)
        return 2
    try:
        if a.cmd == 'check':
            return identify(a.input.read_bytes())
        if a.cmd == 'slots':
            list_slots()
            return 0
        print(f'GR4-LUTSmith {VERSION}')
        if a.cmd == 'selftest':
            look = load_look(Path(__file__).resolve().parent / SELFTEST_LOOK)
            out = make_firmware(a.input.read_bytes(), look)
            ok = sha256(out) == SELFTEST_SHA256
            print(f'selftest: {"PASSED" if ok else "FAILED"} (SHA-256 {sha256(out)})')
            return 0 if ok else 1
        need_numpy()
        if a.cmd == 'preview':
            look = load_look(a.look)
            src = a.lut or find_lut(look, a.look)
            if not src:
                print('LUT not found next to the look; add --lut my_look.cube to compare with it')
            out = preview(look, a.photo, a.output or a.look.with_name('preview.jpg'), src,
                          (look.get('lut') or {}).get('space', 'srgb'))
            print(f'wrote {out}')
            return 0
        if a.cmd in ('make', 'fit'):
            out_dir = a.output_dir or Path('output') / a.lut.stem
            if a.cmd == 'make':
                source = a.input.read_bytes()
                if sha256(source) != OFFICIAL_SHA256:
                    make_firmware(source, dict(matrix_q13=[[8192, 0, 0], [0, 8192, 0], [0, 0, 8192]]))
                if (out_dir / 'fwdc248b.bin').exists() or (out_dir / TURNS_DIR).exists():
                    raise Refused(f'{out_dir} already has results; choose another output directory')
            if a.turns < 1:
                raise Refused('--turns must be at least 1')
            print(f'slot: {slot_line(a.slot)}' + (f', shown as "{a.name}"' if a.name else ''))
            name_accepted = settle_name(a.name, a.slot, a.camera_language, a.accept_name) if a.name else False
            icon = icon_record(a.icon, a.slot) if a.icon else None
            if icon:
                print(f'icon: {a.icon.name} -> {icon["w"]}x{icon["h"]}')
            state = dict(kept=[], history=[], history_turns=[], icon=icon, name_accepted=name_accepted)
            n, post = a.turns, a.after_curves
            while n:
                run_turns(a, n, state, post)
                show_kept(state['kept'])
                n, post = ask_more_turns(a.after_curves)
            kept, best = state['kept'], recommended(state['kept'])
            print(f'\nrecommended: {result_name(best)}')
            describe(best)
            places = [(best, out_dir)]
            if len(kept) > 1:
                places += [(r, out_dir / TURNS_DIR / result_name(r)) for r in kept]
            for r, d in places:
                d.mkdir(parents=True, exist_ok=True)
                (d / 'look.json').write_text(json.dumps(r, indent=1) + '\n')
                if r.get('icon'):
                    icon_image(r['icon']).save(d / 'icon.png')
            if len(state['history']) > 1:
                (out_dir / 'turns.json').write_text(json.dumps(dict(
                    turns=[dict(turn=i + 1, **t) for i, t in enumerate(state['history_turns'])],
                    results=state['history'], kept=[result_name(r) for r in kept],
                    recommended=result_name(best)), indent=1) + '\n')
            print(f'wrote {out_dir / "look.json"}' + (f' (and {len(kept)} kept results in {out_dir / TURNS_DIR})'
                                                     if len(kept) > 1 else ''))
            if a.cmd == 'fit':
                return 0
            build = [(r, d) for r, d in places if r['decision']['build'] or a.force]
            if not build:
                raise Refused('the LUT cannot be reproduced well by the camera (see above); '
                              'use --force to build anyway')
            for r, d in places:
                if not (r['decision']['build'] or a.force):
                    print(f'not built: {d} (the camera cannot reproduce this result well; --force builds it)')
            made = {}
            for r, d in build:
                if id(r) not in made:                          # the recommended result is also in turns/
                    made[id(r)] = make_firmware(source, r)
                data = made[id(r)]
                target = write_atomic(data, d)
                print(f'wrote {target}  (SHA-256 {sha256(data)})')
            print(('Copy one of these files' if len(build) > 1 else 'Copy this file')
                  + ' to the root of the SD card and run the camera\'s firmware update.\n'
                  + f'Then choose {a.name or SLOT_NAMES[a.slot]} in Image Control: your look is there.')
            return 0
        if a.cmd == 'build':
            looks = [load_look(x) for x in a.looks]
            adapt = a.slot or a.name is not None or a.no_name or a.icon or a.no_icon
            if adapt and len(looks) > 1:
                raise Refused('--slot, --name and --icon change one look; build each look alone with them first '
                              '(each gets its own look.json), then build those together')
            if a.name is not None and a.no_name or a.icon and a.no_icon:
                raise Refused('choose either --name or --no-name, and either --icon or --no-icon')
            if adapt:
                looks = [adapt_look(looks[0], a.slot, a.name, a.no_name, a.icon, a.no_icon)]
            for x, lk in zip(a.looks, looks):
                print(f'{x}: {slot_line(look_slot(lk))}' + (f', shown as "{lk["name"]}"' if lk.get('name') else '')
                      + (', own icon' if lk.get('icon') else ''))
                try:
                    describe(lk)
                except (KeyError, TypeError, AttributeError):
                    pass                                     # a look file without its fit results
                if lk.get('name'):
                    lang = a.camera_language or lk.get('camera_language') or system_language()
                    saved = lk.get('name_accepted') and lang == lk.get('camera_language')
                    yes = settle_name(lk['name'], look_slot(lk), lang, a.accept_name or saved,
                                      log=lambda x: print('  ' + x))
                    if adapt:                                # saved with the adapted look
                        lk['camera_language'] = lang
                        lk.pop('name_accepted', None)
                        if yes:
                            lk['name_accepted'] = True
            title = (looks[0].get('lut') or {}).get('title') or a.looks[0].stem
            out_dir = a.output_dir or (Path('output') / title if adapt else a.looks[0].parent)
            if adapt:
                if (out_dir / 'look.json').resolve() == a.looks[0].resolve():
                    raise Refused('that would replace the look.json you started from; choose another folder with -o')
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / 'look.json').write_text(json.dumps(looks[0], indent=1) + '\n')
                if looks[0].get('icon'):
                    icon_image(looks[0]['icon']).save(out_dir / 'icon.png')
                print(f'wrote {out_dir / "look.json"} (the look with your slot, name and icon)')
            data = make_firmware(a.input.read_bytes(), looks)
            target = write_atomic(data, out_dir)
            print(f'wrote {target}  (SHA-256 {sha256(data)})')
            shown = {look_slot(lk): lk.get('name') or SLOT_NAMES[look_slot(lk)] for lk in looks}
            names = [shown[n] for n in SLOT_ORDER if n in shown]
            print('Copy this file to the root of the SD card and run the camera\'s firmware update.\n'
                  + ('Then choose ' + names[0] + ' in Image Control: your look is there.' if len(names) == 1 else
                     'Your looks are on: ' + ', '.join(names) + '.'))
            return 0
    except Refused as exc:
        print(f'refused: {exc}', file=sys.stderr)
        if a.cmd in ('make', 'build', 'selftest'):
            print('nothing was written.', file=sys.stderr)
        return 1
    except OSError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
