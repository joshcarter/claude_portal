# PyPortal client

CircuitPython 10.2.1 on the Adafruit PyPortal (3.2", 320×240).

## Required libraries

Use [circup](https://github.com/adafruit/circup) — it detects the CircuitPython version on the board and installs the matching pre-compiled `.mpy` files in one shot:

```bash
pip install circup
# with the PyPortal connected over USB:
circup install -r pyportal/requirements.txt
```

`circup` writes directly to `/lib` on CIRCUITPY. No manual bundle hunting or version-matching required.

If you ever need to check for outdated libraries later: `circup update`.

## Fonts

Two BDF fonts live in `/fonts/` on CIRCUITPY:

| File | Used for |
|------|----------|
| `Dogica-Pixel-8.bdf` | the bottom-left status line |
| `DESG7Modern-Italic-40.bdf` | the 7-segment style numeric readout |

CircuitPython cannot load OTF/TTF directly, so each font is converted to
BDF with `otf2bdf` first. Convert at the target pixel size and 72 dpi so
one font pixel maps 1:1 to a display pixel — e.g. for Dogica Pixel
([dafont](https://www.dafont.com/dogica.font)) at 8px:

```bash
brew install otf2bdf   # macOS
otf2bdf -p 8 -r 72 dogicapixel.otf -o Dogica-Pixel-8.bdf
```

`DESG7Modern-Italic-40.bdf` is the same process at 40px. Verify the output
has clean glyph rows with no anti-aliasing noise.

If a font file is missing at boot, the code falls back to the built-in
`terminalio.FONT` so the display still works — just less stylish.

## Tachometer frames

The gauge is 21 pre-rendered BMP frames, `tach0.bmp` (idle) through
`tach20.bmp` (pegged), in `/bmp/` on CIRCUITPY. They load one at a time
via `displayio.OnDiskBitmap`, which streams straight from flash.

The frames must be **BMP**: this board's CircuitPython build can't decode
PNG via `OnDiskBitmap`. The gauge is exported from Affinity as PNG-8 and
converted with `graphics/png_to_bmp.py`, which writes the BMPs into
`pyportal/bmp/`.

## Installation

1. Copy `code.py` to the root of CIRCUITPY.
2. Copy `settings.toml.example` to `settings.toml` and fill in your values.
3. Copy the `fonts/` folder to `/fonts/` on CIRCUITPY.
4. Copy the `bmp/` folder to `/bmp/` on CIRCUITPY.
5. Copy the required libraries into `/lib` (via `circup`).

## Configuration (`settings.toml`)

| Key | Default | Notes |
|-----|---------|-------|
| `CIRCUITPY_WIFI_SSID` | — | Required |
| `CIRCUITPY_WIFI_PASSWORD` | — | Required |
| `HOMESERVER_URL` | `http://home.local:7654` | IP or mDNS hostname of the poller |
| `POLL_SECONDS` | `30` | How often to fetch `/status` |
| `UTC_OFFSET_HOURS` | `0` | Integer UTC offset for displayed times (e.g. `-7` for PDT) |

## Display layout

```
┌───────────────────────────────────────────────┐
│                                                 │
│          tachometer gauge  (tach0-20)           │
│                  [ 88 ]                         │
│                                                 │
│ resets 14:30 · 8.5%/hr · →1h12m       [████░░]  │
└───────────────────────────────────────────────┘
```

- **Tachometer** — a 21-frame gauge driven by the 5-hour `redline_ratio`
  (see *Tachometer scale* below). Frames `tach0`–`tach14` are blue,
  `tach15`–`tach17` yellow, `tach18`–`tach20` red.
- **Numeric readout** — a dark "88" ghost (all segments lit) with the live
  0–99 value drawn over it, mimicking an unlit/lit 7-segment display. The
  value follows the same scale as the gauge.
- **Status line** — bottom-left, Dogica font: 5H reset time, current burn
  rate, and projected time to full.
- **7-day bar** — bottom-right: 7-day window utilisation, 0–100%.

## Tachometer scale

The gauge and the 0–99 readout both come from the 5-hour `redline_ratio`
reported by the server (`burn_rate / sustainable_rate`, where `1.0` means
you are on track to spend the window's whole budget exactly when it
resets).

`tach_position()` maps the ratio to a continuous gauge position
`0.0 .. 20.0` in two pieces:

- **Below the redline** (`ratio` ≤ 1.0): a concave curve,
  `position = REDLINE_FRAME * ratio ** BLUE_EXPONENT`. With
  `BLUE_EXPONENT = 0.5` it is steep at low ratios and flattens toward the
  redline, so a modest user still sees the needle move through the day.
- **Above the redline** (`ratio` > 1.0): linear from `tach17` to `tach20`,
  pegging once the burn rate reaches `RED_FULL_RATIO` (default 2×
  sustainable).

`redline_ratio == 1.0` lands exactly on `tach17`, the top of the yellow
band — anything past that is red. The readout is the same position scaled
to 0–99 instead of quantised to 21 frames, so it moves even between frame
changes.

| `redline_ratio` | frame | number | zone |
|---|---|---|---|
| 0.05 | 4  | 19 | blue |
| 0.20 | 8  | 38 | blue |
| 0.50 | 12 | 60 | blue |
| 0.80 | 15 | 75 | yellow |
| 1.00 | 17 | 84 | yellow (redline) |
| 1.60 | 19 | 93 | red |
| ≥2.00 | 20 | 99 | red |

Tuning knobs in `code.py`: `REDLINE_FRAME`, `BLUE_EXPONENT` (lower = more
sensitive at low use), and `RED_FULL_RATIO`.

## Display states

| State | Appearance |
|-------|-----------|
| Normal | Full brightness |
| Stale | 40% brightness (last upstream poll over 5 min ago) |
| Needs auth | 30% brightness, "NEEDS AUTH" centered |
| No server / no wifi | 50% brightness, "NO SERVER" / "NO WIFI" centered |

## Memory notes

The SAMD51 has ~60–80 KB free after CircuitPython + libraries. The scene
graph is built once at startup; the main loop only swaps the tachometer
`OnDiskBitmap` and mutates label text and the small 7D-bar bitmap — no
display objects are added or removed at runtime. Tach frames stream from
flash rather than loading into RAM, so only one frame is resident at a
time. If `bitmaptools` is available it is used for fast rectangle fills;
otherwise the code falls back to manual pixel writes.
