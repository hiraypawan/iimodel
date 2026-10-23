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

test('landing distinguishes implemented local references from physical evidence gates', () => {
  assert.match(html, /isolated complete-GGUF workers/);
  assert.match(html, /local peer\/activation references are implemented/);
  assert.match(html, /physical phone, LAN, and NAT proof remain measured gates/);
  assert.match(html, /Expert IDs[\s\S]*not yet an executable distributed model graph/);
  assert.doesNotMatch(html, /671B|DeepSeek-V4|Qwen3 Nano 4B/);
});

test('landing publishes exact localized headline prices with a tax note', () => {
  assert.match(html, /data-monthly="\$20 \/ mo"/);
  assert.match(html, /data-monthly="\$10 \/ mo"/);
  assert.match(html, /data-monthly="₹249 \/ mo"/);
  assert.match(html, /· \+ tax/);
});

test('universal PWA has an offline shell, install metadata, encrypted vault, and truthful browser boundaries', () => {
  const appHtml = fs.readFileSync(path.join(__dirname, 'app.html'), 'utf8');
  const appScript = fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8');
  const secureStore = fs.readFileSync(path.join(__dirname, 'secure-store.js'), 'utf8');
  const serviceWorker = fs.readFileSync(path.join(__dirname, 'service-worker.js'), 'utf8');
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, 'manifest.webmanifest'), 'utf8'));

  assert.doesNotThrow(() => new vm.Script(appScript, { filename: 'web/app.js' }));
  assert.doesNotThrow(() => new vm.Script(secureStore, { filename: 'web/secure-store.js' }));
  assert.doesNotThrow(() => new vm.Script(serviceWorker, { filename: 'web/service-worker.js' }));
  assert.equal(manifest.display, 'standalone');
  assert.equal(manifest.start_url, '/app.html');
  assert.ok(manifest.icons.some((icon) => icon.sizes === '192x192'));
  assert.ok(manifest.icons.some((icon) => icon.sizes === '512x512'));
  assert.match(appHtml, /Browser mobile works while open/);
  assert.match(appScript, /navigator\.gpu\.requestAdapter/);
  assert.match(appScript, /getUserMedia/);
  assert.match(appScript, /registerIILocalModelAdapter/);
  assert.match(secureStore, /PBKDF2/);
  assert.match(secureStore, /AES-GCM/);
  assert.match(serviceWorker, /ii-resume-job-poll/);
  assert.doesNotMatch(appScript, /fake|simulat(?:e|ed).*answer/i);
  assert.doesNotMatch(appScript, /sessionStorage|localStorage\.setItem\([^)]*(?:token|secret|receipt)/i);
});

test('human learning review console parses and exposes explicit audited gates', () => {
  const reviewHtml = fs.readFileSync(path.join(__dirname, 'review.html'), 'utf8');
  const reviewScript = fs.readFileSync(path.join(__dirname, 'review.js'), 'utf8');
  assert.doesNotThrow(() => new vm.Script(reviewScript, { filename: 'web/review.js' }));
  assert.match(reviewHtml, /human gate · no automatic merge/);
  assert.match(reviewScript, /\/admin\/teachings\/\$\{encodeURIComponent\(item\.id\)\}\/content/);
  assert.match(reviewScript, /decision/);
  assert.match(reviewScript, /sealDataset/);
});
