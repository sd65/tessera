/**
 * One stylesheet for the whole page, constructed once and adopted everywhere:
 * onto `document` by ui/app.js for html/body, and onto every component's shadow
 * root by components/app.js.
 *
 * Shadow DOM is meant to isolate style, and adopting one sheet into all four
 * roots is a deliberate exception to that. The alternative is four near-copies
 * of the same palette and spacing values drifting apart, which is the failure
 * mode isolation is supposed to prevent rather than cause.
 *
 * Custom properties are defined on `:root` and inherit into shadow trees, so
 * this works across the boundary even though the selectors do not.
 *
 * There is one palette, and it is light. `color-scheme: light` is declared
 * explicitly rather than left off: without it an operating system set to dark
 * renders the native parts — the searchable combo's popup, scrollbars, form
 * fields — dark against this light page. The values are written literally, so
 * there is no `light-dark()` floor and no second copy of the colours anywhere.
 */

const CSS = `
:root {
  color-scheme: light;

  --bg: #f7f6f4;
  --panel: #fffefc;
  --panel-2: #f1efeb;
  --line: #e0dcd5;
  --ink: #1b1a17;
  --muted: #6d6862;
  --accent: #2c6b4c;
  --accent-soft: #e4efe7;

  --radius: 6px;
  --gap: 12px;

  font-family: ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.45;
  color: var(--ink);
  background: var(--bg);
}

html, body {
  height: 100%;
  margin: 0;
}

* { box-sizing: border-box; }

button {
  font: inherit;
  color: inherit;
  cursor: pointer;
}

/* ---- <tessera-app> ---------------------------------------------------- */

tessera-app {
  display: block;
  height: 100dvh;
  overflow: hidden;
}

.shell {
  display: grid;
  grid-template-rows: auto 1fr;
  height: 100%;
  min-height: 0;
}

.masthead {
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 10px var(--gap);
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}

.masthead h1 {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  letter-spacing: 0.02em;
}

.columns {
  display: grid;
  grid-template-columns: minmax(260px, 340px) minmax(340px, 1fr) minmax(320px, 1.15fr);
  min-height: 0;
}

.columns > * {
  min-width: 0;
  min-height: 0;
}

/* The three pane hosts are grid children, so they are block-level and stretched
   to the row already — but stated, because the shadow content below depends on
   filling them. */
tessera-video-list,
tessera-video-player,
tessera-transcript {
  display: block;
  height: 100%;
  overflow: hidden;
}

/* ---- <tessera-video-list> --------------------------------------------- */

.filter {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: var(--gap);
  border-bottom: 1px solid var(--line);
}

/* A native searchable combo: an <input> backed by a <datalist>, so the popup,
   the typeahead and the keyboard all belong to the browser. The <select>
   fallback inherits these rules too. There is no visible label — the
   placeholder says what it is and aria-label gives it an accessible name,
   which is the trade for a control that is otherwise a mystery box. */
.filter input,
.filter select {
  display: block;
  width: 100%;
  font: inherit;
  font-size: 13px;
  color: var(--ink);
  padding: 5px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
}

.filter input:disabled,
.filter select:disabled {
  opacity: 0.5;
}

.rows {
  overflow-y: auto;
  flex: 1;
}

.row {
  display: block;
  width: 100%;
  text-align: left;
  padding: 9px var(--gap);
  border: 0;
  border-bottom: 1px solid var(--line);
  border-left: 3px solid transparent;
  background: none;
}

.row:hover { background: var(--panel-2); }

.row[aria-current="true"] {
  background: var(--accent-soft);
  border-left-color: var(--accent);
}

.row .title {
  display: block;
  font-size: 13px;
  font-weight: 550;
  line-height: 1.3;
  margin-bottom: 3px;
}

.row[aria-current="true"] .title { color: var(--accent); }

.row .meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 9px;
  font-size: 11px;
  color: var(--muted);
}

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px var(--gap);
  border-top: 1px solid var(--line);
  background: var(--panel);
  font-size: 12px;
  color: var(--muted);
}

.pager button {
  padding: 3px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
}

.pager button:disabled { opacity: 0.4; cursor: default; }

.notice {
  padding: 18px var(--gap);
  color: var(--muted);
  font-size: 13px;
}

/* ---- <tessera-video-player> ------------------------------------------ */

.player {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  overflow: hidden;
}

.player .frame {
  position: relative;
  flex: 0 0 auto;
  width: 100%;
  aspect-ratio: 16 / 9;
  background: #000;
}

.player .frame iframe {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  border: 0;
}

.player .placeholder {
  position: absolute;
  inset: 0;
  display: grid;
  place-content: center;
  color: var(--muted);
  font-size: 13px;
}

.player .caption {
  padding: 10px var(--gap);
  border-bottom: 1px solid var(--line);
}

.player .caption .title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.3;
  margin-bottom: 3px;
}

.player .caption .meta {
  font-size: 11px;
  color: var(--muted);
}

/* ---- <tessera-transcript> -------------------------------------------- */

.pane {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.pane > header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 9px var(--gap);
  border-bottom: 1px solid var(--line);
  background: var(--panel);
  font-size: 12px;
  color: var(--muted);
}

/* The scroll container's only job is to be a shrinkable flex item AND a flex
   container. Without both, the transcript is unreachable: a flex item defaults
   to min-height:auto, so this box refuses to shrink below its 134,000-character
   child, and because it is not itself a display:flex container, the .text rules
   for flex:1 and min-height:0 are both ignored and overflow-y:auto never gets a
   bounded height to scroll inside. The result measured in Chrome was a 426px pane
   holding 11,527px of clipped, unreachable text with overflow:hidden on the host
   and nothing else on the page scrolling either.

   Note for whoever edits this file: the whole stylesheet is a JS template
   literal, so a backtick in a comment ends the string. That shipped once and
   broke every module that imports this one. */
.body {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

/* The one region that reflows prose. A 134KB single-spaced string renders as
   one text node without trouble, so there is no chunking and no virtualisation
   here — just a scrollbar.

   This was capped at 68ch, which left a column of dead space down the
   right-hand side of the pane: the transcript stopped about two thirds of the
   way across while the scrollbar sat at the far edge. The measure was a
   typographic opinion nobody asked for, so the text now fills the pane it is
   given. */
.text {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px var(--gap) 48px;
  line-height: 1.62;
  white-space: pre-wrap;
  overflow-wrap: break-word;
}

.empty {
  padding: 18px var(--gap);
  color: var(--muted);
  font-size: 13px;
}

.reason {
  padding: 18px var(--gap);
  font-size: 13px;
}

.reason .headline {
  font-weight: 600;
  margin-bottom: 6px;
}

.reason .detail {
  color: var(--muted);
}
`;

export const sheet = new CSSStyleSheet();
sheet.replaceSync(CSS);