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
 * The light palette is the default. `color-scheme` is set explicitly per theme
 * so native controls follow the page instead of the operating system setting.
 * Theme colors live here and inherit into all component shadow roots.
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
  --recommend-summary: #368a54;
  --recommend-passages: #c18a18;
  --recommend-full: #cc5948;
  --recommend-undetermined: #85898a;
  --health-ok: #368a54;
  --health-warning: #c18a18;
  --health-error: #cc5948;
  --health-unknown: #85898a;

  --radius: 6px;
  --gap: 12px;
  --bottom-bar-height: 52px;

  font-family: ui-sans-serif, -apple-system, "Segoe UI", sans-serif;
  font-size: 14px;
  line-height: 1.45;
  color: var(--ink);
  background: var(--bg);
}

:root[data-theme="dark"] {
  color-scheme: dark;

  --bg: #151816;
  --panel: #1d211e;
  --panel-2: #272c28;
  --line: #383e39;
  --ink: #e8ece8;
  --muted: #a0aaa2;
  --accent: #8ec8a0;
  --accent-soft: #293a2e;
  --recommend-summary: #71c98b;
  --recommend-passages: #f2bd4f;
  --recommend-full: #ff8c7a;
  --recommend-undetermined: #a0a7a1;
  --health-ok: #71c98b;
  --health-warning: #f2bd4f;
  --health-error: #ff8c7a;
  --health-unknown: #a0a7a1;
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
  align-items: center;
  justify-content: space-between;
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

.brand {
  display: flex;
  align-items: center;
  gap: 9px;
}

.brand img {
  display: block;
  width: 28px;
  height: 28px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.health-wrap {
  position: relative;
  display: grid;
  place-items: center;
}

.health-indicator {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 50%;
  background: transparent;
}

.health-indicator:hover,
.health-indicator:focus-visible {
  background: var(--panel-2);
}

.health-indicator:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

.health-dot {
  width: 11px;
  height: 11px;
  border: 1px solid color-mix(in srgb, var(--ink) 18%, transparent);
  border-radius: 50%;
  background: var(--health-unknown);
  box-shadow: 0 0 0 2px var(--panel);
}

.health-indicator[data-state="ok"] .health-dot { background: var(--health-ok); }
.health-indicator[data-state="warning"] .health-dot { background: var(--health-warning); }
.health-indicator[data-state="error"] .health-dot { background: var(--health-error); }
.health-indicator[data-state="unknown"] .health-dot { background: var(--health-unknown); }

.health-tooltip {
  position: absolute;
  display: none;
  z-index: 20;
  top: calc(100% + 6px);
  right: 0;
  width: max-content;
  min-width: 230px;
  max-width: min(310px, calc(100vw - 24px));
  padding: 11px 13px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  color: var(--ink);
  box-shadow: 0 5px 20px rgb(0 0 0 / 16%);
  font-size: 12px;
  line-height: 1.65;
}

.health-wrap:hover .health-tooltip,
.health-wrap:focus-within .health-tooltip {
  display: block;
}

.health-tooltip[hidden] { display: none; }

.health-title {
  display: block;
  margin-bottom: 4px;
  font-size: 13px;
}

.health-line { color: var(--muted); }

.theme-toggle {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 50%;
  background: transparent;
  color: var(--accent);
  font-size: 21px;
  line-height: 1;
}

.theme-toggle:hover { background: var(--panel-2); }

.theme-toggle:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
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

.columns > tessera-video-player,
.columns > tessera-transcript {
  border-left: 1px solid var(--line);
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

tessera-chat {
  display: block;
  flex: 1;
  min-height: 0;
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

/* A row is a flex container holding two sibling buttons. .row-open is the
   clickable surface and carries the padding and the selection border; .row is
   layout only. */
.row {
  display: flex;
  align-items: stretch;
  width: 100%;
  border-bottom: 1px solid var(--line);
}

.row:hover { background: var(--panel-2); }

.row-open {
  display: block;
  flex: 1;
  min-width: 0;
  text-align: left;
  padding: 9px var(--gap);
  border: 0;
  border-left: 3px solid transparent;
  background: none;
}

.row-heading {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

/* Revealed on row hover, and on keyboard focus anywhere in the row so it is
   reachable without a pointer. opacity rather than visibility, because
   visibility:hidden would remove the button from the tab order and make it
   unreachable by keyboard — the exact case a hover-only affordance usually
   breaks. */
.row-read {
  flex: 0 0 auto;
  align-self: center;
  width: 28px;
  margin-right: 6px;
  padding: 0;
  border: 0;
  border-radius: var(--radius);
  background: none;
  color: var(--muted);
  font-size: 14px;
  line-height: 1;
  opacity: 0;
  pointer-events: none;
}

/* :has(:focus-visible), not :focus-within. Selecting a row focuses .row-open
   with the pointer, and :focus-within cannot tell that from the keyboard case —
   so the toggle stayed permanently on the selected row, which is not the
   affordance meant here. :focus-visible is the browser's own answer to "was this
   focus keyboard-driven", so the rule needs no modality tracking of its own. */
.row:hover .row-read,
.row:has(:focus-visible) .row-read {
  opacity: 1;
  pointer-events: auto;
}

.row-read:hover { color: var(--ink); }

.row-read:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.row-open:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: -2px;
}

.recommendation-dot {
  flex: 0 0 9px;
  width: 9px;
  height: 9px;
  margin-top: 5px;
  border-radius: 50%;
  background: var(--recommend-undetermined);
}

.recommendation-dot[data-recommendation="summary_sufficient"] {
  background: var(--recommend-summary);
}

.recommendation-dot[data-recommendation="selected_passages"] {
  background: var(--recommend-passages);
}

.recommendation-dot[data-recommendation="full_video_recommended"] {
  background: var(--recommend-full);
}

.recommendation-dot[data-recommendation="undetermined"] {
  background: var(--recommend-undetermined);
}

/* The selection highlight fills the whole row, so it is styled on the row; the
   accent bar stays on .row-open, next to the title. data-current is mirrored
   from aria-current rather than derived with :has(), so the two cannot disagree
   and the rule does not depend on :has() support. */
.row[data-current="true"] { background: var(--accent-soft); }

.row-open[aria-current="true"] { border-left-color: var(--accent); }

.row .title {
  display: block;
  flex: 1;
  font-size: 13px;
  font-weight: 450;
  line-height: 1.3;
  margin-bottom: 3px;
}

/* Weight is the whole unread signal. There was a dot beside it once, marking the
   same fact on a row that already carries a coloured dot a few characters further
   along — and a heavier weight was always going to be the clearer of the two. */
.row[data-read="false"] .title { font-weight: 750; }

.row-open[aria-current="true"] .title { color: var(--accent); }

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
  flex: 0 0 var(--bottom-bar-height);
  height: var(--bottom-bar-height);
  padding: 5px var(--gap);
  border-top: 1px solid var(--line);
  background: var(--panel);
  font-size: 12px;
  color: var(--muted);
}

.pager button {
  min-height: 32px;
  padding: 3px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
}

.pager button:disabled { opacity: 0.4; cursor: default; }

.pager .read-all {
  flex: 0 0 30px;
  width: 30px;
  padding: 0;
  font-size: 16px;
  line-height: 1;
}

.pager .read-all:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}

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

.player .placeholder[hidden] {
  display: none;
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

/* ---- <tessera-chat> --------------------------------------------------- */

.chat {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  border-top: 1px solid var(--line);
}

.chat > header {
  flex: 0 0 auto;
  padding: 8px var(--gap);
  background: var(--panel);
  color: var(--accent);
  font-size: 12px;
  font-weight: 600;
}

.chat-messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px var(--gap);
}

.chat-message {
  max-width: 92%;
  margin: 0 0 10px;
  padding: 8px 10px;
  border-radius: var(--radius);
  background: var(--panel-2);
  overflow-wrap: anywhere;
}

.chat-message.user {
  margin-left: auto;
  background: var(--accent-soft);
}

.chat-role {
  margin-bottom: 3px;
  color: var(--muted);
  font-size: 10px;
  font-weight: 650;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.chat-content {
  white-space: pre-wrap;
  line-height: 1.45;
}

.chat-citation {
  display: block;
  width: 100%;
  margin-top: 7px;
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel);
  color: var(--accent);
  font: inherit;
  font-size: 12px;
  line-height: 1.4;
  text-align: left;
  overflow-wrap: anywhere;
  cursor: pointer;
}

.chat-citation:hover { border-color: var(--accent); }

.chat-citation:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 1px;
}

.chat-message.pending { color: var(--muted); }

.chat-error {
  flex: 0 0 auto;
  padding: 6px var(--gap);
  color: #b33b32;
  font-size: 12px;
}

.chat-form {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 7px;
  flex: 0 0 auto;
  height: var(--bottom-bar-height);
  padding: 5px var(--gap);
  border-top: 1px solid var(--line);
  background: var(--panel);
}

.chat-form input {
  display: block;
  width: 100%;
  min-width: 0;
  height: 34px;
  padding: 5px 8px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--ink);
  font: inherit;
  font-size: 13px;
  line-height: 1.4;
}

