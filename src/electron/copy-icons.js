#!/usr/bin/env node

/**
 * Cross-platform script to copy generated icons to locations expected by electron-builder.
 *
 * This script is run after electron-icon-maker generates icons in build/icons/ subdirectories.
 * It copies the platform-specific icons to the top-level build/ directory where electron-builder
 * expects to find them.
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

// ESM doesn't have __dirname, so we need to create it
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const iconsDir = path.join(__dirname, 'build', 'icons');
const buildDir = path.join(__dirname, 'build');

// Icon files to copy: [source, destination]
const iconsToCopy = [
  [path.join(iconsDir, 'mac', 'icon.icns'), path.join(buildDir, 'icon.icns')],
  [path.join(iconsDir, 'win', 'icon.ico'), path.join(buildDir, 'icon.ico')]
];

console.log('Copying generated icons to electron-builder locations...');

let copied = 0;
let errors = 0;

for (const [src, dest] of iconsToCopy) {
  try {
    if (!fs.existsSync(src)) {
      console.error(`❌ Source file not found: ${src}`);
      errors++;
      continue;
    }

    fs.copyFileSync(src, dest);
    console.log(`✓ Copied ${path.basename(src)} to ${path.relative(__dirname, dest)}`);
    copied++;
  } catch (error) {
    console.error(`❌ Failed to copy ${path.basename(src)}: ${error.message}`);
    errors++;
  }
}

console.log(`\nCopied ${copied} icon file(s)${errors > 0 ? `, ${errors} error(s)` : ''}`);

if (errors > 0) {
  process.exit(1);
}
