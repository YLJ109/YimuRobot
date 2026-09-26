# -*- coding: utf-8 -*-
"""
AI 网关：自然语言 → DSL 程序
- 智谱 GLM-4-Plus + Function Calling
- 快慢双通道（快通道规则匹配 <50ms，慢通道 LLM 规划）
- **真正的 Function Calling 多轮闭环**：工具结果会回灌给模型，模型据此继续规划
- 校验-修复闭环（最多3次）
- 上下文裁剪（最近10轮 + 场景快照）
"""
import os
import re
import json
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Callable, Tuple

from validator import validator, ValidationResult
from kinematics import (
    fk_pose, HOME_JOINTS_DEG, JOINT_LIMITS_DEG,
    joint_limit_text, MAX_REACH_MM, TOOL_LENGTH_MM,
)

try:
    from zhipuai import ZhipuAI
    _HAS_ZHIPU = True
except ImportError:
    _HAS_ZHIPU = False


# ────────────────────────── 配置 ──────────────────────────
LLM_MODEL = os.environ.get("LLM_MODEL", "glm-4-plus")
LLM_TEMPERATURE = 0.1
LLM_MAX_TOKENS = 2048
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", 30))   # 单次请求超时（秒）
MAX_RETRY = 3            # LLM 网络失败的退避重试次数
MAX_TOOL_HOPS = 6        # 单轮对话里「模型请求工具 → 回灌结果 → 模型再决策」的最大跳数
MAX_HISTORY_ROUNDS = 10  # 上下文保留轮数

# API Key 从环境变量读取
ZHIPU_API_KEY = os.environ.get("ZHIPU_API_KEY", "")


# ────────────────────────── 快通道规则 ──────────────────────────
FAST_COMMANDS = {
    '停止': 'stop', '停下': 'stop', 'stop': 'stop',
    '暂停': 'pause', 'pause': 'pause',
    '继续': 'resume', 'resume': 'resume', '接着': 'resume',
    '回家': 'home', '回原点': 'home', '归位': 'home', 'home': 'home',
    '吸取': 'suck_on', '吸盘吸': 'suck_on',
    '放下': 'suck_off', '释放': 'suck_off', '松开': 'suck_off',
}

# 快通道短指令（单字/极短词，需精确匹配）
FAST_SHORT_COMMANDS = {
    '停': 'stop',
}

# 合法即时指令集合（工具调用返回值校验用）
QUICK_COMMANDS = {'stop', 'pause', 'resume', 'home', 'suck_on', 'suck_off'}

# 即时指令的中文回显
QUICK_COMMAND_LABELS = {
    'stop': '停止', 'pause': '暂停', 'resume': '继续',
    'home': '回原点', 'suck_on': '吸取', 'suck_off': '放下',
}

# 关键词唤醒词
WAKE_WORDS = ['小艺小艺', '小艺', '你好小艺']


@dataclass
class AiMessage:
    """AI 对话消息"""
    role: str   # user / assistant / system
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class AiResponse:
    """AI 响应结果"""
    text: str = ""                    # 中文回复
    explanation: str = ""             # 执行解释
    program: str = ""                 # DSL 程序
    program_valid: bool = False       # 程序是否校验通过
    quick_command: Optional[str] = None  # 快通道指令
    quick_commands: List[str] = field(default_factory=list)  # 一轮里的多个即时指令（按顺序执行）
    need_clarify: bool = False        # 是否需要澄清
    clarify_question: str = ""        # 澄清问题
    error: str = ""                   # 错误信息
    audio_url: str = ""               # TTS 音频URL