.chat-form input:disabled { opacity: 0.6; }

.chat-form button {
  min-height: 34px;
  padding: 5px 11px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel-2);
}

.chat-form button:disabled { opacity: 0.45; cursor: default; }

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
  justify-content: space-between;
  gap: 8px;
  padding: 9px var(--gap);
  border-bottom: 1px solid var(--line);
  background: var(--panel);
  font-size: 12px;
  color: var(--muted);
}

.info-meta {
  min-width: 0;
  margin-left: auto;
  overflow: hidden;
  text-overflow: ellipsis;
  text-align: right;
  white-space: nowrap;
}

.info-title {
  flex: 0 0 auto;
  color: var(--accent);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
}

/* The right-pane body is a shrinkable flex container. Its transcript drawer is
   positioned against it, while the summary keeps its own scroll.

   Note for whoever edits this file: the whole stylesheet is a JS template
   literal, so a backtick in a comment ends the string. That shipped once and
   broke every module that imports this one. */
.body {
  position: relative;
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.body.has-transcript { padding-bottom: var(--bottom-bar-height); }

.watch-recommendation { margin-bottom: 10px; }

.passages { margin: -2px 0 12px; }

.summary-text .passages ul {
  display: grid;
  gap: 7px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.passage { margin-top: 0; }

/* The summary fills the space above the transcript launcher and scrolls only
   when its content is taller than that available space. */
.summary-text {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 16px var(--gap) 20px;
}

.summary-title {
  display: block;
  margin: 0 0 10px;
}

.summary-text h3 {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 650;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--accent);
}

.summary-copy h3:not(:first-child) { margin-top: 18px; }

.summary-text p {
  margin: 0 0 10px;
  line-height: 1.55;
}

.summary-text ul {
  margin: 0 0 10px;
  padding-left: 20px;
}

.summary-text li { margin-bottom: 4px; line-height: 1.5; }

/* A transcript drawer pinned to the bottom edge. Opening it overlays the
   summary and grows to fill the right pane's available body. */
.transcript-panel {
  position: absolute;
  z-index: 2;
  right: 0;
  bottom: 0;
  left: 0;
  height: var(--bottom-bar-height);
  background: var(--panel);
}

.transcript-panel.open {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  border-top: 1px solid var(--line);
  box-shadow: 0 -5px 18px rgb(0 0 0 / 12%);
}

.transcript-toggle {
  display: flex;
  align-items: center;
  width: 100%;
  height: var(--bottom-bar-height);
  padding: 5px var(--gap);
  border: 0;
  border-top: 1px solid var(--line);
  background: var(--panel);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
  color: var(--accent);
  cursor: pointer;
}

.transcript-toggle:hover { color: var(--ink); }

.transcript-toggle[hidden],
.transcript-open[hidden] {
  display: none;
}

.transcript-open {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  background: var(--panel);
}

.transcript-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex: 0 0 40px;
  padding: 4px var(--gap);
  border-bottom: 1px solid var(--line);
  background: var(--panel);
}

