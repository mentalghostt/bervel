// Regression checks for the offline architecture atlas.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const html = fs.readFileSync(path.join(root, '.dev/visualizations/bervel-code-map.html'), 'utf8');
const data = JSON.parse(html.match(/<script id="map-data" type="application\/json">([\s\S]*?)<\/script>/)[1]);
assert.ok(data.architecture, 'C4 and ADD architecture model is embedded');
assert.match(html, /id="tab-c4"/);
assert.match(html, /id="tab-add"/);
assert.doesNotMatch(html, /<button id="startup"/);
assert.match(html, /mermaid\.render\(/);
assert.doesNotMatch(html, /railY|function drawWire/);
assert.ok(data.architecture.views.some(v => v.id === 'context'));
assert.ok(data.architecture.views.some(v => v.id === 'containers'));
const ids = new Set(data.architecture.nodes.map(n => n.id));
for (const node of data.architecture.nodes) {
  for (const key of ['purpose','contract','data','failure','evidence']) assert.ok(node[key]?.length, `${node.id}: ${key}`);
  for (const e of node.evidence) {
    assert.ok(fs.existsSync(path.join(root,e.path)), e.path);
    if(e.codeId) assert.ok(data.nodes.some(n => n.id === e.codeId), e.codeId);
  }
}
for (const view of data.architecture.views) {
  for (const id of view.nodes) assert.ok(ids.has(id), `${view.id}: ${id}`);
  for (const edge of view.edges || []) {
    assert.ok(view.nodes.includes(edge.from) && view.nodes.includes(edge.to));
    assert.ok(edge.label);
  }
}
assert.ok(data.architecture.add.steps.length >= 7);
assert.ok(data.architecture.nodes.find(n => n.id === 'worker').failure.includes('последователь'));
assert.ok(data.architecture.nodes.find(n => n.id === 'radar').purpose.includes('первич'));
assert.ok(data.architecture.views.find(v => v.id === 'containers').nodes.includes('web'));
assert.ok(data.architecture.views.find(v => v.id === 'containers').nodes.includes('spa'));
console.log(`Atlas model: ${ids.size} documented nodes, ${data.architecture.views.length} views, ADD checklist: PASS`);
