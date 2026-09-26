// stores/editor.js - 代码编辑器状态管理
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useEditorStore = defineStore('editor', () => {
  // DSL 代码
  // 示例程序：抓取 → 搬运 → 放置，全程在官方工作空间内
  // （两个 MOVEJ 位姿 TCP 半径均为 521.5mm < 官方最大 593mm；
  //   旧示例用 J2=-60,J3=60，在修正关节转向后半径 629.5mm 已越界）
  const code = ref('# 在下方输入 DSL 机器人程序\n# 示例：抓取 → 搬运 → 放置\nHOME\nWAIT 1\nMOVEJ J1=30,J2=-30,J3=30,J4=0,J5=0,J6=0\nSUCK ON\nWAIT 0.5\nMOVEJ J1=-30,J2=-30,J3=30,J4=0,J5=0,J6=0\nSUCK OFF\nHOME\n')
  // 当前执行行
  const currentLine = ref(0)
  // 日志输出
  const logs = ref([])
  // 程序执行结果
  const execResult = ref(null)
  // 校验错误
  const validationErrors = ref([])

  // 设置代码
  function setCode(newCode) {
    code.value = newCode
  }

  // 设置当前执行行
  function setCurrentLine(line) {
    currentLine.value = line
  }

  // 添加日志
  function addLog(message, level = 'info') {
    logs.value.push({
      message,
      level,
      timestamp: Date.now(),
    })
    // 限制日志数量
    if (logs.value.length > 500) {
      logs.value = logs.value.slice(-500)
    }
  }

  // 清空日志
  function clearLogs() {
    logs.value = []
  }

  // 设置执行结果
  function setExecResult(result) {
    execResult.value = result
  }

  // 设置校验错误
  function setValidationErrors(errors) {
    validationErrors.value = errors
  }

  // 插入代码片段
  function insertCode(snippet) {
    code.value += '\n' + snippet
  }

  return {
    code, currentLine, logs, execResult, validationErrors,
    setCode, setCurrentLine, addLog, clearLogs,
    setExecResult, setValidationErrors, insertCode,
  }
})