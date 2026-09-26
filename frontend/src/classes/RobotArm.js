// RobotArm.js — 埃夫特 ER3-600 六轴机器人 Three.js 控制类
//
// ══════════════════════════════════════════════════════════════════════════
//  数模来源
// ══════════════════════════════════════════════════════════════════════════
//  geometry 全部来自官方 ER3-600 机器人数模 V1.2.STEP（埃夫特官网下载中心），
//  由 tools/step2glb 解析 CYLINDRICAL_SURFACE 实体共轴聚类反解出真实关节轴线，
//  导出为 public/models/er3_600.glb。没有任何手工复刻的 DH 参数或猜测几何。
//
// ══════════════════════════════════════════════════════════════════════════
//  坐标系与单位（三个系，务必分清）
// ══════════════════════════════════════════════════════════════════════════
//   1) 数模 / CAD 系：Z 轴向上，原点在基座安装面中心，单位 **米**（GLB 内部）。
//      与 backend/kinematics.py 完全一致（MOVELP 的 X/Y/Z/A/B/C 就是这个系）。
//   2) 基座系：CAD 系换成 **毫米**，朝向不变。getTcpPose() 返回这个系的值，
//      因此可以直接和后端 fk_flange_pose() 的输出逐位对比。
//   3) 场景系：Three.js，Y 轴向上，单位 **毫米**。SceneManager / 相机 / 网格都在这个系里。
//
//  全项目**只有两处**坐标/单位换算，且都集中在本文件：
//      this.group.rotation.x = -Math.PI / 2     // CAD Z-up → 场景 Y-up
//      modelRoot.scale.setScalar(M_TO_MM)       // 数模 米 → 场景 毫米
//  除这两行之外，任何地方都不允许再做换算。
//
// ══════════════════════════════════════════════════════════════════════════
//  关节链（数模实测，mm）
// ══════════════════════════════════════════════════════════════════════════
//   J1 轴 +Z 过 (0, 0, ·)            基座回转
//   J2 轴 ±Y 过 (0, ·, 367.5)        肩   —— 距 J1 轴 367.5
//   J3 轴 ±Y 过 (0, ·, 662.5)        肘   —— 大臂 295.0
//   J4 轴 +X 过 (·, 0, 699.5)        小臂回转 —— 相对 J3 偏置 (0,0,37.0)
//   J5 轴 ±Y 过 (295.5, ·, 699.5)    腕俯仰 —— 小臂 295.5，腕心 (295.5, 0, 699.5)
//   J6 轴 +X 过 (·, 0, 699.5)        末端回转 —— 与 J5 同过腕心（球形腕）
//   法兰面 x = 374  →  腕心到法兰面 78.5

import * as THREE from 'three'
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js'

import {
  AXIS_COUNT,
  MODEL_URL,
  HOME_JOINTS_DEG,
  JOINT_LIMIT_DEG,
  JOINT_LIMITS_DEG,
  JOINT_MAX_SPEED_DEG_S,
  JOINT_AXIS,
  JOINT_SIGN,
  CAD_MEASURED_MM,
  M_TO_MM,
  GRIPPER_LENGTH_MM,
  clampJoints,
} from '../constants/robot.js'

// 向后兼容重导出（老代码从 RobotArm.js 引这些常量）
export {
  HOME_JOINTS_DEG,
  JOINT_LIMITS_DEG,
  JOINT_LIMIT_DEG,
  JOINT_SIGN,
}

/** 数模子树内部单位是米 */
const MM = 1 / M_TO_MM

/** 吸盘接触判定容差（mm）。
 *  15mm 是"唇口必须精准压住顶面中心"的严格值，但画面上的机械臂是在
 *  追赶后端遥测帧（补间有滞后），严格值会让 3D 吸附时有时无。
 *  放宽到 60mm：仍是"贴在物体正上方"的合理范围；低帧率机器上画面追得慢，
 *  这个余量能保证吸附不丢。吸附后还会做一次垂直贴合（见 attachObject），
 *  所以放宽不会留下悬空缝隙。 */
