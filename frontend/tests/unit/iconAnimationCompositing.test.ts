/*
 * Compositor safety for icon animations.
 *
 * MEASURED DEFECT (Chrome DevTools trace, book detail route, mobile 390x844@3,
 * 20x CPU throttle): `.arrow` in HelpBanner.module.css ran
 * `animation: hbNudge 1.8s var(--ease) infinite` — a `transform` keyframe —
 * directly on a lucide `<ArrowUpRight>` SVG. Chrome's frame reporter said
 * `has_compositor_animation: false, has_main_animation: true` on 944/944 update
 * frames: an SVG element does not get its own composited layer, so a transform
 * animation on one silently falls off the compositor and runs on the MAIN
 * thread forever — continuous style recalc -> prepaint -> layerize -> commit at
 * ~60fps, on every route, whether or not anything is on screen.
 *
 *   3,251ms main thread / 19.87s, 1,169 style recalcs, 22 dropped frames, INP 125ms
 *   with the animation cancelled: 399ms, 0 style recalcs, 1 dropped frame, INP 12ms
 *   the same keyframes on a plain <div>: has_compositor_animation TRUE 809/809
 *
 * The failure is silent — the animation still looks right, it just costs the
 * main thread — so nothing but a static check catches it. This pins the GENERAL
 * invariant rather than the single instance:
 *
 *   a keyframe animation that animates a compositor property (transform /
 *   opacity / the transform longhands) must NOT be applied via `className`
 *   directly to an SVG element. Put it on a wrapper element instead.
 *
 * Run: node --test frontend/tests/unit/iconAnimationCompositing.test.ts
 * (Fast Tests runs it through tests/unit/test_frontend_unit_suites_run.py.)
 *
 * POSITIVE CONTROL: the same analyzer is run over synthetic fixtures that must
 * be flagged and synthetic fixtures that must NOT be, so a harness that rejects
 * everything (or accepts everything) cannot masquerade as a working gate.
 *
 * Uses Node's built-in runner + native type stripping, like the other suites
 * here: the frontend ships no test framework on purpose (hard rule 6).
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, existsSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, relative } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, '..', '..');
const SRC = resolve(FRONTEND, 'src');

/* ------------------------------------------------------------------ CSS --- */

/** Properties the compositor can animate off the main thread — and precisely
 *  the ones that stop being compositable when the target is an SVG element. */
const COMPOSITOR_PROP = /(?:^|[;{\s])(?:transform|opacity|translate|rotate|scale)\s*:/;

function stripCssComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, ' ');
}

/** Names of `@keyframes` blocks that animate a compositor property. */
export function compositorKeyframeNames(css: string): Set<string> {
  const text = stripCssComments(css);
  const found = new Set<string>();
  const re = /@(?:-\w+-)?keyframes\s+([\w-]+)\s*\{/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text)) !== null) {
    const bodyStart = re.lastIndex;
    let depth = 1;
    let i = bodyStart;
    while (i < text.length && depth > 0) {
      if (text[i] === '{') depth++;
      else if (text[i] === '}') depth--;
      i++;
    }
    if (COMPOSITOR_PROP.test(text.slice(bodyStart, i - 1))) found.add(match[1]);
    re.lastIndex = i;
  }
  return found;
}

/** class name -> the compositor keyframes it runs. Only innermost rules are
 *  examined, which is what `([^{}]+)\{([^{}]*)\}` yields, so `@media` wrappers
 *  are transparently handled and `@keyframes` step blocks (which carry no
 *  `animation:` declaration) are ignored. */
export function compositorAnimatedClasses(css: string): Map<string, string[]> {
  const text = stripCssComments(css);
  const names = compositorKeyframeNames(text);
  const byClass = new Map<string, string[]>();
  if (names.size === 0) return byClass;

  const rule = /([^{}]+)\{([^{}]*)\}/g;
  let match: RegExpExecArray | null;
  while ((match = rule.exec(text)) !== null) {
    const selector = match[1].trim();
    if (selector.startsWith('@')) continue;
    const declarations = [...match[2].matchAll(/animation(?:-name)?\s*:([^;]*)/g)].map((d) => d[1]);
    if (declarations.length === 0) continue;
    const used = [...names].filter((name) =>
      declarations.some((d) => new RegExp(`(?:^|[^\\w-])${name}(?:[^\\w-]|$)`).test(d)),
    );
    if (used.length === 0) continue;
    for (const cls of selector.matchAll(/\.(-?[_a-zA-Z][\w-]*)/g)) {
      const previous = byClass.get(cls[1]) ?? [];
      byClass.set(cls[1], [...new Set([...previous, ...used])]);
    }
  }
  return byClass;
}

