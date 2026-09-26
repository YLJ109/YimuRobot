/**
 * verify_glb.js — 解析产出的 GLB，校验层级、关节局部位置与正向运动学是否与实测几何自洽
 *
 * 只依赖 Node 内置能力（手工解析 GLB 容器），不需要浏览器环境。
 * 矩阵一律使用 glTF 的**列主序**约定：m[j*4+i] = 第 j 列第 i 行。
 *
 * 用法: node verify_glb.js [er3_600.glb]
 */
'use strict';

const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const GLB = process.argv[2] || path.join(HERE, 'build', 'er3_600.glb');
const META = path.join(path.dirname(GLB), 'er3_600_joints.json');

function readGlb(file) {
  const buf = fs.readFileSync(file);
  if (buf.readUInt32LE(0) !== 0x46546c67) throw new Error('不是合法 GLB（magic 不符）');
  const version = buf.readUInt32LE(4);
  const total = buf.readUInt32LE(8);
  let off = 12;
  let json = null, bin = null;
  while (off + 8 <= buf.length) {
    const len = buf.readUInt32LE(off);
    const type = buf.readUInt32LE(off + 4);
    const data = buf.subarray(off + 8, off + 8 + len);
    if (type === 0x4e4f534a) json = JSON.parse(data.toString('utf8'));
    else if (type === 0x004e4942) bin = data;
    off += 8 + len + ((4 - (len % 4)) % 4);
  }
  return { version, total, json, bin };
}

const DEG = Math.PI / 180;
let pass = 0, fail = 0;
function check(name, ok, detail) {
  if (ok) { pass += 1; console.log(`  [PASS] ${name}${detail ? '   ' + detail : ''}`); }
  else { fail += 1; console.log(`  [FAIL] ${name}${detail ? '   ' + detail : ''}`); }
}

// ── 列主序 4x4 工具 ────────────────────────────────────────────
const I4 = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
function matMul(A, B) {                       // R = A · B
  const R = new Array(16);
  for (let j = 0; j < 4; j++) {
    for (let i = 0; i < 4; i++) {
      let s = 0;
      for (let k = 0; k < 4; k++) s += A[k * 4 + i] * B[j * 4 + k];
      R[j * 4 + i] = s;
    }
  }
  return R;
}
function matRot(axis, deg) {                  // 标准右手系，列主序
  const t = deg * DEG, c = Math.cos(t), s = Math.sin(t);
  if (axis === 'x') return [1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1];
  if (axis === 'y') return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1];
  return [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
}
function matFromNode(n) {
  if (n.matrix) return n.matrix.slice();
  const t = n.translation || [0, 0, 0];
  const q = n.rotation || [0, 0, 0, 1];
  const s = n.scale || [1, 1, 1];
  const [x, y, z, w] = q;
  const x2 = x + x, y2 = y + y, z2 = z + z;
  const xx = x * x2, xy = x * y2, xz = x * z2;
  const yy = y * y2, yz = y * z2, zz = z * z2;
  const wx = w * x2, wy = w * y2, wz = w * z2;
  return [
    (1 - (yy + zz)) * s[0], (xy + wz) * s[0], (xz - wy) * s[0], 0,
    (xy - wz) * s[1], (1 - (xx + zz)) * s[1], (yz + wx) * s[1], 0,
    (xz + wy) * s[2], (yz - wx) * s[2], (1 - (xx + yy)) * s[2], 0,
    t[0], t[1], t[2], 1,
  ];
}
const originOf = (M) => [M[12], M[13], M[14]];
const mm = (p) => p.map((v) => v * 1000);

// ══════════════════════════════════════════════════════════════
const meta = JSON.parse(fs.readFileSync(META, 'utf8'));
const { version, total, json } = readGlb(GLB);
const nodes = json.nodes || [];
const byName = new Map(nodes.map((n, i) => [n.name, i]));

console.log('═'.repeat(76));
console.log('  ER3-600 GLB 校验（几何全部来自官方数模 V1.2 实测）');
console.log('═'.repeat(76));
console.log(`  文件 ${path.basename(GLB)}   glTF ${version}   ${(total / 1048576).toFixed(2)} MB   生成器 ${json.asset && json.asset.generator}`);
console.log(`  节点 ${nodes.length}   网格 ${(json.meshes || []).length}   材质 ${(json.materials || []).length}`);

