/**
 * build_glb.js — 用官方 ER3-600 STEP 数模（已由 occt-import-js 三角化）构建带动画层级的 GLB
 *
 * 关节几何全部来自官方数模的实测（不是估计值）：
 *   · 方法: 解析 STEP 的 CYLINDRICAL_SURFACE 实体，把共线圆柱面聚类 —— 轴承孔/凸台的轴线
 *           即为关节轴线。J1 处有 4 个共轴圆柱面，J5 处 4 个，J3 处 3 个，置信度极高。
 *
 * 实测结果 (mm, CAD 坐标系 Z 向上):
 *   J1  轴 +Z 过 (0, 0, ·)              基座回转
 *   J2  轴 ±Y 过 (0, ·, 367.50)         肩 —— 到 J1 轴距离 367.5
 *   J3  轴 ±Y 过 (0, ·, 662.50)         肘 —— 大臂长 295.0
 *   J4  轴 +X 过 (·, 0, 699.50)         小臂回转 —— 相对 J3 偏移 (0,0,37.0)
 *   J5  轴 ±Y 过 (295.50, ·, 699.50)    腕俯仰 —— 小臂长 295.5，腕心 (295.5, 0, 699.5)
 *   J6  轴 +X 过 (·, 0, 699.50)         末端回转 —— 与 J5 同过腕心（球形腕）
 *   法兰面 x = 374  →  腕心到法兰面 78.5
 *
 * 自洽性验证: 最大臂展 = 大臂 295.0 + |J3→腕心| √(37² + 295.5²) = 295.0 + 297.81
 *            = 592.81 mm ≈ 官方标称 593 mm ✓
 *            （37mm 与 295.5mm 都固定在 J3 系内且恒垂直，故 |J3→腕心| 恒为 297.81）
 *
 * 用法: node build_glb.js [meshes.json] [输出.glb]
 */
'use strict';

const fs = require('fs');
const path = require('path');

// ── three 的 GLTFExporter 依赖浏览器 FileReader；Node 无此全局对象，做最小 polyfill ──
if (typeof globalThis.FileReader === 'undefined') {
  globalThis.FileReader = class FileReader {
    constructor() {
      this.result = null;
      this.onloadend = null;
      this.onerror = null;
    }
    readAsArrayBuffer(blob) {
      blob.arrayBuffer().then(
        (buf) => { this.result = buf; if (this.onloadend) this.onloadend(); },
        (err) => { if (this.onerror) this.onerror(err); },
      );
    }
    readAsDataURL(blob) {
      blob.arrayBuffer().then(
        (buf) => {
          this.result = `data:${blob.type || 'application/octet-stream'};base64,${Buffer.from(buf).toString('base64')}`;
          if (this.onloadend) this.onloadend();
        },
        (err) => { if (this.onerror) this.onerror(err); },
      );
    }
  };
}

const HERE = __dirname;
const MESHES = process.argv[2] || path.join(HERE, 'build', 'meshes.json');
const OUT = process.argv[3] || path.join(HERE, 'build', 'er3_600.glb');

// ══════════════════════════════════════════════════════════════════
//  实测关节表（本文件唯一几何真相源；与 backend/kinematics.py 的
//  CAD_MEASURED 常量必须保持一致）
// ══════════════════════════════════════════════════════════════════
const MM = 0.001;                    // mm -> m
const GEO = {
  d1: 367.5,      // J1轴 -> J2轴（沿 +Z）
  L2: 295.0,      // J2   -> J3 （沿大臂）
  e3: 37.0,       // J3   -> J4 （沿 +Z，始终与 L4 垂直）
  L4: 295.5,      // J4   -> J5 （沿 +X）
  tool: 78.5,     // 腕心 -> 法兰面
};

