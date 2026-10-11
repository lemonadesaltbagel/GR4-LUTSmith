# GR4-LUTSmith

**Your own colour look, from a `.cube` LUT, inside your RICOH GR IV.**

GR4-LUTSmith takes a colour-grading LUT (`.cube`) and builds a GR IV firmware
that carries its look as an **Image Control** look. You can shoot with it in
stills, in movie mode and in the viewfinder, and use it in **RAW Development**.
Everything else on your camera stays exactly as it was, and you can go back to
the official firmware at any time.

This repository contains no RICOH firmware. You download the official firmware
from RICOH yourself, and the included tool turns it into the version with your
look, on your own computer.

> **Unofficial, at your own risk.** Not made or endorsed by RICOH IMAGING.
> Installing modified firmware may void your warranty. Please read
> [Before you start](#before-you-start) and [Risks](#risks) first.

## Two ways to use it

- **The app** (macOS and Windows): download, unzip, open. Every step is on screen, in the camera's 21 languages.
  See [The app](#the-app).
- **The command line** (macOS, Windows, Linux): the same tool for the terminal, with [uv](https://docs.astral.sh/uv/).
  See [Install](#install).

Both make exactly the same firmware from the same LUT and choices.

## How it works

The GR IV does not run LUTs directly. Its looks are built from a few native
controls: a colour mix, tone curves, contrast settings and **hue sectors**
(separate colour changes for 12 ranges of hue). GR4-LUTSmith
**fits** your LUT to those controls, measures how close the camera can get, and
only then writes the firmware.

- **Tonal and overall colour looks** fit very well. That covers film-style
  curves, contrast, faded blacks, warm or cool casts, and overall saturation
  changes.
- **Looks that move single hues separately** (teal-orange, film simulations
  that shift greens or skies, …) are fitted with the **hue sectors**. They get
  much closer than with the colour mix alone. Changes that depend strongly on
  how saturated a colour is (e.g. only the most vivid reds) remain approximate,
  and the tool tells you when that is the case.
- **Log and camera-specific conversion LUTs** (S-Log, V-Log, …) are not
  supported. Use a "creative" LUT made for normal (Rec.709 / sRGB) pictures.

The fit is judged with **CIEDE2000**, the standard measure of how different
two colours look. As a guide, **below 1 is hard to see side by side** and
around 2 is a small visible difference.

| Result | Meaning |
| --- | --- |
| **good** | average difference below 1, and 95 % of colours below 2: looks the same |
| **approximate** | average below 2: close, with visible differences in some colours |
| **poor** | the camera cannot reproduce the LUT's whole colour range; firmware is still written when **everyday colours** fit (see [When a LUT is "poor"](#when-a-lut-is-poor)) |

Every LUT is measured three ways: across its whole colour range, on the
24 everyday colours of a **ColorChecker** chart (skin, sky, foliage, …), and on
**neutral greys** (where any colour cast is most visible). With `--photo`, also
on your own photo.

## What you get

After installing, the Image Control list ends like this:

| Shown as | Look | Still | Movie |
| --- | --- | :---: | :---: |
| HDR Tone | (your existing colour look) | ✓ | |
| Standard | Monochrome Standard | ✓ | ✓ |
| Solid | Monochrome Solid | ✓ | ✓ |
| Soft | Monochrome Soft | ✓ | |
| **High Contrast** | **your LUT's look, in colour** | ✓ | ✓ |
| Grainy | Monochrome Grainy | ✓ | |
| HDR Tone | Monochrome HDR *(experimental)* | ✓ | |

- Your look uses the name and icon of **High Contrast** (the fourth entry after
  the first "HDR Tone"). You can choose **Cinema (Yellow)** or **Cinema (Green)**
  instead, or use several of them: see [Choosing the slot](#choosing-the-slot).
  With `--name` the slot shows your own name instead: see
  [Naming the slot](#naming-the-slot); with `--icon`, your own icon: see
  [Your own icon](#your-own-icon).
- It opens with **every slider at 0**, sharpness at its standard value and
  **grain off**. Your LUT's look is built in at those settings; the sliders then
  work as usual, starting from your look.
- The five monochrome looks of the GR IV Monochrome are available too, as in
  GR4-MonoUnlock.
- All your existing looks are unchanged.

## The app

### Download

From the [latest release](../../releases/latest), download the file for your computer:

| Computer | File |
| --- | --- |
| Mac with Apple silicon (M1 or later) | `GR4-LUTSmith-<version>-macOS-AppleSilicon.zip` |
| Mac with an Intel processor | `GR4-LUTSmith-<version>-macOS-Intel.zip` |
| Windows 10 or 11 (64-bit) | `GR4-LUTSmith-<version>-Windows-x64.zip` |

macOS 12 (Monterey) or later is needed. Not sure which Mac you have? Apple menu → **About This Mac**: "Chip
Apple M…" is Apple silicon, "Processor Intel" is Intel.

Each zip holds the app and a `Licences` folder (the licences of the parts it is built from).

### Check it came from this project (optional)

Every zip is built by this repository's own build on GitHub, which signs a record of where it came from: the
repository, the build and the exact version of the code. You can check that your download is that file and was
not changed or replaced, with the [GitHub CLI](https://cli.github.com) (signed in with `gh auth login`):

```sh
gh attestation verify GR4-LUTSmith-2.0.2-macOS-AppleSilicon.zip --repo lemonadesaltbagel/GR4-LUTSmith
```

(use the name of the zip you downloaded; on Windows, run it in PowerShell from your Downloads folder). It ends with
"Verification succeeded!" and names the build that made the file. Any other result means: do not open it, and
download it again from the release page. The repository's **Attestations** page lists every signed file.

A quicker check that the download is complete: the release lists every file's SHA-256 in `SHA256SUMS`.

- macOS (Terminal): `shasum -a 256 ~/Downloads/GR4-LUTSmith-*.zip`
- Windows (PowerShell): `Get-FileHash $HOME\Downloads\GR4-LUTSmith-*.zip -Algorithm SHA256`

This only shows the file arrived intact, not who made it: `SHA256SUMS` sits on the same page as the zips.

You can also skip the download entirely: run the app from the source code (see [Files](#files)), or make the
firmware with the [command line](#install). The app and the command line build exactly the same `fwdc248b.bin`,
and `uv run gr4_lutsmith.py check fwdc248b.bin` shows what any firmware file contains.

### Open it the first time

The app is free and not signed with a paid Apple or Microsoft certificate, so your computer asks once whether
you trust it.

**macOS**

1. Unzip the file and move **GR4-LUTSmith** to your Applications folder.
2. Open it. macOS says it cannot check the app for malicious software; choose **Done** (macOS 15) or **Cancel**.
3. Open **System Settings → Privacy & Security**, scroll down to the message about GR4-LUTSmith and choose
   **Open Anyway**, then confirm with your password.
   On macOS 12 to 14 you can instead Control-click the app, choose **Open**, then **Open** again.

From then on it opens normally.

**Windows**

1. Right-click the zip → **Extract All…**, and open the extracted **GR4-LUTSmith** folder (do not run it from
   inside the zip).
2. Open **GR4-LUTSmith.exe**. If Windows shows "Windows protected your PC", choose **More info → Run anyway**.

Keep the whole folder together: the app needs the `_internal` folder next to it.

### Use it

The app walks you through the same steps as this guide: your camera's firmware file, the LUT (and optionally a
DNG), the slot with its name and icon, the fit, and the build. It saves `fwdc248b.bin` where you choose; then
follow [Copy it to the SD card](#4-copy-it-to-the-sd-card) and [Update the camera](#5-update-the-camera).

- **Language:** the app follows your computer's language; change it at the top of the window. It is also the
  camera language your slot names are checked against (see [Naming the slot](#naming-the-slot)).
- **Appearance:** System, Day or Night, at the top of the window.
- **Check a firmware file** (left side) tells you whether a `fwdc248b.bin` is RICOH's official file, made by
  GR4-LUTSmith (and with which looks), or something else.
- Looks someone shared (`.json`) work in the app too: choose "A shared look" on the first page.

### If something goes wrong

- **macOS says the app is damaged**: download it again (the file was cut short), and check it as in
  [Check it came from this project](#check-it-came-from-this-project-optional).
- **The app does not start on Windows**: make sure you extracted the whole zip and run `GR4-LUTSmith.exe` from
  the extracted folder.
- **The firmware file is refused**: the app only accepts RICOH's unmodified GR IV 1.11 `fwdc248b.bin`. A file
  GR4-LUTSmith already changed cannot be changed again; start from the unmodified file.

## Before you start

- **Camera:** RICOH GR IV (the standard colour model). Other models are not
  supported.
- **Your camera must already be on firmware 1.11.** Check the version in the
  camera's setup menu.
  - Older version: first update to 1.11 the normal way, with RICOH's official file.
  - Newer version: **do not use this tool.** It only supports 1.11.
- **A fully charged battery.** A firmware update must never lose power.
- **An SD card** you can format.
- **A computer with [uv](https://docs.astral.sh/uv/)** (it installs Python
  and everything else the tool needs). If you prefer plain Python instead, see
  [Without uv](#without-uv).
- **Your LUT** as a `.cube` file (3D or 1D), and whether it was made for
  **sRGB** (most LUTs) or **Adobe RGB**.
- **Recommended:** a DNG photo taken with your GR IV. The tool uses it to check
  the look on real content and to make a preview.
- Note down any custom settings (C1, C2, …) you would not want to set up again.

## Install

### 1. Download the official firmware

From RICOH IMAGING's official GR IV support page, download **firmware version
1.11**. Unpack it if needed until you have the file **`fwdc248b.bin`**.

### 2. Make your firmware

Open a terminal in this folder and run (use your own paths):

```sh
uv sync --extra photo
uv run gr4_lutsmith.py make my_look.cube ~/Downloads/fwdc248b.bin --photo R0001234.DNG
```

- Add `--space adobe` if the LUT was made for Adobe RGB.
- `--photo` is optional, but recommended.
- `--slot vivid`, `--slot cinema-yellow`, … (optional) puts your look in place
  of another look instead of High Contrast. `uv run gr4_lutsmith.py slots`
  lists them all: see [Choosing the slot](#choosing-the-slot).
- `--name "My Look"` (optional) shows the slot under your own name: see
  [Naming the slot](#naming-the-slot).
- `--icon my_icon.png` (optional) gives the slot your own menu icon: see
  [Your own icon](#your-own-icon).
- `--turns 3` (optional) runs three fits and keeps the best ones: see
  [More turns](#more-turns).
- `--after-curves` is optional: see [Colour after the curves](#colour-after-the-curves).

The tool:

1. makes sure you gave it the genuine official 1.11 file;
2. fits the LUT (this takes a few minutes, plus a minute or two for the hue
   sectors) and shows how well it fits;
3. builds the firmware and checks it.

A good run ends like this:

```
  result: GOOD  (ΔE00 0.6, p95 1.42; without the look 2.09)
  on your photo: ΔE00 1.22 (without the look 2.55)
  ...
wrote output/my_look/fwdc248b.bin  (SHA-256 …)
Copy this file to the root of the SD card and run the camera's firmware update.
Then choose High Contrast in Image Control: your look is there.
```

The tool also says at the start which slot your look goes to, for example
`slot: High Contrast (replaces the hidden monochrome High Contrast look)`.

The output folder `output/my_look/` also contains `look.json` (the fitted
look) and can take a preview (step 3). You can share `look.json` with other
GR IV owners: they build the same look without fitting it again (see
[Using a look someone shared](#using-a-look-someone-shared)).

If the LUT cannot be reproduced well, or the firmware file is not the official
1.11, the tool prints `refused: …` and writes **no firmware**.

### 3. (Optional) Preview it

```sh
uv run gr4_lutsmith.py preview output/my_look/look.json R0001234.DNG
```

This writes `output/my_look/preview.jpg` with three panels: the camera
without the look, the LUT itself, and the camera **with** the look. The LUT
panel needs the `.cube` file: the tool finds it next to `look.json` or in the
current folder, or give it with `--lut my_look.cube`; without it, the preview
shows the other two panels. Colour and
tone are what to compare. Sharpening and noise are not simulated.

### 4. Copy it to the SD card

1. **Format the SD card in the camera.**
2. Copy **`output/my_look/fwdc248b.bin`** to the **top level** of the card:
   not into a folder, and keep the name exactly `fwdc248b.bin`.

### 5. Update the camera

1. Insert the fully charged battery and the SD card.
2. Start the firmware update from the camera's setup menu, as described in
   RICOH's official GR IV firmware update instructions.
3. **The camera shows an update from version 1.11 to version 1.11.** This is
   expected: the modified firmware keeps the version number. Confirm the update.
4. **Do not turn the camera off, open the battery or card door, or remove the
   card** until the update has finished and the camera has restarted.

### 6. Shoot

- Open **Image Control** and choose **High Contrast** (after the first
  "HDR Tone"). It is in colour now. If you chose another slot with `--slot`,
  choose that entry instead.
- The firmware version still shows **1.11**. That is normal.
- Delete `fwdc248b.bin` from the card (or format the card) so the update is not
  started again by accident.

To change to another LUT later, make a new firmware from the **official**
file and install it the same way.

## Going back to the official firmware

Repeat steps 4–5 with the **official** `fwdc248b.bin` from RICOH instead. The
camera again shows **1.11 → 1.11**; confirm it, and your camera is back to stock.

## Hue sectors

After fitting the colour mix and tone curves, the tool tries to improve the look
with **hue sectors**: the camera can change colours separately in 12 ranges of
hue. They are kept **only when they clearly help** (the whole colour range or the
ColorChecker improves by at least 10 %) **and nothing gets worse** (your photo,
the greys, the ColorChecker). The result line says `hue sectors: yes` or `no`.

- Greys are never changed by the hue sectors, and neighbouring sectors always
  meet without a gap, so colours stay smooth from one hue to the next.
- With `--photo` the hue sectors are tuned for real content too (recommended).
- `--no-hue` turns them off (`make` and `fit`).

## Colour after the curves

With `--after-curves` (`make` and `fit`), the tool also tries a second colour
mix that the camera applies **after** the tone curves. Many film looks change
colours differently in shadows and highlights. The usual colour mix acts before
the curves, so it cannot follow that, but a mix after the curves often can.

The LUT's whole colour range and real photos do not always agree: a version can
be much closer on saturated colours and a little further on an everyday photo.
So both versions go into the list of kept results described in
[More turns](#more-turns): the version with colour after the curves is kept
whenever it is better on anything (the whole range, the ColorChecker, the greys
or your photo), next to the usual one, and you choose. Preview both on your own
photo before choosing.

**Why it is optional.** See [Which helps more](#which-helps-more): more turns of
the usual fit are the bigger lever. The mix after the curves helps on average,
but it makes every turn several minutes longer.

- Greys stay grey: the mix never adds a colour cast to a neutral grey.
- Fitting takes several minutes longer (four extra fits).
- In our tests on 24 LUTs (one turn each), the usual version stayed exactly as
  before, and 23 LUTs got a second version:
  - **8 better on everything that changed**: there the average difference to
    the LUT fell from 3.7 to 2.4 (ColorChecker 1.9 → 1.1, photo 0.91 → 0.75);
  - **15 with a trade-off**. Colour film looks usually get much
    closer on the whole range and the ColorChecker (e.g. 3.6 → 2.5) with a
    slightly worse photo (by up to 0.17); monochrome looks usually get much
    closer on greys and the photo, but further on the whole range;
  - two LUTs that were refused before can now be built.

## More turns

Fitting starts from a starting point and improves from there. The camera has
only a few colour controls, so **where the fit starts often decides how close it
gets**. With `--turns`, the tool runs several fits ("turns"), each from a new
random starting point, and keeps the good ones:

```sh
uv run gr4_lutsmith.py make my_look.cube ~/Downloads/fwdc248b.bin --photo R0001234.DNG --turns 3
```

- Turn 1 always uses the usual starting point, so one turn (the default) gives
  the same result as before. Every further turn draws a new random start.
- Each turn takes as long as a normal fit (a few minutes).
- **Kept results.** A new result is kept unless an earlier one is at least as
  good on every measure (the whole range, the ColorChecker, the greys and your
  photo). An earlier result that the new one beats on every measure leaves the
  list. So every kept result is better than all the others on at least one
  measure; the tool shows them side by side and recommends the one with the best
  overall score (the average difference on everything the fit uses).
- **Add turns at the end.** When the results come up, the tool asks
  `More turns?`. Type a number to run that many more, the same way the run
  started (`2`); add `a` for turns with
  [colour after the curves](#colour-after-the-curves) (`2a`), or `b` for turns
  without it (`2b`), whichever way the run started. New turns are compared with
  all kept results. Press Enter to keep what you have.
- **Repeat a turn.** Every turn's seed is shown and saved in `turns.json`;
  `--seed 1234` runs the same turn again (with `--turns 3`: seeds 1234, 1235,
  1236).

With more than one kept result, the output folder looks like this:

```
output/my_look/look.json, fwdc248b.bin      the recommended result
output/my_look/turns/turn-1/                 every kept result, each with its
output/my_look/turns/turn-3-after-curves/    own look.json (and, with make, fwdc248b.bin)
output/my_look/turns.json                    every turn, its seed and its scores
```

To use another kept result, copy its `fwdc248b.bin` to the SD card instead.

### Which helps more

In a test with the same fitting time, the best of four turns of the usual fit,
continued, beat a fit with [colour after the curves](#colour-after-the-curves)
at every step. Across ten random turns of each, the version with colour after
the curves was better on average. So both help, but **more turns of the usual
fit give the larger gain**: if you have time, run more turns first, and add
`--after-curves` when you also want to compare that version.

## Choosing the slot

Your look replaces one of the camera's looks. You choose which one with
`--slot` (`make` and `build`); `uv run gr4_lutsmith.py slots` shows the same list
in the terminal:

| `--slot` | Shown as | Replaces | Still | Movie | Extra sliders |
| --- | --- | --- | :---: | :---: | --- |
| `high-contrast` (default) | High Contrast | the hidden monochrome High Contrast look | ✓ | ✓ | — |
| `cinema-yellow` | Cinema (Yellow) | RICOH's Cinema (Yellow) look | ✓ | ✓ | Saturation, Hue |
| `cinema-green` | Cinema (Green) | RICOH's Cinema (Green) look | ✓ | ✓ | Saturation, Hue |
| `vivid` | Vivid | RICOH's Vivid look | ✓ | ✓ | Saturation, Hue |
| `positive-film` | Positive Film | RICOH's Positive Film look | ✓ | ✓ | Saturation, Hue |
| `negative-film` | Negative Film | RICOH's Negative Film look | ✓ | ✓ | Saturation, Hue |
| `bleach-bypass` | Bleach Bypass | RICOH's Bleach Bypass look | ✓ | ✓ | Saturation, Hue |
| `retro` | Retro | RICOH's Retro look | ✓ | ✓ | Saturation, Hue |
| `soft-monotone` | Soft Monotone | RICOH's Soft Monotone look | ✓ | | — |
| `hard-monotone` | Hard Monotone | RICOH's Hard Monotone look | ✓ | ✓ | — |
| `hi-contrast-bw` | Hi-Contrast B&W | RICOH's Hi-Contrast B&W look | ✓ | ✓ | — |
| `mono-standard` | Standard (the second one) | the hidden monochrome Standard look | ✓ | ✓ | — |
| `mono-soft` | Soft | the hidden monochrome Soft look | ✓ | | — |
| `mono-solid` | Solid | the hidden monochrome Solid look | ✓ | ✓ | — |

- The colours are the same in every slot: the camera processes your look the
  same way there, so the fit and its scores do not change with the slot.
- Your look opens with every slider at 0 and grain off, in every slot.
- What changes with the slot is only which entry you lose and which sliders
  you get. On a monochrome slot your look is in colour; Saturation and Hue are
  not offered there.
- **Cinema:** its Color Tone setting has no effect on your look.
  **Bleach Bypass:** its tone setting does not change your look.
  **Retro:** Retro's own toning and vignette-like tone effect are removed, so
  you get your look only.
- A slot you do not use keeps RICOH's look. Without a look on High Contrast, it
  stays the monochrome High Contrast look.
- **Not available:** Standard, Monotone, HDR Tone, Cross Processing and the
  hidden Grainy and HDR looks. These share parts of their processing with other
  looks or use special processing that would change your colours.
- **Several looks in one firmware:** fit each LUT with `fit`, then build them
  together, each look file followed by its own `--slot` (and `--name`,
  `--icon` if you like):

  ```sh
  uv run gr4_lutsmith.py fit look_a.cube --photo R0001234.DNG -o output/a
  uv run gr4_lutsmith.py fit look_b.cube --photo R0001234.DNG -o output/b
  uv run gr4_lutsmith.py build output/a/look.json --slot cinema-yellow output/b/look.json --slot vivid --name "Look B" ~/Downloads/fwdc248b.bin -o output/both
  ```

- **How many at once:** High Contrast and the two Cinema slots always take a
  full look. The other slots share a limited amount of room: each look needs
  room for its tone curves, and much more for hue sectors and colour after the
  curves. A simple look (one tone curve) fits on up to 10 of them at once;
  looks with hue sectors fit on far fewer. If they do not fit, the tool says so
  and writes nothing; use fewer slots or simpler looks, and leave High Contrast
  unused for the most room.
- `check` lists which slots carry a personal look.

## Naming the slot

`--name` (`make` and `build`) gives the slot your own name, in the Image Control
menu and its screens, in all of the camera's languages:

```sh
uv run gr4_lutsmith.py make my_look.cube ~/Downloads/fwdc248b.bin --slot vivid --name "Portra"
```

- Each slot takes names up to a certain length: `uv run gr4_lutsmith.py slots`
  shows it (from 9 characters on Retro to 23 on Cinema (Yellow)). The second
  monochrome Standard cannot be renamed, because it shares its name with the
  colour Standard look.
- **Plain ASCII** (English letters, digits, spaces and punctuation) shows in
  every camera language. This is the safe choice.
- **Other letters and characters** work in the camera languages that use them:
  a Japanese name shows when the camera is set to Japanese, `Müller` in the
  European languages, `Плёнка` in Russian, and so on. Characters that none of
  the camera's languages use (emoji, Arabic, Hebrew, …) are refused.
- The tool checks the name against your **camera language**, which is your
  computer's language unless you set `--camera-language` (for example
  `--camera-language Japanese`; `--help` lists all 21). If the name uses
  characters that language does not, the tool warns that they may not show and
  asks whether to use the name anyway. Answer `y` to go ahead, or add
  `--accept-name` to agree in advance (needed when the tool cannot ask, for
  example in a script). If you later switch the camera to another language, a
  name outside ASCII may not show there.
- Long names may be cut off on some screens (Chinese, Japanese and Korean
  characters take about twice the width of English letters).
- Without `--name`, the slot keeps its own name. The name is not saved in
  `look.json` (that file holds only the look): give `--name` again when you
  `build`. `check` shows the name in a built firmware.

## Your own icon

`--icon` (`make` and `build`) replaces the slot's icon in the Image Control menu
with your own picture:

```sh
uv run gr4_lutsmith.py make my_look.cube ~/Downloads/fwdc248b.bin --slot vivid --name "Portra" --icon portra.png
```

- Any picture works (PNG, JPEG, …). The menu icon is 40×40 pixels: a larger
  or smaller picture is resized, and if it is not square, its **centre** is
  used. Transparent parts stay transparent, so a PNG with a transparent
  background looks like the camera's own icons.
- `make` saves the result as `icon.png` next to the firmware, so you can see
  exactly what the camera will show. Simple, bold shapes read best at this size.
- Every slot except the second monochrome Standard takes an own icon (that one
  shares its icon with the colour Standard look). `slots` lists them.
- It needs Pillow (`uv sync --extra photo`). Without `--icon`, the slot keeps
  its own icon. The icon is not saved in `look.json`: give `--icon` again when
  you `build`. `check` shows which slots have their own icon.

## Using a look someone shared

`look.json` holds the look itself: its colour settings and how well they fit
the LUT, and the LUT's file name — no folders of your computer. When its maker
chose a slot, a name or an icon, the file carries those too (with the name's
camera language). Whoever has the file can build the firmware directly,
without the LUT and without fitting, and gets the same firmware, byte for
byte, as its maker.

The file can have any name, so rename it as you like before you share it
(`portra-400.json`, …):

```sh
uv run gr4_lutsmith.py build portra-400.json ~/Downloads/fwdc248b.bin --slot vivid --name "Portra" --icon portra.png
```

- The tool first shows how well the look fits its LUT (the results its maker
  saw; "on your photo" means the maker's photo).
- Slot, name and icon are yours to choose, as in
  [Choosing the slot](#choosing-the-slot), [Naming the slot](#naming-the-slot)
  and [Your own icon](#your-own-icon). Without `--slot`, the look goes on
  High Contrast. A file that carries its maker's name or icon keeps them;
  `--no-name` and `--no-icon` drop them and keep the slot's own.
- Several looks: write each file's own `--slot`, `--name` and `--icon` right
  after it:

  ```sh
  uv run gr4_lutsmith.py build portra-400.json --slot vivid --name "Portra" tri-x.json --slot hard-monotone ~/Downloads/fwdc248b.bin -o output/both
  ```

- The file's contents are checked before anything is built: a damaged file,
  or one with values the camera cannot take, is refused and nothing is
  written.
- Files made by versions before 1.7.0 may also hold a slot, a name, an icon
  and the full path to the LUT. `build` ignores the slot, name and icon (it
  says which they were); open such a file in a text editor and check
  `"file"` before you share it.

## When a LUT is "poor"

Many film-simulation LUTs reshape very saturated colours in ways the camera's
controls cannot follow, so they are "poor" across their whole colour range even
though everyday pictures look right. The firmware is still written when:

- the **ColorChecker** colours differ by less than 2 on average, the **greys**
  by less than 2, and no grey gets a visible colour cast; and, with `--photo`,
  the look is not poor on your photo; or
- with `--photo`, the look is **good on your photo** (and at least halves the
  difference there), with the same grey and colour-cast limits.

The tool says which rule applied. Otherwise it writes no firmware; add
`--force` to build anyway, and check the preview first.

## Good to know

- **The sliders** (Key, Contrast, Highlight, Shadow, Sharpness, Shading,
  Clarity, Toning, Grain) work on top of your look; at 0 you get the LUT.
  Saturation and Hue are not offered on High Contrast; on the Cinema slots they
  are (see [Choosing the slot](#choosing-the-slot)).
- **Two "Standard" and two "HDR Tone" entries.** The monochrome Standard and HDR
  looks use the same names and icons as the colour ones.
- **An official RICOH update removes your look.** It replaces the whole
  firmware.
- **Only for the standard GR IV.** Never install the file on another model.
- **Before sending the camera for service,** go back to the official firmware.
- To check any firmware file before copying it to a card:

  ```sh
  uv run gr4_lutsmith.py check path/to/fwdc248b.bin
  ```

  It tells you whether the file is the official 1.11, a GR4-LUTSmith firmware,
  or something else.
- To check the tool itself against your official file:

  ```sh
  uv run gr4_lutsmith.py selftest ~/Downloads/fwdc248b.bin
  ```

  This builds the bundled example (`examples/film-1d.cube`) and compares the
  result with the tested build. It prints `selftest: PASSED`.
- `uv run gr4_lutsmith.py fit my_look.cube --photo R0001234.DNG` fits and
  reports without needing the firmware file, so you can try many LUTs first.
- `check` also tells you whether a GR4-LUTSmith firmware uses hue sectors and
  the colour mix after the curves.

## Without uv

With Python 3.9 or newer:

```sh
python3 -m pip install numpy rawpy pillow
python3 gr4_lutsmith.py make my_look.cube ~/Downloads/fwdc248b.bin --photo R0001234.DNG
```

`rawpy` and `pillow` are only needed for `--photo` and `preview`.

## Risks

- Your camera cannot tell a good firmware file from a damaged one. Only copy a
  file the tool has written (or confirmed with `check`) to your card.
- If an update is interrupted, or a firmware file cannot start, the camera may
  not reach the update function again, and it would then need RICOH service.
  This did not happen in testing, but it cannot be ruled out.
- Your camera's own calibration data is not touched.

## Testing status

| What | Status |
| --- | --- |
| A personal LUT look with its own tone curve, in this slot, on stills, movie, viewfinder and RAW Development | tested on a GR IV 1.11 |
| Update accepted; going back to the official 1.11 restores the camera completely | tested on a GR IV 1.11 |
| The five monochrome looks | tested on a GR IV 1.11 (see GR4-MonoUnlock) |
| Keeping the slot's own "High Contrast" name and icon | the name and icon are RICOH's own, unchanged; this exact combination has not been installed yet |
| Sliders opening at 0 (grain off) | made of the same changes as the tested build (a subset of them); not installed on its own yet |
| Looks that need two or three tone curves (colour casts, toned looks) | three curves tested on a GR IV 1.11 |
| Hue sectors | tested on a GR IV 1.11 in RAW Development with a film-simulation LUT (ISO 200, a daylight scene of greens and yellows): the camera matched the tool's prediction, the difference to the LUT fell by about 30 % on coloured areas, and no gaps between colours were visible. Reds, blues, purples and higher ISO have not been checked on the camera yet |
| Colour after the curves (`--after-curves`, optional) | not tested on a camera yet |
| Looks on the Cinema (Yellow) / Cinema (Green) slots | tested on a GR IV 1.11: works |
| Looks on the other slots (Vivid, Positive Film, Negative Film, Bleach Bypass, Retro, the monochrome slots) | tested on a GR IV 1.11: works |
| Your own name for the slot (`--name`) | tested on a GR IV 1.11: works |
| Your own icon for the slot (`--icon`) | tested on a GR IV 1.11: works |
| Building from a shared `look.json` | checked offline: the firmware is byte-identical to `make` with the same LUT result, slot, name and icon; nothing new reaches the camera |
| The app (`GR4-LUTSmith`) builds the same firmware as the command line | checked offline: byte-identical for the same LUT, photo and choices |
| The tool reproduces the tested example exactly | tested (`selftest`) |

## Files

```
GR4-LUTSmith/
├── README.md            this guide
├── gr4_lutsmith.py      the tool (the command line, and the engine the app uses)
├── lutsmith_app/        the app: window, translations (assets/i18n), icons and fonts
├── packaging/           building the app (packaging/build.py), its icons and licence texts
├── tests/               checks run before every app build; scripts that drive the app for development
├── tools/               translation check, app icon
├── pyproject.toml       dependencies (for uv)
├── uv.lock
└── examples/
    ├── film-1d.cube      a 1D film-style LUT (used by selftest)
    └── film-1d.look.json its fitted look
```

Run the app from the source: `uv sync --extra app` then `uv run python -m lutsmith_app`.
Build it: `uv run python packaging/build.py` (on a Mac with Apple silicon, add `--arch x86_64` for the Intel
build); the zip lands in `dist/`.

| File | SHA-256 |
| --- | --- |
| official RICOH GR IV 1.11 `fwdc248b.bin` | `a2f664dfca034059eb0fd6e18ab08684c326b4a034d85c164dad7e1ec9b5655f` |
| example firmware (`selftest`, from `examples/film-1d.look.json`) | `d201add28bc3ab738bc92fcfdaafa3847bc881bfd7eecb6b7e8f331d1289f052` |
