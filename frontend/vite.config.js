import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import AutoImport from 'unplugin-auto-import/vite'
import Components from 'unplugin-vue-components/vite'
import { ElementPlusResolver } from 'unplugin-vue-components/resolvers'

export default defineConfig({
  plugins: [
    vue(),
    // Element Plus 按需自动引入：
    //  - AutoImport 负责脚本里用到的 API（ElMessage 等）
    //  - Components 负责模板里用到的 <el-*> 组件 + 各自的样式
    // 这样打包时只含「真正用到」的组件，不再整包引入 element-plus。
    AutoImport({ resolvers: [ElementPlusResolver()] }),
    Components({ resolvers: [ElementPlusResolver()] }),
  ],
  server: {
    host: '0.0.0.0',
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
      '/socket.io': {
        target: 'http://localhost:5000',
        changeOrigin: true,
        ws: true,
      },
      '/audio': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        // 用函数式 manualChunks，确保 element-plus 的深层按需路径
        //（element-plus/es/components/...）也能正确归并到 element 分包，
        //而不是散落进 index 或被重复打包。
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('element-plus') || id.includes('@element-plus')) return 'element'
          if (id.includes('three')) return 'three'
          if (id.includes('@codemirror') || id.includes('@lezer')) return 'codemirror'
          if (id.includes('socket.io')) return 'socketio'
        },
      },
    },
  },
})
