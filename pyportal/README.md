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

## Exo 2 font (required)

The display uses [Exo 2](https://fonts.google.com/specimen/Exo+2) in three sizes.
You need BDF versions; CircuitPython cannot load TTF.

**Option A — use the pre-converted files** (if provided in this repo under `fonts/`):
Copy `fonts/` to the root of CIRCUITPY.

**Option B — convert yourself:**

1. Download Exo 2 from Google Fonts (the `Exo2-Regular.ttf` file).
2. Install fonttools: `pip install fonttools`
3. Install otf2bdf or use the Adafruit font conversion script:
   ```
   pip install adafruit-circuitpython-bitmap-font
   python -c "
   from fonttools.ttLib import TTFont
   " 
   ```
   The easiest path is the Adafruit `convert_font` tool:
   ```
   pip install adafruit-circuitpython-bitmap-font
   python -m adafruit_bitmap_font.font_converter Exo2-Regular.ttf 11
   python -m adafruit_bitmap_font.font_converter Exo2-Regular.ttf 14
   python -m adafruit_bitmap_font.font_converter Exo2-Regular.ttf 20
   ```
   This produces `Exo2-Regular-11.bdf`, `Exo2-Regular-14.bdf`, `Exo2-Regular-20.bdf`.

4. Create `/fonts/` on CIRCUITPY and copy the three `.bdf` files there.

If the font files are missing at boot, the code falls back to the built-in
`terminalio.FONT` so the display still works — just less stylish.

## Installation

1. Copy `code.py` to the root of CIRCUITPY.
2. Copy `settings.toml.example` to `settings.toml` and fill in your values.
3. Copy the `fonts/` directory as above.
4. Copy the required libraries into `/lib`.

## Configuration (`settings.toml`)

| Key | Default | Notes |
|-----|---------|-------|
| `CIRCUITPY_WIFI_SSID` | — | Required |
| `CIRCUITPY_WIFI_PASSWORD` | — | Required |
| `HOMESERVER_URL` | `http://home.local:8080` | IP or mDNS hostname of the poller |
| `POLL_SECONDS` | `30` | How often to fetch `/status` |
| `HISTORY_REFRESH_EVERY_N_POLLS` | `5` | Fetch `/history` every N polls (~2.5 min) |
| `UTC_OFFSET_HOURS` | `0` | Integer UTC offset for displayed times (e.g. `-7` for PDT) |

## Display states

| State | Appearance |
|-------|-----------|
| Normal | Full brightness, colored segmented bars, burn rate status |
| Stale | 40% brightness, "STALE" badge top-right |
| Needs auth | 30% brightness, "NEEDS AUTH" + hint text centered |
| No server | 50% brightness, "NO SERVER" + URL hint centered |

## Memory notes

The SAMD51 has ~60–80 KB free after CircuitPython + libraries. The scene graph
is built once at startup; the main loop only mutates existing objects. If you hit
`MemoryError` at boot, try removing the 7-day bar segments (reduce `SEG_N` or
comment out the `segs_7d` block) or dropping to smaller font sizes.
