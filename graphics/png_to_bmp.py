#!/usr/bin/env python3
"""Convert the tachometer PNG exports to indexed BMP.

The PyPortal's CircuitPython build can't decode PNG via OnDiskBitmap, but
BMP works on every displayio build. Re-run this after re-exporting the
gauge from Affinity.

Usage:  python3 graphics/png_to_bmp.py
"""

import glob
import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "png")
DST = os.path.join(os.path.dirname(HERE), "pyportal", "bmp")

os.makedirs(DST, exist_ok=True)

count = 0
for src in sorted(glob.glob(os.path.join(SRC, "*.png"))):
    dst = os.path.join(DST, os.path.basename(src)[:-4] + ".bmp")
    Image.open(src).save(dst, "BMP")
    count += 1
    print(os.path.basename(src), "->", os.path.basename(dst))

print(f"Converted {count} file(s) to {DST}")
