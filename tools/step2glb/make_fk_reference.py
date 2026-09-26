# -*- coding: utf-8 -*-
"""
make_fk_reference.py — 从后端 kinematics.py 生成 FK 参考值，供前端 RobotArm.js 逐位比对。

输出: frontend/public/models/fk_reference.json
      { "unit": "mm", "frame": "base(CAD, Z-up)", "rpy": "ZYX R=Rz(c)Ry(b)Rx(a)",
        "poses": [ { "jointsDeg": [...], "x":..,"y":..,"z":..,"a":..,"b":..,"c":..,
                     "wrist": {"x":..,"y":..,"z":..} } ] }

用法: python make_fk_reference.py
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

import kinematics as K  # noqa: E402

OUT = os.path.join(ROOT, "frontend", "public", "models", "fk_reference.json")

POSES = [
    [0, 0, 0, 0, 0, 0],                  # 零位（= 数模姿态）
    [0, 0, 82.863, 0, 0, 0],             # 竖直满展：腕心 z = 960.31
    [0, -90, 82.863, 0, 0, 0],           # 水平满展：腕心 x = 592.81
    [0, 40, -40, 0, 90, 0],              # 工具竖直朝下
    [0, 32, -27, 0, -63, 0],             # 官方搬运姿态（手册 图2-3 / 表2-3）
    [30, -30, 60, 45, -45, 20],          # 一般位姿
    [-120, 20, 100, -90, 60, 170],       # 大角度组合
    [0, -90, 0, 0, 0, 0],                # J2 水平（旧前端默认值的历史回归）
    [90, 85, 185, 190, 130, 360],        # 各轴正向软限位边界
    [-170, -135, -65, -190, -130, -360], # 各轴负向软限位边界
]


def r6(v):
    return [round(float(x), 6) for x in v]


def main() -> int:
    poses = []
    for q in POSES:
        f = K.fk_flange_pose(q)
        w = K.fk_wrist_center(q)
        poses.append({
            "jointsDeg": q,
            "x": round(f["x"], 6), "y": round(f["y"], 6), "z": round(f["z"], 6),
            "a": round(f["a"], 6), "b": round(f["b"], 6), "c": round(f["c"], 6),
            "wrist": {"x": round(w[0], 6), "y": round(w[1], 6), "z": round(w[2], 6)},
        })

    doc = {
        "source": "backend/kinematics.py · fk_flange_pose / fk_wrist_center",
        "unit": "mm",
        "frame": "base (CAD, Z-up, 原点=基座安装面中心)",
        "rpy": "ZYX: R = Rz(c)·Ry(b)·Rx(a)",
        "measured": {
            "d1": K.D1_MM, "L2": K.L2_MM, "e3": K.E3_MM,
            "L4": K.L4_MM, "tool": K.TOOL_LENGTH_MM,
        },
        "nominalReachMm": round(K.NOMINAL_REACH_MM, 6),
        "poses": poses,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(doc, fp, ensure_ascii=False, indent=2)
    print(f"已写出 {OUT}  ({len(poses)} 个位姿)")
    for p in poses:
        print(f"  J={p['jointsDeg']}  flange=({p['x']:.3f}, {p['y']:.3f}, {p['z']:.3f})"
              f"  rpy=({p['a']:.3f}, {p['b']:.3f}, {p['c']:.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
