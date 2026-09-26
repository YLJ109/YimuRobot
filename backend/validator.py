# -*- coding: utf-8 -*-
"""
DSL 程序校验器
- 语法校验（调用 parser）
- 工作空间边界检查（半径593mm圆柱内）
- 关节限位检查（逐轴，取 JOINT_LIMITS_DEG 的真实软限位）
- 逆解可行性检查（半径内但姿态不可达的点也要拦下来）
- 吸取时物体存在性检查
- 返回结构化错误供 LLM 自动修复
"""
import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

from parser import parser, ParseResult, DslInstruction, LoopExpansionError
import collision
from kinematics import (
    MAX_REACH_MM, JOINT_LIMITS_DEG, HOME_JOINTS_DEG, joint_limit_text,
    TOOL_LENGTH_MM, TOOL_DOWN_RPY,
    find_scene_object, scene_object_height, DEFAULT_OBJECT_HEIGHT_MM,
    fk_position, forward_kinematics, solver
)


@dataclass
class ValidationError:
    """校验错误"""
    line: int
    code: str          # 错误码：SYNTAX / WORKSPACE / JOINT_LIMIT / OBJECT_NOT_FOUND / IK_FAIL
    message: str
    suggestion: str = ""  # 修复建议（供 LLM 参考）


@dataclass
class ValidationResult:
    """校验结果"""
    valid: bool
    instructions: List[DslInstruction] = field(default_factory=list)
    errors: List[ValidationError] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            'valid': self.valid,
            'errors': [
                {
                    'line': e.line, 'code': e.code,
                    'message': e.message, 'suggestion': e.suggestion
                }
                for e in self.errors
            ]
        }


