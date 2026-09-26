// SceneManager.js - 场景物体管理类
// 支持添加/删除/修改物体，拖拽，吸盘重父化（Object3D.attach）

import * as THREE from 'three'

let objectIdCounter = 0

/** 复用的临时向量（toJSON 会被频繁调用，避免每次分配） */
const _mTmpV = new THREE.Vector3()

/**
 * 颜色归一化：既接受 three 惯用的 0xff4444，也接受 UI 那边的 '#ff4444' / '#f44'。
 * 物体颜色同时被三处使用（three 材质、store、后端 JSON），来源不统一，
 * 各自 parse 一次迟早出错，集中在这里做。
 */
function toColorInt(c) {
  if (typeof c === 'number') return c
  if (typeof c === 'string') {
    let s = c.trim().replace(/^#/, '')
    if (s.length === 3) s = s.split('').map((ch) => ch + ch).join('')
    const n = parseInt(s, 16)
    return Number.isFinite(n) ? n : 0xff4444
  }
  return 0xff4444
}

/** 坐标取三位小数（mm 精度足够）。统一精度后，前后端 JSON 对比不会因为
 *  浮点噪声而「明明一样却判定不同」，反复触发全量重建。 */
function r3(v) {
  return [+v.x.toFixed(3), +v.y.toFixed(3), +v.z.toFixed(3)]
}

export class SceneManager {
  constructor(scene) {
    this.scene = scene
    this.objects = [] // 场景物体列表
    this.objectsGroup = new THREE.Group()
    this.objectsGroup.name = 'sceneObjects'
    scene.add(this.objectsGroup)

    // 地面 / 区域标记等「环境」对象 —— dispose 时需一并移除，否则重挂载会叠加
    this._groundHelpers = []

    // 地面网格
    this._createGround()

    // 预定义区域标记
    this._createZoneMarkers()
  }

  _createGround() {
    // 网格 / 地面 —— 与 design-system.css 的 --stage-* 一组色值保持一致。
    // 舞台底色提到 #181b22 之后，原来那套「近黑地面 + 暗网格」就完全不成立了：
    // 地面比背景还暗会读成"一个洞"，网格线太暗则整层空间信息丢失。
    // 现在地面比舞台亮一档、网格线用中低饱和的蓝灰，透视图里的远近感才回得来。
    // 尺寸随演示工位一起放大到 2800（A/B 区在 ±480、托盘在 -560），
    // 格子仍保持 40mm 一格，线不会变密。
    const grid = new THREE.GridHelper(2800, 70, 0x5a6484, 0x333a49)
    grid.position.y = 0
    this.scene.add(grid)
    this._groundHelpers.push(grid)

    // 地面平面（接收阴影）
    const groundGeo = new THREE.PlaneGeometry(2800, 2800)
    const groundMat = new THREE.MeshStandardMaterial({
      color: 0x22262f, roughness: 0.9, metalness: 0.05
    })
    const ground = new THREE.Mesh(groundGeo, groundMat)
    ground.rotation.x = -Math.PI / 2
    ground.position.y = -0.1
    ground.receiveShadow = true
    ground.name = 'ground'
    this.scene.add(ground)
    this._groundHelpers.push(ground)
  }

  _createZoneMarkers() {
    // 区域色对齐 design-system.css：--success / --accent / --warning
    // 坐标必须与 RobotViewport 里挂的演示物体一致（A/B 区 ±480、托盘 -560），
    // 方块才会正好落在自己的工位色块上，而不是飘在色块外面。
    // _createZoneBox(name, x, y, z, height, width, color, depth)
    // A 区：绿色，红方块工位
    this._createZoneBox('A区', -480, 0, 140, 100, 260, 0x10b981, 260)
    // B 区：靛蓝，蓝方块工位
    this._createZoneBox('B区', 480, 0, 140, 100, 260, 0x6366f1, 260)
    // 托盘：琥珀，绿圆柱工位
    this._createZoneBox('托盘', 0, 0, -560, 100, 260, 0xf59e0b, 260)
  }

  _createZoneBox(name, x, y, z, height, width, color, depth = 100) {
    const geo = new THREE.BoxGeometry(width, 0.5, depth)
    // 0.14 的透明度在近黑地面上等于看不见，提到 0.26 才读得出"这是 A 区/B 区"
    const mat = new THREE.MeshBasicMaterial({
      color, transparent: true, opacity: 0.26
    })
    const mesh = new THREE.Mesh(geo, mat)
    mesh.position.set(x, y + 0.3, z)
    mesh.name = `zone_${name}`
    this.scene.add(mesh)
    this._groundHelpers.push(mesh)

    // 边框
    const edgeGeo = new THREE.EdgesGeometry(geo)
    const edgeMat = new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.55 })
    const edges = new THREE.LineSegments(edgeGeo, edgeMat)
    edges.position.copy(mesh.position)
    this.scene.add(edges)
    this._groundHelpers.push(edges)
  }

  // ────────────────── 物体管理 ──────────────────
  addObject(type, options = {}) {
    const {
      name = `物体${++objectIdCounter}`,
      color = 0xff4444,
      position = [0, 25, 0],
      size = [50, 50, 50],
    } = options

    let geo
    switch (type) {
      case 'box':
        geo = new THREE.BoxGeometry(size[0], size[1], size[2])
        break
      case 'cylinder':
        geo = new THREE.CylinderGeometry(size[0] / 2, size[0] / 2, size[1], 24)
        break
      case 'sphere':
        geo = new THREE.SphereGeometry(size[0] / 2, 24, 24)
        break
      case 'tray':
        geo = new THREE.BoxGeometry(size[0], size[1], size[2])
        break
      default:
        geo = new THREE.BoxGeometry(size[0], size[1], size[2])
    }

    const colorInt = toColorInt(color)
    const mat = new THREE.MeshStandardMaterial({
      color: colorInt, metalness: 0.3, roughness: 0.5
    })
    const mesh = new THREE.Mesh(geo, mat)
    mesh.position.set(position[0], position[1], position[2])
    mesh.castShadow = true
    mesh.receiveShadow = true
    mesh.name = name
    mesh.userData = {
      id: ++objectIdCounter,
      type,
      name,
      color: `#${colorInt.toString(16).padStart(6, '0')}`,
      size: [...size],
      grabbable: true,
      attached: false,
    }

    this.objectsGroup.add(mesh)
    this.objects.push(mesh)
    return mesh
  }

  removeObject(obj) {
    const idx = this.objects.indexOf(obj)
    if (idx >= 0) {
      this.objects.splice(idx, 1)
      this.objectsGroup.remove(obj)
      if (obj.geometry) obj.geometry.dispose()
      if (obj.material) obj.material.dispose()
    }
  }

  removeObjectById(id) {
    const obj = this.objects.find(o => o.userData.id === id)
    if (obj) this.removeObject(obj)
  }

  updateObjectColor(obj, color) {
    const colorInt = toColorInt(color)
    obj.material.color.set(colorInt)
    // 统一存成 '#rrggbb'，后端 JSON 与 store 都按这个格式读
    obj.userData.color = `#${colorInt.toString(16).padStart(6, '0')}`
  }

  updateObjectName(obj, name) {
    obj.name = name
    obj.userData.name = name
  }

  updateObjectPosition(obj, x, y, z) {
    obj.position.set(x, y, z)
  }

  // ────────────────── 场景序列化 ──────────────────
  toJSON() {
    return this.objects.map(obj => ({
      id: obj.userData.id,
      type: obj.userData.type,
      name: obj.userData.name,
      color: obj.userData.color,
      // ⚠️ 必须取**世界坐标**。物体被吸盘吸住后会被 attach 到工具组，
      //    此时 obj.position 变成「相对工具」的局部坐标；直接上报它，
      //    后端拿到的就是一串跟着机械臂乱跑的坐标，PICK/PLACE 会全部算偏。
      //    另外被吸住的物体要报「拿起时」的位置（parkedPosition）：它此刻的
      //    实时世界坐标就是机械臂的位置，对场景而言没有意义，报了会让物体
      //    列表里的坐标疯狂跳动。objectsGroup 恒位于场景原点且无旋转，
      //    所以世界坐标即场景语义坐标。
      position: (obj.userData.attached && obj.userData.parkedPosition)
        ? obj.userData.parkedPosition.map((v) => +Number(v).toFixed(3))
        : r3(obj.getWorldPosition(_mTmpV)),
      size: obj.userData.size,
      grabbable: obj.userData.grabbable,
    }))
  }

  fromJSON(data) {
    // 清空现有物体
    this.clear()
    // 加载
    for (const item of data) {
      const obj = this.addObject(item.type, {
        name: item.name,
        color: toColorInt(item.color),
        position: item.position,
        size: item.size,
      })
      obj.userData.id = item.id
      obj.userData.grabbable = item.grabbable !== false
    }
  }

  clear() {
    for (const obj of [...this.objects]) {
      this.removeObject(obj)
    }
  }

  // ────────────────── 查询 ──────────────────
  getObjectsData() {
    return this.toJSON()
  }

  findByName(name) {
    return this.objects.find(o => o.userData.name === name)
  }

  findByColor(color) {
    return this.objects.find(o => o.userData.color === color || o.userData.color === color.toLowerCase())
  }

  // ────────────────── 吸盘交互 ──────────────────
  // 吸取：将物体重父化到工具组（保留世界变换）
  attachToTool(obj, toolGroup) {
    toolGroup.attach(obj)
    obj.userData.attached = true
  }

  // 释放：将物体重父化回场景组（保留世界变换）
  detachFromTool(obj) {
    this.objectsGroup.attach(obj)
    obj.userData.attached = false
  }

  // ────────────────── 安全围栏 ──────────────────
  //
  // 一个正方形玻璃围栏：四面墙 + 四角玻璃柱，把机器人围在中间。
  // 临近度分级由 fenceClearance() 算（机器人连杆 vs 围栏几何），颜色由
  // setFenceLevel() 上色，碰撞时 updateFenceFlash() 做红色闪烁。
  //
  // 几何全部建在一个独立 group 里（不和场景物体混），HMR/重挂载时统一释放。
  _fence = null

  /**
   * 创建 / 重建安全围栏。
   * @param {{half:number, height:number, pillarR:number, ref?:number}} cfg
   */
  createSafetyFence(cfg) {
    this._disposeFence()
    const half = Math.max(1, cfg.half)
    const height = Math.max(1, cfg.height)
    const pillarR = Math.max(1, cfg.pillarR)
    const ref = cfg.ref || 150
    const T = 6 // 玻璃墙厚度（mm）

    const group = new THREE.Group()
    group.name = 'safetyFence'

    const COLORS = {
      safe: { wall: 0x10b981, edge: 0x10b981, pillar: 0x10b981, wallOp: 0.10, edgeOp: 0.45, pillarOp: 0.18 },
      warn: { wall: 0x10b981, edge: 0xf59e0b, pillar: 0x10b981, wallOp: 0.10, edgeOp: 0.95, pillarOp: 0.18 },
      danger: { wall: 0xef4444, edge: 0xef4444, pillar: 0xef4444, wallOp: 0.26, edgeOp: 0.95, pillarOp: 0.32 },
      collision: { wall: 0xef4444, edge: 0xef4444, pillar: 0xef4444, wallOp: 0.42, edgeOp: 1.0, pillarOp: 0.5 },
    }

    const wallMats = [], edgeMats = [], pillarMats = []

    const makeWallMat = () => {
      const m = new THREE.MeshStandardMaterial({
        color: COLORS.safe.wall, transparent: true, opacity: COLORS.safe.wallOp,
        roughness: 0.12, metalness: 0.0, side: THREE.DoubleSide, depthWrite: false,
      })
      wallMats.push(m); return m
    }
    const makeEdgeMat = () => {
      const m = new THREE.LineBasicMaterial({
        color: COLORS.safe.edge, transparent: true, opacity: COLORS.safe.edgeOp,
      })
      edgeMats.push(m); return m
    }
    const makePillarMat = () => {
      const m = new THREE.MeshStandardMaterial({
        color: COLORS.safe.pillar, transparent: true, opacity: COLORS.safe.pillarOp,
        roughness: 0.2, metalness: 0.0, depthWrite: false,
      })
      pillarMats.push(m); return m
    }

    const side = half * 2
    // 四面墙
    for (const sz of [-half, half]) {
      const geo = new THREE.BoxGeometry(side, height, T)
      const wall = new THREE.Mesh(geo, makeWallMat())
      wall.position.set(0, height / 2, sz)
      group.add(wall)
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), makeEdgeMat())
      edges.position.copy(wall.position)
      group.add(edges)
    }
    for (const sx of [-half, half]) {
      const geo = new THREE.BoxGeometry(T, height, side)
      const wall = new THREE.Mesh(geo, makeWallMat())
      wall.position.set(sx, height / 2, 0)
      group.add(wall)
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), makeEdgeMat())
      edges.position.copy(wall.position)
      group.add(edges)
    }

    // 四角玻璃柱
    for (const sx of [-half, half]) {
      for (const sz of [-half, half]) {
        const geo = new THREE.CylinderGeometry(pillarR, pillarR, height, 20, 1, true)
        const pillar = new THREE.Mesh(geo, makePillarMat())
        pillar.position.set(sx, height / 2, sz)
        group.add(pillar)
      }
    }

    // ── 四根顶部横梁 ────────────────────────────────────────────
    // 碰撞模型里「顶面」是边界（cy = height − p.y），但围栏四面墙是通透玻璃，
    // 没有横向构件的话用户压低高度却看不到任何边界，会以为高度只是个数字。
    // 这四根梁把「可调高度」这条边显式画出来，压低到机器人以下就看得见它捅出去。
    const RAIL = 10
    for (const sz of [-half, half]) {
      const geo = new THREE.BoxGeometry(side + RAIL, RAIL, RAIL)
      const rail = new THREE.Mesh(geo, makeWallMat())
      rail.position.set(0, height, sz)
      group.add(rail)
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), makeEdgeMat())
      edges.position.copy(rail.position)
      group.add(edges)
    }
    for (const sx of [-half, half]) {
      const geo = new THREE.BoxGeometry(RAIL, RAIL, side + RAIL)
      const rail = new THREE.Mesh(geo, makeWallMat())
      rail.position.set(sx, height, 0)
      group.add(rail)
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo), makeEdgeMat())
      edges.position.copy(rail.position)
      group.add(edges)
    }

    this.scene.add(group)
    this._fence = { group, wallMats, edgeMats, pillarMats, COLORS, config: { half, height, pillarR, ref }, level: 'safe' }
    this.setFenceLevel('safe')
    return this._fence
  }

  setFenceSize(cfg) {
    if (!this._fence) { this.createSafetyFence(cfg); return }
    const level = this._fence.level
    this.createSafetyFence(cfg)
    this.setFenceLevel(level)
  }

  setFenceVisible(v) {
    if (this._fence) this._fence.group.visible = !!v
  }

  setFenceLevel(level) {
    if (!this._fence) return
    const c = this._fence.COLORS[level] || this._fence.COLORS.safe
    for (const m of this._fence.wallMats) {
      m.color.setHex(c.wall); m.opacity = c.wallOp; m.userData.baseOp = c.wallOp
    }
    for (const m of this._fence.edgeMats) {
      m.color.setHex(c.edge); m.opacity = c.edgeOp; m.userData.baseOp = c.edgeOp
    }
    for (const m of this._fence.pillarMats) {
      m.color.setHex(c.pillar); m.opacity = c.pillarOp; m.userData.baseOp = c.pillarOp
    }
    this._fence.level = level
  }

  updateFenceFlash(elapsedMs) {
    if (!this._fence || this._fence.level !== 'collision') return
    const k = 0.44 + 0.26 * Math.sin(elapsedMs / 250 * Math.PI)
    for (const m of [...this._fence.wallMats, ...this._fence.pillarMats]) m.opacity = k
    for (const m of this._fence.edgeMats) m.opacity = 1.0
  }

  /**
   * 计算机器人连杆与围栏的「碰撞接近度」。
   *
   * 语义（用户口径，务必对齐）：
   *   接近度 100% → 已碰撞    → 红色闪烁
   *   接近度 ≥90% → 危险      → 变红
   *   接近度 ≥80% → 警告      → 变黄
   *   其余        → 安全      → 绿色透明
   *
   * 接近度 = (1 − 余量 / REF) × 100%，余量 = 连杆到围栏内壁的最小距离（mm）：
   *   余量 150(=REF) →   0%  安全
   *   余量  30       →  80%  警告（黄）
   *   余量  15       →  90%  危险（红）
   *   余量   0       → 100%  碰撞（红闪）
   *
   * 围栏视为一个**封闭保护体积**（四面墙 + 顶盖 + 四角柱）：连杆任意一点
   * 跑到体积外面就是碰撞。这样「调高度」才有意义 —— 把围栏压低到机器人
   * 零位高度（腕心 699.5mm）以下，它就会捅出顶面而报警。
   *
   * @param {THREE.Vector3[]} points 机器人采样点（场景世界系，Y-up，mm）
   * @returns {{clear:number, proximity:number, level:string}|null}
   */
  fenceClearance(points) {
    if (!this._fence || !Array.isArray(points) || points.length === 0) return null
    const { half, height, pillarR, ref } = this._fence.config
    const corners = [[-half, -half], [half, -half], [-half, half], [half, half]]

    let minClear = Infinity
    for (const p of points) {
      const cx = half - Math.abs(p.x)   // 到左右两面墙内壁的余量
      const cz = half - Math.abs(p.z)   // 到前后两面墙内壁的余量
      const cy = height - p.y           // 到顶盖的余量（低于顶面为正）
      let cp = Infinity                 // 到最近角柱表面的余量（只在围栏高度内有效）
      if (p.y >= 0 && p.y <= height) {
        for (const [sx, sz] of corners) {
          const d = Math.hypot(p.x - sx, p.z - sz) - pillarR
          if (d < cp) cp = d
        }
      }
      const m = Math.min(cx, cz, cy, cp)
      if (m < minClear) minClear = m
    }

    // 余量越少 → 越接近碰撞（100% = 已经撞上）
    const proximity = Math.max(0, Math.min(100, (1 - minClear / ref) * 100))
    let level
    if (minClear <= 0) level = 'collision'      // ≥100%
    else if (proximity >= 90) level = 'danger'  // 余量 < 15mm
    else if (proximity >= 80) level = 'warn'    // 余量 < 30mm
    else level = 'safe'
    return { clear: minClear, proximity, level }
  }

  _disposeFence() {
    if (!this._fence) return
    this.scene.remove(this._fence.group)
    this._fence.group.traverse((o) => {
      if (o.geometry) o.geometry.dispose()
      const mats = Array.isArray(o.material) ? o.material : [o.material]
      for (const m of mats) if (m) m.dispose()
    })
    this._fence = null
  }

  // ────────────────── 销毁 ──────────────────
  dispose() {
    this.clear()
    this.scene.remove(this.objectsGroup)
    // 环境对象（地面 / 网格 / 区域标记）必须显式移除并释放，
    // 否则组件重挂载（HMR / 路由切换）会在 scene 里不断叠加。
    for (const helper of this._groundHelpers) {
      this.scene.remove(helper)
      if (helper.geometry) helper.geometry.dispose()
      const mats = Array.isArray(helper.material) ? helper.material : [helper.material]
      for (const m of mats) if (m) m.dispose()
    }
    this._groundHelpers.length = 0
    // 围栏是独立 group，必须显式释放，否则 HMR 会在 scene 里不断叠加。
    this._disposeFence()
  }
}