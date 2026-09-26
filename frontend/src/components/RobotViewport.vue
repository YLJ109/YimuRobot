<template>
  <div class="robot-viewport" ref="containerRef">
    <!-- 数模加载中的占位遮罩 -->
    <div v-if="!modelReady" class="viewport-loading">
      <div class="loading-track"><div class="loading-bar"></div></div>
      <span class="loading-text">{{ loadError ? '数模加载失败' : '正在加载机器人数模…' }}</span>
    </div>

    <!-- 悬浮控制层 -->
    <div class="viewport-overlay">
      <div class="overlay-tl">
        <div class="vp-badge">
          <span class="vp-badge-led"></span>
          <span class="vp-badge-text">ER3-600</span>
        </div>
        <div class="vp-badge" :class="{ 'is-running': robotStore.isRunning, 'is-paused': robotStore.isPaused }">
          <span class="vp-badge-led"></span>
          <span class="vp-badge-text">{{ stateText }}</span>
        </div>
        <div class="vp-badge" :class="fenceBadgeClass" :title="fenceClearanceLabel || fenceStatusText">
          <span class="vp-badge-led"></span>
          <span class="vp-badge-text">{{ fenceStatusText }}</span>
        </div>
      </div>
      <div class="overlay-tr">
        <button class="vp-icon-btn" @click="resetCamera" title="重置视角（自动取景）">
          <el-icon><Refresh /></el-icon>
        </button>
        <button class="vp-icon-btn" :class="{ active: wireframeActive }" @click="toggleWireframe" title="线框模式">
          <el-icon><View /></el-icon>
        </button>
        <button class="vp-icon-btn" :class="{ active: envelopeActive }" @click="toggleEnvelope" title="显示工作空间包络">
          <el-icon><Aim /></el-icon>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { Refresh, View, Aim } from '@element-plus/icons-vue'
import * as THREE from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'

import { RobotArm } from '../classes/RobotArm.js'
import { SceneManager } from '../classes/SceneManager.js'
import { registerArm, unregisterArm } from '../classes/robotContext.js'
import { useRobotStore } from '../stores/robot.js'
import { useSceneStore } from '../stores/scene.js'
import { useFenceStore } from '../stores/fence.js'
import { speakText } from '../composables/useSpeech.js'

const robotStore = useRobotStore()
const sceneStore = useSceneStore()
const fenceStore = useFenceStore()

const containerRef = ref(null)
const stateText = ref('空闲')
const wireframeActive = ref(false)
const envelopeActive = ref(false)
const modelReady = ref(false)
const loadError = ref(false)

// ── 安全围栏状态徽章（供模板绑定；级别由 fenceStore.level 驱动）───
const level = computed(() => fenceStore.level)
const fenceBadgeClass = computed(() => ({
  'is-fence-safe': level.value === 'safe',
  'is-fence-warn': level.value === 'warn',
  'is-fence-danger': level.value === 'danger',
  'is-fence-collision': level.value === 'collision',
}))
const fenceStatusText = computed(() => ({
  safe: '围栏·安全',
  warn: '围栏·警告',
  danger: '围栏·危险',
  collision: '围栏·碰撞',
}[level.value] || '围栏·安全'))
const fenceClearanceLabel = computed(() => {
  const mm = fenceStore.clearance
  if (level.value === 'safe') return `${Math.round(mm)} mm 余量`
  return ''
})

let scene, camera, renderer, controls
let robotArm, sceneManager
let animationId = null
let wireframe = false

// 3D 舞台底色 —— 与 design-system.css 的 --stage-bg (#181b22) 保持一致。
//   ⚠️ 这**不是** --bg-canvas(#050506)。近黑画布在投影仪上就是一块黑洞，
//   机器人和地面网格全糊掉（用户反馈「3D 背景很黑」）。舞台单独亮一档，
//   雾的颜色也取同一个值，远处才会自然融进背景而不是糊成一片黑。
const STAGE_BG = 0x181b22

// 取景余量：包围球半径 × 该系数 → 相机距离。
// 数值越大，机器人在画面里越小。
//   1.45 → 几乎顶满画布（旧值，用户反馈「太大了」）
//   3.00 → 只占画面高度约 28%，用户反馈「画面太远了，看不到机器人」
//   1.70 → 物体还在 ±380 时的合适值（约占高度 50%）
//   1.50 → 物体挪到 ±480/-560 后，包围盒被撑大、机器人占比被压到 0.68，
//          用 1.5 把它拉回 ~0.77，机器人不被"挤小"、工位又离得开
// 改动后可以跑 tools/ui_probe.mjs 的「检查 0」，它会直接报 NDC 覆盖率。
const FIT_MARGIN = 1.5

