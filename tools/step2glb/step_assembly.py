# -*- coding: utf-8 -*-
"""
step_assembly.py — 解析 STEP AP214 装配结构，还原每个部件的全局变换矩阵

AP214 装配链路:
  CONTEXT_DEPENDENT_SHAPE_REPRESENTATION( #rel, #pds )
    -> #rel  = REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION( #itt ) + (rep_parent, rep_child)
    -> #itt  = ITEM_DEFINED_TRANSFORMATION( transformer = A1(paren), A2(child) )
    -> #pds  = PRODUCT_DEFINITION_SHAPE -> PRODUCT_DEFINITION (子件)
  NEXT_ASSEMBLY_USAGE_OCCURRENCE( parent_pd, child_pd )  给出装配父子关系

变换: M_parent<-child = A1 * inverse(A2)

产出:
  1) 每个部件的全局位姿（原点 + 三轴）—— 部件局部原点通常就落在其关节轴线上
  2) 把 CIRCLE 实体按其所属部件变换到全局坐标 —— 得到精确的关节轴线
  3) 装配树

用法: python step_assembly.py <file.step> [输出json]
"""
import json
import math
import os
import re
import sys

import numpy as np

ENT_RE = re.compile(r'^#(\d+)\s*=\s*([A-Z_0-9]+)\s*\((.*)\)\s*;?\s*$', re.S)
CMPLX_RE = re.compile(r'^#(\d+)\s*=\s*\((.*)\)\s*;?\s*$', re.S)
REF_RE = re.compile(r'#(\d+)')
STR_RE = re.compile(r"'((?:[^']|'')*)'")
NUM_RE = re.compile(r'-?\d+\.?\d*(?:[eE][-+]?\d+)?')


# ══════════════════════════════════════════════════════════════
def parse_entities(path):
    """流式解析 STEP DATA 段。复合实体（形如 #1 =( A(...) B(...) )）记为 COMPLEX"""
    ents = {}
    buf = []
    with open(path, "r", encoding="latin-1") as f:
        in_data = False
        for line in f:
            if not in_data:
                if line.startswith("DATA;"):
                    in_data = True
                continue
            if line.startswith("ENDSEC;"):
                break
            buf.append(line)
            joined = "".join(buf)
            if ";" in joined:
                parts = joined.split(";")
                buf = [parts[-1]]
                for chunk in parts[:-1]:
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    m = ENT_RE.match(chunk + ";")
                    if m:
                        ents[int(m.group(1))] = (m.group(2), m.group(3))
                        continue
                    m2 = CMPLX_RE.match(chunk + ";")
                    if m2:
                        ents[int(m2.group(1))] = ("COMPLEX", m2.group(2))
    return ents


def refs(body):
    return [int(x) for x in REF_RE.findall(body)]


def strings(body):
    return [s.replace("''", "'") for s in STR_RE.findall(body)]


def nums(body):
    # 去掉引用与字符串后再取数字
    b = REF_RE.sub(" ", body)
    b = STR_RE.sub(" ", b)
    return [float(x) for x in NUM_RE.findall(b)]


# ══════════════════════════════════════════════════════════════
def axis2placement(ents, aid):
    """AXIS2_PLACEMENT_3D -> 4x4 齐次矩阵（局部->父）"""
    e = ents.get(aid)
    if not e or e[0] != "AXIS2_PLACEMENT_3D":
        return None
    r = refs(e[1])
    if len(r) < 2:
        return None
    p = ents.get(r[0])
    zdir = ents.get(r[1])
    xdir = ents.get(r[2]) if len(r) > 2 else None
    if not p or not zdir:
        return None
    loc = np.array(nums(p[1])[:3], dtype=float)
    z = np.array(nums(zdir[1])[:3], dtype=float)
    if len(loc) < 3 or len(z) < 3:
        return None
    z = z / (np.linalg.norm(z) or 1.0)
    if xdir:
        x = np.array(nums(xdir[1])[:3], dtype=float)
    else:
        x = np.array([1.0, 0.0, 0.0])
    x = x - np.dot(x, z) * z
    nx = np.linalg.norm(x)
    if nx < 1e-9:
        x = np.array([1.0, 0.0, 0.0])
        x = x - np.dot(x, z) * z
        nx = np.linalg.norm(x)
        if nx < 1e-9:
            x = np.array([0.0, 1.0, 0.0])
            x = x - np.dot(x, z) * z
            nx = np.linalg.norm(x)
    x = x / (nx or 1.0)
    y = np.cross(z, x)
    M = np.eye(4)
    M[:3, 0] = x
    M[:3, 1] = y
    M[:3, 2] = z
    M[:3, 3] = loc
    return M