class ProgramValidator:
    """程序校验器"""

    def validate(
        self,
        code: str,
        scene_objects: Optional[List[dict]] = None,
        current_joints_deg: Optional[List[float]] = None
    ) -> ValidationResult:
        """完整校验流程：语法 → 边界 → 限位 → 物体存在性"""
        result = ValidationResult(valid=True)
        if scene_objects is None:
            scene_objects = []
        # 起点必须与执行器一致（HOME_JOINTS_DEG）。
        # 旧代码写死 [0,-90,90,0,0,0]，那是另一套姿态 —— HOME 之后的 MOVELP
        # 会从错误起点推算可达性，校验结论和执行结果对不上。
        if current_joints_deg is None:
            current_joints_deg = list(HOME_JOINTS_DEG)

        # 1. 语法解析
        parse_result = parser.parse(code)
        if not parse_result.ok:
            result.valid = False
            for e in parse_result.errors:
                result.errors.append(ValidationError(
                    line=e.line, code='SYNTAX',
                    message=e.message,
                    suggestion=f"请检查第 {e.line} 行的指令格式"
                ))
            return result

        # 展开 LOOP（超量会被安全上限拦下，属于「程序非法」而不是崩溃）
        try:
            instructions = parser.expand_loops(parse_result.instructions)
        except LoopExpansionError as exc:
            result.valid = False
            result.errors.append(ValidationError(
                line=0, code='LOOP_LIMIT',
                message=str(exc),
                suggestion="减小 LOOP 次数或减少循环体内的指令数"
            ))
            return result
        result.instructions = instructions

        # 2. 逐条校验语义
        sim_joints = list(current_joints_deg)
        suck_holding: Optional[str] = None  # 当前持物名称
        # 逆解可行性缓存：同一个目标点在一次校验里只解一次。
        # 求解器最坏有 0.25s 预算，不去重的话一个 200 行的程序能把
        # 「生成→校验→修复」闭环拖到几十秒。
        ik_cache: Dict[tuple, bool] = {}

        for instr in instructions:
            errs = self._validate_instruction(
                instr, sim_joints, scene_objects, suck_holding, ik_cache
            )
            if errs:
                result.valid = False
                result.errors.extend(errs)
            else:
                # 模拟执行以更新状态
                sim_joints, suck_holding = self._simulate_step(
                    instr, sim_joints, suck_holding, scene_objects
                )

        return result

    def _validate_instruction(
        self,
        instr: DslInstruction,
        current_joints: List[float],
        scene_objects: List[dict],
        suck_holding: Optional[str],
        ik_cache: Optional[Dict[tuple, bool]] = None,
    ) -> List[ValidationError]:
        """校验单条指令"""
        errors: List[ValidationError] = []
        if ik_cache is None:
            ik_cache = {}
        op = instr.op

        if op == 'MOVEJ':
            joints = instr.params['joints']
            # 关节限位检查 —— 必须**逐轴**用该轴自己的范围。
            # ER3-600 的软限位是非对称的：J3 是 -65~+185、J6 是 ±360。
            # 旧代码拿 J1 的 ±170 当所有轴的上限做 abs(j) > 170 判断，
            # 会把完全合法的 J3=180 / J6=200 判成超限，AI 生成的程序被反复退回。
            for i, j in enumerate(joints):
                lo, hi = JOINT_LIMITS_DEG[i]
                if j < lo or j > hi:
                    errors.append(ValidationError(
                        line=instr.line, code='JOINT_LIMIT',
                        message=f"关节 J{i+1} 角度 {j:.1f}° 超出限位 {joint_limit_text(i)}",
                        suggestion=f"将 J{i+1} 限制在 {joint_limit_text(i)} 以内"
                    ))
            # 工作空间检查
            if not errors:
                pos = fk_position(joints)
                r = math.sqrt(pos[0]**2 + pos[1]**2)
                if r > MAX_REACH_MM:
                    errors.append(ValidationError(
                        line=instr.line, code='WORKSPACE',
                        message=f"TCP 半径 {r:.1f}mm 超出最大工作半径 {MAX_REACH_MM}mm",
                        suggestion=f"减小目标距离，确保在{MAX_REACH_MM:.0f}mm工作半径内"
                    ))


        elif op == 'MOVELP':
            pos = instr.params['pos']
            x, y, z = pos
            r = math.sqrt(x**2 + y**2)
            if r > MAX_REACH_MM:
                errors.append(ValidationError(
                    line=instr.line, code='WORKSPACE',
                    message=f"目标位置半径 {r:.1f}mm 超出最大工作半径 {MAX_REACH_MM}mm",
                    suggestion=f"减小 X/Y 距离，确保在{MAX_REACH_MM:.0f}mm工作半径内"
                ))
            elif not self._ik_feasible(
                ik_cache, (x, y, z), instr.params['rpy'], current_joints
            ):
                errors.append(ValidationError(
                    line=instr.line, code='IK_FAIL',
                    message=f"目标位姿 ({x:.0f},{y:.0f},{z:.0f}) 无法逆解到关节角"
                            f"（姿态 A={instr.params['rpy'][0]:.0f} "
                            f"B={instr.params['rpy'][1]:.0f} C={instr.params['rpy'][2]:.0f}）",
                    suggestion="该点虽然在工作半径内，但以给定姿态不可达，"
                               "请调整坐标或改用工具朝下的姿态 B=90"
                ))
            else:
                # 可达 → 再看这个位姿会不会撞上东西。
                # 校验期给的是「哪一行、撞了谁、怎么改」，AI 拿到就能自己修；
                # 路径中段的穿模由执行期的逐插补点复查兜底。
                sol = self._ik_solution(
                    ik_cache, (x, y, z), instr.params['rpy'], current_joints
                )
                errors.extend(self._collision_errors(
                    instr, sol, scene_objects, set(),
                    f"MOVELP 目标点 ({x:.0f},{y:.0f},{z:.0f})",
                ))


        elif op == 'PICK' or op == 'PLACE':
            target = instr.params['target']
            x, y, z = target
            r = math.sqrt(x**2 + y**2)
            if r > MAX_REACH_MM:
                errors.append(ValidationError(
                    line=instr.line, code='WORKSPACE',
                    message=f"PICK/PLACE 目标半径 {r:.1f}mm 超出工作半径",
                    suggestion=f"将目标移至{MAX_REACH_MM:.0f}mm工作半径内"
                ))
            else:
                # PICK/PLACE 实际走的是「工具朝下、从上方接近」的两点轨迹：
                # 先到 接触面 + 工具长 + 间隙，再下探到 接触面 + 工具长。
                # 两点都要能解出来，否则整条指令会在执行期静默失败。
                #
                # 接触面 = 支撑面 z + **物体高度**，与 executor._exec_pick 用
                # 同一套算法（共用 kinematics.find_scene_object/scene_object_height）。
                # 历史 bug：这里按「接触面 = z」校验，执行器却按 z + 物体高度算，
                # 两边算出的姿态不是同一个 —— 校验通过不等于执行能过。
                if op == 'PICK':
                    picked = find_scene_object(scene_objects, x, y)
                    h = scene_object_height(picked, DEFAULT_OBJECT_HEIGHT_MM)
                    place_x, place_y = x, y
                    ignore = {picked['name']} if picked and picked.get('name') else set()
                else:
                    # PLACE 用「手里那个物体」的高度；空手 PLACE 时执行器按 0 算
                    held = None
                    if suck_holding:
                        held = next(
                            (o for o in (scene_objects or ())
                             if o.get('name') == suck_holding), None
                        )
                    h = scene_object_height(held, 0.0) if suck_holding else 0.0
                    ignore = {suck_holding} if suck_holding else set()
                    # 落点避让必须在这里也算一遍，且用**同一个函数、同一组参数**
                    # （executor._exec_place 用的是这套）。否则会出现
                    # 「校验判该行碰撞 → 程序被退回 → 执行期那套避让永远用不上」：
                    # 把红方块放到已有蓝方块的 B 区是最常见的一条指令，不能把它
                    # 判死。
                    held_size = held.get('size') if held else None
                    fx, fy, moved = collision.find_free_spot(
                        x, y, z, held_size or [0.0, 0.0, 0.0], scene_objects,
                        ignore_names=ignore,
                        # 腕心高度：避让点必须同样够得着，否则会换出一个
                        # 「放得下但 IK 无解」的落点（校验期就会判 IK_FAIL）
                        wrist_z=z + h + TOOL_LENGTH_MM,
                    )
                    place_x, place_y = (fx, fy) if moved else (x, y)

                # 实际会走到的几个点：空手接近高度 / 接触高度 / 带着物体抬到的高度。
                # 每一个都必须能解出来，否则整条指令会在执行期静默失败。
                # 高度一律用 collision.carry_height 现算 —— 校验与执行共用同一个
                # 函数，才不会有"校验通过但执行撞车"或反过来"合法程序被判死"。
                contact_z = z + h
                grasp = (place_x, place_y, contact_z + TOOL_LENGTH_MM)
                safe_z = collision.carry_height(contact_z)
                if op == 'PICK':
                    pts = [('空手接近点', (place_x, place_y, safe_z)),
                           ('接触点', grasp),
                           ('带物抬升点', (place_x, place_y, safe_z))]
                else:
                    # PLACE 全程带着物体：下探与释放后的抬升是同一个高度，
                    # 释放之后物体已经落地（被 ignore 掉），手臂不会更低。
                    pts = [('带物接近点', (place_x, place_y, safe_z)),
                           ('接触点', grasp)]
                bad = None
                for label, pt in pts:
                    if not self._ik_feasible(ik_cache, pt, TOOL_DOWN_RPY, current_joints):
                        bad = (label, pt)
                        break
                if bad is not None:
                    _bad_label, _bad_pt = bad
                    errors.append(ValidationError(
                        line=instr.line, code='IK_FAIL',
                        message=f"{op} 目标 ({place_x:.0f},{place_y:.0f},{z:.0f}) 不可达"
                                f"（{_bad_label}卡在 {_bad_pt[2]:.0f}mm 高度）",
                        suggestion="把物体移近机器人，或确认 z 给的是吸盘接触面高度"
                    ))
                else:
                    # 各点都可达 → 再看会不会撞。目标物体（PICK）与手上物体
                    # （PLACE）和吸盘"接触"是这两条指令的固有动作，排除掉；
                    # 其它物体照常参与判定。
                    for _label, pt in pts:
                        sol = self._ik_solution(ik_cache, pt, TOOL_DOWN_RPY, current_joints)
                        hit_errs = self._collision_errors(
                            instr, sol, scene_objects, ignore,
                            f"{op} ({place_x:.0f},{place_y:.0f},{z:.0f})",
                        )
                        if hit_errs:
                            errors.extend(hit_errs)
                            break


        elif op == 'SUCK':
            if instr.params['on'] and suck_holding is not None:
                errors.append(ValidationError(
                    line=instr.line, code='OBJECT_NOT_FOUND',
                    message=f"已持有物体 '{suck_holding}'，不能重复吸取",
                    suggestion="先 SUCK OFF 释放当前物体"
                ))

        return errors

    def _simulate_step(
        self,
        instr: DslInstruction,
        joints: List[float],
        suck_holding: Optional[str],
        scene_objects: Optional[List[dict]] = None,
    ) -> tuple:
        """模拟执行一步，返回更新后的 (joints, suck_holding)"""
        op = instr.op
        if op == 'MOVEJ':
            joints = list(instr.params['joints'])
        elif op == 'MOVELP':
            # ⚠️ 位置参数签名：inverse_kinematics(x, y, z, rpy, seed)。
            #    旧代码传的是 (pos列表, rpy, seed)，float(list) 直接抛异常，
            #    被上层 try 吞掉后这里静默不动 —— 校验器于是拿错误位姿继续
            #    推算后面所有指令的可达性。
            pos, rpy = instr.params['pos'], instr.params['rpy']
            ik_sol = solver.inverse_kinematics(
                pos[0], pos[1], pos[2], rpy, joints
            )
            if ik_sol:
                joints = ik_sol
        elif op == 'HOME':
            joints = list(HOME_JOINTS_DEG)
        elif op == 'PICK' or op == 'PLACE':
            # 执行器跑完 PICK/PLACE 会停在「目标上方安全高度」，并把持物状态
            # 改掉。旧实现完全没模拟这两条指令：PICK 之后 joints 还停在上一
            # 条指令的位置、suck_holding 也还是 None —— 于是后续 SUCK/PLACE
            # 的可达性与「重复吸取」判断全部基于错误的起点。
            x, y, z = instr.params['target']
            if op == 'PICK':
                obj = find_scene_object(scene_objects, x, y)
                h = scene_object_height(obj, DEFAULT_OBJECT_HEIGHT_MM)
                suck_holding = (obj.get('name') if obj else "_unknown_")
                ignore = {suck_holding}
                held_size = None
            else:
                held = None
                if suck_holding:
                    held = next(
                        (o for o in (scene_objects or ())
                         if o.get('name') == suck_holding), None
                    )
                h = scene_object_height(held, 0.0) if suck_holding else 0.0
                ignore = {suck_holding} if suck_holding else set()
                # 落点避让与 executor/校验共用同一函数，保证模拟出的落点
                # 就是执行器真正会去的位置（后续指令的可达性判断靠这个种子）
                held_size = held.get('size') if held else None
                fx, fy, moved = collision.find_free_spot(
                    x, y, z, held_size or [0.0, 0.0, 0.0], scene_objects,
                    ignore_names=ignore,
                    # 与上面 PLACE 分支同一套参数（腕心高度带余量后的可达性）
                    wrist_z=z + h + TOOL_LENGTH_MM,
                )
                if moved:
                    x, y = fx, fy
                suck_holding = None
            # 执行器跑完 PICK/PLACE 停在「带着物体抬升后的安全高度」，
            # 这个高度由 collision.carry_height 统一给（校验/执行同一函数）。
            safe_z = collision.carry_height(z + h)
            ik_sol = solver.inverse_kinematics(x, y, safe_z, TOOL_DOWN_RPY, joints)
            if ik_sol:
                joints = ik_sol
        elif op == 'SUCK':
            if instr.params['on']:
                suck_holding = "_unknown_"
            else:
                suck_holding = None
        return joints, suck_holding

    def _ik_feasible(
        self,
        cache: Dict[tuple, bool],
        pos: tuple,
        rpy: tuple,
        seed: List[float],
    ) -> bool:
        """目标点能否逆解到关节角（带缓存）。

        这一步是「AI 生成 → 校验 → 修复」闭环里最有价值的一环：
        过去校验只看「半径 < 593mm」，于是一堆几何上根本不可达的点
        被判为合法，程序一路放行到执行期才静默失败，用户看到的就是
        「程序跑完了机器人没动」。
        """
        key = (
            round(float(pos[0]), 1), round(float(pos[1]), 1), round(float(pos[2]), 1),
            round(float(rpy[0]), 1), round(float(rpy[1]), 1), round(float(rpy[2]), 1),
        )
        if key in cache:
            return cache[key][0]
        sol = solver.inverse_kinematics(
            key[0], key[1], key[2], (key[3], key[4], key[5]), seed
        )
        ok = sol is not None
        # 缓存里连解一起存：碰撞检查需要关节角，不能只留一个 bool
        cache[key] = (ok, list(sol) if ok else None)
        return ok

    def _ik_solution(self, cache: Dict[tuple, bool], pos: tuple, rpy: tuple,
                     seed: List[float]) -> Optional[List[float]]:
        """与 _ik_feasible 同一份缓存，但返回关节解本身（不可解时 None）"""
        key = (
            round(float(pos[0]), 1), round(float(pos[1]), 1), round(float(pos[2]), 1),
            round(float(rpy[0]), 1), round(float(rpy[1]), 1), round(float(rpy[2]), 1),
        )
        entry = cache.get(key)
        if entry is None:
            self._ik_feasible(cache, pos, rpy, seed)
            entry = cache.get(key)
        return entry[1] if entry else None

    def _collision_errors(
        self,
        instr: DslInstruction,
        joints: Optional[List[float]],
        scene_objects: List[dict],
        ignore_names: set,
        label: str,
    ) -> List[ValidationError]:
        """这个位姿会不会撞到东西。

        校验期就拦住的价值：AI 拿到的是「第N行 PICK 会与『蓝色方块』碰撞 +
        怎么改」，而不是程序一路放行到执行期才穿模。
        """
        if joints is None:
            return []
        hits = collision.check_pose(joints, scene_objects, ignore_names=ignore_names)
        if not hits:
            return []
        return [ValidationError(
            line=instr.line, code='COLLISION',
            message=f"{label} 会与 {collision.describe(hits)}",
            suggestion="抬高过渡高度、调整目标点，或先把挡路的物体移开再执行"
        )]

    def format_errors_for_llm(self, errors: List[ValidationError]) -> str:
        """将错误格式化为 LLM 可读的修复提示"""
        if not errors:
            return ""
        lines = ["程序校验失败，请修复以下问题："]
        for e in errors:
            lines.append(f"  第{e.line}行 [{e.code}]: {e.message}")
            if e.suggestion:
                lines.append(f"    修复建议: {e.suggestion}")
        return '\n'.join(lines)


# 全局单例
validator = ProgramValidator()