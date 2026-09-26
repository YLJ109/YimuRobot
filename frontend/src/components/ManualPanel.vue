<template>
  <div class="manual-panel">
    <div class="panel-tabs-wrap">
      <el-tabs v-model="activeTab" class="panel-tabs">
        <el-tab-pane label="手动控制" name="manual">
          <!-- ── 模式切换：关节 / 笛卡尔 ── -->
          <div class="mode-switch">
            <button
              class="mode-btn"
              :class="{ active: controlMode === 'joint' }"
              @click="controlMode = 'joint'"
            >关节控制</button>
            <button
              class="mode-btn"
              :class="{ active: controlMode === 'cart' }"
              @click="switchToCart"
            >笛卡尔控制</button>
          </div>

          <!-- ═══ 关节控制 ═══ -->
          <div v-if="controlMode === 'joint'" class="section-block">
            <div class="section-title"><span>六轴关节角</span></div>
            <div class="joint-list">
              <div v-for="j in jointItems" :key="j.idx" class="joint-row">
                <div class="joint-head">
                  <span class="joint-name">{{ j.label }}</span>
                  <span class="joint-range">{{ j.min }}° ~ {{ j.max }}°</span>
                  <span class="joint-deg" :class="{ 'is-limit': j.atLimit }">{{ j.value.toFixed(1) }}°</span>
                </div>
                <div class="joint-track">
                  <el-slider
                    :model-value="j.value"
                    @update:model-value="j.onChange"
                    :min="j.min"
                    :max="j.max"
                    :step="0.5"
                    :show-tooltip="false"
                    size="small"
                  />
                </div>
              </div>
            </div>
          </div>

          <!-- ═══ 笛卡尔控制 ═══ -->
          <div v-else class="section-block">
            <div class="section-title">
              <span>TCP 位姿（基座系）</span>
              <button class="link-btn" @click="syncCart">同步当前</button>
            </div>

            <div class="cart-list">
              <div v-for="ax in CART_AXES" :key="ax.key" class="cart-row">
                <div class="cart-head">
                  <span class="cart-key">{{ ax.label }}</span>
                  <span class="cart-range">{{ ax.min }} ~ {{ ax.max }}{{ ax.unit }}</span>
                  <span class="cart-val">{{ cart[ax.key].toFixed(1) }}<em>{{ ax.unit }}</em></span>
                </div>
                <div class="cart-track">
                  <el-slider
                    :model-value="cart[ax.key]"
                    @update:model-value="(v) => onCartChange(ax.key, v)"
                    :min="ax.min"
                    :max="ax.max"
                    :step="ax.step"
                    :show-tooltip="false"
                    size="small"
                  />
                </div>
              </div>
            </div>

            <div v-if="!armReady" class="cart-hint">等待机器人数模加载…</div>
            <div v-else class="cart-hint">拖动任一轴即时逆解；其余轴受 IK 耦合会自动跟随，滑杆显示的是解算后的真实位姿。</div>
          </div>

          <!-- ── 吸盘工具 ── -->
          <div class="section-block">
            <div class="section-title"><span>吸盘工具</span></div>
            <div class="suck-row">
              <el-switch v-model="suckState" @change="onSuckChange" />
              <span class="suck-label">{{ suckState ? '吸取中' : '已释放' }}</span>
              <span v-if="robotStore.holding" class="holding-tag">{{ robotStore.holding }}</span>
            </div>
          </div>

          <!-- ── 快捷操作 ── -->
          <div class="section-block">
            <div class="section-title"><span>快捷操作</span></div>
            <div class="quick-row">
              <button class="quick-btn" @click="goHome"><el-icon><HomeFilled /></el-icon><span>回零位</span></button>
              <button class="quick-btn" @click="goVertical"><el-icon><Top /></el-icon><span>竖直位</span></button>
            </div>
          </div>

          <!-- ── 实际 TCP ── -->
          <div class="section-block">
            <div class="section-title"><span>实际 TCP 位姿</span></div>
            <div class="tcp-grid">
              <div class="tcp-cell" v-for="item in tcpItems" :key="item.label">
                <span class="tcp-k">{{ item.label }}</span><span class="tcp-v">{{ item.value }}</span><span class="tcp-u">{{ item.unit }}</span>
              </div>
            </div>
          </div>
        </el-tab-pane>

        <!-- 安全围栏做成独立组件：自带 scoped 样式，不跟手动控制页的样式互相污染。 -->
        <el-tab-pane label="安全围栏" name="fence"><SafetyFencePanel /></el-tab-pane>

        <el-tab-pane label="物体库" name="objects"><ObjectLibrary /></el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { HomeFilled, Top } from '@element-plus/icons-vue'
