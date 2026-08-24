import { describe, test } from 'node:test';
import assert from 'node:assert/strict';

import { getPrimaryReadTarget, isReadableFormat } from '../../src/lib/readerTarget.ts';

const SPA_FORMATS = ['epub', 'kepub'];
const SERVER_FORMATS = [
  'pdf', 'txt', 'djvu', 'djv', 'cbz', 'cbr', 'cbt',
  'mp3', 'mp4', 'm4a', 'm4b', 'flac', 'ogg', 'opus', 'wav', 'aac',
];

describe('reader targets honor the viewer role across every supported format', () => {
  test('viewer role opens SPA and server-backed formats', () => {
    for (const format of SPA_FORMATS) {
      assert.equal(getPrimaryReadTarget(197, [format], true), '/read/197');
      assert.equal(isReadableFormat(format), true);
    }
    for (const format of SERVER_FORMATS) {
      assert.equal(getPrimaryReadTarget(197, [format], true), `/view/197/${format}`);
      assert.equal(isReadableFormat(format), true);
    }
  });

  test('without viewer role no readable format produces a target', () => {
    for (const format of [...SPA_FORMATS, ...SERVER_FORMATS]) {
      assert.equal(getPrimaryReadTarget(197, [format], false), null);
    }
  });

  test('unsupported download-only formats never become read targets', () => {
    for (const format of ['mobi', 'azw3', 'cb7']) {
      assert.equal(getPrimaryReadTarget(197, [format], true), null);
      assert.equal(isReadableFormat(format), false);
    }
  });
});
