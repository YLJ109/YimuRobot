// constants/robot.js — ER3-600 前端常量（唯一真相源）
//
// ⚠️ 本文件必须与 backend/kinematics.py 的同名常量逐字一致。
//    任何一侧改动，另一侧必须同步，否则仿真会与后端运动学静默跑偏。
//
// 几何量纲来源：官方 ER3-600 机器人数模 V1.2.STEP（埃夫特官网下载中心），
//    由 tools/step2glb/cyl_axes.py 对 CYLINDRICAL_SURFACE 实体做共轴聚类实测得到，
//    不是经验估计值。自洽性：大臂 295.0 + |J3→腕心| 297.81 = 592.81mm ≈ 官方标称 593mm。

export const ROBOT_NAME = 'ER3-600'
export const AXIS_COUNT = 6

// ── 数模文件 ────────────────────────────────────────────────
export const MODEL_URL = '/models/er3_600.glb'
export const MODEL_META_URL = '/models/er3_600_joints.json'

// ── 零位与软限位（deg，与 kinematics.py 的 HOME_JOINTS_DEG / JOINT_LIMITS_DEG 一致）──
export const HOME_JOINTS_DEG = [0, 0, 0, 0, 0, 0]

export const JOINT_LIMITS_DEG = [
  [-170, 170],   // J1
  [-135, 85],    // J2
  [-65, 185],    // J3
  [-190, 190],   // J4
  [-130, 130],   // J5
  [-360, 360],   // J6
]

// 各轴最大速度（°/s），用于前端的动作时长估算与超限提示
export const JOINT_MAX_SPEED_DEG_S = [400, 300, 520, 500, 530, 840]

// 向后兼容：老代码用单一对称上限做粗判，保留 J1 的上限作为代表值
export const JOINT_LIMIT_DEG = 170

// ── 实测几何（mm，CAD 坐标系 Z 轴向上，原点在基座安装面中心）──
export const CAD_MEASURED_MM = {
  d1: 367.5,      // J1 轴 → J2 轴（沿 +Z）
  L2: 295.0,      // J2 → J3（大臂）
  e3: 37.0,       // J3 → J4（肘部偏置，恒垂直于 L4）
  L4: 295.5,      // J4 → J5（小臂）
  tool: 78.5,     // 腕心 → 法兰安装面（沿 J6 轴 +X）
}

export const TOOL_LENGTH_MM = CAD_MEASURED_MM.tool
export const WRIST_OFFSET_MM = Math.hypot(CAD_MEASURED_MM.e3, CAD_MEASURED_MM.L4)   // 297.81
export const NOMINAL_REACH_MM = CAD_MEASURED_MM.L2 + WRIST_OFFSET_MM                // 592.81
export const MAX_REACH_MM = 593.0            // 官方标称
export const WRIST_HEIGHT_AT_ZERO_MM = CAD_MEASURED_MM.d1 + CAD_MEASURED_MM.L2 + CAD_MEASURED_MM.e3 // 699.5

// ── 肘部几何偏置与满展姿态（与 backend/kinematics.py 同名常量一致）──
// 小臂相对大臂的固定夹角：atan2(e3, L4) = 7.1369°
export const ELBOW_BIAS_DEG = Math.atan2(CAD_MEASURED_MM.e3, CAD_MEASURED_MM.L4) * 180 / Math.PI
// 把手臂完全打直（腕心落在 J2 轴线正上方）所需的 J3 官方角
export const FULL_EXTENSION_J3_DEG = 90 - ELBOW_BIAS_DEG                                  // 82.863
/** 竖直位：手臂完全打直朝上，腕心 (0,0,960.3) —— 常用的标定/待机姿态 */
export const VERTICAL_JOINTS_DEG = [0, 0, FULL_EXTENSION_J3_DEG, 0, 0, 0]

// 真空吸盘（仿真附加工具，非机器人本体；法兰面 → 吸盘唇口）
//
// ⚠️ 必须等于 TOOL_LENGTH_MM。后端 executor 的 PICK/PLACE 把「法兰面」放到
//    contact_z + TOOL_LENGTH_MM，也就是把 TCP 目标点当成法兰面，并期望唇口
//    恰好落在 contact_z（物体顶面）。若这条杆比 TOOL_LENGTH_MM 短，3D 唇口会
//    悬在物体上方够不到 → checkContact 永远不命中 → 吸盘看起来"吸不住"。
export const GRIPPER_LENGTH_MM = TOOL_LENGTH_MM

// 数模内部单位是米，前端场景单位是毫米
export const M_TO_MM = 1000

// 关节节点的局部旋转轴（由数模实测：所有关节节点局部系与 CAD 系同向）
export const JOINT_AXIS = { J1: 'z', J2: 'y', J3: 'y', J4: 'x', J5: 'y', J6: 'x' }

// ── 关节旋转正方向（⚠️ 必须与 backend/kinematics.py 的 JOINT_SIGN 逐位一致）──
// 数模轴线只给出「转轴在哪」，不给出「哪个方向算正」。正方向由官方
// 《ER3-600 运动范围图 V1.1》标注的工作空间包络反解得到：
//   (±J2, ±J3) 四种符号组合扫包络，对图纸 592.8 / 592.8 / −591.6 三个极值的
//   总偏差分别为 20.34 / 2.33 / 37.67 / **0.10** mm —— 只有 (−,−) 命中；
//   再用 (J2,J3) 软限位四角复核图纸另外 6 个尺寸（81.7 / 142.3 / 28.9 /
//   161.6 / 256.9 / 269.8），最大偏差 0.053 mm。
// J1/J4/J5/J6 量程对称，包络无法区分正负，暂取 +1，真机到货后按点动方向标定。
export const JOINT_SIGN = [1, -1, -1, 1, 1, 1]

// ── 工具函数 ────────────────────────────────────────────────
/** 按各轴软限位裁剪 6 轴关节角（输入不足补 0，非有限值归 0） */
export function clampJoints(deg) {
  const src = Array.isArray(deg) ? deg : []
  const out = new Array(AXIS_COUNT)
  for (let i = 0; i < AXIS_COUNT; i++) {
    const v = Number(src[i])
    const x = Number.isFinite(v) ? v : 0
    const [lo, hi] = JOINT_LIMITS_DEG[i]
    out[i] = x < lo ? lo : (x > hi ? hi : x)
  }
  return out
}

/** 是否存在越限轴（返回越限轴序号数组，0 基） */
export function jointLimitViolations(deg) {
  const src = Array.isArray(deg) ? deg : []
  const bad = []
  for (let i = 0; i < AXIS_COUNT; i++) {
    const v = Number(src[i])
    const [lo, hi] = JOINT_LIMITS_DEG[i]
    if (!Number.isFinite(v) || v < lo || v > hi) bad.push(i)
  }
  return bad
}
