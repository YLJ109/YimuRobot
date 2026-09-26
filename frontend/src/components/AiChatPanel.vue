<template>
  <div class="ai-chat-panel">
    <!-- ── 面板标题 ── -->
    <header class="panel-head">
      <span class="head-title">
        <el-icon class="head-icon"><MagicStick /></el-icon>
        AI智能助手
      </span>
      <span class="head-status" :class="{ on: robotStore.connected }">
        <span class="dot"></span>{{ robotStore.connected ? '在线' : '离线' }}
      </span>
    </header>

    <!-- ── 消息区 ── -->
    <div class="chat-screen" ref="messagesRef">
      <!-- 空状态：紧凑一行引导 + 小尺寸示例胶囊 -->
      <div v-if="aiStore.messages.length === 0 && !aiStore.isProcessing" class="chat-empty">
        <div class="empty-line">用一句话描述任务，自动生成并校验 RPL 程序</div>
        <div class="empty-samples">
          <button
            v-for="s in samples"
            :key="s"
            class="sample-chip"
            :disabled="aiStore.isProcessing || !robotStore.connected"
            :title="robotStore.connected ? `直接执行：${s}` : '未连接仿真服务'"
            @click="runSample(s)"
          >{{ s }}</button>
        </div>
      </div>

      <div
        v-for="(msg, idx) in aiStore.messages"
        :key="idx"
        class="chat-bubble"
        :class="msg.role"
      >
        <div class="bubble-avatar">
          <el-icon v-if="msg.role === 'user'"><User /></el-icon>
          <el-icon v-else-if="msg.role === 'assistant'"><ChatDotRound /></el-icon>
          <el-icon v-else><InfoFilled /></el-icon>
        </div>
        <div class="bubble-body">
          <div class="bubble-text">{{ msg.content }}</div>
          <div v-if="msg.program" class="bubble-prog">
            <span class="prog-tag" :class="msg.programValid ? 'tag-ok' : 'tag-warn'">
              <span class="tag-dot"></span>
              {{ msg.programValid ? '程序已生成' : '程序待校验' }}
            </span>
            <pre class="prog-code">{{ msg.program }}</pre>
          </div>
          <div v-if="msg.error" class="bubble-err">{{ msg.error }}</div>
        </div>
      </div>

      <div v-if="aiStore.isProcessing" class="chat-bubble assistant">
        <div class="bubble-avatar"><el-icon><ChatDotRound /></el-icon></div>
        <div class="bubble-body">
          <div class="thinking">
            <span class="led-dot"></span><span class="led-dot"></span><span class="led-dot"></span>
          </div>
        </div>
      </div>
    </div>

    <!-- ── 语音波形 / 识别状态 ── -->
    <div v-if="speech.isRecording.value" class="voice-bar">
      <div class="wave-bars">
        <div class="wave-bar" v-for="i in 16" :key="i" :style="{ height: `${getWaveHeight(i)}px` }"></div>
      </div>
      <span class="wave-label">正在聆听…松开按钮立即识别</span>
    </div>
    <div v-else-if="aiStore.asrState === 'transcribing'" class="voice-bar is-busy">
      <span class="wave-label">正在识别语音…</span>
    </div>

    <div v-if="speech.error.value" class="voice-err">{{ speech.error.value }}</div>
    <div v-else-if="aiStore.asrError" class="voice-err">{{ aiStore.asrError }}</div>

    <!-- ── 输入胶囊：单行容器，按钮内联 ── -->
    <div class="composer">
      <textarea
        class="composer-input"
        v-model="aiStore.inputText"
        rows="1"
        placeholder="输入指令，例如：把红色方块放到B区"
        @keydown.enter.exact.prevent="sendText"
      ></textarea>
      <button
        class="composer-btn"
        :class="{ recording: speech.isRecording.value }"
        @click="toggleRecording"
        :disabled="aiStore.asrState === 'transcribing'"
        :title="speech.isRecording.value ? '停止录音并识别' : '语音输入（说完点一下即识别）'"
      >
        <el-icon v-if="!speech.isRecording.value"><Microphone /></el-icon>
        <el-icon v-else><Mute /></el-icon>
      </button>
      <button
        class="composer-btn is-send"
        @click="sendText"
        :disabled="!aiStore.inputText.trim()"
        title="发送"
      >
        <el-icon><Promotion /></el-icon>
      </button>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { User, ChatDotRound, InfoFilled, Microphone, Mute, Promotion, MagicStick } from '@element-plus/icons-vue'
