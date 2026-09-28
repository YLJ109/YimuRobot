<template>
  <div class="app-root">
    <!-- ═══════════════ 顶栏 ═══════════════ -->
    <header class="app-header">
      <div class="header-left">
        <div class="brand">
          <span class="brand-mark"></span>
          <span class="brand-name">YimuRobot</span>
          <span class="brand-sub">机器人仿真</span>
        </div>
        <div class="conn-led" :class="{ online: connected }">
          <span class="led-bulb"></span>
          <span class="led-label">{{ connected ? '已连接' : '未连接' }}</span>
        </div>
      </div>

      <div class="header-center">
        <div class="btn-group" role="group" aria-label="文件操作">
          <button class="icon-btn" @click="showSaveScene" title="保存场景">
            <el-icon><FolderOpened /></el-icon>
          </button>
          <button class="icon-btn" @click="showLoadScene" title="加载场景">
            <el-icon><Files /></el-icon>
          </button>
        </div>
        <span class="group-divider"></span>
        <div class="btn-group" role="group" aria-label="运行控制">
          <button class="icon-btn is-run" @click="runProgram" :disabled="robotStore.isRunning" title="运行">
            <el-icon><VideoPlay /></el-icon>
          </button>
          <button class="icon-btn is-pause" @click="pauseProgram" :disabled="!robotStore.isRunning" title="暂停">
            <el-icon><VideoPause /></el-icon>
          </button>
          <button class="icon-btn is-resume" @click="resumeProgram" :disabled="!robotStore.isPaused" title="继续">
            <el-icon><CaretRight /></el-icon>
          </button>
          <button class="icon-btn is-stop" @click="stopProgram" :disabled="!robotStore.isRunning && !robotStore.isPaused" title="停止">
            <el-icon><SwitchButton /></el-icon>
          </button>
          <button class="icon-btn" @click="stepProgram" title="单步">
            <el-icon><DArrowRight /></el-icon>
          </button>
        </div>
      </div>

      <div class="header-right">
        <button
          class="icon-btn settings-btn"
          @click="apiKeyDialogVisible = true"
          title="配置 AI 智能助手的 API Key"
        >
          <el-icon><Setting /></el-icon>
        </button>
        <span v-if="transcribing" class="mic-hint">识别中…</span>
        <div class="tts-toggle" :title="ttsEnabled ? '语音播报：开（AI 回复会朗读）' : '语音播报：关'">
          <el-icon class="tts-icon" :class="{ off: !ttsEnabled }"><Microphone v-if="ttsEnabled" /><Mute v-else /></el-icon>
          <span class="tts-label">语音播报</span>
          <el-switch :model-value="ttsEnabled" @change="setTtsEnabled" size="small" />
        </div>
        <button
          class="mic-btn"
          :class="{ recording: isRecording }"
          @click="toggleMic"
          :title="isRecording ? '停止录音（松手即识别）' : '语音输入（或长按空格键说话）'"
        >
          <el-icon v-if="!isRecording"><Microphone /></el-icon>
          <el-icon v-else><Mute /></el-icon>
        </button>
        <kbd v-if="!isRecording" class="mic-kbd" title="长按空格键说话，松开自动发送">空格</kbd>
      </div>
    </header>

    <!-- ═══════════════ 主体三栏 ═══════════════ -->
    <main class="app-main">
      <aside class="sidebar-left">
        <ManualPanel />
      </aside>

      <section class="viewport-area">
        <RobotViewport ref="viewportRef" />
      </section>

      <aside class="sidebar-right">
        <div class="panel panel-ai">
          <AiChatPanel />
        </div>
        <div class="panel panel-code">
          <CodeEditor />
        </div>
      </aside>
    </main>

    <!-- ═══════════════ 底部状态栏 ═══════════════ -->
    <footer class="app-footer">
      <div class="status-cell">
        <span class="cell-key">TCP</span>
        <span class="cell-val">{{ robotStore.tcp.x.toFixed(1) }} · {{ robotStore.tcp.y.toFixed(1) }} · {{ robotStore.tcp.z.toFixed(1) }}</span>
        <span class="cell-unit">mm</span>
      </div>
      <span class="status-sep"></span>
      <div class="status-cell">
        <span class="cell-key">状态</span>
        <span class="led" :class="`led-${stateTagType}`">
          <span class="led-bulb"></span>
          <span class="led-label">{{ stateText }}</span>
        </span>
      </div>
      <span class="status-sep"></span>
      <div class="status-cell">
        <span class="cell-key">帧率</span>
        <span class="cell-val">{{ robotStore.fps }}</span>
      </div>
      <span class="status-sep"></span>
      <div class="status-cell">
        <span class="cell-key">持物</span>
        <span class="cell-val">{{ robotStore.holding || '—' }}</span>
      </div>
      <span class="status-sep"></span>
      <div class="status-cell">
        <span class="cell-key">吸盘</span>
        <span class="led" :class="robotStore.suckOn ? 'led-success' : 'led-idle'">
          <span class="led-bulb"></span>
          <span class="led-label">{{ robotStore.suckOn ? '已吸取' : '已释放' }}</span>
        </span>
      </div>
    </footer>

    <!-- ═══════════════ 对话框 ═══════════════ -->
    <el-dialog v-model="saveDialogVisible" title="保存场景" width="400px">
      <el-input v-model="sceneNameInput" placeholder="场景名称" />
      <template #footer>
        <el-button @click="saveDialogVisible = false">取消</el-button>
        <el-button type="primary" @click="saveScene">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="loadDialogVisible" title="加载场景" width="520px">
      <el-table :data="savedScenes" size="small" @row-click="loadScene" highlight-current-row>
        <el-table-column prop="name" label="名称" />
        <el-table-column prop="created_at" label="创建时间" width="180" />
      </el-table>
      <template #footer>
        <el-button @click="loadDialogVisible = false">关闭</el-button>
      </template>
    </el-dialog>

    <ApiKeyDialog v-model="apiKeyDialogVisible" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { FolderOpened, Files, VideoPlay, VideoPause, CaretRight, SwitchButton, DArrowRight, Microphone, Mute, Setting } from '@element-plus/icons-vue'

