# -*- coding: utf-8 -*-
"""crop_pdf.py — 把 PDF 指定页的指定区域高倍渲染并裁切（用于读图纸上的角度箭头）

用法: python crop_pdf.py <pdf> <page_1based> <out.png> <x0> <y0> <x1> <y1> [dpi]
      x0..y1 为页面相对比例 (0~1)
"""
import sys

import fitz
from PIL import Image

pdf, page_no, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
x0, y0, x1, y1 = (float(v) for v in sys.argv[4:8])
dpi = int(sys.argv[8]) if len(sys.argv) > 8 else 400

doc = fitz.open(pdf)
page = doc[page_no - 1]
r = page.rect
clip = fitz.Rect(r.x0 + (r.x1 - r.x0) * x0, r.y0 + (r.y1 - r.y0) * y0,
                 r.x0 + (r.x1 - r.x0) * x1, r.y0 + (r.y1 - r.y0) * y1)
pix = page.get_pixmap(dpi=dpi, clip=clip)
img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
img.save(out)
print(f"{out}  {img.width}x{img.height}")
