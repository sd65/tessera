/** Fold text for case- and diacritic-insensitive literal transcript search. */
function fold(value) {
  let text = '';
  const starts = [];
  const ends = [];
  let offset = 0;

  for (const character of value) {
    const start = offset;
    offset += character.length;
    const normalized = character.normalize('NFD').replace(/\p{M}/gu, '').toLowerCase();

    if (!normalized) {
      // Keep trailing decomposed accents inside the preceding highlighted range.
      if (ends.length) ends[ends.length - 1] = offset;
      continue;
    }

    text += normalized;
    for (let index = 0; index < normalized.length; index += 1) {
      starts.push(start);
      ends.push(offset);
    }
  }

  return { text, starts, ends };
}

/** Return non-overlapping original-text ranges matching a literal query. */
export function findTranscriptMatches(source, query) {
  if (typeof source !== 'string' || typeof query !== 'string' || !query) return [];

  const haystack = fold(source);
  const needle = fold(query).text;
  if (!needle) return [];

  const ranges = [];
  let cursor = 0;
  while (cursor <= haystack.text.length - needle.length) {
    const index = haystack.text.indexOf(needle, cursor);
    if (index < 0) break;
    const endIndex = index + needle.length;
    ranges.push({
      start: haystack.starts[index],
      end: haystack.ends[endIndex - 1],
    });
    cursor = endIndex;
  }
  return ranges;
}
