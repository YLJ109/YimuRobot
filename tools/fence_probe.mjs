#!/usr/bin/env node
// fence_probe.mjs — 安全围栏端到端探针
//
// 用户报「碰撞了却是绿色」，怀疑分级反了。tools/fence_test.mjs 已经在纯 Node
// 里证明了 fenceClearance() 的数学是对的（9/9），但这个探针要证明的是
// **整条链路**也对：默认值合理（开机不是恒碰撞）、缩小后真的变红、
// 独立组件 SafetyFencePanel 的样式真的生效。
//
//   1. 围栏已创建，默认尺寸 850/1100
//   2. 开机级别 = safe（绿）—— 旧默认值 650/700 在这里会恒红
//   3. 缩小到 280 → 级别变 collision（红闪）—— 证明不是"反了"
//   4. 恢复 850 → 回到 safe
//   5. 安全围栏 tab 存在且可点开
//   6. 独立组件样式生效（面板有高度、状态条有边框色、指示灯有底色、滑杆齐全）
//
// 用法：node tools/fence_probe.mjs [url]
// 前置：vite dev server 跑在 3000（DEV 模式才有 window.__sim）

import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const URL_TO_PROBE = process.argv[2] || 'http://127.0.0.1:3000/'
const PORT = 9334
const CHROME = process.env.CHROME_PATH ||
  'C:/Program Files/Google/Chrome/Application/chrome.exe'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
function record(name, pass, detail) {
  results.push({ name, pass })
  const tag = pass ? '\x1b[32mPASS\x1b[0m' : '\x1b[31mFAIL\x1b[0m'
  console.log(`  ${tag}  ${name}${detail ? '  —  ' + detail : ''}`)
}

class CDP {
  constructor(ws) {
    this.ws = ws
    this.id = 0
    this.pending = new Map()
    this.consoleLogs = []
    ws.addEventListener('message', (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch { return }
      if (msg.method === 'Runtime.consoleAPICalled') {
        const text = (msg.params.args || []).map((a) => a.value ?? a.description ?? a.type).join(' ')
        this.consoleLogs.push(`[${msg.params.type}] ${text}`)
      } else if (msg.method === 'Runtime.exceptionThrown') {
        const d = msg.params.exceptionDetails
        this.consoleLogs.push(`[exception] ${d.exception?.description || d.text}`)
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
    return logs.length ? logs.map((l) => '  ' + l).join('\n') : '  （无输出）'
  }
  send(method, params = {}, timeoutMs = 30000) {
    const id = ++this.id
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject })
      this.ws.send(JSON.stringify({ id, method, params }))
      setTimeout(() => {
        if (this.pending.has(id)) { this.pending.delete(id); reject(new Error(`CDP ${method} 超时`)) }
      }, timeoutMs)
    })
  }
  async eval(expression, timeoutMs = 30000) {
    const r = await this.send('Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true, userGesture: true,
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
    try { const r = await fetch(url); if (r.ok) return true } catch { /* not up */ }
    await sleep(200)
  }
  return false
}

/** 轮询直到页面里的取值满足条件 */
async function poll(cdp, expr, predicate, timeoutMs = 8000) {
  const t0 = Date.now()
  let last
  while (Date.now() - t0 < timeoutMs) {
    try {
      last = await cdp.eval(expr)
      if (predicate(last)) return last
    } catch { /* ignore */ }
    await sleep(250)
  }
  return last
}

