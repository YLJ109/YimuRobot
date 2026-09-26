// RobotIK.js — 前端逆运动学（数值解）
//
// ══════════════════════════════════════════════════════════════════════════
//  为什么用数值解而不是照抄后端的闭式解？
// ══════════════════════════════════════════════════════════════════════════
//  后端的闭式解建立在「解析 D-H 参数」之上，而前端的几何真相是官方 GLB 数模
//  的节点层级。两者虽然已被 FK 一致性自检证明等价，但一旦数模更新、或有人
//  在 RobotArm 里改了挂载方式，照抄一份解析 IK 就会与渲染静默脱节。
//
//  这里改用**数模本身当 FK 预言机**：直接借用 RobotArm.updateJoints +
//  getTcpPose 读位姿，数值差分得到雅可比，再做阻尼最小二乘（DLS）迭代。
//  这样「逆解出的关节角」与「屏幕上看到的姿态」恒等，不可能不一致。
//
// ══════════════════════════════════════════════════════════════════════════
//  单轴模式（axisKey）—— 笛卡尔滑杆的正确语义
// ══════════════════════════════════════════════════════════════════════════
//  最初把滑杆实现成「六自由度精确目标」：拖 X 时把 Y/Z/A/B/C 一起锁死。
//  这个约束在物理上**绝大多数位置都无解** —— 实测把 X 从 374 拖到 150
//  而要求其余五个自由度纹丝不动，连后端闭式解都直接返回 None。
//  于是用户每拖一下就弹「逆解失败：不可达或陷入局部极小」。
//
//  正确的语义是单轴约束：
//      · 被拖动的那一轴 → 硬约束，必须到达；
//      · 其余五个自由度 → 软约束（权重很小），让位给硬约束。
//  这正好对应操作者点动时的心智模型：「我让它往 X 走，姿态和别的轴
//  自己会跟着调」。此时求解难度也从「几乎无解」降到「基本都有解」。
//
// ══════════════════════════════════════════════════════════════════════════
//  坐标系约定
// ══════════════════════════════════════════════════════════════════════════
//  入参与出参的位姿均在**基座系**（CAD Z-up、毫米、ZYX-RPY 度），与
//  RobotArm.getTcpPose() / 后端 fk_flange_pose() 完全同义。
// ══════════════════════════════════════════════════════════════════════════

import {
  JOINT_LIMITS_DEG, AXIS_COUNT,
  HOME_JOINTS_DEG, VERTICAL_JOINTS_DEG,
} from '../constants/robot.js'

/** 姿态误差权重：1° 姿态误差 ≈ 该毫米数的位置误差 */
const ROT_WEIGHT = 2.0
/** 数值差分步长（deg）*/
const DIFF_STEP = 0.5
/** 阻尼系数（Levenberg–Marquardt λ）*/
const DAMPING = 0.35

/**
 * 单轴模式下「非目标轴」的权重。
 * 0.05 是实测出来的平衡点：
 *   · 太小（0.01）→ 其余轴会乱漂，解出的姿态不可预期；
 *   · 太大（0.3）→ 退化成六自由度硬约束，又开始大面积无解。
 */
const SOFT_WEIGHT = 0.05

/** 单轴模式下允许的硬约束残差 */
const AXIS_TOL_MM = 0.8
const AXIS_TOL_DEG = 0.4

const AXIS_KEYS = ['x', 'y', 'z', 'a', 'b', 'c']

function wrap180(a) {
  let x = a % 360
  if (x > 180) x -= 360
  if (x < -180) x += 360
  return x
}

function clampAll(q) {
  const out = new Array(AXIS_COUNT)
  for (let i = 0; i < AXIS_COUNT; i++) {
    const [lo, hi] = JOINT_LIMITS_DEG[i]
    out[i] = Math.max(lo, Math.min(hi, q[i]))
  }
  return out
}

/** 未加权误差向量（真实物理量：mm / deg） */
function rawError(target, cur) {
  return [
    target.x - cur.x,
    target.y - cur.y,
    target.z - cur.z,
    wrap180(target.a - cur.a),
    wrap180(target.b - cur.b),
    wrap180(target.c - cur.c),
  ]
}