// 连杆定义: meshes 为 occt 网格序号；pivot 为该连杆旋转轴线上的一点（CAD 世界坐标 mm）
const LINKS = [
  // name       meshes            pivot(mm)                  axis  parent
  { name: 'Base',  meshes: [0, 1, 2, 3, 4, 5, 6], pivot: [0, 0, 0],          axis: null, parent: null, label: '底座' },
  { name: 'J1',    meshes: [7],  pivot: [0, 0, 0],                          axis: 'z', parent: 'Base', label: '转座' },
  { name: 'J2',    meshes: [8],  pivot: [0, 0, GEO.d1],                     axis: 'y', parent: 'J1',   label: '大臂' },
  { name: 'J3',    meshes: [9],  pivot: [0, 0, GEO.d1 + GEO.L2],            axis: 'y', parent: 'J2',   label: '电机座' },
  { name: 'J4',    meshes: [10], pivot: [0, 0, GEO.d1 + GEO.L2 + GEO.e3],   axis: 'x', parent: 'J3',   label: '手腕体' },
  { name: 'J5',    meshes: [11], pivot: [GEO.L4, 0, GEO.d1 + GEO.L2 + GEO.e3], axis: 'y', parent: 'J4', label: '手腕' },
  { name: 'J6',    meshes: [12], pivot: [GEO.L4, 0, GEO.d1 + GEO.L2 + GEO.e3], axis: 'x', parent: 'J5', label: '法兰' },
];

// 运动范围包络：单独一个根节点，前端默认隐藏（可开关）
const ENVELOPE = { name: 'WorkspaceEnvelope', meshes: [13] };

// 材质：白机身 + 深灰关节（EFORT ER 系列配色）
function pickColor(meshColor, linkName) {
  if (meshColor && Array.isArray(meshColor) && !(meshColor[0] === 1 && meshColor[1] === 1 && meshColor[2] === 1)) {
    return meshColor;
  }
  return [0.94, 0.94, 0.93];   // 机身白
}

