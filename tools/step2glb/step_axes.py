# -*- coding: utf-8 -*-
"""
step_axes.py — 从 STEP 文件中反解各关节旋转轴线

原理: 旋转关节的轴线 = 该处轴承孔/凸台圆的轴线。
STEP AP214 中每个圆以 CIRCLE 实体记录，其定位由 AXIS2_PLACEMENT_3D 给出
（含圆心 CARTESIAN_POINT 与法向 DIRECTION）。把大量共轴圆聚类，即可
精确还原每个关节的轴线位置与方向，无需任何猜测。

用法: python step_axes.py <file.step> [最小半径mm]
"""
import re
import sys
import math
from collections import defaultdict

ENT_RE = re.compile(r'^#(\d+)\s*=\s*([A-Z_0-9]+)\s*\((.*)\)\s*;?\s*$', re.S)
NUM_RE = re.compile(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?')


def parse(path):
    """流式解析 STEP DATA 段，返回 {id: (type, body)}"""
    ents = {}
    buf = ""
    with open(path, "r", encoding="latin-1") as f:
        in_data = False
        for line in f:
            if not in_data:
                if line.startswith("DATA;"):
                    in_data = True
                continue
            if line.startswith("ENDSEC;"):
                break
            buf += line
            # 实体以 ';' 结束
            while ";" in buf:
                chunk, buf = buf.split(";", 1)
                chunk = chunk.strip()
                if not chunk:
                    continue
                m = ENT_RE.match(chunk + ";")
                if m:
                    ents[int(m.group(1))] = (m.group(2), m.group(3))
    return ents


def parse_point(body):
    nums = [float(x) for x in NUM_RE.findall(body)]
    return nums[:3] if len(nums) >= 3 else None


def resolve_point(ents, ref, depth=0):
    if depth > 6:
        return None
    e = ents.get(ref)
    if not e:
        return None
    t, body = e
    if t == "CARTESIAN_POINT":
        return parse_point(body)
    # 间接引用
    for m in NUM_RE.finditer(body):
        pass
    ids = [int(x) for x in re.findall(r'#(\d+)', body)]
    for i in ids:
        p = resolve_point(ents, i, depth + 1)
        if p:
            return p
    return None


def resolve_dir(ents, ref, depth=0):
    if depth > 6:
        return None
    e = ents.get(ref)
    if not e:
        return None
    t, body = e
    if t == "DIRECTION":
        v = parse_point(body)
        return v
    ids = [int(x) for x in re.findall(r'#(\d+)', body)]
    for i in ids:
        v = resolve_dir(ents, i, depth + 1)
        if v:
            return v
    return None


def normalize(v):
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return [c / n for c in v] if n > 1e-12 else [0.0, 0.0, 1.0]


def main():
    path = sys.argv[1]
    min_r = float(sys.argv[2]) if len(sys.argv) > 2 else 12.0

    print(f"解析 {path} ...")
    ents = parse(path)
    print(f"  实体总数 = {len(ents)}")

    # 建立 AXIS2_PLACEMENT_3D 的 id -> (loc_point_ref, axis_dir_ref)
    a2p = {}
    for i, (t, body) in ents.items():
        if t == "AXIS2_PLACEMENT_3D":
            ids = [int(x) for x in re.findall(r'#(\d+)', body)]
            if len(ids) >= 2:
                a2p[i] = (ids[0], ids[1])

    circles = []
    for i, (t, body) in ents.items():
        if t != "CIRCLE":
            continue
        ids = [int(x) for x in re.findall(r'#(\d+)', body)]
        if not ids:
            continue
        nums = NUM_RE.findall(body)
        radius = float(nums[-1]) if nums else None
        if radius is None or radius < min_r:
            continue
        pl = a2p.get(ids[0])
        if not pl:
            continue
        c = resolve_point(ents, pl[0])
        d = resolve_dir(ents, pl[1])
        if not c or not d:
            continue
        circles.append({"id": i, "c": c, "d": normalize(d), "r": radius})

    print(f"  CIRCLE(半径>={min_r}mm) 数量 = {len(circles)}")

    # ── 轴线聚类: 方向相近 且 圆心在同一直线上 ──────────────────
    def key_dir(v):
        v = normalize(v)
        # 归一化到半球，避免正负方向分裂
        for c in v:
            if abs(c) > 1e-9:
                if c < 0:
                    v = [-x for x in v]
                break
        return tuple(round(x, 2) for x in v)

    by_dir = defaultdict(list)
    for cir in circles:
        by_dir[key_dir(cir["d"])].append(cir)

    print("\n--- 按方向分组的圆 (方向 | 圆数 | 半径范围) ---")
    for d, lst in sorted(by_dir.items(), key=lambda kv: -len(kv[1])):
        rs = [c["r"] for c in lst]
        print(f"  ({d[0]:+.2f},{d[1]:+.2f},{d[2]:+.2f})  n={len(lst):4d}  r=[{min(rs):.1f},{max(rs):.1f}]")

    # ── 对每个主轴方向，把圆心投影到与方向垂直的平面，做二维聚类 ──
    print("\n--- 共轴圆簇 -> 候选关节轴线 ---")
    axes = []
    for d, lst in sorted(by_dir.items(), key=lambda kv: -len(kv[1])):
        if len(lst) < 3:
            continue
        dv = list(d)
        # 取两个与 dv 正交的基向量
        tmp = [1.0, 0.0, 0.0] if abs(dv[0]) < 0.9 else [0.0, 1.0, 0.0]
        u = normalize([
            dv[1] * tmp[2] - dv[2] * tmp[1],
            dv[2] * tmp[0] - dv[0] * tmp[2],
            dv[0] * tmp[1] - dv[1] * tmp[0],
        ])
        w = [
            dv[1] * u[2] - dv[2] * u[1],
            dv[2] * u[0] - dv[0] * u[2],
            dv[0] * u[1] - dv[1] * u[0],
        ]
        bins = defaultdict(list)
        for c in lst:
            p = c["c"]
            pu = p[0] * u[0] + p[1] * u[1] + p[2] * u[2]
            pw = p[0] * w[0] + p[1] * w[1] + p[2] * w[2]
            bins[(round(pu / 3.0), round(pw / 3.0))].append((c, pu, pw))
        clusters = sorted(bins.values(), key=len, reverse=True)
        for cl in clusters:
            if len(cl) < 3:
                continue
            mean_u = sum(x[1] for x in cl) / len(cl)
            mean_w = sum(x[2] for x in cl) / len(cl)
            pt = [u[k] * mean_u + w[k] * mean_w for k in range(3)]
            rs = [x[0]["r"] for x in cl]
            # 轴线上的圆在 dv 上的跨度（决定轴段长度）
            span = []
            for x in cl:
                p = x[0]["c"]
                span.append(p[0] * dv[0] + p[1] * dv[1] + p[2] * dv[2])
            axes.append({
                "dir": dv, "pt": pt, "n": len(cl),
                "rmax": max(rs), "rmin": min(rs),
                "t0": min(span), "t1": max(span),
            })

    axes.sort(key=lambda a: -a["n"])
    print(f"\n{'方向 (i,j,k)':<26} {'轴上一点 (mm)':<34} {'圆数':>4} {'r范围':>12} {'轴向跨度':>16}")
    print("-" * 110)
    for a in axes:
        d = a["dir"]; p = a["pt"]
        print(
            f"({d[0]:+.3f},{d[1]:+.3f},{d[2]:+.3f})".ljust(26) +
            f"({p[0]:8.2f},{p[1]:8.2f},{p[2]:8.2f})".ljust(34) +
            f"{a['n']:>4} " +
            f"[{a['rmin']:5.1f},{a['rmax']:5.1f}]".rjust(12) +
            f"  [{a['t0']:7.1f},{a['t1']:7.1f}]".rjust(16)
        )


if __name__ == "__main__":
    main()
