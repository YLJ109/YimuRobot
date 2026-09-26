#!/usr/bin/env node
// ui_probe.mjs — 前端 UI/控制链路端到端回归探针
//
// 用 Chrome DevTools Protocol 驱动真实页面（不是单元测试、不是 mock），
// 验证几件「肉眼看不出来但一坏就是致命」的事：
//
//   0. 取景     机器人在画面里占多大（自动取景是否把它缩到合理比例）
//   1. 关节滑杆  真的用鼠标拖 J1..J6 的滑杆 → 数模实际关节角是否跟着走
//   2. 笛卡尔   真的拖 X 滑杆 → 数值逆解是否成功、6 根滑杆是否与机器人一致
//   3. 程序执行  点「运行」 → 后端是否回帧、TCP 是否真的动
//   5. 智能助手  提问后「思考中」必须结束（不能永久转圈）
//   6. 吸盘吸附  PICK 后物体是否真的被挂到工具组
//   7. 示例按钮  点一下就执行（不是只把文字填进输入框）
//
// 关键点：1 和 2 走的是**真实 UI 事件**（在 .el-slider__runway 上派发
// mousedown），不是直接调 store。历史 bug 恰好就出在「store 写得对、
// 但滑杆到不了 store」这一层，绕过 UI 测不出来。
//
// 用法：
//   node tools/ui_probe.mjs [url]
//   url 默认 http://127.0.0.1:3000/
//
// 前置：前端 dev server 已启动；若测「程序执行」还需后端跑在 5000。

import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const URL_TO_PROBE = process.argv[2] || 'http://127.0.0.1:3000/'
const PORT = 9333
const CHROME = process.env.CHROME_PATH ||
  'C:/Program Files/Google/Chrome/Application/chrome.exe'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const results = []
function record(name, pass, detail) {
  results.push({ name, pass, detail })
  const tag = pass ? '\x1b[32mPASS\x1b[0m' : '\x1b[31mFAIL\x1b[0m'
  console.log(`  ${tag}  ${name}${detail ? '  —  ' + detail : ''}`)
}

// ────────────────────────── CDP 客户端 ──────────────────────────
class CDP {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    /** 页面控制台 / 未捕获异常 —— 应用挂不起来时全靠它定位 */
    this.consoleLogs = []
    ws.addEventListener('message', (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch { return }
      if (msg.method === 'Runtime.consoleAPICalled') {
        const text = (msg.params.args || [])
          .map((a) => a.value ?? a.description ?? a.type)
          .join(' ')
        this.consoleLogs.push(`[${msg.params.type}] ${text}`)
      } else if (msg.method === 'Runtime.exceptionThrown') {
        const d = msg.params.exceptionDetails
        this.consoleLogs.push(`[exception] ${d.exception?.description || d.text}`)
      } else if (msg.method === 'Log.entryAdded') {
        this.consoleLogs.push(`[${msg.params.entry.level}] ${msg.params.entry.text}`)
      }
      if (msg.id && this.pending.has(msg.id)) {
        const { resolve, reject } = this.pending.get(msg.id)
        this.pending.delete(msg.id)
        if (msg.error) reject(new Error(msg.error.message))
        else resolve(msg.result)
      }
    })
  }
  dumpConsole(limit = 25) {
    const logs = this.consoleLogs.slice(-limit)
    if (!logs.length) return '  （页面无控制台输出）'
    return logs.map((l) => '  ' + l).join('\n')
  }
  send(method, params = {}, timeoutMs = 30000) {
    const id = ++this.id
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id)
          reject(new Error(`CDP ${method} 超时`))
        }
      }, timeoutMs)
    })
  }
  /**
   * 在页面里跑一段表达式并取回值（支持 await）。
   * timeoutMs 必须大于页面内最长等待，否则 CDP 会先超时 —— 而页面里的
   * 循环还在跑，会得到一个「探针异常」而不是清晰的业务失败。
   */
  async eval(expression, timeoutMs = 30000) {
    const r = await this.send('Runtime.evaluate', {
      expression,
      awaitPromise: true,
      returnByValue: true,
      userGesture: true,
    }, timeoutMs)
    if (r.exceptionDetails) {
      throw new Error('页面异常: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text))
    }
    return r.result?.value
  }
}