const CONTACT_TOL_MM = 60

/** 吸附瞬间允许的垂直贴合上限（mm）。
 *  唇口与物体顶面的残余间隙在这个范围内时，把物体沿世界 Y 提上来贴住唇口，
 *  避免画面追帧慢时物体"悬"在吸盘下方。
 *  上限是为了防止误吸附时把远处的物体一下吸到天上。 */
const CONTACT_SNAP_MM = 80

/**
 * 补间速度缩放系数。
 *
 * ⚠️ 保持 1.0 —— 即真机额定角速度，**不做人为降速**。
 *    早期版本压到 0.35 想让动作"柔和"一点，结果在低帧率机器上看起来就是
 *    「拖了滑杆机器人几乎不动」，用户明确要求过「不是真机，不要限制移动」。
 *    1.0 下：J1 从 0° 到 90° 约 0.22s，是"看得见但不等"的速度。
 *
 *    注意：用户手动拖滑杆走的是 store 的 snap 路径（见 stores/robot.js），
 *    根本不经过这里；本函数只负责后端遥测帧那种连续流的平滑显示。
 */
const DEMO_SPEED_SCALE = 1.0

// ── 复用的临时对象（避免每帧分配） ──────────────────────────────
const _mInv = new THREE.Matrix4()
const _mFlange = new THREE.Matrix4()
const _mRot = new THREE.Matrix4()
const _v3 = new THREE.Vector3()
const _v3b = new THREE.Vector3()
const _box = new THREE.Box3()

/**
 * 从齐次矩阵取出 ZYX-RPY 角（deg），约定 R = Rz(c)·Ry(b)·Rx(a)
 * 与 backend/kinematics.py 的 matrix_to_rpy() 完全一致。
 */
function rpyFromMatrix4(m) {
  const e = _mRot.extractRotation(m).elements // 列主序
  const r00 = e[0], r10 = e[1], r20 = e[2]
  const r21 = e[6], r22 = e[10]
  const RAD = 180 / Math.PI
  const b = Math.atan2(-r20, Math.hypot(r00, r10))
  const a = Math.atan2(r21, r22)
  const c = Math.atan2(r10, r00)
  return [a * RAD, b * RAD, c * RAD]
}

export class RobotArm {
  /**
   * @param {THREE.Scene} scene
   * @param {{ modelUrl?: string, axisLength?: number }} [options]
   */
  constructor(scene, options = {}) {
    this.scene = scene
    this.modelUrl = options.modelUrl || MODEL_URL

    // ── 唯一坐标转换点 ①：CAD Z-up → 场景 Y-up ──────────────
    // 本节点的局部空间 = 基座系（CAD 朝向、毫米），世界空间 = Y-up 毫米。
    this.group = new THREE.Group()
    this.group.name = 'robotArm'
    this.group.rotation.x = -Math.PI / 2
    scene.add(this.group)

    // 关节链引用：'J1'..'J6' -> Object3D（局部系与 CAD 系同向）
    this.joints = Object.create(null)
    this.jointAngles = [...HOME_JOINTS_DEG]   // 当前显示角（度）
    this.targetAngles = [...HOME_JOINTS_DEG]  // 目标角（度，供插值）

    // 关键节点
    this.model = null          // GLB 根节点（ER3_600）
    this.flangeFrame = null    // 法兰面坐标系（TCP 所在，+X 为法兰法向）
    this.toolGroup = null      // 吸盘工具组（挂载被吸取物体的父节点）
    this.tcpMarker = null      // TCP 标记（法兰面中心）
    this.toolTip = null        // 吸盘唇口中心
    this.envelope = null       // 运动范围包络

    // 状态
    this.ready = false
    this.error = null
    this.suckOn = false
    this.holdingObject = null

    this._disposed = false
    this._cupMaterial = null
    this._loadPromise = this._loadModel()
  }

