# -*- coding: utf-8 -*-
"""
碰撞检测（几何近似，不引入物理引擎）

两个使用场景：
  ① **校验期**：AI 生成程序后立刻判出「这条 MOVELP/PICK/PLACE 会撞」，
     带着修复建议退回给模型 —— 而不是等执行时才在画面上穿模。
  ② **执行期**：笛卡尔插补的每个中间位姿都复查一遍，机械臂本体（连杆胶囊）
     与吸盘所持物体不得穿过场景物体、不得插进地面。

坐标系一律是**基座系（CAD，Z 轴向上，毫米）**。
场景快照里的 position/size 是前端 Three.js 场景系（Y 轴向上、毫米），换算
关系与 kinematics.find_scene_object 保持一致（那里有推导注释）。

⚠️ 误差取向：**宁可漏报，也不要误报**。
   正常搬运时吸盘必然"接触"目标物体，那种接触必须排除掉；而误报会让完全
   合法的程序跑不动，比漏报更难排查。所以：
     - 连杆半径取保守（略小于真实臂宽）；
     - 目标物体 / 手上物体通过 ignore_names 排除；
     - 地面检查跳过底座段（底座本来就装在地面上）。
"""
import math
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from kinematics import (
    APPROACH_CLEARANCE_MM, HOME_JOINTS_DEG, MAX_REACH_MM, TOOL_LENGTH_MM, fk_frames,
)

# 避让落点必须留在工作球内，再留一点余量：
#   把 (480,-140) 往 +x 方向推 150mm 就出圈了（腕心距离 > 593），避让到
#   那里的落点 IK 直接解不出来 —— 等于用一个"能放但够不着"的位置换掉了
#   一个"够得着但会叠"的位置。所以候选点先过可达性，再过占用判定。
REACH_MARGIN_MM = 40.0

# 肩关节（J2 轴心）在基座系中的位置 —— 可达球的球心。
#   MAX_REACH_MM 的官方定义就是「J2 轴 → 腕心」的最大距离，所以判「够不够
#   得着」就是判「腕心到 J2 的距离 ≤ MAX_REACH_MM」。J2 轴心永远落在基座竖
#   直轴上，与关节角无关，取 HOME 位姿算一次即可。
_SHOULDER_MM: Tuple[float, float, float] = tuple(
    float(v) for v in fk_frames(HOME_JOINTS_DEG)[2][:3, 3]
)


def reachable(x: float, y: float, z: float, margin_mm: float = REACH_MARGIN_MM) -> bool:
    """腕心 (x, y, z)（基座系，mm）是否落在可达球内。

    注意传的是**腕心**，不是 TCP、也不是唇口。含 margin_mm 余量，
    避免贴着球面做出来的解在浮点噪声下退化成无解。
    """
    lim = MAX_REACH_MM - margin_mm
    return math.dist((x, y, z), _SHOULDER_MM) <= lim

# ── 连杆碰撞体半径（mm，从基座往上逐段变细）──
#   顺序与 link_capsules() 的段一一对应：
#   底座 / 大臂 / 肘 / 小臂 / 腕→法兰 / 法兰→吸盘唇口
LINK_RADII_MM: Tuple[float, ...] = (90.0, 62.0, 55.0, 55.0, 45.0, 32.0)

# 地面平面高度（基座系，mm）。场景原点就在工厂地面，所以是 0。
GROUND_Z_MM = 0.0


# ══════════════════════════════════════════════════════════════════════════
#  几何基元
# ══════════════════════════════════════════════════════════════════════════
def scene_object_aabb(obj: Dict) -> Optional[Tuple[Tuple[float, float, float],
                                                   Tuple[float, float, float]]]:
    """场景物体 → 基座系轴对齐包围盒 (lo, hi)；字段不全返回 None。

    前端场景系 (sx, sy, sz) 是 Y-up：sy 是高度。
    基座系 (bx, by, bz) 是 Z-up：bx = sx, by = -sz, bz = sy。
    尺寸同理：场景 x→基座 x，场景 y(高)→基座 z，场景 z→基座 y。
    """
    pos = obj.get('position') or []
    size = obj.get('size') or []
    if len(pos) < 3 or len(size) < 3:
        return None
    try:
        cx, cy, cz = float(pos[0]), float(pos[1]), float(pos[2])
        sx, sy, sz = float(size[0]), float(size[1]), float(size[2])
    except (TypeError, ValueError):
        return None
    if min(sx, sy, sz) <= 0:
        return None
    bx, by, bz = cx, -cz, cy
    hx, hy, hz = sx / 2.0, sz / 2.0, sy / 2.0   # 基座系各半尺寸
    return ((bx - hx, by - hy, bz - hz), (bx + hx, by + hy, bz + hz))


