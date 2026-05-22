# Claude usage dashboard — tachometer display.
#
# A 21-frame tachometer gauge (top-left) with a 7-segment style numeric
# readout in its centre, a Dogica status line bottom-left, and a small
# 7-day utilisation bar bottom-right. Boot plays a sweep animation while
# the ESP32 joins WiFi.

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

# --- Config -----------------------------------------------------------------
SSID       = os.getenv("CIRCUITPY_WIFI_SSID", "")
PASSWORD   = os.getenv("CIRCUITPY_WIFI_PASSWORD", "")
SERVER_URL = os.getenv("HOMESERVER_URL", "http://home.local:7654")
POLL_SECS  = int(os.getenv("POLL_SECONDS", "30"))
UTC_OFFSET = int(os.getenv("UTC_OFFSET_HOURS", "0"))

# --- Colors -----------------------------------------------------------------
C_BG    = 0x000000
C_DARK  = 0x0B1D20   # unlit "ghost" segments and 7D bar background
C_LIGHT = 0x40A9BF   # live number, status text, 7D bar fill
C_ERROR = 0xFF2200

# --- Layout -----------------------------------------------------------------
W, H = 320, 240

TACH_BMP        = "/bmp/tach{}.bmp"
TACH_FRAMES     = 21               # tach0 .. tach20
TACH_X, TACH_Y  = 12, 14
REDLINE_FRAME   = 17               # frame for redline_ratio == 1.0 (top of yellow)
BLUE_EXPONENT   = 0.5              # scale curve below redline; <1 = sensitive at low use
RED_FULL_RATIO  = 2.0              # redline_ratio that pegs the gauge at tach20

NUM_FONT_PATH = "/fonts/DESG7Modern-Italic-40.bdf"
NUM_X, NUM_Y  = 180, 160           # left/baseline of the "88" ghost

STATUS_FONT_PATH   = "/fonts/Dogica-Pixel-8.bdf"
STATUS_X, STATUS_Y = 10, 225

BAR7D_X, BAR7D_Y = 250, 220        # x=180 .. x=307
BAR7D_W, BAR7D_H = 57, 9

# --- Hardware: ESP32 --------------------------------------------------------
esp32_cs    = digitalio.DigitalInOut(board.ESP_CS)
esp32_ready = digitalio.DigitalInOut(board.ESP_BUSY)
esp32_reset = digitalio.DigitalInOut(board.ESP_RESET)
spi         = busio.SPI(board.SCK, board.MOSI, board.MISO)
esp         = adafruit_esp32spi.ESP_SPIcontrol(spi, esp32_cs, esp32_ready, esp32_reset)
requests    = None

# Reset the ESP now so it settles while the UI is built and the animation runs.
esp.reset()


def _load_font(path):
    try:
        return bitmap_font.load_font(path)
    except Exception as exc:
        print("font load failed:", path, exc)
        return terminalio.FONT


NUM_FONT    = _load_font(NUM_FONT_PATH)
STATUS_FONT = _load_font(STATUS_FONT_PATH)

# --- Helpers ----------------------------------------------------------------


def fmt_hhmm(unix_ts):
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


def _clamp01(x):
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


# --- Scene graph ------------------------------------------------------------
root = displayio.Group()
board.DISPLAY.root_group = root

root.append(Rect(0, 0, W, H, fill=C_BG))

# Tachometer image — one TileGrid whose bitmap is swapped per frame.
_tach0 = displayio.OnDiskBitmap(TACH_BMP.format(0))
tach_tile = displayio.TileGrid(_tach0, pixel_shader=_tach0.pixel_shader,
                               x=TACH_X, y=TACH_Y)
root.append(tach_tile)


def set_tach(idx):
    # Each frame has its own palette, so swap shader and bitmap together.
    img = displayio.OnDiskBitmap(TACH_BMP.format(idx))
    tach_tile.bitmap = img
    tach_tile.pixel_shader = img.pixel_shader


# Numeric readout: a dark "88" ghost with the live value drawn on top.
num_dark = Label(NUM_FONT, text="88", color=C_DARK, x=NUM_X, y=NUM_Y)
root.append(num_dark)
_num_digit_w = num_dark.bounding_box[2] // 2   # for right-aligning the live value

num_live = Label(NUM_FONT, text="", color=C_LIGHT, x=NUM_X, y=NUM_Y)
root.append(num_live)

# Bottom-left status line.
status_label = Label(STATUS_FONT, text="", color=C_LIGHT, x=STATUS_X, y=STATUS_Y)
root.append(status_label)

# Bottom-right 7-day utilisation bar.
_bar7d_palette = displayio.Palette(2)
_bar7d_palette[0] = C_DARK
_bar7d_palette[1] = C_LIGHT
bar7d_bitmap = displayio.Bitmap(BAR7D_W, BAR7D_H, 2)
bar7d_bitmap.fill(0)
root.append(displayio.TileGrid(bar7d_bitmap, pixel_shader=_bar7d_palette,
                               x=BAR7D_X, y=BAR7D_Y))

