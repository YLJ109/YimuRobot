#!/usr/bin/env node
// ui_shot.mjs — 截取 3D 视口 / 整页截图
//
// 用法：
//   node tools/ui_shot.mjs [输出路径] [url] [viewport|full]
//   默认  tools/shots/viewport.png  http://127.0.0.1:3000/  viewport
//
// 为什么要单独一个脚本：ui_probe.mjs 只能验「链路通不通」，
// 而「模型在画面里好不好看」这类判断必须落到像素上。

import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'

const OUT = resolve(process.argv[2] || 'tools/shots/viewport.png')
const PAGE = process.argv[3] || 'http://127.0.0.1:3000/'
const MODE = process.argv[4] || 'viewport'
const PORT = 9351
const CHROME = process.env.CHROME_PATH || 'C:/Program Files/Google/Chrome/Application/chrome.exe'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const profile = mkdtempSync(join(tmpdir(), 'uishot-'))

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--enable-unsafe-swiftshader',
  '--no-sandbox', '--hide-scrollbars', '--no-proxy-server',
  '--window-size=1600,900', '--force-device-scale-factor=1',
  'about:blank',
], { stdio: 'ignore' })

let ws = null
try {
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(`http://127.0.0.1:${PORT}/json/version`)).ok) break } catch { /* wait */ }
    await sleep(200)
  }
  const t = await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(PAGE)}`, { method: 'PUT' }).then((r) => r.json())
  ws = new WebSocket(t.webSocketDebuggerUrl)
  await new Promise((res, rej) => {
    ws.addEventListener('open', res, { once: true })
    ws.addEventListener('error', () => rej(new Error('WebSocket 连接失败')), { once: true })
  })

  let id = 0
  const pend = new Map()
  ws.addEventListener('message', (ev) => {
    let m
    try { m = JSON.parse(ev.data) } catch { return }
    if (m.id && pend.has(m.id)) {
      const { resolve: res, reject: rej } = pend.get(m.id)
      pend.delete(m.id)
      m.error ? rej(new Error(m.error.message)) : res(m.result)
    }
  })
  const send = (method, params) => {
    const i = ++id
    return new Promise((res, rej) => { pend.set(i, { resolve: res, reject: rej }); ws.send(JSON.stringify({ id: i, method, params: params || {} })) })
  }
  const ev = async (expr) => {
    const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true })
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text)
    return r.result?.value
  }

  await send('Runtime.enable')
  await send('Page.enable')

  for (let i = 0; i < 120; i++) {
    try { if (await ev('!!window.__sim')) break } catch { /* loading */ }
    await sleep(300)
  }
  await ev('(async()=>{for(let i=0;i<100;i++){if(!document.querySelector(".viewport-loading"))return true;await new Promise(r=>setTimeout(r,200))}return false})()')
  await sleep(1500)

  let clip
  if (MODE === 'viewport') {
    clip = await ev(`(() => {
      const el = document.querySelector('.robot-viewport')
      if (!el) return null
      const r = el.getBoundingClientRect()
      return { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) }
    })()`)
  }

  const shot = await send('Page.captureScreenshot', {
    format: 'png',
    ...(clip ? { clip: { ...clip, scale: 1 } } : {}),
    captureBeyondViewport: false,
  })

  mkdirSync(dirname(OUT), { recursive: true })
  writeFileSync(OUT, Buffer.from(shot.data, 'base64'))
  console.log(`已保存 ${OUT}${clip ? `  (${clip.width}×${clip.height})` : '  (整页)'}`)
} catch (e) {
  console.error('截图失败:', e.message)
  process.exitCode = 1
} finally {
  try { ws?.close() } catch { /* ignore */ }
  try { chrome?.kill() } catch { /* ignore */ }
  await sleep(300)
  try { rmSync(profile, { recursive: true, force: true }) } catch { /* ignore */ }
}