def segment_aabb_hit(p0: Sequence[float], p1: Sequence[float], radius: float,
                     lo: Sequence[float], hi: Sequence[float]) -> bool:
    """带半径的线段是否与 AABB 相交（slab 法 + 半径外扩）。

    把 AABB 各面外扩 radius，再做线段-AABB 相交 —— 这是「胶囊 vs 盒子」的
    经典保守近似：球-盒情形精确，圆柱段情形偏保守（宁可多判一点）。
    """
    tmin, tmax = 0.0, 1.0
    for i in range(3):
        a = float(p0[i])
        b = float(p1[i])
        l = float(lo[i]) - radius
        h = float(hi[i]) + radius
        d = b - a
        if abs(d) < 1e-9:
            if a < l or a > h:
                return False
            continue
        t1 = (l - a) / d
        t2 = (h - a) / d
        if t1 > t2:
            t1, t2 = t2, t1
        if t1 > tmin:
            tmin = t1
        if t2 < tmax:
            tmax = t2
        if tmin > tmax:
            return False
    return True


def aabb_overlap(lo1, hi1, lo2, hi2, gap: float = 0.0, gap_z: float = 0.0) -> bool:
    """两个 AABB 是否重叠。

    gap   —— 水平两轴要求的最小间隔（防贴在一起视觉上像粘连）。
    gap_z —— 竖直方向单独的口径。**默认 0，即"正好相接"不算重叠**，
             这样「把方块叠到另一个方块顶面」不会被误判成穿透；
             传一个很小的负数可以容忍浮点噪声导致的微小嵌入。
    """
    if hi1[0] + gap <= lo2[0] or hi2[0] + gap <= lo1[0]:
        return False
    if hi1[1] + gap <= lo2[1] or hi2[1] + gap <= lo1[1]:
        return False
    if hi1[2] + gap_z <= lo2[2] or hi2[2] + gap_z <= lo1[2]:
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════
#  机械臂本体
# ══════════════════════════════════════════════════════════════════════════
def link_capsules(joints_deg: Sequence[float]) -> List[Tuple[Tuple[float, float, float],
                                                             Tuple[float, float, float],
                                                             float]]:
    """把当前位姿下的机械臂简化成 6 段胶囊 (p0, p1, radius)，基座系。

    骨架点全部取真实运动学解，不另设模型：
        P0 基座原点 → J2 → J3 → J4 → J5(腕心) → 法兰面 → 吸盘唇口
    其中「法兰面 → 吸盘唇口」这一段在后端运动学里没有实体（吸盘是前端加的
    仿真工具），但长度恰好等于 TOOL_LENGTH_MM —— 前端 GRIPPER_LENGTH_MM 也是
    这个值，两边必须一致，否则碰撞体和画面对不上。
    """
    fr = fk_frames(joints_deg)
    p0 = fr[0][:3, 3]
    p_j2 = fr[2][:3, 3]
    p_j3 = fr[3][:3, 3]
    p_j4 = fr[4][:3, 3]
    p_j5 = fr[5][:3, 3]
    normal = fr[6][:3, 0]                       # 法兰外法线（单位向量）
    flange = fr[6][:3, 3] + normal * TOOL_LENGTH_MM
    lip = flange + normal * TOOL_LENGTH_MM      # 吸盘唇口

    pts = (p0, p_j2, p_j3, p_j4, p_j5, flange, lip)
    segs = []
    for i in range(len(LINK_RADII_MM)):
        a = (float(pts[i][0]), float(pts[i][1]), float(pts[i][2]))
        b = (float(pts[i + 1][0]), float(pts[i + 1][1]), float(pts[i + 1][2]))
        segs.append((a, b, float(LINK_RADII_MM[i])))
    return segs