# ────────────────────────── Function Calling 工具集 ──────────────────────────
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_robot_program",
            "description": "提交 DSL 机器人程序并执行。当用户要求机器人执行动作（移动、抓取、放置等）时调用。"
                           "提交前请自查语法：每行一条指令，参数不能缺。",
            "parameters": {
                "type": "object",
                "properties": {
                    "program": {
                        "type": "string",
                        "description": "DSL 机器人程序代码，每行一条指令"
                    },
                    "explanation": {
                        "type": "string",
                        "description": "对程序的中文解释，说明将要执行什么操作"
                    }
                },
                "required": ["program", "explanation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "query_scene",
            "description": "重新拉取当前场景中的物体清单和机器人 TCP 位置。"
                           "系统提示里已带有场景快照，只有怀疑快照过期时才调用。",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "quick_command",
            "description": "发送即时指令（停止/回家/吸取/释放/暂停/继续）。"
                           "停止、暂停、继续**只能**用这个工具，DSL 里没有对应指令。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "enum": ["stop", "home", "suck_on", "suck_off", "pause", "resume"],
                        "description": "即时指令类型"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_clarification",
            "description": "当用户指令含糊不清时，向用户提问以澄清意图",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "向用户提出的澄清问题"
                    }
                },
                "required": ["question"]
            }
        }
    }
]

# DSL 指令关键字，用于从纯文本回复里抢救程序（见 _extract_program_from_text）
_DSL_LINE_RE = re.compile(
    r'^\s*(MOVEJ|MOVELP|PICK|PLACE|SUCK|WAIT|SPEED|HOME|LOOP|END)\b', re.IGNORECASE
)


class LLMError(RuntimeError):
    """LLM 调用失败（网络/鉴权/超时）"""
    pass


# ────────────────────────── 场景上下文 ──────────────────────────
class _SceneContext:
    """一次请求内的场景快照 + 组装给模型看的文本。

    坐标换算集中在这里 —— 前端场景系是 Y-up（Three.js），后端基座系是
    Z-up，两者混用会让模型写出「把 Z 填成物体高度」这种错程序。
    换算关系（RobotArm.group.rotation.x = -90°）：
        base_x = scene_x,  base_y = -scene_z,  base_z = scene_y
    """

    def __init__(self, scene_objects: List[dict], current_joints_deg: List[float],
                 holding: Optional[str]):
        self.scene_objects = scene_objects or []
        self.joints = list(current_joints_deg or HOME_JOINTS_DEG)
        self.holding = holding

    # ── 单个物体 → 基座系描述行 ──
    def object_lines(self) -> List[str]:
        lines = []
        for obj in self.scene_objects:
            pos = obj.get('position') or [0, 0, 0]
            size = obj.get('size') or [0, 0, 0]
            try:
                sx, sy, sz = float(pos[0]), float(pos[1]), float(pos[2])
                h = float(size[1])
            except (TypeError, ValueError, IndexError):
                continue
            # 场景系 → 基座系（只在水平面上，高度量不变）
            bx, by = sx, -sz
            base_z = sy - h / 2.0        # 物体底面（支撑面）高度
            top_z = sy + h / 2.0         # 物体顶面高度
            lines.append(
                f"  - {obj.get('name', '未命名')}（{obj.get('type', 'box')}，颜色 {obj.get('color', '未知')}）: "
                f"中心水平坐标 x={bx:.0f}, y={by:.0f}；支撑面高度 z={base_z:.0f}，顶面高度 z={top_z:.0f}；"
                f"尺寸 {size[0]:.0f}×{size[1]:.0f}×{size[2]:.0f}mm；"
                f"可抓取={'是' if obj.get('grabbable', True) else '否'}"
            )
        return lines

    def scene_text(self) -> str:
        lines = self.object_lines()
        return "物体清单（基座坐标系，单位 mm）:\n" + ("\n".join(lines) if lines else "  （场景中暂无物体）")

    def tcp_text(self) -> str:
        tcp = fk_pose(self.joints)
        return (
            f"TCP 法兰位姿: X={tcp['x']:.1f}mm, Y={tcp['y']:.1f}mm, Z={tcp['z']:.1f}mm, "
            f"A={tcp['a']:.1f}°, B={tcp['b']:.1f}°, C={tcp['c']:.1f}°"
        )


