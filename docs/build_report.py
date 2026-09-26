# -*- coding: utf-8 -*-
"""
将 CODE_AUDIT_REPORT.md 渲染为自包含（离线可用）的 HTML 阅读版。

用法：
    python docs/build_report.py

产出：
    docs/CODE_AUDIT_REPORT.html   —— 单文件，无任何 CDN 依赖，双击即可打开
"""
from __future__ import annotations

import html
import pathlib
import re
import sys

try:
    import markdown
    from markdown.extensions.toc import TocExtension
except ImportError:  # pragma: no cover
    sys.exit("缺少依赖：pip install markdown pygments")

ROOT = pathlib.Path(__file__).resolve().parent
SRC = ROOT / "CODE_AUDIT_REPORT.md"
OUT = ROOT / "CODE_AUDIT_REPORT.html"

CSS = r"""
:root{
  --bg:#0b0b0d; --bg-panel:#141418; --bg-elev:#1c1c22; --bg-code:#111114;
  --border:#26262e; --border-soft:#1e1e25;
  --text:#e8e8ea; --text-2:#a1a1aa; --text-3:#71717a;
  --accent:#7c9cff; --accent-soft:rgba(124,156,255,.14);
  --p0:#ff5d5d; --p1:#ffa23a; --p2:#ffd84d; --ok:#3ecf8e;
  --mono:"SFMono-Regular",Consolas,"JetBrains Mono","Cascadia Mono","Liberation Mono",Menlo,monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
  --radius:10px;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{
  margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);
  font-size:15px;line-height:1.75;-webkit-font-smoothing:antialiased;
}
.layout{display:grid;grid-template-columns:320px minmax(0,1fr);max-width:1680px;margin:0 auto}
/* ---------- 侧栏 ---------- */
.sidebar{
  position:sticky;top:0;height:100vh;overflow:hidden;display:flex;flex-direction:column;
  border-right:1px solid var(--border);background:var(--bg-panel);
}
.brand{padding:22px 22px 14px;border-bottom:1px solid var(--border-soft)}
.brand h1{margin:0;font-size:15px;letter-spacing:.01em;color:var(--text)}
.brand p{margin:6px 0 0;font-size:12px;color:var(--text-3);font-family:var(--mono)}
.filter{margin:14px 18px 10px;position:relative}
.filter input{
  width:100%;padding:8px 11px;border-radius:8px;border:1px solid var(--border);
  background:var(--bg-elev);color:var(--text);font-size:13px;font-family:var(--sans);outline:none;
}
.filter input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
nav.toc{overflow-y:auto;padding:4px 12px 40px;flex:1;scrollbar-width:thin}
nav.toc::-webkit-scrollbar{width:8px}
nav.toc::-webkit-scrollbar-thumb{background:#2b2b34;border-radius:4px}
nav.toc ul{list-style:none;margin:0;padding:0}
nav.toc li{margin:1px 0}
nav.toc a{
  display:block;padding:5px 10px;border-radius:6px;color:var(--text-2);
  text-decoration:none;font-size:12.5px;line-height:1.5;border-left:2px solid transparent;
}
nav.toc a:hover{background:var(--bg-elev);color:var(--text)}
nav.toc a.active{background:var(--accent-soft);color:var(--text);border-left-color:var(--accent)}
nav.toc ul ul a{padding-left:22px;font-size:12px;color:var(--text-3)}
/* ---------- 正文 ---------- */
.content{padding:52px 64px 120px;min-width:0}
.content h1{
  font-size:27px;line-height:1.35;letter-spacing:-.01em;margin:0 0 8px;
  font-weight:650;
}
.content h1 + h1{font-size:19px;color:var(--text-2);font-weight:500;margin-bottom:26px}
.content h2{
  font-size:21px;margin:58px 0 18px;padding-bottom:10px;font-weight:640;
  border-bottom:1px solid var(--border);letter-spacing:-.005em;
}
.content h3{font-size:16.5px;margin:38px 0 12px;font-weight:640}
.content h3:first-of-type{margin-top:20px}
.content h4{font-size:14.5px;margin:26px 0 10px;color:var(--text-2);font-weight:640}
.content p{margin:11px 0}
.content strong{color:#fff;font-weight:640}
.content a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(124,156,255,.3)}
.content a:hover{border-bottom-color:var(--accent)}
.content ul,.content ol{padding-left:24px;margin:11px 0}
.content li{margin:5px 0}
.content li>ul,.content li>ol{margin:5px 0}
.content hr{border:0;border-top:1px solid var(--border);margin:46px 0}
.content blockquote{
  margin:16px 0;padding:12px 18px;border-left:3px solid var(--accent);
  background:var(--accent-soft);border-radius:0 8px 8px 0;color:var(--text-2);
}
.content blockquote p{margin:4px 0}
/* ---------- 表格 ---------- */
.content table{
  width:100%;border-collapse:collapse;margin:16px 0;font-size:13.5px;
  border:1px solid var(--border);border-radius:8px;overflow:hidden;display:table;
}
.content thead th{
  background:var(--bg-elev);color:var(--text);text-align:left;font-weight:640;
  padding:9px 13px;border-bottom:1px solid var(--border);white-space:nowrap;
}
.content tbody td{padding:8px 13px;border-bottom:1px solid var(--border-soft);vertical-align:top}
.content tbody tr:last-child td{border-bottom:0}
.content tbody tr:nth-child(even){background:rgba(255,255,255,.014)}
.content tbody tr:hover{background:rgba(124,156,255,.05)}
/* ---------- 代码 ---------- */
.content code{
  font-family:var(--mono);font-size:12.6px;background:var(--bg-elev);
  padding:2px 5px;border-radius:5px;color:#ffd9a8;word-break:break-word;
}
.content pre{
  background:var(--bg-code);border:1px solid var(--border);border-radius:var(--radius);
  padding:15px 18px;overflow-x:auto;margin:15px 0;line-height:1.62;
}
.content pre code{background:none;padding:0;font-size:12.5px;color:inherit}
.content pre::-webkit-scrollbar{height:9px}
.content pre::-webkit-scrollbar-thumb{background:#2b2b34;border-radius:5px}
/* 行内联代码在标题/表格里不要撑破 */
.content th code,.content h2 code,.content h3 code,.content h4 code{font-size:12px}
/* ---------- 顶部进度条 ---------- */
#bar{position:fixed;top:0;left:0;height:2px;background:var(--accent);width:0;z-index:50}
@media (max-width:1100px){
  .layout{grid-template-columns:1fr}
  .sidebar{position:relative;height:auto;border-right:0;border-bottom:1px solid var(--border)}
  nav.toc{max-height:46vh}
  .content{padding:32px 22px 90px}
}
@media print{
  .sidebar,#bar{display:none}
  .layout{grid-template-columns:1fr}
  body{background:#fff;color:#000}
}
"""

