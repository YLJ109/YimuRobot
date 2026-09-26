# -*- coding: utf-8 -*-
"""
fit_joint_signs.py — 用官方《运动范围图》标注的包络尺寸反解各关节的旋转正方向。

原理：转动方向不同 → 同一软限位区间扫出的工作空间包络不同。
官方图纸（ER3-600_workspace_V1.1）在侧视图上标注了从 J1 轴线起算的极值：
    +Z 向上 592.8   +X 向右 592.8   -X 向左 591.6
    以及 506.43 / 310.3 / 206.1 / 161.6 / 142.3 / 28.9 / 269.8 / 256.9 / 81.7
把 (±J2, ±J3) 四种符号组合各扫一遍包络，最贴近标注的那组即为真实正方向。
"""
from __future__ import annotations

import itertools
import math

D1, L2, E3, L4, TOOL = 367.5, 295.0, 37.0, 295.5, 78.5
W = math.hypot(E3, L4)
PHI = math.degrees(math.atan2(E3, L4))          # 7.1367°

LIM_J2 = (-135.0, 85.0)
LIM_J3 = (-65.0, 185.0)


def wrist(sj2: int, sj3: int, q2: float, q3: float):
    """腕心在基座系（Z-up, mm）的坐标；q2/q3 为官方关节角，sj* 为待定的转向符号"""
    t2 = math.radians(sj2 * q2)
    t3 = math.radians(sj3 * q3 - PHI)           # τ = ±q3 − φ
    # w = (0,0,L2) + W·(cos τ, 0, −sin τ)  —— 在 J2 局部系
    wx = W * math.cos(t3)
    wz = L2 - W * math.sin(t3)
    # 再绕 J2 的 Y 轴转 t2
    x = wx * math.cos(t2) + wz * math.sin(t2)
    z = -wx * math.sin(t2) + wz * math.cos(t2)
    return x, z                                     # y ≡ 0（侧视图平面）


def flange(sj2: int, sj3: int, q2: float, q3: float, q5: float = 0.0):
    """法兰面中心（含 78.5 工具长度）。q4=q6=0 时法兰法向在侧视平面内"""
    xw, zw = wrist(sj2, sj3, q2, q3)
    t2 = math.radians(sj2 * q2)
    t3 = math.radians(sj3 * q3 - PHI)
    # 法兰法向 = M[:,0]，q4=q5=q6=0 时 = Ry(sj2·q2 + sj3·q3)·(1,0,0)
    tot = math.radians(sj2 * q2 + sj3 * q3)
    nx, nz = math.cos(tot), -math.sin(tot)
    return xw + TOOL * nx, zw + TOOL * nz


def envelope(sj2: int, sj3: int):
    step2, step3 = 0.25, 0.25
    n2 = int((LIM_J2[1] - LIM_J2[0]) / step2) + 1
    n3 = int((LIM_J3[1] - LIM_J3[0]) / step3) + 1
    xs, zs = [], []
    for i in range(n2):
        q2 = LIM_J2[0] + i * step2
        for j in range(n3):
            q3 = LIM_J3[0] + j * step3
            x, z = wrist(sj2, sj3, q2, q3)
            xs.append(x)
            zs.append(z)
    return xs, zs


TARGET = {"max_z": 592.8, "max_x": 592.8, "min_x": -591.6}
# 官方图纸上其它从 J1 轴线起算的极值（左半侧的可达半径）
TARGET_EXTRA = [506.43, 310.3, 206.1, 161.6, 142.3, 28.9, 269.8, 256.9, 81.7]


def main() -> None:
    print(f"W = |J3→腕心| = {W:.4f}   φ = {PHI:.4f}°   满展 = L2 + W = {L2 + W:.4f}")
    print()
    hdr = f"{'符号 J2':>7} {'符号 J3':>7} | {'max z':>9} {'max x':>9} {'min x':>9} | 偏差"
    print(hdr)
    print("-" * len(hdr))
    best = None
    for sj2, sj3 in itertools.product((1, -1), (1, -1)):
        xs, zs = envelope(sj2, sj3)
        mx, mnx, mz = max(xs), min(xs), max(zs)
        err = (abs(mz - TARGET["max_z"]) + abs(mx - TARGET["max_x"])
               + abs(mnx - TARGET["min_x"]))
        tag = f"J2{'+' if sj2 > 0 else '-'} J3{'+' if sj3 > 0 else '-'}"
        print(f"{tag:>16} | {mz:9.2f} {mx:9.2f} {mnx:9.2f} | {err:7.2f}")
        if best is None or err < best[0]:
            best = (err, sj2, sj3)

    print()
    err, sj2, sj3 = best
    print(f"→ 最佳组合: J2{'+' if sj2 > 0 else '-'}  J3{'+' if sj3 > 0 else '-'}"
          f"   （三个极值总偏差 {err:.2f} mm）")
    if err > 0.5:
        print("  ⚠ 偏差偏大，说明除转向外还可能有零位偏置或建模差异，需进一步核对")

    # ── 用最佳组合核对图纸上其余的包络标注 ────────────────────
    print()
    print("最佳组合下，J2 / J3 取软限位四角时的腕心坐标（相对 J1 轴线与 J2 轴高度）：")
    print(f"  {'q2':>6} {'q3':>6} | {'x':>9} {'z':>9} | {'|x|':>9}")
    corners = []
    for q2 in (LIM_J2[0], LIM_J2[1]):
        for q3 in (LIM_J3[0], LIM_J3[1]):
            x, z = wrist(sj2, sj3, q2, q3)
            corners.append((abs(x), z))
            print(f"  {q2:6.1f} {q3:6.1f} | {x:9.2f} {z:9.2f} | {abs(x):9.2f}")

    print()
    print("图纸标注的其余包络尺寸:", ", ".join(f"{v:g}" for v in TARGET_EXTRA))
    print("最佳组合四角 |x|:", ", ".join(f"{v:.2f}" for v, _ in corners))
    return


if __name__ == "__main__":
    main()