/** 取景时的最小包围球半径（mm）：避免场景为空时相机贴到机器人脸上 */
const MIN_FRAME_RADIUS_MM = 560

/** 演示物体边长（mm）。50 的时候在整机旁边只有几个像素，用户反馈「物体很小」 */
const DEMO_OBJ_SIZE = 130

function initThree() {
  const container = containerRef.value
  // 布局还没算完时 clientWidth/Height 可能是 0，会导致 aspect = NaN →
  // 整个场景投影失效（一片空白）。给个兜底值，onResize 随后会纠正。
  const width = container.clientWidth || 960
  const height = container.clientHeight || 620

  scene = new THREE.Scene()
  scene.background = new THREE.Color(STAGE_BG)
  // 雾要拉得很远才起效：近距离起雾会把地面网格和演示物体一起"吃掉"，
  // 画面读起来就只剩机器人浮在黑底上（用户反馈的「场景和物体很小」）。
  scene.fog = new THREE.Fog(STAGE_BG, 3200, 6400)

  camera = new THREE.PerspectiveCamera(45, width / height, 1, 8000)

  renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' })
  renderer.setSize(width, height)
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
  renderer.shadowMap.enabled = true
  renderer.shadowMap.type = THREE.PCFSoftShadowMap
  renderer.toneMapping = THREE.ACESFilmicToneMapping
  renderer.toneMappingExposure = 1.05
  container.appendChild(renderer.domElement)

  controls = new OrbitControls(camera, renderer.domElement)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.maxPolarAngle = Math.PI / 2 + 0.12

  setupLights()
  sceneManager = new SceneManager(scene)
  // 围栏必须先于 robotArm 创建，因为它依赖 scene；几何由 fenceStore 默认值驱动。
  applyFence()
  robotArm = new RobotArm(scene)

  // 数模异步加载：就绪后再取景并同步一次关节
  robotArm.whenReady().then((ok) => {
    modelReady.value = !!ok
    loadError.value = !ok
    if (!ok) return
    robotArm.updateJoints(robotStore.joints, true)
    robotStore.setActualJoints([...robotArm.jointAngles])
    robotStore.setTcp(robotArm.getTcpPose())
    frameModel()
    // 把实例交给 ManualPanel 的笛卡尔点动使用（见 classes/robotContext.js）
    registerArm(robotArm, frameModel)
  })

  // 演示物体：各自摆在自己的工位上（A 区 / B 区 / 托盘）。
  // 坐标必须与 SceneManager._createZoneMarkers() 里的区域标记一一对应，
  // 否则方块会飘在色块外面。改这里就要同步改那里。
  //   A 区 红方块  (-480, 140)    B 区 蓝方块  (+480, 140)    托盘 绿圆柱  (0, -560)
  // 距离 480~560mm：贴着底座（原来的 ±380）会让画面挤成一团，也没有"搬运"的观感。
  const s = DEMO_OBJ_SIZE
  sceneManager.addObject('box', { name: '红色方块', color: 0xf43f5e, position: [-480, s / 2, 140], size: [s, s, s] })
  sceneManager.addObject('box', { name: '蓝色方块', color: 0x6366f1, position: [480, s / 2, 140], size: [s, s, s] })
  sceneManager.addObject('cylinder', { name: '绿色圆柱', color: 0x10b981, position: [0, s / 2, -560], size: [s, s, s] })
  // 注意：这里必须广播，不能只写本地 store。后端执行器启动时场景快照是空的，
  // 若前端不主动推一次，重启后端后的第一个 PICK 会因为「认不出物体」而算错
  // 接触高度（吸盘直接扎进物体里）。
  broadcastScene()
  animate()
}

function setupLights() {
  // 三灯 + 环境光。
  // 只有单向主光时地面几乎全黑（背光面没有任何补光），地面网格、区域标记
  // 和演示物体全部隐进背景里 —— 用户看到的就是「场景是空的、只有机器人」。
  scene.add(new THREE.HemisphereLight(0xb9c4dc, 0x14141c, 1.25))
  scene.add(new THREE.AmbientLight(0xffffff, 0.35))

  const key = new THREE.DirectionalLight(0xffffff, 1.55)
  key.position.set(520, 900, 420)
  key.castShadow = true
  key.shadow.mapSize.width = 2048
  key.shadow.mapSize.height = 2048
  key.shadow.camera.left = -1200
  key.shadow.camera.right = 1200
  key.shadow.camera.top = 1200
  key.shadow.camera.bottom = -1200
  key.shadow.camera.near = 200
  key.shadow.camera.far = 3000
  key.shadow.bias = -0.0006
  key.shadow.normalBias = 0.5
  scene.add(key)

  const rim = new THREE.DirectionalLight(0x6366f1, 0.7)
  rim.position.set(-620, 420, -520)
  scene.add(rim)

  const fill = new THREE.DirectionalLight(0x8ab4f8, 0.45)
  fill.position.set(-260, 180, 720)
  scene.add(fill)
}