/** 加权误差向量：位置 mm、姿态 deg×ROT_WEIGHT，再乘各自权重 */
function poseError(target, cur, w) {
  const e = rawError(target, cur)
  return [
    e[0] * w[0],
    e[1] * w[1],
    e[2] * w[2],
    e[3] * ROT_WEIGHT * w[3],
    e[4] * ROT_WEIGHT * w[4],
    e[5] * ROT_WEIGHT * w[5],
  ]
}

const UNIT_WEIGHTS = [1, 1, 1, 1, 1, 1]

/** 单轴模式权重：目标轴 1，其余 SOFT_WEIGHT */
function weightsForAxis(axisKey, soft) {
  const idx = AXIS_KEYS.indexOf(axisKey)
  const w = new Array(6).fill(soft)
  if (idx >= 0) w[idx] = 1
  return w
}

function norm6(v) {
  let s = 0
  for (let i = 0; i < 6; i++) s += v[i] * v[i]
  return Math.sqrt(s)
}

/**
 * 解线性方程组 A·x = b（A 为 6×6，部分选主元高斯消元）。
 * @returns {number[]|null} null 表示奇异
 */
function solve6(A, b) {
  const n = 6
  const m = A.map((row, i) => [...row, b[i]])
  for (let col = 0; col < n; col++) {
    let piv = col
    for (let r = col + 1; r < n; r++) {
      if (Math.abs(m[r][col]) > Math.abs(m[piv][col])) piv = r
    }
    if (Math.abs(m[piv][col]) < 1e-12) return null
    if (piv !== col) { const t = m[piv]; m[piv] = m[col]; m[col] = t }
    const d = m[col][col]
    for (let c = col; c <= n; c++) m[col][c] /= d
    for (let r = 0; r < n; r++) {
      if (r === col) continue
      const f = m[r][col]
      if (f === 0) continue
      for (let c = col; c <= n; c++) m[r][c] -= f * m[col][c]
    }
  }
  return m.map((row) => row[n])
}

/**
 * 单次尝试：从一个起点出发做 DLS 迭代。
 * @returns {{ok:boolean, joints:number[], posErr:number, rotErr:number, axisErr:number, iterations:number, score:number, reason?:string}}
 */