class AiGateway:
    """AI 网关主类"""

    def __init__(self):
        self._client = None
        if _HAS_ZHIPU and ZHIPU_API_KEY:
            self._client = ZhipuAI(api_key=ZHIPU_API_KEY)
        # 按会话(sid)隔离的客户端与对话历史 —— 每个用户在页面里填自己的 Key，
        # 互不串号、互不共享历史（遵循「客户端会话状态按 sid 隔离」原则）。
        # 全局 _client 作为兜底（后端 .env 配了 ZHIPU_API_KEY 时），方便演示。
        self._session_clients: Dict[str, Any] = {}
        self._session_history: Dict[str, List[AiMessage]] = {}

    # ────────────────────────── 按会话 API Key ──────────────────────────
    def set_session_key(self, sid: str, key: str) -> bool:
        """为某个会话设置用户自己的智谱 API Key，建一个独立客户端。

        返回是否配置成功（key 非空且 zhipuai 可用）。空 key 视为清除该会话 Key。
        不落盘、不写 .env，仅存于内存（会话级）。
        """
        if not sid:
            return False
        key = (key or "").strip()
        if not key:
            self._session_clients.pop(sid, None)
            return False
        if not _HAS_ZHIPU:
            return False
        try:
            self._session_clients[sid] = ZhipuAI(api_key=key)
        except Exception as exc:  # noqa: BLE001 - Key 非法时 SDK 可能抛错
            self._log(f"会话 {sid} 设置 Key 失败: {exc!r}")
            self._session_clients.pop(sid, None)
            return False
        self._session_history.setdefault(sid, [])
        return True

    def clear_session(self, sid: str):
        """断开 / 清对话时回收该会话的客户端与历史。"""
        self._session_clients.pop(sid, None)
        self._session_history.pop(sid, None)

    def get_client(self, sid: Optional[str]):
        """优先用该会话自己的 Key 建的客户端，其次用全局兜底。"""
        return self._session_clients.get(sid) or self._client

    def is_configured(self) -> bool:
        """是否有任一可用客户端（全局兜底 或 任一会话 Key）。"""
        return bool(self._client or self._session_clients)

    # ────────────────────────── 快通道 ──────────────────────────
    def try_fast_channel(self, text: str) -> Optional[AiResponse]:
        """快通道：规则匹配短指令，<50ms 返回"""
        # 去空格、转小写、去标点
        text_clean = re.sub(r'[，。！？\,\.\!\?\s]', '', text.strip().lower())
        # 短文本（<=4字符）尝试精确匹配单字指令
        if len(text_clean) <= 4:
            for keyword, cmd in FAST_SHORT_COMMANDS.items():
                if text_clean == keyword:
                    return self._build_fast_response(keyword, cmd)
        # 常规快通道匹配（关键词需完整出现且文本较短）
        if len(text_clean) <= 10:
            for keyword, cmd in FAST_COMMANDS.items():
                if keyword in text_clean:
                    return self._build_fast_response(keyword, cmd)
        return None

    def _build_fast_response(self, keyword: str, cmd: str) -> AiResponse:
        """构建快通道响应"""
        resp = AiResponse(
            text=f"好的，执行{keyword}指令",
            quick_command=cmd,
        )
        if cmd == 'home':
            resp.program = "HOME"
            resp.explanation = "回到初始位姿"
        elif cmd == 'suck_on':
            resp.program = "SUCK ON"
            resp.explanation = "开启吸盘"
        elif cmd == 'suck_off':
            resp.program = "SUCK OFF"
            resp.explanation = "关闭吸盘"
        resp.program_valid = True
        return resp

    # ────────────────────────── 慢通道（LLM） ──────────────────────────
    def process(
        self,
        user_text: str,
        scene_objects: List[dict],
        current_joints_deg: List[float],
        holding: Optional[str] = None,
        sid: Optional[str] = None
    ) -> AiResponse:
        """处理自然语言输入（主入口）

        sid：发起请求的会话 id。传入后会用该会话自己的 Key（若有）建客户端，
        并把对话历史隔离在该会话内；不传则回退全局 Key + 全局历史。
        """
        user_text = (user_text or "").strip()
        if not user_text:
            return AiResponse(text="请输入指令。")

        # 1. 先试快通道
        fast = self.try_fast_channel(user_text)
        if fast:
            self._add_history(sid, 'user', user_text)
            self._add_history(sid, 'assistant', fast.text)
            return fast

        # 2. 慢通道：LLM 规划
        if not self.get_client(sid):
            return AiResponse(
                error="LLM 未配置（缺少可用的 API Key），无法处理复杂指令",
                text="抱歉，AI 服务未配置。请在页面右上角「设置」里填入你自己的智谱 API Key。"
            )

        self._add_history(sid, 'user', user_text)

        ctx = _SceneContext(scene_objects, current_joints_deg, holding)
        messages = [{"role": "system", "content": self._build_system_prompt(ctx)}]
        messages.extend(self._get_trimmed_history(sid))

        # 3. 工具闭环 + 校验修复闭环
        #    「程序校验失败 → 回灌错误 → 模型修正」发生在 _run_tool_loop 内部，
        #    由 MAX_TOOL_HOPS 计一跳，不再走外层循环 ——
        #    否则外层 3 次 × 内层 4 跳 = 12 次请求，额度烧得很快还没结果。
        #    外层只负责 LLM 网络类失败的指数退避重试。
        last_error = ""
        for attempt in range(MAX_RETRY):
            try:
                resp = self._run_tool_loop(messages, ctx, sid)
            except LLMError as exc:
                last_error = str(exc)
                self._log(f"LLM 调用失败（第{attempt + 1}次）: {exc}")
                if attempt < MAX_RETRY - 1:
                    time.sleep(min(2 ** attempt, 4))   # 指数退避
                    continue
                fail = AiResponse(error=last_error, text=f"AI 服务不可用：{last_error}")
                self._add_history(sid, 'assistant', fail.text)
                return fail
            except Exception as exc:  # noqa: BLE001 - 绝不让异常冒到 socket 层
                self._log(f"AI 网关异常: {exc!r}")
                fail = AiResponse(error=str(exc), text=f"AI 处理出错：{exc}")
                self._add_history(sid, 'assistant', fail.text)
                return fail

            if resp.error == 'RETRY':
                last_error = resp.text
                continue
            self._add_history(sid, 'assistant', resp.text or resp.explanation or resp.clarify_question)
            return resp

        fail = AiResponse(
            error=last_error or "校验修复超过最大重试次数",
            text="抱歉，我没能生成可通过校验的程序。可以换一种说法，或者把任务拆成更小的步骤。"
        )
        self._add_history(sid, 'assistant', fail.text)
        return fail

    # ────────────────────────── 工具闭环 ──────────────────────────
    def _run_tool_loop(self, messages: List[dict], ctx: _SceneContext, sid: Optional[str] = None) -> AiResponse:
        """真正的 Function Calling 循环。

        ⚠️ 这里过去是「一次调用就结束」的写法：拿到 tool_calls 后只是把参数
        读出来返回，从不把工具结果回灌给模型。于是模型一旦先调用 query_scene
        探路，整轮对话就停在「已查询场景信息」上，既不生成代码也不执行 ——
        用户看到的就是「智能助手不真实回答、不生成代码」。现在按标准
        多轮 tool 协议走：模型要什么就给什么，给了再让它继续决策。
        """
        last_validation_error = ""
        for _hop in range(MAX_TOOL_HOPS):
            msg = self._chat(messages, sid)
            tool_calls = getattr(msg, 'tool_calls', None) or []

            # 把助手的这一轮（含 tool_calls）写回上下文，符合工具调用协议
            messages.append(self._assistant_message_dict(msg))

            if not tool_calls:
                text = (msg.content or '').strip()
                if not text:
                    return AiResponse(text="我需要更多信息才能继续。请再说明一下要做什么。",
                                      need_clarify=True,
                                      clarify_question="请再说明一下要做什么？")
                # 兜底：模型有时把程序写在正文的代码块里而没调工具。
                # 直接抽出来校验，能过就跑 —— 否则用户看到一段"看起来能跑"的代码却什么都没发生。
                program = self._extract_program_from_text(text)
                if program:
                    return self._finish_with_program(program, text, ctx)
                # 模型有时不用 ask_clarification 工具、直接在正文里反问。
                # 前端靠 needClarify 决定要不要进"等待补充信息"状态，所以这里
                # 兜一层：正文里带问号的纯文本回复，按澄清处理。
                if self._looks_like_question(text):
                    return AiResponse(text=text, need_clarify=True, clarify_question=text)
                return AiResponse(text=text)

            # 一轮里可能同时给出多个工具调用，**按顺序**全部处理，不再互相覆盖。
            # 旧实现用 dict[name]=args 去重，第二个 quick_command 会被直接丢掉 ——
            # 用户说「张开吸盘然后停」，结果只执行了吸盘那半句。
            calls: List[dict] = []
            for tc in tool_calls:
                name = getattr(tc.function, 'name', '') or ''
                try:
                    args = json.loads(getattr(tc.function, 'arguments', '') or '{}')
                except (json.JSONDecodeError, TypeError):
                    args = {}
                if name:
                    calls.append({
                        'name': name, 'args': args,
                        'id': getattr(tc, 'id', f'call_{len(calls)}'),
                    })
            names = [c['name'] for c in calls]

            # ① 提交程序：最高优先级
            prog_call = next((c for c in calls if c['name'] == 'execute_robot_program'), None)
            if prog_call is not None:
                program = (prog_call['args'].get('program') or '').strip()
                explanation = (prog_call['args'].get('explanation') or '').strip()
                if not program:
                    messages.append({
                        "role": "user",
                        "content": "execute_robot_program 的 program 参数为空，请重新提交完整程序。"
                    })
                    continue
                result = self._finish_with_program(program, explanation, ctx, retry_hint=True)
                if result.error != 'RETRY':
                    return result
                # 校验没过：回灌错误，让模型在下一跳修正
                last_validation_error = result.text
                messages.append({"role": "user", "content": result.text})
                continue

            # ② 澄清
            clar_call = next((c for c in calls if c['name'] == 'ask_clarification'), None)
            if clar_call is not None:
                q = clar_call['args'].get('question') or '请澄清您的需求'
                return AiResponse(text=q, need_clarify=True, clarify_question=q)

            # ③ 即时指令（可能一轮给多条，全部保留，按顺序执行）
            quick_cmds = [c['args'].get('command', '') for c in calls
                          if c['name'] == 'quick_command']
            quick_cmds = [c for c in quick_cmds if c in QUICK_COMMANDS]
            if quick_cmds:
                labels = "、".join(QUICK_COMMAND_LABELS.get(c, c) for c in quick_cmds)
                return AiResponse(
                    text=f"好的，依次执行：{labels}",
                    quick_command=quick_cmds[0],
                    quick_commands=quick_cmds,
                )
            if any(c['name'] == 'quick_command' for c in calls):
                bad = ", ".join(
                    repr(c['args'].get('command')) for c in calls if c['name'] == 'quick_command'
                )
                messages.append({
                    "role": "user",
                    "content": f"quick_command 只接受 {sorted(QUICK_COMMANDS)}，收到的是 {bad}。"
                })
                continue

            # ④ 查询场景：回灌**真实**数据后继续下一跳
            if 'query_scene' in names:
                payload = "\n".join([ctx.scene_text(), ctx.tcp_text(),
                                     f"当前持物: {ctx.holding or '无'}"])
                for c in calls:
                    if c['name'] == 'query_scene':
                        messages.append({
                            "role": "tool",
                            "tool_call_id": c['id'],
                            "content": payload,
                        })
                continue

            # ⑤ 未知工具：明确告知，别让模型空等
            messages.append({
                "role": "user",
                "content": f"不支持的工具调用：{', '.join(names) or '(空)'}。可用工具："
                           "execute_robot_program / query_scene / quick_command / ask_clarification。"
            })

        # 跳到上限还没拿到合法程序 —— 把**最后一次的校验原因**带给用户，
        # 只说「处理不了」等于把「为什么不可达」这条最有用的信息吞掉了。
        if last_validation_error:
            return AiResponse(
                text=("我没能生成可通过校验的程序，最后一次的校验结果是：\n\n"
                      f"{last_validation_error}\n\n"
                      "可以试试把目标改到离机器人更近、或偏离正后方的位置。"),
                error="TOOL_HOP_LIMIT",
            )
        return AiResponse(
            text="这个任务需要太多轮规划，我暂时处理不了。请把它拆成更明确的单步指令。",
            error="TOOL_HOP_LIMIT"
        )

    def _finish_with_program(self, program: str, explanation: str,
                             ctx: _SceneContext, retry_hint: bool = False) -> AiResponse:
        """校验程序；通过就返回可执行结果，不通过返回 error='RETRY' + 修复提示。

        ⚠️ ctx 必须**显式传参**，不能挂到 self 上：ai_gateway 是进程级单例，
        而 SocketIO 用线程模型，两个用户同时提问时 self 上的上下文会互相覆盖，
        表现为「A 的程序用 B 的场景校验」。
        """
        try:
            val: ValidationResult = validator.validate(
                program, ctx.scene_objects, ctx.joints
            )
        except Exception as exc:  # noqa: BLE001
            self._log(f"校验器异常: {exc!r}")
            return AiResponse(error='RETRY', text=f"程序校验过程出错：{exc}")

        if val.valid:
            return AiResponse(
                text=explanation or "已生成程序并开始执行",
                explanation=explanation,
                program=program,
                program_valid=True,
            )

        err_text = validator.format_errors_for_llm(val.errors)
        self._log(f"程序校验失败:\n{err_text}")
        if not retry_hint:
            return AiResponse(error='RETRY', text=err_text)
        return AiResponse(
            error='RETRY',
            text=(f"{err_text}\n\n请调用 execute_robot_program 重新提交修正后的完整程序。"
                  "注意：停止/暂停/继续不属于 DSL，请改用 quick_command 工具。"),
            program=program,      # 保留原始程序，前端可展示「待校验」版本
            program_valid=False,
        )

    # ────────────────────────── LLM 调用 ──────────────────────────
    def _chat(self, messages: List[dict], sid: Optional[str] = None):
        """单次 LLM 调用（带超时）。失败抛 LLMError。"""
        client = self.get_client(sid)
        try:
            response = client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=TOOLS,
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
                timeout=LLM_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001 - SDK 异常类型不稳定，统一转成 LLMError
            raise LLMError(str(exc)) from exc

        choices = getattr(response, 'choices', None)
        if not choices:
            raise LLMError("LLM 返回体中没有 choices")
        return choices[0].message

    @staticmethod
    def _assistant_message_dict(msg) -> dict:
        """把 SDK 的 assistant 消息转成协议要求的 dict（含 tool_calls）。"""
        out: Dict[str, Any] = {"role": "assistant", "content": getattr(msg, 'content', '') or ""}
        tcs = getattr(msg, 'tool_calls', None) or []
        if tcs:
            out["tool_calls"] = [
                {
                    "id": getattr(tc, 'id', f'call_{i}'),
                    "type": "function",
                    "function": {
                        "name": getattr(tc.function, 'name', ''),
                        "arguments": getattr(tc.function, 'arguments', '') or '{}',
                    },
                }
                for i, tc in enumerate(tcs)
            ]
        return out

    @staticmethod
    def _looks_like_question(text: str) -> bool:
        """纯文本回复是否是一次「反问」。

        只在没有工具调用、也没有程序时才调用，命中面很窄：
        以问号收尾，或含明显的询问意图词。
        """
        t = (text or '').strip()
        if not t:
            return False
        if t.endswith('？') or t.endswith('?'):
            return True
        markers = ('请问', '哪个', '哪一个', '哪一个物体', '是指', '您说的', '你是指')
        return any(m in t for m in markers)

    def _extract_program_from_text(self, text: str) -> str:
        """从纯文本回复里抢救 DSL 程序。

        模型偶尔会把程序写在正文的 ``` 代码块里而不调用工具。这段代码
        在前端看起来完全合法，但后端永远不会执行 —— 属于最坑的一类
        静默失败。这里做一次轻量提取，只认「至少一半的行是已知 DSL 指令」
        的代码块，宁可漏也不要误把一段说明文字当程序跑。
        """
        blocks = re.findall(r'```[a-zA-Z]*\n(.*?)```', text, re.DOTALL)
        if not blocks:
            # 没有围栏时，看整段里有没有连续的 DSL 行
            blocks = [text]
        best = ""
        for block in blocks:
            lines = [ln for ln in block.split('\n')]
            code = [ln for ln in lines if ln.strip() and not ln.strip().startswith('#')]
            if not code:
                continue
            hits = sum(1 for ln in code if _DSL_LINE_RE.match(ln))
            if hits >= max(1, len(code) * 0.6) and hits > 0:
                body = "\n".join(ln for ln in lines if ln.strip())
                if len(body) > len(best):
                    best = body
        return best

    # ────────────────────────── System Prompt ──────────────────────────
    def _build_system_prompt(self, ctx: _SceneContext) -> str:
        """构建系统提示词（注入实时场景状态 + 坐标换算后的目标点）"""
        obj_lines = ctx.object_lines()
        objects_str = "\n".join(obj_lines) if obj_lines else "  （场景中暂无物体）"

        # 区域标记（基座系）。必须与 SceneManager._createZoneMarkers() 保持一致：
        # 场景系 (±480, y, 140) / (0, y, -560) → 基座系 (±480, -140) / (0, 560)
        zones_str = (
            "  - A区: 中心 x=-480, y=-140, 支撑面 z=0（地面色块，红色方块工位）\n"
            "  - B区: 中心 x=+480, y=-140, 支撑面 z=0（地面色块，蓝色方块工位）\n"
            "  - 托盘: 中心 x=0, y=+560, 支撑面 z=0（地面色块，绿色圆柱工位）"
        )

        # 逐轴真实软限位，不要把 J1 的 ±170 当成所有轴的上限
        limits_str = "；".join(
            f"J{i + 1} {joint_limit_text(i)}" for i in range(6)
        )

        prompt = f"""你是埃夫特 ER3-600 六轴工业机器人的 AI 控制助手，运行在一个**纯软件仿真**环境里。你的任务是把用户的自然语言指令转换成 DSL 机器人程序并提交执行。

## 当前场景状态
### 物体清单（基座坐标系，单位 mm）
{objects_str}

### 区域工位（基座坐标系）
{zones_str}

### 机器人状态
- {ctx.tcp_text()}
- 当前持物: {ctx.holding or '无'}
- 当前关节角: {[round(j, 1) for j in ctx.joints]}

## 坐标系说明（非常重要，写错坐标程序就跑偏）
- 场景快照里的物体位置已换算成**基座系**：X 向前、Y 向左、Z 向上，单位 mm。
- DSL 里所有坐标都是这个基座系。物体清单里已经给出换算好的 x / y，**直接抄**，不要再自己换算。
- z 一律表示**高度**。地面上摆放的物体，支撑面高度就是 0。

## DSL 指令集（严格遵守语法）
- MOVEJ J1=度,J2=度,J3=度,J4=度,J5=度,J6=度    关节空间运动，**六个参数一个都不能少**
- MOVELP X=mm,Y=mm,Z=mm,A=度,B=度,C=度          笛卡尔直线运动，**六个参数一个都不能少**
- PICK target=[x,y,z]                            智能抓取（自动生成抬升-下探-吸取轨迹）
- PLACE target=[x,y,z]                           智能放置（自动生成下探-释放-抬升轨迹）
- SUCK ON / SUCK OFF                             吸盘开关
- WAIT 秒                                        延时（单条上限 60s）
- SPEED 1-100                                    速度百分比（默认 50；演示类程序建议在首行加 `SPEED 100`，动作更利落）
- HOME                                           回机械零位
- LOOP n ... END                                 循环 n 次（展开后总指令数不得超过 20000）
- # 注释                                         注释行

## PICK / PLACE 的 target 怎么填
- x, y = 物体的**水平中心坐标**，直接抄物体清单里的 x / y。
- z = 物体**支撑面**高度（放在地面上就填 0）。物体的高度由执行器自动补上，**你不需要自己加吸盘长度**。
- 例：把地面上的红色方块搬到 B 区 → `PICK target=[-480,-140,0]` 然后 `PLACE target=[480,-140,0]`

## 机器人约束
- 关节软限位（逐轴，不要当成统一的 ±170）：{limits_str}
- 最大工作半径 {MAX_REACH_MM:.0f}mm（J2 轴到腕心）。工作范围大致在 Z=0~600mm、水平半径 500mm 以内；
  水平坐标离原点越近，可用的 Z 上限越低（Z 太高时腕心会被顶出可达球）。
- 抓取/放置姿态由执行器自动使用「工具朝下」，你不需要也不应该手写抓取姿态角。
- 注意 J1 在 180° 附近有一段死区：目标点落在 x≈0、y≈0（正后方）的窄楔形里时不可达，尽量让目标点偏离正后方。

## 规则
1. 只操作物体清单里真实存在的物体，不要凭空创造。
2. 先想清楚要动哪个物体、放到哪里，再决定用 PICK/PLACE 还是 MOVEJ/MOVELP。
3. **不要自己写抬升/下探的中间点**：PICK/PLACE 内部已经包含「抬到安全高度→下探→动作→抬回」的完整轨迹，
   你额外加 MOVELP 只会写出撞到可达边界外的点（例如 Z=800 这种请求）。
4. 需要调整机器人姿态、走到某个位置时用 MOVELP（指定完整姿态）或 MOVEJ（指定关节角）。
5. **停止 / 暂停 / 继续不属于 DSL**，必须用 quick_command 工具，不要写进程序里。
   一句话里包含多个即时动作（例如"张开吸盘然后停"）时，请在**同一次回复里多次调用** quick_command，
   不要只执行前半句。
6. 用户指令含糊（"那个东西""这边"）时，调用 ask_clarification 反问，不要猜。
7. 程序里的角度单位是度、长度单位是毫米。
8. 生成程序后必须调用 execute_robot_program 提交；如果返回校验错误，按提示修正后重新提交。

## 输出要求
- 能用程序解决的就调用 execute_robot_program，explanation 用中文简要说明动作。
- 纯问答（不涉及机器人动作）才直接用文本回答。
- 程序中可用 # 注释标注每一步。
"""
        return prompt

    # ────────────────────────── 历史管理（按会话隔离） ──────────────────────────
    def _add_history(self, sid, role: str, content: str):
        if not content:
            return
        self._session_history.setdefault(sid or '_global', []).append(
            AiMessage(role=role, content=content)
        )

    def _get_trimmed_history(self, sid=None) -> List[dict]:
        """裁剪上下文：保留最近 MAX_HISTORY_ROUNDS 轮（按会话）"""
        hist = self._session_history.get(sid or '_global', [])
        recent = hist[-(MAX_HISTORY_ROUNDS * 2):]
        return [{"role": m.role, "content": m.content} for m in recent]

    def get_history(self, sid=None) -> List[dict]:
        return [{"role": m.role, "content": m.content, "timestamp": m.timestamp}
                for m in self._session_history.get(sid or '_global', [])]

    def clear_history(self, sid=None):
        if sid:
            self._session_history.get(sid, []).clear()
        else:
            for h in self._session_history.values():
                h.clear()

    def _log(self, msg: str):
        print(f"[AiGateway] {msg}")


# 全局单例
ai_gateway = AiGateway()
