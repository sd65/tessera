// Entry point. Loads the components and puts the stylesheet on the document.
//
// The document-level rules (html/body margin and height, the palette) have to be
// adopted here because nothing outside a shadow root can style them. Each
// component then adopts the same sheet into its own root.

import { sheet } from './styles.js';
import './components/app.js';

document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];