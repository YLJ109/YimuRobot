<template>
  <el-dialog
    :model-value="modelValue"
    title="AI 智能助手 · API Key 配置"
    width="440px"
    append-to-body
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <div class="api-key-body">
      <p class="api-key-tip">
        每个用户在这里填入<strong>自己的</strong>智谱 AI API Key，仅用于你当前会话的 AI 对话，
        互不串号。Key 只保存在<strong>本浏览器</strong>与<strong>当前会话内存</strong>，
        不写入后端磁盘、不与其他用户共享。
      </p>

      <label class="api-key-label">智谱 API Key</label>
      <el-input
        v-model="keyInput"
        type="password"
        show-password
        placeholder="粘贴你的 ZHIPU_API_KEY，例如 sk-..."
        class="api-key-input"
        @keyup.enter="onSave"
      />

      <div class="api-key-status" :class="statusClass">
        <span class="dot"></span>
        <span class="txt">{{ statusText }}</span>
      </div>

      <p class="api-key-note">
        没有 Key？前往智谱开放平台（open.bigmodel.cn）免费申请。后端 .env 若已配置全局 Key，
        则无需填写也可使用（共用演示 Key）。
      </p>
    </div>

    <template #footer>
      <el-button @click="onClear" :disabled="!ai.apiKey">清除</el-button>
      <el-button type="primary" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { useAiStore } from '../stores/ai.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue'])

const ai = useAiStore()
const keyInput = ref(ai.apiKey || '')

// 打开弹窗时同步当前已存的 Key
watch(
  () => props.modelValue,
  (v) => { if (v) keyInput.value = ai.apiKey || '' }
)

const statusText = computed(() => {
  if (ai.llmKeyMessage) return ai.llmKeyMessage
  return ai.llmConfigured ? '已配置，AI 智能助手可用' : '尚未配置'
})
const statusClass = computed(() => {
  if (ai.llmKeyMessage && !ai.llmConfigured) return 'is-error'
  if (ai.llmConfigured) return 'is-ok'
  return 'is-idle'
})

function onSave() {
  ai.applyApiKey(keyInput.value)
}
function onClear() {
  ai.clearApiKey()
  keyInput.value = ''
}
</script>

<style scoped>
.api-key-body { display: flex; flex-direction: column; gap: var(--space-3); }
.api-key-tip {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--text-secondary);
}
.api-key-tip strong { color: var(--text-primary); }
.api-key-label {
  font-size: 13px;
  color: var(--text-secondary);
}
.api-key-input { width: 100%; }
.api-key-status {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 13px;
}
.api-key-status .dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--text-muted);
  flex: none;
}
.api-key-status.is-ok .dot { background: var(--success); box-shadow: var(--glow-success); }
.api-key-status.is-ok .txt { color: var(--success); }
.api-key-status.is-error .dot { background: var(--danger); box-shadow: var(--glow-danger); }
.api-key-status.is-error .txt { color: var(--danger); }
.api-key-status.is-idle .txt { color: var(--text-muted); }
.api-key-note {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--text-muted);
}
</style>