async function waitForHttp(url, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const r = await fetch(url)
      if (r.ok) return true
    } catch { /* 还没起来 */ }
    await sleep(200)
  }
  return false
}

/**
 * 注入到页面里的滑杆拖拽工具。
 * Element Plus 的 .el-slider__runway 绑的是 mousedown（onSliderDown），
 * 在按比例算出的像素位置派发一个合成 mousedown 就能触发它的取值逻辑。
 */
const PAGE_HELPERS = `
function __dragSlider(runway, ratio) {
  const r = runway.getBoundingClientRect()
  const x = r.left + r.width * ratio
  const y = r.top + r.height / 2
  runway.dispatchEvent(new MouseEvent('mousedown', { clientX: x, clientY: y, bubbles: true, button: 0 }))
  document.dispatchEvent(new MouseEvent('mouseup', { clientX: x, clientY: y, bubbles: true }))
}
function __ratio(value, min, max) { return (value - min) / (max - min) }
`

async function main() {
  const profile = mkdtempSync(join(tmpdir(), 'uiprobe-'))
  let chrome = null
  let ws = null

  try {
    console.log('▶ 启动无头 Chrome …')
    chrome = spawn(CHROME, [
      '--headless=new',
      `--remote-debugging-port=${PORT}`,
      `--user-data-dir=${profile}`,
      '--no-first-run', '--no-default-browser-check',
      '--disable-gpu', '--enable-unsafe-swiftshader', '--no-sandbox',
      '--hide-scrollbars', '--no-proxy-server',
      '--window-size=1600,900',
      'about:blank',
    ], { stdio: 'ignore' })

    if (!(await waitForHttp(`http://127.0.0.1:${PORT}/json/version`))) {
      throw new Error('Chrome 调试端口未就绪')
    }

    const created = await fetch(
      `http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(URL_TO_PROBE)}`,
      { method: 'PUT' },
    ).then((r) => r.json())

    ws = new WebSocket(created.webSocketDebuggerUrl)
    await new Promise((res, rej) => {
      ws.addEventListener('open', res, { once: true })
      ws.addEventListener('error', () => rej(new Error('WebSocket 连接失败')), { once: true })
    })
    const cdp = new CDP(ws)
    await cdp.send('Runtime.enable')
    await cdp.send('Page.enable')
    await cdp.send('Log.enable')

    // ── 等应用挂载 + 数模就绪 ──
    console.log('▶ 等待应用与机器人数模就绪 …')
    const t0 = Date.now()
    let booted = false
    while (Date.now() - t0 < 40000) {
      try {
        const ok = await cdp.eval(`!!(window.__sim && window.__sim.stores.robot)`)
        if (ok) { booted = true; break }
      } catch { /* 页面还在加载 */ }
      await sleep(300)
    }
    if (!booted) {
      console.error('\n页面控制台：\n' + cdp.dumpConsole())
      throw new Error('应用未挂载（window.__sim 缺失）— 确认跑的是 dev server 且窗口无报错')
    }

    const ready = await cdp.eval(`(async () => {
      for (let i = 0; i < 100; i++) {
        if (document.querySelector('.viewport-loading') === null && window.__sim.stores.robot.actualJoints) return true
        await new Promise(r => setTimeout(r, 200))
      }
      return false
    })()`)
    if (!ready) throw new Error('机器人数模未在 20s 内加载完成')

    await cdp.eval(PAGE_HELPERS)
    await sleep(600)

    // ══════════════ 检查 0：取景占比 ══════════════
    console.log('\n▶ 检查 0：自动取景（模型在画面里的占比）')
    const cov = await cdp.eval(`(() => {
      const vp = window.__sim && window.__sim.viewport
      if (!vp) return { error: 'window.__sim.viewport 缺失；__sim keys = ' + JSON.stringify(Object.keys(window.__sim || {})) }
      return vp.coverage()
    })()`)
    if (!cov || cov.error) {
      record('取景探针可用', false, cov?.error || '无返回值')
    } else {
      // NDC 尺寸：2.0 = 正好铺满视口。既要「没占满」，也要「没小成一个点」。
      record('模型未占满画面（NDC 高 < 1.6）', cov.h < 1.6,
        `NDC 覆盖 ${cov.w.toFixed(2)} × ${cov.h.toFixed(2)}（2.0 = 铺满）`)
      record('模型尺寸合理（NDC 高 > 0.5）', cov.h > 0.5,
        `包围球半径 ${cov.radius.toFixed(0)}mm，相机距离 ${cov.dist.toFixed(0)}mm`)
    }

    // ══════════════ 检查 1：关节滑杆（真实鼠标拖拽）══════════════
    console.log('\n▶ 检查 1：关节滑杆（拖 J3 / J2 的滑杆 → 数模实际角）')
    const j1 = await cdp.eval(`(async () => {
      const s = window.__sim.stores.robot
      s.setAllJoints([0,0,0,0,0,0], { snap: true })
      await new Promise(r => setTimeout(r, 500))

      const rows = [...document.querySelectorAll('.joint-row')]
      if (rows.length !== 6) return { error: '关节行数不是 6：' + rows.length }

      // J3 量程 [-65, 185]，目标 45°
      __dragSlider(rows[2].querySelector('.el-slider__runway'), __ratio(45, -65, 185))
      await new Promise(r => setTimeout(r, 350))
      const afterJ3 = { cmd: s.joints[2], actual: s.actualJoints[2] }

      // J2 量程 [-135, 85]，目标 -30°
      __dragSlider(rows[1].querySelector('.el-slider__runway'), __ratio(-30, -135, 85))
      await new Promise(r => setTimeout(r, 350))
      const afterJ2 = { cmd: s.joints[1], actual: s.actualJoints[1] }

      return { afterJ3, afterJ2, cmd: s.joints.slice(), actual: s.actualJoints.slice(), tcp: { ...s.tcp } }
    })()`)

    if (j1.error) {
      record('关节面板含 6 行滑杆', false, j1.error)
    } else {
      record('关节面板含 6 行滑杆', true, '6 行')
      record('拖 J3 滑杆到 45°', Math.abs(j1.afterJ3.cmd - 45) < 3,
        `指令 ${j1.afterJ3.cmd.toFixed(2)}°`)
      record('拖 J2 滑杆到 -30°', Math.abs(j1.afterJ2.cmd - (-30)) < 3,
        `指令 ${j1.afterJ2.cmd.toFixed(2)}°`)
      // snap 语义：指令一下发，实际角立刻跟上（这就是「不限制移动」）
      record('J3 实际角即时跟上指令（无速度限制）',
        Math.abs(j1.afterJ3.actual - j1.afterJ3.cmd) < 0.05,
        `实际 ${j1.afterJ3.actual.toFixed(2)}° / 指令 ${j1.afterJ3.cmd.toFixed(2)}°`)
      record('J2 实际角即时跟上指令（无速度限制）',
        Math.abs(j1.afterJ2.actual - j1.afterJ2.cmd) < 0.05,
        `实际 ${j1.afterJ2.actual.toFixed(2)}° / 指令 ${j1.afterJ2.cmd.toFixed(2)}°`)
    }

    // ══════════════ 检查 2：笛卡尔滑杆（真实鼠标拖拽 + 数值逆解）══════════════
    console.log('\n▶ 检查 2：笛卡尔滑杆（切模式 → 拖 X 滑杆 → 逆解下发）')
    const c1 = await cdp.eval(`(async () => {
      const s = window.__sim.stores.robot
      s.setAllJoints([0,0,0,0,0,0], { snap: true })
      await new Promise(r => setTimeout(r, 500))

      const modeBtns = [...document.querySelectorAll('.mode-btn')]
      const cartBtn = modeBtns.find(b => b.textContent.includes('笛卡尔'))
      if (!cartBtn) return { error: '未找到「笛卡尔控制」按钮' }
      cartBtn.click()
      await new Promise(r => setTimeout(r, 700))

      const rows = [...document.querySelectorAll('.cart-row')]
      if (rows.length !== 6) return { error: '笛卡尔行数不是 6：' + rows.length }
      const read = () => rows.map(r => parseFloat(r.querySelector('.cart-val').textContent))
      const before = read()
      const jointsBefore = s.actualJoints.slice()

      // X 量程 [-600, 600]，拖到 150mm
      __dragSlider(rows[0].querySelector('.el-slider__runway'), __ratio(150, -600, 600))
      await new Promise(r => setTimeout(r, 1500))

      const after = read()
      const errEl = document.querySelector('.cart-err')
      return {
        before, after, jointsBefore,
        cmdJoints: s.joints.slice(),
        actualJoints: s.actualJoints.slice(),
        tcp: { ...s.tcp },
        error: errEl ? errEl.textContent.trim() : null,
      }
    })()`)

    if (c1.error && c1.error.startsWith('未找到')) {
      record('笛卡尔模式切换', false, c1.error)
    } else if (c1.error) {
      record('笛卡尔面板含 6 根位姿滑杆', false, c1.error)
    } else {
      record('笛卡尔面板含 6 根位姿滑杆', true, '6 根')
      // 硬约束：被拖的那一轴必须到达目标（±8mm 容忍滑杆像素量化）
      record('拖 X 滑杆后 X 到达目标 ≈150mm', Math.abs(c1.after[0] - 150) < 8,
        `X ${c1.before[0].toFixed(1)} → ${c1.after[0].toFixed(1)} mm（目标 150）`)
      const jDelta = c1.actualJoints.reduce((a, v, i) => a + Math.abs(v - c1.jointsBefore[i]), 0)
      record('拖 X 后关节角真正变化（逆解成功）', jDelta > 1,
        `Σ|ΔJ| = ${jDelta.toFixed(2)}° → J = [${c1.actualJoints.map(v => v.toFixed(1)).join(', ')}]`)
      record('笛卡尔拖动无报错', !c1.error, c1.error || '无错误提示')

      // ── 「适配」断言：6 根滑杆显示值必须等于机器人真实位姿 ──
      // 拖一个自由度时其余轴会被 IK 一起带动，滑杆若还停在用户拖出来的
      // 理想值上就会与画面里看到的机器人对不上。
      const keys = ['x', 'y', 'z', 'a', 'b', 'c']
      const tol = [2, 2, 2, 1, 1, 1]
      const diffs = keys.map((k, i) => Math.abs(c1.after[i] - c1.tcp[k]))
      const worst = Math.max(...diffs.map((d, i) => d / tol[i]))
      record('滑杆显示值 = 机器人实际位姿（IK 耦合已适配）', worst < 1,
        `最大偏差 ${diffs.map((d, i) => keys[i] + ' ' + d.toFixed(2)).join(', ')}`)
    }

    // ══════════════ 检查 3：程序执行 ══════════════
    console.log('\n▶ 检查 3：运行 DSL 程序（需要后端在 5000）')
    const connected = await cdp.eval(`window.__sim.stores.robot.connected`)
    if (!connected) {
      record('后端已连接', false, '未连接 → 跳过执行链路检查（先启动 backend/app.py）')
    } else {
      record('后端已连接', true, 'socket 在线')
      // 默认示例程序（HOME + WAIT 1 + 两个 MOVEJ + 两次 WAIT）实测约 7s，
      // 这里给到 14s 并**等它跑完**，而不是定长等待 —— 否则程序还没做完
      // 就断言「没动」，只会得到一个假失败。
      const r1 = await cdp.eval(`(async () => {
        const s = window.__sim.stores.robot
        const e = window.__sim.stores.editor
        s.setAllJoints([0,0,0,0,0,0], { snap: true })
        await new Promise(r => setTimeout(r, 400))
        const tcpBefore = { ...s.tcp }
        const jointsBefore = s.actualJoints.slice()
        let maxJointSwing = 0
        let maxTcpMove = 0
        e.clearLogs()
        const btn = document.querySelector('.tool-btn.is-run')
        if (!btn) return { error: '未找到运行按钮' }
        btn.click()
        const t0 = Date.now()
        // 轮询到执行结束（或 14s 超时）
        while (Date.now() - t0 < 14000) {
          await new Promise(r => setTimeout(r, 200))
          for (let i = 0; i < 6; i++) {
            const d = Math.abs(s.actualJoints[i] - jointsBefore[i])
            if (d > maxJointSwing) maxJointSwing = d
          }
          const m = Math.hypot(
            s.tcp.x - tcpBefore.x, s.tcp.y - tcpBefore.y, s.tcp.z - tcpBefore.z,
          )
          if (m > maxTcpMove) maxTcpMove = m
          if (s.execState !== 'running' && Date.now() - t0 > 1200) break
        }
        return {
          execState: s.execState,
          elapsed: Date.now() - t0,
          tcpBefore,
          tcpAfter: { ...s.tcp },
          jointsBefore,
          joints: s.actualJoints.slice(),
          maxJointSwing,
          maxTcpMove,
          logs: e.logs.map(l => l.level + ': ' + l.message).slice(-12),
        }
      })()`)
      record('运行按钮可点击并触发执行', !r1.error, r1.error || `execState=${r1.execState}（耗时 ${(r1.elapsed / 1000).toFixed(1)}s）`)
      record('执行期间关节真实摆动', r1.maxJointSwing > 5,
        `最大单轴摆幅 ${r1.maxJointSwing.toFixed(1)}° → J=[${r1.joints.map(v => v.toFixed(1)).join(', ')}]`)
      // 采样执行过程中的峰值位移：程序结尾回 HOME，只看首末会读成 0
      record('执行期间 TCP 位姿发生位移', r1.maxTcpMove > 5, `峰值位移 ${r1.maxTcpMove.toFixed(1)}mm`)
      record('收到后端执行日志', r1.logs.length > 0, r1.logs.length ? r1.logs.slice(-2).join(' | ') : '无日志')
    }

    // ══════════════ 检查 4：场景物体增删改（本地优先）══════════════
    // 历史上改颜色/拖坐标只写 store 再发后端，等后端广播回来才更新 3D，
    // 于是 socket 一断就完全没反应。这里直接比对数模本体的颜色与坐标。
    console.log('\n▶ 检查 4：场景物体改动即时落到 3D 数模')
    const sc = await cdp.eval(`(async () => {
      const st = window.__sim.stores.scene
      const sp = window.__sim.scene
      if (!sp) return { error: 'window.__sim.scene 缺失（确认是 DEV 构建）' }
      const first = st.objects[0]
      if (!first) return { error: '场景里没有物体' }
      const before = sp.probe(first.id)
      const snapshot = st.getSnapshot()[0] || {}

      // 走 ObjectLibrary 实际使用的同一条链路
      window.dispatchEvent(new CustomEvent('scene_patch', {
        detail: { id: first.id, updates: { color: '#00ff88', position: [123, 45, 67] } },
      }))
      await new Promise(r => setTimeout(r, 300))

      const after = sp.probe(first.id)
      // 再验证一次「新增」也能落到 3D
      const n0 = sp.count()
      const obj = { id: 9001, type: 'sphere', name: '探针球', color: '#ff8800', position: [10, 20, 30], size: [40, 40, 40], grabbable: true }
      st.addObject(obj)
      window.dispatchEvent(new CustomEvent('scene_add', { detail: obj }))
      await new Promise(r => setTimeout(r, 300))
      const added = sp.probe(9001)

      window.dispatchEvent(new CustomEvent('scene_remove', { detail: { id: 9001 } }))
      await new Promise(r => setTimeout(r, 300))
      const afterRemove = sp.probe(9001)

      return { before, after, added, afterRemove, removedCount: sp.count(), n0, snapshotKeys: Object.keys(snapshot) }
    })()`)

    if (sc.error) {
      record('场景探针可用', false, sc.error)
    } else {
      record('改颜色即时生效（落到了 3D 材质）', sc.after && sc.after.color === '#00ff88',
        `${sc.before?.color} → ${sc.after?.color}`)
      record('拖坐标即时生效（落到了 3D 位置）',
        sc.after && sc.after.position[0] === 123 && sc.after.position[2] === 67,
        `[${sc.before?.position?.join(', ')}] → [${sc.after?.position?.join(', ')}]`)
      record('新增物体即时落到 3D 且保留类型',
        !!sc.added && sc.added.type === 'sphere',
        sc.added ? `type=${sc.added.type} color=${sc.added.color}` : '未找到新物体')
      record('删除物体即时从 3D 移除', sc.afterRemove === null, sc.afterRemove ? '仍存在' : '已移除')
      // 旧版快照丢了 id/type，后端广播回来时所有物体都会被重建成方块
      record('场景快照带 id / type（供后端与 AI 使用）',
        sc.snapshotKeys.includes('id') && sc.snapshotKeys.includes('type'),
        `字段：${sc.snapshotKeys.join(', ')}`)
    }

    // ══════════════ 检查 5：智能助手思考态必定结束 ══════════════
    // 用户报「一直卷圈」。历史上 sendText 只把 isProcessing 置真，完全依赖
    // ai_reply 来复位：socket 没连上/后端超时/回复丢了，转圈就永不停止。
    console.log('\n▶ 检查 5：智能助手提问后「思考中」必定结束')
    const ai = await cdp.eval(`(async () => {
      const a = window.__sim.stores.ai
      a.clearMessages()
      a.setProcessing(false)
      a.inputText = '回家'
      // 发送按钮是 :disabled="!inputText.trim()"，Vue 的 DOM 更新是异步的。
      // 同步点下去会打在「仍然 disabled」的按钮上，什么都不会发生 ——
      // 必须等一帧让 disabled 解除。
      await new Promise(r => setTimeout(r, 120))
      const btn = document.querySelector('.composer-btn.is-send')
      if (!btn) return { error: '未找到发送按钮' }
      if (btn.disabled) return { error: '发送按钮仍处于禁用态（输入未生效）' }
      btn.click()
      const t0 = Date.now()
      while (Date.now() - t0 < 40000) {
        await new Promise(r => setTimeout(r, 300))
        if (!a.isProcessing) {
          const last = a.messages[a.messages.length - 1] || {}
          return { ok: true, ms: Date.now() - t0, msgCount: a.messages.length, last: (last.content || '').slice(0, 60) }
        }
      }
      return { ok: false, ms: Date.now() - t0, blocking: a.isProcessing }
    })()`)
    if (ai.error) {
      record('智能助手发送按钮可用', false, ai.error)
    } else {
      record('提问后思考态结束（不再永久转圈）', ai.ok,
        ai.ok ? `${ai.ms}ms 内结束，末条回复："${ai.last}"` : `超过 40s 仍在转圈`)
      record('提问后进入过对话列表', ai.msgCount >= 2, `消息数 ${ai.msgCount}`)
    }

    // ══════════════ 检查 6：吸盘真实吸附（PICK 真的把物体吸起来）══════════════
    // RobotArm 里 checkContact / attachObject / detachObject 三个方法一直存在，
    // 但从来没人调用 —— 「吸取」在 3D 里只是唇口变个颜色，物体纹丝不动。
    // 这条断言直接跑一段 PICK，看数模里的 attached 标记和实际父节点。
    console.log('\n▶ 检查 6：吸盘真实吸附（跑 PICK → 物体被重父化到工具组）')
    const grab = await cdp.eval(`(async () => {
      const sp = window.__sim.scene
      const st = window.__sim.stores.scene
      const e = window.__sim.stores.editor
      const s = window.__sim.stores.robot
      if (!sp || !sp.heldName) return { error: 'window.__sim.scene 不完整（确认 DEV 构建）' }
      // 用演示红方块（-480,65,140 → 基座系 x=-480,y=-140, 支撑面 0）
      const target = st.objects.find(o => o.name === '红色方块') || st.objects[0]
      if (!target) return { error: '场景里没有物体' }
      // 检查 4 把第一个物体挪到了 [123,45,67]，先把它放回 A 区工位，
      // 否则 PICK 的目标点下方根本没有物体，吸附当然命中不了。
      window.dispatchEvent(new CustomEvent('scene_patch', {
        detail: { id: target.id, updates: { position: [-480, 65, 140] } },
      }))
      await new Promise(r => setTimeout(r, 350))
      const before = sp.probe(target.id)
      e.setCode('SPEED 100\\nPICK target=[-480,-140,0]')
      await new Promise(r => setTimeout(r, 150))
      const btn = document.querySelector('.tool-btn.is-run')
      if (!btn) return { error: '未找到运行按钮' }
      btn.click()
      const t0 = Date.now()
      let after = before
      while (Date.now() - t0 < 30000) {
        await new Promise(r => setTimeout(r, 250))
        after = sp.probe(target.id)
        if (after && after.attached) break
      }
      return { id: target.id, name: target.name, before, after, held: sp.heldName(), heldState: s.holding }
    })()`, 45000)
    if (grab.error) {
      record('PICK 吸附链路可用', false, grab.error)
    } else {
      record('PICK 后物体被吸附（attached=true）', !!grab.after.attached,
        `${grab.name}: attached=${grab.before.attached} → ${grab.after.attached}`)
      record('物体父节点变为工具组', grab.after.parent !== grab.before.parent,
        `parent: "${grab.before.parent}" → "${grab.after.parent}"`)
      record('机器人持物状态指向真实物体名', grab.held === grab.name || grab.heldState === grab.name,
        `heldName=${grab.held} holding=${grab.heldState}`)
    }

    // ══════════════ 检查 7：示例指令按钮必须「点了就执行」══════════════
    // 这三个按钮一度只是 `aiStore.inputText = s` —— 点一下把文字填进输入框
    // 就没了，用户还得再点一次发送。看着像坏了，实际上就是只填不发。
    // 这条断言直接派发真实 click，看对话列表里有没有冒出一条用户消息
    //（= 真的发出去了），而不是只看输入框内容变了没有。
    console.log('\n▶ 检查 7：智能助手示例按钮点了直接执行')
    const chipRun = await cdp.eval(`(async () => {
      const a = window.__sim.stores.ai
      a.clearMessages()
      a.setProcessing(false)
      a.inputText = ''
      await new Promise(r => setTimeout(r, 150))

      const chips = [...document.querySelectorAll('.sample-chip')]
      if (!chips.length) return { error: '页面上找不到 .sample-chip 示例按钮' }
      const chip = chips.find(c => (c.textContent || '').trim() === '回家') || chips[0]
      if (chip.disabled) return { error: '示例按钮处于禁用态（未连接仿真服务）' }

      const label = (chip.textContent || '').trim()
      chip.click()

      // ① 必须真的发出去：等一条内容含该按钮文案的用户消息
      const t0 = Date.now()
      let sent = false
      while (Date.now() - t0 < 8000) {
        await new Promise(r => setTimeout(r, 150))
        if (a.messages.some(m => m.role === 'user' && (m.content || '').includes(label))) {
          sent = true
          break
        }
      }
      if (!sent) {
        return { error: '点了按钮但对话里没有用户消息（只填输入框没发送）',
                 label, inputText: a.inputText, msgCount: a.messages.length }
      }

      // ② 必须拿到回复、思考态必须结束
      let replied = false
      const t1 = Date.now()
      while (Date.now() - t1 < 40000) {
        await new Promise(r => setTimeout(r, 300))
        if (!a.isProcessing) break
      }
      replied = a.messages.some(m => m.role === 'assistant' && (m.content || '').trim().length > 0)
      return { label, chipCount: chips.length, sent, replied, ms: Date.now() - t1,
               msgCount: a.messages.length }
    })()`, 60000)
    if (chipRun.error) {
      record('示例按钮点了就执行', false, chipRun.error)
    } else {
      record('示例按钮存在（三个样例）', chipRun.chipCount >= 3,
        `${chipRun.chipCount} 个，点击的是「${chipRun.label}」`)
      record('点击即发送（对话里出现用户消息）', chipRun.sent, '已发出')
      record('点击后拿到真实回复且思考态结束', chipRun.replied,
        `${chipRun.ms}ms 内结束，消息数 ${chipRun.msgCount}`)
    }

    // ══════════════ 汇总 ══════════════
    const failed = results.filter((r) => !r.pass)
    console.log('\n' + '='.repeat(64))
    console.log(`  合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`)
    console.log('='.repeat(64))
    if (failed.length) {
      console.log('  失败项：')
      for (const f of failed) console.log(`    ✗ ${f.name} — ${f.detail}`)
    }
    process.exitCode = failed.length ? 1 : 0
  } catch (err) {
    console.error('\n\x1b[31m探针异常：\x1b[0m', err.message)
    process.exitCode = 2
  } finally {
    try { ws?.close() } catch { /* ignore */ }
    try { chrome?.kill() } catch { /* ignore */ }
    await sleep(300)
    try { rmSync(profile, { recursive: true, force: true }) } catch { /* ignore */ }
  }
}

main()
