import assert from 'node:assert/strict';
import { nextThemeBoundary, scheduledTheme } from '../tessera/ui/theme.js';

function at(hour, minute = 0, day = 3) {
  return new Date(2026, 0, day, hour, minute, 0, 0);
}

assert.equal(scheduledTheme(at(20, 29)), 'light');
assert.equal(scheduledTheme(at(20, 30)), 'dark');
assert.equal(scheduledTheme(at(23, 0)), 'dark');
assert.equal(scheduledTheme(at(6, 59)), 'dark');
assert.equal(scheduledTheme(at(7, 0)), 'light');
assert.equal(scheduledTheme(at(12, 0)), 'light');

assert.equal(nextThemeBoundary(at(6, 59)).getTime(), at(7, 0).getTime());
assert.equal(nextThemeBoundary(at(10, 0)).getTime(), at(20, 30).getTime());
assert.equal(nextThemeBoundary(at(20, 29)).getTime(), at(20, 30).getTime());
assert.equal(nextThemeBoundary(at(20, 30)).getTime(), at(7, 0, 4).getTime());
assert.equal(nextThemeBoundary(at(23, 0)).getTime(), at(7, 0, 4).getTime());

console.log('Theme schedule checks passed');
