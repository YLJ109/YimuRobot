// stores/fence.js — 安全围栏状态
//
// ══════════════════════════════════════════════════════════════════════════
//  这是什么
// ══════════════════════════════════════════════════════════════════════════
//  一个**正方形**玻璃安全围栏：四面墙 + 四角柱，把机器人围在中间。
//  临近度分级（由 RobotViewport 每帧算出来写回 level）：
//      safe     安全      —— 绿透明玻璃
//      warn     警告(20%) —— 绿玻璃 + 黄边
//      danger   危险(10%) —— 红玻璃
//      collision 碰撞     —— 红玻璃闪烁 + 语音报警 + 自动停机
//
//  这是「客户端会话偏好」式状态：围栏尺寸/显隐是前端本地的事，不进后端
//  global_state（否则一个页面改了影响所有连着的客户端，违反 sid 隔离约定）。
//  后端只管机器人执行；围栏纯前端可视化 + 报警。
//
//  为什么是正方形：用户明确要求「正方形」的安全围栏，所以只用一个 half
//  同时约束 X / Z 两个方向（halfX === halfZ），expand/shrink 等比缩放。
// ══════════════════════════════════════════════════════════════════════════

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

// 警告带基准宽度（mm）：机器人与围栏之间的「安全距离」参考值。
//   · 余量 < 10% × REF(15mm) → danger（红）
//   · 余量 < 20% × REF(30mm) → warn（黄边）
//   · 否则                      → safe（绿）
//   · 余量 ≤ 0                  → collision（闪烁 + 报警）
// 这个带宽是固定的，与围栏当前尺寸无关 —— 围栏缩小时机器人更容易进入带内，
// 反过来放大围栏会把机器人「保护」得更宽松，符合直觉。
export const FENCE_REF_MM = 150

// 尺寸上下限（mm）：夹住 expand/shrink 与滑杆，避免围栏缩到机器人身上
// 或放大到把整个演示台吞掉。
const HALF_MIN = 300
const HALF_MAX = 1400
const HEIGHT_MIN = 200
const HEIGHT_MAX = 1600
const PILLAR_MIN = 8
const PILLAR_MAX = 60
const STEP = 50 // 每次扩大/缩小 50mm

// ── 默认值：必须让机器人「安全」─────────────────────────────────
// 这两个数是按 ER3-600 的真实包络定的，改之前先看下面的推导：
//   水平：MAX_REACH_MM = 593（腕心），工具再伸 78.5×2 ≈ 750 最坏情况。
//         半边 850 → 最坏余量 100mm，常规作业（物体在 480/560）余量 ~257mm，
//         都远大于 warn 带（30mm），所以启动和正常搬运都是绿的。
//   高度：零位腕心 699.5、法兰 ~778；TCP 上限位 850，再加工具 ~930。
//         height 1100 → 顶部余量 ~170mm，安全。
//   旧值 half=650 / height=700 是错的：700 比机器人零位还矮，
//   一开机顶部余量就是负的，围栏恒红（或恒闪），用户看到的就是"反了"。
export const FENCE_DEFAULT_HALF = 850
export const FENCE_DEFAULT_HEIGHT = 1100
export const FENCE_DEFAULT_PILLAR_R = 20

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, Number(v) || 0))

export const useFenceStore = defineStore('fence', () => {
  // ── 几何 ──────────────────────────────────────────────────
  const visible = ref(true)        // 是否显示围栏
  const half = ref(FENCE_DEFAULT_HALF)        // 半边长（正方形：X/Z 同向），整边长 = 2×half
  const height = ref(FENCE_DEFAULT_HEIGHT)    // 围栏高度（mm，沿场景 Y）
  const pillarR = ref(FENCE_DEFAULT_PILLAR_R) // 角柱半径（mm）

  // ── 状态（由 RobotViewport 每帧回写）────────────────────────
  const level = ref('safe')        // safe | warn | danger | collision
  const clearance = ref(FENCE_REF_MM) // 当前最近余量（mm），展示用
  const proximity = ref(0)         // 碰撞接近度 0~100（100 = 已碰撞）

  // ── 派生 ──────────────────────────────────────────────────
  const side = computed(() => half.value * 2)
  const sizeLabel = computed(() => `${side.value} × ${side.value} × ${height.value} mm`)

  // ── 操作 ──────────────────────────────────────────────────
  function setVisible(v) { visible.value = !!v }
  function expand(delta = STEP) { half.value = clamp(half.value + delta, HALF_MIN, HALF_MAX) }
  function shrink(delta = STEP) { half.value = clamp(half.value - delta, HALF_MIN, HALF_MAX) }
  function setHalf(v) { half.value = clamp(v, HALF_MIN, HALF_MAX) }
  function setHeight(v) { height.value = clamp(v, HEIGHT_MIN, HEIGHT_MAX) }
  function setPillarR(v) { pillarR.value = clamp(v, PILLAR_MIN, PILLAR_MAX) }
  function setLevel(l) { level.value = l }
  function setClearance(mm) { clearance.value = mm }
  function setProximity(p) { proximity.value = Math.max(0, Math.min(100, Number(p) || 0)) }

  return {
    visible, half, height, pillarR, level, clearance, proximity,
    FENCE_REF_MM,
    side, sizeLabel,
    setVisible, expand, shrink, setHalf, setHeight, setPillarR,
    setLevel, setClearance, setProximity,
  }
})