  /** 数模加载完成后 resolve(true)；失败 resolve(false) 并写 this.error */
  whenReady() {
    return this._loadPromise
  }

  // ══════════════════════════════════════════════════════════════
  //  加载与装配
  // ══════════════════════════════════════════════════════════════
  _loadModel() {
    const loader = new GLTFLoader()
    return new Promise((resolve) => {
      loader.load(
        this.modelUrl,
        (gltf) => {
          if (this._disposed) {
            this._disposeSubtree(gltf.scene)
            resolve(false)
            return
          }
          try {
            this._mount(gltf)
            resolve(true)
          } catch (err) {
            this.error = err
            console.error('[RobotArm] 数模装配失败:', err)
            resolve(false)
          }
        },
        undefined,
        (err) => {
          this.error = err
          console.error('[RobotArm] 数模加载失败:', this.modelUrl, err)
          resolve(false)
        },
      )
    })
  }

  _mount(gltf) {
    const root = gltf.scene.getObjectByName('ER3_600') || gltf.scene
    root.name = 'ER3_600'
    // ── 唯一单位转换点 ②：数模 米 → 场景 毫米 ──────────────
    root.scale.setScalar(M_TO_MM)
    this.group.add(root)
    this.model = root

    root.traverse((o) => {
      if (JOINT_AXIS[o.name]) this.joints[o.name] = o
      if (o.name === 'WorkspaceEnvelope') {
        this.envelope = o
        o.visible = false
      }
      if (o.isMesh) {
        const mats = Array.isArray(o.material) ? o.material : [o.material]
        const isEnvelope = mats.some((m) => m && m.name === 'mat_envelope')
        o.castShadow = !isEnvelope
        o.receiveShadow = !isEnvelope
        for (const m of mats) {
          if (!m) continue
          m.envMapIntensity = 0.8
          if (isEnvelope) m.depthWrite = false
        }
      }
    })

    const missing = Object.keys(JOINT_AXIS).filter((n) => !this.joints[n])
    if (missing.length) {
      throw new Error(`数模缺少关节节点: ${missing.join(', ')}（检查 ${this.modelUrl} 的节点命名）`)
    }

    this._buildTool()

    this.ready = true
    this._applyJointRotation()
  }

  /** 法兰坐标系 + 真空吸盘（吸盘是仿真附加工具，不属于机器人本体） */
  _buildTool() {
    const tool_mm = CAD_MEASURED_MM.tool

    // 法兰面坐标系：J6 轴点沿 +X 前进 78.5mm（数模实测）
    const flange = new THREE.Object3D()
    flange.name = 'FlangeFrame'
    flange.position.set(tool_mm * MM, 0, 0)
    this.joints.J6.add(flange)
    this.flangeFrame = flange

    const bodyMat = new THREE.MeshStandardMaterial({
      color: 0x8f959c, metalness: 0.85, roughness: 0.32, name: 'mat_tool_body',
    })
    const cupMat = new THREE.MeshStandardMaterial({
      color: 0x2b2f36, metalness: 0.35, roughness: 0.62, name: 'mat_cup',
    })
    this._cupMaterial = cupMat

    // 法兰盘（贴合安装面）
    const discGeo = new THREE.CylinderGeometry(31 * MM, 31 * MM, 6 * MM, 32)
      .rotateZ(-Math.PI / 2)
      .translate(3 * MM, 0, 0)
    const disc = new THREE.Mesh(discGeo, bodyMat)
    disc.name = 'flangeDisc'
    disc.castShadow = true
    flange.add(disc)

    // 吸盘杆 + 唇口（沿法兰 +X 方向伸出）
    const L = GRIPPER_LENGTH_MM
    const stemGeo = new THREE.CylinderGeometry(14 * MM, 18 * MM, L * MM, 28)
      .rotateZ(-Math.PI / 2)
      .translate((L / 2) * MM, 0, 0)
    const cup = new THREE.Mesh(stemGeo, cupMat)
    cup.name = 'suctionCup'
    cup.castShadow = true
    flange.add(cup)

    const lipGeo = new THREE.CylinderGeometry(26 * MM, 22 * MM, 4 * MM, 28)
      .rotateZ(-Math.PI / 2)
      .translate((L - 2) * MM, 0, 0)
    const lip = new THREE.Mesh(lipGeo, cupMat)
    lip.name = 'cupLip'
    lip.castShadow = true
    flange.add(lip)

    // TCP 标记：法兰面中心（与后端 fk_flange_pose 同一点）
    const tcp = new THREE.Mesh(
      new THREE.SphereGeometry(5 * MM, 16, 16),
      new THREE.MeshStandardMaterial({ color: 0xff3b30, emissive: 0x3a0906, metalness: 0.1, roughness: 0.5 }),
    )
    tcp.name = 'tcpMarker'
    flange.add(tcp)
    this.tcpMarker = tcp

    // 工具组：被吸取物体的挂载父节点
    const toolGroup = new THREE.Group()
    toolGroup.name = 'tool'
    flange.add(toolGroup)
    this.toolGroup = toolGroup

    // 吸盘唇口中心（接触判定用）
    const tip = new THREE.Object3D()
    tip.name = 'toolTip'
    tip.position.set(GRIPPER_LENGTH_MM * MM, 0, 0)
    toolGroup.add(tip)
    this.toolTip = tip
  }