(async () => {
  const THREE = await import('three');
  const { GLTFExporter } = await import('three/examples/jsm/exporters/GLTFExporter.js');

  console.log(`读取 ${MESHES}`);
  const data = JSON.parse(fs.readFileSync(MESHES, 'utf8'));
  const byIndex = new Map(data.meshes.map((m) => [m.i, m]));

  // ── 材质池（按颜色去重） ──────────────────────────────────────
  const matCache = new Map();
  function materialFor(linkName, meshColor) {
    const c = pickColor(meshColor, linkName);
    const key = c.map((v) => v.toFixed(3)).join(',');
    if (matCache.has(key)) return matCache.get(key);
    const mat = new THREE.MeshStandardMaterial({
      color: new THREE.Color(c[0], c[1], c[2]),
      metalness: 0.25,
      roughness: 0.55,
      name: `mat_${key.replace(/[^0-9]/g, '')}`,
    });
    matCache.set(key, mat);
    return mat;
  }

  // ── 建几何：顶点平移到以 pivot 为原点，并 mm -> m ────────────
  function buildGeometry(meshIdx, pivotMm) {
    const m = byIndex.get(meshIdx);
    if (!m || !m.positions || !m.indices) return null;
    const pos = new Float32Array(m.positions.length);
    for (let v = 0; v < m.positions.length; v += 3) {
      pos[v] = (m.positions[v] - pivotMm[0]) * MM;
      pos[v + 1] = (m.positions[v + 1] - pivotMm[1]) * MM;
      pos[v + 2] = (m.positions[v + 2] - pivotMm[2]) * MM;
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    if (m.normals) g.setAttribute('normal', new THREE.BufferAttribute(new Float32Array(m.normals), 3));
    g.setIndex(new THREE.BufferAttribute(new Uint32Array(m.indices), 1));
    if (!m.normals) g.computeVertexNormals();
    g.computeBoundingSphere();
    return g;
  }

  // ── 组装层级 ─────────────────────────────────────────────────
  const root = new THREE.Group();
  root.name = 'ER3_600';
  // 约定：GLB 保持 CAD 的 Z-up，前端在场景根节点统一做 Z-up -> Y-up 旋转
  const nodeByName = new Map();

  const nodeInfo = [];
  for (const L of LINKS) {
    const g = new THREE.Group();
    g.name = L.name;
    let pos = [0, 0, 0];
    if (L.parent) {
      const parent = LINKS.find((x) => x.name === L.parent);
      pos = L.pivot.map((v, i) => v - parent.pivot[i]);
    }
    g.position.set(pos[0] * MM, pos[1] * MM, pos[2] * MM);
    g.userData = { label: L.label, axis: L.axis, pivotMm: L.pivot };
    const parentNode = L.parent ? nodeByName.get(L.parent) : root;
    parentNode.add(g);
    nodeByName.set(L.name, g);

    for (const mi of L.meshes) {
      const src = byIndex.get(mi);
      const geo = buildGeometry(mi, L.pivot);
      if (!geo) continue;
      const mesh = new THREE.Mesh(geo, materialFor(L.name, src && src.color));
      mesh.name = `${L.name}_${mi}`;
      g.add(mesh);
    }
    nodeInfo.push({
      name: L.name, label: L.label, axis: L.axis,
      localPositionMm: pos, pivotMm: L.pivot, parent: L.parent,
      meshes: L.meshes,
    });
  }

  // 运动范围包络（根节点下，独立）
  {
    const g = new THREE.Group();
    g.name = ENVELOPE.name;
    let tot = 0;
    for (const mi of ENVELOPE.meshes) {
      const src = byIndex.get(mi);
      const geo = buildGeometry(mi, [0, 0, 0]);
      if (!geo) continue;
      const mm = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({
        color: 0x4a9eff, metalness: 0.0, roughness: 0.9,
        transparent: true, opacity: 0.18, side: THREE.DoubleSide,
        depthWrite: false, name: 'mat_envelope',
      }));
      mm.name = `${ENVELOPE.name}_${mi}`;
      g.add(mm);
      tot += src ? src.tris : 0;
    }
    root.add(g);
    nodeInfo.push({ name: ENVELOPE.name, label: '运动范围包络', axis: null, parent: 'root', meshes: ENVELOPE.meshes, triangles: tot });
  }

  // ── 统计 ─────────────────────────────────────────────────────
  let tri = 0, vert = 0;
  root.traverse((o) => {
    if (o.isMesh) {
      tri += o.geometry.index ? o.geometry.index.count / 3 : 0;
      vert += o.geometry.attributes.position.count;
    }
  });
  console.log(`\n节点数 = ${nodeByName.size + 1}   三角面 = ${tri}   顶点 = ${vert}`);

  // ── 导出 GLB ─────────────────────────────────────────────────
  const exporter = new GLTFExporter();
  const glb = await new Promise((resolve, reject) => {
    exporter.parse(root, resolve, reject, { binary: true, onlyVisible: false, truncateDrawRange: true });
  });
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, Buffer.from(glb));
  console.log(`\n已写出 ${OUT}  (${(glb.byteLength / 1048576).toFixed(2)} MB)`);

  // ── 同时写一份前端直接用的关节元数据 ────────────────────────
  const meta = {
    source: data.source,
    unit: 'm',
    upAxis: 'Z',
    note: '关节几何来自官方 ER3-600 数模 V1.2 实测（CYLINDRICAL_SURFACE 共轴聚类）',
    measured: GEO,
    reachMm: GEO.L2 + Math.hypot(GEO.e3, GEO.L4),
    nodes: nodeInfo,
  };
  const metaPath = path.join(path.dirname(OUT), 'er3_600_joints.json');
  fs.writeFileSync(metaPath, JSON.stringify(meta, null, 2));
  console.log(`已写出 ${metaPath}`);
  console.log(`\n最大臂展 = ${meta.reachMm.toFixed(2)} mm  (官方标称 593 mm)`);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
