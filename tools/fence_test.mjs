// tools/fence_test.mjs — 安全围栏临近度分级单测（纯 Node，不需要浏览器）
//
// 为什么要有这个：用户在界面上看到「碰撞了却是绿色」，怀疑分级逻辑写反了。
// 界面截图看不出是几何问题、默认值问题还是阈值反了 —— 只有把 fenceClearance()
// 拉到 Node 里喂确定的坐标、断言确定的级别，才能一锤定音。
//
// SceneManager 只 import three（纯几何，无 WebGL），所以能直接在 Node 里跑。
//
// 运行：node tools/fence_test.mjs

import * as THREE from '../frontend/node_modules/three/build/three.module.js'
import { SceneManager } from '../frontend/src/classes/SceneManager.js'

const REF = 150
const HALF = 850
const HEIGHT = 1100
const PILLAR_R = 20

const scene = new THREE.Scene()
const sm = new SceneManager(scene)
sm.createSafetyFence({ half: HALF, height: HEIGHT, pillarR: PILLAR_R, ref: REF })

let pass = 0
let fail = 0

function check(name, pt, expectLevel) {
  const res = sm.fenceClearance([new THREE.Vector3(pt[0], pt[1], pt[2])])
  const got = res.level
  const ok = got === expectLevel
  if (ok) pass++
  else fail++
  const tag = ok ? 'PASS' : 'FAIL'
  console.log(
    `  [${tag}] ${name}\n` +
    `         点(${pt.join(', ')}) → 余量 ${res.clear.toFixed(1)}mm, ` +
    `接近度 ${res.proximity.toFixed(1)}% → ${got}` +
    `${ok ? '' : `  (期望 ${expectLevel})`}`,
  )
}

console.log(`\n围栏: 半边 ${HALF}mm, 高 ${HEIGHT}mm, 角柱 r=${PILLAR_R}mm, 警告带 REF=${REF}mm\n`)
console.log('阈值口径: 接近度 100%=碰撞(红闪) / ≥90%=危险(红) / ≥80%=警告(黄) / 其余=安全(绿)\n')
console.log('─ 远离围栏（应安全/绿）─')
check('中心低位', [0, 100, 0], 'safe')
check('离墙 200mm', [HALF - 200, 100, 0], 'safe')

console.log('─ 接近度 ≥80%（应警告/黄）─')
check('离墙 25mm (83%)', [HALF - 25, 100, 0], 'warn')

console.log('─ 接近度 ≥90%（应危险/红）─')
check('离墙 10mm (93%)', [HALF - 10, 100, 0], 'danger')

console.log('─ 已穿出（应碰撞/红闪）─')
check('穿出侧墙 50mm', [HALF + 50, 100, 0], 'collision')
check('刚好贴墙 (余量0)', [HALF, 100, 0], 'collision')

console.log('─ 顶部边界（高度参数有效）─')
check('低于顶面很安全', [0, 500, 0], 'safe')
check('穿出顶面 100mm', [0, HEIGHT + 100, 0], 'collision')

console.log('─ 角柱（四角更敏感）─')
check('贴近角柱内侧', [HALF - 5, 100, HALF - 5], 'collision')

console.log(`\n结果: ${pass} 通过 / ${fail} 失败\n`)
process.exit(fail === 0 ? 0 : 1)
