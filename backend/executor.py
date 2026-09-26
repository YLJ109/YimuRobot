# -*- coding: utf-8 -*-
"""
虚拟机器人控制器 / 程序执行器
- 轨迹生成（关节插补 / 笛卡尔直线插补）
- 60Hz 帧推送
- 运行/停止/暂停/单步
- PICK/PLACE 自动轨迹生成
- SUCK 吸盘控制
"""
import math
import os
import time
import threading
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Dict, Any
from enum import Enum

from parser import DslInstruction, parser, LoopExpansionError
import collision
from kinematics import (
    solver, fk_position, fk_pose, forward_kinematics,
    HOME_JOINTS_DEG, JOINT_LIMIT_DEG, JOINT_LIMITS_DEG,
    deg2rad, rad2deg, TOOL_LENGTH_MM, TOOL_DOWN_RPY,
    find_scene_object, scene_object_height, DEFAULT_OBJECT_HEIGHT_MM,
)


class ExecState(Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FINISHED = "finished"
    ERROR = "error"


@dataclass
class TrajectoryFrame:
    """单帧轨迹数据"""
    joints_deg: List[float]   # 6个关节角度
    suck_on: bool = False
    line: int = 0             # 当前执行行
    holding: Optional[str] = None  # 持物名称


class RobotExecutor:
    """虚拟机器人执行器"""

    FRAME_RATE = 60.0          # 推送帧率 Hz
    FRAME_DT = 1.0 / 60.0      # 帧间隔 秒
    DEFAULT_SPEED = 50         # 默认速度百分比

    # ── 安全上限（硬编码兜底，可用环境变量覆盖）──
    #  这些值曾只写在 .env.example 里、代码从不读取，等于没有：
    #  LOOP 999999 能展开成十几万条指令直接把内存吃光，WAIT 3600 能把
    #  执行线程挂死一小时，程序本身也没有总时长上限。
    MAX_WAIT_SECONDS = float(os.environ.get("MAX_WAIT_SECONDS", 60))
    MAX_EXEC_SECONDS = float(os.environ.get("MAX_EXEC_SECONDS", 300))

    # 吸/放动作在接触高度的停留时间（秒）。
    #  前端 3D 是在追赶遥测帧的，唇口要等画面插值追上来才判定吸附成功。
    #  0.3s 在低帧率机器上不够（画面只追到一半就抬走了），给到 0.45s。
    GRASP_SETTLE_SECONDS = 0.45

    # 执行期碰撞复查开关（COLLISION_CHECK=0 可关掉，用于排查"是不是误报"）。
    COLLISION_CHECK = os.environ.get("COLLISION_CHECK", "1").lower() not in ("0", "false", "no")

    def __init__(self):
        self.state = ExecState.IDLE
        self.joints_deg: List[float] = list(HOME_JOINTS_DEG)
        self.speed_percent: int = self.DEFAULT_SPEED
        self.suck_on: bool = False
        self.holding: Optional[str] = None  # 持物名称

        # 场景物体快照（供 PICK 就近认物体用）。由 app.py 在场景变更时同步。
        self.scene_objects: List[dict] = []

        self._instructions: List[DslInstruction] = []
        self._frame_queue: List[TrajectoryFrame] = []
        self._thread: Optional[threading.Thread] = None
        self._pause_event = threading.Event()
        self._pause_event.set()  # 非暂停状态
        self._stop_flag = threading.Event()
        # 互斥：run() 会替换 _instructions 并起新线程。没有这把锁时，
        # 连点两次「运行」就有两个线程同时推帧、抢写 self.joints_deg，
        # 画面表现为机械臂在两套轨迹之间抽搐，且 on_finished 会触发两次。
        self._run_lock = threading.Lock()

        # ── 碰撞检测状态（见 collision.py）──
        #  _collision_ignore：本次动作要排除的物体名。正在抓取/放置的目标必然
        #  与吸盘"接触"，那不是穿透；漏掉这一步会把每一条正常搬运都判成碰撞。
        self._collision_ignore: set = set()
        #  因碰撞中止程序时，用这句话替代笼统的"程序已停止"
        self._abort_message: Optional[str] = None

        # 回调：帧推送
        self.on_frame: Optional[Callable[[TrajectoryFrame], None]] = None
        # 回调：执行完成
        self.on_finished: Optional[Callable[[bool, str], None]] = None
        # 回调：日志
        self.on_log: Optional[Callable[[str], None]] = None

        # 单步模式
        self._step_mode: bool = False
        self._step_event = threading.Event()

        # 本次执行的起始时刻（用于整程序超时兜底）
        self._exec_started_at: float = 0.0

    # ────────────────────────── 公共接口 ──────────────────────────
    def load_program(self, code: str):
        """加载程序（解析+展开LOOP）"""
        result = parser.parse(code)
        if not result.ok:
            return False, result.errors
        try:
            self._instructions = parser.expand_loops(result.instructions)
        except LoopExpansionError as exc:
            # LOOP 展开量超上限属于「程序非法」，不能让它冒泡成 500
            return False, [str(exc)]
        return True, []

    def run(self, code: str = ""):
        """开始执行"""
        if code:
            ok, errs = self.load_program(code)
            if not ok:
                if self.on_finished:
                    self.on_finished(False, f"解析失败: {errs}")
                return

        if not self._instructions:
            if self.on_finished:
                self.on_finished(False, "没有可执行的指令")
            return

        # 上一轮还在跑就先停掉：不允许两个执行线程同时改 joints_deg
        with self._run_lock:
            if self.state in (ExecState.RUNNING, ExecState.PAUSED) and \
                    self._thread is not None and self._thread.is_alive():
                self._log("检测到上一次执行仍在进行，已先停止")
                self.stop()
                # 给旧线程一点时间退出循环（它每个插补步都会检查 stop_flag）
                self._thread.join(timeout=1.0)

            self.state = ExecState.RUNNING
            self._stop_flag.clear()
            self._pause_event.set()
            self._step_mode = False
            self._exec_started_at = time.monotonic()
            self._collision_ignore = set()
            self._abort_message = None

            self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """停止执行"""
        self._stop_flag.set()
        self._pause_event.set()
        self._step_event.set()
        self.state = ExecState.STOPPED

    def pause(self):
        """暂停"""
        if self.state == ExecState.RUNNING:
            self._pause_event.clear()
            self.state = ExecState.PAUSED

    def resume(self):
        """继续"""
        if self.state == ExecState.PAUSED:
            self._pause_event.set()
            self.state = ExecState.RUNNING

    def step(self):
        """单步执行"""
        self._step_mode = True
        self._step_event.set()
        if self.state == ExecState.IDLE:
            self.state = ExecState.RUNNING
            self._stop_flag.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def set_joint(self, index: int, degree: float):
        """手动设置单个关节

        限位必须取**该轴自己的**范围：J3 官方式是 -65~+185，用对称的
        ±JOINT_LIMIT_DEG(=170) 会把 170~185 这一段合法角度悄悄削掉。
        """
        if 0 <= index < 6:
            lo, hi = JOINT_LIMITS_DEG[index]
            self.joints_deg[index] = max(lo, min(hi, degree))

    def set_joints_all(self, joints_deg: List[float]) -> bool:
        """手动设置整组关节角（笛卡尔逆解 / 回零 / 竖直位等批量下发）

        逐轴走 set_joint 的限位逻辑；长度不是 6 视为非法输入，直接忽略。
        """
        if not joints_deg or len(joints_deg) != 6:
            return False
        for i in range(6):
            self.set_joint(i, float(joints_deg[i]))
        return True

    def get_tcp_pose(self) -> dict:
        """获取当前 TCP 位姿"""
        return fk_pose(self.joints_deg)

    # ────────────────────────── 执行主循环 ──────────────────────────
    def _run_loop(self):
        """执行主循环"""
        try:
            for instr in self._instructions:
                if self._stop_flag.is_set():
                    break
                # 等待暂停解除
                self._pause_event.wait()
                if self._stop_flag.is_set():
                    break
                # 单步等待
                if self._step_mode:
                    self._step_event.clear()
                    self._step_event.wait()
                    if self._stop_flag.is_set():
                        break

                self._execute_instruction(instr)

            if not self._stop_flag.is_set():
                self.state = ExecState.FINISHED
                if self.on_finished:
                    self.on_finished(True, "程序执行完成")
            else:
                if self.on_finished:
                    self.on_finished(False, self._abort_message or "程序已停止")
        except Exception as e:
            self.state = ExecState.ERROR
            if self.on_finished:
                self.on_finished(False, f"执行错误: {str(e)}")
        finally:
            if self.state not in (ExecState.STOPPED, ExecState.ERROR):
                self.state = ExecState.FINISHED

    def _execute_instruction(self, instr: DslInstruction):
        """执行单条指令，生成并推送轨迹帧"""
        op = instr.op
        self._log(f"执行第{instr.line}行: {instr.raw.strip()}")

        if op == 'MOVEJ':
            self._exec_movej(instr.params['joints'], instr.line)
        elif op == 'MOVELP':
            self._exec_movelp(instr.params['pos'], instr.params['rpy'], instr.line)
        elif op == 'PICK':
            self._exec_pick(instr.params['target'], instr.line)
        elif op == 'PLACE':
            self._exec_place(instr.params['target'], instr.line)
        elif op == 'SUCK':
            self.suck_on = instr.params['on']
            if not self.suck_on:
                self.holding = None
            self._push_frame(self.joints_deg, instr.line)
            self._log(f"吸盘 {'ON' if self.suck_on else 'OFF'}")
        elif op == 'WAIT':
            self._exec_wait(instr.params['seconds'], instr.line)
        elif op == 'SPEED':
            self.speed_percent = instr.params['value']
            self._log(f"速度设为 {self.speed_percent}%")
        elif op == 'HOME':
            self._exec_movej(list(HOME_JOINTS_DEG), instr.line)

    # ────────────────────── 碰撞复查（执行期） ──────────────────────
    #
    # 几何校验挡在两道口子上：
    #   ① validator（校验期）—— AI 刚生成完就判，带修复建议退回模型；
    #   ② 这里（执行期）—— 插补的每个中间位姿都复查。即使校验时用的是旧快照、
    #      或者有人手改了程序，也不会真的把方块撞穿。
    # 撞到就整段停下（真机也是急停），并把原因带进 program_finished。
    def _ignored_objects(self) -> set:
        """不参与碰撞判定的物体名。

        正在抓取/放置的目标、以及吸盘手里已经吸住的那个 —— 与吸盘"接触"
        是这类动作的正常状态，不是穿透。漏掉这步会把每条搬运都判成碰撞。
        """
        names = set(self._collision_ignore)
        if self.holding:
            names.add(self.holding)
        return names

    def _check_collision(self, joints: List[float], line: int) -> bool:
        """当前位姿是否碰撞；True 表示已中止（调用方必须立刻 return）"""
        if not self.COLLISION_CHECK:
            return False
        held_size = None
        if self.holding:
            for obj in self.scene_objects:
                if obj.get('name') == self.holding:
                    held_size = obj.get('size')
                    break
        hits = collision.check_pose(
            joints, self.scene_objects,
            ignore_names=self._ignored_objects(),
            held_size=held_size,
        )
        if not hits:
            return False
        desc = collision.describe(hits)
        self._log(f"第{line}行 检测到碰撞（{desc}），已停止执行")
        self._abort_message = f"检测到碰撞：{desc}"
        self._stop_flag.set()
        return True

    # ────────────────────────── 运动指令 ──────────────────────────
    def _exec_movej(self, target_joints: List[float], line: int):
        """关节空间插补运动"""
        start = list(self.joints_deg)
        end = list(target_joints)
        duration = self._estimate_duration(start, end)
        steps = max(int(duration * self.FRAME_RATE), 1)

        for i in range(1, steps + 1):
            if self._stop_flag.is_set():
                return
            self._pause_event.wait()
            t = i / steps
            # S 曲线插补（平滑加减速）
            s = self._s_curve(t)
            curr = [a + (b - a) * s for a, b in zip(start, end)]
            self.joints_deg = curr
            if self._check_collision(curr, line):
                return
            self._push_frame(curr, line)
            time.sleep(self.FRAME_DT / (self.speed_percent / 100.0))

    def _travel_to(self, pos: List[float], rpy: List[float], line: int):
        """横移到位姿正上方：用**关节空间**插补（MOVJ 语义）。

        为什么这一小段不用 MOVELP：从 A 区走到 B 区的那条**直线**会横穿基座
        正上方，而工具朝下时「r < ~150mm 且 z ≈ 360mm」正好是工作空间的内孔
        —— 逐点逆解会成片失败（实测 960mm 行程里约 1/4 的插补点解不出来），
        画面上就是手臂卡在半路然后瞬移过去。真机程序也是这么分的：
        点位之间用 MOVJ，只有下探/贴合这种"必须走直线"的段才用 MOVL。

        关节插补天然不会有内孔问题：两端都能解，中间就都能解
        （关节线性插值不会越过限位盒）。路径是弯曲的，所以每一步照样过
        碰撞复查 —— 弯曲路径反而更贴近真机的避让走法。
        """
        sol = solver.inverse_kinematics(
            pos[0], pos[1], pos[2], rpy, self.joints_deg
        )
        if sol is None:
            self._log(
                f"第{line}行 横移目标 ({pos[0]:.0f},{pos[1]:.0f},{pos[2]:.0f}) "
                f"逆解失败，未执行"
            )
            return
        self._exec_movej(sol, line)

    def _exec_movelp(self, pos: List[float], rpy: List[float], line: int):
        """笛卡尔直线运动（走IK）

        ⚠️ solver.inverse_kinematics 是**位置参数**签名：
              inverse_kinematics(x, y, z, rpy_deg, current_joints_deg)
        旧代码写成 inverse_kinematics(curr_pos, curr_rpy, self.joints_deg)，
        把列表当 x 传进去 → float(list) 抛 TypeError → 被 except 吞成 None
        → 每一次插补都被判定「IK 失败」→ 机器人一步不动，还不报错。
        这类「静默失败」是整条笛卡尔链路失效的根因，改动时务必保持解包写法。
        """
        start_pose = fk_pose(self.joints_deg)
        start_pos = [start_pose['x'], start_pose['y'], start_pose['z']]
        end_pos = pos
        start_rpy = [start_pose['a'], start_pose['b'], start_pose['c']]
        end_rpy = rpy

        dist = np.linalg.norm(np.array(end_pos) - np.array(start_pos))
        duration = dist / (200.0 * self.speed_percent / 100.0)  # 200mm/s 满速
        steps = max(int(duration * self.FRAME_RATE), 1)

        failed = 0
        for i in range(1, steps + 1):
            if self._stop_flag.is_set():
                return
            self._pause_event.wait()
            t = i / steps
            s = self._s_curve(t)
            # 线性插补位置
            curr_pos = [a + (b - a) * s for a, b in zip(start_pos, end_pos)]
            curr_rpy = [a + (b - a) * s for a, b in zip(start_rpy, end_rpy)]
            # IK 解算
            ik_sol = solver.inverse_kinematics(
                curr_pos[0], curr_pos[1], curr_pos[2],
                curr_rpy, self.joints_deg,
            )
            if ik_sol:
                self.joints_deg = ik_sol
                if self._check_collision(ik_sol, line):
                    return
                self._push_frame(ik_sol, line)
            else:
                failed += 1
            time.sleep(self.FRAME_DT / (self.speed_percent / 100.0))

        # 静默失败必须说出来：整段插补一次都没解出来，等于这条指令没执行
        if failed == steps:
            self._log(
                f"第{line}行 MOVELP 未执行：目标 "
                f"({end_pos[0]:.0f},{end_pos[1]:.0f},{end_pos[2]:.0f}) 全部插补点逆解失败"
                f"（姿态 A={end_rpy[0]:.0f} B={end_rpy[1]:.0f} C={end_rpy[2]:.0f}）"
            )
        elif failed:
            self._log(f"第{line}行 MOVELP 部分插补点逆解失败 {failed}/{steps}（已跳过）")

    def _exec_pick(self, target: List[float], line: int):
        """智能抓取：抬高→下探→吸取→抬升

        坐标语义（与 System Prompt 里的说明必须一致）：
          target = [x, y, z]
            x, y —— 物体中心在**基座系**下的水平坐标
            z    —— 物体**支撑面**的高度（放在地面上就是 0）

        吸盘接触面 = 物体顶面 = z + 物体高度。物体高度从场景快照里按
        水平坐标就近取；取不到就退化成「接触面 = z」，至少不会撞下去。
        这样 DSL 只描述「抓地面上的方块」，不需要 LLM 自己算高度。
        """
        x, y, z = target
        obj = self._object_at(x, y)
        if obj is None:
            # 场景快照为空或坐标对不上。这时如果按高度 0 去下探，唇口会直接
            # 落到支撑面（也就是扎进物体里），而画面上看不出报错。
            self._log(
                f"第{line}行 PICK：({x:.0f},{y:.0f}) 附近没有匹配物体"
                f"（场景快照 {len(self.scene_objects)} 个），"
                f"按默认高度 {DEFAULT_OBJECT_HEIGHT_MM:.0f}mm 估算接触面"
            )
        contact_z = z + scene_object_height(obj, DEFAULT_OBJECT_HEIGHT_MM)
        approach_z = collision.carry_height(contact_z)
        grasp_z = contact_z + TOOL_LENGTH_MM
        # 抓取全程把**目标物体**排除在碰撞判定外：吸盘必须贴上去"接触"它，
        # 那是抓取本身而不是穿透。旁边的物体照常参与判定。
        obj_name = self._resolve_object_name(x, y)
        self._collision_ignore = {obj_name}
        try:
            # 1. 走到目标正上方安全高度（横移用关节插补，见 _travel_to）
            self._travel_to([x, y, approach_z], list(TOOL_DOWN_RPY), line)
            if self._stop_flag.is_set():
                return
            # 2. 下探到接触高度（这一段必须走直线：吸盘要对准物体顶面）
            self._exec_movelp([x, y, grasp_z], list(TOOL_DOWN_RPY), line)
            if self._stop_flag.is_set():
                return
            # 3. 吸取
            self.suck_on = True
            self.holding = obj_name
            self._push_frame(self.joints_deg, line)
            self._log(f"吸取物体: {self.holding}")
            time.sleep(self.GRASP_SETTLE_SECONDS)
            # 4. 垂直抬升（直线：手里的物体不该横着蹭到别的东西）
            self._exec_movelp([x, y, approach_z], list(TOOL_DOWN_RPY), line)
        finally:
            self._collision_ignore = set()

    def _exec_place(self, target: List[float], line: int):
        """智能放置：避让落点→下探→释放→抬升（坐标语义同 PICK）

        接触面 = 支撑面 + **手里这个物体**的高度 —— 这样把方块放到地面
        （z=0）时，方块底面正好落在地面上，而不是半截陷进地里。

        落点若被别的物体占着（典型：把红方块放到已经有蓝方块的 B 区），
        先自动避让到最近的空位 —— 否则两个方块会叠在同一个坐标上，画面上
        就是互相穿透。
        """
        x, y, z = target
        held_name = self.holding
        held_size = self._held_size() or [0.0, 0.0, 0.0]
        contact_z = z + self._held_height()
        release_z = contact_z + TOOL_LENGTH_MM
        approach_z = collision.carry_height(contact_z)
        ignore = {held_name} if held_name else set()
        # 避让时把腕心高度带上：避让后的落点必须照样够得着，否则就是拿一个
        # 「更靠外、放得下但够不着」的位置替掉了原来的合法落点。
        fx, fy, moved = collision.find_free_spot(
            x, y, z, held_size, self.scene_objects,
            ignore_names=ignore,
            wrist_z=release_z,
        )
        if moved:
            self._log(
                f"落点 ({x:.0f},{y:.0f}) 已被其它物体占用，"
                f"已避让到 ({fx:.0f},{fy:.0f})"
            )
            x, y = fx, fy

        # 手里这个物体全程排除（含释放后的抬升段）：它与吸盘"接触"是必然的，
        # 释放之后它已经落地，抬升路径仍会贴着它。
        self._collision_ignore = ignore
        try:
            # 1. 走到目标正上方（横移用关节插补，见 _travel_to）
            self._travel_to([x, y, approach_z], list(TOOL_DOWN_RPY), line)
            if self._stop_flag.is_set():
                return
            # 2. 下探（直线：吸盘要对准支撑面）
            self._exec_movelp([x, y, release_z], list(TOOL_DOWN_RPY), line)
            if self._stop_flag.is_set():
                return
            # 3. 释放
            self.suck_on = False
            self.holding = None
            self._push_frame(self.joints_deg, line)
            self._log("释放物体")
            time.sleep(self.GRASP_SETTLE_SECONDS)
            # 4. 垂直抬升（直线：刚放下的物体就在正下方，横着走会蹭到它）
            self._exec_movelp([x, y, approach_z], list(TOOL_DOWN_RPY), line)
        finally:
            self._collision_ignore = set()

    # ── 场景快照查询（坐标换算集中在这里，别在别处再写一套）──
    #
    #   场景系（前端 Three.js，Y-up，mm）：(scene_x, scene_y=高度, scene_z)
    #   基座系（后端 kinematics，Z-up，mm）：(base_x, base_y, base_z)
    #   RobotArm 的 group.rotation.x = -90°，即 世界 = Rx(-90°)·局部：
    #       scene_x = base_x,  scene_y = base_z,  scene_z = -base_y
    #   反解：base_x = scene_x,  base_y = -scene_z,  base_z = scene_y
    def _object_at(self, x: float, y: float) -> Optional[dict]:
        """按基座系水平坐标就近取场景物体（坐标反解集中在 kinematics）"""
        return find_scene_object(self.scene_objects, x, y)

    def _held_size(self) -> Optional[list]:
        """当前手上物体的 size（按名字回场景里查），用于碰撞体与落点避让"""
        if not self.holding:
            return None
        for obj in self.scene_objects:
            if obj.get('name') == self.holding:
                return obj.get('size')
        return None

    def _held_height(self) -> float:
        """当前手上物体的高度（按名字回场景里查）"""
        if not self.holding:
            return 0.0
        for obj in self.scene_objects:
            if obj.get('name') == self.holding:
                return scene_object_height(obj, 0.0)
        return 0.0

    def _resolve_object_name(self, x: float, y: float) -> str:
        """按水平坐标就近匹配场景物体名，匹配不到给一个通用名。

        以前这里硬编码 'picked_object'，底部状态栏永远显示这串英文，
        既不可读也看不出到底吸了哪个物体。
        """
        obj = self._object_at(x, y)
        return obj.get('name') if obj else "已吸取物体"

    def _exec_wait(self, seconds: float, line: int):
        """延时（带硬上限，防止 WAIT 999999 把执行线程挂死）"""
        if seconds > self.MAX_WAIT_SECONDS:
            self._log(
                f"第{line}行 WAIT {seconds:g}s 超过上限，已按 {self.MAX_WAIT_SECONDS:g}s 执行"
            )
            seconds = self.MAX_WAIT_SECONDS
        steps = max(int(seconds * self.FRAME_RATE), 1)
        for _ in range(steps):
            if self._stop_flag.is_set():
                return
            self._pause_event.wait()
            self._push_frame(self.joints_deg, line)
            time.sleep(self.FRAME_DT)

    # ────────────────────────── 工具方法 ──────────────────────────
    def _s_curve(self, t: float) -> float:
        """S 曲线插补（5次多项式平滑）"""
        if t <= 0:
            return 0.0
        if t >= 1:
            return 1.0
        return 10 * t**3 - 15 * t**4 + 6 * t**5

    def _estimate_duration(self, start: List[float], end: List[float]) -> float:
        """估算关节运动时长（秒）"""
        max_delta = max(abs(a - b) for a, b in zip(start, end))
        # 满速 90°/s
        return max_delta / (90.0 * self.speed_percent / 100.0)

    def _push_frame(self, joints: List[float], line: int = 0):
        """推送一帧"""
        frame = TrajectoryFrame(
            joints_deg=list(joints),
            suck_on=self.suck_on,
            line=line,
            holding=self.holding
        )
        if self.on_frame:
            self.on_frame(frame)

    def _log(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    def get_state_dict(self) -> dict:
        """获取当前状态"""
        tcp = self.get_tcp_pose()
        return {
            'state': self.state.value,
            'joints_deg': self.joints_deg,
            'tcp': tcp,
            'suck_on': self.suck_on,
            'holding': self.holding,
            'speed': self.speed_percent,
        }


# 全局单例
executor = RobotExecutor()