# Centered error overlay.
overlay = Label(STATUS_FONT, text="", color=C_ERROR)
overlay.anchor_point = (0.5, 0.5)
overlay.anchored_position = (W // 2, H // 2)
overlay.hidden = True
root.append(overlay)

# --- Update functions -------------------------------------------------------


def tach_position(ratio):
    # 5H redline_ratio -> continuous gauge position, 0.0 .. TACH_FRAMES-1.
    # Concave below the redline (sensitive at low use), linear above it.
    if not ratio or ratio <= 0:
        return 0.0
    if ratio <= 1.0:
        return REDLINE_FRAME * ratio ** BLUE_EXPONENT
    top = TACH_FRAMES - 1
    over = (ratio - 1.0) / (RED_FULL_RATIO - 1.0)
    return min(top, REDLINE_FRAME + (top - REDLINE_FRAME) * over)


def update_tach(fh):
    pos = tach_position(fh.get("redline_ratio"))
    set_tach(int(round(pos)))
    txt = str(int(round(pos / (TACH_FRAMES - 1) * 99)))
    num_live.text = txt
    num_live.x = NUM_X + (2 - len(txt)) * _num_digit_w


def update_status_line(fh):
    reset_at = fh.get("resets_at_unix") or 0
    burn = fh.get("burn_rate_pct_per_hour") or 0.0
    used = fh.get("used_pct") or 0.0
    parts = []
    if reset_at:
        parts.append("resets " + fmt_hhmm(reset_at))
    if burn > 0:
        parts.append("{:.1f}%/hr".format(burn))
        eta = min(int((100.0 - used) / burn * 3600), 24 * 3600)
        parts.append("→ " + fmt_duration(eta))
    status_label.text = " · ".join(parts)


def update_bar7d(sd):
    used = sd.get("used_pct") or 0.0
    filled = int(round(_clamp01(used / 100.0) * BAR7D_W))
    bar7d_bitmap.fill(0)
    if filled > 0:
        if _HAS_BITMAPTOOLS:
            bitmaptools.fill_region(bar7d_bitmap, 0, 0, filled, BAR7D_H, 1)
        else:
            for y in range(BAR7D_H):
                for x in range(filled):
                    bar7d_bitmap[x, y] = 1


def _apply(data):
    fh = data.get("five_hour") or {}
    sd = data.get("seven_day") or {}
    update_tach(fh)
    update_status_line(fh)
    update_bar7d(sd)


def show_normal(data):
    board.DISPLAY.brightness = 1.0
    overlay.hidden = True
    _apply(data)


def show_stale(data):
    _apply(data)
    overlay.hidden = True
    board.DISPLAY.brightness = 0.4


def show_auth_failed(data):
    _apply(data)
    overlay.text = "NEEDS AUTH"
    overlay.hidden = False
    board.DISPLAY.brightness = 0.3


def show_no_server(detail):
    print("no server:", detail)
    overlay.text = "NO WIFI" if not esp.is_connected else "NO SERVER"
    overlay.hidden = False
    board.DISPLAY.brightness = 0.5


# --- WiFi -------------------------------------------------------------------


def wifi_kickoff():
    """Start a non-blocking join (the ESP was reset at boot)."""
    time.sleep(0.3)
    try:
        esp.wifi_set_passphrase(SSID, PASSWORD)
        print("WiFi: join started for", SSID)
    except Exception as exc:
        print("WiFi: kickoff failed:", exc)


def wifi_finish():
    """Block until connected, re-kicking every ~5s, then open a session."""
    global requests
    tries = 0
    while not esp.is_connected:
        time.sleep(0.5)
        tries += 1
        if tries % 10 == 0:
            print("WiFi: still joining, re-kicking")
            try:
                esp.connect_AP(SSID, PASSWORD)
            except Exception as exc:
                print("WiFi: retry failed:", exc)
    print("WiFi: connected, IP =", esp.pretty_ip(esp.ip_address))
    pool = adafruit_connection_manager.get_radio_socketpool(esp)
    requests = adafruit_requests.Session(pool)


# --- Boot -------------------------------------------------------------------
# Background, tach0 and the dark "88" are already on screen. Join WiFi.
wifi_kickoff()
wifi_finish()

# --- Main loop --------------------------------------------------------------
last_data = {}

while True:
    server_ok = False
    server_error = ""
    try:
        resp = requests.get(SERVER_URL + "/status", timeout=10)
        if resp.status_code == 200:
            last_data = resp.json()
            server_ok = True
        else:
            server_error = "HTTP {}".format(resp.status_code)
        resp.close()
    except Exception as exc:
        server_error = "{}: {}".format(type(exc).__name__, exc)
        print("status fetch failed:", server_error)

    gc.collect()

    if not server_ok:
        show_no_server(server_error)
    elif last_data.get("auth_failed"):
        show_auth_failed(last_data)
    elif last_data.get("stale"):
        show_stale(last_data)
    else:
        show_normal(last_data)

    time.sleep(POLL_SECS)