import RobotViewport from './components/RobotViewport.vue'
import ManualPanel from './components/ManualPanel.vue'
import AiChatPanel from './components/AiChatPanel.vue'
import CodeEditor from './components/CodeEditor.vue'
import ApiKeyDialog from './components/ApiKeyDialog.vue'

import { SimSocket } from './classes/SimSocket.js'
import { useRobotStore } from './stores/robot.js'
import { useSceneStore } from './stores/scene.js'
import { useAiStore } from './stores/ai.js'
import { useEditorStore } from './stores/editor.js'
import { useSpeech, bindPushToTalk, unbindPushToTalk } from './composables/useSpeech.js'
import { HOME_JOINTS_DEG } from './constants/robot.js'

const robotStore = useRobotStore()
const sceneStore = useSceneStore()
const aiStore = useAiStore()
const editorStore = useEditorStore()

const viewportRef = ref(null)
const connected = ref(false)
const transcribing = ref(false)

const simSocket = new SimSocket()
// useSpeech 现在是**模块级单例**：这里传一次 socket，AiChatPanel 那边再调用
// useSpeech() 拿到的是同一份状态和同一个 MediaRecorder。
// 历史坑：AiChatPanel 用的是 useSpeech(null)，录到的音频一个字节都发不出去。
const speech = useSpeech(simSocket)
const isRecording = speech.isRecording
const ttsEnabled = speech.ttsEnabled
const setTtsEnabled = speech.setTtsEnabled

const saveDialogVisible = ref(false)
const loadDialogVisible = ref(false)
const sceneNameInput = ref('')
const savedScenes = ref([])
const apiKeyDialogVisible = ref(false)

const stateText = computed(() => {
  const map = { idle: '空闲', running: '运行中', paused: '已暂停', stopped: '已停止', finished: '已完成', error: '错误' }
  return map[robotStore.execState] || robotStore.execState
})
const stateTagType = computed(() => {
  const map = { idle: 'idle', running: 'success', paused: 'warning', stopped: 'danger', finished: 'success', error: 'danger' }
  return map[robotStore.execState] || 'idle'
})

