// stores/robot.js — 机器人状态管理
//
// ══════════════════════════════════════════════════════════════════════════
//  数据流契约（改动前务必读完，历史 bug 就出在这里）
// ══════════════════════════════════════════════════════════════════════════
//  本 store 是「指令位置」的唯一真相源；RobotArm 是「实际位置」的唯一真相源。
//
//    joints       指令角度（deg）  ← 唯一写入方：用户输入 / 后端 robot_frame
//    actualJoints 实际角度（deg）  ← 唯一写入方：RobotViewport 的渲染循环
//    tcp          实际 TCP 位姿    ← 唯一写入方：RobotViewport 的渲染循环
//    jointRev     指令版本号      ← 每次指令变化 +1，供 watcher 精确触发
//    jointSnap    true = 下一次同步直接跳变，否则走补间
//
//  ⚠️ 严禁在渲染循环里回写 joints / 用 joints 表示「实际角度」。
//     历史实现里 RobotViewport.animate() 每帧执行
//         robotStore.joints = [...robotArm.jointAngles]
//     而滑杆写入 joints 后要等 Vue 的异步 watcher 才能生效 —— 回写抢在
//     watcher 之前把用户输入覆盖掉，于是「拖滑杆机器人纹丝不动」。
//     现在指令与实际彻底分流，同类问题不会再发生。
// ══════════════════════════════════════════════════════════════════════════

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  HOME_JOINTS_DEG,
  JOINT_LIMITS_DEG,
  JOINT_LIMIT_DEG,
  JOINT_MAX_SPEED_DEG_S,
  clampJoints,
} from '../constants/robot.js'

const HOME = [...HOME_JOINTS_DEG]
const RAD2DEG = 180 / Math.PI

export const useRobotStore = defineStore('robot', () => {
  // ── 关节 ──────────────────────────────────────────────────
  const joints = ref([...HOME])          // 指令角度
  const actualJoints = ref([...HOME])    // 实际角度（渲染回读）
  const jointSnap = ref(false)           // 下次同步是否跳变
  const jointRev = ref(0)                // 指令版本号

  // ── 位姿 ──────────────────────────────────────────────────
  const tcp = ref({ x: 0, y: 0, z: 0, a: 0, b: 0, c: 0 })   // 实际 TCP
  const tcpCommand = ref({ x: 0, y: 0, z: 0, a: 0, b: 0, c: 0 }) // 笛卡尔指令

  // ── 设备状态 ──────────────────────────────────────────────
  const suckOn = ref(false)
  const holding = ref(null)
  const execState = ref('idle') // idle/running/paused/stopped/finished/error
  const speed = ref(50)
  const currentLine = ref(0)
  const connected = ref(false)

  // ── 帧率统计（渲染循环驱动）────────────────────────────────
  const fps = ref(0)
  let frameCount = 0
  let lastFpsTime = Date.now()

  const JOINT_LIMIT = JOINT_LIMIT_DEG
  const JOINT_LIMITS = JOINT_LIMITS_DEG
  const JOINT_MAX_SPEED = JOINT_MAX_SPEED_DEG_S

  const isRunning = computed(() => execState.value === 'running')
  const isPaused = computed(() => execState.value === 'paused')

  // ══════════════ 指令写入（触发 watcher）══════════════
  //
  //  ⚠️ 默认 snap = true —— 这是**教学仿真**，不是真机。
  //     真机做加减速是安全需要；仿真里做补间只会让滑杆"拖了不动"，
  //     用户明确要求过「不要限制移动」。所以指令一律即时到位，
  //     拖动滑杆 = 机器人立刻跟到该角度，没有速度上限，也就没有追不上目标的问题。
  //
  /** 单轴写入（默认即时到位）*/
  function setJoint(index, degree, { snap = true } = {}) {
    if (index < 0 || index >= 6) return
    const [lo, hi] = JOINT_LIMITS[index]
    const v = Number(degree)
    const clamped = Math.max(lo, Math.min(hi, Number.isFinite(v) ? v : 0))
    const next = [...joints.value]
    next[index] = clamped
    joints.value = next
    jointSnap.value = !!snap
    jointRev.value++
  }

  /** 整组写入（默认即时到位）*/
  function setAllJoints(angles, { snap = true } = {}) {
    joints.value = clampJoints(angles)
    jointSnap.value = !!snap
    jointRev.value++
  }

  /** 后端 robot_frame 下发的关节角（弧度）*/
  function setJointsFromRad(jRad) {
    if (!Array.isArray(jRad) || jRad.length < 6) return
    joints.value = clampJoints(jRad.map((r) => r * RAD2DEG))
    jointSnap.value = false
    jointRev.value++
  }

  /** watcher 消费掉 snap 标志，避免影响下一次同步 */
  function ackJointSnap() { jointSnap.value = false }

  // ══════════════ 渲染回读（只读，不触发指令）══════════════
  function setActualJoints(angles) { actualJoints.value = angles }
  function setTcp(pose) { tcp.value = pose }
  function setTcpCommand(pose) { tcpCommand.value = { ...tcpCommand.value, ...pose } }
  function setConnected(on) { connected.value = !!on }

  // ══════════════ 其他设备状态 ══════════════
  function setSuck(on) {
    suckOn.value = !!on
    if (!on) holding.value = null
  }
  function setHolding(name) { holding.value = name }
  function setExecState(state) { execState.value = state }
  function setCurrentLine(line) { currentLine.value = line }
  function setSpeed(s) { speed.value = Math.max(1, Math.min(100, Number(s) || 50)) }

  function reset() {
    joints.value = [...HOME]
    actualJoints.value = [...HOME]
    jointRev.value++
    suckOn.value = false
    holding.value = null
    execState.value = 'idle'
    currentLine.value = 0
  }

  /**
   * 渲染循环每帧调用一次。
   *
   * 数字做指数平滑：按整秒统计的话，帧率读数会在 58/60/62 之间来回跳
   * （用户看到的「帧率一直跳」）。0.3 的平滑系数让读数稳定，又能反映趋势。
   * 浏览器 rAF 本身封顶在 ~60Hz，所以稳定值就是 60 上下，这是正常的。
   */
  function tickFps() {
    frameCount++
    const now = Date.now()
    const elapsed = now - lastFpsTime
    if (elapsed >= 500) {
      const instant = frameCount * 1000 / elapsed
      fps.value = fps.value > 0
        ? Math.round(fps.value * 0.7 + instant * 0.3)
        : Math.round(instant)
      frameCount = 0
      lastFpsTime = now
    }
  }

  return {
    joints, actualJoints, jointSnap, jointRev,
    tcp, tcpCommand, suckOn, holding, execState, speed,
    currentLine, connected, fps,
    JOINT_LIMIT, JOINT_LIMITS, JOINT_MAX_SPEED,
    isRunning, isPaused,
    setJoint, setAllJoints, setJointsFromRad, ackJointSnap,
    setActualJoints, setTcp, setTcpCommand, setConnected,
    setSuck, setHolding, setExecState, setCurrentLine, setSpeed,
    reset, tickFps,
  }
})