import { useRobotStore } from '../stores/robot.js'
import ObjectLibrary from './ObjectLibrary.vue'
import SafetyFencePanel from './SafetyFencePanel.vue'
import { HOME_JOINTS_DEG, JOINT_LIMITS_DEG, VERTICAL_JOINTS_DEG } from '../constants/robot.js'
import { solveIK, fkOf } from '../classes/RobotIK.js'
import { getArm, isArmReady } from '../classes/robotContext.js'

const robotStore = useRobotStore()
const activeTab = ref('manual')
const controlMode = ref('joint')
const suckState = ref(false)

// ── 笛卡尔控制状态 ──────────────────────────────────────────
//
// 量程说明（基座系，Z 轴向上、原点在基座安装面）：
//   X / Y 是水平面，机器人最大水平伸展 ~593mm，取 ±590 略留余量；
//   Z 从基座面向上升到腕心 699.5 + 法兰 78.5 ≈ 778mm，取 [80, 800]；
//   A/B/C 为 ZYX 欧拉角，取 ±180。
// 量程只是滑杆的显示边界，**不做拦截** —— 这是纯软件仿真，不是真机，
// 够不着时 IK 会把该轴停在极限位置，滑杆跟着显示实际到达值即可。
const CART_AXES = [
  { key: 'x', label: 'X', unit: 'mm', min: -600, max: 600, step: 1 },
  { key: 'y', label: 'Y', unit: 'mm', min: -600, max: 600, step: 1 },
  { key: 'z', label: 'Z', unit: 'mm', min: 50, max: 850, step: 1 },
  { key: 'a', label: 'A', unit: '°', min: -180, max: 180, step: 1 },
  { key: 'b', label: 'B', unit: '°', min: -180, max: 180, step: 1 },
  { key: 'c', label: 'C', unit: '°', min: -180, max: 180, step: 1 },
]
const cart = ref({ x: 0, y: 0, z: 0, a: 0, b: 0, c: 0 })
const armReady = ref(false)

// ── 逆解节流（拖动滑杆会产生高频 input 事件）────────────────
// 不直接在每个 input 里解算：一来 60 次/秒 × ~10ms 会吃掉整个帧预算，
// 二来中间的拖拽位置没意义。这里只保留**最新目标**，每帧最多解一次，
// 被丢掉的那些中间值本来也会被下一次覆盖。
let pendingCartTarget = null
let pendingCartAxis = null
let cartRafId = 0

function onCartChange(axisKey, value) {
  if (!armReady.value) return
  const base = { ...cart.value }
  base[axisKey] = value
  pendingCartTarget = base
  pendingCartAxis = axisKey
  if (!cartRafId) cartRafId = requestAnimationFrame(flushCartSolve)
}

function flushCartSolve() {
  cartRafId = 0
  const target = pendingCartTarget
  const axisKey = pendingCartAxis
  pendingCartTarget = null
  pendingCartAxis = null
  if (!target) return

  const arm = getArm()
  if (!arm || !arm.ready) return

  // axisKey = 单轴模式：被拖的那一轴是硬约束必须到位，其余五个自由度
  // 只做软约束、可以随 IK 自由让位。细节见 classes/RobotIK.js 顶部注释。
  const sol = solveIK(arm, target, robotStore.joints, {
    axisKey,
    maxIter: 60,
  })

  // ── 纯仿真：不做可达性拦截，也不弹错误 ──
  // sol.ok=false 只代表「没能精确命中」，sol.joints 依然是尽力而为的解。
  // 照常应用，让滑杆停在真实到达值上 —— 够不着时用户看到的是「滑杆
  // 自己退回极限位置」，这比一个红框提示直观得多。
  robotStore.setAllJoints(sol.joints)
  robotStore.setTcpCommand(target)
  // 同步后端：整组下发，保证后端 global_state 与画面一致
  window.dispatchEvent(new CustomEvent('sim_set_joints', { detail: { joints: sol.joints } }))

  // ── 「适配」关键一步 ──
  // 一个自由度动了，其余 5 个也会被 IK 一起带动。所以不能把滑杆留在用户
  // 拖出来的理想值上，必须用解算结果做一次正解，把 6 根滑杆全部刷成
  // **机器人真实到达的位姿**，否则滑杆显示与画面里的机器人会对不上。
  const real = fkOf(arm, sol.joints)
  if (real) cart.value = real
}

