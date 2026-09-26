# -*- coding: utf-8 -*-
"""
DSL 机器人编程语言解析器
支持指令：MOVEJ / MOVELP / PICK / PLACE / SUCK / WAIT / SPEED / HOME / LOOP
注释：# 开头
"""
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Any


# ────────────────────────── 安全上限 ──────────────────────────
#  LOOP 展开后的最大指令条数。嵌套 LOOP 是乘积关系：
#  `LOOP 1000 / LOOP 1000 / ... / END / END` 会展开成 100 万条，
#  再乘上每条插补帧就是几十 GB 的内存 —— 纯演示场景没有任何理由这么大。
#  超过上限直接判非法，而不是"尽力展开"（半展开的程序没法解释也没法验收）。
MAX_EXPANDED_INSTRUCTIONS = int(os.environ.get("MAX_EXPANDED_INSTRUCTIONS", 20000))

#  单条 LOOP 的循环次数上限，用于在展开前就挡住明显的恶意/误输入
MAX_LOOP_COUNT = int(os.environ.get("MAX_LOOP_COUNT", 10000))


class LoopExpansionError(ValueError):
    """LOOP 展开量超过安全上限"""
    pass


@dataclass
class DslInstruction:
    """单条 DSL 指令"""
    op: str                        # 操作类型
    params: dict = field(default_factory=dict)
    line: int = 0                  # 源码行号（1-based）
    raw: str = ""                  # 原始文本


@dataclass
class ParseError:
    """解析错误"""
    line: int
    message: str
    raw: str = ""


@dataclass
class ParseResult:
    """解析结果"""
    instructions: List[DslInstruction] = field(default_factory=list)
    errors: List[ParseError] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


# ────────────────────────── 正则模式 ──────────────────────────
_RE_MOVEJ = re.compile(
    r'^MOVEJ\s+'
    r'J1=(-?\d+\.?\d*)\s*,\s*'
    r'J2=(-?\d+\.?\d*)\s*,\s*'
    r'J3=(-?\d+\.?\d*)\s*,\s*'
    r'J4=(-?\d+\.?\d*)\s*,\s*'
    r'J5=(-?\d+\.?\d*)\s*,\s*'
    r'J6=(-?\d+\.?\d*)\s*$',
    re.IGNORECASE
)

_RE_MOVELP = re.compile(
    r'^MOVELP\s+'
    r'X=(-?\d+\.?\d*)\s*,\s*'
    r'Y=(-?\d+\.?\d*)\s*,\s*'
    r'Z=(-?\d+\.?\d*)\s*,\s*'
    r'A=(-?\d+\.?\d*)\s*,\s*'
    r'B=(-?\d+\.?\d*)\s*,\s*'
    r'C=(-?\d+\.?\d*)\s*$',
    re.IGNORECASE
)

_RE_PICK_PLACE = re.compile(
    r'^(PICK|PLACE)\s+target=\[(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\]\s*$',
    re.IGNORECASE
)

_RE_SUCK = re.compile(r'^SUCK\s+(ON|OFF)\s*$', re.IGNORECASE)
_RE_WAIT = re.compile(r'^WAIT\s+(\d+\.?\d*)\s*$', re.IGNORECASE)
_RE_SPEED = re.compile(r'^SPEED\s+(\d+)\s*$', re.IGNORECASE)
_RE_HOME = re.compile(r'^HOME\s*$', re.IGNORECASE)
_RE_LOOP = re.compile(r'^LOOP\s+(\d+)\s*$', re.IGNORECASE)
_RE_END = re.compile(r'^END\s*$', re.IGNORECASE)