  // ══════════════════════════════════════════════════════════════
  //  关节控制
  // ══════════════════════════════════════════════════════════════
  /**
   * 直接设置关节角（度）。会按各轴软限位裁剪。
   * @param {number[]} anglesDeg
   * @param {boolean} [immediate] true 时同时把目标角对齐（跳过插值）
   */
  updateJoints(anglesDeg, immediate = false) {
    this.jointAngles = clampJoints(anglesDeg)
    if (immediate) this.targetAngles = [...this.jointAngles]
    this._applyJointRotation()
  }

  /** 设置插值目标角（度） */
  setTargetAngles(anglesDeg) {
    this.targetAngles = clampJoints(anglesDeg)
  }

  _applyJointRotation() {
    if (!this.ready) return
    for (let i = 0; i < AXIS_COUNT; i++) {
      const name = `J${i + 1}`
      const node = this.joints[name]
      if (!node) continue
      // 官方关节角 → 几何旋转量（JOINT_SIGN，与 backend/kinematics.py 同表）
      const rot = THREE.MathUtils.degToRad(this.jointAngles[i]) * JOINT_SIGN[i]
      node.rotation[JOINT_AXIS[name]] = rot
    }
    this.group.updateMatrixWorld(true)
  }

  /**
   * 关节补间（每帧调用一次）。
   *
   * ⚠️ 必须是**时间相关**的，不能写成「每帧走固定比例」。
   *    历史实现是 `jointAngles += diff * 0.15`（每帧固定系数），于是运动快慢
   *    完全由帧率决定：144Hz 上 0.2s 到位，10Hz 上要 2s —— 在低帧率机器上
   *    看起来就像「拖了滑杆机器人几乎不动」。这里改为按各轴最大角速度推进，
   *    与渲染帧率解耦。
   *
   * @param {number} dtSeconds 距上一帧的秒数
   * @param {{speedScale?:number, settleEps?:number}} [opts]
   *        speedScale 演示速度系数（真机满速 400~840°/s 太快，默认压到 35%）
   * @returns {boolean} 是否发生了变化
   */
  lerpUpdate(dtSeconds, opts = {}) {
    if (!this.ready) return false
    // 夹住 dt：切后台/卡顿后不要一帧瞬移到位
    const dt = Math.min(0.1, Math.max(0, Number(dtSeconds) || 0))
    if (dt === 0) return false
    const speedScale = opts.speedScale ?? DEMO_SPEED_SCALE
    const settleEps = opts.settleEps ?? 1e-3

    let changed = false
    for (let i = 0; i < AXIS_COUNT; i++) {
      const diff = this.targetAngles[i] - this.jointAngles[i]
      if (Math.abs(diff) <= settleEps) {
        if (diff !== 0) { this.jointAngles[i] = this.targetAngles[i]; changed = true }
        continue
      }
      const vmax = JOINT_MAX_SPEED_DEG_S[i] * speedScale   // °/s
      const step = Math.min(Math.abs(diff), vmax * dt)
      this.jointAngles[i] += Math.sign(diff) * step
      changed = true
    }
    if (changed) this._applyJointRotation()
    return changed
  }

