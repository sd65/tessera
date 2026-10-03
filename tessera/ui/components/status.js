/**
 * The one status string the client needs before it has any response.
 *
 * This used to be published as `meta.ok`, which cost an extra request on every
 * page load. That was the tail wagging the dog: the default view hides non-`ok`
 * rows, the default has to be a `status` query parameter rather than a
 * client-side display filter, and a filter's value cannot be read out of the
 * very response that filter is supposed to shape. So the first load had to
 * fetch a constant in order to avoid writing it down.
 *
 * Everything else in `meta` is still published rather than hardcoded —
 * `retryable`, `permanent`, `max_attempts`, `channels`, `total` — because each
 * of those is derivable only from the whole corpus, which a client holding one
 * page does not have. This one is not; it is a literal, and the whole corpus
 * cannot tell a client something it could not have written down.
 *
 * Mirrors `OK` in transcript.py: both name the literal the fetcher writes into
 * an `ok` row's `status`. If that ever changes, these are the two places, and
 * every stored row changes with them.
 */

export const OK = 'ok';