/**
 * 自动取景：按机器人本体包围球算相机距离，保证整机完整入画。
 * 旧实现把相机位置写死 (760,620,980) + target y=250，模型几乎占满画面。
 */
function frameModel() {
  if (!robotArm || !robotArm.ready) return
  const box = robotArm.getVisibleBounds()
  if (box.isEmpty()) return

  // 把场景物体并进包围盒 —— 取景单位是「整个演示台」，不是「机器人本身」。
  // 只框机器人时两个毛病会同时出现：机器人被顶到占满画面（"太大"），
  // 而旁边的方块/工作台被挤出画面或缩成几个像素（"场景和物体很小"）。
  if (sceneManager) {
    for (const obj of sceneManager.objects) {
      if (obj.userData && obj.userData.attached) continue // 已被吸盘吸附的不算
      box.expandByObject(obj)
    }
  }

  const sphere = box.getBoundingSphere(new THREE.Sphere())
  const radius = Math.max(sphere.radius, MIN_FRAME_RADIUS_MM)
  const fov = THREE.MathUtils.degToRad(camera.fov)
  const dist = (radius * FIT_MARGIN) / Math.sin(fov / 2)

  // 固定观察方向：右前上方，兼顾整机轮廓与工作空间纵深
  const dir = new THREE.Vector3(0.66, 0.46, 0.82).normalize()
  // 观察点直接取内容包围盒中心：机器人（0~1040）与地面物体（0~130）一起
  // 被居中。之前压到 0.82 会让画面顶部空出一大片，主体反而偏下。
  const target = sphere.center.clone()
  camera.position.copy(target).addScaledVector(dir, dist)
  camera.near = Math.max(1, dist / 100)
  camera.far = dist * 12
  camera.updateProjectionMatrix()
  controls.target.copy(target)
  controls.minDistance = radius * 0.6
  controls.maxDistance = radius * 8
  controls.update()
}

// 补间用的帧间隔计时器。用 performance.now() 差分而不是 Clock.getDelta()，
// 免得和别处对 clock 的读取互相吃掉 delta。
let lastFrameTs = 0

function animate() {
  animationId = requestAnimationFrame(animate)

  const nowTs = performance.now()
  // 首帧 / 切回前台后 lastFrameTs=0 或间隔异常大 → 当作 0，本帧只渲染不推进
  let dt = lastFrameTs ? (nowTs - lastFrameTs) / 1000 : 0
  lastFrameTs = nowTs
  if (dt > 0.1) dt = 0.1 // 卡顿/切后台后不要一帧瞬移

  if (robotArm.ready) {
    // dt 必须传真实的帧间隔：早先写成 lerpUpdate(0, 0.15)，
    // dt=0 会被内部直接 return，补间永远不推进 —— 表现就是滑杆"拖了不动"。
    robotArm.lerpUpdate(dt)
    // 吸盘吸附要在画面追上遥测之后才判定，所以放在每帧里重试
    tryGrab()
    // 只读回写：实际位置 / TCP —— 绝不回写 robotStore.joints（那会吃掉用户指令）
    robotStore.setActualJoints([...robotArm.jointAngles])
    robotStore.setTcp(robotArm.getTcpPose())
  }
  // 安全围栏：每帧算机器人 vs 围栏几何的最近余量，驱动玻璃颜色 / 闪烁 / 报警。
  updateFence()
  robotStore.tickFps()
  controls.update()
  renderer.render(scene, camera)
}

/**
 * 指令 → 机械臂。
 * 监听 jointRev（版本号）而非 joints 本身：既避免深度遍历开销，
 * 也不会被渲染循环的只读回写误触发。
 */
watch(() => robotStore.jointRev, () => {
  if (!robotArm || !robotArm.ready) return
  const cmd = [...robotStore.joints]
  if (robotStore.jointSnap) {
    robotArm.updateJoints(cmd, true)
    robotStore.ackJointSnap()
  } else {
    robotArm.setTargetAngles(cmd)
  }
})