.transcript-title {
  color: var(--accent);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.05em;
}

.transcript-close,
.transcript-nav {
  display: grid;
  place-items: center;
  width: 32px;
  height: 30px;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--panel-2);
}

.transcript-close { font-size: 20px; line-height: 1; }

.transcript-searchbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto 32px 32px;
  align-items: center;
  gap: 5px;
  flex: 0 0 44px;
  padding: 5px var(--gap);
  border-bottom: 1px solid var(--line);
}

.transcript-search {
  width: 100%;
  min-width: 0;
  height: 32px;
  padding: 4px 7px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--bg);
  color: var(--ink);
  font: inherit;
  font-size: 12px;
}

.transcript-count {
  min-width: 38px;
  color: var(--muted);
  font-size: 10px;
  text-align: center;
  white-space: nowrap;
}

.transcript-nav:disabled {
  opacity: 0.4;
  cursor: default;
}

.transcript-text {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 12px var(--gap) 28px;
  line-height: 1.62;
  white-space: pre-wrap;
  overflow-wrap: break-word;
}

.transcript-text mark.match {
  padding: 0 1px;
  border-radius: 2px;
  background: #f2d675;
  color: #1b1a17;
}

.transcript-text mark.match.active {
  background: #ec9e54;
  outline: 2px solid #b86626;
  outline-offset: 1px;
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