JS = r"""
(function(){
  var bar=document.getElementById('bar');
  var links=[].slice.call(document.querySelectorAll('nav.toc a'));
  var heads=links.map(function(a){
    var id=decodeURIComponent(a.getAttribute('href').slice(1));
    return document.getElementById(id);
  });
  function onScroll(){
    var top=window.scrollY||document.documentElement.scrollTop;
    var h=document.documentElement.scrollHeight-window.innerHeight;
    bar.style.width=(h>0?(top/h*100):0)+'%';
    var idx=0;
    for(var i=0;i<heads.length;i++){
      if(heads[i] && heads[i].getBoundingClientRect().top<=120) idx=i;
    }
    links.forEach(function(a,i){a.classList.toggle('active',i===idx);});
  }
  window.addEventListener('scroll',onScroll,{passive:true});
  onScroll();
  // TOC 过滤
  var box=document.getElementById('toc-filter');
  var items=[].slice.call(document.querySelectorAll('nav.toc li'));
  if(box){
    box.addEventListener('input',function(){
      var q=box.value.trim().toLowerCase();
      items.forEach(function(li){
        var t=li.querySelector('a');
        if(!t) return;
        var hit=!q||t.textContent.toLowerCase().indexOf(q)>=0;
        if(q) li.style.display=hit?'block':'none';
        else li.style.display='';
      });
      if(!q) items.forEach(function(li){li.style.display='';});
    });
  }
})();
"""


