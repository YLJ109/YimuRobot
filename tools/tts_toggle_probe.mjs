// tools/tts_toggle_probe.mjs —— 语音播报开关 端到端验证
//
// 验证三件事：
//   1. 顶栏出现「语音播报」开关，默认开启（喇叭图标、localStorage 默认 '1'/缺省）。
//   2. 点击开关 → 持久化到 localStorage('huawei_tts_enabled')，图标随状态切换
//      （开=Microphone / 关=Mute，且 .tts-icon.off 类切换）。
//   3. 唯一出口 speak() 受开关拦截：开启时派发 ai_reply(audioUrl) 会构造并播放
//      Audio 元素；关闭时派发同样的事件，speak 静默返回、不构造任何 Audio。
//
// 实现：用 CDP 驱动真实页面（fake media 设备），对 .tts-toggle 真实点击，
// 并在页面里挂 Audio 构造计数器 + 覆盖 play()（避免自动播放策略把测试绕晕）。
//
// 用法：node tools/tts_toggle_probe.mjs   （前端需已在 127.0.0.1:3000 运行）
import { spawn } from 'node:child_process'
import { rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PORT = 9337
const profile = join(tmpdir(), 'dbg-tts-' + Date.now())
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const results = []
const rec = (name, pass, detail) => {
  results.push({ name, pass, detail })
  console.log(`  ${pass ? '\x1b[32mPASS\x1b[0m' : '\x1b[31mFAIL\x1b[0m'}  ${name}${detail ? '  —  ' + detail : ''}`)
}

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

const chrome = spawn(CHROME, [
  '--headless=new', '--remote-debugging-port=' + PORT, `--user-data-dir=${profile}`,
  '--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream',
  '--window-size=1440,900', '--no-first-run', '--disable-gpu', 'http://127.0.0.1:3000/',
], { stdio: 'ignore' })

try {
  let targets
  for (let i = 0; i < 50; i++) {
    try { const r = await fetch('http://127.0.0.1:' + PORT + '/json/list'); targets = await r.json(); if (targets.find(t => t.type === 'page' && t.url.includes(':3000'))) break } catch {}
    await sleep(300)
  }
  const page = targets.find((t) => t.type === 'page' && t.url.includes(':3000'))
  ws = new (globalThis.WebSocket)(page.webSocketDebuggerUrl)
  await new Promise((r) => { ws.onopen = r })
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data)
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.res(m.result) }
  }
  await send('Runtime.enable')
  await sleep(2500)

  // 先清掉可能存在的旧偏好，保证从「默认开启」开始
  await ev(`localStorage.removeItem('huawei_tts_enabled')`)
  await send('Page.reload')
  await sleep(2500)

  // 1. 控件存在 + 默认开启
  const ctrl = await ev(`(() => {
    const t = document.querySelector('.tts-toggle')
    if (!t) return { exists: false }
    const label = (t.querySelector('.tts-label')||{}).textContent || ''
    const iconOff = (t.querySelector('.tts-icon')||{}).className.includes('off')
    return { exists: true, label, iconOff, ls: localStorage.getItem('huawei_tts_enabled') }
  })()`)
  rec('控件存在 (.tts-toggle)', !!ctrl?.exists, JSON.stringify(ctrl))
  rec('标签为「语音播报」', ctrl?.label === '语音播报', ctrl?.label)
  rec('默认开启（图标非 off，且 localStorage 缺省/1）', ctrl && !ctrl.iconOff && (ctrl.ls === null || ctrl.ls === '1'), 'iconOff=' + ctrl?.iconOff + ' ls=' + ctrl?.ls)

  // 覆盖 play() 避免自动播放策略干扰，并挂 Audio 构造计数
  const armCounter = `(() => {
    window.__audioCtor = 0
    const Orig = window.Audio
    window.Audio = function(...a){ window.__audioCtor++; return new Orig(...a) }
    window.HTMLMediaElement.prototype.play = function(){ return Promise.resolve() }
  })()`

  // 2. 开启态：speak 应构造 Audio
  await ev(armCounter)
  await ev(`window.dispatchEvent(new CustomEvent('ai_reply', { detail: { text:'演示', audioUrl:'/audio/_probe_tts_on.mp3' } }))`)
  await sleep(400)
  const onCtor = await ev(`window.__audioCtor`)
  rec('开启态：派发 ai_reply 触发播报（构造 Audio）', onCtor >= 1, 'audioCtor=' + onCtor)

  // 3. 点击关闭 → localStorage=0 + 图标 off
  await ev(`document.querySelector('.tts-toggle .el-switch').click()`)
  await sleep(400)
  const offState = await ev(`(() => ({
    ls: localStorage.getItem('huawei_tts_enabled'),
    iconOff: (document.querySelector('.tts-toggle .tts-icon')||{}).className.includes('off')
  }))()`)
  rec('关闭：localStorage=0', offState?.ls === '0', 'ls=' + offState?.ls)
  rec('关闭：图标切换为静音(off)', !!offState?.iconOff, 'iconOff=' + offState?.iconOff)

  // 4. 关闭态：speak 静默跳过（不构造 Audio）
  await ev(armCounter)
  await ev(`window.dispatchEvent(new CustomEvent('ai_reply', { detail: { text:'演示', audioUrl:'/audio/_probe_tts_off.mp3' } }))`)
  await sleep(400)
  const offCtor = await ev(`window.__audioCtor`)
  rec('关闭态：派发 ai_reply 被拦截（不构造 Audio）', offCtor === 0, 'audioCtor=' + offCtor)

  // 5. 持久化：刷新后仍保持关闭
  await send('Page.reload')
  await sleep(2500)
  const afterReload = await ev(`(() => ({
    ls: localStorage.getItem('huawei_tts_enabled'),
    iconOff: (document.querySelector('.tts-toggle .tts-icon')||{}).className.includes('off')
  }))()`)
  rec('持久化：刷新后仍为关闭', afterReload?.ls === '0' && !!afterReload?.iconOff, 'ls=' + afterReload?.ls + ' iconOff=' + afterReload?.iconOff)

  // 6. 重新打开 → localStorage=1 + 图标恢复
  await ev(`document.querySelector('.tts-toggle .el-switch').click()`)
  await sleep(400)
  const onAgain = await ev(`(() => ({
    ls: localStorage.getItem('huawei_tts_enabled'),
    iconOff: (document.querySelector('.tts-toggle .tts-icon')||{}).className.includes('off')
  }))()`)
  rec('重新打开：localStorage=1', onAgain?.ls === '1', 'ls=' + onAgain?.ls)
  rec('重新打开：图标恢复为喇叭', !onAgain?.iconOff, 'iconOff=' + onAgain?.iconOff)

  const passed = results.filter(r => r.pass).length
  console.log(`\n  === 语音播报开关：${passed}/${results.length} 通过 ===`)
  process.exitCode = passed === results.length ? 0 : 1
} catch (err) {
  console.error('ERR', err.message)
  process.exitCode = 1
} finally {
  try { ws?.close() } catch {}
  try { chrome.kill() } catch {}
  await sleep(300)
  try { rmSync(profile, { recursive: true, force: true }) } catch {}
}