class DslParser:
    """DSL 解析器主类"""

    def parse(self, code: str) -> ParseResult:
        """解析整段 DSL 代码，返回 ParseResult"""
        result = ParseResult()
        lines = code.split('\n')
        # 处理 LOOP/END 嵌套
        loop_stack: List[Tuple[int, int]] = []  # (loop行号, 循环次数)
        flat_instructions: List[DslInstruction] = []

        for idx, raw_line in enumerate(lines, start=1):
            line = raw_line.strip()
            if not line or line.startswith('#'):
                continue
            instr = self._parse_line(line, idx, raw_line)
            if instr is None:
                # _parse_line 已经把错误加进 result 了，这里需要重新解析以收集错误
                continue
            if instr.op == '__error__':
                result.errors.append(ParseError(
                    line=idx, message=instr.params.get('msg', '未知错误'), raw=raw_line
                ))
                continue

            # 处理 LOOP 结构
            if instr.op == 'LOOP':
                loop_stack.append((idx, instr.params['count']))
                flat_instructions.append(instr)
            elif instr.op == 'END':
                if not loop_stack:
                    result.errors.append(ParseError(
                        line=idx, message="END 没有匹配的 LOOP", raw=raw_line
                    ))
                else:
                    loop_stack.pop()
                    flat_instructions.append(instr)
            else:
                flat_instructions.append(instr)

        # 检查未闭合的 LOOP
        for loop_line, _ in loop_stack:
            result.errors.append(ParseError(
                line=loop_line, message="LOOP 未闭合，缺少 END"
            ))

        result.instructions = flat_instructions
        return result

    def _parse_line(self, line: str, line_no: int, raw: str) -> Optional[DslInstruction]:
        """解析单行，返回指令或 None（错误已记录到全局）"""
        # MOVEJ
        m = _RE_MOVEJ.match(line)
        if m:
            return DslInstruction(
                op='MOVEJ',
                params={
                    'joints': [float(m.group(i)) for i in range(1, 7)]  # 角度
                },
                line=line_no, raw=raw
            )
        # MOVELP
        m = _RE_MOVELP.match(line)
        if m:
            return DslInstruction(
                op='MOVELP',
                params={
                    'pos': [float(m.group(1)), float(m.group(2)), float(m.group(3))],  # mm
                    'rpy': [float(m.group(4)), float(m.group(5)), float(m.group(6))],  # 度
                },
                line=line_no, raw=raw
            )
        # PICK / PLACE
        m = _RE_PICK_PLACE.match(line)
        if m:
            return DslInstruction(
                op=m.group(1).upper(),
                params={
                    'target': [float(m.group(2)), float(m.group(3)), float(m.group(4))]
                },
                line=line_no, raw=raw
            )
        # SUCK
        m = _RE_SUCK.match(line)
        if m:
            return DslInstruction(
                op='SUCK',
                params={'on': m.group(1).upper() == 'ON'},
                line=line_no, raw=raw
            )
        # WAIT
        m = _RE_WAIT.match(line)
        if m:
            return DslInstruction(
                op='WAIT',
                params={'seconds': float(m.group(1))},
                line=line_no, raw=raw
            )
        # SPEED
        m = _RE_SPEED.match(line)
        if m:
            speed = int(m.group(1))
            if not (1 <= speed <= 100):
                return DslInstruction(
                    op='__error__',
                    params={'msg': f'SPEED 值 {speed} 超出范围 [1,100]'},
                    line=line_no, raw=raw
                )
            return DslInstruction(
                op='SPEED',
                params={'value': speed},
                line=line_no, raw=raw
            )
        # HOME
        if _RE_HOME.match(line):
            return DslInstruction(op='HOME', params={}, line=line_no, raw=raw)
        # LOOP
        m = _RE_LOOP.match(line)
        if m:
            return DslInstruction(
                op='LOOP',
                params={'count': int(m.group(1))},
                line=line_no, raw=raw
            )
        # END
        if _RE_END.match(line):
            return DslInstruction(op='END', params={}, line=line_no, raw=raw)

        # 无法识别
        return DslInstruction(
            op='__error__',
            params={'msg': f'无法识别的指令: "{line}"'},
            line=line_no, raw=raw
        )

    def expand_loops(self, instructions: List[DslInstruction]) -> List[DslInstruction]:
        """展开 LOOP...END 块为重复的指令序列。

        超过 MAX_EXPANDED_INSTRUCTIONS 抛 LoopExpansionError（调用方负责
        转成用户可读的校验错误）。这里必须**在 extend 之前**判断：
        先扩展再检查的话，2e7 条指令已经分配完了，限制就没意义了。
        """
        result: List[DslInstruction] = []
        i = 0
        while i < len(instructions):
            instr = instructions[i]
            if instr.op == 'LOOP':
                # 找到匹配的 END
                depth = 1
                block: List[DslInstruction] = []
                j = i + 1
                while j < len(instructions) and depth > 0:
                    if instructions[j].op == 'LOOP':
                        depth += 1
                        block.append(instructions[j])
                    elif instructions[j].op == 'END':
                        depth -= 1
                        if depth > 0:
                            block.append(instructions[j])
                    else:
                        block.append(instructions[j])
                    j += 1
                # 递归展开内部 LOOP
                block = self.expand_loops(block)
                count = instr.params['count']
                if block and count * len(block) > MAX_EXPANDED_INSTRUCTIONS:
                    raise LoopExpansionError(
                        f"第{instr.line}行 LOOP {count} 展开后共 {count * len(block)} 条指令，"
                        f"超过上限 {MAX_EXPANDED_INSTRUCTIONS}"
                    )
                if len(result) + count * len(block) > MAX_EXPANDED_INSTRUCTIONS:
                    raise LoopExpansionError(
                        f"程序 LOOP 展开后总指令数超过上限 {MAX_EXPANDED_INSTRUCTIONS}"
                    )
                for _ in range(count):
                    result.extend(block)
                i = j
            elif instr.op == 'END':
                i += 1  # 跳过顶层 END
            else:
                result.append(instr)
                i += 1
        return result


# 全局单例
parser = DslParser()