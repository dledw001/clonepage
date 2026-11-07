import { cpSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

// Keep viewer resources on the NAS and at the same version as the bundled worker.
const destination = fileURLToPath(new URL('../public/pdfjs/', import.meta.url));
mkdirSync(destination, { recursive: true });
for (const folder of ['cmaps', 'standard_fonts', 'wasm', 'iccs', 'web/images']) {
  cpSync(new URL(`../node_modules/pdfjs-dist/${folder}/`, import.meta.url),
         `${destination}/${folder}`, { recursive: true });
}
