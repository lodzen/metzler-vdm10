#!/usr/bin/env node
// Syntax-check .mmd files with mermaid.parse (same engine as the
// mermaidchart VS Code extension bundles). Exit 1 on the first failure.
// Flowchart-class diagrams touch DOMPurify at module init, which needs a
// DOM — jsdom supplies it for parse-only use.
import { JSDOM } from 'jsdom';

const { window } = new JSDOM('');
globalThis.window = window;
globalThis.document = window.document;
// navigator is a read-only getter in Node >= 21
Object.defineProperty(globalThis, 'navigator', { value: window.navigator, configurable: true });

const { default: mermaid } = await import('mermaid');
const { readFileSync } = await import('node:fs');

let ok = true;
for (const file of process.argv.slice(2)) {
  try {
    const res = await mermaid.parse(readFileSync(file, 'utf8'));
    console.log(`OK   ${file}  (${res.diagramType})`);
  } catch (e) {
    ok = false;
    console.log(`FAIL ${file}\n     ${String(e.message || e).split('\n').slice(0, 6).join('\n     ')}`);
  }
}
process.exit(ok ? 0 : 1);