function setupSocketEvents() {
  simSocket.on('connect', () => {
    connected.value = true
    robotStore.setConnected(true)
    ElMessage.success('已连接到仿真服务')
    // 让视口补推一次场景快照：后端执行器是进程级的，重启后它的场景是空的，
    // 而前端只在「场景变更时」才推。少了这一下，「重启后端 → 直接跑 PICK」
    // 就会因为执行器认不出物体而算错接触高度（吸盘扎进物体）。
    window.dispatchEvent(new CustomEvent('sim_scene_flush'))
    // 重连后把本浏览器存的 Key 重新发给后端，保持该会话用自己的 Key
    aiStore.resendKey()
  })
  simSocket.on('llm_key_status', (data) => { aiStore.setLlmKeyStatus(data) })
  simSocket.on('disconnect', () => { connected.value = false; robotStore.setConnected(false); ElMessage.warning('仿真服务连接断开') })
  simSocket.on('robot_frame', (data) => { window.dispatchEvent(new CustomEvent('robot_frame', { detail: data })) })
  simSocket.on('program_finished', (data) => {
    window.dispatchEvent(new CustomEvent('program_finished', { detail: data }))
    robotStore.setExecState(data.success ? 'finished' : 'stopped')
    editorStore.addLog(data.message, data.success ? 'info' : 'error')
    if (data.errors) editorStore.setValidationErrors(data.errors)
  })
  simSocket.on('ai_reply', (data) => {
    window.dispatchEvent(new CustomEvent('ai_reply', { detail: data }))
    // 后端出口统一 camelCase（见 backend/app.py 的 on_nl_input）
    if (data.program && data.programValid) editorStore.setCode(data.program)
  })
  // 语音链路：识别中 / 识别完成 / 识别失败，三类都要往前端派发。
  // 以前只有 asr_final，识别失败时后端什么都不发 —— 界面只能一直等，
  // 用户看到的就是「语音转文字没反应」。
  simSocket.on('asr_status', (data) => {
    transcribing.value = data && data.state === 'transcribing'
    window.dispatchEvent(new CustomEvent('asr_status', { detail: data }))
  })
  simSocket.on('asr_final', (data) => {
    transcribing.value = false
    window.dispatchEvent(new CustomEvent('asr_final', { detail: data }))
  })
  simSocket.on('asr_error', (data) => {
    transcribing.value = false
    window.dispatchEvent(new CustomEvent('asr_error', { detail: data }))
  })
  simSocket.on('scene_objects', (data) => { window.dispatchEvent(new CustomEvent('scene_objects', { detail: data })) })
  // ⚠️ 只派发事件，不在这里落账。日志的唯一写入点是 CodeEditor 的 onSimLog
  //    （订阅 sim_log）。此前这里也调 addLog，导致每条日志被写两遍。
  simSocket.on('log', (data) => { window.dispatchEvent(new CustomEvent('sim_log', { detail: data })) })
}

function setupGlobalListeners() {
  window.addEventListener('sim_run_program', (e) => { simSocket.runProgram(e.detail) })
  window.addEventListener('sim_command', (e) => {
    const cmd = e.detail
    if (cmd === 'run') runProgram()
    else if (cmd === 'stop') stopProgram()
    else if (cmd === 'pause') pauseProgram()
    else if (cmd === 'resume') resumeProgram()
    else if (cmd === 'step') stepProgram()
    else if (cmd === 'home') {
      // 回零位是「姿态指令」不是「程序」：旧实现把它当程序发给后端（runProgram('HOME')），
      // 校验器一看到 'HOME' 不是合法指令就直接判失败，用户看到一条莫名其妙的报错。
      robotStore.setAllJoints([...HOME_JOINTS_DEG])
      simSocket.setJoints(HOME_JOINTS_DEG)
    }
    else if (cmd === 'suck_on') { robotStore.setSuck(true); simSocket.setSuck(true) }
    else if (cmd === 'suck_off') { robotStore.setSuck(false); simSocket.setSuck(false) }
  })
  window.addEventListener('sim_set_joint', (e) => { simSocket.setJoint(e.detail.index, e.detail.degree) })
  window.addEventListener('sim_set_joints', (e) => { simSocket.setJoints(e.detail.joints) })
  window.addEventListener('sim_suck', (e) => { simSocket.setSuck(!!e.detail.on) })
  window.addEventListener('sim_nl_input', (e) => { simSocket.nlInput(e.detail) })
  // 用户在设置弹窗里填的 Key：前端不直连后端，统一走 CustomEvent → 这里转发到 socket
  window.addEventListener('sim_set_llm_key', (e) => { simSocket.emit('set_llm_key', e.detail) })
  window.addEventListener('sim_scene_update', (e) => { simSocket.sceneUpdate(e.detail.objects) })
}

