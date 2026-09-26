<template>
  <div class="code-editor-panel">
    <!-- ── 标题行 + 工具栏（同一行，右侧对齐）── -->
    <header class="panel-head">
      <span class="head-title">
        <el-icon class="head-icon"><Document /></el-icon>
        DSL 程序
      </span>

      <div class="toolbar">
        <button class="tool-btn is-run" @click="runProgram" :disabled="robotStore.isRunning" title="运行">
          <el-icon><VideoPlay /></el-icon>
        </button>
        <button class="tool-btn is-stop" @click="stopProgram" :disabled="!robotStore.isRunning && !robotStore.isPaused" title="停止">
          <el-icon><SwitchButton /></el-icon>
        </button>
        <span class="toolbar-divider"></span>
        <button class="tool-btn" @click="pauseProgram" :disabled="!robotStore.isRunning" title="暂停">
          <el-icon><VideoPause /></el-icon>
        </button>
        <button class="tool-btn" @click="resumeProgram" :disabled="!robotStore.isPaused" title="继续">
          <el-icon><CaretRight /></el-icon>
        </button>
        <button class="tool-btn" @click="stepProgram" title="单步执行">
          <el-icon><DArrowRight /></el-icon>
        </button>
        <span class="toolbar-divider"></span>
        <button class="tool-btn" @click="toggleLog" :class="{ active: showLog }" title="显示 / 隐藏日志">
          <el-icon><Tickets /></el-icon>
        </button>
        <button class="tool-btn" @click="clearLogs" title="清空日志">
          <el-icon><Delete /></el-icon>
        </button>
      </div>
    </header>

    <div class="editor-container" ref="editorRef"></div>

    <div v-show="showLog" class="log-output" ref="logRef">
      <div v-if="editorStore.logs.length === 0" class="log-empty">暂无日志输出</div>
      <div v-for="log in editorStore.logs" :key="log.timestamp" class="log-line" :class="log.level">
        <span class="log-time">{{ formatTime(log.timestamp) }}</span><span class="log-msg">{{ log.message }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { VideoPlay, VideoPause, CaretRight, SwitchButton, DArrowRight, Delete, Tickets, Document } from '@element-plus/icons-vue'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, highlightActiveLine, Decoration, ViewPlugin } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands'
import { syntaxHighlighting, HighlightStyle, defaultHighlightStyle } from '@codemirror/language'
import { tags as t } from '@lezer/highlight'
import { useEditorStore } from '../stores/editor.js'
import { useRobotStore } from '../stores/robot.js'

const editorStore = useEditorStore()
const robotStore = useRobotStore()
const editorRef = ref(null)
const logRef = ref(null)
const showLog = ref(true)
let editorView = null

const dslKeywords = ['MOVEJ', 'MOVELP', 'PICK', 'PLACE', 'SUCK', 'WAIT', 'SPEED', 'HOME', 'LOOP', 'END', 'ON', 'OFF']
const dslParams = ['J1', 'J2', 'J3', 'J4', 'J5', 'J6', 'X', 'Y', 'Z', 'A', 'B', 'C', 'target']

const RE_COMMENT = /#[^\n]*/g
const RE_NUMBER = /-?\d+\.?\d*/g
const RE_KEYWORDS = dslKeywords.map(kw => ({ re: new RegExp(`\\b${kw}\\b`, 'g'), style: 'color: #A855F7; font-weight: bold;' }))
const RE_PARAMS = dslParams.map(p => ({ re: new RegExp(`\\b${p}\\b`, 'g'), style: 'color: #818CF8;' }))

const dslHighlightPlugin = ViewPlugin.fromClass(
  class {
    constructor(view) { this.decorations = this.buildDecorations(view) }
    update(update) { if (update.docChanged || update.viewportChanged) this.decorations = this.buildDecorations(update.view) }
    buildDecorations(view) {
      const decorations = []
      for (let { from, to } of view.visibleRanges) {
        const text = view.state.doc.sliceString(from, to)
        for (const m of text.matchAll(RE_COMMENT)) decorations.push(Decoration.mark({ attributes: { style: 'color: #71717A; font-style: italic;' } }).range(from + m.index, from + m.index + m[0].length))
        for (const { re, style } of RE_KEYWORDS) { re.lastIndex = 0; for (const m of text.matchAll(re)) decorations.push(Decoration.mark({ attributes: { style } }).range(from + m.index, from + m.index + m[0].length)) }
        for (const { re, style } of RE_PARAMS) { re.lastIndex = 0; for (const m of text.matchAll(re)) decorations.push(Decoration.mark({ attributes: { style } }).range(from + m.index, from + m.index + m[0].length)) }
        for (const m of text.matchAll(RE_NUMBER)) decorations.push(Decoration.mark({ attributes: { style: 'color: #F59E0B;' } }).range(from + m.index, from + m.index + m[0].length))
      }
      return Decoration.set(decorations, true)
    }
  },
  { decorations: v => v.decorations }
)

const currentLinePlugin = ViewPlugin.fromClass(
  class {
    constructor(view) { this.decorations = this.build(view) }
    update(update) { this.decorations = this.build(update.view) }
    build(view) {
      const line = editorStore.currentLine
      if (line <= 0 || line > view.state.doc.lines) return Decoration.none
      const lineInfo = view.state.doc.line(line)
      return Decoration.set([Decoration.line({ attributes: { style: 'background: rgba(99,102,241,0.12); border-left: 2px solid #6366F1;' } }).range(lineInfo.from)])
    }
  },
  { decorations: v => v.decorations }
)

