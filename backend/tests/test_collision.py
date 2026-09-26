# -*- coding: utf-8 -*-
"""
碰撞检测测试（几何近似方案）

覆盖四层：
  1. 几何基元      —— scene_object_aabb / segment_aabb_hit / aabb_overlap
  2. 位姿判定      —— check_pose：正常抓取不误报；真撞要报；地面要报
  3. 落点避让      —— find_free_spot：占用会挪、堆叠不挪、避让点必须够得着
  4. 双闸门一致性  —— 校验器（拦在 AI 闭环里）＋ 执行器（拦在插补中）
                     用同一套避让算法，否则「合法程序被判死」或「穿模放行」

坐标系约定：场景快照是前端 Three.js 场景系（Y-up，size[1] 是高度），
本模块内部一律换算到基座系（CAD，Z-up，mm）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collision import (
    aabb_overlap, check_pose, describe, find_free_spot, reachable,
    scene_object_aabb, segment_aabb_hit,
)
from executor import RobotExecutor
from kinematics import HOME_JOINTS_DEG, TOOL_DOWN_RPY, TOOL_LENGTH_MM, solver
from validator import validator

# A 区（-X）/ B 区（+X）各一个方块，另有 +Y 远处一个圆柱（不挡路）
SCENE = [
    {'name': '红色方块', 'type': 'box', 'position': [-480, 65, 140],
     'size': [130, 130, 130], 'grabbable': True},
    {'name': '蓝色方块', 'type': 'box', 'position': [480, 65, 140],
     'size': [130, 130, 130], 'grabbable': True},
    {'name': '绿色圆柱', 'type': 'box', 'position': [0, 65, -560],
     'size': [130, 130, 130], 'grabbable': True},
]

# 正对机器人、400mm 高的一堵墙 —— 手臂要往 (300,0,300) 去必然穿过它
WALL_SCENE = [
    {'name': '挡路高墙', 'type': 'box', 'position': [300, 200, 0],
     'size': [130, 400, 130]},
]


# ══════════════════ 1. 几何基元 ══════════════════
def test_scene_object_aabb_axis_mapping():
    """场景系(Y-up) → 基座系(Z-up)：bx=sx, by=-sz, bz=sy"""
    bb = scene_object_aabb({'position': [480, 65, 140], 'size': [130, 130, 130]})
    assert bb is not None
    (lo, hi) = bb
    # 中心 (480, -140, 65)，半尺寸都是 65
    assert abs(lo[0] - 415) < 1e-6 and abs(hi[0] - 545) < 1e-6
    assert abs(lo[1] - (-205)) < 1e-6 and abs(hi[1] - (-75)) < 1e-6
    assert abs(lo[2] - 0) < 1e-6 and abs(hi[2] - 130) < 1e-6
    # 字段不全 / 尺寸非法 → None（不能让半个快照把检测搞崩）
    assert scene_object_aabb({'position': [0, 0], 'size': [1, 1, 1]}) is None
    assert scene_object_aabb({'position': [0, 0, 0], 'size': [0, 1, 1]}) is None


def test_aabb_overlap_touching_is_not_overlap():
    """竖直方向"正好相接"不算重叠 —— 否则把方块叠到方块顶面会被误判穿透"""
    lo1, hi1 = (0, 0, 0), (100, 100, 100)
    # 底面正好贴在 z=100 上
    lo2, hi2 = (0, 0, 100), (100, 100, 200)
    assert not aabb_overlap(lo1, hi1, lo2, hi2, gap=0.0, gap_z=-0.5)
    # 沉下去 1mm 才算穿透
    lo3, hi3 = (0, 0, 99), (100, 100, 199)
    assert aabb_overlap(lo1, hi1, lo3, hi3, gap=0.0, gap_z=-0.5)


def test_segment_aabb_hit_basic():
    lo, hi = (0, 0, 0), (100, 100, 100)
    # 穿过盒子
    assert segment_aabb_hit((50, 50, -50), (50, 50, 150), 0.0, lo, hi)
    # 从旁边 200mm 过（半径 10）→ 不碰
    assert not segment_aabb_hit((300, 50, -50), (300, 50, 150), 10.0, lo, hi)
    # 半径够大就擦上了（保守近似）
    assert segment_aabb_hit((300, 50, -50), (300, 50, 150), 220.0, lo, hi)


# ══════════════════ 2. 位姿判定 ══════════════════
def test_check_pose_home_is_clean():
    """HOME 位姿离三个方块都远，不应误报"""
    hits = check_pose(HOME_JOINTS_DEG, SCENE)
    assert hits == [], f"HOME 不应有碰撞，实际: {describe(hits)}"


def test_check_pose_ground_skips_base_segment():
    """底座本来就装在地面上：查地面时必须跳过第 0 段，否则永远报警"""
    assert check_pose(HOME_JOINTS_DEG, [], check_ground=True) == []


def test_check_pose_detects_target_but_ignored_when_picking():
    """抓取位姿必然"贴"在目标物体上：排除它干净，不排除就该报"""
    target = (-480.0, -140.0, 178.5)          # 红方块顶面 + 工具长
    sol = solver.inverse_kinematics(target[0], target[1], target[2],
                                    TOOL_DOWN_RPY, HOME_JOINTS_DEG)
    assert sol is not None, "抓取位姿应可逆解"

    clean = check_pose(sol, SCENE, ignore_names={'红色方块'})
    assert clean == [], f"排除目标后不应有碰撞，实际: {describe(clean)}"

    hits = check_pose(sol, SCENE)             # 不排除
    assert any(h['name'] == '红色方块' for h in hits), \
        f"不排除时应报红方块，实际: {describe(hits)}"


def test_check_pose_detects_wall_in_path():
    """手臂伸向被高墙占住的位置 → 必须报出来"""
    sol = solver.inverse_kinematics(300.0, 0.0, 300.0, TOOL_DOWN_RPY, HOME_JOINTS_DEG)
    assert sol is not None, "该点本身应可逆解（是几何可达、只是会撞）"
    hits = check_pose(sol, WALL_SCENE)
    assert any(h['name'] == '挡路高墙' for h in hits), \
        f"应报挡路高墙，实际: {describe(hits)}"


# ══════════════════ 3. 落点避让 ══════════════════
def test_find_free_spot_keeps_original_when_free():
    x, y, moved = find_free_spot(0, 400, 0, [130, 130, 130], SCENE)
    assert (x, y, moved) == (0, 400, False), "空位不该挪"


def test_find_free_spot_avoids_occupied_spot():
    """B 区已被蓝方块占着 → 落到 (480,-140) 会叠上去 → 必须挪开"""
    wrist_z = 130 + TOOL_LENGTH_MM            # 手里红方块高 130
    x, y, moved = find_free_spot(
        480, -140, 0, [130, 130, 130], SCENE,
        ignore_names={'红色方块'}, wrist_z=wrist_z,
    )
    assert moved, "占用的落点应触发避让"
    assert (x, y) != (480, -140)
    # 挪完之后确实不跟蓝方块重叠（竖直方向有重叠时就要求水平分开）
    lo2, hi2 = scene_object_aabb(
        {'position': [480, 65, 140], 'size': [130, 130, 130]}
    )
    lo1 = (x - 65, y - 65, 0.0)
    hi1 = (x + 65, y + 65, 130.0)
    assert not aabb_overlap(lo1, hi1, lo2, hi2, gap=0.0, gap_z=-0.5), \
        f"避让后的落点 (%.0f,%.0f) 仍与蓝方块重叠" % (x, y)


def test_find_free_spot_allows_stacking():
    """z=130（蓝方块顶面）时 xy 重合是正常堆叠，不是穿透 → 不该挪"""
    _, _, moved = find_free_spot(
        480, -140, 130, [130, 130, 130], SCENE,
        ignore_names={'红色方块'}, wrist_z=130 + TOOL_LENGTH_MM + 130,
    )
    assert not moved, "竖直方向错开的堆叠不该触发避让"


def test_find_free_spot_avoidance_stays_reachable():
    """避让不能拿"放得下但够不着"的位置换掉一个合法落点。

    旧实现往 +x 直推 150mm → 落在可达球外 → IK 直接无解，
    合法的「红方块放进 B 区」反而被判 IK_FAIL。现在候选点先过可达性。
    """
    wrist_z = 130 + TOOL_LENGTH_MM
    x, y, moved = find_free_spot(
        480, -140, 0, [130, 130, 130], SCENE,
        ignore_names={'红色方块'}, wrist_z=wrist_z,
    )
    assert moved
    assert reachable(x, y, wrist_z), \
        f"避让落点 (%.0f,%.0f) 落在可达球外（腕心 {wrist_z:.0f}mm）" % (x, y)


# ══════════════════ 4. 双闸门：校验器 / 执行器 ══════════════════
def test_validator_passes_legit_pick_place_with_avoidance():
    """最常见的一条指令：把红方块放进已经有蓝方块的 B 区 —— 必须放行"""
    program = "PICK target=[-480,-140,0]\nPLACE target=[480,-140,0]"
    result = validator.validate(program, SCENE, HOME_JOINTS_DEG)
    assert result.valid, "避让后可达的合法程序不该被判死：" + \
        "; ".join(f"[{e.code}] {e.message}" for e in result.errors)


def test_validator_rejects_wall_collision():
    """MOVELP 目标位姿撞墙 → 校验期就要报 COLLISION，并给出可执行的建议"""
    program = "MOVELP X=300,Y=0,Z=300,A=0,B=90,C=0"
    result = validator.validate(program, WALL_SCENE, HOME_JOINTS_DEG)
    assert not result.valid, "撞墙的目标位姿应校验失败"
    err = next((e for e in result.errors if e.code == 'COLLISION'), None)
    assert err is not None, \
        "错误码应为 COLLISION，实际：" + str([e.code for e in result.errors])
    assert '挡路高墙' in err.message
    assert err.suggestion, "必须给出修复建议（AI 靠它自纠）"


def test_validator_clear_movelp_still_passes():
    """空旷场景下同样的 MOVELP 应放行 —— 确认不是"一律拦死" """
    program = "MOVELP X=300,Y=0,Z=300,A=0,B=90,C=0"
    result = validator.validate(program, [], HOME_JOINTS_DEG)
    assert result.valid, "空旷场景下该 MOVELP 应通过：" + \
        "; ".join(f"[{e.code}] {e.message}" for e in result.errors)


def _run_program(program, scene, timeout=20.0):
    """跑一小段程序，收集日志/结束原因。返回 (ok, message, logs)"""
    ex = RobotExecutor()
    ex.scene_objects = scene
    ex.speed_percent = 1000          # 测试里不需要真实节奏，加快插补
    logs, done = [], {}

    ex.on_log = logs.append
    ex.on_finished = lambda ok, msg: done.update(ok=ok, msg=msg)

    ex.run(program)
    ex._thread.join(timeout=timeout)
    assert not ex._thread.is_alive(), "执行线程未在超时内结束"
    return done.get('ok'), done.get('msg', ''), logs


def test_executor_aborts_on_collision():
    """执行期闸门：插补到墙里 → 整段中止，并把原因带进 program_finished"""
    ok, msg, logs = _run_program(
        "MOVELP X=300,Y=0,Z=300,A=0,B=90,C=0", WALL_SCENE
    )
    assert ok is False, "撞墙的程序不应报成功"
    assert '碰撞' in msg, f"结束原因应说明碰撞，实际: {msg!r}"
    assert '挡路高墙' in msg
    assert any('检测到碰撞' in m for m in logs), "日志里应有碰撞记录"


def test_executor_place_avoids_occupied_spot():
    """执行期避让：把红方块放进被占的 B 区 → 自动挪位并跑完，不叠上去"""
    program = "PICK target=[-480,-140,0]\nPLACE target=[480,-140,0]"
    ok, msg, logs = _run_program(program, SCENE)
    assert any('已避让到' in m for m in logs), \
        "应记录落点避让，实际日志：" + str(logs)
    assert ok is True, f"避让后应完整跑完，实际: {msg!r}"


if __name__ == '__main__':
    print("=" * 60)
    print("  碰撞检测测试套件")
    print("=" * 60)
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_')]
    passed = 0
    for fn in fns:
        try:
            fn()
            print(f"  [PASS] {fn.__name__}")
            passed += 1
        except AssertionError as exc:
            print(f"  [FAIL] {fn.__name__}: {exc}")
    print("=" * 60)
    print(f"  {passed}/{len(fns)} 通过")
