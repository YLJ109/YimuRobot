// useSpeech.js - 语音采集 composable
// MediaRecorder API → 整段音频经 WebSocket 送后端做 ASR
//
// ⚠️ 这里是**模块级单例**（状态和实例都定义在模块作用域，不在 useSpeech 里）。
//
// 历史坑：App.vue 和 AiChatPanel.vue 各有一个麦克风按钮，各自 `useSpeech(...)`。
//   · AiChatPanel 传的是 `useSpeech(null)` —— socket 是 null，录到的音频
//      一个字节都发不出去，按钮点了完全没反应；
//    · 两个独立的 MediaRecorder 意味着两份麦克风占用、两份权限请求、
//      两份波形状态，点哪个都只亮自己那半边 UI。
//   合成单例之后两个按钮共用同一个采集器，谁点都走同一条链路。
//
// 发送时机：**松手时一次发整段**。MediaRecorder 的 timeslice 分片只有第一片
//   带容器头，逐片喂 whisper 是解不出来的（详见 SimSocket.audioUtterance）。

import { ref, onUnmounted } from 'vue'

// ────────────────── 单例状态 ──────────────────
const isRecording = ref(false)
const hasPermission = ref(false)
const error = ref('')
const isTranscribing = ref(false)
const audioLevel = ref(0) // 0-1 音量电平
const isSpeaking = ref(false)

// 语音播报总开关（前端本地偏好，持久化 localStorage）。默认开启。
// 注意：这是「客户端会话偏好」，不发给后端做全局开关 —— 否则一个用户关掉，
// 会影响所有连着的客户端（违反「客户端会话状态按 sid 隔离」约定）。
const TTS_ENABLED_KEY = 'huawei_tts_enabled'
const ttsEnabled = ref(true)
try {
  const _saved = localStorage.getItem(TTS_ENABLED_KEY)
  if (_saved !== null) ttsEnabled.value = _saved === '1' || _saved === 'true'
} catch (_) {}
function setTtsEnabled(v) {
  ttsEnabled.value = !!v
  try { localStorage.setItem(TTS_ENABLED_KEY, ttsEnabled.value ? '1' : '0') } catch (_) {}
  if (!ttsEnabled.value) stopSpeaking() // 关掉立刻停掉正在播的
}

let socket = null
let mediaRecorder = null
let audioStream = null
let audioContext = null
let analyser = null
let animationFrameId = null
let chunks = []
let autoStopTimer = null
let refCount = 0

// push-to-talk（长按空格）：空格键按下 = 录制，松开 = 发送整段
let pttActive = false      // 空格键当前是否物理按住
let pttOwns = false        // 当前录音是否由空格触发（避免和麦克风按钮互相抢）
let pttWantsCancel = false // 松开早于异步 startRecording 完成：到手即停

/** 单段录音硬上限（秒）。忘了松手不该把麦克风占一整天。 */
const MAX_RECORD_SECONDS = 30

/** 绑定通信套接字。由持有 SimSocket 实例的组件调用（App.vue）。 */
export function bindSpeechSocket(sock) {
  socket = sock
}

// ────────────────── 麦克风权限 ──────────────────
async function requestPermission() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    stream.getTracks().forEach((t) => t.stop()) // 释放测试流
    hasPermission.value = true
    error.value = ''
    return true
  } catch (e) {
    hasPermission.value = false
    if (e.name === 'NotAllowedError') {
      error.value = '麦克风权限被拒绝，请在浏览器地址栏的权限设置里允许麦克风'
    } else if (e.name === 'NotFoundError') {
      error.value = '未找到麦克风设备'
    } else if (e.name === 'NotReadableError') {
      error.value = '麦克风被其它程序占用（会议软件 / 语音助手），请先关掉再试'
    } else {
      error.value = `麦克风错误: ${e.message}`
    }
    return false
  }
}