onMounted(() => {
  editorView = new EditorView({
    state: EditorState.create({
      doc: editorStore.code,
      extensions: [
        lineNumbers(), history(), highlightActiveLine(),
        keymap.of([...defaultKeymap, ...historyKeymap]),
        dslHighlightPlugin, currentLinePlugin, EditorView.lineWrapping,
        EditorView.theme({
          '&': { backgroundColor: '#08080a', color: '#EDEDEF', fontSize: '12px', fontFamily: "'JetBrains Mono', 'Fira Code', Consolas, monospace", height: '100%' },
          '.cm-gutters': { backgroundColor: '#08080a', color: '#52525B', border: 'none' },
          '.cm-activeLineGutter': { backgroundColor: 'rgba(99,102,241,0.08)', color: '#A1A1AA' },
          '.cm-activeLine': { backgroundColor: 'rgba(255,255,255,0.03)' },
          '.cm-content': { caretColor: '#6366F1' },
          '.cm-cursor': { borderLeftColor: '#6366F1', borderLeftWidth: '2px' },
          '.cm-selectionBackground': { backgroundColor: 'rgba(99,102,241,0.15)' },
        }),
        EditorView.updateListener.of(update => { if (update.docChanged) editorStore.setCode(update.state.doc.toString()) }),
      ],
    }),
    parent: editorRef.value,
  })
  window.addEventListener('sim_log', onSimLog)
})
onUnmounted(() => {
  if (editorView) editorView.destroy()
  window.removeEventListener('sim_log', onSimLog)
})

// 运行/停止等统一走 App.vue 的 sim_command 通道，
// 由 App.vue 做「未连接则报错并保持 idle」的守卫（避免两处各写一套状态机）。
function runProgram() { window.dispatchEvent(new CustomEvent('sim_command', { detail: 'run' })) }
function stopProgram() { window.dispatchEvent(new CustomEvent('sim_command', { detail: 'stop' })) }
function pauseProgram() { window.dispatchEvent(new CustomEvent('sim_command', { detail: 'pause' })) }
function resumeProgram() { window.dispatchEvent(new CustomEvent('sim_command', { detail: 'resume' })) }
function stepProgram() { window.dispatchEvent(new CustomEvent('sim_command', { detail: 'step' })) }
function clearLogs() { editorStore.clearLogs() }
function toggleLog() { showLog.value = !showLog.value }
function formatTime(ts) { const d = new Date(ts); return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}` }

watch(() => editorStore.logs.length, () => { if (logRef.value) logRef.value.scrollTop = logRef.value.scrollHeight })
watch(() => editorStore.code, (newCode) => { if (editorView && editorView.state.doc.toString() !== newCode) editorView.dispatch({ changes: { from: 0, to: editorView.state.doc.length, insert: newCode } }) })
watch(() => editorStore.currentLine, () => { if (editorView) editorView.dispatch({}) })
const onSimLog = (e) => { editorStore.addLog(e.detail.message, e.detail.level || 'info') }
</script>

<style scoped>
/* 全部色值/尺寸来自 assets/design-system.css */

.code-editor-panel {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--bg-surface);
}

/* ── 标题行（含工具栏）───────────────────────────────────── */
.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
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
  white-space: nowrap;
}
.head-icon { color: var(--accent); font-size: 13px; }

.toolbar { display: flex; align-items: center; gap: 2px; }
.toolbar-divider { width: 1px; height: 14px; background: var(--border); margin: 0 var(--pad-xs); flex-shrink: 0; }

.tool-btn {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-tertiary);
  font-size: 14px;
  cursor: pointer;
  flex-shrink: 0;
  transition: background var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out);
}
.tool-btn:hover:not(:disabled) { background: var(--bg-hover); color: var(--text-primary); }
.tool-btn:active:not(:disabled) { transform: scale(0.94); }
.tool-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.tool-btn.active { color: var(--accent); background: var(--accent-soft); }

.tool-btn.is-run { color: var(--success); }
.tool-btn.is-run:hover:not(:disabled) { background: var(--success-soft); color: var(--success-hover); }
.tool-btn.is-stop { color: var(--danger); }
.tool-btn.is-stop:hover:not(:disabled) { background: var(--danger-soft); color: var(--danger-hover); }

/* ── 编辑器 ───────────────────────────────────────────────── */
.editor-container {
  flex: 1;
  min-height: 90px;
  margin: var(--pad-lg) var(--pad-xl) 0;
  overflow: hidden;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-recessed);
}
.editor-container :deep(.cm-editor) { height: 100%; background: var(--bg-recessed); }
.editor-container :deep(.cm-editor.cm-focused) { outline: none; }
.editor-container :deep(.cm-scroller) { overflow: auto; font-family: var(--font-mono); }
.editor-container :deep(.cm-gutters) { background: var(--bg-recessed); border-right: 1px solid var(--border-subtle); }

/* ── 日志输出 ─────────────────────────────────────────────── */
.log-output {
  height: 116px;
  flex-shrink: 0;
  margin: var(--pad-lg) var(--pad-xl) var(--pad-xl);
  overflow-y: auto;
  padding: var(--pad-md) var(--pad-lg);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-recessed);
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
}
.log-empty {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-disabled);
  font-family: var(--font-sans);
}

.log-line {
  display: flex;
  gap: var(--space-2);
  padding: 2px 0;
  line-height: 1.5;
}
.log-time {
  flex-shrink: 0;
  color: var(--text-disabled);
  font-variant-numeric: tabular-nums;
}
.log-msg { color: var(--text-secondary); word-break: break-word; }

/* 深色底上必须用亮色系，旧版沿用 #ff3b30 在近黑背景对比度不足 */
.log-line.error .log-msg { color: var(--danger-hover); }
.log-line.warning .log-msg { color: var(--warning-hover); }
.log-line.success .log-msg { color: var(--success-hover); }
</style>
