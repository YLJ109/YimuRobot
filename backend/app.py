# -*- coding: utf-8 -*-
"""
Flask 主应用 + SocketIO 事件路由
- WebSocket 实时通信
- 整合 parser / executor / kinematics / ai_gateway / speech / validator
- SQLite 持久化（场景、程序、对话历史）
- 静态文件服务（前端构建产物）
"""
# ══════════════════════════════════════════════════════════════════════════
#  并发模型：threading（**不要**改回 eventlet）
# ══════════════════════════════════════════════════════════════════════════
#  这里曾经是 eventlet + monkey_patch。问题出在 eventlet 是协作式调度：
#  RobotExecutor 的插补循环（_exec_movej / _exec_movelp）里只要有任何一个
#  不主动让出 CPU 的片段，整个 green hub 就被饿死 —— HTTP 端口还在
#  LISTEN，但 /api/scenes 会一直 000 超时，socket.io 也升不上 WebSocket。
#  现象就是「后端像是死了，代码执行不了」。
#
#  threading 模式下 Flask/Werkzeug 用真实线程，执行器跑在自己的
#  thread 里，它卡住只卡自己，服务端始终可响应。websocket 由
#  simple-websocket 提供（threading 模式下 engineio 的标准搭配）。
# ══════════════════════════════════════════════════════════════════════════

import os
from dotenv import load_dotenv
load_dotenv()
import json
import time
import math
import numpy as np
from datetime import datetime
from typing import Optional, List, Dict, Any

from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from flask_cors import CORS

# 导入业务模块
from parser import parser
from validator import validator
from executor import executor, RobotExecutor, TrajectoryFrame, ExecState
from kinematics import solver, fk_pose, fk_position, deg2rad, rad2deg, HOME_JOINTS_DEG
from ai_gateway import ai_gateway, AiGateway, AiResponse
from speech import asr_engine, tts_engine, wake_detector

# ────────────────────────── Flask 应用 ──────────────────────────
app = Flask(__name__, static_folder='../frontend/dist', static_url_path='')
app.config['SECRET_KEY'] = 'efort-robot-sim-2024'
CORS(app)
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    # 执行器每 1/60s 推一帧，默认 ping 间隔足够；但前端在后台标签页会被
    # 浏览器降频，这里把超时放宽，避免演示中途莫名其妙掉线。
    ping_timeout=60,
    ping_interval=25,
)

# ────────────────────────── SQLite 数据库 ──────────────────────────
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), 'robot_sim.db')


def init_db():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS scenes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        data TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS programs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        code TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()


def save_scene(name: str, data: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO scenes (name, data) VALUES (?, ?)", (name, data))
    conn.commit()
    conn.close()


def load_scenes() -> List[dict]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, data, created_at FROM scenes ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'name': r[1], 'data': json.loads(r[2]), 'created_at': r[3]} for r in rows]