// ────────────────── 开始录音 ──────────────────
async function startRecording() {
  if (isRecording.value) return

  if (!socket || !socket.isConnected()) {
    error.value = '未连接到仿真服务，语音识别不可用（请确认后端 127.0.0.1:5000 已启动）'
    return
  }

  if (!hasPermission.value) {
    const ok = await requestPermission()
    if (!ok) return
  }

  try {
    error.value = ''
    audioStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        sampleRate: 16000,
        echoCancellation: true,
        noiseSuppression: true,
      },
    })

    // 音量波形
    audioContext = new (window.AudioContext || window.webkitAudioContext)()
    const source = audioContext.createMediaStreamSource(audioStream)
    analyser = audioContext.createAnalyser()
    analyser.fftSize = 256
    source.connect(analyser)
    updateAudioLevel()

    const mime = getSupportedMimeType()
    mediaRecorder = new MediaRecorder(audioStream, mime ? { mimeType: mime } : undefined)
    chunks = []

    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunks.push(e.data)
    }

    // 松手 → 拼成整段 → 一次性发出去
    mediaRecorder.onstop = () => {
      const type = (mediaRecorder && mediaRecorder.mimeType) || mime || 'audio/webm'
      const blob = new Blob(chunks, { type })
      chunks = []
      if (!blob.size) {
        error.value = '没有录到声音，请靠近麦克风重试'
        return
      }
      isTranscribing.value = true
      socket.audioUtterance(blob, type).catch(() => { isTranscribing.value = false })
    }

    mediaRecorder.start(250) // 分片只用于最后拼接，不往上发
    isRecording.value = true

    // push-to-talk：用户松手比 getUserMedia 授权还快时，到手立刻停掉，
    // 否则录音会一直挂着直到 30s 硬上限才结束。
    if (pttWantsCancel) { pttWantsCancel = false; stopRecording(); return }

    clearTimeout(autoStopTimer)
    autoStopTimer = setTimeout(() => {
      if (isRecording.value) {
        error.value = `录音超过 ${MAX_RECORD_SECONDS} 秒，已自动结束`
        stopRecording()
      }
    }, MAX_RECORD_SECONDS * 1000)
  } catch (e) {
    error.value = `录音启动失败: ${e.message}`
    stopRecording()
  }
}

// ────────────────── 停止录音 ──────────────────
function stopRecording() {
  clearTimeout(autoStopTimer)
  autoStopTimer = null
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop()
  }
  if (audioStream) {
    audioStream.getTracks().forEach((t) => t.stop())
    audioStream = null
  }
  if (animationFrameId) {
    cancelAnimationFrame(animationFrameId)
    animationFrameId = null
  }
  if (audioContext) {
    audioContext.close()
    audioContext = null
  }
  analyser = null
  isRecording.value = false
  audioLevel.value = 0
}

// ────────────────── 音量电平 ──────────────────
function updateAudioLevel() {
  if (!analyser) return
  const dataArray = new Uint8Array(analyser.frequencyBinCount)
  analyser.getByteFrequencyData(dataArray)
  let sum = 0
  for (let i = 0; i < dataArray.length; i++) sum += dataArray[i]
  audioLevel.value = sum / dataArray.length / 255
  animationFrameId = requestAnimationFrame(updateAudioLevel)
}

// ────────────────── 工具 ──────────────────
function getSupportedMimeType() {
  const types = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/mp4',
    'audio/wav',
  ]
  for (const type of types) {
    if (typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported(type)) return type
  }
  return ''
}

async function toggleRecording() {
  if (isRecording.value) stopRecording()
  else await startRecording()
}

// ────────────────── 长按空格 push-to-talk ──────────────────
// 全局空格 = 按住录制、松开发送整段。规则：
//   · 用 event.code === 'Space'（与键盘布局无关）
//   · 忽略自动重复（e.repeat）和修饰键（Ctrl/Meta/Alt，保住 Ctrl+空格 等）
//   · 焦点在输入框 / 可编辑区时不抢空格（否则聊天框打不出空格）
//   · 只在空格真正发起录音时才接管「松手停」，不与麦克风按钮互斥
function isEditableTarget(el) {
  if (!el) return false
  const tag = el.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA') return true
  if (el.isContentEditable) return true
  if (el.getAttribute && el.getAttribute('role') === 'textbox') return true
  return false
}

function handlePttKeydown(e) {
  if (e.code !== 'Space') return
  if (e.repeat || e.ctrlKey || e.metaKey || e.altKey) return
  if (isEditableTarget(e.target)) return
  e.preventDefault() // 别让空格把页面滚了
  if (pttActive) return
  pttActive = true
  pttWantsCancel = false
  if (isRecording.value) { pttOwns = false; return } // 麦克风按钮先开的：空格不接管
  pttOwns = true
  startRecording()
}

function handlePttKeyup(e) {
  if (e.code !== 'Space') return
  if (!pttActive) return
  pttActive = false
  if (!pttOwns) return // 不是空格发起的录音，松手不管
  pttOwns = false
  if (isRecording.value) stopRecording()
  else pttWantsCancel = true // 异步 start 还在路上，到手即停
}

let pttKeyDownHandler = null
let pttKeyUpHandler = null
let pttBound = false

