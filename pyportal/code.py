import gc
import os
import time

import board
import busio
import digitalio
import displayio
import terminalio
bitmaptools = None
try:
    import bitmaptools
    _HAS_BITMAPTOOLS = True
except ImportError:
    _HAS_BITMAPTOOLS = False
from adafruit_bitmap_font import bitmap_font
from adafruit_display_shapes.rect import Rect
from adafruit_display_text.label import Label
from adafruit_esp32spi import adafruit_esp32spi
import adafruit_connection_manager
import adafruit_requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SSID         = os.getenv("CIRCUITPY_WIFI_SSID", "")
PASSWORD     = os.getenv("CIRCUITPY_WIFI_PASSWORD", "")
SERVER_URL   = os.getenv("HOMESERVER_URL", "http://home.local:7654")
POLL_SECS    = int(os.getenv("POLL_SECONDS", "30"))
HIST_EVERY   = int(os.getenv("HISTORY_REFRESH_EVERY_N_POLLS", "5"))
UTC_OFFSET   = int(os.getenv("UTC_OFFSET_HOURS", "0"))

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
C_BG         = 0x000810
C_PANEL      = 0x000E1C
C_SEP        = 0x003355
C_TEXT       = 0x00BBFF
C_DIM_TEXT   = 0x004466
C_UNFILLED   = 0x001833
C_BLUE       = 0x0055EE
C_AMBER      = 0xFFAA00
C_ORANGE     = 0xFF4400
C_RED        = 0xFF0000
C_ERROR      = 0xFF2200
C_STALE_BADGE = 0x884400

# ---------------------------------------------------------------------------
# Display dimensions
# ---------------------------------------------------------------------------
W, H = 320, 240

# 5-hour bar
BAR_X   = 20
BAR_Y   = 22
BAR_W   = 279   # same total span as the old 20-segment layout
BAR_H   = 14

# Chart area — left edge and width match the 5H bar
CHART_X  = BAR_X
CHART_Y  = 78
CHART_W  = BAR_W
CHART_H  = H - CHART_Y - 12   # 150px
BAR_CW   = 11
BAR_CGAP = 1

# ---------------------------------------------------------------------------
# WiFi + requests setup
# ---------------------------------------------------------------------------
esp32_cs    = digitalio.DigitalInOut(board.ESP_CS)
esp32_ready = digitalio.DigitalInOut(board.ESP_BUSY)
esp32_reset = digitalio.DigitalInOut(board.ESP_RESET)
spi         = busio.SPI(board.SCK, board.MOSI, board.MISO)
esp         = adafruit_esp32spi.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset)
requests    = None


def wifi_connect():
    global requests
    print("WiFi: resetting ESP32")
    esp.reset()
    print("WiFi: connecting to", SSID)
    while not esp.is_connected:
        try:
            esp.connect_AP(SSID, PASSWORD)
            print("WiFi: connected, IP =", esp.pretty_ip(esp.ip_address))
        except Exception as e:
            print("WiFi: connect failed:", type(e).__name__, e)
            time.sleep(2)
    pool     = adafruit_connection_manager.get_radio_socketpool(esp)
    requests = adafruit_requests.Session(pool)


# ---------------------------------------------------------------------------
# Font loading — falls back to built-in terminalio if BDF not found
# ---------------------------------------------------------------------------
def _load_font(path):
    try:
        return bitmap_font.load_font(path)
    except Exception:
        return terminalio.FONT