function attempt(arm, target, seed, opts) {
  const maxIter = opts.maxIter ?? 60
  const tolPos = opts.tolPos ?? 0.5
  const tolRot = opts.tolRot ?? 0.25
  const axisKey = opts.axisKey || null
  const axisIdx = axisKey ? AXIS_KEYS.indexOf(axisKey) : -1
  const w = opts.weights || UNIT_WEIGHTS

  const fk = (q) => { arm.updateJoints(q, true); return arm.getTcpPose() }

  let q = clampAll(seed)
  let cur = fk(q)
  let err = poseError(target, cur, w)
  let bestNorm = norm6(err)
  let iterations = 0

  const h = DIFF_STEP
  const H = [[], [], [], [], [], []]

  // 注意：`ok` 只表示「精确命中」。仿真场景下即使 ok=false，
  // 返回的 joints 也是尽力而为的最优解，调用方应当照常应用，
  // 让滑杆停在真实到达值上（见 ManualPanel.onCartChange）。
  const done = (ok, reason) => {
    const raw = rawError(target, cur)
    return {
      ok,
      joints: q,
      posErr: Math.hypot(raw[0], raw[1], raw[2]),
      rotErr: Math.max(Math.abs(raw[3]), Math.abs(raw[4]), Math.abs(raw[5])),
      axisErr: axisIdx >= 0 ? Math.abs(raw[axisIdx]) : null,
      iterations,
      score: bestNorm,
      reason,
    }
  }

  for (let it = 0; it < maxIter; it++) {
    iterations = it + 1

    // ── 收敛判定 ──
    if (axisIdx >= 0) {
      // 单轴模式：只看被拖的那一轴是否到位（其余是软约束，不参与判停）
      const raw = rawError(target, cur)
      const tol = axisIdx < 3 ? AXIS_TOL_MM : AXIS_TOL_DEG
      if (Math.abs(raw[axisIdx]) < tol) return done(true)
    } else {
      const raw = rawError(target, cur)
      const posErr = Math.hypot(raw[0], raw[1], raw[2])
      const rotErr = Math.max(Math.abs(raw[3]), Math.abs(raw[4]), Math.abs(raw[5]))
      if (posErr < tolPos && rotErr < tolRot) return done(true)
    }

    // ── 数值雅可比（中心差分，6×6）──
    //
    // ⚠️ H 必须是任务雅可比 ∂x/∂q（x = TCP 位姿），**不是** ∂(target−x)/∂q。
    //    这两者差一个负号，写错的话下降方向整体反向 —— 回溯线搜索从
    //    alpha=1 到 1/128 会**全部被拒**，于是每次都在第 1 次迭代就返回
    //    「不可达或陷入局部极小」。这正是笛卡尔滑杆一直拖不动的原因。
    //    推导：rawError(target, fk(qp)) = target − x(qp)，
    //          所以 x(qp) − x(qm) = rawError(qm) − rawError(qp)。
    for (let j = 0; j < AXIS_COUNT; j++) {
      const qp = [...q]; qp[j] += h
      const qm = [...q]; qm[j] -= h
      const ep = rawError(target, fk(qp))   // target − x(qp)
      const em = rawError(target, fk(qm))   // target − x(qm)
      for (let r = 0; r < 6; r++) {
        const scale = r < 3 ? w[r] : w[r] * ROT_WEIGHT
        H[r][j] = ((em[r] - ep[r]) / (2 * h)) * scale
      }
    }

    // ── 阻尼最小二乘：Δq = Jᵀ (J Jᵀ + λ²I)⁻¹ e ──
    const A = []
    for (let r = 0; r < 6; r++) {
      A.push(new Array(6).fill(0))
      for (let c = 0; c < 6; c++) {
        let s = 0
        for (let k = 0; k < 6; k++) s += H[r][k] * H[c][k]
        A[r][c] = s + (r === c ? DAMPING * DAMPING : 0)
      }
    }
    const y = solve6(A, err)
    if (!y) return done(false, '雅可比奇异')

    const dq = new Array(AXIS_COUNT)
    for (let j = 0; j < AXIS_COUNT; j++) {
      let s = 0
      for (let r = 0; r < 6; r++) s += H[r][j] * y[r]
      dq[j] = s
    }

    // ── 带限幅回溯的步长搜索 ──
    let alpha = 1.0
    let accepted = false
    for (let ls = 0; ls < 8; ls++) {
      const qn = clampAll(q.map((v, j) => v + alpha * dq[j]))
      const cn = fk(qn)
      const n = norm6(poseError(target, cn, w))
      if (n < bestNorm - 1e-6) {
        q = qn; cur = cn; err = poseError(target, cn, w); bestNorm = n
        accepted = true
        break
      }
      alpha *= 0.5
    }
    if (!accepted) return done(false, '不可达或陷入局部极小')
  }

  return done(false, '未完全收敛')
}

/**
 * 生成候选起点。
 * 第一个永远是「当前姿态」——单轴拖动时相邻两次目标的差很小，
 * 从这里出发基本 3~8 次迭代就收敛，多起点只是失败后的兜底。
 */
function buildSeeds(seedDeg, currentJoints) {
  const seeds = []
  const push = (q) => {
    if (!q || q.length !== AXIS_COUNT) return
    for (const s of seeds) {
      let same = true
      for (let i = 0; i < AXIS_COUNT; i++) if (Math.abs(s[i] - q[i]) > 1e-6) { same = false; break }
      if (same) return
    }
    seeds.push([...q])
  }
  push(seedDeg && seedDeg.length === AXIS_COUNT ? seedDeg : currentJoints)
  push(currentJoints)
  push(HOME_JOINTS_DEG)
  push(VERTICAL_JOINTS_DEG)
  // 两组确定性的抖动起点：肘部弯/直各一个，覆盖典型的两支解
  const base = seeds[0] || currentJoints || HOME_JOINTS_DEG
  push(base.map((v, i) => v + (i === 2 ? 45 : i === 4 ? -30 : 0)))
  push(base.map((v, i) => v + (i === 2 ? -45 : i === 4 ? 30 : 0)))
  return seeds
}

