/**
 * probe_step.js — 探测 STEP 装配体的节点树 / 部件名 / 网格规模
 * 用法: node probe_step.js <file.step> [maxDepth]
 */
'use strict';

const fs = require('fs');
const path = require('path');
const occtimportjs = require('occt-import-js');

const stepPath = process.argv[2];
const maxDepth = Number(process.argv[3] || 3);

if (!stepPath) {
  console.error('usage: node probe_step.js <file.step> [maxDepth]');
  process.exit(2);
}

(async () => {
  const occt = await occtimportjs({
    locateFile: (f) => path.join(__dirname, 'node_modules', 'occt-import-js', 'dist', f),
  });

  const buf = fs.readFileSync(stepPath);
  console.log(`文件: ${stepPath}  (${(buf.length / 1048576).toFixed(2)} MB)`);

  const t0 = Date.now();
  const res = occt.ReadStepFile(new Uint8Array(buf), {
    linearUnit: 'millimeter',
    linearDeflectionType: 'bounding_box_ratio',
    linearDeflection: 0.0015,
    angularDeflection: 0.5,
  });
  const ms = Date.now() - t0;

  console.log(`success = ${res.success}   解析耗时 = ${ms} ms`);
  if (!res.success) process.exit(1);

  const meshes = res.meshes || [];
  console.log(`meshes  = ${meshes.length}`);

  let totalVerts = 0;
  let totalTris = 0;
  for (const m of meshes) {
    const pos = m.attributes && m.attributes.position;
    totalVerts += pos ? pos.array.length / 3 : 0;
    totalTris += m.index ? m.index.array.length / 3 : 0;
  }
  console.log(`总计: 顶点 ${totalVerts}  三角面 ${totalTris}`);

  // 有名字的网格
  const named = meshes.map((m, i) => ({ i, name: (m.name || '').trim() })).filter((x) => x.name);
  console.log(`\n--- 有名字的 mesh (${named.length}/${meshes.length}) ---`);
  named.slice(0, 80).forEach((x) => console.log(`  [${String(x.i).padStart(3)}] ${x.name}`));
  if (named.length > 80) console.log(`  ... 其余 ${named.length - 80} 个略`);

  // 装配树
  console.log(`\n--- 装配树 (depth<=${maxDepth}) ---`);
  let nodeCount = 0;
  const walk = (n, d) => {
    if (!n) return;
    nodeCount++;
    if (d <= maxDepth) {
      const nm = (n.name || '').trim() || '(unnamed)';
      console.log(`${'  '.repeat(d)}- ${nm}   [mesh ${(n.meshes || []).length}] [child ${(n.children || []).length}]`);
    }
    (n.children || []).forEach((c) => walk(c, d + 1));
  };
  walk(res.root, 0);
  console.log(`\n节点总数 = ${nodeCount}`);
})();