  /** 关节角是否已收敛到目标 */
  get settled() {
    for (let i = 0; i < AXIS_COUNT; i++) {
      if (Math.abs(this.targetAngles[i] - this.jointAngles[i]) > 0.05) return false
    }
    return true
  }

  // ══════════════════════════════════════════════════════════════
  //  TCP / 位姿
  // ══════════════════════════════════════════════════════════════
  /** 法兰面中心在「基座系（CAD Z-up，mm）」下的齐次矩阵 */
  _flangeMatrixInBase(out) {
    this.group.updateMatrixWorld(true)
    _mInv.copy(this.group.matrixWorld).invert()
    return out.multiplyMatrices(_mInv, this.flangeFrame.matrixWorld)
  }

  /**
   * TCP 位姿，**基座系（CAD Z-up，毫米 + ZYX-RPY 度）**。
   * 与后端 fk_flange_pose() 输出逐位同义，可直接和 MOVELP 的 X/Y/Z/A/B/C 对照。
   * @returns {{x:number,y:number,z:number,a:number,b:number,c:number}}
   */
  getTcpPose() {
    if (!this.ready || !this.flangeFrame) {
      return { x: 0, y: 0, z: 0, a: 0, b: 0, c: 0 }
    }
    const m = this._flangeMatrixInBase(_mFlange)
    const p = _v3.setFromMatrixPosition(m)
    const [a, b, c] = rpyFromMatrix4(m)
    return { x: p.x, y: p.y, z: p.z, a, b, c }
  }

  /** 法兰面中心在场景世界系（Y-up，mm）的位置——用于渲染/接触判定 */
  getTcpWorldPosition() {
    if (!this.ready || !this.flangeFrame) return new THREE.Vector3()
    return this.flangeFrame.getWorldPosition(new THREE.Vector3())
  }

  /** 吸盘唇口中心在场景世界系（mm）的位置 */
  getToolTipWorldPosition() {
    if (!this.ready || !this.toolTip) return new THREE.Vector3()
    return this.toolTip.getWorldPosition(new THREE.Vector3())
  }

  /** 腕心（J6 轴点，即 J5/J6 交点）在基座系（mm）的位置 */
  getWristCenter() {
    if (!this.ready || !this.joints.J6) return { x: 0, y: 0, z: 0 }
    this.group.updateMatrixWorld(true)
    _mInv.copy(this.group.matrixWorld).invert()
    const p = _v3.setFromMatrixPosition(_mFlange.multiplyMatrices(_mInv, this.joints.J6.matrixWorld))
    return { x: p.x, y: p.y, z: p.z }
  }

  /** 法兰法向（单位向量）在基座系下的方向——用于判断工具是否朝下 */
  getToolDirection() {
    if (!this.ready || !this.flangeFrame) return new THREE.Vector3(1, 0, 0)
    const m = this._flangeMatrixInBase(_mFlange)
    return _v3.setFromMatrixColumn(m, 0).normalize().clone()
  }

  // ══════════════════════════════════════════════════════════════
  //  吸盘 / 抓取
  // ══════════════════════════════════════════════════════════════
  setSuck(on) {
    this.suckOn = !!on
    if (this._cupMaterial) {
      this._cupMaterial.color.setHex(on ? 0x00b45a : 0x2b2f36)
      this._cupMaterial.emissive.setHex(on ? 0x00391c : 0x000000)
    }
    if (!on) this.holdingObject = null
  }

