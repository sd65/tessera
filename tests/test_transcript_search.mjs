import assert from 'node:assert/strict';
import { findTranscriptMatches } from '../tessera/ui/transcript-search.js';

assert.deepEqual(
  findTranscriptMatches('Été E\u0301té été', 'ETE'),
  [
    { start: 0, end: 3 },
    { start: 4, end: 8 },
    { start: 9, end: 12 },
  ],
);

assert.deepEqual(
  findTranscriptMatches('Crème brûlée, creme brulee, CRÈME', 'creme'),
  [
    { start: 0, end: 5 },
    { start: 14, end: 19 },
    { start: 28, end: 33 },
  ],
);

assert.deepEqual(findTranscriptMatches('a+b A+B', 'A+B'), [
  { start: 0, end: 3 },
  { start: 4, end: 7 },
]);
assert.deepEqual(findTranscriptMatches('aaaa', 'aa'), [
  { start: 0, end: 2 },
  { start: 2, end: 4 },
]);
assert.deepEqual(findTranscriptMatches('anything', ''), []);

console.log('Transcript search checks passed');