def check_fences(text: str) -> int:
    """代码围栏必须成对，否则渲染会整体错位。"""
    n = len([1 for line in text.splitlines() if line.strip().startswith("```")])
    return n


def main() -> None:
    if not SRC.exists():
        sys.exit(f"找不到源文件：{SRC}")
    text = SRC.read_text(encoding="utf-8")

    fences = check_fences(text)
    if fences % 2:
        sys.exit(f"代码围栏数量为奇数（{fences}），Markdown 结构损坏，已中止渲染")

    md = markdown.Markdown(
        extensions=[
            "extra",              # 含 tables / fenced_code / attr_list / def_list
            "sane_lists",
            TocExtension(toc_depth="2-3", permalink=False, slugify=lambda v, s: re.sub(r"[^\w\u4e00-\u9fff-]+", "-", v).strip("-").lower()),
            "codehilite",         # 需要 pygments
        ],
        extension_configs={
            "codehilite": {"guess_lang": False, "css_class": "hl", "noclasses": False}
        },
        output_format="html5",
    )
    body = md.convert(text)
    toc = md.toc
    # 从 TOC 中剔除内嵌设计文档那一节（它在代码块里，理论上不会进 TOC，这里做保护）
    toc = re.sub(r"<li>\s*<a[^>]*>一、设计系统.*?</li>", "", toc, flags=re.S)

    counts = {
        "P0": len(re.findall(r"\bP0-[A-Z]\d+", text)) ,
        "lines": len(text.splitlines()),
    }

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>埃夫特 ER3-600 仿真系统 · 全维度代码审查报告</title>
<style>{CSS}
/* ---- pygments (深色) ---- */
.hl .hll{{background:#2a2a33}}
.hl .c,.hl .ch,.hl .cm,.hl .c1,.hl .cs{{color:#6b7280;font-style:italic}}
.hl .k,.hl .kc,.hl .kd,.hl .kn,.hl .kp,.hl .kr,.hl .kt{{color:#c792ea}}
.hl .o,.hl .ow{{color:#89ddff}}
.hl .p,.hl .pi{{color:#a1a1aa}}
.hl .n,.hl .na,.hl .nb,.hl .nc,.hl .nd,.hl .ne,.hl .nf,.hl .ni,.hl .nl,.hl .nn,.hl .nt,.hl .nv,.hl .nx,.hl .py,.hl .w{{color:#e8e8ea}}
.hl .s,.hl .s1,.hl .s2,.hl .sb,.hl .sc,.hl .sd,.hl .se,.hl .sh,.hl .si,.hl .sx,.hl .sr,.hl .ss,.hl .sa,.hl .dl{{color:#a8e6a3}}
.hl .m,.hl .mb,.hl .mf,.hl .mh,.hl .mi,.hl .mo,.hl .il{{color:#f8c97c}}
.hl .mi{{color:#f8c97c}}
.hl .gd{{color:#ff8080}} .hl .gi{{color:#8ce99a}}
.hl .gh{{color:#7c9cff}} .hl .gu{{color:#ffa23a;font-weight:600}}
.hl .err{{color:#ff5d5d}}
</style>
</head>
<body>
<div id="bar"></div>
<div class="layout">
  <aside class="sidebar">
    <div class="brand">
      <h1>ER3-600 仿真系统 · 代码审查</h1>
      <p>{counts['lines']} 行 · Vue3 + Flask · 生成于源码快照</p>
    </div>
    <div class="filter"><input id="toc-filter" type="search" placeholder="筛选章节…（如 高危 / 性能 / UI）"></div>
    <nav class="toc">{toc}</nav>
  </aside>
  <main class="content">{body}</main>
</div>
<script>{JS}</script>
</body>
</html>
"""
    OUT.write_text(doc, encoding="utf-8")
    print(f"OK -> {OUT}")
    print(f"   源 {counts['lines']} 行，输出 {OUT.stat().st_size/1024:.0f} KB，分页计数 {counts}")


if __name__ == "__main__":
    main()
