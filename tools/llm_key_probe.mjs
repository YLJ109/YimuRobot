// tools/llm_key_probe.mjs —— 验证「每用户页面配置 API Key」链路（set_llm_key 事件）。
// 前端在设置弹窗填 Key → 经 sim_set_llm_key → 后端 set_llm_key → 回 llm_key_status。
// 这里直接用 socket.io 客户端走同一通道，确认后端按会话存 Key、清 Key 都正确。
//
// 用法：node tools/llm_key_probe.mjs  （后端 :5000 需已运行）
import { io } from '../frontend/node_modules/socket.io-client/build/esm/index.js'

const URL = 'http://127.0.0.1:5000'
const results = {}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function once(sock, event, timeoutMs = 8000) {
  return new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('超时等待 ' + event)), timeoutMs)
    sock.once(event, (d) => { clearTimeout(t); res(d) })
  })
}

try {
  const sock = io(URL, { transports: ['websocket'], reconnection: false })
  await once(sock, 'connect')

  // 1) 设置自己的 Key（非空即视为已配置，ZhipuAI 构造不校验，调用时才鉴权）
  const p1 = once(sock, 'llm_key_status')
  sock.emit('set_llm_key', { key: 'sk-probe-dummy-key' })
  const s1 = await p1
  results.setReturnsConfigured = s1 && s1.configured === true

  // 2) 清 Key（空串）→ 该会话 configured=false
  const p2 = once(sock, 'llm_key_status')
  sock.emit('set_llm_key', { key: '' })
  const s2 = await p2
  results.clearReturnsConfigured = s2 && s2.configured === false

  sock.close()
  await sleep(200)
} catch (err) {
  results.error = err.message
}

const pass = []
const fail = []
const check = (n, c) => (c ? pass : fail).push(n)
check('set_llm_key 非空 → llm_key_status.configured=true', results.setReturnsConfigured === true)
check('set_llm_key 空串 → llm_key_status.configured=false（清除）', results.clearReturnsConfigured === true)

console.log('结果明细:', JSON.stringify(results))
for (const p of pass) console.log('  [PASS] ' + p)
for (const f of fail) console.log('  [FAIL] ' + f)
console.log(`\n合计 ${pass.length + fail.length} 项，通过 ${pass.length}，失败 ${fail.length}`)
process.exit(fail.length ? 1 : 0)
