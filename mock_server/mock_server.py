#!/usr/bin/env python3
"""
Minimal mock server for PyPortal development.
Serves /status and /history with realistic fake data.

Usage:
    python3 mock_server.py [port]   (default port: 7654)
"""

import json
import math
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 7654

# ---------------------------------------------------------------------------
# Fake data generators
# ---------------------------------------------------------------------------

# History pattern: simulate a workday — quiet overnight, ramping up mid-morning,
# peak in the afternoon, tapering off in the evening.
_HOUR_SHAPE = [
    5, 4, 3, 3, 4, 6,       # 00–05  overnight
    10, 18, 32, 48, 60, 72, # 06–11  morning ramp
    78, 85, 88, 82, 75, 68, # 12–17  afternoon peak
    55, 42, 30, 20, 12, 7,  # 18–23  evening wind-down
]


def _history_buckets(hours: int) -> list:
    now = int(time.time())
    hour_start = (now // 3600) * 3600
    start_ts = hour_start - (hours - 1) * 3600

    buckets = []
    for i in range(hours):
        h_ts = start_ts + i * 3600
        # map bucket index to hour-of-day using shape table, add mild noise
        hour_of_day = (h_ts // 3600) % 24
        base = _HOUR_SHAPE[hour_of_day]
        # add deterministic jitter so it looks natural but is stable across polls
        jitter = (math.sin(h_ts * 0.0003 + i) * 12)
        peak = max(0.0, min(100.0, base + jitter))
        # leave a few buckets empty to test the no-data path
        if i % 11 == 3:
            peak = None
        buckets.append({"hour_unix": h_ts, "five_hour_peak": peak})
    return buckets


def _status() -> dict:
    now = int(time.time())
    # Oscillate 5H usage between 40–85% on a ~4-minute cycle so you can watch
    # the bar and color change without restarting the server.
    cycle = (now % 240) / 240.0          # 0.0 → 1.0 over 4 minutes
    pct_5h = 40.0 + 45.0 * abs(math.sin(cycle * math.pi))

    # 5H resets ~2 hours out; fixed burn rate so the redline tracks pct_5h.
    resets_at = now + 2 * 3600
    burn = 8.5  # %/hr
    sustainable_5h = (100.0 - pct_5h) / ((resets_at - now) / 3600)
    redline_5h = min(burn / sustainable_5h, 10.0) if sustainable_5h > 0 else 10.0

    # 7-day window: burn oscillates around its sustainable rate on a slower
    # ~6-minute cycle, so the redline indicator can be watched crossing 1.0.
    seven_pct = 38.2
    seven_resets_at = now + 5 * 24 * 3600
    seven_sustainable = (100.0 - seven_pct) / ((seven_resets_at - now) / 3600)
    seven_cycle = (now % 360) / 360.0
    seven_burn = seven_sustainable * (0.2 + 1.6 * abs(math.sin(seven_cycle * math.pi)))
    seven_redline_ratio = min(seven_burn / seven_sustainable, 10.0)

    return {
        "five_hour": {
            "used_pct": round(pct_5h, 1),
            "resets_at_unix": resets_at,
            "burn_rate_pct_per_hour": burn,
            "sustainable_pct_per_hour": round(sustainable_5h, 3),
            "redline_ratio": round(redline_5h, 2),
        },
        "seven_day": {
            "used_pct": seven_pct,
            "resets_at_unix": seven_resets_at,
            "burn_rate_pct_per_hour": round(seven_burn, 3),
            "sustainable_pct_per_hour": round(seven_sustainable, 3),
            "redline_ratio": round(seven_redline_ratio, 2),
        },
        "seven_day_opus": None,
        "stale": False,
        "auth_failed": False,
        "last_update_unix": now,
        "server_now_unix": now,
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} {fmt % args}")

    def _send_json(self, obj, status=200):
        body = json.dumps(obj, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == "/health":
            self._send_json({"ok": True})

        elif parsed.path == "/status":
            self._send_json(_status())

        elif parsed.path == "/history":
            hours = int(qs.get("hours", ["24"])[0])
            hours = max(1, min(168, hours))
            self._send_json({
                "buckets": _history_buckets(hours),
                "server_now_unix": int(time.time()),
            })

        else:
            self._send_json({"error": "not found"}, status=404)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Mock server listening on http://0.0.0.0:{PORT}")
    print(f"  /status   — 5H usage + redline oscillate on a 4-minute cycle;")
    print(f"              7D redline_ratio oscillates 0.2–1.8 on a 6-minute cycle")
    print(f"  /history  — 24h workday-shaped histogram with a few empty buckets")
    print()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
