# -*- coding: utf-8 -*-
"""
校验器 + AI 网关 + 运动学 综合测试
覆盖4个核心验证场景：
1. 语音："把红色方块放到B区" → 应生成 PICK+PLACE 程序
2. 语音："停！" → 快通道立即停止
3. 文字："抓取那个东西"（含糊）→ AI 应反问澄清
4. 程序错误：MOVEJ 缺少 J4 参数 → 校验器报行号，LLM 自动修复
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validator import validator, ValidationError
from ai_gateway import ai_gateway, FAST_COMMANDS
from kinematics import solver, fk_position, fk_pose, forward_kinematics, HOME_JOINTS_DEG, MAX_REACH_MM, JOINT_LIMIT_DEG
from parser import parser


# ────────────────── 场景1：语音"把红色方块放到B区" → PICK+PLACE ──────────────────
def test_scenario_1_pick_place():
    """场景1：模拟 AI 生成 PICK+PLACE 程序并校验"""
    print("\n{'─'*60}")
    print("场景1：语音'把红色方块放到B区' → 应生成 PICK+PLACE 程序")

    # 模拟场景物体（坐标 = 前端场景系：Y-up，size[1] 是高度）
    # 注意 A 区在 -X 侧、B 区在 +X 侧：基座系下分别是 (-480,-140) / (+480,-140)。
    scene_objects = [
        {'name': '红色方块', 'type': 'box', 'color': '#f43f5e',
         'position': [-480, 65, 140], 'size': [130, 130, 130], 'grabbable': True},
        {'name': '蓝色方块', 'type': 'box', 'color': '#6366f1',
         'position': [480, 65, 140], 'size': [130, 130, 130], 'grabbable': True},
    ]

    # 模拟 LLM 生成的 DSL 程序。
    # z 是**支撑面高度**（地面 = 0），不是接触面 —— 物体高度由执行器按场景补。
    # 不要用 (-150, 0, 25) 这类点：它落在 J1 的 ±170° 死区（需要 180°），
    # 属于几何上真的不可达，校验器现在会正确拦下。
    program = """# 把红色方块放到B区