function handleRobotFrame(data) {
  if (!robotArm || !robotArm.ready) return
  if (data.type === 'joints' && data.j) {
    robotStore.setJointsFromRad(data.j)
  } else if (data.type === 'suck') {
    // ⚠️ 顺序不能反：RobotArm.setSuck(false) 会把 holdingObject 置空，
    //    先调它的话 releaseHeldObject 就找不到物体了，物体会被永久留在
    //    工具组里跟着机械臂跑。所以先释放、再切吸盘状态。
    if (!data.on) {
      pendingGrab = false
      releaseHeldObject()
    }
    robotArm.setSuck(data.on)
    robotStore.setSuck(data.on)
    if (data.on) scheduleGrab()
  } else if (data.type === 'line') {
    robotStore.setCurrentLine(data.line)
  }
}

// ── 吸盘真实吸附 ───────────────────────────────────────────────
//
// RobotArm 里 checkContact / attachObject / detachObject 三个方法一直存在，
// 但从来没有任何地方调用过 —— 所以「吸取」在 3D 里只是吸盘唇口变个颜色，
// 物体纹丝不动，PICK/PLACE 看不出在搬东西。
//
// 这里补上这条链路。难点在时序：后端在执行器「下探完成」的那一刻就把
// suck_on 置真推过来，而画面上的机械臂还在追赶那串遥测帧 —— 此时唇口
// 离物体还有一段距离。所以不能只在收到 suck 的那一帧判定一次，要开一个
// 短窗口每帧重试，等画面追上了再吸附。
const GRAB_WINDOW_MS = 1600
let pendingGrab = false
let pendingGrabUntil = 0

// ── 围栏碰撞报警：进入 collision 时触发一次，退出后允许再次触发 ──
//   每帧重算 level，但只在「从非碰撞 → 碰撞」那一帧触发语音 + 自动停机，
//   否则 60Hz 下会每秒吼 60 遍。
let fenceAlarmActive = false

function scheduleGrab() {
  pendingGrab = true
  pendingGrabUntil = performance.now() + GRAB_WINDOW_MS
}

/** 在窗口内每帧尝试吸附；命中就绑定并结束窗口 */
function tryGrab() {
  if (!pendingGrab) return
  if (performance.now() > pendingGrabUntil) {
    pendingGrab = false
    console.warn('[suck] 吸附窗口内未找到接触物体（可能物体不在吸盘正下方）')
    return
  }
  const obj = robotArm.checkContact(sceneManager.objects)
  if (!obj) return
  robotArm.attachObject(obj)
  pendingGrab = false
  syncSceneToStore()
}

/** 释放：把手上物体还回场景组，并用真实位置回写 store */
function releaseHeldObject() {
  if (!robotArm || !robotArm.holdingObject) return
  robotArm.detachObject(sceneManager.objectsGroup)
  syncSceneToStore()
}
// ── 场景：本地优先 ────────────────────────────────────────────
//
// ObjectLibrary 的增删改会先派发这几个事件，直接落到 SceneManager 上
// （帧内生效），再顺手把快照发给后端持久化。历史实现只发后端、等后端
// 广播回来才更新 3D，socket 一断就「改颜色 / 拖坐标完全没反应」。

/** 按 userData.id 找 3D 物体 */
function findSceneObject(id) {
  if (!sceneManager) return null
  return sceneManager.objects.find((o) => o.userData.id === id) || null
}

function handleScenePatch(e) {
  const { id, updates } = e.detail || {}
  if (!sceneManager || !updates) return
  const obj = findSceneObject(id)
  if (!obj) return
  if (updates.color) sceneManager.updateObjectColor(obj, updates.color)
  if (updates.name) sceneManager.updateObjectName(obj, updates.name)
  if (Array.isArray(updates.position)) sceneManager.updateObjectPosition(obj, ...updates.position)
  broadcastScene()
}

function handleSceneAdd(e) {
  if (!sceneManager || !e.detail) return
  const o = e.detail
  const mesh = sceneManager.addObject(o.type, {
    name: o.name,
    color: o.color,
    position: o.position,
    size: o.size,
  })
  mesh.userData.id = o.id            // 对齐 store 的 id，后续 patch/remove 才能命中
  mesh.userData.grabbable = o.grabbable !== false
  broadcastScene()
}

function handleSceneRemove(e) {
  if (!sceneManager || !e.detail) return
  sceneManager.removeObjectById(e.detail.id)
  broadcastScene()
}

