<template>
  <!--
    安全围栏独立面板。

    正方形玻璃围栏：4 面玻璃墙 + 4 根角柱 + 4 根顶梁，把机器人围在中间。
    按「碰撞接近度」分级上色（余量 REF = 150mm 对应 0%）：
      · 100%  已碰撞 → 红色闪烁
      · ≥90%  危险   → 变红   （余量 < 15mm）
      · ≥80%  警告   → 变黄   （余量 < 30mm）
      · 其余  安全   → 绿色透明玻璃

    这里是**纯控制面板**：所有改动只写 fenceStore，
    由 RobotViewport 的 watch 同步到 Three.js 几何；级别由 RobotViewport
    每帧回写（level / clearance / proximity），本面板只负责显示。
  -->
  <div class="fence-panel">
    <!-- ── 尺寸 ── -->
    <div class="section-block">
      <div class="section-title">
        <span>围栏尺寸</span>
        <span class="fence-dim">{{ fenceStore.sizeLabel }}</span>
      </div>

      <div class="fence-row">
        <el-switch
          :model-value="fenceStore.visible"
          @update:model-value="(v) => fenceStore.setVisible(v)"
        />
        <span class="fence-label">显示围栏</span>
      </div>

      <div class="fence-row">
        <span class="fence-key">范围</span>
        <el-slider
          class="fence-grow"
          :model-value="fenceStore.half"
          @update:model-value="(v) => fenceStore.setHalf(v)"
          :min="300" :max="1400" :step="10" :show-tooltip="false" size="small"
        />
        <span class="fence-val">{{ fenceStore.half }}<em>半边</em></span>
      </div>
      <div class="fence-row fence-row--btns">
        <button class="fence-btn" @click="fenceStore.shrink()" title="缩小 50mm">
          <el-icon><Minus /></el-icon><span>缩小</span>
        </button>
        <button class="fence-btn" @click="fenceStore.expand()" title="扩大 50mm">
          <el-icon><Plus /></el-icon><span>扩大</span>
        </button>
      </div>

      <div class="fence-row">
        <span class="fence-key">高度</span>
        <el-slider
          class="fence-grow"
          :model-value="fenceStore.height"
          @update:model-value="(v) => fenceStore.setHeight(v)"
          :min="200" :max="1600" :step="10" :show-tooltip="false" size="small"
        />
        <span class="fence-val">{{ fenceStore.height }}<em>mm</em></span>
      </div>
    </div>

    <!-- ── 实时状态 ── -->
    <div class="section-block">
      <div class="section-title"><span>实时状态</span></div>
      <div class="fence-status" :class="levelClass">
        <span class="fence-dot"></span>
        <span class="fence-status-text">{{ levelText }}</span>
        <span class="fence-metrics">
          接近度 {{ proximity }}% · 余量 {{ clearanceMm }}mm
        </span>
      </div>
      <div class="fence-tip">
        玻璃颜色随接近度变化：绿=安全 / 黄=警告(≥80%) / 红=危险(≥90%) / 红闪=已碰撞(100%)。
        碰撞时自动播报语音并停机（语音播报关闭时静默）。
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Minus, Plus } from '@element-plus/icons-vue'
import { useFenceStore } from '../stores/fence.js'

const fenceStore = useFenceStore()

const levelText = computed(() => ({
  safe: '安全',
  warn: '警告',
  danger: '危险',
  collision: '碰撞！',
}[fenceStore.level] || '安全'))

const levelClass = computed(() => `is-${fenceStore.level}`)

const proximity = computed(() => {
  const p = Number(fenceStore.proximity)
  return Number.isFinite(p) ? Math.max(0, Math.min(100, Math.round(p))) : 0
})

const clearanceMm = computed(() => {
  const c = Number(fenceStore.clearance)
  return Number.isFinite(c) ? Math.round(c) : 0
})
</script>