/**
 * 运行程序。
 * 关键：未连接时**必须明确报错并保持 idle**。旧实现无论连不连得上都把状态
 * 置成 running，界面显示「运行中」而机器人纹丝不动，看上去就像程序坏了。
 */
function runProgram() {
  if (!simSocket.isConnected()) {
    ElMessage.error('未连接到仿真服务（127.0.0.1:5000），请先启动后端再运行')
    robotStore.setExecState('idle')
    return
  }
  window.dispatchEvent(new CustomEvent('sim_run_program', { detail: editorStore.code }))
  robotStore.setExecState('running')
}
function stopProgram() { simSocket.stop(); robotStore.setExecState('stopped') }
function pauseProgram() { simSocket.pause(); robotStore.setExecState('paused') }
function resumeProgram() { simSocket.resume(); robotStore.setExecState('running') }
function stepProgram() {
  if (!simSocket.isConnected()) { ElMessage.error('未连接到仿真服务，无法单步执行'); return }
  simSocket.step()
}

/**
 * 麦克风开关。
 *   isRecording 现在是共享状态（`speech.isRecording`），不能再手动赋值 ——
 *   手动赋值会和 composable 内部状态分叉，出现"按钮显示在录、实际没录"。
 *   startRecording 内部已经把「未连接服务 / 权限被拒 / 设备被占用」的错误
 *   文本写进 speech.error，这里只负责把它弹出来。
 */
async function toggleMic() {
  if (isRecording.value) {
    speech.stopRecording()
    return
  }
  const ok = await speech.requestPermission()
  if (!ok) {
    ElMessage.warning(speech.error.value || '麦克风权限被拒绝')
    return
  }
  await speech.startRecording()
  if (!isRecording.value && speech.error.value) {
    ElMessage.error(speech.error.value)
  }
}

function showSaveScene() { sceneNameInput.value = sceneStore.sceneName; saveDialogVisible.value = true }
async function saveScene() {
  try {
    const r = await fetch('/api/scenes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: sceneNameInput.value, objects: sceneStore.getSnapshot() }) })
    if (r.ok) { ElMessage.success('场景已保存'); saveDialogVisible.value = false }
  } catch (e) { ElMessage.error('保存失败: ' + e.message) }
}
async function showLoadScene() {
  try { const r = await fetch('/api/scenes'); savedScenes.value = await r.json(); loadDialogVisible.value = true }
  catch (e) { ElMessage.error('加载场景列表失败') }
}
function loadScene(row) { sceneStore.setObjects(row.data); simSocket.sceneUpdate(row.data); loadDialogVisible.value = false; ElMessage.success(`已加载: ${row.name}`) }

onMounted(() => { simSocket.connect(); setupSocketEvents(); setupGlobalListeners(); bindPushToTalk() })
onUnmounted(() => { simSocket.disconnect(); unbindPushToTalk() })
</script>

<style scoped>
/* ════════════════════════════════════════════════════════════
   App 外壳 — Linear Aesthetic 深色
   全部色值/尺寸来自 assets/design-system.css，本文件零硬编码色。
   ════════════════════════════════════════════════════════════ */

.app-root {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: var(--bg-canvas);
  font-family: var(--font-sans);
  color: var(--text-primary);
}

/* ── 顶栏：玻璃拟态 ──────────────────────────────────────── */
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  height: var(--header-height);
  padding: 0 var(--pad-xl);
  flex-shrink: 0;
  background: var(--glass-bg-strong);
  backdrop-filter: blur(var(--glass-blur));
  -webkit-backdrop-filter: blur(var(--glass-blur));
  border-bottom: 1px solid var(--border-subtle);
  position: relative;
  z-index: var(--z-overlay);
}