/**
 * 后端广播的场景。
 * 只在内容真的不同时才重建 —— fromJSON 是「清空 + 全量重建」，会把
 * 吸盘已吸附的物体（Object3D.attach 的重父化）打回原形并闪一下。
 * 而本地改动本来就会通过 sim_scene_update 回显，所以绝大多数广播都是
 * 自己刚发出去的那份，直接跳过即可。
 */
function handleSceneObjects(data) {
  if (!data || !data.objects || !sceneManager) return

  // 拒绝「空场景」广播把本地场景清掉。
  // 后端 global_state 是进程级的，任何一次来自其它页面/历史会话的
  // 空快照（sim_scene_update with objects: []）都会广播回来，把当前页面
  // 里刚建好的演示物体整组删除 —— 表现就是「物体库莫名其妙空了」。
  // 单机演示场景下，宁可忽略空广播，也不要静默丢场景。
  if (data.objects.length === 0 && sceneManager.objects.length > 0) {
    console.warn('[scene] 忽略空场景广播，保留本地物体')
    return
  }

  const incoming = JSON.stringify(data.objects)
  const current = JSON.stringify(sceneStore.getSnapshot())
  if (incoming === current) return
  sceneManager.fromJSON(data.objects)
  sceneStore.setObjects(data.objects)
}
function handleProgramFinished(data) {
  robotStore.setExecState(data.success ? 'finished' : 'stopped')
  stateText.value = data.success ? '完成' : '已停止'
  if (!data.success && data.errors) console.warn('程序错误:', data.errors)
}
function syncSceneToStore() { sceneStore.setObjects(sceneManager.getObjectsData()) }

/**
 * 把当前场景整份推给后端。
 *
 * 后端执行器的 PICK/PLACE 靠这份快照「按坐标认物体」（取名字、取高度）。
 * 快照为空或过期时它算不出接触高度，会直接按 z 下探 —— 视觉上就是吸盘
 * 扎进物体里、还吸不住。所以：任何本地场景改动之后，以及每次连上后端，
 * 都要推一次（见 setupSocketEvents 的 sim_scene_flush）。
 */
function broadcastScene() {
  syncSceneToStore()
  window.dispatchEvent(new CustomEvent('sim_scene_update', {
    detail: { objects: sceneStore.getSnapshot() },
  }))
}

function resetCamera() { frameModel() }

function toggleWireframe() {
  wireframe = !wireframe
  wireframeActive.value = wireframe
  if (robotArm) robotArm.setWireframe(wireframe)
}
function toggleEnvelope() {
  if (!robotArm) return
  envelopeActive.value = !envelopeActive.value
  robotArm.setEnvelopeVisible(envelopeActive.value)
}

// ── 安全围栏：围栏 store 与 Three.js 几何同步 + 临近度计算 ──────
//
// 围栏的尺寸 / 显隐完全由 fenceStore 驱动：ManualPanel 改 store → 这里的
// watch → SceneManager 重建或显隐。临近度由 updateFence() 每帧算一次。
//
// 围栏临近度只算机器人连杆 vs 围栏几何（四面墙+四角柱），**不算场景物体**：
//  PICK 时吸盘和物体本来就会接触，算进去每条搬运都误报。
function applyFenceSize() {
  if (!sceneManager) return
  sceneManager.setFenceSize({
    half: fenceStore.half,
    height: fenceStore.height,
    pillarR: fenceStore.pillarR,
    ref: fenceStore.FENCE_REF_MM,
  })
  // 重建后恢复上一帧的级别配色，否则会闪一下绿（默认 safe）。
  sceneManager.setFenceLevel(fenceStore.level)
}
function applyFenceVisible() {
  if (sceneManager) sceneManager.setFenceVisible(fenceStore.visible)
}
function applyFence() {
  // 首次创建（sceneManager 刚 new 出来时还没有 fence group）。
  if (!sceneManager._fence) {
    sceneManager.createSafetyFence({
      half: fenceStore.half,
      height: fenceStore.height,
      pillarR: fenceStore.pillarR,
      ref: fenceStore.FENCE_REF_MM,
    })
  } else {
    applyFenceSize()
  }
  applyFenceVisible()
}

function triggerFenceAlarm() {
  // 报警语音（复用 /api/tts，受语音播报开关约束；关掉则静默）。
  speakText('警告！机器人已碰触安全围栏，已自动停止运行，请注意安全。')
  // 自动停机：仅在程序运行时才有意义；否则只在画面里给提示就够了。
  if (robotStore.isRunning) {
    window.dispatchEvent(new CustomEvent('sim_command', { detail: 'stop' }))
  }
}

