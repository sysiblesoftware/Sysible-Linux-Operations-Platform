// Render the REAL CopyButton from ui.jsx, click it, and read the clipboard back.
// A Copy button that renders but does not copy is the exact failure this guards.
// argv[2] = path to ui.jsx, argv[3] = a writable scratch directory.
// exit 0 pass, 1 fail, 2 dependencies unavailable (the caller skips).
const fs = require('fs');
const path = require('path');

const UI = process.argv[2];
const TMP = process.argv[3];

// react/react-dom/esbuild come from wherever this checkout keeps them -- the
// frontend's own node_modules in a dev tree, or anything on NODE_PATH. Resolve
// from several roots rather than assuming one, and SKIP (exit 2) rather than fail
// when they are simply not installed.
const ROOTS = [path.dirname(UI), path.join(path.dirname(UI), '..'),
               path.join(path.dirname(UI), '..', '..'), process.cwd(), __dirname]
  .concat((process.env.NODE_PATH || '').split(path.delimiter).filter(Boolean));
const find = (name) => path.dirname(require.resolve(name + '/package.json', { paths: ROOTS }));
let chromium, esbuild, reactDir, reactDomDir;
try {
  // Plain require first: playwright is usually installed globally, and setting
  // NODE_PATH for the others would otherwise hide it.
  try { ({ chromium } = require('playwright')); }
  catch { ({ chromium } = require(find('playwright'))); }
  esbuild = require(find('esbuild'));
  reactDir = find('react');
  reactDomDir = find('react-dom');
} catch (e) {
  console.error('missing dependency: ' + e.message);
  process.exit(2);
}

const app = path.join(TMP, 'app.jsx');
fs.writeFileSync(app, `
import React from 'react'
import { createRoot } from 'react-dom/client'
import { CopyButton } from ${JSON.stringify(UI)}
const BIG = Array.from({length: 500}, (_, i) => 'line ' + i + ' of the run log').join('\\n')
createRoot(document.getElementById('root')).render(
  <div>
    <CopyButton text={BIG} label="Copy log" />
    <CopyButton text={() => BIG} label="Copy lazy" />
    <CopyButton text="" label="Copy empty" />
    <div id="probe">{BIG.length}</div>
  </div>)
`);
const bundle = path.join(TMP, 'bundle.js');
try {
  esbuild.buildSync({
    entryPoints: [app], bundle: true, outfile: bundle,
    loader: { '.jsx': 'jsx' }, define: { 'process.env.NODE_ENV': '"production"' },
    logLevel: 'error', alias: { react: reactDir, 'react-dom': reactDomDir },
  });
} catch (e) {
  console.error('bundle failed: ' + (e.message || e));
  process.exit(2);
}
fs.writeFileSync(path.join(TMP, 'index.html'),
  '<!doctype html><meta charset="utf-8"><div id="root"></div><script src="bundle.js"></script>');

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ permissions: ['clipboard-read', 'clipboard-write'] });
  const p = await ctx.newPage();
  await p.goto('file://' + path.join(TMP, 'index.html'));
  await p.waitForSelector('#probe');
  const want = +(await p.textContent('#probe'));
  let fails = 0;
  const say = (n, ok, m) => { console.log(`  ${ok ? 'OK  ' : 'FAIL'} ${n.padEnd(32)} ${m}`); if (!ok) fails++; };
  const btn = (i) => p.locator('button').nth(i);
  const settle = () => p.waitForTimeout(1700);   // the ✓ Copied label reverts after 1500ms

  for (const [i, name] of [[0, 'eager text'], [1, 'lazy text()']]) {
    await settle();
    await p.evaluate(() => navigator.clipboard.writeText('SENTINEL'));
    await btn(i).click();
    await p.waitForTimeout(150);
    const got = await p.evaluate(() => navigator.clipboard.readText());
    say(name, got.length === want, got.length + ' chars on the clipboard');
    say(name + ' confirms', (await btn(i).textContent()).includes('Copied'), 'label');
  }
  await settle();
  await p.evaluate(() => navigator.clipboard.writeText('SENTINEL'));
  await btn(2).click();
  await p.waitForTimeout(150);
  say('nothing to copy leaves it alone',
      (await p.evaluate(() => navigator.clipboard.readText())) === 'SENTINEL', 'clipboard');
  say('nothing to copy says so', (await btn(2).textContent()).includes('Blocked'), 'label');
  await settle();
  say('label reverts', (await btn(0).textContent()).trim() === 'Copy log', 'label');
  await b.close();
  process.exit(fails ? 1 : 0);
})();