// ── 1. 节点齐全 ────────────────────────────────────────────────
const expected = ['ER3_600', 'Base', 'J1', 'J2', 'J3', 'J4', 'J5', 'J6', 'WorkspaceEnvelope'];
const missing = expected.filter((n) => !byName.has(n));
check('结构：期望节点齐全', missing.length === 0,
      missing.length ? `缺少 ${missing.join(',')}` : expected.join(' / '));

// ── 2. 父子链 ──────────────────────────────────────────────────
const parentOf = new Map();
nodes.forEach((n, i) => (n.children || []).forEach((c) => parentOf.set(c, i)));
function pathOf(name) {
  const out = [];
  let cur = byName.get(name);
  while (cur !== undefined) { out.unshift(nodes[cur].name); cur = parentOf.get(cur); }
  return out.join('/');
}
{
  const cases = [
    ['J1', 'ER3_600/Base/J1'],
    ['J3', 'ER3_600/Base/J1/J2/J3'],
    ['J6', 'ER3_600/Base/J1/J2/J3/J4/J5/J6'],
    ['WorkspaceEnvelope', 'ER3_600/WorkspaceEnvelope'],
  ];
  const bad = cases.filter(([n, want]) => pathOf(n) !== want);
  check('结构：关节串成一条运动链', bad.length === 0,
        bad.length ? bad.map(([n]) => `${n}→${pathOf(n)}`).join(' ') : 'Base→J1→J2→J3→J4→J5→J6');
}

// ── 3. 相邻关节间距 == 实测杆长 ────────────────────────────────
{
  const T = (name) => {
    const M = matFromNode(nodes[byName.get(name)]);
    return originOf(M);
  };
  // 关节自身局部平移（相对父节点）
  const local = {
    J2: matFromNode(nodes[byName.get('J2')]),
    J3: matFromNode(nodes[byName.get('J3')]),
    J4: matFromNode(nodes[byName.get('J4')]),
    J5: matFromNode(nodes[byName.get('J5')]),
  };
  const rows = [
    ['J2 距 J1 轴 = d1', mm(originOf(local.J2)), [0, 0, meta.measured.d1]],
    ['J3 距 J2   = L2', mm(originOf(local.J3)), [0, 0, meta.measured.L2]],
    ['J4 距 J3   = e3', mm(originOf(local.J4)), [0, 0, meta.measured.e3]],
    ['J5 距 J4   = L4', mm(originOf(local.J5)), [meta.measured.L4, 0, 0]],
  ];
  const bad = [];
  const detail = [];
  for (const [label, got, want] of rows) {
    const err = Math.max(...want.map((v, i) => Math.abs(v - got[i])));
    detail.push(`${label} 实测 ${got.map((v) => v.toFixed(2)).join(',')}`);
    if (err > 1e-3) bad.push(label);
  }
  check('几何：相邻关节间距 == 数模实测杆长', bad.length === 0, detail.join('  |  '));
}

// ── 4. 零位世界轴点 == 实测轴点 ────────────────────────────────
{
  const order = ['Base', 'J1', 'J2', 'J3', 'J4', 'J5', 'J6'];
  const world = new Map();
  for (const n of order) {
    const p = [];
    let cur = byName.get(n);
    while (cur !== undefined) { p.unshift(cur); cur = parentOf.get(cur); }
    let M = I4;
    for (const i of p) M = matMul(M, matFromNode(nodes[i]));
    world.set(n, originOf(M));
  }
  let worst = 0;
  for (const item of meta.nodes) {
    if (!item.pivotMm || !world.has(item.name)) continue;
    const want = item.pivotMm.map((v) => v * 0.001);
    worst = Math.max(worst, Math.max(...want.map((v, i) => Math.abs(v - world.get(item.name)[i]))) * 1000);
  }
  check('几何：零位各关节世界轴点 == 数模实测值', worst < 1e-4, `最大偏差 ${worst.toExponential(2)} mm`);
}