/** 轮询等待 RobotViewport 注册数模实例（GLB 是异步加载的）*/
let readyTimer = null
function pollArmReady() {
  if (isArmReady()) {
    armReady.value = true
    syncCart()
    if (readyTimer) { clearInterval(readyTimer); readyTimer = null }
  }
}

/** 用当前**指令关节角**做正解 → 指令位姿（与 IK 同源，不会与渲染回读打架）*/
function syncCart() {
  const p = fkOf(getArm(), robotStore.joints)
  if (p) cart.value = p
}

function switchToCart() {
  controlMode.value = 'cart'
  syncCart()
}

// ── 关节控制 ────────────────────────────────────────────────
const jointItems = computed(() => {
  const items = []
  for (let i = 0; i < 6; i++) {
    const [lo, hi] = JOINT_LIMITS_DEG[i]
    const v = robotStore.joints[i]
    items.push({
      idx: i,
      label: `J${i + 1}`,
      min: lo,
      max: hi,
      value: v,
      atLimit: v <= lo + 1e-6 || v >= hi - 1e-6,
      onChange: (val) => onJointChange(i, val),
    })
  }
  return items
})

const tcpItems = computed(() => [
  { label: 'X', value: robotStore.tcp.x.toFixed(2), unit: 'mm' },
  { label: 'Y', value: robotStore.tcp.y.toFixed(2), unit: 'mm' },
  { label: 'Z', value: robotStore.tcp.z.toFixed(2), unit: 'mm' },
  { label: 'A', value: robotStore.tcp.a.toFixed(2), unit: '°' },
  { label: 'B', value: robotStore.tcp.b.toFixed(2), unit: '°' },
  { label: 'C', value: robotStore.tcp.c.toFixed(2), unit: '°' },
])

function onJointChange(index, value) {
  robotStore.setJoint(index, value)          // 本地即时到位（snap）
  window.dispatchEvent(new CustomEvent('sim_set_joint', { detail: { index, degree: value } }))
}

// 安全围栏的控制与状态展示已抽到独立组件 SafetyFencePanel.vue，
// 这里只挂一个 tab 容器，避免两处样式互相污染。
function onSuckChange(val) {
  robotStore.setSuck(val)
  window.dispatchEvent(new CustomEvent('sim_suck', { detail: { on: val } }))
}
// 回零位 = 机械零位（与 backend/kinematics.py 的 HOME_JOINTS_DEG 一致）。
// 走 sim_command 通道，由 App.vue 统一做「本地姿态 + 后端同步」两件事，
// 避免这里和 App.vue 各写一半。
function goHome() {
  window.dispatchEvent(new CustomEvent('sim_command', { detail: 'home' }))
}
// 竖直位 = 手臂完全打直朝上（J3 = 90° − 肘偏置 = 82.863°）
function goVertical() {
  robotStore.setAllJoints([...VERTICAL_JOINTS_DEG])
  window.dispatchEvent(new CustomEvent('sim_set_joints', { detail: { joints: VERTICAL_JOINTS_DEG } }))
}

watch(() => robotStore.suckOn, (val) => { suckState.value = val })

// 程序执行时指令位姿会持续变化，低频跟随（避免每帧跑正解）
let lastCartSync = 0
watch(() => robotStore.jointRev, () => {
  if (controlMode.value !== 'cart') return
  const now = Date.now()
  if (now - lastCartSync < 120) return
  lastCartSync = now
  syncCart()
})

onMounted(() => {
  pollArmReady()
  readyTimer = setInterval(pollArmReady, 300)
})
onUnmounted(() => { if (readyTimer) clearInterval(readyTimer) })
</script>

<style scoped>
/* 全部色值/尺寸来自 assets/design-system.css（Linear 深色）。 */

.manual-panel {
  height: 100%;
  overflow-y: auto;
  background: var(--bg-surface);
  font-family: var(--font-sans);
}

.panel-tabs-wrap { padding: var(--pad-xl); }

.panel-tabs :deep(.el-tabs__header) { margin: 0 0 var(--space-3) 0; }
.panel-tabs :deep(.el-tabs__nav-wrap) { padding: 0; }
.panel-tabs :deep(.el-tabs__item) {
  padding: 0 var(--pad-xs) !important;
  margin-right: var(--space-4);
  font-size: var(--font-size-md);
  height: 30px;
  line-height: 30px;
}
.panel-tabs :deep(.el-tabs__content) { overflow: visible; }

