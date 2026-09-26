// tools/console_probe.mjs —— 抓开发页面的运行时异常、失败请求与页面状态。
//
// 用途：ui_probe 的断言只会告诉你「某项 FAIL」，但当根因是「组件挂载时就抛
// 异常」（页面半白、window.__sim 不存在）时，断言全红也看不出到底哪里炸了。
// 这个探针把 console / exceptionThrown / Log 三类 CDP 事件抓回来并打印堆栈，
// 用来定位只有浏览器里才暴露的问题（例如模块级常量漏定义、HMR 后状态错乱）。
//
// 同时抓 **Network** 域：浏览器控制台里那句
//   "Failed to load resource: the server responded with a status of 404"
// 只说 404，不说是哪个 URL —— 这里把 status / url / initiator 一起打出来。
// 顺带把 `Log.entryAdded` 里的 source=network 条目也收进来。
//
// 用法：node tools/console_probe.mjs      （前端需已在 3000 运行）
import { spawn } from 'node:child_process'
import { rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const profile = join(tmpdir(), 'dbg-console-' + Date.now())
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const chrome = spawn(CHROME, [
  '--headless=new', '--remote-debugging-port=9334', `--user-data-dir=${profile}`,
  '--window-size=1440,900', '--no-first-run', '--disable-gpu', 'http://127.0.0.1:3000/',
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

try {
  let targets
  for (let i = 0; i < 40; i++) {
    try {
      const r = await fetch('http://127.0.0.1:9334/json/list')
      targets = await r.json(); if (targets.find(t => t.type === 'page' && t.url.includes(':3000'))) break
    } catch { /* retry */ }
    await sleep(300)
  }
  const page = targets.find((t) => t.type === 'page' && t.url.includes(':3000'))
  ws = new (globalThis.WebSocket)(page.webSocketDebuggerUrl)
  await new Promise((r) => { ws.onopen = r })
  const logs = []
  const badRequests = []
  const reqUrl = new Map()
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data)
    if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); p.res(m.result); return }
    if (m.method === 'Runtime.consoleAPICalled') {
      logs.push('[console.' + m.params.type + '] ' + m.params.args.map(a => a.value ?? a.description ?? a.type).join(' ').slice(0, 400))
    }
    if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails
      logs.push('[EXCEPTION] ' + (d.exception?.description || d.text || '').slice(0, 800))
    }
    if (m.method === 'Log.entryAdded') {
      const en = m.params.entry
      const line = '[log.' + en.level + '] ' + (en.text || '') + (en.url ? '  <' + en.url + '>' : '')
      logs.push(line.slice(0, 400))
    }
    if (m.method === 'Network.requestWillBeSent') {
      reqUrl.set(m.params.requestId, m.params.request.url)
    }
    if (m.method === 'Network.responseReceived') {
      const { requestId, response, type } = m.params
      if (response.status >= 400) {
        badRequests.push({
          status: response.status,
          type,
          url: response.url,
          from: m.params.frameId ? 'page' : '?',
        })
      }
    }
    if (m.method === 'Network.loadingFailed') {
      badRequests.push({
        status: 'FAILED',
        type: m.params.type || '',
        url: reqUrl.get(m.params.requestId) || '(未知)',
        from: m.params.errorText || '',
      })
    }
  }
  await send('Runtime.enable')
  await send('Log.enable')
  await send('Network.enable')
  await sleep(6000)
  // 主动点一下麦克风，把语音识别那条链路也走一遍（404 常常只在这里出现）
  await send('Runtime.evaluate', {
    expression: `(() => { const b = document.querySelector('.mic-btn, .composer-btn.is-mic'); if (b) { b.click(); return 'clicked:' + (b.className || '') } return 'no-mic-button' })()`,
    returnByValue: true,
  }).then((r) => logs.push('[probe] 麦克风按钮 → ' + r.result?.value)).catch(() => {})
  await sleep(6000)
  const r = await send('Runtime.evaluate', {
    expression: 'JSON.stringify({sim: typeof window.__sim, canvas: !!document.querySelector("canvas"), appHtml: (document.querySelector("#app")||{}).innerHTML ? document.querySelector("#app").innerHTML.length : 0})',
    returnByValue: true,
  })
  console.log('页面状态:', r.result?.value)
  console.log('--- 失败请求(' + badRequests.length + ') ---')
  for (const b of badRequests.slice(0, 30)) {
    console.log(`  [${b.status}] ${b.type || ''} ${b.url}${b.from && b.from !== 'page' ? '  (' + b.from + ')' : ''}`)
  }
  console.log('--- 日志(' + logs.length + ') ---')
  for (const l of logs.slice(0, 40)) console.log(l)
} catch (err) {
  console.error('ERR', err.message)
} finally {
  try { ws?.close() } catch {}
  try { chrome.kill() } catch {}
  await sleep(300)
  try { rmSync(profile, { recursive: true, force: true }) } catch {}
}
