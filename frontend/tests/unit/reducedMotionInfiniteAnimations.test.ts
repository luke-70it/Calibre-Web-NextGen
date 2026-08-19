/*
 * Every infinite animation in the SPA must have a reduced-motion off-switch.
 *
 * MEASURED DEFECT (F-7d206e, counted from the files 2026-08-18): eight CSS
 * modules carried an `animation: … infinite` rule. Four switched it off under
 * `@media (prefers-reduced-motion: reduce)` and three did not -- including
 * `components/Spinner.module.css` `.ring`, the shared <Spinner>/<SpinnerCentered>
 * used more widely than any other loading indicator in the app. So the fork was
 * inconsistent with itself in both directions, and a user who asks their OS to
 * reduce motion got a still spinner on some screens and a spinning one on
 * others, on the same page load.
 *
 * The house rule, already followed by the four that were correct, is
 * `animation: none` under the reduce query. This suite pins that rule to the
 * POPULATION rather than to today's file list: it derives every class carrying
 * an infinite animation by scanning the modules, and fails if any one of them
 * lacks an off-switch. A spinner added next year with no reduce block fails
 * here without anyone remembering this finding existed.
 *
 * Deliberately a static CSS analysis, not a browser test. `prefers-reduced-motion`
 * is an OS-level setting; asserting on the stylesheet is the cheap, deterministic
 * form of the same question, and it is the form that keeps working when the
 * component is refactored.
 */
import { test, describe } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, relative } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '..', '..', 'src');

/** Every *.module.css under frontend/src, recursively. */
function cssModules(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const p = resolve(dir, entry.name);
    if (entry.isDirectory()) cssModules(p, out);
    else if (entry.isFile() && entry.name.endsWith('.module.css')) out.push(p);
  }
  return out;
}

/** Strip /* … *\/ comments so a commented-out rule never counts. */
const decomment = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '');

/**
 * Classes whose rule body declares an infinite animation.
 *
 * Matches `.name { … animation: … infinite … }`. Only class selectors are
 * considered: an infinite animation on a bare element or a keyframes block is
 * not something a module can switch off by class, and none exist today.
 */
function infiniteAnimationClasses(css: string): Set<string> {
  const found = new Set<string>();
  const body = decomment(css);
  const RULE = /(^|[}\s])((?:\.[A-Za-z_][\w-]*\s*,?\s*)+)\{([^{}]*)\}/g;
  for (const m of body.matchAll(RULE)) {
    const [, , selectors, declarations] = m;
    if (!/\banimation\b\s*:[^;]*\binfinite\b/.test(declarations)) continue;
    for (const sel of selectors.split(',')) {
      const name = sel.trim().replace(/^\./, '');
      if (name) found.add(name);
    }
  }
  return found;
}

/**
 * Classes switched off inside a `prefers-reduced-motion: reduce` block.
 *
 * The at-rule body nests one level, so it cannot be matched with the same flat
 * regex -- brace-count from the query to its matching close.
 */