.header-left,
.header-center,
.header-right { display: flex; align-items: center; gap: var(--space-3); }

/* ── 品牌 ─────────────────────────────────────────────────── */
.brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding-right: var(--pad-xl);
}
.brand-mark {
  width: 4px;
  height: 18px;
  border-radius: var(--radius-full);
  background: linear-gradient(180deg, var(--accent), var(--info));
  box-shadow: var(--glow-accent);
}
.brand-name {
  font-family: var(--font-mono);
  font-size: var(--font-size-base);
  font-weight: 600;
  letter-spacing: var(--tracking-wide);
  color: var(--text-primary);
}
.brand-sub {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  padding-left: var(--pad-lg);
  border-left: 1px solid var(--border);
}

/* ── 连接状态 ─────────────────────────────────────────────── */
.conn-led {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--pad-md) var(--pad-xl);
  border-radius: var(--radius-full);
  border: 1px solid var(--border-subtle);
  background: var(--bg-card);
}
.led-bulb {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--danger);
  flex-shrink: 0;
  transition: background var(--duration-normal) var(--ease-out);
}
.conn-led.online .led-bulb {
  background: var(--success);
  box-shadow: var(--glow-success);
}
.led-label {
  font-size: var(--font-size-xs);
  font-weight: 500;
  color: var(--text-secondary);
  white-space: nowrap;
}

/* ── 按钮组 ───────────────────────────────────────────────── */
.btn-group {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: var(--pad-xs);
  border-radius: var(--radius-md);
  border: 1px solid var(--border-subtle);
  background: var(--bg-card);
}
.group-divider {
  width: 1px;
  height: 20px;
  background: var(--border);
}

.icon-btn {
  width: 30px;
  height: 30px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  color: var(--text-tertiary);
  font-size: 15px;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out);
}
.icon-btn:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
.icon-btn:active:not(:disabled) { transform: scale(0.94); }
.icon-btn:disabled { opacity: 0.3; cursor: not-allowed; }

/* 语义色 —— 图标区分度：运行实心三角 / 暂停双竖 / 继续单箭头 / 停止方块+圆 */
.icon-btn.is-run { color: var(--success); }
.icon-btn.is-run:hover:not(:disabled) { background: var(--success-soft); }
.icon-btn.is-pause { color: var(--warning); }
.icon-btn.is-pause:hover:not(:disabled) { background: var(--warning-soft); }
.icon-btn.is-resume { color: var(--accent); }
.icon-btn.is-resume:hover:not(:disabled) { background: var(--accent-soft); }
.icon-btn.is-stop { color: var(--danger); }
.icon-btn.is-stop:hover:not(:disabled) { background: var(--danger-soft); }

/* ── 麦克风 ───────────────────────────────────────────────── */
.mic-hint {
  font-size: 12px;
  color: var(--accent);
  letter-spacing: 0.02em;
  margin-right: var(--space-2);
  animation: hint-fade 1.4s ease-in-out infinite;
}
@keyframes hint-fade { 0%, 100% { opacity: 1; } 50% { opacity: 0.45; } }

