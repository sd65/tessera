/**
 * The one DOM helper the components share.
 *
 * It was copy-pasted into two components verbatim, which is the point at which a
 * helper stops being a convenience and starts being two things to keep in sync.
 * `textContent`, never `innerHTML`: titles come from an RSS feed.
 */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}