async function main() {
  const profile = mkdtempSync(join(tmpdir(), 'fenceprobe-'))
  let chrome = null
  let ws = null
  try {
    console.log('▶ 启动无头 Chrome …')
    chrome = spawn(CHROME, [
      '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
      '--no-first-run', '--no-default-browser-check',
      '--disable-gpu', '--enable-unsafe-swiftshader', '--no-sandbox',
      '--hide-scrollbars', '--no-proxy-server', '--window-size=1600,900',
      'about:blank',
    ], { stdio: 'ignore' })

    if (!(await waitForHttp(`http://127.0.0.1:${PORT}/json/version`))) throw new Error('Chrome 调试端口未就绪')
    const created = await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(URL_TO_PROBE)}`, { method: 'PUT' }).then((r) => r.json())
    ws = new WebSocket(created.webSocketDebuggerUrl)
    await new Promise((res, rej) => {
      ws.addEventListener('open', res, { once: true })
      ws.addEventListener('error', () => rej(new Error('WebSocket 连接失败')), { once: true })
    })
    const cdp = new CDP(ws)
    await cdp.send('Runtime.enable')
    await cdp.send('Page.enable')

    console.log('▶ 等待应用与数模就绪 …')
    let booted = false
    const t0 = Date.now()
    while (Date.now() - t0 < 40000) {
      try { if (await cdp.eval(`!!(window.__sim && window.__sim.viewport && window.__sim.viewport.fence)`)) { booted = true; break } } catch { /* loading */ }
      await sleep(300)
    }
    if (!booted) {
      console.error('\n页面控制台：\n' + cdp.dumpConsole())
      throw new Error('应用未挂载（window.__sim.viewport.fence 缺失）')
    }

    console.log('\n── 围栏几何与默认级别 ──')
    const f0 = await cdp.eval(`window.__sim.viewport.fence()`)
    record('围栏已创建，默认半边 850mm', f0.half === 850, `half=${f0.half}`)
    record('围栏默认高度 1100mm', f0.height === 1100, `height=${f0.height}`)
    record('围栏默认可见', f0.visible === true, `visible=${f0.visible}`)

    // 关键：开机必须是安全（绿）。旧默认值 650/700 会让顶部余量为负 → 恒红。
    const s0 = await poll(cdp, `window.__sim.viewport.fence()`, (v) => v && v.level === 'safe', 8000)
    record('开机级别 = safe（绿）', s0 && s0.level === 'safe',
      `level=${s0?.level}, 余量=${s0?.clearance?.toFixed?.(1)}mm`)

    console.log('\n── 缩小到 280mm：必须变碰撞（红闪）──')
    await cdp.eval(`window.__sim.viewport.forceCollide()`)
    const s1 = await poll(cdp, `window.__sim.viewport.fence()`, (v) => v && v.level === 'collision', 8000)
    record('缩小后级别 = collision（红闪）', s1 && s1.level === 'collision',
      `level=${s1?.level}, 余量=${s1?.clearance?.toFixed?.(1)}mm`)
    {
      const shotRed = await cdp.send('Page.captureScreenshot', { format: 'png' })
      if (shotRed?.data) writeFileSync(join(process.cwd(), 'tools', 'fence_collision.png'), Buffer.from(shotRed.data, 'base64'))
    }

    console.log('\n── 重载页面：必须回到安全（证明碰撞态可解除、默认值合理）──')
    await cdp.send('Page.reload', { ignoreCache: false })
    const s2 = await poll(cdp, `window.__sim && window.__sim.viewport && window.__sim.viewport.fence() ? window.__sim.viewport.fence() : null`,
      (v) => v && v.level === 'safe', 25000)
    record('重载后级别 = safe（绿）', s2 && s2.level === 'safe',
      `level=${s2?.level}, 余量=${s2?.clearance?.toFixed?.(1)}mm`)

    console.log('\n── 安全围栏 tab 与独立组件样式 ──')
    const clicked = await cdp.eval(`(() => {
      const tabs = [...document.querySelectorAll('.el-tabs__item')]
      const t = tabs.find(e => e.textContent.trim() === '安全围栏')
      if (!t) return 'notfound'
      t.click()
      return 'clicked'
    })()`)
    record('tab 栏存在「安全围栏」项', clicked === 'clicked', `结果=${clicked}`)
    await sleep(800)

    const ui = await poll(cdp, `(() => {
      const p = document.querySelector('.fence-panel')
      if (!p) return null
      const st = p.querySelector('.fence-status')
      const dot = p.querySelector('.fence-dot')
      const cs = st ? getComputedStyle(st) : null
      const cd = dot ? getComputedStyle(dot) : null
      return {
        panelH: Math.round(p.getBoundingClientRect().height),
        statusBorder: cs ? cs.borderTopColor : null,
        statusBg: cs ? cs.backgroundColor : null,
        dotBg: cd ? cd.backgroundColor : null,
        sliders: p.querySelectorAll('.el-slider').length,
        sliderW: Math.round((p.querySelector('.el-slider')?.getBoundingClientRect().width) || 0),
        metrics: (p.querySelector('.fence-metrics')?.textContent || '').trim(),
        tip: !!p.querySelector('.fence-tip'),
      }
    })()`, (v) => v && v.panelH > 0, 8000)

    record('独立组件已渲染（有高度）', !!ui && ui.panelH > 100, `panelH=${ui?.panelH}px`)
    record('状态条有边框色（样式生效）', !!ui && ui.statusBorder && ui.statusBorder !== 'rgba(0, 0, 0, 0)', `border=${ui?.statusBorder}`)
    record('指示灯有底色（--glow/色变量生效）', !!ui && ui.dotBg && ui.dotBg !== 'rgba(0, 0, 0, 0)', `dot=${ui?.dotBg}`)
    record('两根滑杆（范围/高度）都在', !!ui && ui.sliders === 2, `sliders=${ui?.sliders}`)
    record('滑杆有实际宽度（未被 flex 挤没）', !!ui && ui.sliderW > 40, `sliderW=${ui?.sliderW}px`)
    record('接近度/余量文案在', !!ui && /接近度/.test(ui.metrics || ''), ui?.metrics)
    record('说明文案 .fence-tip 在', !!ui && ui.tip === true)

    // 截图存档
    const shot = await cdp.send('Page.captureScreenshot', { format: 'png' })
    if (shot?.data) {
      const out = join(process.cwd(), 'tools', 'fence_tab.png')
      writeFileSync(out, Buffer.from(shot.data, 'base64'))
      console.log(`\n  截图已保存: ${out}`)
    }

    console.log('\n页面控制台（最后 12 条）：\n' + cdp.dumpConsole(12))
  } catch (e) {
    console.error('\n探针异常：', e.message)
    process.exitCode = 1
  } finally {
    try { ws?.close() } catch { /* ignore */ }
    try { chrome?.kill('SIGKILL') } catch { /* ignore */ }
    try { rmSync(profile, { recursive: true, force: true }) } catch { /* ignore */ }
  }

  const passed = results.filter((r) => r.pass).length
  console.log(`\n结果: ${passed}/${results.length} 通过\n`)
  if (passed !== results.length) process.exitCode = 1
}

main()