/* ------------------------------------------------------------------ TSX --- */

interface ImportedName { local: string; imported: string; spec: string }

function namedImports(tsx: string): ImportedName[] {
  const out: ImportedName[] = [];
  const re = /import\s+(?:type\s+)?\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(tsx)) !== null) {
    for (const raw of match[1].split(',')) {
      const part = raw.trim();
      if (!part) continue;
      const halves = part.split(/\s+as\s+/);
      const imported = halves[0].trim().replace(/^type\s+/, '');
      const local = (halves[1] ?? halves[0]).trim().replace(/^type\s+/, '');
      if (imported && local) out.push({ local, imported, spec: match[2] });
    }
  }
  return out;
}

function defaultImports(tsx: string): Array<{ local: string; spec: string }> {
  const out: Array<{ local: string; spec: string }> = [];
  const re = /import\s+([A-Za-z_$][\w$]*)\s*(?:,\s*\{[^}]*\}\s*)?from\s*['"]([^'"]+)['"]/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(tsx)) !== null) out.push({ local: match[1], spec: match[2] });
  return out;
}

/** Walk forward from `<Name` to the `>` that closes the opening tag, skipping
 *  over braces, strings and template literals so `size={a > b ? 1 : 2}` and
 *  `onClick={() => …}` do not terminate the scan early. */
function openingTagAttributes(text: string, tagStart: number): string | null {
  let i = tagStart + 1;
  while (i < text.length && /[\w$.]/.test(text[i])) i++;
  const attrsStart = i;
  let depth = 0;
  let quote: string | null = null;
  while (i < text.length) {
    const ch = text[i];
    if (quote !== null) {
      if (ch === '\\') { i += 2; continue; }
      if (ch === quote) quote = null;
      i++;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; i++; continue; }
    if (ch === '{') depth++;
    else if (ch === '}') depth--;
    else if (ch === '>' && depth === 0) return text.slice(attrsStart, i);
    i++;
  }
  return null;
}

function balancedEnd(text: string, openIndex: number, open: string, close: string): number {
  let depth = 0;
  let quote: string | null = null;
  let i = openIndex;
  while (i < text.length) {
    const ch = text[i];
    if (quote !== null) {
      if (ch === '\\') { i += 2; continue; }
      if (ch === quote) quote = null;
      i++;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; i++; continue; }
    if (ch === open) depth++;
    else if (ch === close) { depth--; if (depth === 0) return i + 1; }
    i++;
  }
  return -1;
}

/** Every `className=` expression in one opening tag's attribute text. */
export function classNameExpressions(attrs: string): string[] {
  const out: string[] = [];
  const re = /className\s*=\s*/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(attrs)) !== null) {
    const start = match.index + match[0].length;
    const ch = attrs[start];
    if (ch === '{') {
      const end = balancedEnd(attrs, start, '{', '}');
      if (end > 0) { out.push(attrs.slice(start + 1, end - 1)); re.lastIndex = end; }
    } else if (ch === '"' || ch === "'") {
      const end = attrs.indexOf(ch, start + 1);
      if (end > 0) { out.push(attrs.slice(start + 1, end)); re.lastIndex = end + 1; }
    }
  }
  return out;
}

function styleClassRefs(expression: string, stylesIdent: string): string[] {
  const out: string[] = [];
  const dotted = new RegExp(`\\b${stylesIdent}\\.([A-Za-z_$][\\w$]*)`, 'g');
  const bracketed = new RegExp(`\\b${stylesIdent}\\[\\s*['"]([^'"]+)['"]\\s*\\]`, 'g');
  for (const m of expression.matchAll(dotted)) out.push(m[1]);
  for (const m of expression.matchAll(bracketed)) out.push(m[1]);
  return out;
}

/** True when the module returns an `<svg>` as a component root — those custom
 *  components are SVG elements at runtime exactly like a lucide icon is. */
