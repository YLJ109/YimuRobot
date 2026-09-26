// tools/ptt_probe.mjs —— 验证「长按空格说话、松开发送」的 push-to-talk 链路。
//
// 用 CDP 派发**真实**的物理空格键事件（keydown / keyup，code='Space'），
// 并给 Chrome 挂假麦克风设备（--use-fake-device-for-media-stream），这样
// getUserMedia 能成功、还能产出一段音频喂给后端 ASR，真正走完：
//   空格按下 → startRecording → 空格松开 → stopRecording → audioUtterance
//   → 后端 asr_status(transcribing) / asr_final / asr_error
//
// 同时验证两条守卫：
//   ① 焦点在输入框时不抢空格（否则聊天框打不出空格）；
//   ② 修饰键（Ctrl/Meta/Alt）不触发。
//
// 用法：node tools/ptt_probe.mjs      （前端 3000 + 后端 5000 需已运行）
import { spawn } from 'node:child_process'
import { rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const profile = join(tmpdir(), 'dbg-ptt-' + Date.now())
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const chrome = spawn(CHROME, [
  '--headless=new', '--remote-debugging-port=9336', `--user-data-dir=${profile}`,
  '--window-size=1440,900', '--no-first-run', '--disable-gpu',
  '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
  'http://127.0.0.1:3000/',
], { stdio: 'ignore' })

let ws
const pending = new Map()
let id = 0
function send(method, params = {}, timeoutMs = 30000) {
  const i = ++id
  return new Promise((res, rej) => {
    pending.set(i, { res, rej })
    ws.send(JSON.stringify({ id: i, method, params }))
    setTimeout(() => { if (pending.has(i)) { pending.delete(i); rej(new Error('CDP 超时 ' + method)) } }, timeoutMs)
  })
}
const ev = (expr) => send('Runtime.evaluate', { expression: expr, returnByValue: true }).then((r) => r.result?.value)
const keyDown = () => send('Input.dispatchKeyEvent', { type: 'keyDown', key: ' ', code: 'Space', nativeVirtualKeyCode: 32, windowsVirtualKeyCode: 32 })
const keyUp = () => send('Input.dispatchKeyEvent', { type: 'keyUp', key: ' ', code: 'Space', nativeVirtualKeyCode: 32, windowsVirtualKeyCode: 32 })

const results = {}
try {
  let targets
  for (let i = 0; i < 40; i++) {
    try { const r = await fetch('http://127.0.0.1:9336/json/list'); targets = await r.json(); if (targets.find((t) => t.type === 'page' && t.url.includes(':3000'))) break } catch {}
    await sleep(300)
  }
  const page = targets.find((t) => t.type === 'page' && t.url.includes(':3000'))
  ws = new (globalThis.WebSocket)(page.webSocketDebuggerUrl)
  await new Promise((r) => { ws.onopen = r })
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data)
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.res(m.result); return }
  }
  await send('Runtime.enable')
  await send('Log.enable')

  // 挂页面内事件收集器：asr_status / asr_final / asr_error 都会被 App.vue 派发到 window
  await send('Runtime.evaluate', { expression: `window.__ptt = []; ['asr_status','asr_final','asr_error'].forEach(t => window.addEventListener(t, e => window.__ptt.push({ t, d: e.detail })))` })

  // 等挂载 + 后端连接
  for (let i = 0; i < 30; i++) {
    const ok = await ev(`!!(window.__sim) && !!document.querySelector('.conn-led.online')`)
    if (ok) break
    await sleep(400)
  }
  results.mounted = await ev(`!!(window.__sim)`)
  results.connected = await ev(`!!document.querySelector('.conn-led.online')`)

  // ── 用例 A：空格 push-to-talk 完整链路 ──
  await ev(`(document.activeElement && document.activeElement.blur && document.activeElement.blur())`)
  await ev(`window.__ptt = []`)
  await keyDown()
  // startRecording 是异步的（先 requestPermission 再 getUserMedia），轮询录态而不是只拍一瞬
  results.A_recordingStarted = false
  for (let i = 0; i < 16; i++) {
    if (await ev(`document.querySelector('.mic-btn')?.classList.contains('recording') === true`)) { results.A_recordingStarted = true; break }
    await sleep(150)
  }
  await sleep(1200) // 录 ~1.7s
  await keyUp()
  await sleep(4500) // 等后端 ASR 回包
  results.A_asrEvents = await ev(`JSON.parse(JSON.stringify(window.__ptt || []))`)
  results.A_gotAsr = Array.isArray(results.A_asrEvents) && results.A_asrEvents.length > 0

  // ── 用例 B：焦点在输入框里，空格不应触发录音 ──
  const focused = await ev(`(() => { const el = document.querySelector('input,textarea'); if (el) { el.focus(); return el.tagName } return 'none' })()`)
  results.B_focusedTag = focused
  await ev(`window.__ptt = []`)
  await keyDown()
  await sleep(500)
  results.B_recordingWhileTyping = await ev(`document.querySelector('.mic-btn')?.classList.contains('recording') === true`)
  await keyUp()
  // 兜底：若 B 误触发了录音，松开后应已停，不影响 A 的判定
  await sleep(500)
  results.B_recordingAfterRelease = await ev(`document.querySelector('.mic-btn')?.classList.contains('recording') === true`)

  // ── 用例 C：Ctrl+空格 不触发（保住输入法切换）──
  await ev(`(document.activeElement && document.activeElement.blur && document.activeElement.blur())`)
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: ' ', code: 'Space', nativeVirtualKeyCode: 32, windowsVirtualKeyCode: 32, modifiers: 2 })
  await sleep(400)
  results.C_recordingWithCtrl = await ev(`document.querySelector('.mic-btn')?.classList.contains('recording') === true`)
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: ' ', code: 'Space', nativeVirtualKeyCode: 32, windowsVirtualKeyCode: 32, modifiers: 2 })
} catch (err) {
  console.error('ERR', err.message)
  results.error = err.message
} finally {
  try { ws?.close() } catch {}
  try { chrome.kill() } catch {}
  await sleep(300)
  try { rmSync(profile, { recursive: true, force: true }) } catch {}
}

// ── 判定 ──
const pass = []
const fail = []
const check = (name, cond) => (cond ? pass : fail).push(name)
check('A 挂载', results.mounted)
check('A 后端已连接', results.connected)
check('A 空格按下开始录音', results.A_recordingStarted === true)
check('A 松手触发 ASR 回包', results.A_gotAsr === true)
check('B 输入框内空格不录音', results.B_recordingWhileTyping === false)
check('C Ctrl+空格不录音', results.C_recordingWithCtrl === false)

console.log('--- 结果明细 ---')
console.log(JSON.stringify(results, null, 2))
console.log('--- 判定 ---')
for (const p of pass) console.log('  [PASS] ' + p)
for (const f of fail) console.log('  [FAIL] ' + f)
console.log(`\n合计 ${pass.length + fail.length} 项，通过 ${pass.length}，失败 ${fail.length}`)
process.exit(fail.length ? 1 : 0)
