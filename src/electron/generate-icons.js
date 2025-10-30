#!/usr/bin/env node

/**
 * Generate Windows ICO and macOS ICNS from source PNG using png2icons.
 * This script replaces the electron-icon-maker + copy-icons.js workflow.
 */

const png2icons = require('png2icons');
const fs = require('fs');
const path = require('path');

const sourcePng = path.join(__dirname, 'build', 'icon.png');
const outputIco = path.join(__dirname, 'build', 'icon.ico');
const outputIcns = path.join(__dirname, 'build', 'icon.icns');

console.log('Generating icons from source PNG...');

try {
  const input = fs.readFileSync(sourcePng);

  // Generate Windows ICO with special exe format
  // API: createICO(input, scalingAlgorithm, numOfColors, usePNG, forWinExe)
  // - scalingAlgorithm: png2icons.HERMITE (or 0-5)
  // - numOfColors: 0 for lossless
  // - usePNG: false = use BMP encoding (better compatibility)
  // - forWinExe: true = mixed format (BMP for <64px, PNG for >=64px)
  const icoOutput = png2icons.createICO(input, png2icons.HERMITE, 0, false, true);

  if (!icoOutput) {
    throw new Error('Failed to generate ICO file');
  }

  fs.writeFileSync(outputIco, icoOutput);
  console.log('✓ Generated icon.ico with Windows executable format');
  console.log('  Sizes: 16, 24, 32, 48, 64, 72, 96, 128, 256 (mixed BMP/PNG)');

  // Generate macOS ICNS
  const icnsOutput = png2icons.createICNS(input, png2icons.HERMITE, 0);

  if (!icnsOutput) {
    throw new Error('Failed to generate ICNS file');
  }

  fs.writeFileSync(outputIcns, icnsOutput);
  console.log('✓ Generated icon.icns for macOS');

  console.log('\nIcon generation complete!');
  process.exit(0);
} catch (error) {
  console.error('❌ Icon generation failed:', error.message);
  process.exit(1);
}