def held_object_aabb(joints_deg: Sequence[float], size: Sequence[float]):
    """吸盘所持物体在当前位姿下的 AABB（基座系，近似）。

    物体被吸在唇口上、沿法兰法线向外延伸，所以中心 = 唇口 + 法线 × 高度/2。
    姿态朝下时（PICK/PLACE 固定用 B=90°）结果就是轴对齐的，与画面一致；
    其它姿态下这个近似会偏，但搬运时不会偏离到影响判断的程度。
    """
    if not size or len(size) < 3:
        return None
    try:
        sx, sy, sz = float(size[0]), float(size[1]), float(size[2])
    except (TypeError, ValueError):
        return None
    if min(sx, sy, sz) <= 0:
        return None

    fr = fk_frames(joints_deg)
    normal = fr[6][:3, 0]
    flange = fr[6][:3, 3] + normal * TOOL_LENGTH_MM
    lip = flange + normal * TOOL_LENGTH_MM
    c = lip + normal * (sy / 2.0)
    c = (float(c[0]), float(c[1]), float(c[2]))
    # 尺寸：高度沿法线方向。法线基本是 ±Z 时，直接用场景系尺寸映射。
    hx, hy, hz = sx / 2.0, sz / 2.0, sy / 2.0
    return ((c[0] - hx, c[1] - hy, c[2] - hz), (c[0] + hx, c[1] + hy, c[2] + hz))


# ══════════════════════════════════════════════════════════════════════════
#  对外主入口
# ══════════════════════════════════════════════════════════════════════════
def check_pose(
    joints_deg: Sequence[float],
    scene_objects: Optional[Iterable[Dict]] = None,
    ignore_names: Iterable[str] = (),
    held_size: Optional[Sequence[float]] = None,
    check_ground: bool = True,
    ground_z: float = GROUND_Z_MM,
) -> List[Dict]:
    """检查一个位姿是否碰撞，返回碰撞列表（空列表 = 无碰撞）。

    @param ignore_names 这些名字的物体不参与检测（正在抓取/放置的目标物体、
                        手上已吸住的物体 —— 接触它们是正常的）。
    @param held_size    吸盘所持物体的 size，会跟着法兰一起参与检测。
    @returns [{'type': 'object'|'ground', 'name': str, 'z': float}] 便于直接打日志
    """
    ignore = {n for n in (ignore_names or ()) if n}
    boxes: List[Tuple[str, tuple, tuple]] = []
    for obj in scene_objects or ():
        name = obj.get('name') or ''
        if name and name in ignore:
            continue
        bb = scene_object_aabb(obj)
        if bb:
            boxes.append((name or '未命名物体', bb[0], bb[1]))

    # 吸盘所持物体：它**刚性挂**在唇口上，顶面正好贴着唇口 —— 拿它去和
    # 连杆做检测，唇口那一段（半径 32mm）必然"碰到"它自己，于是每一次搬运
    # 都会被判碰撞、整段中止。所以它只跟场景物体比（AABB vs AABB），
    # 不参与连杆检测。
    held_bb = held_object_aabb(joints_deg, held_size) if held_size else None

    segs = link_capsules(joints_deg)
    hits: List[Dict] = []
    seen = set()

    for idx, (a, b, r) in enumerate(segs):
        # 地面：跳过第 0 段（底座）。底座本来就装在地面上，按半径判会永远报。
        if check_ground and idx > 0:
            low = min(a[2], b[2]) - r
            if low < ground_z:
                key = ('ground',)
                if key not in seen:
                    seen.add(key)
                    hits.append({'type': 'ground', 'name': '地面', 'z': round(low, 1)})
        for (name, lo, hi) in boxes:
            if segment_aabb_hit(a, b, r, lo, hi):
                key = ('object', name)
                if key not in seen:
                    seen.add(key)
                    hits.append({'type': 'object', 'name': name, 'z': 0.0})

    # 手上这个物体会不会插进别的物体里（"不要互相穿透"在搬运途中的那一半）
    if held_bb:
        for (name, lo, hi) in boxes:
            if aabb_overlap(held_bb[0], held_bb[1], lo, hi, gap=0.0, gap_z=-0.5):
                key = ('carried', name)
                if key not in seen:
                    seen.add(key)
                    hits.append({
                        'type': 'object', 'name': name, 'z': 0.0, 'carried': True,
                    })

    return hits


def describe(hits: Sequence[Dict]) -> str:
    """把碰撞列表拼成一句人能读的日志。"""
    if not hits:
        return ''
    parts = []
    for h in hits:
        if h.get('type') == 'ground':
            parts.append(f"机身插入地面 {abs(h.get('z', 0)):.0f}mm")
        elif h.get('carried'):
            parts.append(f"手上物体插入「{h.get('name')}」")
        else:
            parts.append(f"与「{h.get('name')}」碰撞")
    return '、'.join(parts)