function updateFence() {
  if (!fenceStore.visible) return
  if (!robotArm || !robotArm.ready) return
  if (!sceneManager) return
  const pts = robotArm.getLinkWorldPoints()
  const res = sceneManager.fenceClearance(pts)
  if (!res) return
  // 回写 store：level 变化才写（避免每帧触发响应式风暴）；
  // clearance / proximity 按 0.5 的精度写一次，用于 UI 显示余量与接近度。
  if (fenceStore.level !== res.level) fenceStore.setLevel(res.level)
  if (Math.abs(fenceStore.clearance - res.clear) > 0.5) fenceStore.setClearance(res.clear)
  if (Math.abs(fenceStore.proximity - res.proximity) > 0.5) fenceStore.setProximity(res.proximity)
  sceneManager.setFenceLevel(res.level)
  sceneManager.updateFenceFlash(performance.now())
  // 边沿检测：进入碰撞的瞬间报警 + 自动停，退出后允许再次触发。
  if (res.level === 'collision') {
    if (!fenceAlarmActive) {
      fenceAlarmActive = true
      triggerFenceAlarm()
    }
  } else {
    fenceAlarmActive = false
  }
}

function onResize() {
  if (!containerRef.value || !renderer) return
  const w = containerRef.value.clientWidth, h = containerRef.value.clientHeight
  if (!w || !h) return
  camera.aspect = w / h
  camera.updateProjectionMatrix()
  renderer.setSize(w, h)
}

// ── 事件监听：必须用具名函数 ──────────────────────────────────
//
// removeEventListener 比的是**函数引用**。旧代码注册时写
//     window.addEventListener('robot_frame', (e) => handleRobotFrame(e.detail))
// 卸载时又写了一个**全新**的箭头函数，引用不同 → 谁也删不掉。
// HMR 每次热更新都会再叠一层，于是同一条遥测帧被处理 N 次。
const onRobotFrameEvt = (e) => handleRobotFrame(e.detail)
const onSceneObjectsEvt = (e) => handleSceneObjects(e.detail)
const onProgramFinishedEvt = (e) => handleProgramFinished(e.detail)
// 后端连接（含重连）成功后，App.vue 会派发这个事件让我们补推一次场景快照。
// 因为 initThree() 里那次广播很可能赶在 socket 连上之前，会被直接丢掉。
const onSceneFlushEvt = () => { if (sceneManager) broadcastScene() }

// ── 围栏 store → 3D 几何同步 ──────────────────────────────────
//   尺寸变了重建几何（setFenceSize 内部会重建 group）；
//   显隐变了只切 visible，不重建。rebuild 比 setFenceVisible 贵得多。
watch(() => [fenceStore.half, fenceStore.height, fenceStore.pillarR], () => {
  if (sceneManager) applyFenceSize()
})
watch(() => fenceStore.visible, (v) => { if (sceneManager) sceneManager.setFenceVisible(!!v) })

