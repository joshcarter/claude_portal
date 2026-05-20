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

## Font

The display uses [Dogica Pixel](https://www.dafont.com/dogica.font) at 8px — a single BDF file for all text.

CircuitPython cannot load OTF/TTF directly, so you need to convert it to BDF first.

### Converting Dogica Pixel to BDF

1. Download **Dogica Pixel** (`dogicapixel.otf`) from the link above.
2. Install `otf2bdf`:
   ```bash
   brew install otf2bdf   # macOS
   ```
3. Convert at 8pt/72dpi so each font pixel maps 1:1 to a display pixel:
   ```bash
   otf2bdf -p 8 -r 72 dogicapixel.otf -o Dogica-Pixel-8.bdf
   ```
4. Verify the output looks clean — glyph rows should be clean hex patterns
   with no anti-aliasing noise:
   ```bash
   grep -A 12 "STARTCHAR A" Dogica-Pixel-8.bdf
   ```
5. Create `/fonts/` on CIRCUITPY and copy `Dogica-Pixel-8.bdf` there.

If the font file is missing at boot, the code falls back to the built-in
`terminalio.FONT` so the display still works — just less stylish.

## Installation

1. Copy `code.py` to the root of CIRCUITPY.
2. Copy `settings.toml.example` to `settings.toml` and fill in your values.
3. Copy `Dogica-Pixel-8.bdf` into `/fonts/` on CIRCUITPY as above.
4. Copy the required libraries into `/lib` (via `circup`).

## Configuration (`settings.toml`)

| Key | Default | Notes |
|-----|---------|-------|
| `CIRCUITPY_WIFI_SSID` | — | Required |
| `CIRCUITPY_WIFI_PASSWORD` | — | Required |
| `HOMESERVER_URL` | `http://home.local:7654` | IP or mDNS hostname of the poller |
| `POLL_SECONDS` | `30` | How often to fetch `/status` |
| `HISTORY_REFRESH_EVERY_N_POLLS` | `5` | Refresh 24H chart every N status polls |
| `UTC_OFFSET_HOURS` | `0` | Integer UTC offset for displayed times (e.g. `-7` for PDT) |

## Display layout

```
┌─────────────────────────────────────────────────────┐
│ 5H  [████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░] │  ← solid fill bar (blue/amber/orange/red)
│     resets 14:30 · 8.5%/hr · →~1h12m               │  ← status line
├─────────────────────────────────────────────────────┤
│ 24H                                                 │
│ ┌─────────────────────────────────────────────────┐ │
│ │    ▌▌  ▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌▌             │ │  ← hourly peaks, always blue
│ └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

**5H bar** — solid fill, color-coded by usage level:

| Color | Meaning |
|-------|---------|
| Blue | < 60% used |
| Amber | 60–79% used |
| Orange | 80–94% used (or burning fast) |
| Red | ≥ 95% used (or projected to fill before reset) |

**24H chart** — one column per hour, height = peak 5H usage during that hour.
All bars are blue; the 5H bar carries the urgency color signal.

**Status line** — shows reset time, current burn rate, and projected time to full.
Hidden when there's nothing to show.

## Display states

| State | Appearance |
|-------|-----------|
| Normal | Full brightness, bar and chart active |
| Stale | 40% brightness, "STALE" badge top-right |
| Needs auth | 30% brightness, "NEEDS AUTH" + hint text centered |
| No server / no wifi | 50% brightness, error message centered |

## Memory notes

The SAMD51 has ~60–80 KB free after CircuitPython + libraries. The scene graph
is built once at startup and the main loop only mutates bitmap contents —
no display objects are added or removed at runtime. The two bitmaps
(5H bar: 279×14, 24H chart: 279×150) together use ~50 KB, well within limits.
If `bitmaptools` is available it's used for fast rectangle fills; otherwise the
code falls back to manual pixel writes (slower but correct).
