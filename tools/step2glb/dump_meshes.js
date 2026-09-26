/**
 * dump_meshes.js — 一次性解析 STEP，导出每个网格的几何 + 正确解码的部件名 + 包围盒
 * 结果缓存到 build/meshes.json，供后续 build_glb.js 复用（避免重复 40s+ 解析）
 *
 * 用法: node dump_meshes.js <file.step>
 */
'use strict';

const fs = require('fs');
const path = require('path');
const occtimportjs = require('occt-import-js');

const stepPath = process.argv[2];
if (!stepPath) {
  console.error('usage: node dump_meshes.js <file.step>');
  process.exit(2);
}

const outDir = path.join(__dirname, 'build');
fs.mkdirSync(outDir, { recursive: true });

/** STEP 里的部件名是 GBK 编码被当作 latin1 读出来的，这里还原 */
function fixName(raw) {
  if (!raw) return '';
  try {
    const bytes = Buffer.from(raw, 'latin1');
    const decoded = new TextDecoder('gbk').decode(bytes);
    // 若解码出替换字符则回退原文
    return decoded.includes('\uFFFD') ? raw : decoded;
  } catch {
    return raw;
  }
}

(async () => {
  const occt = await occtimportjs({
    locateFile: (f) => path.join(__dirname, 'node_modules', 'occt-import-js', 'dist', f),
  });

  const buf = fs.readFileSync(stepPath);
  console.log(`解析 ${path.basename(stepPath)} (${(buf.length / 1048576).toFixed(2)} MB) ...`);
  const t0 = Date.now();
  const res = occt.ReadStepFile(new Uint8Array(buf), {
    linearUnit: 'millimeter',
    linearDeflectionType: 'bounding_box_ratio',
    linearDeflection: 0.0015,
    angularDeflection: 0.5,
  });
  console.log(`  success=${res.success}  耗时=${((Date.now() - t0) / 1000).toFixed(1)}s`);
  if (!res.success) process.exit(1);

  // ── 节点树 → mesh 归属 ──────────────────────────────────────────
  // occt-import-js: 节点上的 meshes 数组元素可能是 mesh 对象或索引，这里都兼容
  const meshOwner = new Map(); // meshObjIndex -> nodePath
  const nodeTree = [];
  const meshRefs = new Set();

  const walk = (n, depth, parentPath) => {
    if (!n) return;
    const nm = fixName((n.name || '').trim());
    const p = parentPath ? `${parentPath}/${nm || '(unnamed)'}` : nm || '(root)';
    const kids = n.children || [];
    const ms = n.meshes || [];
    nodeTree.push({
      path: p,
      depth,
      name: nm,
      meshCount: ms.length,
      children: kids.length,
    });
    ms.forEach((m) => {
      if (m && typeof m === 'object') meshRefs.add(m);
    });
    kids.forEach((c) => walk(c, depth + 1, p));
  };
  walk(res.root, 0, '');

  // ── 每个 mesh 的几何信息 ────────────────────────────────────────
  const out = [];
  res.meshes.forEach((m, i) => {
    const pos = (m.attributes && m.attributes.position && m.attributes.position.array) || null;
    const nor = (m.attributes && m.attributes.normal && m.attributes.normal.array) || null;
    const idx = (m.index && m.index.array) || null;

    let bb = null;
    if (pos && pos.length >= 3) {
      let x0 = Infinity, y0 = Infinity, z0 = Infinity;
      let x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
      for (let k = 0; k < pos.length; k += 3) {
        const x = pos[k], y = pos[k + 1], z = pos[k + 2];
        if (x < x0) x0 = x; if (x > x1) x1 = x;
        if (y < y0) y0 = y; if (y > y1) y1 = y;
        if (z < z0) z0 = z; if (z > z1) z1 = z;
      }
      bb = { min: [x0, y0, z0], max: [x1, y1, z1] };
    }

    const owner = nodeTree.length ? null : null;
    out.push({
      i,
      name: fixName((m.name || '').trim()),
      verts: pos ? pos.length / 3 : 0,
      tris: idx ? idx.length / 3 : 0,
      hasNormal: !!nor,
      color: m.color || null,
      bbox: bb,
      nodePath: owner,
      positions: pos ? Array.from(pos) : null,
      normals: nor ? Array.from(nor) : null,
      indices: idx ? Array.from(idx) : null,
    });
  });

  // ── 反查 node 归属 ─────────────────────────────────────────────
  const idxOf = new Map();
  res.meshes.forEach((m, i) => idxOf.set(m, i));
  const assign = (n, parentPath) => {
    if (!n) return;
    const nm = fixName((n.name || '').trim());
    const p = parentPath ? `${parentPath}/${nm || '(unnamed)'}` : nm || '(root)';
    (n.meshes || []).forEach((m) => {
      const i = idxOf.get(m);
      if (i !== undefined && !out[i].nodePath) out[i].nodePath = p;
    });
    (n.children || []).forEach((c) => assign(c, p));
  };
  assign(res.root, '');

  fs.writeFileSync(path.join(outDir, 'meshes.json'), JSON.stringify({
    source: path.basename(stepPath),
    meshCount: out.length,
    nodeTree,
    meshes: out,
  }));
  // 只存名字的小索引，便于快速查看
  fs.writeFileSync(path.join(outDir, 'meshes_index.json'), JSON.stringify({
    meshCount: out.length,
    nodeTree,
    meshes: out.map(({ positions, normals, indices, ...rest }) => rest),
  }, null, 2));

  console.log(`\n${'idx'.padStart(3)} | ${'顶点'.padStart(7)} | ${'三角面'.padStart(7)} | 部件名 / 归属节点`);
  console.log('-'.repeat(100));
  out.forEach((m) => {
    console.log(
      `${String(m.i).padStart(3)} | ${String(m.verts).padStart(7)} | ${String(m.tris).padStart(7)} | ` +
      `${m.name || '(无名)'}   << ${m.nodePath || '?'}`
    );
  });

  console.log(`\n--- 节点树 ---`);
  nodeTree.forEach((n) => {
    console.log(`${'  '.repeat(n.depth)}- ${n.name || '(unnamed)'}  meshes=${n.meshCount} children=${n.children}`);
  });

  console.log(`\n--- 包围盒 (mm, 用于判定各部件在机器人上的位置) ---`);
  out.forEach((m) => {
    if (!m.bbox) return;
    const { min, max } = m.bbox;
    console.log(
      `${String(m.i).padStart(3)} ${(m.name || '(无名)').slice(0, 22).padEnd(24)} ` +
      `x[${min[0].toFixed(0)},${max[0].toFixed(0)}] y[${min[1].toFixed(0)},${max[1].toFixed(0)}] z[${min[2].toFixed(0)},${max[2].toFixed(0)}]`
    );
  });

  console.log(`\n已写入 build/meshes.json 与 build/meshes_index.json`);
})();