onMounted(() => {
  initThree()
  window.addEventListener('resize', onResize)
  window.addEventListener('robot_frame', onRobotFrameEvt)
  window.addEventListener('scene_objects', onSceneObjectsEvt)
  window.addEventListener('scene_patch', handleScenePatch)
  window.addEventListener('scene_add', handleSceneAdd)
  window.addEventListener('scene_remove', handleSceneRemove)
  window.addEventListener('program_finished', onProgramFinishedEvt)
  window.addEventListener('sim_scene_flush', onSceneFlushEvt)

  // 仅 DEV：暴露「模型在画面里占多大」的量化接口。
  // 这个只有 DEV 需要，生产构建 tree-shake 掉。修 viewport 都是靠肉眼调相机，
  // 有了它才能把「模型太大」变成一条可回归的断言（见 tools/ui_probe.mjs 检查 0）。
  if (import.meta.env.DEV) {
    window.__sim = window.__sim || {}
    window.__sim.viewport = {
      /** @returns {{w:number,h:number,radius:number,dist:number}|null} NDC 尺寸，2 = 正好铺满 */
      coverage() {
        if (!robotArm || !robotArm.ready) return null
        const box = robotArm.getVisibleBounds()
        if (box.isEmpty()) return null
        const sphere = box.getBoundingSphere(new THREE.Sphere())
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
        const v = new THREE.Vector3()
        for (let i = 0; i < 8; i++) {
          v.set(
            (i & 1) ? box.max.x : box.min.x,
            (i & 2) ? box.max.y : box.min.y,
            (i & 4) ? box.max.z : box.min.z,
          ).project(camera)
          if (v.x < minX) minX = v.x
          if (v.x > maxX) maxX = v.x
          if (v.y < minY) minY = v.y
          if (v.y > maxY) maxY = v.y
        }
        return {
          w: Math.min(2, maxX - minX),
          h: Math.min(2, maxY - minY),
          radius: sphere.radius,
          dist: camera.position.distanceTo(sphere.center),
        }
      },
      /** 安全围栏实时状态（供 ui_probe / fence_probe 验证几何与临近度） */
      fence() {
        return {
          visible: fenceStore.visible,
          half: fenceStore.half,
          height: fenceStore.height,
          pillarR: fenceStore.pillarR,
          level: fenceStore.level,
          clearance: +fenceStore.clearance.toFixed(2),
          alarmActive: !!fenceAlarmActive,
        }
      },
      /** 强制把围栏缩到 280mm，触发碰撞；测试用 */
      forceCollide() { fenceStore.setHalf(280) },
    }
    // 同理，给探针一个读取 3D 物体真实状态的窗口 ——
    // 「改颜色/坐标没反应」这种问题只有比对数模本体才测得出来，
    // 光看 store 是测不出来的（store 一直是改对的）。
    window.__sim.scene = {
      probe(id) {
        if (!sceneManager) return null
        const o = sceneManager.objects.find((x) => x.userData.id === id)
        if (!o) return null
        return {
          id: o.userData.id,
          type: o.userData.type,
          name: o.userData.name,
          color: '#' + o.material.color.getHexString(),
          position: [+o.position.x.toFixed(2), +o.position.y.toFixed(2), +o.position.z.toFixed(2)],
          // 是否已被吸盘吸附（重父化到 toolGroup）。
          // 「吸取」链路过去完全没接上：attachObject 从来没人调用，
          // 物体在 3D 里纹丝不动，PICK/PLACE 看不出在搬东西。
          attached: !!o.userData.attached,
          parent: o.parent ? o.parent.name || o.parent.type : '',
        }
      },
      count() { return sceneManager ? sceneManager.objects.length : 0 },
      heldName() { return robotArm && robotArm.holdingObject ? robotArm.holdingObject.userData.name : null },
      /**
       * 世界系几何快照 —— 把「吸盘唇口到底压在哪」变成可断言数值。
       *
       * 光看 store 的 suckOn=true 判不出吸附是否成立：唇口可能悬在物体上方
       * 几十毫米。这里直接给出唇口/法兰的世界坐标与目标物体的世界包围盒，
       * `gapTop` = 唇口高度 − 物体顶面高度，正常吸附时应接近 0。
       * @param {string} [id] 目标物体 id，省略则只返回工具几何
       */
      geometry(id) {
        if (!robotArm || !robotArm.ready) return null
        const r3 = (v) => [+v[0].toFixed(2), +v[1].toFixed(2), +v[2].toFixed(2)]
        robotArm.group.updateMatrixWorld(true)
        const tip = robotArm.getToolTipWorldPosition()
        const tcp = robotArm.getTcpWorldPosition()
        const dir = robotArm.getToolDirection()
        let box = null
        if (id && sceneManager) {
          const o = sceneManager.objects.find((x) => x.userData.id === id)
          if (o) {
            const b = new THREE.Box3().setFromObject(o)
            if (!b.isEmpty()) {
              const top = new THREE.Vector3(
                (b.min.x + b.max.x) / 2, b.max.y, (b.min.z + b.max.z) / 2,
              )
              box = {
                min: r3([b.min.x, b.min.y, b.min.z]),
                max: r3([b.max.x, b.max.y, b.max.z]),
                top: r3([top.x, top.y, top.z]),
                size: [...o.userData.size],
                gapTop: +(tip.y - b.max.y).toFixed(2),
              }
            }
          }
        }
        return { tip: r3([tip.x, tip.y, tip.z]), tcp: r3([tcp.x, tcp.y, tcp.z]), dir: r3([dir.x, dir.y, dir.z]), box }
      },
    }
  }
})
onUnmounted(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('robot_frame', onRobotFrameEvt)
  window.removeEventListener('scene_objects', onSceneObjectsEvt)
  window.removeEventListener('scene_patch', handleScenePatch)
  window.removeEventListener('scene_add', handleSceneAdd)
  window.removeEventListener('scene_remove', handleSceneRemove)
  window.removeEventListener('program_finished', onProgramFinishedEvt)
  window.removeEventListener('sim_scene_flush', onSceneFlushEvt)
  pendingGrab = false
  if (animationId) cancelAnimationFrame(animationId)
  unregisterArm(robotArm)
  if (robotArm) robotArm.dispose()
  if (sceneManager) sceneManager.dispose()
  if (controls) controls.dispose()
  if (renderer) {
    renderer.dispose()
    if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement)
  }
})
defineExpose({
  getRobotArm: () => robotArm,
  getSceneManager: () => sceneManager,
  frameModel,
  syncSceneToStore,
})
</script>

