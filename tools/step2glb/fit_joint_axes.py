# -*- coding: utf-8 -*-
"""
fit_joint_axes.py — 从 occt 已变换到全局坐标的网格中，数值反解各个关节的轴线

原理: 旋转关节处必然有圆柱配合面（轴承孔/凸台）。对每个连杆的网格，
沿某一候选方向切片，在每一层薄片内做「圆拟合」(least_squares)，
残差极小的层即说明该处存在绕该方向的圆柱面 —— 其圆心就是轴线上的点。

输出一张「连杆 x 方向 x 切片」的圆柱特征地图，用于客观判定：
  每个连杆绕哪个轴旋转、轴线在哪、以及各关节的杆长。

用法: python fit_joint_axes.py [meshes.json]
"""
import json
import math
import os
import sys

import numpy as np
from scipy.optimize import least_squares

HERE = os.path.dirname(os.path.abspath(__file__))
MESHES = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "build", "meshes.json")

# 已知部件名 -> occt 网格序号（由 dump_meshes.js 得到）
LINK_NAMES = {
    6: "基座(base)",
    7: "转座(J1)",
    8: "大臂(J2)",
    9: "电机座(J3?)",
    10: "手腕体(小臂/J4?)",
    11: "手腕(J5?)",
    12: "法兰(J6/TCP)",
    13: "运动范围(排除)",
}

AXES = {
    "X": np.array([1.0, 0.0, 0.0]),
    "Y": np.array([0.0, 1.0, 0.0]),
    "Z": np.array([0.0, 0.0, 1.0]),
}


def basis(d):
    """返回与 d 正交的两个单位向量 u, w"""
    d = d / np.linalg.norm(d)
    tmp = np.array([1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(d, tmp)
    u /= np.linalg.norm(u)
    w = np.cross(d, u)
    return u, w


def fit_circle_2d(q):
    """q: (N,2) 二维点。返回 (center(2,), radius, rms)"""
    c0 = q.mean(axis=0)
    r0 = np.linalg.norm(q - c0, axis=1).mean()

    def resid(p):
        return np.linalg.norm(q - p[:2], axis=1) - p[2]

    # 初值扰动几次取最优，避免局部极小
    best = None
    for pert in [(0, 0), (3, 0), (-3, 0), (0, 3), (0, -3), (6, 6), (-6, -6)]:
        p0 = np.array([c0[0] + pert[0], c0[1] + pert[1], r0])
        try:
            r = least_squares(resid, p0, method="lm", max_nfev=2000)
        except Exception:
            continue
        rms = math.sqrt(float(np.mean(r.fun ** 2)))
        if best is None or rms < best[2]:
            best = (r.center if False else (r.x[:2], r.x[2], rms))
    if best is None:
        return c0, r0, 1e9
    return best[0], best[1], best[2]


def analyse(P, dname, slab=16.0, min_pts=60, step=14.0):
    """沿 dname 方向切片，每层做圆拟合。返回 [(t_center, center2d_world, r, rms, n), ...]"""
    d = AXES[dname]
    u, w = basis(d)
    t = P @ d
    qu = P @ u
    qw = P @ w
    lo, hi = t.min(), t.max()
    out = []
    if hi - lo < slab:
        return out
    n = int((hi - lo - slab) // step) + 1
    for k in range(n):
        a = lo + k * step
        b = a + slab
        m = (t >= a) & (t < b)
        cnt = int(m.sum())
        if cnt < min_pts:
            continue
        q = np.column_stack([qu[m], qw[m]])
        c, r, rms = fit_circle_2d(q)
        # 圆周覆盖率：把角度分成 24 格，看有多少格有点（完整圆=1.0）
        ang = np.arctan2(q[:, 1] - c[1], q[:, 0] - c[0])
        cover = len(np.unique(((ang + math.pi) / (2 * math.pi) * 24).astype(int))) / 24.0
        world = c[0] * u + c[1] * w
        out.append(((a + b) / 2.0, world, r, rms, cnt, cover))
    return out


def main():
    print(f"读取 {MESHES}")
    with open(MESHES, "r", encoding="utf-8") as f:
        data = json.load(f)

    for m in data["meshes"]:
        i = m["i"]
        if not m.get("positions"):
            continue
        P = np.asarray(m["positions"], dtype=np.float64).reshape(-1, 3)
        label = LINK_NAMES.get(i, m.get("name") or f"mesh{i}")
        print("\n" + "=" * 96)
        print(f"[{i}] {label}   顶点={len(P)}   包围盒 min={np.round(P.min(axis=0),1)} max={np.round(P.max(axis=0),1)}")
        print("=" * 96)

        # 先给出整体主成分，帮助确认长轴
        C = P - P.mean(axis=0)
        ev, evec = np.linalg.eigh(C.T @ C / len(P))
        order = np.argsort(ev)[::-1]
        print("  主成分方向 (按特征值降序): " +
              "  ".join(f"{np.round(evec[:, j],3)} (σ={math.sqrt(max(ev[j],0)):.1f}mm)" for j in order))

        for dname in ["X", "Y", "Z"]:
            rows = analyse(P, dname)
            # 只保留「接近完整圆 且 残差小」的层：这才是真正的圆柱配合面
            good = [r for r in rows if r[5] >= 0.6 and r[3] <= max(1.2, 0.06 * r[2])]
            if not good:
                continue
            print(f"  ── 方向 {dname}：发现 {len(good)} 层圆柱特征（覆盖率≥60%，残差≤6%R）")
            for t, wpos, r, rms, cnt, cover in good[:14]:
                print(f"        t={t:8.1f}  轴线上点=({wpos[0]:8.2f},{wpos[1]:8.2f},{wpos[2]:8.2f})"
                      f"  R={r:7.2f}  残差={rms:6.3f}  点数={cnt:5d}  覆盖={cover*100:3.0f}%")


if __name__ == "__main__":
    main()
