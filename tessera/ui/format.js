/**
 * Formatting shared by the components.
 *
 * Separate from `components/el.js` because that one is about building nodes and
 * this one is about turning values into strings. Both are imported by more than
 * one component, which is the only reason either exists as a module.
 */

/** 24h in milliseconds. Named because `86400000` in a date calculation is a
 *  number you should have to look up. */
const DAY = 86400000;

/**
 * A whole number of days, as a local calendar difference.
 *
 * "Today" is a local idea, so this compares local midnights rather than elapsed
 * hours: a video published at 23:30 UTC is yesterday for most of the world and
 * not for the part of it that has not gone to bed yet. Rounding rather than
 * flooring is belt-and-braces on a value that should already be a whole number
 * of days.
 */
export function daysAgo(iso, from = new Date()) {
  // The type check is not fussy, it is load-bearing: `new Date(null)` is the Unix
  // epoch and perfectly valid, so a row whose published_at is SQL NULL would
  // render as "20,729 days ago" rather than as nothing. `new Date('')` and
  // `new Date(undefined)` are both Invalid Date, so only null needed catching —
  // and it is a null, because published_at comes from an RSS feed.
  if (typeof iso !== 'string' || iso === '') return null;

  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return null;

  const midnight = (date) => Date.UTC(date.getFullYear(), date.getMonth(), date.getDate());
  return Math.round((midnight(from) - midnight(when)) / DAY);
}

/**
 * "3 days ago", for the two places a publication date is shown.
 *
 * Singular is spelled out rather than special-cased to "yesterday", because the
 * rows stack up against each other and "1 day ago" / "2 days ago" reads as a
 * sequence where "yesterday" / "2 days ago" does not.
 *
 * A video from 2023 says "1,128 days ago", which is the honest consequence of
 * expressing every date relatively and is why every call site also puts the
 * exact timestamp in the element's `title`. If that reads badly, the fix is a
 * threshold here and one threshold, not a change in two components.
 */
export function relativeDay(iso, from = new Date()) {
  const days = daysAgo(iso, from);
  if (days === null) return '';

  if (days === 0) return 'today';
  if (days > 0) return days === 1 ? '1 day ago' : `${days.toLocaleString()} days ago`;
  const ahead = -days;
  return ahead === 1 ? 'in 1 day' : `in ${ahead.toLocaleString()} days`;
}