def save_program(name: str, code: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO programs (name, code) VALUES (?, ?)", (name, code))
    conn.commit()
    conn.close()


def load_programs() -> List[dict]:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, name, code, created_at FROM programs ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    return [{'id': r[0], 'name': r[1], 'code': r[2], 'created_at': r[3]} for r in rows]


# ────────────────────────── 全局状态 ──────────────────────────
global_state = {
    'scene_objects': [],           # 场景物体列表
    'current_joints_deg': list(HOME_JOINTS_DEG),  # 当前关节角度
    'holding': None,               # 持物名称
    'suck_on': False,
    'clients': set(),              # 已连接的客户端
}


# ────────────────────────── 执行器回调 ──────────────────────────
def on_frame(frame: TrajectoryFrame):
    """执行器帧推送回调 → 通过 SocketIO 推送给前端"""
    global_state['current_joints_deg'] = frame.joints_deg
    global_state['suck_on'] = frame.suck_on
    global_state['holding'] = frame.holding

    # 推送关节帧（弧度）
    joints_rad = [deg2rad(j) for j in frame.joints_deg]
    socketio.emit('robot_frame', {'type': 'joints', 'j': joints_rad})
    # 推送吸盘状态
    socketio.emit('robot_frame', {'type': 'suck', 'on': frame.suck_on})
    # 推送当前执行行
    if frame.line > 0:
        socketio.emit('robot_frame', {'type': 'line', 'line': frame.line})


def on_finished(success: bool, message: str):
    """执行完成回调"""
    socketio.emit('program_finished', {'success': success, 'message': message})


def on_log(msg: str):
    """日志回调"""
    socketio.emit('log', {'message': msg, 'timestamp': time.time()})


executor.on_frame = on_frame
executor.on_finished = on_finished
executor.on_log = on_log


# ────────────────────────── HTTP 路由 ──────────────────────────
@app.route('/')
def index():
    """前端首页"""
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/<path:path>')
def static_files(path):
    """静态文件"""
    full_path = os.path.join(app.static_folder, path)
    if os.path.exists(full_path):
        return send_from_directory(app.static_folder, path)
    return send_from_directory(app.static_folder, 'index.html')


# ── TTS 音频服务 ─────────────────────────────────────────────
# 落盘目录必须与 speech.TtsEngine 的写入口径一致（都用 __file__ 定位，
# 不依赖启动时的工作目录）。
AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static', 'audio')


@app.route('/audio/<path:filename>')
def serve_audio(filename):
    """提供 TTS 合成的 mp3。

    ⚠️ 这条路由以前**根本不存在**：speech.synthesize_to_file() 返回的是
    /audio/xxx.mp3，但 Flask 里没有任何规则接住它，请求于是落到上面的
    catch-all 静态路由，去 ../frontend/dist/audio/xxx.mp3 找文件（不存在），
    最后返回 dist/index.html —— 一段 HTML。
    前端 <audio> 拿到非音频内容就抛
    "Failed to load because no supported source was found"，
    看起来像是「浏览器自动播放策略没放开」，实际是文件压根没被服务。
    """
    return send_from_directory(AUDIO_DIR, filename)


@app.route('/api/health')
def health():
    """健康检查。除了总开关，还要把 ASR 的**具体状态**带出去。

    『语音转文字不行』这个报障最难查的地方就是：/api/health 只回一个
    `whisper_loaded: false`，看不出到底是「没装库」「模型在下载」「模型下不下来」
    还是「装了但加载报错」。这里把 status 原样带出来，前端和探针都能一眼定位。
    """
    return jsonify({
        'status': 'ok',
        'robot': 'EFORT ER3-600',
        'whisper_loaded': asr_engine._loaded,
        'asr': asr_engine.status(),
        'tts_available': tts_engine is not None,
        'llm_configured': ai_gateway.is_configured(),
    })


@app.route('/api/scenes', methods=['GET', 'POST'])
def scenes_api():
    if request.method == 'POST':
        data = request.json
        save_scene(data['name'], json.dumps(data['objects']))
        return jsonify({'ok': True})
    return jsonify(load_scenes())


@app.route('/api/programs', methods=['GET', 'POST'])
def programs_api():
    if request.method == 'POST':
        data = request.json
        save_program(data['name'], data['code'])
        return jsonify({'ok': True})
    return jsonify(load_programs())


@app.route('/api/tts', methods=['POST'])
def tts_api():
    """TTS 合成接口"""
    text = request.json.get('text', '')
    if not text:
        return jsonify({'error': '缺少 text 参数'}), 400
    audio_url = tts_engine.synthesize_to_file(text)
    if audio_url:
        return jsonify({'audio_url': audio_url})
    return jsonify({'error': 'TTS 合成失败'}), 500


@app.route('/api/tcp')
def tcp_api():
    """获取当前 TCP 位姿"""
    pose = fk_pose(global_state['current_joints_deg'])
    return jsonify({
        'tcp': pose,
        'joints_deg': global_state['current_joints_deg'],
        'holding': global_state['holding'],
        'suck_on': global_state['suck_on'],
    })


# ────────────────────────── SocketIO 事件 ──────────────────────────
@socketio.on('connect')
def on_connect():
    global_state['clients'].add(request.sid)
    print(f"[SocketIO] 客户端连接: {request.sid}, 当前连接数: {len(global_state['clients'])}")
    # 推送当前状态
    joints_rad = [deg2rad(j) for j in global_state['current_joints_deg']]
    emit('robot_frame', {'type': 'joints', 'j': joints_rad})
    emit('robot_frame', {'type': 'suck', 'on': global_state['suck_on']})
    emit('scene_objects', {'objects': global_state['scene_objects']})


@socketio.on('disconnect')
def on_disconnect():
    global_state['clients'].discard(request.sid)
    # 回收该会话的 API Key 客户端与对话历史（按 sid 隔离，避免串号/内存泄漏）
    ai_gateway.clear_session(request.sid)
    print(f"[SocketIO] 客户端断开: {request.sid}")


@socketio.on('run_program')
def on_run_program(data):
    """开始执行程序"""
    code = data.get('code', '')
    if not code:
        emit('program_finished', {'success': False, 'message': '程序为空'})
        return
    # 执行前把场景快照给执行器：PICK/PLACE 要靠它认物体名和物体高度
    executor.scene_objects = global_state['scene_objects']
    # 先校验
    val_result = validator.validate(
        code, global_state['scene_objects'], global_state['current_joints_deg']
    )
    if not val_result.valid:
        emit('program_finished', {
            'success': False,
            'message': '程序校验失败',
            'errors': val_result.to_dict()['errors']
        })
        return
    executor.run(code)


@socketio.on('stop')
def on_stop():
    executor.stop()
    emit('program_finished', {'success': False, 'message': '已停止'})


@socketio.on('pause')
def on_pause():
    executor.pause()


@socketio.on('resume')
def on_resume():
    executor.resume()


@socketio.on('step')
def on_step():
    executor.step()


@socketio.on('set_joint')
def on_set_joint(data):
    """手动控制单个关节"""
    index = data.get('index', -1)
    degree = data.get('degree', 0)
    if 0 <= index < 6:
        executor.set_joint(index, degree)
        global_state['current_joints_deg'][index] = degree
        # 推送更新
        joints_rad = [deg2rad(j) for j in global_state['current_joints_deg']]
        emit('robot_frame', {'type': 'joints', 'j': joints_rad})


@socketio.on('set_joints')
def on_set_joints(data):
    """手动控制：整组关节角。

    前端笛卡尔滑杆是本地跑数值逆解后一次性下发 6 个角，没有这个入口的话
    后端 global_state 会一直停在旧姿态 —— 下一次 run_program 的校验和
    起始位姿就全错了。
    """
    joints = data.get('joints')
    if not executor.set_joints_all(joints):
        return
    global_state['current_joints_deg'] = [float(j) for j in joints]
    joints_rad = [deg2rad(j) for j in global_state['current_joints_deg']]
    emit('robot_frame', {'type': 'joints', 'j': joints_rad})


@socketio.on('nl_input')
def on_nl_input(data):
    """自然语言输入（文字）"""
    text = data.get('text', '').strip()
    if not text:
        return

    # 唤醒词检测
    if not wake_detector.check(text):
        emit('ai_reply', {'text': '请先说唤醒词"小艺小艺"来激活语音控制'})
        return
    text = wake_detector.extract_command(text)

    # ── 调用 AI 网关 ──
    #
    # ⚠️ 必须保证**任何情况下都回一条 ai_reply**。
    #    前端在发消息时会把「思考中」置真，只等 ai_reply 复位。历史上这里是
    #    裸调用：只要 LLM 超时 / 网络异常 / 返回体解析失败抛一次异常，
    #    ai_reply 就永远不来，界面上的转圈会一直转下去（用户报的「一直卷圈」）。
    try:
        resp = ai_gateway.process(
            user_text=text,
            scene_objects=global_state['scene_objects'],
            current_joints_deg=global_state['current_joints_deg'],
            holding=global_state['holding'],
            sid=request.sid,
        )
    except Exception as exc:  # noqa: BLE001 - 兜底，绝不能让前端悬着
        emit('ai_reply', {
            'text': f'AI 处理失败：{exc}',
            'audioUrl': '',
            'program': None,
            'programValid': False,
            'quickCommand': None,
            'quickCommands': [],
            'needClarify': False,
            'clarifyQuestion': '',
            'error': str(exc),
        })
        return

    # TTS 语音播报（同样不能让它拖垮回复：合成失败就静默降级成纯文本）
    audio_url = ""
    try:
        if resp.text:
            audio_url = tts_engine.synthesize_to_file(resp.text) or ""
    except Exception:  # noqa: BLE001
        audio_url = ""

    # 推送 AI 响应
    #
    # ⚠️ 字段一律 **camelCase**（项目约定：后端出口统一转换）。
    #    历史上前端 stores/ai.js 读 camelCase、App.vue 读 snake_case，
    #    而后端发的又是 snake_case —— 结果是「程序已生成」徽章永远不亮、
    #    TTS 永远不播、澄清状态永远不生效，且两个消费端口径不一致。
    quick_commands = list(resp.quick_commands or [])
    if not quick_commands and resp.quick_command:
        quick_commands = [resp.quick_command]

    emit('ai_reply', {
        'text': resp.text,
        'audioUrl': audio_url,
        'explanation': resp.explanation,
        'program': resp.program,
        'programValid': resp.program_valid,
        'quickCommand': quick_commands[0] if quick_commands else None,
        'quickCommands': quick_commands,
        'needClarify': resp.need_clarify,
        'clarifyQuestion': resp.clarify_question,
        'error': resp.error,
    })

    # 如果有有效程序，自动执行
    if resp.program_valid and resp.program:
        val_result = validator.validate(
            resp.program, global_state['scene_objects'], global_state['current_joints_deg']
        )
        if val_result.valid:
            executor.run(resp.program)

    # 即时指令（可能一条消息里有多个，按模型给的顺序执行）
    for cmd in quick_commands:
        _handle_quick_command(cmd)


def _handle_quick_command(cmd: str):
    """处理快通道即时指令"""
    if cmd == 'stop':
        executor.stop()
    elif cmd == 'pause':
        executor.pause()
    elif cmd == 'resume':
        executor.resume()
    elif cmd == 'home':
        # "HOME" 是姿态不是程序：当程序丢给校验器会被判成非法指令。
        # 直接把关节角设回机械零位，再推一帧让前端跟上。
        executor.set_joints_all(list(HOME_JOINTS_DEG))
        global_state['current_joints_deg'] = list(HOME_JOINTS_DEG)
        socketio.emit('robot_frame', {
            'type': 'joints',
            'j': [deg2rad(j) for j in HOME_JOINTS_DEG],
        })
    elif cmd == 'suck_on':
        global_state['suck_on'] = True
        socketio.emit('robot_frame', {'type': 'suck', 'on': True})
    elif cmd == 'suck_off':
        global_state['suck_on'] = False
        global_state['holding'] = None
        socketio.emit('robot_frame', {'type': 'suck', 'on': False})


@socketio.on('audio_utterance')
def on_audio_utterance(data):
    """接收**一整段**语音（浏览器 MediaRecorder 从按下到松开的完整产物）并转写。

    为什么改成整段传（原来是每 100ms 发一个 audio_chunk）：
      MediaRecorder 的 timeslice 分片**只有第一片带 WebM 容器头**，后面都是裸帧。
      逐片喂给 whisper，除了第一片之外全部解不出声音 —— 识别结果永远是空字符串。
      前端现在自己把分片拼成一个 Blob，松手时一次性发过来。

    data: { data: <bytes|bytearray|list[int]>, mime: 'audio/webm;codecs=opus', sampleRate: 16000 }
    """
    if isinstance(data, (bytes, bytearray)):
        audio_bytes, mime = bytes(data), ''
    elif isinstance(data, dict) and 'data' in data:
        raw = data['data']
        audio_bytes = bytes(raw) if not isinstance(raw, bytes) else raw
        mime = str(data.get('mime') or '')
    else:
        audio_bytes, mime = b'', ''

    if not audio_bytes:
        return

    # 后缀要跟真实容器一致：PyAV 按内容嗅探，但对不上时会多绕一层
    suffix = '.wav'
    for key, ext in (('webm', '.webm'), ('ogg', '.ogg'), ('mp4', '.mp4'),
                     ('wav', '.wav'), ('mpeg', '.mp3')):
        if key in mime:
            suffix = ext
            break

    emit('asr_status', {'state': 'transcribing'})
    text = asr_engine.transcribe_bytes(audio_bytes, suffix=suffix)

    if text:
        emit('asr_final', {'text': text})
        # 直接进 AI 链路：用户按下麦克风→说话→松手 就是一个完整指令
        on_nl_input({'text': text})
    else:
        # 识别不出来必须说出来。以前这里什么都不发，前端只能看到麦克风
        # 转完圈然后没反应，看起来就是"语音转文字坏了"。
        st = asr_engine.status()
        reason = st.get('error') or (
            '没有识别到语音内容。请靠近麦克风、说清楚一点后重试。'
        )
        emit('asr_error', {'message': reason, 'status': st})


@socketio.on('set_suck')
def on_set_suck(data):
    """手动吸盘开关。

    以前手动拨吸盘是借道 nl_input('吸取'/'放下') 走快通道的，结果是：
    后端回 ai_reply(quick_command=suck_on) → 前端再派发 sim_command('suck_on')
    → 又调 nl_input('吸取') → **无限往返**，同时每绕一圈还往对话面板里塞一条
    「好的，执行吸盘指令」。吸盘是设备状态，不该走自然语言链路。
    """
    on = bool(data.get('on'))
    global_state['suck_on'] = on
    executor.suck_on = on
    if not on:
        global_state['holding'] = None
        executor.holding = None
    socketio.emit('robot_frame', {'type': 'suck', 'on': on})


@socketio.on('scene_update')
def on_scene_update(data):
    """场景变更同步"""
    objects = data.get('objects', [])
    global_state['scene_objects'] = objects
    # 执行器也要有一份：PICK/PLACE 靠它按坐标认物体（取名、取高度）
    executor.scene_objects = objects
    # 广播给所有客户端
    socketio.emit('scene_objects', {'objects': objects})


@socketio.on('clear_chat')
def on_clear_chat():
    """清空对话历史（仅当前会话）"""
    ai_gateway.clear_history(request.sid)
    emit('ai_reply', {'text': '对话历史已清空'})


@socketio.on('set_llm_key')
def on_set_llm_key(data):
    """用户在前端页面填入自己的智谱 API Key（按会话隔离，不落盘）。

    前端把 Key 发到这里，后端为该会话建一个独立 ZhipuAI 客户端；
    之后该会话的 AI 对话都走自己的 Key，互不串号。空 Key 视为清除。
    """
    key = ((data or {}).get('key', '') or '') if isinstance(data, dict) else ''
    ok = ai_gateway.set_session_key(request.sid, key)
    emit('llm_key_status', {
        'configured': ok,
        'message': '已配置，AI 智能助手可用' if ok else '配置失败：Key 为空或无效',
    })


# ────────────────────────── 启动 ──────────────────────────
def main():
    init_db()
    st = asr_engine.status()
    print("=" * 60)
    print("  埃夫特 ER3-600 六轴工业机器人 3D 仿真系统")
    print("=" * 60)
    print(f"  LLM 模型: glm-4-plus")
    print(f"  LLM 已配置: {ai_gateway._client is not None}")
    print(f"  ASR 依赖: {'已安装' if st['available'] else '未安装'}  "
          f"模型: {st['model']}  下载源: {st['endpoint']}")
    print(f"  TTS 可用: {tts_engine is not None}")
    print(f"  数据库: {DB_PATH}")
    print("=" * 60)

    # 后台预热 whisper：模型首次要联网下载几百 MB，等用户按下麦克风才开始
    # 下载的话 socket 回调会被阻塞好几分钟，前端只会看到转圈然后没反应。
    asr_engine.warmup_async()

    host = os.environ.get('HOST', '0.0.0.0')
    port = int(os.environ.get('PORT', 5000))
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)


if __name__ == '__main__':
    main()