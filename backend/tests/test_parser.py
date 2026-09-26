# -*- coding: utf-8 -*-
"""
DSL 解析器单元测试
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from parser import parser, ParseResult


def test_basic_movej():
    """测试 MOVEJ 基本解析"""
    code = "MOVEJ J1=0,J2=-90,J3=90,J4=0,J5=0,J6=0"
    result = parser.parse(code)
    assert result.ok, f"解析应成功，错误: {result.errors}"
    assert len(result.instructions) == 1
    instr = result.instructions[0]
    assert instr.op == 'MOVEJ'
    assert instr.params['joints'] == [0, -90, 90, 0, 0, 0]
    print("[PASS] test_basic_movej")


def test_basic_movelp():
    """测试 MOVELP 基本解析"""
    code = "MOVELP X=200,Y=0,Z=300,A=180,B=0,C=0"
    result = parser.parse(code)
    assert result.ok, f"解析应成功，错误: {result.errors}"
    instr = result.instructions[0]
    assert instr.op == 'MOVELP'
    assert instr.params['pos'] == [200, 0, 300]
    assert instr.params['rpy'] == [180, 0, 0]
    print("[PASS] test_basic_movelp")


def test_pick_place():
    """测试 PICK/PLACE 解析"""
    code = "PICK target=[-150,0,25]\nPLACE target=[150,0,25]"
    result = parser.parse(code)
    assert result.ok, f"解析应成功，错误: {result.errors}"
    assert len(result.instructions) == 2
    assert result.instructions[0].op == 'PICK'
    assert result.instructions[0].params['target'] == [-150, 0, 25]
    assert result.instructions[1].op == 'PLACE'
    print("[PASS] test_pick_place")


def test_suck():
    """测试 SUCK 解析"""
    code = "SUCK ON\nSUCK OFF"
    result = parser.parse(code)
    assert result.ok
    assert result.instructions[0].params['on'] == True
    assert result.instructions[1].params['on'] == False
    print("[PASS] test_suck")


def test_home_wait_speed():
    """测试 HOME/WAIT/SPEED 解析"""
    code = "HOME\nWAIT 2.5\nSPEED 75"
    result = parser.parse(code)
    assert result.ok
    assert result.instructions[0].op == 'HOME'
    assert result.instructions[1].params['seconds'] == 2.5
    assert result.instructions[2].params['value'] == 75
    print("[PASS] test_home_wait_speed")


def test_comments():
    """测试注释"""
    code = "# 这是注释\nHOME\n# 另一行注释\nWAIT 1"
    result = parser.parse(code)
    assert result.ok
    assert len(result.instructions) == 2  # 注释行不计
    print("[PASS] test_comments")


def test_loop():
    """测试 LOOP/END"""
    code = "LOOP 3\nHOME\nWAIT 1\nEND"
    result = parser.parse(code)
    assert result.ok, f"解析应成功，错误: {result.errors}"
    # 展开后应该有 3 * 2 = 6 条指令
    expanded = parser.expand_loops(result.instructions)
    assert len(expanded) == 6
    print("[PASS] test_loop")


def test_syntax_error_movej_missing_j4():
    """测试场景4：MOVEJ 缺少 J4 参数 → 报行号"""
    code = "MOVEJ J1=0,J2=-90,J3=90,J5=0,J6=0"
    result = parser.parse(code)
    assert not result.ok, "应该报语法错误"
    assert len(result.errors) == 1
    assert result.errors[0].line == 1
    print(f"[PASS] test_syntax_error_movej_missing_j4 (行号: {result.errors[0].line})")


def test_syntax_error_unknown_command():
    """测试未知指令"""
    code = "MOVEX J1=0"
    result = parser.parse(code)
    assert not result.ok
    print("[PASS] test_syntax_error_unknown_command")


def test_syntax_error_speed_range():
    """测试 SPEED 超范围"""
    code = "SPEED 150"
    result = parser.parse(code)
    assert not result.ok
    print("[PASS] test_syntax_error_speed_range")


def test_unclosed_loop():
    """测试未闭合 LOOP"""
    code = "LOOP 3\nHOME\nWAIT 1"
    result = parser.parse(code)
    assert not result.ok
    assert any("未闭合" in e.message for e in result.errors)
    print("[PASS] test_unclosed_loop")


if __name__ == '__main__':
    test_basic_movej()
    test_basic_movelp()
    test_pick_place()
    test_suck()
    test_home_wait_speed()
    test_comments()
    test_loop()
    test_syntax_error_movej_missing_j4()
    test_syntax_error_unknown_command()
    test_syntax_error_speed_range()
    test_unclosed_loop()
    print("\n✅ 全部解析器测试通过！")