FONT  = _load_font("/fonts/Dogica-Pixel-8.bdf")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fmt_hhmm(unix_ts):
    # Apply UTC offset and format HH:MM
    local = unix_ts + UTC_OFFSET * 3600
    h = (local // 3600) % 24
    m = (local // 60) % 60
    return "{:02d}:{:02d}".format(h, m)


def fmt_duration(seconds):
    if seconds <= 0:
        return "--"
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    if h > 0:
        return "~{}h{:02d}m".format(h, m)
    return "~{}m".format(m)


# ---------------------------------------------------------------------------
# Scene graph construction — build once, mutate in loop
# ---------------------------------------------------------------------------

root = displayio.Group()
board.DISPLAY.root_group = root

# Background
bg = Rect(0, 0, W, H, fill=C_BG)
root.append(bg)

# Top panel (5-hour section)
top_panel = Rect(0, 0, W, 60, fill=C_PANEL)
root.append(top_panel)

# "5H" label
lbl_5h = Label(FONT, text="5H", color=C_DIM_TEXT, x=BAR_X, y=14)
root.append(lbl_5h)

# 5-hour bar — solid fill bitmap with outline, no background
bar_outline = Rect(BAR_X - 1, BAR_Y - 1, BAR_W + 2, BAR_H + 2,
                   fill=None, outline=C_DIM_TEXT)
root.append(bar_outline)

_BAR_PALETTE = displayio.Palette(5)
_BAR_PALETTE.make_transparent(0)   # index 0 = transparent (panel shows through)
_BAR_PALETTE[1] = C_BLUE
_BAR_PALETTE[2] = C_AMBER
_BAR_PALETTE[3] = C_ORANGE
_BAR_PALETTE[4] = C_RED

bar_bitmap = displayio.Bitmap(BAR_W, BAR_H, 5)
bar_bitmap.fill(0)
bar_tile = displayio.TileGrid(bar_bitmap, pixel_shader=_BAR_PALETTE,
                              x=BAR_X, y=BAR_Y)
root.append(bar_tile)

# Status line (resets / burn rate / ETA)
lbl_status = Label(FONT, text="", color=C_DIM_TEXT, x=BAR_X, y=50)
root.append(lbl_status)

# Separator line
sep = Rect(0, 60, W, 1, fill=C_SEP)
root.append(sep)

# Chart area label
lbl_chart = Label(FONT, text="24H", color=C_DIM_TEXT, x=BAR_X, y=70)
root.append(lbl_chart)

# Chart: single bitmap, all bars blue, transparent background
# Palette: 0=transparent  1=blue
_CHART_PALETTE = displayio.Palette(2)
_CHART_PALETTE.make_transparent(0)
_CHART_PALETTE[1] = C_BLUE

chart_bitmap = displayio.Bitmap(CHART_W, CHART_H, 2)
chart_bitmap.fill(0)
chart_tile = displayio.TileGrid(chart_bitmap, pixel_shader=_CHART_PALETTE,
                                x=CHART_X, y=CHART_Y)
root.append(chart_tile)

# Outline around chart
chart_outline = Rect(CHART_X - 1, CHART_Y - 1, CHART_W + 2, CHART_H + 2,
                     fill=None, outline=C_DIM_TEXT)
root.append(chart_outline)

# ---------------------------------------------------------------------------
# Error / stale overlay elements
# ---------------------------------------------------------------------------

# "STALE" badge (top right)
lbl_stale = Label(FONT, text="STALE", color=C_AMBER, x=W - 42, y=6)
lbl_stale.hidden = True
root.append(lbl_stale)

# "NEEDS AUTH" overlay (center)
lbl_auth = Label(FONT, text="NEEDS AUTH", color=C_ERROR, x=90, y=H // 2 - 8)
lbl_auth.hidden = True
root.append(lbl_auth)

lbl_auth_hint = Label(FONT, text="update CLAUDE_SESSION_KEY", color=C_DIM_TEXT, x=28, y=H // 2 + 10)
lbl_auth_hint.hidden = True
root.append(lbl_auth_hint)

# "NO SERVER" overlay (center)
lbl_no_server = Label(FONT, text="NO SERVER", color=C_ERROR, x=96, y=H // 2 - 8)
lbl_no_server.hidden = True
root.append(lbl_no_server)

lbl_no_server_hint = Label(FONT, text="can't reach " + SERVER_URL, color=C_DIM_TEXT, x=28, y=H // 2 + 10)
lbl_no_server_hint.hidden = True
root.append(lbl_no_server_hint)

# ---------------------------------------------------------------------------
# Update functions
# ---------------------------------------------------------------------------

def _bar_color_idx(pct, burn_fast=False):
    if pct >= 95 or (burn_fast and pct >= 80): return 4   # red
    if pct >= 80 or (burn_fast and pct >= 60): return 3   # orange
    if pct >= 60 or (burn_fast and pct < 60):  return 2   # amber
    return 1                                               # blue


def update_bar(pct, color_idx):
    bar_bitmap.fill(0)
    filled_w = max(0, min(BAR_W, int(round(pct / 100.0 * BAR_W))))
    if filled_w > 0:
        if _HAS_BITMAPTOOLS:
            bitmaptools.fill_region(bar_bitmap, 0, 0, filled_w, BAR_H, color_idx)
        else:
            for y in range(BAR_H):
                for x in range(filled_w):
                    bar_bitmap[x, y] = color_idx


def _fill_col(x0, y0, x1, y1, idx):
    if _HAS_BITMAPTOOLS:
        bitmaptools.fill_region(chart_bitmap, x0, y0, x1, y1, idx)
    else:
        for y in range(y0, y1):
            for x in range(x0, x1):
                chart_bitmap[x, y] = idx


def update_chart(buckets):
    chart_bitmap.fill(0)  # clear to unfilled
    for i, bucket in enumerate(buckets):
        if i >= 24:
            break
        peak = bucket.get("five_hour_peak")
        if peak is None:
            continue
        h = max(1, int(peak / 100.0 * CHART_H))
        x0 = i * (BAR_CW + BAR_CGAP)
        _fill_col(x0, CHART_H - h, x0 + BAR_CW, CHART_H, 1)  # always blue


def show_normal(data):
    board.DISPLAY.brightness = 1.0

    fh = data.get("five_hour") or {}
    pct_5h   = fh.get("used_pct") or 0.0
    reset_5h = fh.get("resets_at_unix") or 0
    burn     = data.get("burn_rate_pct_per_hour") or 0.0
    proj     = data.get("projected_full_at_unix")
    server_now = data.get("server_now_unix") or int(time.time())

    burn_fast = (proj is not None) and (reset_5h > 0) and (proj < reset_5h)

    update_bar(pct_5h, _bar_color_idx(pct_5h, burn_fast))

    # Status line
    parts = []
    if reset_5h:
        parts.append("resets " + fmt_hhmm(reset_5h))
    if burn > 0:
        parts.append("{:.1f}%/hr".format(burn))
        if proj:
            remaining = proj - server_now
            parts.append("→ " + fmt_duration(remaining))
    lbl_status.text = " · ".join(parts) if parts else ""

    lbl_stale.hidden = True
    lbl_auth.hidden = True
    lbl_auth_hint.hidden = True
    lbl_no_server.hidden = True
    lbl_no_server_hint.hidden = True


def show_stale(data):
    # Show last data dimmed
    show_normal(data)
    board.DISPLAY.brightness = 0.4
    lbl_stale.hidden = False
    lbl_auth.hidden = True
    lbl_auth_hint.hidden = True
    lbl_no_server.hidden = True
    lbl_no_server_hint.hidden = True


def show_auth_failed(data):
    show_stale(data)
    board.DISPLAY.brightness = 0.3
    lbl_stale.hidden = True
    lbl_auth.hidden = False
    lbl_auth_hint.hidden = False


def show_no_server(detail=""):
    board.DISPLAY.brightness = 0.5
    lbl_stale.hidden = True
    lbl_auth.hidden = True
    lbl_auth_hint.hidden = True
    if not esp.is_connected:
        lbl_no_server.text = "NO WIFI"
        lbl_no_server_hint.text = "not connected to " + SSID
    else:
        lbl_no_server.text = "NO SERVER"
        lbl_no_server_hint.text = (detail or SERVER_URL)[:40]
    lbl_no_server.hidden = False
    lbl_no_server_hint.hidden = False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def fetch_history():
    try:
        resp = requests.get(SERVER_URL + "/history?hours=24", timeout=15)
        if resp.status_code == 200:
            hist = resp.json()
            resp.close()
            update_chart(hist.get("buckets", []))
        else:
            resp.close()
    except Exception as e:
        print("history fetch exception:", type(e).__name__, e)
    gc.collect()


wifi_connect()

last_status = {}
poll_count  = 0

# Populate the chart immediately before entering the poll loop
fetch_history()

while True:
    # --- fetch /status ---
    server_ok = False
    server_error = ""
    try:
        resp = requests.get(SERVER_URL + "/status", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            resp.close()
            server_ok = True
            last_status = data
        else:
            server_error = "HTTP {}".format(resp.status_code)
            print("status fetch failed:", server_error)
            resp.close()
    except Exception as e:
        server_error = "{}: {}".format(type(e).__name__, e)
        print("status fetch exception:", server_error)

    gc.collect()

    if not server_ok:
        show_no_server(server_error)
    elif last_status.get("auth_failed"):
        show_auth_failed(last_status)
    elif last_status.get("stale"):
        show_stale(last_status)
    else:
        show_normal(last_status)

    # --- refresh /history every N polls ---
    poll_count += 1
    if server_ok and poll_count % HIST_EVERY == 0:
        fetch_history()

    time.sleep(POLL_SECS)
