#!/usr/bin/env node
// capture_shots.mjs — 给 README 拍项目截图（headless Chrome + CDP）
//
// 用法：
//   node tools/capture_shots.mjs [url] [outdir]
//   url    默认 http://127.0.0.1:3000/
//   outdir 默认 docs/shots
//
// 前置：
//   - 前端 dev server 已在 :3000 运行
//   - 后端已在 :5000 运行（否则顶栏 LED 是「未连接」，截图不好看）
//
// 拍 4 张（保存到 outdir）：
//   yimu_01_main.png    主界面总览（手动控制 + 3D 视口 + AI 面板 + DSL 编辑器）
//   yimu_02_fence.png   安全围栏标签页
//   yimu_03_objects.png 物体库标签页
//   yimu_04_ai.png      AI 对话（点示例按钮「回家」后的真实回复）

import { spawn } from 'node:child_process'
import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const URL_TO_SHOOT = process.argv[2] || 'http://127.0.0.1:3000/'
const OUT_DIR = process.argv[3] || 'docs/shots'
const DEBUG_PORT = 9341
const CHROME = process.env.CHROME_PATH ||
  'C:/Program Files/Google/Chrome/Application/chrome.exe'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

mkdirSync(OUT_DIR, { recursive: true })
const profile = join(tmpdir(), 'shots-' + Date.now())

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${DEBUG_PORT}`, `--user-data-dir=${profile}`,
  '--window-size=1440,900', '--force-device-scale-factor=2',
  '--no-first-run', '--hide-scrollbars', '--enable-unsafe-swiftshader',
  URL_TO_SHOOT,
], { stdio: 'ignore' })

// ────────────────────────── CDP 迷你客户端 ──────────────────────────
let ws
const pending = new Map()
let seq = 0
function send(method, params = {}) {
  return new Promise((res, rej) => {
    const id = ++seq
    pending.set(id, { res, rej })
    ws.send(JSON.stringify({ id, method, params }))
    setTimeout(() => {
      if (pending.has(id)) { pending.delete(id); rej(new Error('CDP 超时: ' + method)) }
    }, 30000)
  })
}

async function evaljs(expression) {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true })
  if (r.exceptionDetails) {
    throw new Error('eval 失败: ' + JSON.stringify(r.exceptionDetails).slice(0, 300))
  }
  return r.result?.value
}

async function shot(name) {
  const r = await send('Page.captureScreenshot', { format: 'png' })
  writeFileSync(join(OUT_DIR, name), Buffer.from(r.data, 'base64'))
  console.log('  saved  ' + name)
}

async function waitFor(expr, timeoutMs = 30000, label = expr) {
  const t0 = Date.now()
  while (Date.now() - t0 < timeoutMs) {
    try { if (await evaljs(expr)) return } catch { /* 页面未就绪，继续等 */ }
    await sleep(500)
  }
  throw new Error('waitFor 超时: ' + label)
}

try {
  // ── 找到页面调试目标 ──
  let target = null
  for (let i = 0; i < 40; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${DEBUG_PORT}/json/list`)
      const list = await r.json()
      target = list.find((t) => t.type === 'page' && t.url.includes(':3000'))
      if (target) break
    } catch { /* chrome 未起，重试 */ }
    await sleep(500)
  }
  if (!target) throw new Error('未找到页面调试目标（chrome 没起来？）')

  ws = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data)
    if (m.id && pending.has(m.id)) {
      const p = pending.get(m.id); pending.delete(m.id)
      m.error ? p.rej(new Error(m.error.message)) : p.res(m.result)
    }
  }

  await send('Page.enable')
  await send('Runtime.enable')

  // ── 等应用就绪：canvas 出现 → dev 钩子挂上 → 后端 LED 变绿 ──
  await waitFor(`!!document.querySelector('canvas')`, 30000, 'canvas 出现')
  await waitFor(`!!(window.__sim && window.__sim.stores)`, 30000, '__sim 挂载')
  await waitFor(`!!document.querySelector('.conn-led.online')`, 30000, '后端连接 LED 变绿')
  // 等 Three.js 渲染稳定 + 模型贴图到位
  await sleep(5000)

  // ── 1) 主界面总览（默认「手动控制」标签） ──
  await shot('yimu_01_main.png')

  // ── 2) 安全围栏标签页 ──
  const clickedFence = await evaljs(
    `(() => { const t = [...document.querySelectorAll('.el-tabs__item')]
        .find(e => e.textContent.includes('安全围栏'))
      if (t) { t.click(); return true } return false })()`)
  if (clickedFence) { await sleep(1500); await shot('yimu_02_fence.png') }
  else console.log('  skip   yimu_02_fence.png（没找到安全围栏标签）')

  // ── 3) 物体库标签页 ──
  const clickedObj = await evaljs(
    `(() => { const t = [...document.querySelectorAll('.el-tabs__item')]
        .find(e => e.textContent.includes('物体库'))
      if (t) { t.click(); return true } return false })()`)
  if (clickedObj) { await sleep(1500); await shot('yimu_03_objects.png') }
  else console.log('  skip   yimu_03_objects.png（没找到物体库标签）')

  // ── 4) AI 对话：点示例按钮「回家」→ 等真实回复 ──
  const hasBtn = await evaljs(
    `(() => { const b = [...document.querySelectorAll('button')]
        .find(x => x.textContent.trim() === '回家')
      if (b) { b.click(); return true } return false })()`)
  if (hasBtn) {
    // 快通道一般 ~2s 出回复；留足余量，且等「思考中」消失
    await sleep(9000)
    await shot('yimu_04_ai.png')
  } else {
    console.log('  skip   yimu_04_ai.png（没找到示例按钮「回家」）')
  }

  console.log('完成：4 张截图已保存到 ' + OUT_DIR)
} finally {
  try { chrome.kill() } catch {}
  try { rmSync(profile, { recursive: true, force: true }) } catch {}
}
