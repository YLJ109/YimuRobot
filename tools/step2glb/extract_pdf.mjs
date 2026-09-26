import fs from 'fs';
const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs');
const files = process.argv.slice(2);
for (const f of files) {
  const data = new Uint8Array(fs.readFileSync(f));
  const doc = await pdfjs.getDocument({ data, useSystemFonts: false }).promise;
  console.log('\n========== ' + f + '  pages=' + doc.numPages + ' ==========');
  for (let p = 1; p <= doc.numPages; p++) {
    const page = await doc.getPage(p);
    const tc = await page.getTextContent();
    let last = null, line = [];
    const out = [];
    for (const it of tc.items) {
      if (it.str === undefined) continue;
      const y = it.transform[5], x = it.transform[4];
      if (last !== null && Math.abs(y - last) > 2.2) { out.push(line.join('')); line = []; }
      line.push(it.str); last = y;
    }
    if (line.length) out.push(line.join(''));
    const txt = out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
    if (txt) console.log('\n----- page ' + p + ' -----\n' + txt.slice(0, 3000));
  }
}
