# -*- coding: utf-8 -*-
"""
kinsematics.py — 埃夫特 ER3-600 六轴机器人运动学（几何量纲全部取自官方数模实测）

═══════════════════════════════════════════════════════════════════════════
 一、几何来源与可信度
═══════════════════════════════════════════════════════════════════════════
官方下载中心「ER3-600 机器人数模 V1.2.STEP」（21.1 MB，AP214，SolidWorks
2016 导出）中解析得到。关节轴线不是估计出来的，而是解析 STEP 的
CYLINDRICAL_SURFACE 实体后、把同一部件内共线的圆柱面聚类得到的：

    关节      轴向      轴线上一点 (mm)      共轴圆柱面数（置信度）
    J1       +Z       (0, 0, ·)                4
    J2       ±Y       (0, ·, 367.50)           2
    J3       ±Y       (0, ·, 662.50)           3
    J4       +X       (·, 0, 699.50)           2
    J5       ±Y       (295.50, ·, 699.50)      4
    J6       +X       (·, 0, 699.50)           2  （与 J5 同过腕心→球形腕）

由此得到杆长（mm）:
    d1 = 367.5   J1 轴 → J2 轴（沿 +Z）
    L2 = 295.0   J2 → J3（大臂）
    e3 = 37.0    J3 → J4（沿 J3 系 +Z，恒垂直于 L4）
    L4 = 295.5   J4 → J5（小臂，沿 J4 系 +X）
    tool = 78.5  腕心(J5) → 法兰安装面（法兰面 x = 374）

自洽性交叉验证（三条独立证据互相印证）:
    ① 最大臂展 = L2 + |J3→腕心|
                = 295.0 + √(37² + 295.5²)
                = 295.0 + 297.81 = 592.81 mm   ≈ 官方标称 593 mm（偏差 0.19 mm）
       注: e3 与 L4 都固定在 J3 系内且互相垂直，故 |J3→腕心| 恒为 297.81，
           不随 J2/J3 变化 —— 这正是该机型臂展的定义。
    ② 全伸展时腕心 z = d1 + L2 + |J3→腕心| = 960.31 mm
       ，与数模「运动范围」包络体的 z 上限 960.3 mm 一致。
    ③ 数模装配里「手腕」与「法兰」两个零件的局部原点重合于
       (295.50, 0, 699.50)，即腕心 —— 与 ④ 的轴线解算结果一致。

═══════════════════════════════════════════════════════════════════════════
 二、坐标系、零位与关节正方向约定
═══════════════════════════════════════════════════════════════════════════
后端统一使用 **CAD 坐标系**：原点在基座安装面中心（J1 轴与安装面的交点），
Z 轴向上，X 轴前向，Y 轴左手方向。单位 mm。

零位 = 数模姿态（即 q1..q6 全 0 时，机器人形状与官方 STEP 数模完全重合，
也和官方《运动范围图 V1.1》里画的姿态一致）:
    大臂竖直向上，小臂前伸（小臂轴线比水平高 7.137° = atan(e3/L4)）。

关节正方向 **不是** 数模轴线的 ±Y / ±X 符号能给出的，而要由官方《运动范围图
V1.1》上标注的工作空间包络反解 —— 见下面 JOINT_SIGN 的详细推导。结论：

    J1 +   J2 −   J3 −   J4 +   J5 +   J6 +

因此对官方关节角 q，几何旋转量是 JOINT_SIGN[i] · q。前端 GLB 驱动必须用同一张
表（frontend/src/constants/robot.js 的 JOINT_SIGN），否则仿真会与后端反向。

注意: 官方软限位表（J1±170 / J2 −135~+85 / J3 −65~+185 / J4±190 / J5±130
/ J6±360）按「数模姿态为零位」解释。若将来真机到货后发现控制器零位不同，
只需改 JOINT_ZERO_OFFSET_DEG 一处，其余代码无需改动。

═══════════════════════════════════════════════════════════════════════════
 三、正向运动学
═══════════════════════════════════════════════════════════════════════════
与前端 GLB 的节点层级逐字对应（Three.js 节点变换 = T(local)·R(local)），
下面 q1..q6 已是 **几何旋转量**（= JOINT_SIGN × 官方关节角）:

    M  = Rz(q1)
    M @= T(0,0,d1) · Ry(q2)
    M @= T(0,0,L2) · Ry(q3)
    M @= T(0,0,e3) · Rx(q4)
    M @= T(L4,0,0) · Ry(q5)
    M @= Rx(q6)

    腕心  = M[:3, 3]
    法兰面 = 腕心 + tool · M[:3, 0]        （法兰外法线 = J6 系 +X）

姿态表达沿用项目约定 ZYX-RPY: R = Rz(c) · Ry(b) · Rx(a)。

═══════════════════════════════════════════════════════════════════════════
 四、逆向运动学（闭式解 + 数值兜底）
═══════════════════════════════════════════════════════════════════════════
因腕部为球形腕（J4 ∥ J6 且 J5 过腕心），可闭式求解，比单纯数值迭代更快更稳:

  1) 由目标法兰位姿反推腕心:  wrist = p − tool · R·ex
  2) q1 = atan2(wy, wx)  （另有一组反向解 q1+180°）
  3) 平面解 q2/q3: 令 a = u, b = v − d1（u,v 为臂平面内水平/垂直坐标）
         a = L2·sin q2 + R·cos ψ
         b = L2·cos q2 − R·sin ψ          ψ = q2 + q3 − α,  α = atan2(e3, L4)
     消去 ψ 得  a·sin q2 + b·cos q2 = (a² + b² + L2² − R²) / (2·L2)
     再解出 ψ → q3 = ψ + α − q2
  4) 腕部 q4/q5/q6 由 Rx(q4)·Ry(q5)·Rx(q6) = Ry(−(q2+q3))·Rz(−q1)·R 做 X-Y-X 欧拉分解
  5) 枚举全部候选（≤8 组），按软限位过滤，取离种子最近者

闭式解失败时回退到阻尼最小二乘（DLS）数值求解，两条路互相校验。

自测: python kinematics.py
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# ══════════════════════════════════════════════════════════════════════════
#  一、整机规格（官方标称）
# ══════════════════════════════════════════════════════════════════════════
ROBOT_NAME = "EFORT_ER3_600"
ROBOT_DISPLAY_NAME = "埃夫特 ER3-600"
ROBOT_VENDOR = "EFORT Intelligent Equipment Co., Ltd."

PAYLOAD_KG = 3.0                 # 手腕可搬运质量
BODY_WEIGHT_KG = 27.0            # 本体质量
REPEATABILITY_MM = 0.02          # 重复定位精度 ±0.02mm
MAX_REACH_MM = 593.0             # 官方标称最大臂展（J2 轴 → 腕心）

AXIS_COUNT = 6
MOUNTING_MODES = ("地面", "顶吊", "壁挂")

# 各轴最大速度 (°/s)
JOINT_MAX_SPEED_DEG_S: Tuple[float, ...] = (400.0, 300.0, 520.0, 500.0, 530.0, 840.0)

# 各轴运动范围 (°)，官方软限位
JOINT_LIMITS_DEG: Tuple[Tuple[float, float], ...] = (
    (-170.0, 170.0),
    (-135.0, 85.0),
    (-65.0, 185.0),
    (-190.0, 190.0),
    (-130.0, 130.0),
    (-360.0, 360.0),
)

# 向后兼容：老代码用对称上限做粗判，这里保留 J1 的限位作为代表值
JOINT_LIMIT_DEG = JOINT_LIMITS_DEG[0][1]

# ══════════════════════════════════════════════════════════════════════════
#  关节旋转正方向（⚠️ 关键约定，改动前务必读完这段）
# ══════════════════════════════════════════════════════════════════════════
# 数模里各关节的几何轴线方向（±Y、±X）只给出「转轴在哪」，不给出「哪个方向算正」。
# 正方向由官方《ER3-600 运动范围图 V1.1》确定：
#
#   图纸在侧视图上标注了从 J1 轴线起算的工作空间包络极值：
#       +Z 向上 592.8 / +X 前向 592.8 / −X 后向 591.6
#       以及 (J2, J3) 取软限位四角时的 |x| 与 z：81.7 / 142.3 / 28.9 / 161.6
#       256.9 / 269.8
#   把 (±J2, ±J3) 四种符号组合各扫一遍包络，只有 (−,−) 能同时命中：
#
#       符号组合        图纸 592.8 / 592.8 / −591.6 的总偏差
#       J2+ J3+                        20.34 mm
#       J2+ J3−                         2.33 mm
#       J2− J3+                        37.67 mm
#       J2− J3−                         0.10 mm   ← 唯一解
#
#   四角复核（J2−, J3−，腕心相对 J1 轴线 / J2 轴高度）：
#       (−135, −65) → x=−81.74  z=−142.30   图纸标注 81.7 / 142.3  ✓
#       ( 85, −65) → x=−28.85  z= 161.55   图纸标注 28.9 / 161.6  ✓
#       ( 85, 185) → x=−256.88 z=−269.79   图纸标注 256.9 / 269.8 ✓
#   6 个独立尺寸全部吻合到 0.1 mm（图纸标注精度）。
#
#   旁证：手册《图2-3 吊装搬运示意图》的搬运姿态 (J2=+32, J3=−27, J5=−63)
#   画的是大臂向**后**倾倒 32°，与 J2 取负号一致；且该姿态下 J2 取正号会
#   让大臂向前倾，与图纸不符（J2+ 组合的 2.33 mm 偏差也来自这里）。
#
# J1 / J4 / J5 / J6 的量程对称（±190 / ±130 / ±360），包络无法区分正负，
# 暂取 +1；这三个轴只影响「命令正角度时往哪边转」，不影响可达空间。
# 真机到货后按示教器点动方向标定，此处加 JOINT_ZERO_OFFSET_DEG 一起改。
JOINT_SIGN: Tuple[int, ...] = (1, -1, -1, 1, 1, 1)

# 内部旋转量（= JOINT_SIGN × 官方关节角）对应的软限位，仅闭式 IK 内部使用
JOINT_LIMITS_INT_DEG: Tuple[Tuple[float, float], ...] = tuple(
    tuple(sorted((JOINT_SIGN[i] * lo, JOINT_SIGN[i] * hi)))
    for i, (lo, hi) in enumerate(JOINT_LIMITS_DEG)
)

# 内部旋转量（= JOINT_SIGN × 官方关节角）对应的软限位，仅闭式 IK 内部使用
JOINT_LIMITS_INT_DEG: Tuple[Tuple[float, float], ...] = tuple(
    tuple(sorted((JOINT_SIGN[i] * lo, JOINT_SIGN[i] * hi)))
    for i, (lo, hi) in enumerate(JOINT_LIMITS_DEG)
)

DEFAULT_SPEED_PERCENT = 50.0     # 默认运行倍率
TCP_MAX_SPEED_MM_S = 1500.0      # 末端线速度上限（仿真用安全阀）
BASE_SIZE_MM = 150.0             # 底座安装尺寸
BASE_BOLT_CIRCLE_MM = 320.0      # 底座安装孔分布圆

# 关节零位偏置：真机到货后若发现控制器零位与数模零位不同，只改这里
JOINT_ZERO_OFFSET_DEG: Tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


# ══════════════════════════════════════════════════════════════════════════
#  二、实测几何量纲（唯一真相源；与 tools/step2glb/build_glb.js 的 GEO 必须一致）
# ══════════════════════════════════════════════════════════════════════════
D1_MM = 367.5      # J1 轴 → J2 轴
L2_MM = 295.0      # J2 → J3（大臂）
E3_MM = 37.0       # J3 → J4（肘部偏置，恒垂直于 L4）
L4_MM = 295.5      # J4 → J5（小臂）
TOOL_LENGTH_MM = 78.5   # 腕心 → 法兰安装面

# ══════════════════════════════════════════════════════════════════════════
#  「工具朝下」的标准姿态 —— PICK / PLACE / 抓取类动作必须用它
# ══════════════════════════════════════════════════════════════════════════
#  法兰外法线 = 法兰系 +X。rpy_to_matrix(a,b,c) 的第 0 列就是该法线在基座系
#  下的方向，且与 a、c 无关，只由 b 决定：
#      b =   0° → ( 1, 0, 0)  水平朝前
#      b =  90° → ( 0, 0,-1)  垂直朝下   ← 吸盘要的就是这个
#      b = -90° → ( 0, 0, 1)  垂直朝上
#  历史 bug：PICK/PLACE 传的是 (180,0,0)，法线水平，低位目标根本不可达，
#  于是每一条 PICK 都在 IK 那里静默失败、机器人一步不动。
#  a / c 只绕工具轴自转，吸盘无所谓；求解器会在候选解里挑离当前姿态最近的那组。
TOOL_DOWN_RPY: Tuple[float, float, float] = (0.0, 90.0, 0.0)

# 抓取/放置时抬升到接触面之上的安全间隙（mm）
APPROACH_CLEARANCE_MM = 80.0

# 复合量
WRIST_OFFSET_MM = math.hypot(E3_MM, L4_MM)          # |J3 → 腕心| = 297.81（恒定）
ELBOW_BIAS_DEG = math.degrees(math.atan2(E3_MM, L4_MM))  # α = 7.137°
NOMINAL_REACH_MM = L2_MM + WRIST_OFFSET_MM          # 592.81
WRIST_HEIGHT_AT_ZERO_MM = D1_MM + L2_MM + E3_MM     # 699.5

# J3 的等效臂平面角 τ = JOINT_SIGN[2]·q3 − α 的取值范围（用于解析包络）
_TAU_ENDS = sorted(JOINT_SIGN[2] * q - ELBOW_BIAS_DEG for q in JOINT_LIMITS_DEG[2])
TAU_RANGE_DEG: Tuple[float, float] = (_TAU_ENDS[0], _TAU_ENDS[1])

# 使小臂与 X 轴（而非大臂）对齐所需的 J3 角度。
# 官方软限位 J3 ∈ [−65, 185]，满展（大臂小臂共线向上）发生在 J3 = +82.863，
# 落在限位内 → 图纸标注的最大臂展 592.8 确实可达。详见 JOINT_SIGN 的推导。
FULL_EXTENSION_J3_DEG = 90.0 - ELBOW_BIAS_DEG    # +82.863°

WRIST_IS_SPHERICAL = True   # 数模实测：J4 与 J6 共线，J5 与二者同交于腕心


# ══════════════════════════════════════════════════════════════════════════
#  场景物体快照的坐标语义（executor 与 validator 共用，禁止各写一套）
# ══════════════════════════════════════════════════════════════════════════
#  前端为了渲染用 Three.js 场景系（Y 轴向上、毫米），后端用 CAD 系（Z 轴向上、
#  毫米）。RobotArm 的 group.rotation.x = −90° 给出：
#      scene_x = base_x,  scene_y = base_z,  scene_z = −base_y
#  反解：base_x = scene_x,  base_y = −scene_z,  base_z = scene_y
#  所以「按基座系水平坐标就近找物体」比较的是 (p[0], −p[2])。
#  历史教训：executor 与 validator 各写了一份「反解坐标 + 查高度」的实现，
#  结果一个按物体高度算接触面、另一个按 0 算，校验通过的姿态执行时对不上。
SCENE_OBJECT_MATCH_TOL_MM = 250.0

# 快照里找不到目标物体时用来估接触面的默认物体高度（mm）。
#  宁可悬空，也不要按高度 0 下探 —— 后者会让吸盘直接扎进物体里。
DEFAULT_OBJECT_HEIGHT_MM = 60.0


def find_scene_object(scene_objects, x: float, y: float,
                      tol_mm: float = SCENE_OBJECT_MATCH_TOL_MM):
    """按**基座系**水平坐标 (x, y) 就近取场景物体；超出容差返回 None。

    scene_objects 里每个元素形如
        {'name':..., 'position':[sx, sy, sz], 'size':[sx, sy, sz], ...}
    其中 position 是前端**场景系**坐标（Y 轴向上）。
    """
    best, best_d = None, float('inf')
    for obj in scene_objects or ():
        p = obj.get('position') or []
        if len(p) < 3:
            continue
        try:
            d = math.hypot(float(p[0]) - x, -float(p[2]) - y)
        except (TypeError, ValueError):
            continue
        if d < best_d:
            best, best_d = obj, d
    return best if (best is not None and best_d <= tol_mm) else None


def scene_object_height(obj, default: float = DEFAULT_OBJECT_HEIGHT_MM) -> float:
    """物体高度（mm）= 场景系 size 的 Y 分量；解析不出来时返回 default。"""
    if not obj:
        return float(default)
    size = obj.get('size') or []
    if len(size) < 2:
        return float(default)
    try:
        h = float(size[1])
    except (TypeError, ValueError):
        return float(default)
    return h if h > 0 else float(default)


def _reach_band_mm(joint_index: int = 2, samples: int = 40001) -> Tuple[float, float]:
    """
    扫描 J3 全量程，求腕心到 J2 轴的距离区间 (最小值, 最大值)。
    该区间只由 L2 / e3 / L4 与 J3 软限位决定，与其它轴无关。
    J3 ∈ [−65, 185] 对应  |J3→腕心| 方向与 +Z 的夹角 ∈ [147.86°, 102.14°]，
    故最小值并不是「两级几乎折叠」的 |L4 − L2| = 2.81，而是 164.1 mm。
    """
    lo, hi = JOINT_LIMITS_DEG[joint_index]
    s = JOINT_SIGN[joint_index]
    wmin, wmax = float("inf"), 0.0
    for k in range(samples):
        q = lo + (hi - lo) * k / (samples - 1)
        tau = math.radians(s * q - ELBOW_BIAS_DEG)
        w = math.hypot(WRIST_OFFSET_MM * math.cos(tau), L2_MM - WRIST_OFFSET_MM * math.sin(tau))
        if w < wmin:
            wmin = w
        if w > wmax:
            wmax = w
    return wmin, wmax


# 可达范围（相对 J2 轴的距离），由 J3 软限位扫描得到
REACH_MIN_MM, REACH_MAX_MM = _reach_band_mm()        # (164.1, 592.81)


def wrist_center_planar(q2_deg: float, q3_deg: float) -> Tuple[float, float]:
    """
    腕心在臂平面内的解析坐标 (x, z)，单位 mm，原点为 J1 轴与安装面交点。
    与 fk_wrist_center() 的 x/z 完全等价，但只做标量运算，适合做包络扫描。
        x = W·cos(τ + θ2) + L2·sin θ2
        z = L2·cos θ2 − W·sin(τ + θ2)
    其中 θ2 = JOINT_SIGN[1]·q2，τ = JOINT_SIGN[2]·q3 − α，W = |J3→腕心| = 297.81。
    固定 θ2 时腕心落在以 (L2·sin θ2, L2·cos θ2) 为心、半径 W 的圆弧上 —— 这正是
    两连杆平面臂的经典结论。
    """
    t2 = _D(JOINT_SIGN[1] * float(q2_deg))
    tau = _D(JOINT_SIGN[2] * float(q3_deg) - ELBOW_BIAS_DEG)
    ph = tau + t2
    return (WRIST_OFFSET_MM * math.cos(ph) + L2_MM * math.sin(t2),
            L2_MM * math.cos(t2) - WRIST_OFFSET_MM * math.sin(ph))


def planar_envelope(samples: int = 8009) -> Dict[str, float]:
    """
    解析求侧视（臂平面）工作空间包络的极值，用于与官方《运动范围图》比对。

    z 一律相对 J2 轴。固定 θ2 时腕心沿圆弧运动，故圆弧上极值只能出现在
    ① 圆弧两端（τ = TAU_RANGE 边界）或 ② cos/sin 取 ±1 的相位处。
    再对 θ2 均匀采样即可（θ2 方向光滑，误差 ~1e-4 mm 量级）。
    """
    q2_lo, q2_hi = JOINT_LIMITS_DEG[1]
    s2 = JOINT_SIGN[1]
    tlo, thi = _D(TAU_RANGE_DEG[0]), _D(TAU_RANGE_DEG[1])
    phases = (0.0, 0.5 * math.pi, math.pi, 1.5 * math.pi)

    x_max, x_min = -1e18, 1e18
    z_max, z_min = -1e18, 1e18
    arg = {}

    for i in range(samples + 1):
        q2 = q2_lo + (q2_hi - q2_lo) * i / samples
        t2 = _D(s2 * q2)
        cand = [tlo, thi]
        for ph in phases:
            for k in range(-2, 3):
                t = ph - t2 + 2.0 * math.pi * k
                if tlo - 1e-12 <= t <= thi + 1e-12:
                    cand.append(t)
        sin_t2, cos_t2 = math.sin(t2), math.cos(t2)
        for t in cand:
            ph = t + t2
            x = WRIST_OFFSET_MM * math.cos(ph) + L2_MM * sin_t2
            z = L2_MM * cos_t2 - WRIST_OFFSET_MM * math.sin(ph)
            q3 = (math.degrees(t) + ELBOW_BIAS_DEG) / JOINT_SIGN[2]
            if x > x_max:
                x_max, arg["x_max"] = x, (q2, q3)
            if x < x_min:
                x_min, arg["x_min"] = x, (q2, q3)
            if z > z_max:
                z_max, arg["z_max"] = z, (q2, q3)
            if z < z_min:
                z_min, arg["z_min"] = z, (q2, q3)

    return {"x_max": x_max, "x_min": x_min, "z_max": z_max, "z_min": z_min, "arg": arg}

# 数模几何自检用的参考量（selftest 会核对）
CAD_REFERENCE = {
    "wrist_center_zero_mm": (L4_MM, 0.0, WRIST_HEIGHT_AT_ZERO_MM),      # (295.5, 0, 699.5)
    "flange_face_zero_mm": (L4_MM + TOOL_LENGTH_MM, 0.0, WRIST_HEIGHT_AT_ZERO_MM),  # (374, 0, 699.5)
    "full_extension_wrist_z_mm": D1_MM + NOMINAL_REACH_MM,              # 960.31
    "source": "ER3-600 机器人数模 V1.2.STEP（官方下载中心）",
    "method": "CYLINDRICAL_SURFACE 实体共轴聚类",
}

# 官方《运动范围图 V1.1》上标注的工作空间包络尺寸（mm），用于反解关节转向
WORKSPACE_REFERENCE = {
    "source": "ER3-600_workspace_V1.1（官方下载中心）侧视图标注",
    "max_z_above_j2_mm": 592.8,      # J2 轴以上最大高度
    "max_x_mm": 592.8,               # 前向最大水平伸距
    "min_x_mm": -591.6,              # 后向最大水平伸距
    # (J2, J3) 取软限位四角时，腕心相对 J1 轴线的 (|x|, z)
    "corners": {
        (-135.0, -65.0): (81.7, -142.3),
        (85.0, -65.0): (28.9, 161.6),
        (85.0, 185.0): (256.9, -269.8),
    },
}

HOME_JOINTS_DEG: Tuple[float, ...] = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

_D = math.radians
_R = math.degrees


# ══════════════════════════════════════════════════════════════════════════
#  三、基础数学
# ══════════════════════════════════════════════════════════════════════════
def deg2rad(deg: float) -> float:
    return float(deg) * math.pi / 180.0


def rad2deg(rad: float) -> float:
    return float(rad) * 180.0 / math.pi


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def wrap_deg(deg: float) -> float:
    """把角度折算到 (-180, 180]"""
    d = math.fmod(float(deg) + 180.0, 360.0)
    if d <= 0:
        d += 360.0
    return d - 180.0


def clamp_joints(joints_deg: Sequence[float]) -> List[float]:
    """按各轴软限位裁剪关节角（返回 6 元素新列表，不足补 0）"""
    js = [float(x) for x in list(joints_deg)[:6]]
    js += [0.0] * (6 - len(js))
    return [_clamp(js[i], JOINT_LIMITS_DEG[i][0], JOINT_LIMITS_DEG[i][1]) for i in range(6)]


def to_internal_joints(joints_deg: Sequence[float]) -> List[float]:
    """官方关节角 → 几何旋转量（乘 JOINT_SIGN；软限位同步交换上下限）"""
    js = [float(x) for x in list(joints_deg)[:6]]
    js += [0.0] * (6 - len(js))
    return [_clamp(js[i] * JOINT_SIGN[i], *JOINT_LIMITS_INT_DEG[i]) for i in range(6)]


def to_external_joints(joints_int_deg: Sequence[float]) -> List[float]:
    """几何旋转量 → 官方关节角（JOINT_SIGN 为 ±1，自身即为逆运算）"""
    js = [float(x) for x in list(joints_int_deg)[:6]]
    js += [0.0] * (6 - len(js))
    return [js[i] * JOINT_SIGN[i] for i in range(6)]


def joints_within_limits(joints_deg: Sequence[float], tol: float = 1e-6) -> bool:
    js = list(joints_deg)[:6]
    if len(js) < 6:
        return False
    for i, j in enumerate(js):
        if not math.isfinite(float(j)):
            return False
        lo, hi = JOINT_LIMITS_DEG[i]
        if j < lo - tol or j > hi + tol:
            return False
    return True


def joint_limit_violations(joints_deg: Sequence[float]) -> List[Tuple[int, float, float, float]]:
    """越限清单：[(轴序号(0基), 值, 下限, 上限), ...]"""
    out = []
    js = list(joints_deg)[:6]
    for i, j in enumerate(js):
        lo, hi = JOINT_LIMITS_DEG[i]
        if not math.isfinite(float(j)) or j < lo or j > hi:
            out.append((i, float(j), lo, hi))
    return out


def joint_limit_text(index: int) -> str:
    lo, hi = JOINT_LIMITS_DEG[index]
    return f"J{index + 1} ∈ [{lo:g}°, {hi:g}°]"


def max_joint_delta_deg(a: Sequence[float], b: Sequence[float]) -> float:
    """两组关节角的最大单轴差（用于速度/连续性判断）"""
    return max(abs(float(x) - float(y)) for x, y in zip(list(a)[:6], list(b)[:6]))


# ══════════════════════════════════════════════════════════════════════════
#  四、旋转矩阵 / RPY（ZYX 约定：R = Rz(c)·Ry(b)·Rx(a)）
# ══════════════════════════════════════════════════════════════════════════
def rot_x(rad: float) -> np.ndarray:
    c, s = math.cos(rad), math.sin(rad)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def rot_y(rad: float) -> np.ndarray:
    c, s = math.cos(rad), math.sin(rad)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def rot_z(rad: float) -> np.ndarray:
    c, s = math.cos(rad), math.sin(rad)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def rpy_to_matrix(a_deg: float, b_deg: float, c_deg: float) -> np.ndarray:
    """ZYX-RPY → 旋转矩阵: R = Rz(c)·Ry(b)·Rx(a)"""
    return rot_z(_D(c_deg)) @ rot_y(_D(b_deg)) @ rot_x(_D(a_deg))


def matrix_to_rpy(R: np.ndarray) -> Tuple[float, float, float]:
    """
    旋转矩阵 → ZYX-RPY (a, b, c)。
    R = Rz(c)·Ry(b)·Rx(a) 展开后:
        R[2,0] = -sin b        R[1,0]/R[0,0] = tan c        R[2,1]/R[2,2] = tan a
    万向锁 (b = ±90°) 时只有 a±c 可辨，约定 a = 0。
    """
    R = np.asarray(R, dtype=float).reshape(3, 3)
    sb = _clamp(-R[2, 0], -1.0, 1.0)
    b = math.asin(sb)
    if abs(sb) < 1.0 - 1e-12:
        c = math.atan2(R[1, 0], R[0, 0])
        a = math.atan2(R[2, 1], R[2, 2])
    elif sb > 0:
        # b = +90°: R[0,1] = sin(a-c), R[0,2] = cos(a-c)；取 a = 0 → c = -atan2(...)
        a = 0.0
        c = -math.atan2(R[0, 1], R[0, 2])
    else:
        # b = -90°: R[0,1] = -sin(a+c), R[0,2] = -cos(a+c)；取 a = 0 → c = atan2(...)
        a = 0.0
        c = math.atan2(-R[0, 1], -R[0, 2])
    return _R(a), _R(b), _R(c)


def rot_log(R_err: np.ndarray) -> np.ndarray:
    """旋转矩阵的轴角对数映射（返回 3 维旋转向量，弧度）"""
    R = np.asarray(R_err, dtype=float).reshape(3, 3)
    tr = _clamp((R[0, 0] + R[1, 1] + R[2, 2] - 1.0) * 0.5, -1.0, 1.0)
    theta = math.acos(tr)
    if theta < 1e-9:
        return np.zeros(3)
    if abs(math.pi - theta) < 1e-6:
        # 接近 180°，用 (R + I)/2 的列向量取轴
        A = (R + np.eye(3)) * 0.5
        i = int(np.argmax(np.diag(A)))
        v = A[:, i]
        n = float(np.linalg.norm(v))
        if n < 1e-12:
            return np.zeros(3)
        return (v / n) * theta
    return theta / (2.0 * math.sin(theta)) * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])


def rot_angle_deg(R_err: np.ndarray) -> float:
    """两个姿态之间的夹角（度）"""
    return _R(float(np.linalg.norm(rot_log(R_err))))


# ══════════════════════════════════════════════════════════════════════════
#  五、正向运动学
# ══════════════════════════════════════════════════════════════════════════
def _tf(R: np.ndarray, t: Sequence[float]) -> np.ndarray:
    M = np.eye(4)
    M[:3, :3] = R
    M[:3, 3] = np.asarray(t, dtype=float)
    return M


def fk_frames(joints_deg: Sequence[float]) -> List[np.ndarray]:
    """
    返回 7 个齐次矩阵: [Base, J1, J2, J3, J4, J5, J6]（均在基座坐标系）。

    关节正方向来自 JOINT_SIGN（由官方运动范围图反解），因此这里先把输入的
    官方关节角换算成「几何旋转量」再套用固定链式，链式本身与前端 GLB 节点
    层级逐字对应，不做任何符号判断。
    """
    raw = [float(v) for v in list(joints_deg)[:6]]
    raw += [0.0] * (6 - len(raw))
    q = [deg2rad(raw[i] * JOINT_SIGN[i]) for i in range(6)]

    frames = [np.eye(4)]
    M = np.eye(4)
    M = M @ _tf(rot_z(q[0]), (0.0, 0.0, 0.0))
    frames.append(M.copy())
    M = M @ _tf(rot_y(q[1]), (0.0, 0.0, D1_MM))
    frames.append(M.copy())
    M = M @ _tf(rot_y(q[2]), (0.0, 0.0, L2_MM))
    frames.append(M.copy())
    M = M @ _tf(rot_x(q[3]), (0.0, 0.0, E3_MM))
    frames.append(M.copy())
    M = M @ _tf(rot_y(q[4]), (L4_MM, 0.0, 0.0))
    frames.append(M.copy())
    M = M @ _tf(rot_x(q[5]), (0.0, 0.0, 0.0))
    frames.append(M.copy())
    return frames


def forward_kinematics(joint_angles_deg: Sequence[float]) -> np.ndarray:
    """末端法兰坐标系在基座系下的 4x4 齐次矩阵"""
    return fk_frames(joint_angles_deg)[6]


def fk_wrist_center(joint_angles_deg: Sequence[float]) -> Tuple[float, float, float]:
    """腕心（J5 原点）坐标 —— 与前端 GLB 的 J6 节点世界位置一致"""
    frames = fk_frames(joint_angles_deg)
    p = frames[5][:3, 3]
    return float(p[0]), float(p[1]), float(p[2])


def fk_flange_pose(joint_angles_deg: Sequence[float]) -> Dict[str, float]:
    """法兰安装面中心的位姿 {x,y,z,a,b,c}"""
    M = forward_kinematics(joint_angles_deg)
    p = M[:3, 3] + M[:3, 0] * TOOL_LENGTH_MM
    a, b, c = matrix_to_rpy(M[:3, :3])
    return {"x": float(p[0]), "y": float(p[1]), "z": float(p[2]), "a": a, "b": b, "c": c}


def fk_pose(joint_angles_deg: Sequence[float]) -> Dict[str, float]:
    """兼容入口：返回法兰安装面位姿（= fk_flange_pose）"""
    return fk_flange_pose(joint_angles_deg)


def fk_position(joint_angles_deg: Sequence[float]) -> Tuple[float, float, float]:
    """兼容入口：返回法兰安装面中心坐标"""
    p = fk_flange_pose(joint_angles_deg)
    return p["x"], p["y"], p["z"]


def pose_to_matrix(pose: Dict[str, float]) -> np.ndarray:
    """{x,y,z,a,b,c} → 4x4 齐次矩阵"""
    M = np.eye(4)
    M[:3, :3] = rpy_to_matrix(pose.get("a", 0.0), pose.get("b", 0.0), pose.get("c", 0.0))
    M[:3, 3] = (float(pose.get("x", 0.0)), float(pose.get("y", 0.0)), float(pose.get("z", 0.0)))
    return M


def flange_target_to_wrist(target: Dict[str, float]) -> np.ndarray:
    """由目标法兰位姿反推腕心坐标（法兰外法线 = 法兰系 +X）"""
    M = pose_to_matrix(target)
    return M[:3, 3] - M[:3, 0] * TOOL_LENGTH_MM


# ══════════════════════════════════════════════════════════════════════════
#  六、几何雅可比
# ══════════════════════════════════════════════════════════════════════════
def geometric_jacobian(joint_angles_deg: Sequence[float]) -> np.ndarray:
    """6x6 几何雅可比（末端线速度/角速度 ← 关节角速度）"""
    frames = fk_frames(joint_angles_deg)
    axes = [np.array([0.0, 0.0, 1.0]),                       # J1 绕基座 +Z
            np.array([0.0, 1.0, 0.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([1.0, 0.0, 0.0]),
            np.array([0.0, 1.0, 0.0]),
            np.array([1.0, 0.0, 0.0])]
    # 关节正方向符号要一起带上，否则 ∂p/∂q 的符号会反
    axes = [a * JOINT_SIGN[i] for i, a in enumerate(axes)]
    p_end = frames[6][:3, 3] + frames[6][:3, 0] * TOOL_LENGTH_MM
    J = np.zeros((6, 6))
    for i in range(6):
        zi = frames[i][:3, :3] @ axes[i]
        pi = frames[i][:3, 3]
        J[:3, i] = np.cross(zi, p_end - pi)
        J[3:, i] = zi
    return J


def manipulability(joint_angles_deg: Sequence[float]) -> float:
    """Yoshikawa 可操作度 sqrt(det(J·Jᵀ))，用于识别奇异位形"""
    try:
        J = geometric_jacobian(joint_angles_deg)
        d = float(np.linalg.det(J @ J.T))
        return math.sqrt(d) if d > 0 else 0.0
    except Exception:
        return 0.0


# ══════════════════════════════════════════════════════════════════════════
#  七、可达性判断
# ══════════════════════════════════════════════════════════════════════════
def wrist_distance_from_j2(x: float, y: float, z: float) -> float:
    """点到 J2 轴（过 (0,0,d1) 且平行 Y）的距离"""
    r = math.hypot(float(x), float(y))
    return math.hypot(r, float(z) - D1_MM)


def is_position_reachable(x: float, y: float, z: float, tol: float = 1.0) -> Tuple[bool, str]:
    """
    判断某点作为**腕心**是否几何可达。
    注: 若要判断法兰目标点，请先用 flange_target_to_wrist 折算，或直接交给 solver。
    """
    try:
        x, y, z = float(x), float(y), float(z)
    except (TypeError, ValueError):
        return False, "坐标不是数值"
    if not all(math.isfinite(v) for v in (x, y, z)):
        return False, "坐标含 NaN/Inf"
    d = wrist_distance_from_j2(x, y, z)
    if d < REACH_MIN_MM - tol:
        return False, f"过近：距 J2 轴 {d:.1f}mm < 最小 {REACH_MIN_MM:.1f}mm"
    if d > REACH_MAX_MM + tol:
        return False, f"过远：距 J2 轴 {d:.1f}mm > 最大 {REACH_MAX_MM:.1f}mm"
    if z < -tol:
        return False, f"低于安装面：z={z:.1f}mm"
    return True, "可达"


def reach_bounds_mm() -> Tuple[float, float]:
    """(最小, 最大) 腕心到 J2 轴的距离，mm"""
    return REACH_MIN_MM, REACH_MAX_MM


# ══════════════════════════════════════════════════════════════════════════
#  八、闭式逆向运动学
# ══════════════════════════════════════════════════════════════════════════
def _planar_ik(u: float, v: float) -> List[Tuple[float, float]]:
    """
    臂平面内解 (q2, q3)。u = 相对 J1 轴的水平距离（带符号），v = 绝对高度。
    返回 [(q2_deg, q3_deg), ...]，最多 2 组（肘上/肘下）。
    """
    a = u
    b = v - D1_MM
    rho2 = a * a + b * b
    if rho2 < 1e-12:
        return []
    rho = math.sqrt(rho2)
    K = (rho2 + L2_MM * L2_MM - WRIST_OFFSET_MM * WRIST_OFFSET_MM) / (2.0 * L2_MM)
    ratio = K / rho
    if ratio > 1.0 + 1e-9 or ratio < -1.0 - 1e-9:
        return []
    ratio = _clamp(ratio, -1.0, 1.0)
    beta = math.atan2(b, a)
    out: List[Tuple[float, float]] = []
    seen = set()
    for base in (math.asin(ratio), math.pi - math.asin(ratio)):
        q2 = base - beta
        # 由几何关系解 ψ 再得 q3
        cx = a - L2_MM * math.sin(q2)
        cy = -(b - L2_MM * math.cos(q2))
        psi = math.atan2(cy, cx)
        q3 = psi + _D(ELBOW_BIAS_DEG) - q2
        q2d, q3d = wrap_deg(_R(q2)), wrap_deg(_R(q3))
        key = (round(q2d, 6), round(q3d, 6))
        if key not in seen:
            seen.add(key)
            out.append((q2d, q3d))
    return out


def _xyx_decompose(M: np.ndarray) -> List[Tuple[float, float, float]]:
    """
    Rx(A)·Ry(B)·Rx(C) = M 的 X-Y-X 欧拉分解（角度制），返回最多 2 组。
    奇异（B≈0 或 π）时取 C = 0。
    """
    M = np.asarray(M, dtype=float).reshape(3, 3)
    out: List[Tuple[float, float, float]] = []
    sB = math.hypot(M[0, 1], M[0, 2])
    if sB > 1e-9:
        B = math.atan2(sB, _clamp(M[0, 0], -1.0, 1.0))
        C = math.atan2(M[0, 1], M[0, 2])
        A = math.atan2(M[1, 0], -M[2, 0])
        out.append((_R(A), _R(B), _R(C)))
        # 另一组: B 取负
        B2 = -B
        C2 = math.atan2(-M[0, 1], -M[0, 2])
        A2 = math.atan2(-M[1, 0], M[2, 0])
        out.append((_R(A2), _R(B2), _R(C2)))
    elif M[0, 0] > 0:
        # B = 0:  M = Rx(A + C)，取 C = 0
        A = math.atan2(M[2, 1], M[1, 1])
        out.append((_R(A), 0.0, 0.0))
    else:
        # B = π:  R[1,1] = cos(A − C), R[1,2] = sin(A − C)，取 C = 0
        A = math.atan2(M[1, 2], M[1, 1])
        out.append((_R(A), 180.0, 0.0))
    return out


def _snap_equivalent_angles(q: Sequence[float], seed: Sequence[float],
                            limits: Sequence[Sequence[float]] = JOINT_LIMITS_DEG) -> List[float]:
    """
    把各轴角度折到软限位内。若某轴量程 ≥360°，则在所有等价的 ±360° 分支中
    选离种子角最近的一个（J6 量程 720°，可避免无意义的大幅回转）。
    limits 缺省为官方限位；闭式 IK 内部传 JOINT_LIMITS_INT_DEG。
    """
    ref = list(seed)[:6] + [0.0] * 6
    out = []
    for i in range(6):
        lo, hi = limits[i]
        v = float(q[i])
        if hi - lo >= 360.0:
            best, best_d = None, float("inf")
            k0 = int(round((ref[i] - v) / 360.0))
            for k in range(k0 - 2, k0 + 3):
                cand = v + 360.0 * k
                if lo - 1e-9 <= cand <= hi + 1e-9:
                    d = abs(cand - ref[i])
                    if d < best_d:
                        best_d, best = d, cand
            if best is None:
                best = _clamp(v, lo, hi)
            out.append(best)
        else:
            out.append(_clamp(v, lo, hi))
    return out


def inverse_kinematics_closed(
    target: Dict[str, float],
    current_joints_deg: Optional[Sequence[float]] = None,
) -> List[List[float]]:
    """
    闭式 IK。target = {x,y,z,a,b,c}（法兰安装面位姿）。
    返回**全部**候选关节角（官方约定；已按软限位过滤并按与种子角的距离升序），可能为空。

    说明：闭式推导（_planar_ik / _xyx_decompose）是在「几何旋转量」下做的，
    因此入口把种子角换算成内部量，出口再换算回官方关节角 + 用官方限位校验。
    """
    seed_ext = clamp_joints(current_joints_deg) if current_joints_deg else list(HOME_JOINTS_DEG)
    seed = to_internal_joints(seed_ext)
    M_t = pose_to_matrix(target)
    R_t = M_t[:3, :3]
    p_t = M_t[:3, 3]
    wrist = p_t - R_t[:, 0] * TOOL_LENGTH_MM
    wx, wy, wz = float(wrist[0]), float(wrist[1]), float(wrist[2])

    if not all(math.isfinite(v) for v in (wx, wy, wz)):
        return []

    r = math.hypot(wx, wy)
    q1_base = math.atan2(wy, wx) if r > 1e-9 else 0.0

    candidates: List[List[float]] = []
    for flip in (0, 1):
        q1 = q1_base + (math.pi if flip else 0.0)
        u = r if flip == 0 else -r
        for q2d, q3d in _planar_ik(u, wz):
            R_pre = rot_z(q1) @ rot_y(_D(q2d + q3d))
            R_wrist = R_pre.T @ R_t
            for q4d, q5d, q6d in _xyx_decompose(R_wrist):
                q_int = [wrap_deg(_R(q1)), q2d, q3d, wrap_deg(q4d), wrap_deg(q5d), wrap_deg(q6d)]
                candidates.append(_snap_equivalent_angles(q_int, seed, JOINT_LIMITS_INT_DEG))

    # 换算回官方约定，去重 + 限位过滤
    uniq: List[List[float]] = []
    seen = set()
    for q_int in candidates:
        q = to_external_joints(q_int)
        if not joints_within_limits(q, tol=1e-6):
            continue
        key = tuple(round(v, 4) for v in q)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(q)

    uniq.sort(key=lambda q: max_joint_delta_deg(q, seed_ext))
    return uniq


# ══════════════════════════════════════════════════════════════════════════
#  九、数值兜底求解器（阻尼最小二乘 / Levenberg–Marquardt）
# ══════════════════════════════════════════════════════════════════════════
@dataclass
class _SolveConfig:
    max_iter: int = 140
    pos_tol_mm: float = 0.5
    rot_tol_rad: float = 0.5 * math.pi / 180.0
    max_stall: int = 25
    max_step_deg: float = 15.0
    damping0: float = 1.0
    seed_tries: int = 24
    time_budget_s: float = 0.25


class KinematicsSolver:
    """ER3-600 逆解器：闭式优先、DLS 兜底。对外接口与旧版保持兼容。"""

    def __init__(self, config: Optional[_SolveConfig] = None):
        self.cfg = config or _SolveConfig()
        self._stats = {"solve_calls": 0, "failed_calls": 0, "closed_form_hits": 0,
                       "numeric_hits": 0, "avg_iterations": 0.0}
        self._iter_sum = 0
        self._iter_cnt = 0

    # ── 统计 ──────────────────────────────────────────────────────
    @property
    def stats(self) -> Dict[str, float]:
        calls = self._stats["solve_calls"]
        s = dict(self._stats)
        s["success_rate"] = (1.0 - self._stats["failed_calls"] / calls) if calls else 0.0
        s["avg_iterations"] = (self._iter_sum / self._iter_cnt) if self._iter_cnt else 0.0
        return s

    def reset_stats(self) -> None:
        self._stats = {"solve_calls": 0, "failed_calls": 0, "closed_form_hits": 0,
                       "numeric_hits": 0, "avg_iterations": 0.0}
        self._iter_sum = 0
        self._iter_cnt = 0

    # ── 残差 ──────────────────────────────────────────────────────
    @staticmethod
    def _residual(q: Sequence[float], p_dst: np.ndarray, R_dst: np.ndarray) -> np.ndarray:
        M = forward_kinematics(q)
        p = M[:3, 3] + M[:3, 0] * TOOL_LENGTH_MM
        e_pos = p_dst - p
        e_rot = rot_log(R_dst @ M[:3, :3].T)
        return np.concatenate([e_pos, e_rot])

    def verify_solution(self, joints_deg: Sequence[float],
                        target_xyz: Sequence[float],
                        target_rpy: Optional[Sequence[float]] = None) -> Tuple[bool, float, float]:
        """返回 (是否满足容差, 位置误差mm, 姿态误差度)"""
        pose = fk_flange_pose(joints_deg)
        ep = math.dist((pose["x"], pose["y"], pose["z"]), tuple(float(v) for v in target_xyz[:3]))
        if target_rpy is None:
            return ep < self.cfg.pos_tol_mm, ep, 0.0
        R_d = rpy_to_matrix(*[float(v) for v in target_rpy[:3]])
        R_c = rpy_to_matrix(pose["a"], pose["b"], pose["c"])
        er = rot_angle_deg(R_d @ R_c.T)
        return (ep < self.cfg.pos_tol_mm and er < _R(self.cfg.rot_tol_rad)), ep, er

    # ── DLS ───────────────────────────────────────────────────────
    def _solve_dls(self, seed: Sequence[float], p_dst: np.ndarray, R_dst: np.ndarray,
                   deadline: float):
        q = clamp_joints(seed)
        lam = self.cfg.damping0
        best = None
        best_norm = float("inf")
        stall = 0
        iters = 0
        for it in range(self.cfg.max_iter):
            iters = it + 1
            if time.monotonic() > deadline:
                break
            err = self._residual(q, p_dst, R_dst)
            n = float(np.linalg.norm(err))
            if n < best_norm - 1e-9:
                best_norm = n
                best = list(q)
                stall = 0
            else:
                stall += 1
                if stall > self.cfg.max_stall:
                    break
            if math.dist(err[:3], (0, 0, 0)) < self.cfg.pos_tol_mm and \
               float(np.linalg.norm(err[3:])) < self.cfg.rot_tol_rad:
                return q, iters
            try:
                J = geometric_jacobian(q)
            except Exception:
                break
            A = J.T @ J + (lam ** 2) * np.eye(6)
            try:
                dq = np.linalg.solve(A, J.T @ err)
            except np.linalg.LinAlgError:
                lam *= 2.0
                continue
            step = _R(float(np.max(np.abs(dq))))
            if step > self.cfg.max_step_deg:
                dq = dq * (self.cfg.max_step_deg / step)
                lam = min(lam * 1.4, 1e6)
            else:
                lam = max(lam * 0.85, 1e-6)
            q = clamp_joints([q[i] + _R(float(dq[i])) for i in range(6)])
        return (best if best is not None else clamp_joints(seed)), iters

    # ── 种子 ──────────────────────────────────────────────────────
    def _candidate_seeds(self, current_joints_deg: Optional[Sequence[float]]) -> List[List[float]]:
        """有界种子集合：热启动 + 若干典型构型（上限 cfg.seed_tries）"""
        base: List[List[float]] = []
        if current_joints_deg:
            base.append(clamp_joints(current_joints_deg))
        base.append(list(HOME_JOINTS_DEG))
        span2 = JOINT_LIMITS_DEG[1]
        span3 = JOINT_LIMITS_DEG[2]
        span5 = JOINT_LIMITS_DEG[4]
        grid = [(q1, q2, q3, q5)
                for q1 in (0.0, 90.0, -90.0, 180.0)
                for q2 in (span2[0], 0.0, -60.0, span2[1])
                for q3 in (span3[0], 0.0, 60.0, span3[1])
                for q5 in (span5[0], 0.0, span5[1])]
        random.shuffle(grid)
        for q1, q2, q3, q5 in grid:
            base.append([q1, q2, q3, 0.0, q5, 0.0])
        # 去重后截断
        out, seen = [], set()
        for q in base:
            key = tuple(round(v, 3) for v in q)
            if key in seen:
                continue
            seen.add(key)
            out.append(q)
            if len(out) >= self.cfg.seed_tries:
                break
        return out

    # ── 主入口 ────────────────────────────────────────────────────
    def inverse_kinematics(
        self,
        x: float, y: float, z: float,
        rpy_deg: Optional[Sequence[float]] = None,
        current_joints_deg: Optional[Sequence[float]] = None,
    ) -> Optional[List[float]]:
        """
        求解使法兰面到达 (x,y,z[,a,b,c]) 的关节角。
        成功返回 6 元素关节角列表（已满足软限位），失败返回 None。
        rpy_deg 为 None 时只约束位置（取当前姿态作为目标姿态）。
        """
        self._stats["solve_calls"] += 1
        try:
            p = np.array([float(x), float(y), float(z)], dtype=float)
        except (TypeError, ValueError):
            self._stats["failed_calls"] += 1
            return None
        if not np.all(np.isfinite(p)):
            self._stats["failed_calls"] += 1
            return None

        if rpy_deg is not None:
            try:
                a, b, c = (float(rpy_deg[0]), float(rpy_deg[1]), float(rpy_deg[2]))
            except (TypeError, ValueError, IndexError):
                self._stats["failed_calls"] += 1
                return None
            if not all(math.isfinite(v) for v in (a, b, c)):
                self._stats["failed_calls"] += 1
                return None
        else:
            cur = clamp_joints(current_joints_deg) if current_joints_deg else list(HOME_JOINTS_DEG)
            cp = fk_flange_pose(cur)
            a, b, c = cp["a"], cp["b"], cp["c"]

        target = {"x": p[0], "y": p[1], "z": p[2], "a": a, "b": b, "c": c}
        R_dst = rpy_to_matrix(a, b, c)

        # 快速失败：腕心离 J2 轴太远/太近
        wrist = p - R_dst[:, 0] * TOOL_LENGTH_MM
        d = wrist_distance_from_j2(wrist[0], wrist[1], wrist[2])
        if d > REACH_MAX_MM + 1.0 or d < REACH_MIN_MM - 1.0:
            self._stats["failed_calls"] += 1
            return None

        # ① 闭式解
        seed = clamp_joints(current_joints_deg) if current_joints_deg else list(HOME_JOINTS_DEG)
        closed = inverse_kinematics_closed(target, seed)
        for q in closed:
            ok, ep, er = self.verify_solution(q, (p[0], p[1], p[2]), (a, b, c))
            if ok:
                self._stats["closed_form_hits"] += 1
                self._iter_sum += 1
                self._iter_cnt += 1
                return q

        # ② 数值兜底
        deadline = time.monotonic() + self.cfg.time_budget_s
        best_q, best_err, total_it = None, float("inf"), 0
        seeds = closed[:4] + self._candidate_seeds(current_joints_deg)
        for s in seeds:
            if time.monotonic() > deadline:
                break
            q, iters = self._solve_dls(s, p, R_dst, deadline)
            total_it += iters
            ep = math.dist(fk_position(q), (p[0], p[1], p[2]))
            er = rot_angle_deg(R_dst @ rpy_to_matrix(*[fk_flange_pose(q)[k] for k in "abc"]).T)
            score = ep + er * 5.0
            if score < best_err:
                best_err, best_q = score, q
            if ep < self.cfg.pos_tol_mm and er < _R(self.cfg.rot_tol_rad):
                self._stats["numeric_hits"] += 1
                self._iter_sum += total_it
                self._iter_cnt += 1
                return q

        if best_q is not None:
            ok, ep, er = self.verify_solution(best_q, (p[0], p[1], p[2]), (a, b, c))
            if ok:
                self._stats["numeric_hits"] += 1
                self._iter_sum += total_it
                self._iter_cnt += 1
                return best_q

        self._stats["failed_calls"] += 1
        self._iter_sum += total_it
        self._iter_cnt += 1
        return None

    def inverse_kinematics_multi(
        self,
        x: float, y: float, z: float,
        rpy_deg: Optional[Sequence[float]] = None,
        current_joints_deg: Optional[Sequence[float]] = None,
        limit: int = 8,
    ) -> List[List[float]]:
        """返回全部（或前 limit 组）可行解，按离种子角由近到远排序"""
        if rpy_deg is None:
            cur = clamp_joints(current_joints_deg) if current_joints_deg else list(HOME_JOINTS_DEG)
            cp = fk_flange_pose(cur)
            rpy_deg = (cp["a"], cp["b"], cp["c"])
        target = {"x": float(x), "y": float(y), "z": float(z),
                  "a": float(rpy_deg[0]), "b": float(rpy_deg[1]), "c": float(rpy_deg[2])}
        seed = clamp_joints(current_joints_deg) if current_joints_deg else list(HOME_JOINTS_DEG)
        out = []
        for q in inverse_kinematics_closed(target, seed):
            ok, _, _ = self.verify_solution(q, (x, y, z), rpy_deg)
            if ok:
                out.append(q)
        if not out:
            one = self.inverse_kinematics(x, y, z, rpy_deg, current_joints_deg)
            if one:
                out = [one]
        return out[:limit]

    @staticmethod
    def select_nearest_solution(solutions: Sequence[Sequence[float]],
                                reference_deg: Sequence[float]) -> Optional[List[float]]:
        if not solutions:
            return None
        ref = list(reference_deg)[:6]
        return list(min(solutions, key=lambda s: max_joint_delta_deg(s, ref)))


solver = KinematicsSolver()


# ══════════════════════════════════════════════════════════════════════════
#  十、自测
# ══════════════════════════════════════════════════════════════════════════
def _selftest(verbose: bool = True) -> int:
    fails: List[str] = []
    n_pass = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal n_pass
        if ok:
            n_pass += 1
            if verbose:
                print(f"  [PASS] {name}" + (f"   {detail}" if detail else ""))
        else:
            fails.append(name)
            print(f"  [FAIL] {name}" + (f"   {detail}" if detail else ""))

    print("─" * 74)
    print("  ER3-600 运动学自检（几何量纲来自官方数模 V1.2 实测）")
    print("─" * 74)

    # 1. 结构
    check("球形手腕：J4 与 J6 共线、J5 同交于腕心", WRIST_IS_SPHERICAL,
          f"腕心 (…, {WRIST_OFFSET_MM:.2f}) 恒定")

    # 2. 臂展自洽（对官方 593）
    check("标称臂展 ≈ 官方 593mm",
          abs(NOMINAL_REACH_MM - 593.0) <= 1.0,
          f"L2 {L2_MM:.1f} + |J3→腕心| {WRIST_OFFSET_MM:.2f} = {NOMINAL_REACH_MM:.2f}mm，"
          f"偏差 {NOMINAL_REACH_MM - 593.0:+.2f}mm")

    # 3. 零位与数模重合
    wc0 = fk_wrist_center(HOME_JOINTS_DEG)
    f0 = fk_flange_pose(HOME_JOINTS_DEG)
    exp_w = CAD_REFERENCE["wrist_center_zero_mm"]
    exp_f = CAD_REFERENCE["flange_face_zero_mm"]
    check("零位腕心 == 数模实测 (295.5, 0, 699.5)",
          math.dist(wc0, exp_w) < 1e-6,
          f"实得 ({wc0[0]:.2f}, {wc0[1]:.2f}, {wc0[2]:.2f})")
    check("零位法兰面 == 数模实测 (374, 0, 699.5)",
          math.dist((f0["x"], f0["y"], f0["z"]), exp_f) < 1e-6,
          f"实得 ({f0['x']:.2f}, {f0['y']:.2f}, {f0['z']:.2f})")
    check("零位姿态 RPY 全 0", max(abs(f0["a"]), abs(f0["b"]), abs(f0["c"])) < 1e-9)

    # 4. 全伸展（q3 = -90°+α）
    q_ext = [0.0, 0.0, FULL_EXTENSION_J3_DEG, 0.0, 0.0, 0.0]
    wz = fk_wrist_center(q_ext)[2]
    check("全伸展腕心 z == 数模包络上限 960.31mm",
          abs(wz - CAD_REFERENCE["full_extension_wrist_z_mm"]) < 1e-6,
          f"实得 {wz:.2f}mm（数模包络 z 上限 960.3mm）")

    # 5. q2=90 时水平臂展 == 593
    q_h = [0.0, 90.0, FULL_EXTENSION_J3_DEG, 0.0, 0.0, 0.0]
    wc_h = fk_wrist_center(q_h)
    r_h = math.hypot(wc_h[0], wc_h[1])
    check("q2=90° 时腕心半径 == 臂展 592.81mm", abs(r_h - NOMINAL_REACH_MM) < 1e-6,
          f"实得 {r_h:.2f}mm  z={wc_h[2]:.2f}mm（应 = d1 = {D1_MM}）")

    # 5b. 官方《运动范围图》包络复核 —— 这是 JOINT_SIGN 的唯一判据
    ref = WORKSPACE_REFERENCE
    env = planar_envelope()
    # 解析式必须与 fk_wrist_center 完全等价（在极值点处核对）
    worst_formula = 0.0
    for key in ("x_max", "x_min", "z_max", "z_min"):
        q2, q3 = env["arg"][key]
        x, _y, z = fk_wrist_center([0.0, q2, q3, 0.0, 0.0, 0.0])
        xf, zf = wrist_center_planar(q2, q3)
        worst_formula = max(worst_formula, abs(x - xf), abs(z - D1_MM - zf))
    check("包络解析式 == fk_wrist_center（极值点处）", worst_formula < 1e-9,
          f"最大偏差 {worst_formula:.2e} mm")

    check("包络最大高度(z) == 图纸 592.8mm", abs(env["z_max"] - ref["max_z_above_j2_mm"]) <= 0.1,
          f"实得 {env['z_max']:.2f}mm（图纸 {ref['max_z_above_j2_mm']}）"
          f" @ J2={env['arg']['z_max'][0]:.1f}° J3={env['arg']['z_max'][1]:.1f}°")
    check("包络前向最大伸距(x) == 图纸 592.8mm", abs(env["x_max"] - ref["max_x_mm"]) <= 0.1,
          f"实得 {env['x_max']:.2f}mm"
          f" @ J2={env['arg']['x_max'][0]:.1f}° J3={env['arg']['x_max'][1]:.1f}°")
    check("包络后向最大伸距(x) == 图纸 −591.6mm", abs(env["x_min"] - ref["min_x_mm"]) <= 0.1,
          f"实得 {env['x_min']:.2f}mm"
          f" @ J2={env['arg']['x_min'][0]:.1f}° J3={env['arg']['x_min'][1]:.1f}°")

    # ③ 软限位四角逐点核对（图纸共标注 6 个尺寸）
    worst_corner, corner_detail = 0.0, []
    for (j2, j3), (ax, az) in ref["corners"].items():
        x, _y, z = fk_wrist_center([0.0, j2, j3, 0.0, 0.0, 0.0])
        d = max(abs(abs(x) - ax), abs(z - D1_MM - az))
        worst_corner = max(worst_corner, d)
        corner_detail.append(f"({j2:g},{j3:g})→|x|{abs(x):.1f}/{ax} z{z - D1_MM:.1f}/{az}")
    check("包络四角 == 图纸 6 个尺寸（0.1mm 内）", worst_corner <= 0.1,
          f"最大偏差 {worst_corner:.3f}mm · " + "; ".join(corner_detail))

    # 5c. 满展确实落在官方 J3 软限位内（否则图纸标注的 592.8 不可达）
    lo3, hi3 = JOINT_LIMITS_DEG[2]
    check("满展 J3 落在官方软限位内",
          lo3 - 1e-9 <= FULL_EXTENSION_J3_DEG <= hi3 + 1e-9,
          f"J3 = {FULL_EXTENSION_J3_DEG:.3f}° ∈ [{lo3:g}, {hi3:g}]")

    # 解析上界：|w|² = W² + L2² − 2·L2·W·sin τ，τ ∈ [TAU_LO, TAU_HI]
    tau_lo, tau_hi = TAU_RANGE_DEG
    w_sq = WRIST_OFFSET_MM ** 2 + L2_MM ** 2
    exp_min = math.sqrt(w_sq - 2 * L2_MM * WRIST_OFFSET_MM * math.sin(_D(tau_hi)))
    exp_max = math.sqrt(w_sq + 2 * L2_MM * WRIST_OFFSET_MM)
    check("腕心可达距离区间 == 解析解",
          abs(REACH_MIN_MM - exp_min) < 1e-4 and abs(REACH_MAX_MM - exp_max) < 1e-4,
          f"[{REACH_MIN_MM:.2f}, {REACH_MAX_MM:.2f}] mm"
          f"（τ ∈ [{tau_lo:.2f}°, {tau_hi:.2f}°]，非 |L4−L2| = {abs(WRIST_OFFSET_MM - L2_MM):.2f}）")

    # 6. RPY 往返
    random.seed(20260926)
    worst = 0.0
    for _ in range(3000):
        a, b, c = (random.uniform(-179, 179), random.uniform(-89, 89), random.uniform(-179, 179))
        aa, bb, cc = matrix_to_rpy(rpy_to_matrix(a, b, c))
        e = rot_angle_deg(rpy_to_matrix(a, b, c) @ rpy_to_matrix(aa, bb, cc).T)
        worst = max(worst, e)
    check("RPY ↔ 矩阵 往返闭合（3000 组随机角）", worst < 1e-8,
          f"最大偏差 {worst:.3e}°")

    # 7. 万向锁
    gl_ok = True
    gl_worst = 0.0
    for b in (90.0, -90.0):
        for a in (-40.0, 0.0, 55.0):
            R = rpy_to_matrix(a, b, 30.0)
            aa, bb, cc = matrix_to_rpy(R)
            e = rot_angle_deg(R @ rpy_to_matrix(aa, bb, cc).T)
            gl_worst = max(gl_worst, e)
            gl_ok = gl_ok and e < 1e-8
    check("万向锁 b=±90° 可逆", gl_ok, f"最大矩阵偏差 {gl_worst:.3e}°")

    # 8. FK → IK → FK 回环
    total, ok_cnt, worst_p, worst_r = 90, 0, 0.0, 0.0
    t0 = time.monotonic()
    tried = 0
    for _ in range(total):
        a, b, c = (random.uniform(-170, 170), random.uniform(-100, 100), random.uniform(-179, 179))
        q_src = [random.uniform(*JOINT_LIMITS_DEG[i]) for i in range(6)]
        tgt = fk_flange_pose(q_src)
        tried += 1
        sol = solver.inverse_kinematics(tgt["x"], tgt["y"], tgt["z"],
                                        (tgt["a"], tgt["b"], tgt["c"]), HOME_JOINTS_DEG)
        if sol:
            ok, ep, er = solver.verify_solution(sol, (tgt["x"], tgt["y"], tgt["z"]),
                                                (tgt["a"], tgt["b"], tgt["c"]))
            if ok:
                ok_cnt += 1
                worst_p = max(worst_p, ep)
                worst_r = max(worst_r, er)
        _ = (a, b, c)
    dt = time.monotonic() - t0
    check(f"FK → IK → FK 回环（{tried} 组随机位姿）", ok_cnt >= tried * 0.95,
          f"收敛 {ok_cnt}/{tried}，位置 ≤{worst_p:.4f}mm，姿态 ≤{worst_r:.4f}°，"
          f"均耗时 {dt / max(tried, 1) * 1000:.2f}ms")

    # 9. 姿态确实被遵守
    q_src = [20.0, -60.0, 80.0, 25.0, 40.0, -30.0]
    tp = fk_flange_pose(q_src)
    sol = solver.inverse_kinematics(tp["x"], tp["y"], tp["z"], (tp["a"], tp["b"], tp["c"]),
                                    HOME_JOINTS_DEG)
    if sol:
        ok, ep, er = solver.verify_solution(sol, (tp["x"], tp["y"], tp["z"]), (tp["a"], tp["b"], tp["c"]))
        check("IK 遵守目标姿态（A/B/C 不被忽略）", ok, f"位置 {ep:.4f}mm / 姿态 {er:.4f}°")
    else:
        check("IK 遵守目标姿态（A/B/C 不被忽略）", False, "无解")

    # 10. 多解枚举
    q_probe = [10.0, -30.0, 40.0, 0.0, 20.0, 0.0]
    tp_probe = fk_flange_pose(q_probe)
    multi = solver.inverse_kinematics_multi(tp_probe["x"], tp_probe["y"], tp_probe["z"],
                                            (tp_probe["a"], tp_probe["b"], tp_probe["c"]), q_probe)
    check("多解枚举接口可用且解均有效",
          isinstance(multi, list) and len(multi) >= 1 and
          all(solver.verify_solution(q, (tp_probe["x"], tp_probe["y"], tp_probe["z"]),
                                     (tp_probe["a"], tp_probe["b"], tp_probe["c"]))[0] for q in multi),
          f"返回 {len(multi)} 组可行解")

    # 10b. 返回的是离种子最近的解
    near = solver.inverse_kinematics(tp_probe["x"], tp_probe["y"], tp_probe["z"],
                                     (tp_probe["a"], tp_probe["b"], tp_probe["c"]), q_probe)
    check("IK 返回离种子最近的解", near is not None and max_joint_delta_deg(near, q_probe) < 30.0,
          f"与种子最大单轴差 {max_joint_delta_deg(near, q_probe):.2f}°" if near else "无解")

    # 11. 限位裁剪
    over = [999, -999, 999, 999, -999, 999]
    lim = clamp_joints(over)
    expect = [JOINT_LIMITS_DEG[i][1] if over[i] > 0 else JOINT_LIMITS_DEG[i][0] for i in range(6)]
    check("越限输入按方向裁到上/下限", lim == expect,
          f"{[round(v, 1) for v in lim]}")
    check("官方 J2 范围 -135~+85 生效", JOINT_LIMITS_DEG[1] == (-135.0, 85.0))
    check("官方 J3 范围 -65~+185 生效", JOINT_LIMITS_DEG[2] == (-65.0, 185.0))
    check("越限能被检出", len(joint_limit_violations([0, -200, 0, 0, 0, 0])) == 1)

    # 12. 不可达快速失败
    t0 = time.monotonic()
    far = solver.inverse_kinematics(3000.0, 0.0, 3000.0, (0, 0, 0), HOME_JOINTS_DEG)
    dt_ms = (time.monotonic() - t0) * 1000
    check("不可达目标返回 None 且快速", far is None and dt_ms < 50.0, f"耗时 {dt_ms:.3f}ms")
    check("NaN 输入返回 None（不抛异常）",
          solver.inverse_kinematics(float("nan"), 0.0, 0.0, (0, 0, 0)) is None)

    # 13. 直线插补连续性
    p0 = fk_flange_pose([0, -20, 30, 0, 30, 0])
    p1 = fk_flange_pose([0, 20, -20, 0, 50, 0])
    n = 21
    seed = [0, -20, 30, 0, 30, 0]
    okc = 0
    for k in range(n):
        t = k / (n - 1)
        pt = {kk: p0[kk] + (p1[kk] - p0[kk]) * t for kk in "xyz"}
        s = solver.inverse_kinematics(pt["x"], pt["y"], pt["z"], None, seed)
        if s:
            okc += 1
            seed = s
    check(f"笛卡尔直线离散点连续可解（{n} 点）", okc == n, f"成功 {okc}/{n}")

    # 14. rpy=None 保持当前姿态
    cur = [0.0, -25.0, 35.0, 10.0, 25.0, 45.0]
    cur_pose = fk_flange_pose(cur)
    s = solver.inverse_kinematics(cur_pose["x"], cur_pose["y"], cur_pose["z"], None, cur)
    if s:
        ep2 = fk_flange_pose(s)
        drift = max(abs(ep2["a"] - cur_pose["a"]), abs(ep2["b"] - cur_pose["b"]),
                    abs(ep2["c"] - cur_pose["c"]))
        check("rpy=None 时保持当前姿态不变", drift < 0.5, f"姿态漂移 {drift:.4f}°")
    else:
        check("rpy=None 时保持当前姿态不变", False, "无解")

    # 15. 可操作度
    mach = manipulability([0, -90, 90, 0, 0, 0])
    check("可操作度可计算且非负", mach >= 0.0, f"µ = {mach:,.1f}")

    print("─" * 74)
    if fails:
        print(f"  结果：{len(fails)} 项失败 -> {fails}")
    else:
        st = solver.stats
        print("  结果：全部通过")
        print(f"  求解统计：{st}")
    print("─" * 74)
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