export function bindPushToTalk() {
  if (pttBound) return
  pttKeyDownHandler = (e) => handlePttKeydown(e)
  pttKeyUpHandler = (e) => handlePttKeyup(e)
  window.addEventListener('keydown', pttKeyDownHandler)
  window.addEventListener('keyup', pttKeyUpHandler)
  pttBound = true
}

export function unbindPushToTalk() {
  if (!pttBound) return
  window.removeEventListener('keydown', pttKeyDownHandler)
  window.removeEventListener('keyup', pttKeyUpHandler)
  pttBound = false
  pttActive = false
  pttOwns = false
}

// ────────────────── TTS 播放 ──────────────────
let ttsAudio = null

// 被浏览器自动播放策略拦截时，把这条音频挂到「用户下一次交互」上补播，
// 而不是只弹一句"请先点击页面"就把音频丢掉。
let pendingUrl = ''
let resumeHandler = null

function armResumeOnGesture(url) {
  pendingUrl = url
  if (resumeHandler) return
  resumeHandler = () => {
    resumeHandler = null
    const u = pendingUrl
    pendingUrl = ''
    if (u) speak(u)
  }
  window.addEventListener('pointerdown', resumeHandler, { once: true })
}

function speak(audioUrl) {
  if (!ttsEnabled.value) return // 语音播报开关关闭：静默跳过，不浪费播放
  if (!audioUrl) return
  // barge-in：打断当前播报
  if (ttsAudio) {
    ttsAudio.pause()
    ttsAudio = null
  }
  const audio = new Audio(audioUrl)
  ttsAudio = audio
  isSpeaking.value = true

  audio.onended = () => {
    isSpeaking.value = false
    ttsAudio = null
  }

  // ⚠️ 「资源加载失败」与「自动播放被拦截」是两回事，必须分开提示。
  //    onerror  —— src 取不到或不是音频（典型：后端缺 /audio 路由，
  //                请求落到 catch-all 返回了一段 HTML，MediaError 4）。
  //    play().catch(NotAllowedError) —— 浏览器要求先有用户交互。
  audio.onerror = () => {
    const code = audio.error ? audio.error.code : 0
    error.value = code === 4
      ? `语音文件无法播放：${audioUrl} 返回的不是音频（检查后端 /audio 路由与文件是否存在）`
      : `语音加载失败（MediaError ${code}）：${audioUrl}`
    isSpeaking.value = false
    ttsAudio = null
  }

  audio.play().catch((e) => {
    if (e && e.name === 'NotAllowedError') {
      error.value = '语音已就绪，点击页面任意位置即可播放'
      armResumeOnGesture(audioUrl)
    } else {
      error.value = `语音播放失败: ${e.message}`
    }
    isSpeaking.value = false
  })
}

function stopSpeaking() {
  pendingUrl = ''
  if (resumeHandler) {
    window.removeEventListener('pointerdown', resumeHandler)
    resumeHandler = null
  }
  if (ttsAudio) {
    ttsAudio.pause()
    ttsAudio = null
  }
  isSpeaking.value = false
}

/**
 * 把一段文本合成为语音并播放（报警 / 提示用）。
 * 复用后端 /api/tts（与 AI 回复走同一条合成链路），受语音播报开关约束：
 * 开关关掉时静默跳过，不报错。合成/播放失败也静默 —— 报警绝不该因为
 * TTS 异常而打断仿真或把错误喷到控制台。
 * @param {string} text
 */
export async function speakText(text) {
  if (!ttsEnabled.value) return
  if (!text) return
  try {
    const res = await fetch('/api/tts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    })
    if (!res.ok) return
    const data = await res.json()
    if (data && data.audio_url) speak(data.audio_url)
  } catch (_) {
    // 静默：报警语音不可用时不影响仿真继续
  }
}

/** 转写结束（成功或失败）时清掉"识别中"态 */
function setTranscribing(v) {
  isTranscribing.value = !!v
  if (v === false) return
}

export function useSpeech(sock) {
  if (sock) bindSpeechSocket(sock)

  refCount++
  onUnmounted(() => {
    // 引用计数归零才停采集：两个组件（App + 对话面板）共用这一份，
    // 面板卸载不该把 App 的录音一起掐掉。
    refCount = Math.max(0, refCount - 1)
    if (refCount === 0) {
      stopRecording()
      stopSpeaking()
    }
  })

  return {
    isRecording,
    hasPermission,
    error,
    audioLevel,
    isSpeaking,
    isTranscribing,
    ttsEnabled,
    setTtsEnabled,
    setTranscribing,
    requestPermission,
    startRecording,
    stopRecording,
    toggleRecording,
    speak,
    stopSpeaking,
    speakText,
  }
}