import { useAiStore } from '../stores/ai.js'
import { useRobotStore } from '../stores/robot.js'
import { useSpeech } from '../composables/useSpeech.js'

const aiStore = useAiStore()
const robotStore = useRobotStore()
const messagesRef = ref(null)
// ⚠️ 不传 socket：useSpeech 是模块级单例，App.vue 已经用真实的 SimSocket 绑定过。
//   这里以前写的是 useSpeech(null) —— socket 是 null，录到的音频一个字节都发
//   不出去，对话面板里的这个麦克风按钮点了完全没有反应。
const speech = useSpeech()

const samples = ['把红色方块放到B区', '回家', '抓取蓝色方块']

// 「思考中」的兜底计时器。
// 正常链路上 ai_reply 会把它清掉；但只要后端没连上 / AI 网关超时 / 回复丢了，
// 没有这个兜底转圈就会一直转下去（用户报的「智能助手一直卷圈」）。
let thinkingTimer = null

function startThinking() {
  aiStore.setProcessing(true)
  clearTimeout(thinkingTimer)
  thinkingTimer = setTimeout(() => {
    if (!aiStore.isProcessing) return
    aiStore.setProcessing(false)
    aiStore.addAiMessage({
      text: '等待 AI 响应超时。请确认后端已启动（127.0.0.1:5000）后再试。',
      error: '请求超时（30s）',
    })
    ElMessage.warning('等待 AI 响应超时，已取消等待')
  }, 30000)
}
function stopThinking() {
  clearTimeout(thinkingTimer)
  thinkingTimer = null
  aiStore.setProcessing(false)
}

function sendText() {
  const text = aiStore.inputText.trim()
  if (!text) return
  if (!robotStore.connected) {
    // 没连后端还发出去，等于把消息丢进黑洞：转圈永远停不下来。
    ElMessage.error('未连接到仿真服务（127.0.0.1:5000），无法使用智能助手')
    return
  }
  aiStore.addUserMessage(text)
  aiStore.inputText = ''
  startThinking()
  window.dispatchEvent(new CustomEvent('sim_nl_input', { detail: text }))
}
/**
 * 示例胶囊：点一下**直接发出执行**。
 * 历史实现只做 `aiStore.inputText = s`（把文本填进输入框就完事），用户还得
 * 再点一次发送按钮才真的跑起来 —— 点下去没反应，看着像按钮坏了。
 */
function runSample(text) {
  if (aiStore.isProcessing) return
  aiStore.inputText = text
  sendText()
}

async function toggleRecording() {
  // toggleRecording 由 composable 提供，内部维护 isRecording，
  // 组件不再自己维护一份（两份状态必然分叉）。
  await speech.toggleRecording()
  if (speech.error.value) ElMessage.warning(speech.error.value)
}
function getWaveHeight(i) { const base = speech.audioLevel.value * 26; return Math.max(3, base + Math.sin(Date.now() / 100 + i) * 7) }

watch(() => aiStore.messages.length, () => { nextTick(() => { if (messagesRef.value) messagesRef.value.scrollTop = messagesRef.value.scrollHeight }) })

onMounted(() => {
  window.addEventListener('ai_reply', handleAiReply)
  window.addEventListener('asr_status', handleAsrStatus)
  window.addEventListener('asr_final', handleAsrFinal)
  window.addEventListener('asr_error', handleAsrError)
})
function handleAiReply(e) {
  const data = e.detail
  aiStore.addAiMessage(data)
  stopThinking()
  if (data.audioUrl) speech.speak(data.audioUrl)
  // 一条消息里可能带回多个即时指令（如「张开吸盘然后停」），按顺序全部派发。
  // 后端已经统一成 camelCase：quickCommands 数组 + quickCommand 首项。
  const cmds = Array.isArray(data.quickCommands) && data.quickCommands.length
    ? data.quickCommands
    : (data.quickCommand ? [data.quickCommand] : [])
  for (const cmd of cmds) {
    window.dispatchEvent(new CustomEvent('sim_command', { detail: cmd }))
  }
}
function handleAsrStatus(e) {
  aiStore.setAsrTranscribing(!!(e.detail && e.detail.state === 'transcribing'))
  // 转写结束时清掉 composable 的"识别中"标志（成功走 asr_final，失败走 asr_error）
  if (!e.detail || e.detail.state !== 'transcribing') speech.setTranscribing(false)
}
function handleAsrFinal(e) {
  speech.setTranscribing(false)
  aiStore.setAsrFinal(e.detail.text)
  if (e.detail.text) { aiStore.addUserMessage(e.detail.text); startThinking() }
}
function handleAsrError(e) {
  speech.setTranscribing(false)
  const msg = (e.detail && e.detail.message) || '语音识别失败'
  aiStore.setAsrError(msg)
  aiStore.addAiMessage({ text: msg, error: '语音识别失败' })
  stopThinking()
}
onUnmounted(() => {
  clearTimeout(thinkingTimer)
  window.removeEventListener('ai_reply', handleAiReply)
  window.removeEventListener('asr_status', handleAsrStatus)
  window.removeEventListener('asr_final', handleAsrFinal)
  window.removeEventListener('asr_error', handleAsrError)
})
</script>