PICK target=[-480,-140,0]
PLACE target=[480,-140,0]
"""

    # 校验
    result = validator.validate(program, scene_objects, HOME_JOINTS_DEG)
    print(f"  生成的程序:\n{program}")
    print(f"  校验结果: {'通过' if result.valid else '失败'}")
    if result.errors:
        for e in result.errors:
            print(f"    错误: 第{e.line}行 [{e.code}] {e.message}")
    assert result.valid, "PICK+PLACE 程序应校验通过"
    print("  [PASS] 场景1验证通过")


# ────────────────── 场景1b：不可达点必须被拦下 ──────────────────
def test_scenario_1b_unreachable_rejected():
    """半径内但姿态不可达的点（J1 死区）必须判非法 —— 这正是从前放行到执行期才静默失败的典型"""
    scene_objects = []
    # (-150, 0) 落在 J1 的 ±170° 缺口里，工作半径只有 150mm，却解不出来
    result = validator.validate("PICK target=[-150,0,0]", scene_objects, HOME_JOINTS_DEG)
    print(f"\n  不可达点校验结果: {'通过' if result.valid else '失败'}")
    for e in result.errors:
        print(f"    错误: [{e.code}] {e.message}")
    assert not result.valid, "J1 死区内的目标应被判非法"
    assert any(e.code == 'IK_FAIL' for e in result.errors), "错误码应为 IK_FAIL"


# ────────────────── 场景1c：LOOP 展开量必须被安全上限拦下 ──────────────────
def test_scenario_1c_loop_limit():
    """LOOP 300 / LOOP 300 展开成 9 万条 > 20000 上限，应判非法而不是把内存吃光"""
    program = "LOOP 300\nLOOP 300\nMOVEJ J1=1,J2=0,J3=0,J4=0,J5=0,J6=0\nEND\nEND"
    result = validator.validate(program, [], HOME_JOINTS_DEG)
    print(f"\n  LOOP 超量校验结果: {'通过' if result.valid else '失败'}")
    for e in result.errors:
        print(f"    错误: [{e.code}] {e.message}")
    assert not result.valid, "LOOP 展开量超上限应判非法"
    assert any(e.code == 'LOOP_LIMIT' for e in result.errors), "错误码应为 LOOP_LIMIT"


# ────────────────── 场景2：语音"停！" → 快通道立即停止 ──────────────────
def test_scenario_2_fast_channel_stop():
    """场景2：快通道匹配'停！'"""
    print("\n{'─'*60}")
    print("场景2：语音'停！' → 快通道立即停止")

    # 测试快通道匹配
    resp = ai_gateway.try_fast_channel("停！")
    assert resp is not None, "快通道应匹配'停'"
    assert resp.quick_command == 'stop', f"应返回 stop，实际: {resp.quick_command}"
    print(f"  输入: '停！'")
    print(f"  快通道匹配: {resp.quick_command}")
    print(f"  回复: {resp.text}")
    print("  [PASS] 场景2验证通过")

    # 测试更多快通道
    for text, expected in [("停止", "stop"), ("回家", "home"), ("吸取", "suck_on"), ("放下", "suck_off")]:
        resp = ai_gateway.try_fast_channel(text)
        assert resp is not None and resp.quick_command == expected, \
            f"'{text}' 应匹配 {expected}，实际: {resp.quick_command if resp else None}"
    print("  [PASS] 所有快通道关键词匹配正确")


# ────────────────── 场景3：文字"抓取那个东西"（含糊）→ AI 反问澄清 ──────────────────
def test_scenario_3_clarification():
    """场景3：含糊指令应触发澄清"""
    print("\n{'─'*60}")
    print("场景3：文字'抓取那个东西'（含糊）→ AI 应反问澄清")

    # 快通道不应匹配（不是短指令）
    resp = ai_gateway.try_fast_channel("抓取那个东西")
    assert resp is None, "含糊指令不应匹配快通道"
    print("  输入: '抓取那个东西'")
    print("  快通道: 未匹配（正确，进入慢通道）")
    print("  慢通道: LLM 应调用 ask_clarification 反问")
    print("  预期回复: '请问您要抓取哪个物体？场景中有红色方块、蓝色方块等'")
    print("  [PASS] 场景3验证通过（含糊指令不匹配快通道，进入LLM澄清）")


# ────────────────── 场景4：MOVEJ 缺少 J4 → 校验报行号 → LLM修复 ──────────────────
def test_scenario_4_syntax_error_and_fix():
    """场景4：语法错误 → 校验报行号 → 模拟 LLM 修复"""
    print("\n{'─'*60}")
    print("场景4：MOVEJ 缺少 J4 参数 → 校验器报行号，LLM 自动修复")

    # 错误程序（缺少 J4）
    # 注：J2/J3 取 ±30 是刻意选择 —— 该姿态在新（官方转向修正后）FK 下
    #     TCP 半径 521.5mm < 593mm，仍在工作空间内；若沿用旧的 J2=-60,J3=60
    #     则半径 629.5mm 越界，会掩盖"缺参"这个被考察的错误。
    bad_program = "MOVEJ J1=30,J2=-30,J3=30,J5=0,J6=0"

    # 校验
    result = validator.validate(bad_program, [], HOME_JOINTS_DEG)
    print(f"  错误程序: {bad_program}")
    print(f"  校验结果: {'通过' if result.valid else '失败'}")
    assert not result.valid, "缺少 J4 的程序应校验失败"

    for e in result.errors:
        print(f"    错误: 第{e.line}行 [{e.code}] {e.message}")
        if e.suggestion:
            print(f"    建议: {e.suggestion}")

    # 格式化错误给 LLM
    error_text = validator.format_errors_for_llm(result.errors)
    print(f"\n  发送给 LLM 的修复提示:\n{error_text}")

    # 模拟 LLM 修复后的程序（补回缺失的 J4=0，姿态不变）
    fixed_program = "MOVEJ J1=30,J2=-30,J3=30,J4=0,J5=0,J6=0"
    result2 = validator.validate(fixed_program, [], HOME_JOINTS_DEG)
    print(f"\n  修复后程序: {fixed_program}")
    print(f"  修复后校验: {'通过' if result2.valid else '失败'}")
    assert result2.valid, "修复后的程序应校验通过"
    print("  [PASS] 场景4验证通过（错误检测+修复闭环）")


# ────────────────── 运动学测试 ──────────────────
def test_kinematics():
    """运动学正逆解测试"""
    print("\n{'─'*60}")
    print("运动学测试")

    # 正向运动学
    pos = fk_position(HOME_JOINTS_DEG)
    print(f"  HOME 位姿 TCP: ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f})")

    # 工作空间检查
    r = (pos[0]**2 + pos[1]**2) ** 0.5
    print(f"  TCP 半径: {r:.1f}mm (限: {MAX_REACH_MM}mm)")
    assert r <= MAX_REACH_MM, "HOME 位姿应在工作空间内"

    # 逆解测试
    # ⚠️ solver.inverse_kinematics 是位置参数签名 (x, y, z, rpy, seed)。
    #    这条断言以前写成 `if ik_sol:`，而调用签名又传错了 —— 逆解永远返回
    #    None，测试却因为「None 就不校验」而一直绿着，把一条整段失效的
    #    笛卡尔链路藏了很久。现在必须硬断言真的解出来了。
    target = [200, 0, 300]
    ik_sol = solver.inverse_kinematics(target[0], target[1], target[2],
                                       [0, 90, 0], HOME_JOINTS_DEG)
    assert ik_sol is not None, "IK 应能解出 (200,0,300) 的逆解"
    print(f"  IK 解: {[round(j, 1) for j in ik_sol]}")
    fk_pos = fk_position(ik_sol)
    error = ((fk_pos[0]-target[0])**2 + (fk_pos[1]-target[1])**2 + (fk_pos[2]-target[2])**2) ** 0.5
    print(f"  FK 验证误差: {error:.2f}mm")
    assert error < 10, f"IK 解误差应 < 10mm，实际: {error:.2f}mm"

    # 工具朝下姿态（PICK/PLACE 用的那种）：低位目标必须可解
    from kinematics import TOOL_DOWN_RPY
    grasp = solver.inverse_kinematics(-480.0, -140.0, 178.5, TOOL_DOWN_RPY, HOME_JOINTS_DEG)
    assert grasp is not None, "工具朝下姿态应能解到低位抓取点"
    gp = fk_position(grasp)
    gerr = ((gp[0]+480)**2 + (gp[1]+140)**2 + (gp[2]-178.5)**2) ** 0.5
    print(f"  工具朝下 IK 误差: {gerr:.2f}mm")
    assert gerr < 10, f"抓取点 IK 误差应 < 10mm，实际: {gerr:.2f}mm"

    print("  [PASS] 运动学测试通过")


# ────────────────── 校验器边界测试 ──────────────────
def test_validator_workspace():
    """工作空间边界检查"""
    print("\n{'─'*60}")
    print("校验器边界测试")

    # 超出工作半径
    program = "MOVELP X=700,Y=0,Z=300,A=180,B=0,C=0"
    result = validator.validate(program, [], HOME_JOINTS_DEG)
    assert not result.valid, "超出工作半径应校验失败"
    print(f"  超半径: 第{result.errors[0].line}行 {result.errors[0].message}")


    # 关节超限
    program = "MOVEJ J1=200,J2=-90,J3=90,J4=0,J5=0,J6=0"
    result = validator.validate(program, [], HOME_JOINTS_DEG)
    assert not result.valid, "关节超限应校验失败"
    print(f"  关节超限: {result.errors[0].message}")

    print("  [PASS] 校验器边界测试通过")


if __name__ == '__main__':
    print("=" * 60)
    print("  埃夫特 ER3-600 机器人仿真系统 - 测试套件")
    print("=" * 60)

    test_scenario_1_pick_place()
    test_scenario_2_fast_channel_stop()
    test_scenario_3_clarification()
    test_scenario_4_syntax_error_and_fix()
    test_kinematics()
    test_validator_workspace()

    print("\n" + "=" * 60)
    print("  ✅ 全部测试通过！")
    print("=" * 60)