function reducedMotionOffSwitches(css: string): Set<string> {
  const body = decomment(css);
  const off = new Set<string>();
  const QUERY = /@media[^{]*prefers-reduced-motion\s*:\s*reduce[^{]*\{/g;
  for (const m of body.matchAll(QUERY)) {
    let depth = 1;
    let i = m.index! + m[0].length;
    const start = i;
    while (i < body.length && depth > 0) {
      if (body[i] === '{') depth++;
      else if (body[i] === '}') depth--;
      i++;
    }
    const inner = body.slice(start, i - 1);
    const RULE = /((?:\.[A-Za-z_][\w-]*\s*,?\s*)+)\{([^{}]*)\}/g;
    for (const r of inner.matchAll(RULE)) {
      const [, selectors, declarations] = r;
      if (!/\banimation\b\s*:\s*none\b/.test(declarations)) continue;
      for (const sel of selectors.split(',')) {
        const name = sel.trim().replace(/^\./, '');
        if (name) off.add(name);
      }
    }
  }
  return off;
}

const MODULES = cssModules(SRC).sort();

describe('reduced motion covers every infinite animation', () => {
  test('there are modules to scan, and infinite animations among them', () => {
    // An empty population would make every assertion below pass vacuously --
    // which is exactly how this class of gate stops working unnoticed.
    assert.ok(MODULES.length > 10, `only ${MODULES.length} CSS modules found under ${SRC}`);
    const total = MODULES.reduce(
      (n, f) => n + infiniteAnimationClasses(readFileSync(f, 'utf8')).size, 0);
    assert.ok(total > 0, 'no infinite animations found at all -- the scanner is broken');
  });

  test('every class with an infinite animation is switched off under reduced motion', () => {
    const offenders: string[] = [];
    for (const file of MODULES) {
      const css = readFileSync(file, 'utf8');
      const infinite = infiniteAnimationClasses(css);
      if (infinite.size === 0) continue;
      const off = reducedMotionOffSwitches(css);
      for (const name of infinite) {
        if (!off.has(name)) offenders.push(`${relative(SRC, file)} .${name}`);
      }
    }
    assert.deepEqual(
      offenders, [],
      'these classes animate forever with no `animation: none` under ' +
      '`@media (prefers-reduced-motion: reduce)`:\n  ' + offenders.join('\n  '));
  });

  test('the three modules F-7d206e named are specifically covered', () => {
    // Named explicitly so a refactor that deletes them shows up as a failure
    // here rather than as a silently shrinking population above.
    for (const [rel, cls] of [
      ['components/Spinner.module.css', 'ring'],
      ['pages/Reader.module.css', 'spin'],
      ['pages/Upload.module.css', 'spin'],
    ] as const) {
      const p = resolve(SRC, rel);
      assert.ok(statSync(p).isFile(), `${rel} is gone`);
      const css = readFileSync(p, 'utf8');
      assert.ok(infiniteAnimationClasses(css).has(cls), `${rel} .${cls} no longer animates`);
      assert.ok(reducedMotionOffSwitches(css).has(cls), `${rel} .${cls} has no reduced-motion off-switch`);
    }
  });
});

describe('the scanner itself', () => {
  // The assertions above are only worth their runtime if the two scanners are
  // right, so pin them against hand-written CSS with a known answer.
  test('finds an infinite animation, ignores a finite one', () => {
    assert.deepEqual(
      [...infiniteAnimationClasses('.a { animation: spin 1s linear infinite; }')], ['a']);
    assert.deepEqual(
      [...infiniteAnimationClasses('.a { animation: spin 1s linear; }')], []);
  });

  test('ignores a commented-out rule', () => {
    assert.deepEqual(
      [...infiniteAnimationClasses('/* .a { animation: spin 1s infinite; } */')], []);
  });

  test('reads a grouped selector', () => {
    assert.deepEqual(
      [...infiniteAnimationClasses('.a, .b { animation: spin 1s infinite; }')].sort(), ['a', 'b']);
  });

  test('finds an off-switch on one line and in a nested block', () => {
    assert.ok(reducedMotionOffSwitches(
      '@media (prefers-reduced-motion: reduce) { .a { animation: none; } }').has('a'));
    assert.ok(reducedMotionOffSwitches(
      '@media (prefers-reduced-motion: reduce) {\n  .a {\n    animation: none;\n  }\n}').has('a'));
  });

  test('a reduce block that only kills transitions is not an off-switch', () => {
    assert.deepEqual(
      [...reducedMotionOffSwitches(
        '@media (prefers-reduced-motion: reduce) { .a { transition: none; } }')], []);
  });

  test('does not stop at the first inner close brace', () => {
    // The Reader module puts several rules in one reduce block; a scanner that
    // ended the block at the first `}` would see only the first of them.
    const css = '@media (prefers-reduced-motion: reduce) { .a { animation: none; } .b { animation: none; } }';
    assert.deepEqual([...reducedMotionOffSwitches(css)].sort(), ['a', 'b']);
  });

  test('an off-switch outside a reduce query does not count', () => {
    assert.deepEqual([...reducedMotionOffSwitches('.a { animation: none; }')], []);
  });
});
