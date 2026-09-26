# -*- coding: utf-8 -*-
"""
ransac_circles.py — 用 RANSAC 在网格顶点中检测圆特征，反解关节轴线

思路: 把顶点投影到与候选轴方向垂直的平面上，用 RANSAC 拟合半径 8~110mm 的圆。
    若某连杆在多个半径上出现「同心圆」，该圆心即是它绕该方向的旋转轴线上的一点。
    （轴承孔/凸台/螺栓圈在 CAD 网格里必然留下精确的圆轮廓）

用法: python ransac_circles.py [meshes.json]
"""
import json
import math
import os
import random
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MESHES = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "build", "meshes.json")

LINK_NAMES = {
    6: "基座",
    7: "转座",
    8: "大臂",
    9: "电机座",
    10: "手腕体",
    11: "手腕",
    12: "法兰",
}
AXES = {"X": np.array([1.0, 0.0, 0.0]), "Y": np.array([0.0, 1.0, 0.0]), "Z": np.array([0.0, 0.0, 1.0])}

R_MIN, R_MAX = 8.0, 110.0
TOL = 0.6          # 内点容差 mm
MIN_INLIERS = 28   # 认定为「真实圆轮廓」所需最少顶点
ITERS = 9000
random.seed(20260926)
np.random.seed(20260926)


def basis(d):
    d = d / np.linalg.norm(d)
    tmp = np.array([1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(d, tmp)
    u /= np.linalg.norm(u)
    return u, np.cross(d, u)


def circle_from_3(p1, p2, p3):
    ax, ay = p1; bx, by = p2; cx, cy = p3
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return None
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    return ux, uy, r


def find_circles(q, max_circles=10):
    """q: (N,2)。返回 [(cx, cy, r, n_inliers, inlier_idx), ...]"""
    q = np.asarray(q, dtype=np.float64)
    n = len(q)
    found = []
    alive = np.ones(n, dtype=bool)
    for _ in range(max_circles):
        idx_alive = np.nonzero(alive)[0]
        if len(idx_alive) < MIN_INLIERS:
            break
        Q = q[idx_alive]
        best = None
        for _it in range(ITERS):
            s = np.random.choice(len(Q), 3, replace=False)
            c = circle_from_3(tuple(Q[s[0]]), tuple(Q[s[1]]), tuple(Q[s[2]]))
            if c is None:
                continue
            ux, uy, r = c
            if not (R_MIN <= r <= R_MAX):
                continue
            dist = np.abs(np.hypot(Q[:, 0] - ux, Q[:, 1] - uy) - r)
            m = dist < TOL
            cnt = int(m.sum())
            if best is None or cnt > best[3]:
                best = (ux, uy, r, cnt, m)
                if cnt > 0.6 * len(Q):
                    break
        if best is None or best[3] < MIN_INLIERS:
            break
        ux, uy, r, cnt, m = best
        # 精修：用全部内点重新最小二乘
        P = Q[m]
        for _ in range(6):
            d = np.hypot(P[:, 0] - ux, P[:, 1] - uy)
            r = d.mean()
            # 代数最小二乘圆拟合（Kasa）
            A = np.column_stack([2 * P[:, 0], 2 * P[:, 1], np.ones(len(P))])
            b = P[:, 0] ** 2 + P[:, 1] ** 2
            try:
                sol, *_ = np.linalg.lstsq(A, b, rcond=None)
            except Exception:
                break
            nx, ny = sol[0], sol[1]
            nr = math.sqrt(sol[2] + nx * nx + ny * ny)
            newm = np.abs(np.hypot(Q[:, 0] - nx, Q[:, 1] - ny) - nr) < TOL
            if newm.sum() < MIN_INLIERS:
                break
            ux, uy, r = nx, ny, nr
            if np.array_equal(newm, m):
                m = newm
                break
            m = newm
            P = Q[m]
        gi = idx_alive[m]
        found.append((ux, uy, r, len(gi), gi))
        alive[gi] = False
    return found


def main():
    with open(MESHES, "r", encoding="utf-8") as f:
        data = json.load(f)

    for m in data["meshes"]:
        i = m["i"]
        if i not in LINK_NAMES or not m.get("positions"):
            continue
        P = np.asarray(m["positions"], dtype=np.float64).reshape(-1, 3)
        print("\n" + "=" * 100)
        print(f"[{i}] {LINK_NAMES[i]}   顶点={len(P)}   bbox min={np.round(P.min(axis=0),1)} max={np.round(P.max(axis=0),1)}")
        print("=" * 100)
        for dname, d in AXES.items():
            u, w = basis(d)
            q = np.column_stack([P @ u, P @ w])
            t = P @ d
            found = find_circles(q)
            if not found:
                continue
            print(f"  ── 方向 {dname}  检出 {len(found)} 个圆轮廓")
            for ux, uy, r, cnt, gi in sorted(found, key=lambda z: -z[3]):
                world = ux * u + uy * w
                ts = t[gi]
                print(f"      圆心=({world[0]:8.2f},{world[1]:8.2f},{world[2]:8.2f})"
                      f"  R={r:7.2f}  点数={cnt:5d}  轴向 t=[{ts.min():7.1f},{ts.max():7.1f}]")
            # 同心聚类：同一圆心出现 >=2 个不同半径 => 强关节轴证据
            cl = []
            for ux, uy, r, cnt, gi in sorted(found, key=lambda z: -z[3]):
                placed = False
                for c in cl:
                    if math.hypot(c["x"] - ux, c["y"] - uy) < 2.5:
                        c["rs"].append(r); placed = True; break
                if not placed:
                    cl.append({"x": ux, "y": uy, "rs": [r]})
            strong = [c for c in cl if len(c["rs"]) >= 2]
            if strong:
                print(f"     ★ 同心圆簇（关节轴强证据）:")
                for c in strong:
                    world = c["x"] * u + c["y"] * w
                    print(f"       轴线上点=({world[0]:8.2f},{world[1]:8.2f},{world[2]:8.2f})"
                          f"  半径组={[round(x,1) for x in sorted(c['rs'])]}")


if __name__ == "__main__":
    main()
