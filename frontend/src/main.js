import { createApp } from 'vue'
import { createPinia } from 'pinia'
// ⚠️ 不再整包引入 Element Plus：组件 / API / 图标均走 vite.config.js 里的
// unplugin 按需自动引入（ElementPlusResolver）。这里只保留「全站级」的
// 深色变量表（只是一组 CSS 自定义属性，体积极小），组件样式由 resolver 按需注入。
import 'element-plus/theme-chalk/dark/css-vars.css'

import './assets/global.css'
import App from './App.vue'

import { useRobotStore } from './stores/robot.js'
import { useEditorStore } from './stores/editor.js'
import { useSceneStore } from './stores/scene.js'
import { useAiStore } from './stores/ai.js'

// 全站深色（Linear Aesthetic）。EP 的深色变量挂在 `html.dark` 下，
// global.css 再把它映射到 design-system.css 的 Linear 调色板。
document.documentElement.classList.add('dark')

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.mount('#app')

// ── 开发期调试钩子 ────────────────────────────────────────────
// 仅 DEV 构建注入，生产打包会被 tree-shake 掉。供 tools/ui_probe.mjs 做
// 端到端回归（点动滑杆 → 数模是否真的动、运行程序 → 是否真的收到帧）。
// ⚠️ 必须是「合并」而不是整体赋值。
//   app.mount() 会同步跑完子组件的 onMounted，RobotViewport 在里面已经往
//   window.__sim 上挂了 viewport（NDC 覆盖率探针）。这里若写
//   `window.__sim = { stores }` 会把刚挂上去的 viewport 整个抹掉 ——
//   现象就是探针报「__sim keys = ["stores"]」。
if (import.meta.env.DEV) {
  window.__sim = window.__sim || {}
  window.__sim.stores = {
    robot: useRobotStore(),
    editor: useEditorStore(),
    scene: useSceneStore(),
    ai: useAiStore(),
  }
}