<style scoped>
/* 全部色值/尺寸取自 assets/design-system.css，零硬编码。 */
.fence-panel { width: 100%; }

.section-block { margin-bottom: var(--space-4); }
.section-block:last-child { margin-bottom: 0; }

.section-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--text-tertiary);
  letter-spacing: var(--tracking-wide);
  margin-bottom: var(--space-2);
}

.fence-dim {
  font-size: 10px;
  color: var(--text-disabled);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  letter-spacing: 0;
}

/* ── 行布局 ── */
.fence-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--pad-sm) var(--pad-xl);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  margin-bottom: var(--space-2);
}
.fence-row--btns { gap: var(--space-2); padding: var(--pad-sm); }

.fence-label {
  font-size: var(--font-size-base);
  color: var(--text-secondary);
  font-weight: 500;
}

.fence-key {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  font-weight: 600;
  font-family: var(--font-mono);
  width: 28px;
  flex-shrink: 0;
}

/* 滑杆必须能撑开：Element Plus 的 .el-slider 默认是块级但无宽度，
   放在 flex 行里若不给 flex:1 会被内容挤成 0 宽（表现就是“滑杆不见了”）。 */
.fence-grow { flex: 1 1 auto; min-width: 0; }

.fence-val {
  font-size: var(--font-size-xs);
  font-family: var(--font-mono);
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  min-width: 58px;
  text-align: right;
  flex-shrink: 0;
}
.fence-val em {
  margin-left: 2px;
  font-style: normal;
  font-size: 10px;
  color: var(--text-disabled);
}

/* ── 按钮 ── */
.fence-btn {
  flex: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  height: 30px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: var(--bg-elevated);
  color: var(--text-secondary);
  font-size: var(--font-size-md);
  font-weight: 500;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out);
}
.fence-btn:hover { background: var(--bg-hover); border-color: var(--border-strong); color: var(--text-primary); }
.fence-btn:active { transform: scale(0.98); }

/* ── 状态条 ── */
.fence-status {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--pad-sm) var(--pad-xl);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  font-size: var(--font-size-sm);
}

.fence-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--text-disabled);
  flex-shrink: 0;
  transition: background var(--duration-fast) var(--ease-out);
}

.fence-status-text { color: var(--text-secondary); font-weight: 500; flex-shrink: 0; }

.fence-metrics {
  margin-left: auto;
  font-family: var(--font-mono);
  font-size: var(--font-size-xs);
  color: var(--text-disabled);
  font-variant-numeric: tabular-nums;
}

.fence-status.is-safe { border-color: var(--success); }
.fence-status.is-safe .fence-dot { background: var(--success); box-shadow: var(--glow-success); }
.fence-status.is-safe .fence-status-text { color: var(--success); }

.fence-status.is-warn { border-color: var(--warning); background: var(--warning-soft); }
.fence-status.is-warn .fence-dot { background: var(--warning); box-shadow: var(--glow-warning); }
.fence-status.is-warn .fence-status-text { color: var(--warning); }

.fence-status.is-danger { border-color: var(--danger); background: var(--danger-soft); }
.fence-status.is-danger .fence-dot { background: var(--danger); box-shadow: var(--glow-danger); }
.fence-status.is-danger .fence-status-text { color: var(--danger); }

.fence-status.is-collision {
  border-color: var(--danger);
  background: var(--danger);
  animation: fence-collision-flash 0.45s ease-in-out infinite;
}
.fence-status.is-collision .fence-dot { background: #fff; box-shadow: 0 0 6px #fff; }
.fence-status.is-collision .fence-status-text { color: #fff; }
.fence-status.is-collision .fence-metrics { color: rgba(255, 255, 255, 0.85); }

@keyframes fence-collision-flash {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.55; }
}

.fence-tip {
  margin-top: var(--space-2);
  font-size: var(--font-size-xs);
  color: var(--text-disabled);
  line-height: 1.6;
}
</style>
