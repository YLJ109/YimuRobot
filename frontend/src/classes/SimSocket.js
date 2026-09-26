// SimSocket.js - WebSocket 通信封装
// 连接管理 + 事件收发 + 断线重连

import { io } from 'socket.io-client'

export class SimSocket {
  constructor(url = '') {
    this.url = url || (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.hostname}:5000` : 'http://localhost:5000')
    this.socket = null
    this.connected = false
    this.reconnectAttempts = 0
    this.maxReconnectAttempts = 10
    this.reconnectDelay = 2000

    // 事件回调
    this.handlers = {
      connect: [],
      disconnect: [],
      robot_frame: [],
      program_finished: [],
      ai_reply: [],
      asr_status: [],
      asr_final: [],
      asr_error: [],
      scene_objects: [],
      log: [],
    }
  }

  connect() {
    if (this.socket?.connected) return

    this.socket = io(this.url, {
      // polling 优先，再升级到 websocket。
      // 反过来写（websocket 优先）时，一旦 upgrade 被中间层掐掉，socket.io 会
      // 卡在 "WebSocket is closed before the connection is established"，
      // 长时间连不上 —— 用户看到的「一直连接失败」就是它。
      transports: ['polling', 'websocket'],
      upgrade: true,
      reconnection: true,
      reconnectionAttempts: this.maxReconnectAttempts,
      reconnectionDelay: this.reconnectDelay,
      timeout: 10000,
    })

    this.socket.on('connect', () => {
      this.connected = true
      this.reconnectAttempts = 0
      this._emit('connect')
    })

    this.socket.on('disconnect', (reason) => {
      this.connected = false
      this._emit('disconnect', reason)
    })

    this.socket.on('connect_error', (err) => {
      this.reconnectAttempts++
    })

    // 注册所有事件
    for (const event of Object.keys(this.handlers)) {
      if (event === 'connect' || event === 'disconnect') continue
      this.socket.on(event, (data) => {
        this._emit(event, data)
      })
    }
  }

  disconnect() {
    if (this.socket) {
      this.socket.disconnect()
      this.socket = null
      this.connected = false
    }
  }

  // ────────────────── 事件订阅 ──────────────────
  on(event, callback) {
    if (this.handlers[event]) {
      this.handlers[event].push(callback)
    }
    return () => this.off(event, callback)
  }

  off(event, callback) {
    if (this.handlers[event]) {
      const idx = this.handlers[event].indexOf(callback)
      if (idx >= 0) this.handlers[event].splice(idx, 1)
    }
  }

  _emit(event, data) {
    if (this.handlers[event]) {
      for (const cb of this.handlers[event]) {
        try { cb(data) } catch (e) { console.error(`[SimSocket] 事件处理错误 ${event}:`, e) }
      }
    }
  }

  // ────────────────── 发送事件 ──────────────────
  // 前端 → 后端
  runProgram(code) {
    this._send('run_program', { code })
  }

  stop() {
    this._send('stop')
  }

  pause() {
    this._send('pause')
  }

  resume() {
    this._send('resume')
  }

  step() {
    this._send('step')
  }

  setJoint(index, degree) {
    this._send('set_joint', { index, degree })
  }

  /**
   * 整组关节角下发。
   * 笛卡尔滑杆是本地跑数值逆解得到 6 个角，必须一次性同步给后端，
   * 否则后端 global_state 会与画面分叉，下次运行程序时起始位姿就错了。
   */
  setJoints(joints) {
    if (!Array.isArray(joints) || joints.length !== 6) return
    this._send('set_joints', { joints: joints.map(Number) })
  }

  /**
   * 吸盘开关。
   * 走独立事件而不是 nl_input('吸取')：借道自然语言链路会形成
   * ai_reply(quick_command) → sim_command → nl_input → … 的无限往返。
   */
  setSuck(on) {
    this._send('set_suck', { on: !!on })
  }

  nlInput(text) {
    this._send('nl_input', { text })
  }

  /**
   * 发送**一整段**语音（按下麦克风 → 说话 → 松手 的完整产物）。
   *
   * 以前是每 100ms 发一个 audio_chunk，后端拿每一片去喂 whisper ——
   * MediaRecorder 的 timeslice 分片只有第一片带 WebM 容器头，其余都是裸帧，
   * whisper 解不出来，识别结果永远是空字符串。整段发送才对。
   *
   * @param {Blob|ArrayBuffer} audio 完整音频
   * @param {string} mime 容器类型（audio/webm;codecs=opus 等），后端据此定后缀
   */
  async audioUtterance(audio, mime = '') {
    if (!audio) return
    let buf
    try {
      buf = audio instanceof Blob ? await audio.arrayBuffer() : audio
    } catch (e) {
      console.warn('[SimSocket] 音频读取失败', e)
      return
    }
    if (!buf || !buf.byteLength) return
    this._send('audio_utterance', { data: new Uint8Array(buf), mime })
  }

  sceneUpdate(objects) {
    this._send('scene_update', { objects })
  }

  clearChat() {
    this._send('clear_chat')
  }

  _send(event, data) {
    if (this.socket?.connected) {
      this.socket.emit(event, data)
    } else {
      console.warn(`[SimSocket] 未连接，无法发送 ${event}`)
    }
  }

  // ────────────────── 状态 ──────────────────
  isConnected() {
    return this.connected
  }
}