export function svgRootComponents(tsx: string): Set<string> {
  const names = new Set<string>();
  if (!/(?:return|=>)\s*\(?\s*<svg[\s>]/.test(tsx)) return names;
  for (const m of tsx.matchAll(/export\s+(?:default\s+)?(?:function|const)\s+([A-Z][\w$]*)/g)) {
    names.add(m[1]);
  }
  return names;
}

export interface Violation {
  file: string;
  line: number;
  component: string;
  className: string;
  keyframes: string[];
}

export interface ScanInput {
  path: string;
  tsx: string;
  readCss: (absolutePath: string) => string | null;
  svgComponentsByModule?: Map<string, Set<string>>;
}

export function scanSource(input: ScanInput): Violation[] {
  const { path, tsx, readCss } = input;
  const svgByModule = input.svgComponentsByModule ?? new Map<string, Set<string>>();

  // Which local identifiers render an <svg> root?
  const iconComponents = new Set<string>(['svg']);
  for (const imp of namedImports(tsx)) {
    if (imp.spec === 'lucide-react') { iconComponents.add(imp.local); continue; }
    if (!imp.spec.startsWith('.')) continue;
    const resolved = resolveModule(path, imp.spec);
    if (resolved && svgByModule.get(resolved)?.has(imp.imported)) iconComponents.add(imp.local);
  }
  // `{ icon: Icon }` destructuring — the codebase's dynamic-icon shape. Widening
  // the check here can only make it stricter, never blind.
  for (const m of tsx.matchAll(/\bicon\s*:\s*([A-Z][\w$]*)/g)) iconComponents.add(m[1]);

  // Which local identifiers are CSS-module namespaces, and what do they animate?
  const animatedByIdent = new Map<string, Map<string, string[]>>();
  for (const imp of defaultImports(tsx)) {
    if (!/\.css$/.test(imp.spec)) continue;
    const cssPath = resolve(dirname(path), imp.spec);
    const css = readCss(cssPath);
    if (css === null) continue;
    animatedByIdent.set(imp.local, compositorAnimatedClasses(css));
  }
  if (animatedByIdent.size === 0) return [];

  const violations: Violation[] = [];
  for (const component of iconComponents) {
    const tagRe = new RegExp(`<${component}(?![\\w$.])`, 'g');
    let match: RegExpExecArray | null;
    while ((match = tagRe.exec(tsx)) !== null) {
      const attrs = openingTagAttributes(tsx, match.index);
      if (attrs === null) continue;
      for (const expression of classNameExpressions(attrs)) {
        for (const [ident, animated] of animatedByIdent) {
          for (const cls of styleClassRefs(expression, ident)) {
            const keyframes = animated.get(cls);
            if (!keyframes) continue;
            violations.push({
              file: path,
              line: tsx.slice(0, match.index).split('\n').length,
              component,
              className: cls,
              keyframes,
            });
          }
        }
      }
    }
  }
  return violations;
}

function resolveModule(fromFile: string, spec: string): string | null {
  const base = resolve(dirname(fromFile), spec);
  for (const candidate of [base, `${base}.tsx`, `${base}.ts`, resolve(base, 'index.tsx')]) {
    if (existsSync(candidate) && statSync(candidate).isFile()) return candidate;
  }
  return null;
}

/* ------------------------------------------------------ real-project scan --- */

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = resolve(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

const ALL_FILES = walk(SRC);
const TSX_FILES = ALL_FILES.filter((f) => f.endsWith('.tsx'));
const CSS_FILES = ALL_FILES.filter((f) => f.endsWith('.css'));

const SVG_COMPONENTS_BY_MODULE = new Map<string, Set<string>>();
for (const file of TSX_FILES) {
  const names = svgRootComponents(readFileSync(file, 'utf8'));
  if (names.size > 0) SVG_COMPONENTS_BY_MODULE.set(file, names);
}

const readCssFromDisk = (p: string): string | null =>
  existsSync(p) && statSync(p).isFile() ? readFileSync(p, 'utf8') : null;

const REAL_VIOLATIONS = TSX_FILES.flatMap((file) =>
  scanSource({
    path: file,
    tsx: readFileSync(file, 'utf8'),
    readCss: readCssFromDisk,
    svgComponentsByModule: SVG_COMPONENTS_BY_MODULE,
  }),
);

/* ------------------------------------------------------------- fixtures --- */

const NUDGE_CSS = `
.arrow { color: red; flex: none; animation: fxNudge 1.8s var(--ease) infinite; }
@keyframes fxNudge { 0%, 100% { transform: translate(0, 0); } 50% { transform: translate(2px, -2px); } }
.tinted { animation: fxTint 2s linear infinite; }
@keyframes fxTint { to { color: blue; } }
.plain { color: green; }
@media (prefers-reduced-motion: reduce) { .arrow { animation: none; } }
`;

function fixtureScan(tsx: string): Violation[] {
  return scanSource({
    path: resolve(SRC, 'components', '__fixture__.tsx'),
    tsx,
    readCss: (p) => (p.endsWith('Fixture.module.css') ? NUDGE_CSS : null),
  });
}

const FIXTURE_HEAD =
  "import { ArrowUpRight } from 'lucide-react';\nimport styles from './Fixture.module.css';\n";

/* ---------------------------------------------------------------- tests --- */

describe('analyzer positive control', () => {
  test('flags a transform keyframe applied straight to a lucide icon', () => {
    const found = fixtureScan(
      `${FIXTURE_HEAD}export const X = () => <ArrowUpRight size={15} className={styles.arrow} aria-hidden="true" />;`,
    );
    assert.equal(found.length, 1, `expected one violation, got ${JSON.stringify(found)}`);
    assert.equal(found[0].component, 'ArrowUpRight');
    assert.equal(found[0].className, 'arrow');
    assert.deepEqual(found[0].keyframes, ['fxNudge']);
  });

  test('flags it through a template literal and a ternary too', () => {
    assert.equal(
      fixtureScan(`${FIXTURE_HEAD}export const X = () => <ArrowUpRight className={\`\${styles.plain} \${styles.arrow}\`} />;`).length,
      1,
    );
    assert.equal(
      fixtureScan(`${FIXTURE_HEAD}export const X = ({ on }: { on: boolean }) => <ArrowUpRight className={on ? styles.arrow : undefined} />;`).length,
      1,
    );
  });

  test('flags a bare <svg> and a locally-defined <svg>-root component', () => {
    assert.equal(
      fixtureScan(`import styles from './Fixture.module.css';\nexport const X = () => <svg className={styles.arrow} />;`).length,
      1,
    );
    const mark = resolve(SRC, 'components', 'KofiMark.tsx');
    const svgModules = new Map<string, Set<string>>([[mark, new Set(['KofiMark'])]]);
    const found = scanSource({
      path: resolve(SRC, 'components', '__fixture__.tsx'),
      tsx: "import { KofiMark } from './KofiMark';\nimport styles from './Fixture.module.css';\nexport const X = () => <KofiMark className={styles.arrow} />;",
      readCss: (p) => (p.endsWith('Fixture.module.css') ? NUDGE_CSS : null),
      svgComponentsByModule: svgModules,
    });
    assert.equal(found.length, 1);
    assert.equal(found[0].component, 'KofiMark');
  });

  test('does NOT flag the same animation on a wrapper element — this is the fix shape', () => {
    assert.deepEqual(
      fixtureScan(
        `${FIXTURE_HEAD}export const X = () => <span className={styles.arrow}><ArrowUpRight size={15} aria-hidden="true" /></span>;`,
      ),
      [],
    );
  });

  test('does NOT flag a non-compositor animation, or an unanimated class, on an icon', () => {
    assert.deepEqual(
      fixtureScan(`${FIXTURE_HEAD}export const X = () => <ArrowUpRight className={styles.tinted} />;`),
      [],
    );
    assert.deepEqual(
      fixtureScan(`${FIXTURE_HEAD}export const X = () => <ArrowUpRight className={styles.plain} />;`),
      [],
    );
  });

  test('does NOT trip on JSX that merely contains ">" inside an attribute', () => {
    assert.deepEqual(
      fixtureScan(
        `${FIXTURE_HEAD}export const X = ({ n }: { n: number }) => <ArrowUpRight size={n > 2 ? 16 : 12} onClick={() => undefined} className={styles.plain} />;`,
      ),
      [],
    );
  });
});

describe('CSS analysis', () => {
  // `compositorAnimatedClasses` reads INNERMOST rules, which is what makes
  // `@media` wrappers transparent -- and is exactly why native CSS nesting
  // would blind it: in `.a { animation: x 1s; &:hover { ... } }` the outer
  // block is no longer innermost and its `animation` declaration is never
  // seen. The SPA writes flat CSS today; pin that, so the day someone adopts
  // nesting this fails loudly instead of quietly passing everything.
  test('the SPA writes flat CSS -- nesting would blind the innermost-rule parse', () => {
    const nesting: string[] = [];
    for (const file of CSS_FILES) {
      const text = readFileSync(file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, ' ');
      // a `&` outside a url()/data: value is native nesting
      if (/&/.test(text.replace(/url\([^)]*\)/g, ' '))) nesting.push(relative(FRONTEND, file));
    }
    assert.deepEqual(nesting, [], `CSS nesting found -- teach compositorAnimatedClasses about it:\n${nesting.join('\n')}`);
  });

  test('reads the real HelpBanner keyframes', () => {
    const css = readFileSync(resolve(SRC, 'components', 'HelpBanner.module.css'), 'utf8');
    const names = compositorKeyframeNames(css);
    assert.ok(names.has('hbNudge'), `hbNudge missing from ${[...names].join(', ')}`);
    assert.ok(names.has('hbSlideDown'));
    assert.ok(compositorAnimatedClasses(css).has('arrow'), 'the .arrow class should still carry the nudge');
  });
});

describe('the SPA never animates a compositor property on an SVG icon', () => {
  test('the scan is not vacuous', () => {
    assert.ok(TSX_FILES.length > 20, `only ${TSX_FILES.length} .tsx files found under ${SRC}`);
    assert.ok(CSS_FILES.length > 10, `only ${CSS_FILES.length} .css files found under ${SRC}`);
    const animatedSomewhere = CSS_FILES.reduce(
      (n, f) => n + compositorAnimatedClasses(readFileSync(f, 'utf8')).size,
      0,
    );
    assert.ok(animatedSomewhere > 0, 'no compositor-animated classes found at all — the CSS parse is broken');
    const lucideUsers = TSX_FILES.filter((f) => readFileSync(f, 'utf8').includes("from 'lucide-react'"));
    assert.ok(lucideUsers.length > 10, `only ${lucideUsers.length} files import lucide-react`);
  });

  test('no animated class is applied directly to an icon', () => {
    const report = REAL_VIOLATIONS.map(
      (v) => `${relative(FRONTEND, v.file)}:${v.line}  <${v.component} className={styles.${v.className}}>  ` +
        `runs @keyframes ${v.keyframes.join(', ')} on the MAIN THREAD (SVG targets never composite). ` +
        `Move the animation to a wrapper element.`,
    );
    assert.deepEqual(report, [], `\n${report.join('\n')}\n`);
  });
});

describe('the announcement banner nudge specifically', () => {
  const tsx = readFileSync(resolve(SRC, 'components', 'AnnouncementBanner.tsx'), 'utf8');

  test('puts the nudge on a wrapper, not on <ArrowUpRight>', () => {
    const arrowTag = tsx.slice(tsx.indexOf('<ArrowUpRight'));
    const attrs = openingTagAttributes(tsx, tsx.indexOf('<ArrowUpRight'));
    assert.ok(attrs !== null, 'no <ArrowUpRight> in AnnouncementBanner.tsx any more');
    assert.ok(
      !attrs.includes('styles.arrow'),
      `the nudge is still on the SVG itself: <ArrowUpRight${attrs}>`,
    );
    assert.ok(
      /<span className=\{styles\.arrow\}>/.test(tsx),
      'expected a <span className={styles.arrow}> wrapper carrying the animation',
    );
    assert.ok(arrowTag.includes('aria-hidden="true"'), 'the icon must stay decorative');
    assert.ok(arrowTag.includes('focusable={false}'), 'the icon must stay out of the tab order');
  });

  test('reduced motion still switches the nudge off', () => {
    const css = readFileSync(resolve(SRC, 'components', 'HelpBanner.module.css'), 'utf8');
    const reduced = css.slice(css.indexOf('@media (prefers-reduced-motion: reduce)'));
    assert.ok(reduced.length > 0, 'the reduced-motion block disappeared');
    assert.ok(/\.arrow\s*\{[^}]*animation:\s*none/.test(reduced), '.arrow no longer honours reduced motion');
    assert.ok(/\.banner\s*\{[^}]*animation:\s*none/.test(reduced), '.banner no longer honours reduced motion');
  });
});
