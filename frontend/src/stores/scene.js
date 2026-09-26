// stores/scene.js - 场景物体状态管理
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const useSceneStore = defineStore('scene', () => {
  // 物体列表
  const objects = ref([])
  // 选中的物体ID
  const selectedId = ref(null)
  // 场景名称
  const sceneName = ref('默认场景')

  // 物体类型选项
  const objectTypes = [
    { type: 'box', label: '方块', icon: 'Box' },
    { type: 'cylinder', label: '圆柱', icon: 'Coin' },
    { type: 'sphere', label: '球体', icon: 'Location' },
    { type: 'tray', label: '托盘', icon: 'Grid' },
  ]

  // 颜色选项
  const colorOptions = [
    { label: '红色', value: '#ff4444' },
    { label: '蓝色', value: '#2196F3' },
    { label: '绿色', value: '#4CAF50' },
    { label: '黄色', value: '#FFEB3B' },
    { label: '橙色', value: '#FF9800' },
    { label: '紫色', value: '#9C27B0' },
    { label: '青色', value: '#00BCD4' },
    { label: '白色', value: '#FFFFFF' },
  ]

  // 计算属性
  const selectedObject = computed(() =>
    objects.value.find(o => o.id === selectedId.value)
  )

  const objectCount = computed(() => objects.value.length)

  // 方法
  function setObjects(objs) {
    objects.value = objs
  }

  function addObject(obj) {
    objects.value.push(obj)
  }

  function removeObject(id) {
    const idx = objects.value.findIndex(o => o.id === id)
    if (idx >= 0) objects.value.splice(idx, 1)
    if (selectedId.value === id) selectedId.value = null
  }

  function updateObject(id, updates) {
    const obj = objects.value.find(o => o.id === id)
    if (obj) Object.assign(obj, updates)
  }

  function selectObject(id) {
    selectedId.value = id
  }

  function clearObjects() {
    objects.value = []
    selectedId.value = null
  }

  // 按名称查找
  function findByName(name) {
    return objects.value.find(o => o.name === name)
  }

  // 按颜色查找
  function findByColor(color) {
    return objects.value.find(o => o.color === color)
  }

  // 获取场景快照（供 AI / 后端同步使用）
  //
  // ⚠️ 必须带 id 和 type。旧版把这两个字段丢了，于是后端广播回来时
  //    SceneManager.fromJSON 拿不到 type（落到 default 分支），
  //    场景里所有物体都会被重建成方块 —— 用户看到的就是「改了个颜色，
  //    物体形状却变了」。id 丢了还会导致选中状态和 3D 物体对不上号。
  function getSnapshot() {
    return objects.value.map(o => ({
      id: o.id,
      type: o.type,
      name: o.name,
      color: o.color,
      position: o.position,
      size: o.size,
      grabbable: o.grabbable !== false,
    }))
  }

  return {
    objects, selectedId, sceneName,
    objectTypes, colorOptions,
    selectedObject, objectCount,
    setObjects, addObject, removeObject, updateObject,
    selectObject, clearObjects,
    findByName, findByColor, getSnapshot,
  }
})