<style scoped>
/* 全部色值/尺寸来自 assets/design-system.css（Linear 深色）。 */

.ai-chat-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--bg-surface);
  font-family: var(--font-sans);
}

/* ── 面板标题 ─────────────────────────────────────────────── */
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 34px;
  padding: 0 var(--pad-xl);
  flex-shrink: 0;
  border-bottom: 1px solid var(--border-subtle);
}
.head-title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-secondary);
  letter-spacing: var(--tracking-tight);
}
.head-icon { color: var(--accent); font-size: 13px; }
.head-status {
  display: inline-flex;
  align-items: center;
  gap: var(--pad-md);
  font-size: var(--font-size-xs);
  color: var(--text-disabled);
}
.head-status .dot {
  width: 5px;
  height: 5px;
  border-radius: var(--radius-full);
  background: var(--text-disabled);
}
.head-status.on { color: var(--success); }
.head-status.on .dot { background: var(--success); box-shadow: var(--glow-success); }

/* ── 消息区 ───────────────────────────────────────────────── */
.chat-screen {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--pad-xl);
}

/* ── 空状态（紧凑）─────────────────────────────────────────── */
.chat-empty {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--pad-xl) 0;
}
.empty-line {
  font-size: var(--font-size-sm);
  color: var(--text-tertiary);
  line-height: 1.5;
}
.empty-samples {
  display: flex;
  flex-wrap: wrap;
  gap: var(--pad-md);
}
.sample-chip {
  padding: var(--pad-md) var(--pad-lg);
  border: 1px solid var(--border);
  border-radius: var(--radius-full);
  background: var(--bg-card);
  color: var(--text-secondary);
  font-size: var(--font-size-xs);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out);
}
.sample-chip:hover {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent);
}

/* ── 气泡 ─────────────────────────────────────────────────── */
.chat-bubble {
  display: flex;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
  align-items: flex-start;
}
.chat-bubble:last-child { margin-bottom: 0; }
.chat-bubble.user { flex-direction: row-reverse; }

.bubble-avatar {
  width: 22px;
  height: 22px;
  border-radius: var(--radius-full);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 12px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-elevated);
  color: var(--text-secondary);
}
/* 三种角色三种底色 —— 旧版 info 分支为「白底 + 白字」，图标完全看不见 */
.chat-bubble.user .bubble-avatar {
  background: var(--accent);
  border-color: transparent;
  color: var(--text-on-accent);
}
.chat-bubble.assistant .bubble-avatar {
  background: var(--bg-elevated);
  border-color: var(--border);
  color: var(--accent);
}
.chat-bubble.info .bubble-avatar {
  background: var(--info-soft);
  border-color: transparent;
  color: var(--info);
}

.bubble-body {
  max-width: 86%;
  padding: var(--pad-md) var(--pad-lg);
  border-radius: var(--radius-md);
  font-size: var(--font-size-sm);
  line-height: 1.55;
}
.chat-bubble.user .bubble-body {
  background: var(--accent);
  color: var(--text-on-accent);
  border-top-right-radius: var(--radius-xs);
}
.chat-bubble.assistant .bubble-body {
  background: var(--bg-card);
  border: 1px solid var(--border-subtle);
  color: var(--text-primary);
  border-top-left-radius: var(--radius-xs);
}
.bubble-text { word-break: break-word; white-space: pre-wrap; }

/* ── 程序代码块 ───────────────────────────────────────────── */
.bubble-prog { margin-top: var(--space-2); }
.prog-tag {
  display: inline-flex;
  align-items: center;
  gap: var(--pad-md);
  padding: var(--pad-xs) var(--pad-lg);
  border-radius: var(--radius-full);
  font-size: var(--font-size-xs);
  font-weight: 600;
  margin-bottom: var(--pad-md);
}
.tag-dot { width: 5px; height: 5px; border-radius: var(--radius-full); background: currentColor; }
.tag-ok { background: var(--success-soft); color: var(--success); }
.tag-warn { background: var(--warning-soft); color: var(--warning); }

