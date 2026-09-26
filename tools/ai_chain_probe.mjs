// tools/ai_chain_probe.mjs
// AI 链路端到端探针：走真实 WebSocket 连后端，验证
//   ① 真的连上了 LLM（不是本地假回复）
//   ② 真的回答（带真实场景数据）
//   ③ 真的生成代码（program 非空且通过校验）
//   ④ 真的执行（robot_frame 里关节实际在动 + program_finished）
//   ⑤ 字段命名统一 camelCase
//
// 用法：node tools/ai_chain_probe.mjs [http://127.0.0.1:5000]

import { io } from '../frontend/node_modules/socket.io-client/build/esm/index.js'

const URL = process.argv[2] || 'http://127.0.0.1:5000'
const RAD2DEG = 180 / Math.PI

// 与前端演示场景一致的三个物体（场景系：Y-up，size[1] 是高度）
const SCENE = [
  { id: 101, type: 'box', name: '红色方块', color: '#f43f5e', position: [-480, 65, 140], size: [130, 130, 130], grabbable: true },
  { id: 102, type: 'box', name: '蓝色方块', color: '#6366f1', position: [480, 65, 140], size: [130, 130, 130], grabbable: true },
  { id: 103, type: 'cylinder', name: '绿色圆柱', color: '#10b981', position: [0, 65, -560], size: [130, 130, 130], grabbable: true },
]

const results = []
function check(name, ok, detail) {
  results.push({ name, ok, detail })
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  —  ${detail}` : ''}`)
}

const socket = io(URL, { transports: ['polling', 'websocket'], timeout: 10000 })

/** 发一条自然语言，等 ai_reply（或超时） */
function ask(text, timeoutMs = 60000) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve({ timeout: true }), timeoutMs)
    socket.once('ai_reply', (data) => { clearTimeout(timer); resolve(data) })
    socket.emit('nl_input', { text })
  })
}

/** 观察执行：等到 program_finished 或到上限为止，统计关节真实摆幅 */
function observeMotion(maxMs = 90000) {
  return new Promise((resolve) => {
    const frames = []
    let finished = null
    const started = Date.now()
    const onFrame = (f) => { if (f.type === 'joints' && f.j) frames.push(f.j) }
    const onFin = (d) => { finished = d; done() }
    let done = () => {
      socket.off('robot_frame', onFrame)
      socket.off('program_finished', onFin)
      let maxSwing = 0
      if (frames.length > 1) {
        const base = frames[0]
        for (const f of frames) {
          for (let i = 0; i < 6; i++) {
            maxSwing = Math.max(maxSwing, Math.abs((f[i] - base[i]) * RAD2DEG))
          }
        }
      }
      resolve({ frameCount: frames.length, maxSwing, finished, waitedMs: Date.now() - started })
    }
    socket.on('robot_frame', onFrame)
    socket.on('program_finished', onFin)
    // 先给一段静默期等程序真正启动，避免 ai_reply 刚回来就判"没动"
    setTimeout(() => { if (!finished) done() }, maxMs)
  })
}