def product_name(ents, pd_id, depth=0):
    """PRODUCT_DEFINITION -> 产品名"""
    if depth > 8:
        return None
    e = ents.get(pd_id)
    if not e:
        return None
    t, body = e
    if t == "PRODUCT":
        s = strings(body)
        return s[1] if len(s) >= 2 else (s[0] if s else None)
    for r in refs(body):
        nm = product_name(ents, r, depth + 1)
        if nm:
            return nm
    return None


# ══════════════════════════════════════════════════════════════
def main():
    path = sys.argv[1]
    out_json = sys.argv[2] if len(sys.argv) > 2 else None

    print(f"解析 {os.path.basename(path)} ...")
    ents = parse_entities(path)
    print(f"  实体总数 = {len(ents)}")

    # ── 1. CDSR -> (child_pd, transform_matrix) ──────────────────
    # PRODUCT_DEFINITION_SHAPE 的第 3 个引用可能是 NAUO，也可能是 product_definition
    pds2ref = {}
    for i, (t, body) in ents.items():
        if t == "PRODUCT_DEFINITION_SHAPE":
            r = refs(body)
            if r:
                pds2ref[i] = r[-1]

    # NAUO -> (parent_pd, child_pd, nauo_id)
    nauos = []
    nauo_child = {}
    for i, (t, body) in ents.items():
        if t == "NEXT_ASSEMBLY_USAGE_OCCURRENCE":
            r = refs(body)
            if len(r) >= 2:
                nauos.append((r[-2], r[-1], i))
                nauo_child[i] = r[-1]
    print(f"  NEXT_ASSEMBLY_USAGE_OCCURRENCE = {len(nauos)}")

    def resolve_child_pd(pds_id):
        """PRODUCT_DEFINITION_SHAPE -> (child product_definition)"""
        x = pds2ref.get(pds_id)
        if x is None:
            return None
        return nauo_child.get(x, x)

    # CDSR -> child_pd + transform
    child_xf = {}   # child_pd -> (M, nauo_name)
    cd_details = []
    for i, (t, body) in ents.items():
        if t != "CONTEXT_DEPENDENT_SHAPE_REPRESENTATION":
            continue
        r = refs(body)
        if len(r) < 2:
            continue
        rel_id, pds_id = r[0], r[1]
        child_pd = resolve_child_pd(pds_id)
        if child_pd is None:
            continue
        # rel 多为复合实体: 在其中找出 ITEM_DEFINED_TRANSFORMATION
        rel = ents.get(rel_id)
        M = None
        itt_id = None
        if rel:
            for cand in refs(rel[1]):
                ce = ents.get(cand)
                if ce and ce[0] == "ITEM_DEFINED_TRANSFORMATION":
                    itt_id = cand
                    ir = refs(ce[1])
                    if len(ir) >= 2:
                        A1 = axis2placement(ents, ir[-2])
                        A2 = axis2placement(ents, ir[-1])
                        if A1 is not None and A2 is not None:
                            M = A1 @ np.linalg.inv(A2)
                    break
        cd_details.append((child_pd, M, pds_id, rel_id, itt_id))
        if M is not None:
            child_xf.setdefault(child_pd, []).append(M)

    print(f"  CONTEXT_DEPENDENT_SHAPE_REPRESENTATION = {len(cd_details)}  (含可用变换 {sum(1 for d in cd_details if d[1] is not None)})")

    # ── 2. 装配树 ────────────────────────────────────────────────
    children_of = {}
    all_children = set()
    all_parents = set()
    for p, c, nid in nauos:
        children_of.setdefault(p, []).append((c, nid))
        all_children.add(c)
        all_parents.add(p)
    roots = [p for p in all_parents if p not in all_children]
    print(f"  根节点 product_definition 数 = {len(roots)}")

    # 每个部件在全局的变换（从根复合下来）
    global_xf = {}
    names = {}

    def walk(pd_id, M, depth, seen):
        if pd_id in seen or depth > 12:
            return
        seen = seen | {pd_id}
        global_xf[pd_id] = M
        if pd_id not in names:
            names[pd_id] = product_name(ents, pd_id) or f"pd{pd_id}"
        for c, nid in children_of.get(pd_id, []):
            Ms = child_xf.get(c)
            if not Ms:
                walk(c, M, depth + 1, seen)
                continue
            # 若有多个 CDSR，取第一个（SolidWorks 通常只 1 个）
            walk(c, M @ Ms[0], depth + 1, seen)

    for r in roots:
        walk(r, np.eye(4), 0, frozenset())
    # 兜底：未被遍历到的
    for pd in set(list(all_children) + list(all_parents)):
        if pd not in global_xf:
            walk(pd, np.eye(4), 0, frozenset())

    print(f"  参与装配的部件数 = {len(global_xf)}")

    # ── 3. 输出部件表 ────────────────────────────────────────────
    print("\n" + "=" * 104)
    print(f"{'product_definition':>18} | {'部件名':<34} | {'全局原点 (mm)':<30} | 局部轴->全局轴")
    print("=" * 104)
    for pd_id, M in sorted(global_xf.items(), key=lambda kv: names.get(kv[0], "")):
        o = M[:3, 3]
        zx = M[:3, 2]
        print(f"{pd_id:>18} | {(names.get(pd_id) or '?')[:34]:<34} | "
              f"({o[0]:8.2f},{o[1]:8.2f},{o[2]:8.2f})".ljust(34) +
              f"| z->({zx[0]:+.3f},{zx[1]:+.3f},{zx[2]:+.3f})")

    result = {
        "parts": [
            {"pd": pd_id, "name": names.get(pd_id), "origin": global_xf[pd_id][:3, 3].tolist(),
             "matrix": global_xf[pd_id].tolist()}
            for pd_id in global_xf
        ],
        "nauo_count": len(nauos),
    }

    # ── 4. CIRCLE 实体 -> 全局（按部件 bbox 归属） ───────────────
    if out_json:
        bbox = {}
        bj = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build", "meshes_index.json")
        if os.path.exists(bj):
            with open(bj, "r", encoding="utf-8") as f:
                md = json.load(f)
            for m in md["meshes"]:
                if m.get("bbox"):
                    bbox[m["i"]] = (np.array(m["bbox"]["min"]), np.array(m["bbox"]["max"]))

        a2p = {}
        for i, (t, body) in ents.items():
            if t == "AXIS2_PLACEMENT_3D":
                a2p[i] = refs(body)

        circles = []
        for i, (t, body) in ents.items():
            if t != "CIRCLE":
                continue
            r = refs(body)
            if not r:
                continue
            n = nums(body)
            if not n:
                continue
            radius = n[-1]
            if radius < 8.0:
                continue
            pl = a2p.get(r[0])
            if not pl or len(pl) < 2:
                continue
            pe = ents.get(pl[0]); ze = ents.get(pl[1])
            if not pe or not ze:
                continue
            center = np.array(nums(pe[1])[:3], dtype=float)
            zdir = np.array(nums(ze[1])[:3], dtype=float)
            if len(center) < 3 or len(zdir) < 3:
                continue
            zdir = zdir / (np.linalg.norm(zdir) or 1.0)
            circles.append({"id": i, "c": center, "d": zdir, "r": radius})

        # ── 把每个圆的「部件局部坐标」用各部件变换试一遍，取圆周完全落入的最小包围盒 ──
        vol = {mi: float(np.prod(hi - lo)) for mi, (lo, hi) in bbox.items()}
        xf_list = [M for M in global_xf.values()]
        assigned = []
        dropped = 0
        for cir in circles:
            c, d, r = cir["c"], cir["d"], cir["r"]
            best = None
            for M in xf_list:
                gc = (M[:3, :3] @ c) + M[:3, 3]
                gd = M[:3, :3] @ d
                gd = gd / (np.linalg.norm(gd) or 1.0)
                tmp = np.array([1.0, 0.0, 0.0]) if abs(gd[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
                u = np.cross(gd, tmp); u /= (np.linalg.norm(u) or 1.0)
                w = np.cross(gd, u); w /= (np.linalg.norm(w) or 1.0)
                pts = [gc, gc + r * u, gc - r * u, gc + r * w, gc - r * w]
                for mi, (lo, hi) in bbox.items():
                    if all(np.all(p >= lo - 3.0) and np.all(p <= hi + 3.0) for p in pts):
                        if best is None or vol[mi] < best[0]:
                            best = (vol[mi], mi, gc, gd)
            if best is not None:
                assigned.append({
                    "circle": cir["id"], "mesh": best[1], "r": r,
                    "center": np.round(best[2], 3).tolist(), "dir": np.round(best[3], 4).tolist(),
                })
            else:
                dropped += 1

        print(f"\n  CIRCLE(半径>=8mm) = {len(circles)}，成功归属到部件 bbox 的 = {len(assigned)}（丢弃 {dropped}）")

        # 按 mesh + 方向 聚合，找同心圆簇
        from collections import defaultdict
        grp = defaultdict(list)
        for a in assigned:
            d = np.abs(np.array(a["dir"]))
            axis = "X" if d[0] > 0.9 else ("Y" if d[1] > 0.9 else ("Z" if d[2] > 0.9 else "oblique"))
            grp[(a["mesh"], axis)].append(a)

        print("\n--- 各部件内的轴线候选（同心圆簇，按圆数排序） ---")
        axis_candidates = []
        for (mi, axis), lst in sorted(grp.items(), key=lambda kv: -len(kv[1])):
            if mi not in (6, 7, 8, 9, 10, 11, 12, 13):
                continue
            # 聚类
            clusters = []
            for a in sorted(lst, key=lambda z: -z["r"]):
                c = np.array(a["center"])
                hit = None
                for cl in clusters:
                    cc = np.array(cl["center"])
                    if not np.allclose(np.abs(np.array(cl["dir"])), np.abs(np.array(a["dir"])), atol=1e-3):
                        continue
                    # 点到另一直线的垂距
                    dvec = np.array(cl["dir"], dtype=float)
                    v = c - cc
                    perp = v - np.dot(v, dvec) * dvec
                    if np.linalg.norm(perp) < 3.0:
                        hit = cl
                        break
                if hit:
                    hit["rs"].append(a["r"])
                else:
                    clusters.append({"center": a["center"], "dir": a["dir"], "rs": [a["r"]]})
            strong = sorted([c for c in clusters if len(set(round(x, 1) for x in c["rs"])) >= 2],
                            key=lambda z: -len(set(round(x, 1) for x in z["rs"])))
            if strong:
                print(f"  mesh[{mi}] 方向{axis}: {len(strong)} 个同心簇")
                for c in strong[:8]:
                    cc = c["center"]
                    rs = sorted(set(round(x, 1) for x in c["rs"]))
                    print(f"      轴线上点=({cc[0]:8.2f},{cc[1]:8.2f},{cc[2]:8.2f})"
                          f"  方向={np.round(c['dir'],3)}  R={rs}")
                    axis_candidates.append({"mesh": mi, "axis": axis, "point": cc, "dir": c["dir"],
                                            "radii": rs, "count": len(rs)})

        result["circles_assigned"] = len(assigned)
        result["axis_candidates"] = axis_candidates

        # 存一份完整圆数据备用
        with open(os.path.join(os.path.dirname(bj), "circles_global.json"), "w", encoding="utf-8") as f:
            json.dump(assigned, f)

    if out_json:
        with open(out_json, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=1)
        print(f"\n已写出 {out_json}")


if __name__ == "__main__":
    main()