/**
 * 数值逆解。
 *
 * @param {import('./RobotArm.js').RobotArm} arm 已就绪的机械臂（作为 FK 预言机）
 * @param {{x:number,y:number,z:number,a:number,b:number,c:number}} target
 *        目标 TCP 位姿（基座系，mm + deg）
 * @param {number[]} [seedDeg] 首选迭代起点，默认取当前关节角
 * @param {object} [opts]
 *   @param {number}   [opts.maxIter=60]
 *   @param {number}   [opts.tolPos=0.5]     六自由度模式的收敛阈值
 *   @param {number}   [opts.tolRot=0.25]
 *   @param {string}   [opts.axisKey]        单轴模式：'x'|'y'|'z'|'a'|'b'|'c'
 *   @param {number}   [opts.softWeight]     单轴模式下非目标轴的权重
 *   @param {number[]} [opts.weights]        自定义六维权重（优先于 axisKey）
 * @returns {{ok:boolean, joints:number[], posErr:number, rotErr:number,
 *            axisErr:number|null, iterations:number, reason?:string}}
 */
export function solveIK(arm, target, seedDeg, opts = {}) {
  if (!arm || !arm.ready) {
    return {
      ok: false, joints: [...(seedDeg || [])],
      posErr: Infinity, rotErr: Infinity, axisErr: null, iterations: 0,
      reason: '数模未就绪',
    }
  }

  const axisKey = opts.axisKey || null
  const softWeight = opts.softWeight ?? SOFT_WEIGHT

  // 求解过程会临时改动机械臂姿态；结束后必须还原，避免视觉抖动
  const savedAngles = [...arm.jointAngles]
  const savedTargets = [...arm.targetAngles]

  const runSeeds = (weights, key) => {
    let best = null
    for (const seed of buildSeeds(seedDeg, savedAngles)) {
      const r = attempt(arm, target, seed, { ...opts, weights, axisKey: key })
      if (r.ok) return r
      if (!best || r.score < best.score) best = r
    }
    return best
  }

  try {
    // ── 阶段 1：六自由度精确命中 ──
    // 标准「世界坐标点动」语义：拖 X 就只沿 X 平移，Y/Z/A/B/C 保持不动。
    // 注意 opts.weights 是高级用法（自定义权重），给了就跳过这一阶段。
    if (!opts.weights) {
      const exact = runSeeds(UNIT_WEIGHTS, null)
      if (exact.ok) return exact
      // 六自由度解不到时才降级，避免无谓地把两轮都跑满
      if (!axisKey) return exact
    } else {
      const custom = runSeeds(opts.weights, axisKey)
      if (custom.ok) return custom
      if (!axisKey) return custom
    }

    // ── 阶段 2：单轴兜底 ──
    // 只要求被拖的那一轴到位，其余五个自由度让位。
    // 这在「角度轴」或接近构型极限时几乎总能给出一个可用的姿态，
    // 好过直接告诉用户「不可达」。
    return runSeeds(weightsForAxis(axisKey, softWeight), axisKey)
  } finally {
    arm.updateJoints(savedAngles, true)
    arm.setTargetAngles(savedTargets)
  }
}

/**
 * 用数模做一次正解（不改变可见姿态）。
 * @param {import('./RobotArm.js').RobotArm} arm
 * @param {number[]} qDeg 关节角（官方角，deg）
 * @returns {{x:number,y:number,z:number,a:number,b:number,c:number}|null} 基座系位姿
 */
export function fkOf(arm, qDeg) {
  if (!arm || !arm.ready) return null
  const savedAngles = [...arm.jointAngles]
  const savedTargets = [...arm.targetAngles]
  try {
    arm.updateJoints(qDeg, true)
    return arm.getTcpPose()
  } finally {
    arm.updateJoints(savedAngles, true)
    arm.setTargetAngles(savedTargets)
  }
}

// ── 关于「可达性」──────────────────────────────────────────────
//
//  ⚠️ 这是**纯软件仿真**，不是真机，也不是数字孪生。
//
//  所以这里**没有**可达性门禁、没有安全高度、没有软限位拦截 ——
//  那些都是真机现场才需要的保护。仿真里唯一该做的事是：拖到哪，
//  就尽力把姿态算到哪。够不着就让被拖的那一轴停在极限位置，
//  滑杆自然跟着显示实际到达值，这本身就是最好的反馈，
//  不需要再弹一个红框告诉用户「不可达」。
//
//  （真机版本需要恢复的常量参考：腕心到 J2 轴距离带 [164.1, 592.81] mm）