// ── 5. 旋转方向与解析解一致 ────────────────────────────────────
{
  const { d1, L2, e3, L4 } = meta.measured;
  const build = (q2deg) => {
    let M = I4;
    for (const n of ['ER3_600', 'Base', 'J1', 'J2']) {
      const base = matFromNode(nodes[byName.get(n)]);
      M = matMul(M, n === 'J2' ? matMul(base, matRot('y', q2deg)) : base);
    }
    for (const n of ['J3', 'J4', 'J5', 'J6']) M = matMul(M, matFromNode(nodes[byName.get(n)]));
    return mm(originOf(M));       // 腕心（J6 轴点）
  };
  // 解析解：腕心 = J2 + R_y(q)·[(0,0,L2+e3) + (L4,0,0)]
  //   R_y(q)·(x,y,z) = (x cos q + z sin q, y, −x sin q + z cos q)
  const analytic = (q) => {
    const t = q * DEG;
    const a = [0, 0, L2 + e3];
    const b = [L4, 0, 0];
    const r = (v) => [v[0] * Math.cos(t) + v[2] * Math.sin(t), v[1], -v[0] * Math.sin(t) + v[2] * Math.cos(t)];
    const ra = r(a), rb = r(b);
    return [ra[0] + rb[0], ra[1] + rb[1], d1 + ra[2] + rb[2]];
  };
  let worst = 0;
  const samples = [];
  for (const q of [-90, -45, 0, 30, 60, 90, 135]) {
    const got = build(q), want = analytic(q);
    worst = Math.max(worst, Math.max(...want.map((v, i) => Math.abs(v - got[i]))));
    samples.push(`q2=${q}°→(${got.map((v) => v.toFixed(1)).join(',')})`);
  }
  check('运动学：J2 旋转后腕心与解析解一致', worst < 0.01,
        `7 个角度最大偏差 ${worst.toExponential(2)} mm`);
  console.log(`         ${samples.join('  ')}`);
}

// ── 6. 臂展 ────────────────────────────────────────────────────
{
  const { d1, L2, e3, L4, tool } = meta.measured;
  const reach = L2 + Math.hypot(e3, L4);
  check('规格：最大臂展 == 官方标称 593mm', Math.abs(reach - 593) < 1.0,
        `实测 ${reach.toFixed(2)} mm，偏差 ${(reach - 593).toFixed(2)} mm`);
  check('规格：腕心 → 法兰面 78.5mm', Math.abs(tool - 78.5) < 1e-6, `${tool} mm`);
}

// ── 7. 网格归属 ────────────────────────────────────────────────
{
  // glTF 用 `mesh`（单个网格索引）承载网格。运动链是嵌套的，故这里只统计
  // 「直接挂在某连杆节点下」的网格，而不是整棵子树（子树必然包含下级连杆）。
  const directMeshes = (name) => (nodes[byName.get(name)].children || [])
    .filter((c) => nodes[c].mesh !== undefined).length;
  const bad = [];
  const detail = [];
  for (const item of meta.nodes) {
    if (!item.meshes) continue;
    const got = directMeshes(item.name);
    detail.push(`${item.name}:${got}`);
    if (got !== item.meshes.length) bad.push(`${item.name} ${got}≠${item.meshes.length}`);
  }
  check('结构：各连杆直挂网格数与实测归属一致', bad.length === 0,
        bad.length ? bad.join(' ') : detail.join('  '));
  const totalMeshNodes = nodes.filter((n) => n.mesh !== undefined).length;
  check('结构：网格节点数与定义一一对应', totalMeshNodes === (json.meshes || []).length,
        `网格节点 ${totalMeshNodes} / json.meshes ${(json.meshes || []).length}`);
}

// ── 8. 包围盒（模型尺度合理）────────────────────────────────────
{
  const acc = (json.accessors || []);
  let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  (json.meshes || []).forEach((m) => m.primitives.forEach((p) => {
    const a = acc[p.attributes.POSITION];
    if (!a || !a.min || !a.max) return;
    for (let i = 0; i < 3; i++) {
      lo[i] = Math.min(lo[i], a.min[i]);
      hi[i] = Math.max(hi[i], a.max[i]);
    }
  }));
  // 每个连杆几何已被平移到以其自身轴点为原点，故此处为「局部包围盒的并集」
  const ok = hi.every((v, i) => v - lo[i] > 0.05 && v - lo[i] < 1.5);
  check('几何：单位与尺度合理（m 量级）', ok,
        `并集 bbox ${lo.map((v) => v.toFixed(3)).join(',')} … ${hi.map((v) => v.toFixed(3)).join(',')}`);
}

console.log('─'.repeat(76));
console.log(`  结果：${fail === 0 ? '全部通过 ✓' : fail + ' 项失败'}   (通过 ${pass} / 失败 ${fail})`);
console.log('─'.repeat(76));
process.exit(fail === 0 ? 0 : 1);
