'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
const inlineScripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((match) => match[1]);

test('landing inline JavaScript parses and real endpoints are wired', () => {
  assert.equal(inlineScripts.length, 1);
  assert.doesNotThrow(() => new vm.Script(inlineScripts[0], { filename: 'web/index.inline.js' }));
  assert.match(inlineScripts[0], /apiUrl\('\/stats'\)/);
  assert.match(inlineScripts[0], /setInterval\(refreshStats, 10_000\)/);
  assert.match(inlineScripts[0], /apiUrl\('\/genesis'\)/);
  assert.match(inlineScripts[0], /apiUrl\(`\/route\?expert_ids=/);
  assert.doesNotMatch(inlineScripts[0], /demo-host|SIMULATED/);
});

test('landing distinguishes live complete workers from future voice and distributed tensors', () => {
  assert.match(html, /complete-GGUF workers are live/);
  assert.match(html, /distributed tensor execution remain[s]? measured roadmap work/);
  assert.match(html, /Expert IDs[\s\S]*not yet an executable distributed model graph/);
  assert.doesNotMatch(html, /671B|DeepSeek-V4|Qwen3 Nano 4B/);
});

test('landing publishes exact localized headline prices with a tax note', () => {
  assert.match(html, /data-monthly="\$20 \/ mo"/);
  assert.match(html, /data-monthly="\$10 \/ mo"/);
  assert.match(html, /data-monthly="₹249 \/ mo"/);
  assert.match(html, /· \+ tax/);
});