  /** 把物体重父化到吸盘（保留世界变换），并沿世界 Y 把顶面贴到唇口 */
  attachObject(obj) {
    if (!obj || !this.toolGroup) return
    this.toolGroup.attach(obj)
    this.toolGroup.updateMatrixWorld(true)

    // 记下「被拿起时」的世界坐标。物体跟着吸盘走之后，它的实时世界坐标就是
    // 机械臂的位置，对场景而言没有意义；物体列表和后端执行器要的是一个稳定
    // 的逻辑位置，所以上报快照时用这个值（见 SceneManager.toJSON）。
    const parked = obj.getWorldPosition(_v3b)
    obj.userData.parkedPosition = [parked.x, parked.y, parked.z]

    // 垂直贴合：checkContact 是带容差命中的，画面追帧慢时唇口可能还差几十
    // 毫米。这里把物体沿世界 Y 提上来补掉这段间隙（横向不动，避免侧向瞬移），
    // 让「吸住」在画面上真的是贴住的。偏差超过 CONTACT_SNAP_MM 则不动，
    // 宁可留缝也不做夸张的瞬移。
    if (this.toolTip) {
      const tip = this.getToolTipWorldPosition()
      _box.setFromObject(obj)
      if (Number.isFinite(_box.max.y)) {
        const dy = tip.y - _box.max.y
        if (Math.abs(dy) > 1e-3 && Math.abs(dy) <= CONTACT_SNAP_MM) {
          const wp = obj.getWorldPosition(_v3b)
          wp.y += dy
          this.toolGroup.worldToLocal(wp)
          obj.position.copy(wp)
          this.toolGroup.updateMatrixWorld(true)
        }
      }
    }

    this.holdingObject = obj
    obj.userData.attached = true
  }

  /** 把物体重父化回场景（保留世界变换） */
  detachObject(target) {
    if (!this.holdingObject) return
    const parent = target || this.scene
    parent.attach(this.holdingObject)
    this.holdingObject.userData.attached = false
    // 已经放回场景，逻辑位置重新以真实世界坐标为准
    delete this.holdingObject.userData.parkedPosition
    this.holdingObject = null
  }

  /**
   * 在候选物体中找出被吸盘唇口压住的那一个。
   * @param {THREE.Object3D[]} objects
   * @returns {THREE.Object3D|null}
   */
  checkContact(objects) {
    if (!this.ready || !this.toolTip || !Array.isArray(objects)) return null
    const tip = this.getToolTipWorldPosition()
    for (const obj of objects) {
      if (!obj || !obj.isMesh) continue
      if (obj.userData && obj.userData.attached) continue
      _box.setFromObject(obj)
      if (!Number.isFinite(_box.max.y)) continue
      _v3b.set(
        (_box.min.x + _box.max.x) / 2,
        _box.max.y,
        (_box.min.z + _box.max.z) / 2,
      )
      if (tip.distanceTo(_v3b) > CONTACT_TOL_MM) continue
      // 唇口必须在物体顶面之上（不能从侧面/下方“吸”）
      if (tip.y >= _box.max.y - 5) return obj
    }
    return null
  }

  // ══════════════════════════════════════════════════════════════
  //  显示控制
  // ══════════════════════════════════════════════════════════════
  /** 运动范围包络（官方工作空间）显隐 */
  setEnvelopeVisible(visible) {
    if (this.envelope) this.envelope.visible = !!visible
  }

  /** 线框模式（仅作用于数模本体 + 工具，不含场景物体） */
  setWireframe(on) {
    const apply = (m) => { if (m) m.wireframe = !!on }
    this.group.traverse((o) => {
      if (!o.isMesh) return
      if (Array.isArray(o.material)) o.material.forEach(apply)
      else apply(o.material)
    })
  }