# ══════════════════════════════════════════════════════════════════════════
#  接近高度（校验 / 执行共用）
# ══════════════════════════════════════════════════════════════════════════
def carry_height(contact_z: float, clearance_mm: float = APPROACH_CLEARANCE_MM) -> float:
    """PICK/PLACE 的安全接近高度（TCP z，基座系 mm）= 接触面 + 工具长 + 间隙。

    单独抽成一个函数（而不是在两边各写一遍 `z + h + TOOL + clearance`）是为了
    把「校验器和执行器必须算出同一个高度」变成结构上不可能违反的事。
    高度一旦漂移，后果就是最难受的那类 bug：校验通过、执行撞车。

    历史上试过在这里按「场景里最高的物体」动态抬高，想顺手解决横移擦边 ——
    实测没必要：横移改成关节空间插补（executor._travel_to）之后，TCP 走的是
    一条弧线，本来就不会贴着旁边的方块擦过去；而硬抬高会让远端目标在
    高处无解，把合法程序判成 IK_FAIL。所以这里保持"够用就好"。
    """
    return float(contact_z) + TOOL_LENGTH_MM + float(clearance_mm)


# ══════════════════════════════════════════════════════════════════════════
#  放置落点避让
# ══════════════════════════════════════════════════════════════════════════
def find_free_spot(
    x: float,
    y: float,
    z: float,
    size: Sequence[float],
    scene_objects: Optional[Iterable[Dict]] = None,
    ignore_names: Iterable[str] = (),
    gap_mm: float = 10.0,
    max_ring: int = 6,
    step_mm: float = 0.0,
    wrist_z: Optional[float] = None,
) -> Tuple[float, float, bool]:
    """在 (x, y) 附近找一个不与其它物体重叠的落点（基座系）。

    @param z    支撑面高度（mm）。物体底面落在这里，AABB 竖直范围是 [z, z+高]。
                必须传 z：不同层的物体不该互相挤走 —— 托盘上的方块和地面上的
                方块 xy 可以重合，那是正常堆叠，不是穿透。
    @param wrist_z  放到该落点时**腕心**的高度（= 接触面 + 工具长）。给了就
                要求候选点的腕心还在可达球内 —— 避让不能把一个够得着的落点
                换成一个够不着的（那样程序会更早挂在 IK 上）。不给则不筛。

    搜索方式：以目标点为圆心逐圈外扩，每圈取 8 个方向 —— 简单、可预测，
    而且**偏移量最小**（第一圈就能命中时几乎无感）。

    @returns (x', y', moved)；moved=False 表示原落点就可用（或实在找不到空位）。
    """
    try:
        sx, sy, sz = float(size[0]), float(size[1]), float(size[2])
    except (TypeError, ValueError, IndexError):
        return x, y, False
    if min(sx, sy, sz) <= 0:
        return x, y, False

    ignore = {n for n in (ignore_names or ()) if n}
    step = step_mm or (max(sx, sz) * 1.15)   # 一圈的步长按物体自身尺寸定
    half_x, half_y = sx / 2.0, sz / 2.0      # 目标物体的基座系水平半尺寸

    def occupied(cx: float, cy: float) -> Optional[str]:
        lo = (cx - half_x, cy - half_y, float(z))
        hi = (cx + half_x, cy + half_y, float(z) + sy)
        for obj in scene_objects or ():
            name = obj.get('name') or ''
            if name and name in ignore:
                continue
            bb = scene_object_aabb(obj)
            if not bb:
                continue
            if aabb_overlap(lo, hi, bb[0], bb[1], gap=gap_mm, gap_z=-0.5):
                return name or '未命名物体'
        return None

    if occupied(x, y) is None:
        return x, y, False

    for ring in range(1, max_ring + 1):
        r = step * ring
        for k in range(8):
            ang = math.pi / 4 * k
            cx = x + r * math.cos(ang)
            cy = y + r * math.sin(ang)
            # 避让点必须先"够得着"再谈占用：出圈的候选点即使空着也放不了
            # （IK 会解不出来），拿它换掉一个合法落点等于把程序直接搞挂。
            if wrist_z is not None and not reachable(cx, cy, wrist_z):
                continue
            if occupied(cx, cy) is None:
                return cx, cy, True

    # 一圈圈找下来没有既空又够得着的位置 —— 退回原点，
    # 让执行期的碰撞复查去拦（宁可明确报错，也不要偷偷穿模）。
    return x, y, False
