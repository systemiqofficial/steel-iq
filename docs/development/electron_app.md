# Electron Desktop App

This document covers development and maintenance of the STEEL-IQ Electron desktop application.

## Overview

The Electron app is a standalone desktop application that bundles:
- The Django web interface
- A portable Python environment
- All necessary dependencies
- Background task worker (django_tasks)

## Icon Management

### Source Icon

The application icon is maintained as a single source file:
```
src/electron/build/icon.png
```

This should be a **1024x1024 PNG** with transparent background.

### Generating Platform-Specific Icons

To regenerate icons for all platforms (macOS, Windows) from the source PNG:

```bash
cd src/electron
npm run icons
```

This script uses `png2icons` (via `generate-icons.js`) to automatically generate:
- `build/icon.icns` - macOS icon (committed to repo)
- `build/icon.ico` - Windows icon with optimized encoding (committed to repo)
  - Uses BMP encoding for sizes <64px (better Windows compatibility)
  - Uses PNG encoding for sizes ≥64px (smaller file size)
  - Includes sizes: 16, 24, 32, 48, 64, 72, 96, 128, 256
- `build/icon.png` - Linux icon (committed to repo, uses source)

**Important:** The generated `.icns` and `.ico` files are committed to the repository so that electron-builder can find them during CI builds. **Both files must be committed together** after regeneration to avoid shipping stale icons on one platform.

**Why png2icons?** This tool provides better Windows icon quality than the previous `electron-icon-maker`, with explicit support for small icon sizes (16x16, 32x32) that are critical for Windows taskbar display.

### Icon Configuration

Platform-specific icon paths are explicitly configured in `src/electron/electron-builder.json`:

```json
{
  "mac": {
    "icon": "build/icon.icns",
    ...
  },
  "win": {
    "icon": "build/icon.ico",
    ...
  },
  "linux": {
    "icon": "build/icon.png",
    ...
  }
}
```

## Building the Application

### Quick Build (Development)

For faster iteration during development, use the directory build:

```bash
cd src/electron
npm run build:dir
```

This creates unpacked application directories without creating installers:
- macOS: `dist/mac-arm64/STEEL-IQ.app`
- Windows: `dist/win-unpacked/STEEL-IQ.exe`
- Linux: `dist/linux-unpacked/`

### Full Build

For production builds with installers / distributables:

```bash
cd src/electron
npm run build
```

Outputs include:
- macOS: signed app bundle under `dist/mac-arm64/`
- Windows: unpacked directory under `dist/win-unpacked/`
- Linux: `dist/linux-unpacked/` plus `dist/STEEL-IQ-<version>.AppImage`

### Verifying Icons

After building, verify the icons appear correctly:

**macOS:**
1. Open Finder and navigate to `dist/mac-arm64/`
2. Check the `STEEL-IQ.app` icon in Finder (press Space for Quick Look)
3. The icon should also appear in the Dock when running

**Windows:**
1. Open File Explorer and navigate to `dist/win-unpacked/`
2. Check the `STEEL-IQ.exe` icon
3. The icon should appear in the taskbar when running

**Linux:**
1. Navigate to `dist/linux-unpacked/` and ensure the `resources/app.asar` metadata references the icon
2. When running the AppImage (`chmod +x STEEL-IQ-*.AppImage && ./STEEL-IQ-*.AppImage`), confirm the desktop environment displays the STEEL-IQ icon in the launcher/taskbar

## GitHub Actions Build

The application is automatically built for Windows, macOS, and Linux via GitHub Actions:
- Workflow: `.github/workflows/standalone_app.yaml`
- Triggered manually via `workflow_dispatch`
- Produces platform-specific builds uploaded to S3 (AppImage + `linux-unpacked/` tarball for Linux)

Linux builds run on Ubuntu runners. If you need to reproduce CI output locally, make sure the following packages are installed beforehand:

```bash
sudo apt-get update
sudo apt-get install -y libfuse2 rpm libnss3 libgtk-3-0 libxss1 libasound2t64 libatk1.0-0 \
  libatk-bridge2.0-0 libgdk-pixbuf2.0-0 patchelf
```

> Note: On older Ubuntu/Debian releases the package name is `libasound2`; install whichever is available for your distribution.

Then execute the standard build commands (`npm run build:dir` or `npm run build`). The generated AppImage is located in `dist/`—set execute permissions before launching.

The workflow automatically uses the icons from `src/electron/build/` - no special configuration needed.

## Updating the Icon

To update the application icon:

1. Replace the source PNG:
   ```bash
   cp /path/to/new-icon.png src/electron/build/icon.png
   ```

2. Regenerate platform-specific icons:
   ```bash
   cd src/electron
   npm run icons
   ```
   This runs `generate-icons.js` which uses `png2icons` to create optimized `icon.icns` and `icon.ico` files.

3. Test locally:
   ```bash
   npm run build:dir
   # Check the icon in dist/mac-arm64/STEEL-IQ.app or dist/win-unpacked/
   ```

4. **Commit the generated icon files** (required for CI builds):
   ```bash
   git add src/electron/build/icon.png src/electron/build/icon.icns src/electron/build/icon.ico
   git commit -m "Update application icon"
   ```

   **Note:** These files must be committed so that GitHub Actions can build the app without needing to run `npm run icons` first. **Always commit both `.icns` and `.ico` files together** to avoid shipping stale icons on one platform.

The GitHub Actions workflow will automatically use the new icons on the next build.