/* ── 模式切换（分段控件）───────────────────────────────────── */
.mode-switch {
  display: flex;
  gap: 2px;
  padding: var(--pad-xs);
  margin-bottom: var(--space-3);
  border-radius: var(--radius-md);
  background: var(--bg-recessed);
  border: 1px solid var(--border-subtle);
}
.mode-btn {
  flex: 1;
  height: 26px;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-tertiary);
  font-size: var(--font-size-sm);
  font-weight: 500;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out);
}
.mode-btn:hover { color: var(--text-secondary); }
.mode-btn.active {
  background: var(--bg-elevated);
  color: var(--accent);
  font-weight: 600;
  box-shadow: var(--shadow-small);
}

/* ── 分区块 ───────────────────────────────────────────────── */
.section-block { margin-bottom: var(--space-4); }
.section-block:last-child { margin-bottom: 0; }

.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--text-tertiary);
  letter-spacing: var(--tracking-wide);
  margin-bottom: var(--space-2);
}
.link-btn {
  background: none;
  border: none;
  color: var(--accent);
  font-size: var(--font-size-xs);
  font-weight: 500;
  cursor: pointer;
  padding: 0;
  letter-spacing: 0;
}
.link-btn:hover { color: var(--accent-hover); text-decoration: underline; }

/* ══ 关节控制 ══ */
.joint-list {
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  padding: var(--pad-sm);
}
.joint-row {
  padding: var(--pad-sm) var(--pad-xl) var(--pad-xs);
  border-radius: var(--radius-sm);
  transition: background var(--duration-fast) var(--ease-out);
}
.joint-row:hover { background: var(--bg-elevated); }
.joint-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--pad-xs);
}
.joint-name {
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  letter-spacing: 0.04em;
}
.joint-range {
  flex: 1;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-disabled);
  font-variant-numeric: tabular-nums;
}
.joint-deg {
  font-size: var(--font-size-sm);
  font-family: var(--font-mono);
  color: var(--accent);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.joint-deg.is-limit { color: var(--warning); }
.joint-track { padding: 0 var(--pad-xs); }

/* ══ 笛卡尔控制（滑杆版）══ */
.cart-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--pad-sm);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}
.cart-row {
  padding: var(--pad-sm) var(--pad-lg) var(--pad-xs);
  border-radius: var(--radius-sm);
  transition: background var(--duration-fast) var(--ease-out);
}
.cart-row:hover { background: var(--bg-elevated); }
.cart-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--pad-xs);
}
.cart-key {
  width: 12px;
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  font-weight: 700;
  color: var(--text-secondary);
}
.cart-range {
  flex: 1;
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-disabled);
  font-variant-numeric: tabular-nums;
}
.cart-val {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--accent);
  font-variant-numeric: tabular-nums;
}
.cart-val em {
  margin-left: 2px;
  font-style: normal;
  font-size: 10px;
  color: var(--text-disabled);
}
.cart-track { padding: 0 var(--pad-xs); }

.cart-hint {
  margin-top: var(--space-2);
  font-size: var(--font-size-xs);
  color: var(--text-disabled);
  line-height: 1.5;
}

/* ══ 吸盘 ══ */
.suck-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--pad-lg) var(--pad-xl);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}
.suck-label { font-size: var(--font-size-base); color: var(--text-secondary); font-weight: 500; }
.holding-tag {
  margin-left: auto;
  padding: var(--pad-xs) var(--pad-lg);
  border-radius: var(--radius-full);
  background: var(--success-soft);
  color: var(--success);
  font-size: var(--font-size-xs);
  font-weight: 600;
}

/* ══ 快捷操作 ══ */
.quick-row { display: flex; gap: var(--space-2); }
.quick-btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  height: 34px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: var(--font-size-md);
  font-weight: 500;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out);
}
.quick-btn:hover { background: var(--bg-hover); border-color: var(--border-strong); color: var(--text-primary); }
.quick-btn:active { transform: scale(0.98); }

/* ══ TCP ══ */
.tcp-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-1);
}
.tcp-cell {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--pad-sm) var(--pad-lg);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
}
.tcp-k {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  font-weight: 600;
  font-family: var(--font-mono);
  width: 12px;
  flex-shrink: 0;
}
.tcp-v {
  flex: 1;
  text-align: right;
  font-size: var(--font-size-sm);
  font-family: var(--font-mono);
  color: var(--text-primary);
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.tcp-u { font-size: 10px; color: var(--text-disabled); font-family: var(--font-mono); }

</style>
