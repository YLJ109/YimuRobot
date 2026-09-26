// robotContext.js — 机械臂实例的跨组件访问点
//
// RobotViewport 拥有唯一一个 RobotArm 实例（它负责 Three.js 场景生命周期），
// 但 ManualPanel 的笛卡尔点动需要在同一个实例上跑数值逆解 —— 逆解必须以
// 「屏幕上这一个数模」为 FK 预言机，另建一个实例会导致解出的姿态与渲染不符。
//
// 这里用一个极薄的模块级注册表把实例暴露出去，避免为了拿一个对象引用
// 而在组件树里层层 provide/inject。

let _arm = null
let _frameModel = null

/** 由 RobotViewport 在数模就绪后调用 */
export function registerArm(arm, frameModel) {
  _arm = arm || null
  _frameModel = typeof frameModel === 'function' ? frameModel : null
}

/** 由 RobotViewport 在卸载时调用 */
export function unregisterArm(arm) {
  if (arm && _arm !== arm) return   // 只清理自己注册的那个实例
  _arm = null
  _frameModel = null
}

/** @returns {import('./RobotArm.js').RobotArm|null} */
export function getArm() { return _arm }

/** 请求一次自动取景（重置视角按钮 / 姿态大改后调用）*/
export function requestFrame() { if (_frameModel) _frameModel() }

/** 供 UI 判断是否已就绪 */
export function isArmReady() { return !!(_arm && _arm.ready) }