socket.on('connect', async () => {
  console.log(`▶ 已连接 ${URL}\n`)
  socket.emit('scene_update', { objects: SCENE })
  await new Promise((r) => setTimeout(r, 300))

  // ── 用例 1：需要 LLM 规划 + 生成 + 执行的复合指令 ──
  console.log('【用例1】"把红色方块搬到蓝色方块旁边"')
  const t1 = Date.now()
  const r1 = await ask('把红色方块搬到蓝色方块旁边')
  const dt1 = ((Date.now() - t1) / 1000).toFixed(1)
  if (r1.timeout) {
    check('收到 ai_reply（未超时）', false, '超时')
  } else {
    check('收到 ai_reply', true, `${dt1}s`)
    check('回复带真实内容（非空）', !!(r1.text || '').trim(), JSON.stringify((r1.text || '').slice(0, 60)))
    check('生成并校验通过 DSL 程序', r1.programValid === true && !!r1.program,
      r1.program ? r1.program.split('\n')[0] : '(空)')
    check('程序含 PICK/PLACE（真在搬东西）',
      /PICK/.test(r1.program || '') && /PLACE/.test(r1.program || ''))
    check('字段为 camelCase（programValid 存在）', 'programValid' in r1)
    check('字段为 camelCase（audioUrl 存在）', 'audioUrl' in r1 || 'audioUrl' === '' )
    check('不再返回 snake_case 的 program_valid', !('program_valid' in r1))

    // 观察执行：关节是否真的动、程序是否真的跑完
    const motion = await observeMotion(90000)
    check('执行期关节真实摆动', motion.maxSwing > 20,
      `帧数 ${motion.frameCount}，最大单轴摆幅 ${motion.maxSwing.toFixed(1)}°`)
    check('收到 program_finished', !!motion.finished,
      motion.finished
        ? `${motion.finished.success ? 'success' : 'fail'}: ${motion.finished.message}`
        : `等待 ${(motion.waitedMs / 1000).toFixed(0)}s 未收到`)
  }

  // ── 用例 2：纯问答（不应生成程序，但要真实回答） ──
  console.log('\n【用例2】"现在场景里有哪些物体？"')
  const r2 = await ask('现在场景里有哪些物体？')
  if (r2.timeout) check('纯问答收到回复', false, '超时')
  else {
    check('纯问答有真实回答', !!(r2.text || '').trim(), JSON.stringify((r2.text || '').slice(0, 50)))
    check('回答里提到了场景真实物体', /红色方块|蓝色方块|绿色圆柱/.test(r2.text || ''))
    check('纯问答不误生成程序', !r2.program)
  }

  // ── 用例 3：含糊指令 → 澄清，且 needClarify 为 true ──
  console.log('\n【用例3】"抓取那个东西"（含糊）')
  const r3 = await ask('抓取那个东西')
  if (r3.timeout) check('含糊指令收到回复', false, '超时')
  else {
    check('模型反问澄清', r3.needClarify === true, JSON.stringify((r3.text || '').slice(0, 50)))
    check('澄清问题里出现候选物体', /红色方块|蓝色方块|绿色圆柱/.test(r3.text || ''))
  }

  // ── 用例 4：快通道即时指令 + 多指令 ──
  console.log('\n【用例4】"回家"（快通道）')
  const r4 = await ask('回家')
  if (r4.timeout) check('快通道收到回复', false, '超时')
  else {
    check('快通道返回 quickCommand', r4.quickCommand === 'home', `quickCommand=${r4.quickCommand}`)
    check('quickCommands 为数组', Array.isArray(r4.quickCommands), JSON.stringify(r4.quickCommands))
  }

  // ── 用例 5：TTS 音频真的能被取到（HTTP 层）
  //
  //   历史 bug：speech.synthesize_to_file() 返回 /audio/xxx.mp3，但 Flask 里
  //   根本没有这条路由 —— 请求落到 catch-all 静态路由，去找
  //   frontend/dist/audio/xxx.mp3（不存在），最后返回 index.html。前端
  //   <audio> 拿到一段 HTML 就报 "no supported source was found"，看起来
  //   像浏览器自动播放策略问题，其实是文件压根没被服务。
  //   这里从 HTTP 层堵住回归：audioUrl 形如 /audio/*.mp3 且真能取到音频字节。
  console.log('\n【用例5】TTS 音频可被 HTTP 服务')
  const audioUrl = (r4 && r4.audioUrl) || ''
  check('回复带 audioUrl（TTS 已产出）', /^\/audio\/.+\.mp3$/.test(audioUrl), audioUrl || '(空)')
  if (/^\/audio\//.test(audioUrl)) {
    try {
      const res = await fetch(URL + audioUrl)
      const ct = res.headers.get('content-type') || ''
      const buf = await res.arrayBuffer()
      check('音频 URL 返回 200', res.status === 200, `status=${res.status}`)
      check('返回的是音频而不是 HTML（type + 体积）',
        /audio\//.test(ct) && buf.byteLength > 1024,
        `type=${ct} bytes=${buf.byteLength}`)
    } catch (e) {
      check('音频 URL 可请求', false, e.message)
    }
  }

  // ── 汇总 ──
  const failed = results.filter((r) => !r.ok)
  console.log('\n' + '='.repeat(64))
  console.log(`  合计 ${results.length} 项，通过 ${results.length - failed.length}，失败 ${failed.length}`)
  console.log('='.repeat(64))
  socket.disconnect()
  process.exit(failed.length ? 1 : 0)
})

socket.on('connect_error', (e) => {
  console.error('连接失败:', e.message)
  process.exit(2)
})

setTimeout(() => { console.error('总超时'); process.exit(3) }, 150000)
