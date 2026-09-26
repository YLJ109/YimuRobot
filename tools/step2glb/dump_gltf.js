// dump_gltf.js — 直接读 GLB 的 JSON 块，核对节点名 / 层级 / 变换 / 材质
const fs = require('fs');
const buf = fs.readFileSync(process.argv[2]);
const jlen = buf.readUInt32LE(12);
const json = JSON.parse(buf.slice(20, 20 + jlen).toString('utf8'));

console.log('asset   :', JSON.stringify(json.asset));
console.log('scenes  :', JSON.stringify(json.scenes));
console.log('nodes   :');
json.nodes.forEach((n, i) => {
  const t = n.translation ? n.translation.map(v => v.toFixed(4)).join(',') : '-';
  const r = n.rotation ? n.rotation.map(v => v.toFixed(4)).join(',') : '-';
  const s = n.scale ? n.scale.join(',') : '-';
  const ch = n.children ? JSON.stringify(n.children) : '-';
  console.log(`  [${i}] ${n.name}  mesh=${n.mesh !== undefined ? n.mesh : '-'}  children=${ch}  T=${t}  R=${r}  S=${s}`);
});
console.log('meshes  :', json.meshes.length, ' materials:', (json.materials || []).map(m => m.name).join(' | '));
let tris = 0;
json.meshes.forEach(m => m.primitives.forEach(p => { tris += json.accessors[p.indices].count / 3; }));
console.log('triangles:', tris);

function bboxOf(i, prefix) {
  const n = json.nodes[i];
  if (n.mesh !== undefined) {
    const p = json.meshes[n.mesh].primitives[0].attributes.POSITION;
    const a = json.accessors[p];
    console.log(`  ${prefix}${n.name}  min=[${a.min.map(v => v.toFixed(4))}]  max=[${a.max.map(v => v.toFixed(4))}]`);
  }
  if (n.children) n.children.forEach(c => bboxOf(c, prefix + '  '));
}
console.log('bbox per node (local, m):');
json.scenes[0].nodes.forEach(r => bboxOf(r, '  '));
