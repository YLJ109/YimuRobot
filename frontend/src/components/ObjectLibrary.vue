<template>
  <div class="object-library">
    <div class="add-row">
      <el-select v-model="newType" size="small" class="type-select">
        <el-option v-for="t in sceneStore.objectTypes" :key="t.type" :label="t.label" :value="t.type" />
      </el-select>
      <el-color-picker v-model="newColor" size="small" class="color-picker" />
      <button class="add-btn" @click="addObject"><el-icon><Plus /></el-icon><span>添加</span></button>
    </div>
    <div class="obj-list">
      <div v-for="obj in sceneStore.objects" :key="obj.id" class="obj-card" :class="{ selected: obj.id === sceneStore.selectedId }" @click="sceneStore.selectObject(obj.id)">
        <div class="obj-swatch" :style="{ background: obj.color }"></div>
        <div class="obj-detail">
          <div class="obj-name">{{ obj.name }}</div>
          <div class="obj-coord">{{ obj.position[0].toFixed(0) }} · {{ obj.position[1].toFixed(0) }} · {{ obj.position[2].toFixed(0) }}</div>
        </div>
        <button class="obj-del" @click.stop="removeObject(obj.id)"><el-icon><Delete /></el-icon></button>
      </div>
      <div v-if="sceneStore.objects.length === 0" class="obj-empty">暂无物体</div>
    </div>
    <div v-if="sceneStore.selectedObject" class="edit-block">
      <div class="section-title">编辑物体</div>
      <el-form size="small" label-width="48px" class="edit-form">
        <el-form-item label="名称"><el-input v-model="editName" @change="updateName" /></el-form-item>
        <el-form-item label="颜色"><el-color-picker v-model="editColor" @change="updateColor" /></el-form-item>
        <el-form-item label="X"><el-input-number v-model="editPos[0]" :step="10" @change="updatePos" controls-position="right" /></el-form-item>
        <el-form-item label="Y"><el-input-number v-model="editPos[1]" :step="10" @change="updatePos" controls-position="right" /></el-form-item>
        <el-form-item label="Z"><el-input-number v-model="editPos[2]" :step="10" @change="updatePos" controls-position="right" /></el-form-item>
      </el-form>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import { Plus, Delete } from '@element-plus/icons-vue'
import { useSceneStore } from '../stores/scene.js'
const sceneStore = useSceneStore()
const newType = ref('box')
const newColor = ref('#ff4444')
const editName = ref('')
const editColor = ref('')
const editPos = ref([0, 0, 0])
let nextId = 100

// ── 本地优先（乐观更新）──────────────────────────────────────
//
// 关键教训：3D 场景**不能**靠后端来回同步。
// 历史上这里只改 store + 发 sim_scene_update 给后端，指望后端广播
// scene_objects 再回来驱动 SceneManager。于是只要 socket 没连上，
// 拖坐标、换颜色就完全看不到变化；即使连上了，也得等一个来回。
// 现在改成：先直接改 3D 场景（帧内生效），再顺手把快照同步给后端做持久化。
function patchLocal(id, updates) {
  window.dispatchEvent(new CustomEvent('scene_patch', { detail: { id, updates } }))
}
function addLocal(obj) {
  window.dispatchEvent(new CustomEvent('scene_add', { detail: obj }))
}
function removeLocal(id) {
  window.dispatchEvent(new CustomEvent('scene_remove', { detail: { id } }))
}

function addObject() {
  const obj = { id: ++nextId, type: newType.value, name: `${getTypeLabel(newType.value)}${sceneStore.objects.length + 1}`, color: newColor.value, position: [0, 25, 0], size: [50, 50, 50], grabbable: true }
  sceneStore.addObject(obj)
  addLocal(obj)
  notifySceneUpdate()
}
function removeObject(id) {
  sceneStore.removeObject(id)
  removeLocal(id)
  notifySceneUpdate()
}
function updateName() {
  if (!sceneStore.selectedObject) return
  sceneStore.updateObject(sceneStore.selectedId, { name: editName.value })
  patchLocal(sceneStore.selectedId, { name: editName.value })
  notifySceneUpdate()
}
function updateColor() {
  if (!sceneStore.selectedObject) return
  sceneStore.updateObject(sceneStore.selectedId, { color: editColor.value })
  patchLocal(sceneStore.selectedId, { color: editColor.value })
  notifySceneUpdate()
}
function updatePos() {
  if (!sceneStore.selectedObject) return
  const position = [...editPos.value]
  sceneStore.updateObject(sceneStore.selectedId, { position })
  patchLocal(sceneStore.selectedId, { position })
  notifySceneUpdate()
}
function getTypeLabel(type) { const t = sceneStore.objectTypes.find(o => o.type === type); return t ? t.label : type }
// 后台同步：socket 未连接时后端拿不到，但场景已经是改好的（本地优先）
function notifySceneUpdate() {
  window.dispatchEvent(new CustomEvent('sim_scene_update', { detail: { objects: sceneStore.getSnapshot() } }))
}
watch(() => sceneStore.selectedObject, (obj) => { if (obj) { editName.value = obj.name; editColor.value = obj.color; editPos.value = [...obj.position] } }, { immediate: true })
</script>

<style scoped>
/* ObjectLibrary 渲染在 ManualPanel 的 tab 面板内，
   padding 由外层 .panel-tabs-wrap 统一提供，这里不能再叠加一层。 */

.object-library { font-family: var(--font-sans); }

.add-row {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  margin-bottom: var(--space-3);
}
.type-select { flex: 1; }
.color-picker { flex-shrink: 0; }

.add-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  height: 30px;
  padding: 0 var(--pad-xl);
  border: none;
  border-radius: var(--radius-sm);
  background: var(--accent);
  color: var(--text-on-accent);
  font-size: var(--font-size-sm);
  font-weight: 600;
  cursor: pointer;
  flex-shrink: 0;
  transition: background var(--duration-fast) var(--ease-out);
}
.add-btn:hover { background: var(--accent-hover); }
.add-btn:active { transform: scale(0.97); }

/* ── 物体列表 ─────────────────────────────────────────────── */
.obj-list {
  max-height: 300px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.obj-card {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--pad-lg) var(--pad-xl);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-sm);
  background: var(--bg-card);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out);
}
.obj-card:hover { background: var(--bg-elevated); border-color: var(--border); }
.obj-card.selected { border-color: var(--accent); background: var(--accent-soft); }

.obj-swatch {
  width: 18px;
  height: 18px;
  border-radius: var(--radius-xs);
  flex-shrink: 0;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.14);
}

.obj-detail { flex: 1; min-width: 0; }
.obj-name {
  font-size: var(--font-size-md);
  color: var(--text-primary);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.obj-coord {
  font-size: var(--font-size-xs);
  color: var(--text-tertiary);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
}

.obj-del {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-disabled);
  cursor: pointer;
  flex-shrink: 0;
  transition: background var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out);
}
.obj-del:hover { background: var(--danger-soft); color: var(--danger); }

.obj-empty {
  text-align: center;
  padding: var(--pad-xl);
  color: var(--text-disabled);
  font-size: var(--font-size-md);
}

/* ── 编辑区 ───────────────────────────────────────────────── */
.edit-block {
  margin-top: var(--space-4);
  padding-top: var(--pad-xl);
  border-top: 1px solid var(--border-subtle);
}
.section-title {
  font-size: var(--font-size-xs);
  font-weight: 600;
  color: var(--text-tertiary);
  letter-spacing: var(--tracking-wide);
  margin-bottom: var(--space-2);
}
.edit-form :deep(.el-form-item) { margin-bottom: var(--space-2); }
</style>
