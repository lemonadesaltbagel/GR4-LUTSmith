# Contributing

Bug reports and fixes are welcome. This is what the project asks before a change is merged.

## Setup

[uv](https://docs.astral.sh/uv/) and Python 3.9 or newer (3.12 is what the app is built with):

    uv sync --extra app

That installs numpy, Qt for the window, and the photo tools. The command line alone needs only numpy:
`uv run gr4_lutsmith.py --help` should print its usage.

## Checks

Every check is a plain script: it prints its checks and exits non-zero when one fails.

    uv run python tests/test_app.py     # the window: pages in every language, names, shared looks, a short fit
    for t in tests/test_*.py; do uv run python "$t"; done   # everything, including the engine's own checks

With the official firmware file on disk, test_app.py also verifies the self-test against it:

    GR4_FIRMWARE=/path/to/fwdc248b.bin uv run python tests/test_app.py

Text changes are checked against all 21 languages:

    uv run python tools/i18n_check.py   # every text translated, placeholders and tags intact

## Development scripts

These drive the window offscreen and are how a change is eyeballed end to end. They are not part of the app.

    tests/drive_app.py <folder>         # a screenshot of each step (no camera file needed)
    tests/flow.py <folder>              # the whole walk with short fits; needs GR4_FIRMWARE and GR4_LUT, takes GR4_DNG
    tests/identity.py <folder>          # the app and the command line must build byte-identical firmware; needs all three

The environment variables: `GR4_FIRMWARE` is the official fwdc248b.bin, `GR4_LUT` a .cube file, `GR4_DNG` a photo from a
GR IV. identity.py runs a full-length fit and takes a few minutes.

## Translations

The app's text lives in `lutsmith_app/assets/i18n/<language>.json`; the English text is the key.
`tools/i18n_check.py --missing` prints the texts any language still misses.

- `{placeholders}` and HTML tags must survive: the check compares them with the English.
- Product words stay as they are in every language: ΔE00, LUT, DNG, sRGB, Adobe RGB, ColorChecker, SHA-256,
  GR4-LUTSmith, RICOH, GR IV, .cube, .json, look.json, p95 (the check enforces this list).
- The app's languages are the camera's menu languages (21 of them, `CAMERA_LANGUAGES` in gr4_lutsmith.py).

## Packaging

    uv run python packaging/build.py                 # this Mac's chip, or Windows x64
    uv run python packaging/build.py --arch x86_64   # Intel Mac build on Apple Silicon (runs under Rosetta)

Writes `dist/GR4-LUTSmith-<version>-<platform>.zip` with the licences inside, plus its `.sha256`, and smoke-tests the
packaged app before zipping it.

## Version

The version is written twice — `version` in pyproject.toml and `VERSION` in gr4_lutsmith.py — and release commits are
named after it. Keep the two equal when bumping.
