# -*- coding: utf-8 -*-
"""
cyl_axes.py — 从 STEP 的 CYLINDRICAL_SURFACE 实体反解关节轴线（最权威来源）

圆柱面实体自带定位坐标系 AXIS2_PLACEMENT_3D，直接给出「轴线上一点 + 轴向 + 半径」，
无需拟合、无需猜测。把同一部件内共线的圆柱面聚类，
圆柱面数量最多的共线簇即为该部件的旋转关节轴线。

前置: 先运行 step_assembly.py 生成 build/assembly.json

用法: python cyl_axes.py <file.step> [最小半径mm]
"""
import json
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import step_assembly as SA  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
LINK_NAMES = {6: "基座", 7: "转座(J1)", 8: "大臂(J2)", 9: "电机座(J3)",
              10: "手腕体(J4)", 11: "手腕(J5)", 12: "法兰(J6)", 13: "运动范围"}


def main():
    step = sys.argv[1]
    min_r = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

    asm_path = os.path.join(HERE, "build", "assembly.json")
    idx_path = os.path.join(HERE, "build", "meshes_index.json")
    with open(asm_path, "r", encoding="utf-8") as f:
        asm = json.load(f)
    with open(idx_path, "r", encoding="utf-8") as f:
        idx = json.load(f)

    bbox = {}
    for m in idx["meshes"]:
        if m.get("bbox"):
            bbox[m["i"]] = (np.array(m["bbox"]["min"]), np.array(m["bbox"]["max"]))
    vol = {mi: float(np.prod(hi - lo)) for mi, (lo, hi) in bbox.items()}

    M_list = [np.array(p["matrix"], dtype=float) for p in asm["parts"]]

    ents = SA.parse_entities(step)
    print(f"实体总数 = {len(ents)}")

    a2p_cache = {}

    def placement(aid):
        if aid in a2p_cache:
            return a2p_cache[aid]
        e = ents.get(aid)
        res = None
        if e and e[0] == "AXIS2_PLACEMENT_3D":
            r = SA.refs(e[1])
            if len(r) >= 2:
                pe, ze = ents.get(r[0]), ents.get(r[1])
                if pe and ze:
                    p = np.array(SA.nums(pe[1])[:3], dtype=float)
                    z = np.array(SA.nums(ze[1])[:3], dtype=float)
                    if len(p) == 3 and len(z) == 3:
                        n = np.linalg.norm(z)
                        res = (p, z / n if n > 1e-12 else z)
        a2p_cache[aid] = res
        return res

    cyls = []
    for i, (t, body) in ents.items():
        if t != "CYLINDRICAL_SURFACE":
            continue
        r = SA.refs(body)
        if not r:
            continue
        n = SA.nums(body)
        if not n:
            continue
        radius = n[-1]
        if radius < min_r or radius > 400:
            continue
        pl = placement(r[0])
        if pl is None:
            continue
        cyls.append({"id": i, "p": pl[0], "d": pl[1], "r": radius})

    print(f"CYLINDRICAL_SURFACE(半径 {min_r}~400mm) = {len(cyls)}")

    # ── 归属部件 ──────────────────────────────────────────────
    assigned = []
    for cy in cyls:
        best = None
        for M in M_list:
            gp = (M[:3, :3] @ cy["p"]) + M[:3, 3]
            gd = M[:3, :3] @ cy["d"]
            gd = gd / (np.linalg.norm(gd) or 1.0)
            for mi, (lo, hi) in bbox.items():
                if np.all(gp >= lo - 5.0) and np.all(gp <= hi + 5.0):
                    if best is None or vol[mi] < best[0]:
                        best = (vol[mi], mi, gp, gd)
        if best is not None:
            assigned.append({"mesh": best[1], "p": best[2], "d": best[3], "r": cy["r"]})
    print(f"成功归属 = {len(assigned)}")

    # ── 按部件 + 轴向分组，再按共线聚类 ────────────────────────
    print("\n" + "=" * 100)
    for mi in sorted(bbox):
        if mi not in LINK_NAMES:
            continue
        sub = [a for a in assigned if a["mesh"] == mi]
        if not sub:
            continue
        groups = defaultdict(list)
        for a in sub:
            ad = np.abs(np.round(a["d"], 2))
            key = "X" if ad[0] > 0.98 else ("Y" if ad[1] > 0.98 else ("Z" if ad[2] > 0.98 else "obl"))
            groups[(key, tuple(round(float(x), 3) for x in a["d"]))].append(a)

        print(f"\n[{mi}] {LINK_NAMES[mi]}   圆柱面数 = {len(sub)}")
        for (key, dvec), lst in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            if len(lst) < 2:
                continue
            clusters = []
            for a in sorted(lst, key=lambda z: -z["r"]):
                hit = None
                for cl in clusters:
                    v = a["p"] - cl["p"]
                    perp = v - np.dot(v, np.array(cl["d"])) * np.array(cl["d"])
                    if np.linalg.norm(perp) < 1.5:
                        hit = cl
                        break
                if hit:
                    hit["rs"].append(round(a["r"], 2))
                else:
                    clusters.append({"p": a["p"], "d": a["d"], "rs": [round(a["r"], 2)]})
            clusters.sort(key=lambda c: -len(set(c["rs"])))
            for cl in clusters:
                rs = sorted(set(cl["rs"]))
                if len(rs) < 2:
                    continue
                p = cl["p"]
                print(f"    ★ 共轴圆柱面 {len(rs)} 个  轴线上点=({p[0]:8.2f},{p[1]:8.2f},{p[2]:8.2f})"
                      f"  方向=({cl['d'][0]:+.3f},{cl['d'][1]:+.3f},{cl['d'][2]:+.3f})")
                print(f"        R = {rs}")


if __name__ == "__main__":
    main()