  /** 数模统计（用于演示页/自检页显示） */
  stats() {
    let triangles = 0
    let meshes = 0
    this.group.traverse((o) => {
      if (!o.isMesh) return
      meshes++
      const g = o.geometry
      triangles += g && g.index ? g.index.count / 3 : (g ? g.attributes.position.count / 3 : 0)
    })
    return { meshes, triangles, joints: Object.keys(this.joints).length, ready: this.ready }
  }

  /**
   * 机器人本体（不含运动范围包络）在**场景世界系（Y-up，mm）**的包围盒。
   *
   * 注意：不能用 `Box3.setFromObject(this.group)` —— 它会把隐藏的
   * WorkspaceEnvelope 也算进去（three 的 setFromObject 不跳过 visible=false），
   * 那样包围盒会膨胀到整个工作空间，相机自动取景会把机器人缩成一个小点。
   *
   * @returns {THREE.Box3} 空盒表示尚无可见几何
   */
  getVisibleBounds() {
    const box = new THREE.Box3()
    if (!this.ready) return box
    this.group.updateMatrixWorld(true)
    const tmp = new THREE.Box3()
    this.group.traverse((o) => {
      if (!o.isMesh || !o.geometry) return
      // 逐级向上检查可见性
      for (let p = o; p; p = p.parent) if (p.visible === false) return
      const mats = Array.isArray(o.material) ? o.material : [o.material]
      if (mats.some((m) => m && m.name === 'mat_envelope')) return
      tmp.setFromObject(o)
      if (!tmp.isEmpty()) box.union(tmp)
    })
    return box
  }

  /**
   * 机器人本体在**场景世界系（Y-up，mm）**的代表采样点。
   *
   * 供安全围栏做「连杆 vs 围栏几何」临近度计算用。取各关节节点
   * （J1..J6）+ 法兰面 + 吸盘唇口的世界坐标，并对相邻节点之间的连杆做
   * 细分采样，避免长连杆（大臂/小臂）中段穿过围栏却因为两端点都在内部
   * 而被漏判。返回的是世界坐标数组（Vector3），围栏模块直接用。
   *
   * @returns {THREE.Vector3[]}
   */
  getLinkWorldPoints() {
    if (!this.ready) return []
    this.group.updateMatrixWorld(true)
    const nodes = [
      this.group,          // 基座原点（地面）
      this.joints.J1,
      this.joints.J2,
      this.joints.J3,
      this.joints.J4,
      this.joints.J5,
      this.joints.J6,
      this.flangeFrame,
      this.toolTip,
    ].filter(Boolean).map((o) => o.getWorldPosition(new THREE.Vector3()))

    const pts = []
    const SEG = 3 // 每段连杆细分 3 段，覆盖中段
    for (let i = 0; i < nodes.length; i++) {
      pts.push(nodes[i])
      if (i < nodes.length - 1) {
        const a = nodes[i]
        const b = nodes[i + 1]
        for (let s = 1; s < SEG; s++) pts.push(a.clone().lerp(b, s / SEG))
      }
    }
    return pts
  }

  // ══════════════════════════════════════════════════════════════
  //  销毁
  // ══════════════════════════════════════════════════════════════
  _disposeSubtree(obj) {
    obj.traverse((child) => {
      if (!child.isMesh) return
      if (child.geometry) child.geometry.dispose()
      const mats = Array.isArray(child.material) ? child.material : [child.material]
      for (const m of mats) if (m) m.dispose()
    })
  }

  dispose() {
    this._disposed = true
    this.ready = false
    if (this.group.parent) this.group.parent.remove(this.group)
    this._disposeSubtree(this.group)
    this.joints = Object.create(null)
    this.model = null
    this.flangeFrame = null
    this.toolGroup = null
    this.tcpMarker = null
    this.toolTip = null
    this.envelope = null
    this._cupMaterial = null
  }
}

export default RobotArm