.prog-code {
  margin: 0;
  padding: var(--pad-lg);
  max-height: 140px;
  overflow-y: auto;
  background: var(--bg-recessed);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  font-size: var(--font-size-xs);
  font-family: var(--font-mono);
  color: var(--text-secondary);
  white-space: pre-wrap;
  line-height: 1.6;
}

/* ── 错误提示 ─────────────────────────────────────────────── */
/* 旧版写成 `color: var(--danger); background: var(--danger)` —— 红底红字，
   文字完全不可读。正确做法：soft 底 + 亮色字 + 左侧色条。 */
.bubble-err {
  margin-top: var(--space-2);
  padding: var(--pad-md) var(--pad-lg);
  font-size: var(--font-size-xs);
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
  border-left: 2px solid var(--danger);
  color: var(--danger-hover);
  word-break: break-word;
}
.voice-err {
  margin: 0 var(--pad-xl) var(--pad-md);
  padding: var(--pad-md) var(--pad-lg);
  font-size: var(--font-size-xs);
  border-radius: var(--radius-sm);
  background: var(--danger-soft);
  border-left: 2px solid var(--danger);
  color: var(--danger-hover);
}

/* ── 思考动画 ─────────────────────────────────────────────── */
.thinking { display: flex; gap: 5px; padding: var(--pad-xs) var(--pad-sm); align-items: center; }
.led-dot {
  width: 5px; height: 5px;
  border-radius: var(--radius-full);
  background: var(--accent);
  animation: pulse-dot 1.4s var(--ease-out) infinite;
}
.led-dot:nth-child(2) { animation-delay: 0.2s; }
.led-dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes pulse-dot {
  0%, 60%, 100% { opacity: 0.25; transform: scale(0.7); }
  30%           { opacity: 1;    transform: scale(1); }
}

/* ── 语音波形 ─────────────────────────────────────────────── */
.voice-bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--pad-md) var(--pad-lg);
  margin: 0 var(--pad-xl) var(--pad-md);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}
/* 识别中：波形没了（已经松开麦克风），用左侧小进度条表示这一环还在跑 */
.voice-bar.is-busy {
  border-color: var(--border-focus);
  background: var(--accent-soft);
}
.voice-bar.is-busy::before {
  content: '';
  width: 12px; height: 12px;
  border-radius: var(--radius-full);
  border: 2px solid var(--accent);
  border-top-color: transparent;
  animation: asr-spin 0.7s linear infinite;
}
@keyframes asr-spin { to { transform: rotate(360deg); } }
.wave-bars { display: flex; align-items: center; gap: 2px; height: 22px; }
.wave-bar {
  width: 2px;
  min-height: 3px;
  border-radius: var(--radius-full);
  background: var(--accent);
  animation: wave-anim 0.5s infinite alternate var(--ease-out);
}
@keyframes wave-anim { from { transform: scaleY(0.5); } to { transform: scaleY(1); } }
.wave-label { flex: 1; font-size: var(--font-size-xs); color: var(--text-secondary); }

/* ── 输入胶囊 ─────────────────────────────────────────────── */
.composer {
  display: flex;
  align-items: flex-end;
  gap: var(--pad-md);
  margin: 0 var(--pad-xl) var(--pad-xl);
  padding: var(--pad-lg);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--bg-recessed);
  flex-shrink: 0;
  transition: border-color var(--duration-fast) var(--ease-out),
              box-shadow var(--duration-fast) var(--ease-out);
}
.composer:focus-within {
  border-color: var(--accent);
  box-shadow: var(--shadow-glow-accent);
}
.composer-input {
  flex: 1;
  min-width: 0;
  max-height: 72px;
  padding: var(--pad-xs) 0;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-primary);
  font-family: var(--font-sans);
  font-size: var(--font-size-sm);
  line-height: 1.5;
  resize: none;
}
.composer-input::placeholder { color: var(--text-disabled); }

.composer-btn {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-tertiary);
  font-size: 15px;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out);
}
.composer-btn:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
.composer-btn.recording {
  background: var(--danger-soft);
  color: var(--danger);
  animation: mic-blink 1.4s ease-in-out infinite;
}
.composer-btn.is-send {
  background: var(--accent);
  color: var(--text-on-accent);
}
.composer-btn.is-send:hover:not(:disabled) { background: var(--accent-hover); }
.composer-btn:disabled { opacity: 0.32; cursor: not-allowed; }
@keyframes mic-blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
</style>
