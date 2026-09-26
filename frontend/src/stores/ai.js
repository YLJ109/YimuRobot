// stores/ai.js - AI 对话状态管理
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useAiStore = defineStore('ai', () => {
  // 对话消息列表
  const messages = ref([])
  // 输入框文本
  const inputText = ref('')
  // 是否正在处理
  const isProcessing = ref(false)
  // ASR 转写文本
  const asrPartialText = ref('')
  const asrFinalText = ref('')
  // ASR 链路状态：'idle' | 'transcribing' | 'error'
  const asrState = ref('idle')
  const asrError = ref('')
  // AI 生成的程序
  const generatedProgram = ref('')
  const programExplanation = ref('')
  // 是否需要澄清
  const needClarify = ref(false)
  const clarifyQuestion = ref('')

  // ── 每用户 API Key（前端页面配置，按会话隔离）──
  // Key 只存在「本浏览器 localStorage」+「当前会话内存」，不落后端磁盘、不与他人共享。
  const LLM_KEY_STORAGE = 'huawei_llm_key'
  const apiKey = ref('')
  const llmConfigured = ref(false)
  const llmKeyMessage = ref('')
  try {
    apiKey.value = localStorage.getItem(LLM_KEY_STORAGE) || ''
  } catch { /* localStorage 不可用时忽略 */ }
  if (apiKey.value) llmConfigured.value = true

  // 添加用户消息
  function addUserMessage(text) {
    messages.value.push({
      role: 'user',
      content: text,
      timestamp: Date.now(),
    })
  }

  // 添加 AI 消息
  function addAiMessage(data) {
    messages.value.push({
      role: 'assistant',
      content: data.text || data.explanation || '',
      explanation: data.explanation || '',
      program: data.program || '',
      programValid: data.programValid || false,
      audioUrl: data.audioUrl || '',
      needClarify: data.needClarify || false,
      error: data.error || '',
      timestamp: Date.now(),
    })
    if (data.program) generatedProgram.value = data.program
    if (data.explanation) programExplanation.value = data.explanation
    if (data.needClarify) {
      needClarify.value = true
      clarifyQuestion.value = data.text || ''
    } else {
      needClarify.value = false
    }
  }

  // 添加系统消息
  function addSystemMessage(text) {
    messages.value.push({
      role: 'system',
      content: text,
      timestamp: Date.now(),
    })
  }

  // 清空对话
  function clearMessages() {
    messages.value = []
    generatedProgram.value = ''
    programExplanation.value = ''
    needClarify.value = false
  }

  // 设置处理状态
  function setProcessing(processing) {
    isProcessing.value = processing
  }

  // ASR 更新
  function setAsrPartial(text) {
    asrPartialText.value = text
  }

  function setAsrFinal(text) {
    asrFinalText.value = text
    asrPartialText.value = ''
    asrState.value = 'idle'
    asrError.value = ''
  }

  /**
   * 识别失败。
   * 以前这条路径完全不存在：后端识别不出来就什么都不发，界面上麦克风转完圈
   * 一切照旧，用户只能得出「语音转文字不行」的结论，也拿不到任何线索。
   */
  function setAsrError(message) {
    asrState.value = 'error'
    asrError.value = message || '语音识别失败'
    asrPartialText.value = ''
  }

  function setAsrTranscribing(on) {
    asrState.value = on ? 'transcribing' : 'idle'
    if (on) asrError.value = ''
  }

  // ── API Key 配置相关 ──
  function _sendKey(key) {
    // 复用既有的「组件发 CustomEvent、App.vue 转发到 socket」通道
    window.dispatchEvent(new CustomEvent('sim_set_llm_key', { detail: { key } }))
  }

  function applyApiKey(key) {
    const k = (key || '').trim()
    apiKey.value = k
    try {
      if (k) localStorage.setItem(LLM_KEY_STORAGE, k)
      else localStorage.removeItem(LLM_KEY_STORAGE)
    } catch { /* 忽略 */ }
    _sendKey(k)
  }

  function clearApiKey() {
    applyApiKey('')
  }

  /** 后端回包（llm_key_status）写入状态 */
  function setLlmKeyStatus(data) {
    llmConfigured.value = !!(data && data.configured)
    llmKeyMessage.value = (data && data.message) || ''
  }

  /** 重连后把本浏览器存的 Key 重新发给后端（保持该会话用自己的 Key） */
  function resendKey() {
    if (apiKey.value) _sendKey(apiKey.value)
  }

  return {
    messages, inputText, isProcessing,
    asrPartialText, asrFinalText, asrState, asrError,
    generatedProgram, programExplanation,
    needClarify, clarifyQuestion,
    apiKey, llmConfigured, llmKeyMessage,
    addUserMessage, addAiMessage, addSystemMessage,
    clearMessages, setProcessing,
    setAsrPartial, setAsrFinal, setAsrError, setAsrTranscribing,
    applyApiKey, clearApiKey, setLlmKeyStatus, resendKey,
  }
})