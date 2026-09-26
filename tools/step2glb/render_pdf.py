# -*- coding: utf-8 -*-
"""render_pdf.py — 用 PyMuPDF 把 PDF 逐页渲染成 PNG（供多模态读图核对官方参数表）

用法: python render_pdf.py <pdf> <out_dir> [dpi]
"""
import os
import sys

import fitz

pdf = sys.argv[1]
out_dir = sys.argv[2]
dpi = int(sys.argv[3]) if len(sys.argv) > 3 else 150
os.makedirs(out_dir, exist_ok=True)

doc = fitz.open(pdf)
print(f"{pdf}: {doc.page_count} 页")
for i, page in enumerate(doc):
    pix = page.get_pixmap(dpi=dpi)
    p = os.path.join(out_dir, f"page{i + 1}_{pix.width}x{pix.height}.png")
    pix.save(p)
    print(f"  -> {p}  ({os.path.getsize(p) // 1024} KB)")
    # 同时导出纯文本，便于快速核对
    txt = page.get_text("text")
    if txt.strip():
        tp = os.path.join(out_dir, f"page{i + 1}.txt")
        with open(tp, "w", encoding="utf-8") as fp:
            fp.write(txt)
        print(f"     文本 {len(txt)} 字符 -> {tp}")
