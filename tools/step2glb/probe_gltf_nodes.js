// probe_gltf_nodes.js — 打印 GLB 中关节节点的完整定义（含 matrix 字段）
const fs = require('fs');
const buf = fs.readFileSync(process.argv[2]);
const jlen = buf.readUInt32LE(12);
const json = JSON.parse(buf.slice(20, 20 + jlen).toString('utf8'));
const want = ['ER3_600', 'Base', 'J1', 'J2', 'J3', 'J4', 'J5', 'J6', 'WorkspaceEnvelope'];
json.nodes.forEach((n, i) => {
  if (!want.includes(n.name)) return;
  console.log(`[${i}] ${n.name}`);
  console.log('   ', JSON.stringify(n));
});