<style scoped>
.robot-viewport {
  width: 100%;
  height: 100%;
  position: relative;
  /* 舞台底色：与 WebGL 的 scene.background(STAGE_BG) 同值。
     若这里还用 --bg-canvas(#050506)，WebGL 上下文的初始化那一两帧、
     以及 canvas 尺寸变化时会露出底下更黑的一层，闪一下。 */
  background: var(--stage-bg);
  overflow: hidden;
}

/* ── 加载遮罩 ─────────────────────────────────────────────── */
.viewport-loading {
  position: absolute;
  inset: 0;
  z-index: 20;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-4);
  background: var(--stage-bg);
}
.loading-track {
  width: 180px;
  height: 2px;
  border-radius: var(--radius-full);
  background: var(--border);
  overflow: hidden;
}
.loading-bar {
  width: 40%;
  height: 100%;
  border-radius: var(--radius-full);
  background: var(--accent);
  animation: vp-loading-slide 1.1s var(--ease-out) infinite;
}
@keyframes vp-loading-slide {
  0%   { transform: translateX(-100%); }
  100% { transform: translateX(350%); }
}
.loading-text {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  letter-spacing: 0.02em;
}

/* ── 悬浮控制层 ───────────────────────────────────────────── */
.viewport-overlay {
  position: absolute;
  inset: 0 0 auto 0;
  padding: var(--pad-xl);
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  pointer-events: none;
  z-index: var(--z-overlay);
}
.overlay-tl,
.overlay-tr { display: flex; gap: var(--space-2); pointer-events: auto; }

/* ── 玻璃徽章 ─────────────────────────────────────────────── */
.vp-badge {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--pad-md) var(--pad-xl);
  border-radius: var(--radius-full);
  background: var(--glass-bg);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  border: 1px solid var(--glass-border);
  font-size: var(--font-size-xs);
  font-weight: 500;
  color: var(--text-secondary);
  letter-spacing: 0.02em;
  transition: color var(--duration-normal) var(--ease-out);
}
.vp-badge-text { line-height: 1; white-space: nowrap; }

.vp-badge-led {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--text-disabled);
  flex-shrink: 0;
  transition: background var(--duration-normal) var(--ease-out);
}

.vp-badge.is-running { color: var(--success); }
.vp-badge.is-running .vp-badge-led { background: var(--success); box-shadow: var(--glow-success); animation: led-pulse 2s ease-in-out infinite; }
.vp-badge.is-paused { color: var(--warning); }
.vp-badge.is-paused .vp-badge-led { background: var(--warning); }

/* ── 安全围栏徽章（绿/琥珀/红/闪烁） ──────────────────────── */
.vp-badge.is-fence-safe { color: var(--success); }
.vp-badge.is-fence-safe .vp-badge-led { background: var(--success); box-shadow: var(--glow-success); }
.vp-badge.is-fence-warn { color: var(--warning); }
.vp-badge.is-fence-warn .vp-badge-led { background: var(--warning); box-shadow: var(--glow-warning); }
.vp-badge.is-fence-danger { color: var(--danger); background: var(--danger-soft); border-color: var(--danger); }
.vp-badge.is-fence-danger .vp-badge-led { background: var(--danger); box-shadow: var(--glow-danger); animation: led-pulse 0.8s ease-in-out infinite; }
.vp-badge.is-fence-collision {
  color: #fff; background: var(--danger); border-color: var(--danger);
  animation: vp-fence-flash 0.45s ease-in-out infinite;
}
.vp-badge.is-fence-collision .vp-badge-led { background: #fff; box-shadow: 0 0 6px #fff; }
@keyframes vp-fence-flash {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.45; transform: scale(0.97); }
}

/* ── 圆形图标按钮 ─────────────────────────────────────────── */
.vp-icon-btn {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-full);
  background: var(--glass-bg);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  color: var(--text-secondary);
  font-size: 14px;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out);
}
.vp-icon-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
.vp-icon-btn:active { transform: scale(0.94); }
.vp-icon-btn.active {
  background: var(--accent-soft);
  border-color: var(--border-focus);
  color: var(--accent);
}

@keyframes led-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.4; }
}
</style>