.mic-btn {
  width: 34px;
  height: 34px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-full);
  border: 1px solid var(--border);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: 16px;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out),
              box-shadow var(--duration-fast) var(--ease-out);
}
.mic-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
.mic-btn.recording {
  background: var(--danger);
  border-color: transparent;
  color: var(--text-on-accent);
  box-shadow: var(--glow-danger);
  animation: mic-pulse 1.6s ease-in-out infinite;
}
.mic-kbd {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 30px;
  height: 20px;
  padding: 0 var(--pad-md);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: var(--bg-card);
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.04em;
  color: var(--text-tertiary);
  cursor: default;
  user-select: none;
}
/* ── 语音播报开关 ─────────────────────────────────────────── */
.tts-toggle {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--pad-xs) var(--pad-md);
  border-radius: var(--radius-full);
  border: 1px solid var(--border-subtle);
  background: var(--bg-card);
}
.tts-icon { font-size: 15px; color: var(--text-secondary); transition: color var(--duration-fast) var(--ease-out); }
.tts-icon.off { color: var(--text-disabled); }
.tts-label {
  font-size: var(--font-size-xs);
  font-weight: 500;
  color: var(--text-tertiary);
  white-space: nowrap;
  user-select: none;
}

.mic-kbd:hover { color: var(--text-secondary); border-color: var(--border-focus); }
@keyframes mic-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(244, 63, 94, 0.4); }
  50%      { box-shadow: 0 0 0 6px rgba(244, 63, 94, 0); }
}

/* ═══ 主体三栏 ═══ */
.app-main {
  display: flex;
  flex: 1;
  min-height: 0;
  padding: var(--pad-xl);
  gap: var(--gap-shell);
  background: var(--bg-canvas);
}

.sidebar-left {
  width: var(--sidebar-left-width);
  flex-shrink: 0;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-subtle);
  background: var(--bg-surface);
  overflow: hidden;
}

.viewport-area {
  flex: 1;
  min-width: 0;
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-subtle);
  background: var(--bg-surface);
}

.sidebar-right {
  width: var(--sidebar-right-width);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border-radius: var(--radius-lg);
  border: 1px solid var(--border-subtle);
  background: var(--bg-surface);
  overflow: hidden;
}
/* 两块功能区共处一张卡片，用一条分隔线代替两个独立边框 —— 视觉更轻、层级更清 */
.panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}
.panel-ai { flex: 4; }
.panel-code {
  flex: 6;
  border-top: 1px solid var(--border-subtle);
}

/* ═══ 底部状态栏 ═══ */
.app-footer {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  height: var(--footer-height);
  padding: 0 var(--pad-xl);
  flex-shrink: 0;
  background: var(--bg-surface);
  border-top: 1px solid var(--border-subtle);
}
.status-cell { display: flex; align-items: center; gap: var(--space-2); }
.cell-key {
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--text-tertiary);
  letter-spacing: var(--tracking-wide);
  white-space: nowrap;
}
.cell-val {
  font-family: var(--font-mono);
  font-size: var(--font-size-sm);
  font-weight: 600;
  color: var(--text-primary);
  font-variant-numeric: tabular-nums;
}
.cell-unit { font-family: var(--font-mono); font-size: var(--font-size-xs); color: var(--text-tertiary); }
.status-sep { width: 1px; height: 14px; background: var(--border); flex-shrink: 0; }

/* ── 状态指示灯 ───────────────────────────────────────────── */
.led {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--pad-xs) var(--pad-lg);
  border-radius: var(--radius-full);
  border: 1px solid var(--border-subtle);
  background: var(--bg-card);
}
.led .led-bulb { width: 5px; height: 5px; }
.led .led-label { font-size: var(--font-size-xs); font-weight: 500; color: var(--text-secondary); white-space: nowrap; }

.led-idle .led-bulb { background: var(--text-disabled); }
.led-info .led-bulb { background: var(--info); }
.led-success .led-bulb { background: var(--success); box-shadow: var(--glow-success); }
.led-warning .led-bulb { background: var(--warning); }
.led-danger .led-bulb { background: var(--danger); box-shadow: var(--glow-danger); }

/* ═══ 响应式 ═══
   桌面 >1400：三栏完整
   平板 1024~1400：右栏收窄
   窄屏 <1024：隐藏左右栏，仅留 3D 视口（演示主画面优先） */
@media (max-width: 1400px) {
  :root { --sidebar-right-width: 380px; --sidebar-left-width: 264px; }
}
@media (max-width: 1024px) {
  .sidebar-right { display: none; }
  .header-center { display: none; }
}
@media (max-width: 768px) {
  .sidebar-left { display: none; }
  .brand-sub { display: none; }
}
</style>
