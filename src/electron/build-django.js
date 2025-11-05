import fs from 'fs';
import path from 'path';
import { execSync } from 'child_process';
import { fileURLToPath } from 'url';
import crypto from 'crypto';

// ESM doesn't have __dirname, so we need to create it
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const IS_WIN = process.platform === 'win32';
const IS_MAC = process.platform === 'darwin';
const IS_LINUX = process.platform === 'linux';
const SHARED_LIB_REGEX = IS_WIN ? /\.dll$/i : IS_MAC ? /\.dylib$/ : /\.so(\.\d+)*$/;
const isSharedLib = (filename) => SHARED_LIB_REGEX.test(filename);
const SHARED_LIB_DIR_NAMES = ['.dylibs', '.libs'];
const PYTHON_STANDALONE_RELEASE = '20251007';

const projectRoot = path.join(__dirname, '..', '..');
const djangoSource = path.join(projectRoot, 'src', 'django');
const steeloweb = path.join(projectRoot, 'src', 'steeloweb');
const steelo = path.join(projectRoot, 'src', 'steelo');
const buildDir = path.join(__dirname, 'django-bundle');
const TARGET_PYTHON_VERSION = '3.13.8';
const TARGET_PYTHON_MINOR = TARGET_PYTHON_VERSION.split('.').slice(0, 2).join('.');
const TARGET_PYTHON_BINARY = `python${TARGET_PYTHON_MINOR}`;
const TARGET_PYTHON_MINOR_NO_DOT = TARGET_PYTHON_MINOR.replace('.', '');

console.log('Building Django bundle for production...');

// Clean build directory
if (fs.existsSync(buildDir)) {
  fs.rmSync(buildDir, { recursive: true });
}
fs.mkdirSync(buildDir, { recursive: true });

// Copy Django app
console.log('Copying Django source...');
fs.cpSync(djangoSource, path.join(buildDir, 'django'), { recursive: true });
fs.cpSync(steeloweb, path.join(buildDir, 'steeloweb'), { recursive: true });
fs.cpSync(steelo, path.join(buildDir, 'steelo'), { recursive: true });

// Copy project files needed for dependencies
const pyprojectPath = path.join(projectRoot, 'pyproject.toml');
if (fs.existsSync(pyprojectPath)) {
  fs.copyFileSync(pyprojectPath, path.join(buildDir, 'pyproject.toml'));
}

const readmePath = path.join(projectRoot, 'README.md');
if (fs.existsSync(readmePath)) {
  fs.copyFileSync(readmePath, path.join(buildDir, 'README.md'));
}

// Create portable Python environment (Windows only for now)
if (IS_WIN) {
  console.log('Creating portable Python environment for Windows...');
  
  let portablePythonCreated = false;

  try {
    // First, create a virtual environment to get the dependencies
    console.log('Creating temporary virtual environment to gather dependencies...');
    const tempVenvPath = path.join(buildDir, 'temp-venv');

    execSync(`uv venv "${tempVenvPath}" --python ${TARGET_PYTHON_VERSION}`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    // Install dependencies in temp venv
    execSync(`uv pip install --python "${tempVenvPath}" -e "${projectRoot}"`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    execSync(`uv pip install --python "${tempVenvPath}" django-debug-toolbar pytest pytest-django pytest-mock mypy django-environ whitenoise`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    // Explicitly ensure netcdf4, h5netcdf, h5py, and certifi are installed with all dependencies
    console.log('Ensuring NetCDF4, HDF5, and SSL certificate support...');
    // Force reinstall to ensure we get all dependencies
    execSync(`uv pip install --python "${tempVenvPath}" --force-reinstall netcdf4 h5netcdf h5py certifi`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    // Verify critical packages are installed
    console.log('Verifying NetCDF4 and HDF5 packages...');
    const verifyScript = `
import sys
try:
    import h5py
    print(f"[OK] h5py {h5py.__version__} installed")
except ImportError as e:
    print(f"[ERROR] h5py NOT installed: {e}")
    sys.exit(1)

try:
    import netCDF4
    print(f"[OK] netcdf4 {netCDF4.__version__} installed")
except ImportError as e:
    print(f"[ERROR] netcdf4 NOT installed: {e}")
    sys.exit(1)

try:
    import h5netcdf
    print(f"[OK] h5netcdf {h5netcdf.__version__} installed")
except ImportError as e:
    print(f"[ERROR] h5netcdf NOT installed: {e}")
    sys.exit(1)

print("[OK] All NetCDF4 and HDF5 packages are installed")
`;
    
    // Write verification script to a temporary file to avoid shell escaping issues
    const verifyScriptPath = path.join(buildDir, 'verify_packages.py');
    fs.writeFileSync(verifyScriptPath, verifyScript);
    
    const pythonExe = process.platform === 'win32' 
      ? path.join(tempVenvPath, 'Scripts', 'python.exe')
      : path.join(tempVenvPath, 'bin', 'python');
    
    try {
      execSync(`"${pythonExe}" "${verifyScriptPath}"`, {
        cwd: buildDir,
        stdio: 'inherit'
      });
    } finally {
      // Clean up the temporary script file
      if (fs.existsSync(verifyScriptPath)) {
        fs.unlinkSync(verifyScriptPath);
      }
    }

    // Now create the portable Python directory structure
    console.log('Creating portable Python structure...');
    const pythonDir = path.join(buildDir, 'python');
    fs.mkdirSync(pythonDir, { recursive: true });
    fs.mkdirSync(path.join(pythonDir, 'Scripts'), { recursive: true });
    fs.mkdirSync(path.join(pythonDir, 'Lib'), { recursive: true });
    fs.mkdirSync(path.join(pythonDir, 'DLLs'), { recursive: true });

    // Find the Python installation that UV is using
    // First try to get the Python that UV would use
    let pythonPath;
    let pythonBaseDir;

    try {
      // Try to get UV's Python first
      const uvPythonOutput = execSync(`uv python find ${TARGET_PYTHON_VERSION}`, { encoding: 'utf8' }).trim();
      pythonPath = uvPythonOutput;
      pythonBaseDir = path.dirname(pythonPath);
      console.log(`Found UV Python ${TARGET_PYTHON_VERSION} at: ${pythonPath}`);
    } catch (uvError) {
      try {
        // Fallback: try to find the target Python in common locations
        const possiblePaths = [
          `C:\\hostedtoolcache\\windows\\Python\\${TARGET_PYTHON_VERSION}\\x64\\python.exe`,
          `C:\\Python${TARGET_PYTHON_MINOR_NO_DOT}\\python.exe`,
          `C:\\Program Files\\Python${TARGET_PYTHON_MINOR_NO_DOT}\\python.exe`,
        ];

        for (const testPath of possiblePaths) {
          if (fs.existsSync(testPath)) {
            pythonPath = testPath;
            pythonBaseDir = path.dirname(testPath);
            console.log(`Found Python ${TARGET_PYTHON_MINOR} at: ${pythonPath}`);
            break;
          }
        }

        if (!pythonPath) {
          // Last resort: use where python but check version
          const wherePython = execSync('where python', { encoding: 'utf8' }).trim().split('\n')[0];
          const versionOutput = execSync(`"${wherePython}" --version`, { encoding: 'utf8' });
          console.log(`System Python version: ${versionOutput}`);

          if (versionOutput.includes('3.13') || versionOutput.includes('3.12') || versionOutput.includes('3.11') || versionOutput.includes('3.10')) {
            pythonPath = wherePython;
            pythonBaseDir = path.dirname(pythonPath);
            console.log(`Using system Python: ${pythonPath}`);
          } else {
            throw new Error(`Python version too old: ${versionOutput}. Need Python 3.10+ for Django 4.2+`);
          }
        }
      } catch (fallbackError) {
        throw new Error(`Could not find suitable Python 3.10+ installation: ${fallbackError.message}`);
      }
    }

    // Copy Python executable and essential DLLs
    console.log('Copying Python executable and DLLs...');
    fs.copyFileSync(path.join(pythonBaseDir, 'python.exe'), path.join(pythonDir, 'Scripts', 'python.exe'));

    // Copy Visual C++ Runtime DLLs from approved sources
    console.log('Copying Visual C++ Runtime DLLs from approved sources...');
    const vcRuntimeDlls = ['vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll'];
    let copiedVcDlls = 0;

    vcRuntimeDlls.forEach(dll => {
      let srcPath = path.join(pythonBaseDir, dll);  // Same dir as python.exe
      let source = 'Python installation';

      // If not found in Python dir, try approved fallback sources
      if (!fs.existsSync(srcPath)) {
        // Try parent directory (in case of embeddable Python)
        const pythonParentPath = path.join(pythonBaseDir, '..', dll);
        if (fs.existsSync(pythonParentPath)) {
          srcPath = pythonParentPath;
          source = 'Python embeddable distribution';
        }
        // For GitHub Actions CI, use system DLLs (runners are licensed)
        else if (process.env.GITHUB_ACTIONS === 'true') {
          const systemPath = path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', dll);
          if (fs.existsSync(systemPath)) {
            srcPath = systemPath;
            source = 'GitHub Actions system (licensed)';
            console.log(`  ${dll} not in Python dir, using ${source}`);
          }
        }
      }

      if (fs.existsSync(srcPath)) {
        const destPath = path.join(pythonDir, 'Scripts', dll);
        fs.copyFileSync(srcPath, destPath);
        console.log(`  Copied ${dll} from ${source}`);
        copiedVcDlls++;
      } else {
        console.error(`  ERROR: ${dll} not found in any approved location!`);
      }
    });

    // Require all 3 DLLs for truly portable build
    if (copiedVcDlls < 3) {
      console.error(`\n❌ ERROR: Only ${copiedVcDlls}/3 VC++ Runtime DLLs copied!`);
      console.error('Missing DLLs will cause startup failures on clean Windows installations.');
      console.error('\nTo fix this permanently:');
      console.error('1. Download: https://aka.ms/vs/17/release/vc_redist.x64.exe');
      console.error('2. Extract: vc_redist.x64.exe /extract:path\\to\\extract');
      console.error('3. Copy missing DLLs to Python installation before building');
      throw new Error('VC++ Runtime DLLs incomplete');
    }

    console.log(`[OK] Copied all ${copiedVcDlls}/3 VC++ Runtime DLLs`);

    // Copy pythonw.exe if it exists
    const pythonwPath = path.join(pythonBaseDir, 'pythonw.exe');
    if (fs.existsSync(pythonwPath)) {
      fs.copyFileSync(pythonwPath, path.join(pythonDir, 'Scripts', 'pythonw.exe'));
    }

    // Track copied DLLs to avoid duplicates
    const copiedDlls = new Map(); // Map of lowercase name -> destination path
    
    // Helper function to copy DLL only once
    const copyDllOnce = (sourcePath, destPath, dllName) => {
      const normalizedName = dllName.toLowerCase();
      
      if (copiedDlls.has(normalizedName)) {
        console.log(`  Skipping duplicate DLL: ${dllName}`);
        return false;
      }
      
      try {
        fs.copyFileSync(sourcePath, destPath);
        copiedDlls.set(normalizedName, destPath);
        return true;
      } catch (error) {
        console.error(`  Failed to copy ${dllName}: ${error.message}`);
        return false;
      }
    };

    // Copy Python DLLs
    const pythonDllFiles = fs.readdirSync(pythonBaseDir).filter(file => file.match(/python.*\.dll$/i));
    pythonDllFiles.forEach(dll => {
      copyDllOnce(path.join(pythonBaseDir, dll), path.join(pythonDir, 'Scripts', dll), dll);
    });

    // Copy DLLs directory
    const dllsDir = path.join(pythonBaseDir, 'DLLs');
    if (fs.existsSync(dllsDir)) {
      fs.cpSync(dllsDir, path.join(pythonDir, 'DLLs'), { recursive: true });
    }

    // Copy Library/bin directory (contains HDF5, NetCDF DLLs for packages like netcdf4)
    const libraryBinDir = path.join(pythonBaseDir, '..', 'Library', 'bin');
    if (fs.existsSync(libraryBinDir)) {
      console.log('Copying Library/bin directory with HDF5 and NetCDF DLLs...');
      const destLibraryBinDir = path.join(pythonDir, 'Library', 'bin');
      fs.mkdirSync(path.dirname(destLibraryBinDir), { recursive: true });
      fs.cpSync(libraryBinDir, destLibraryBinDir, { recursive: true });
      
      // Also copy DLLs to Scripts directory for better accessibility
      const libraryDllFiles = fs.readdirSync(libraryBinDir).filter(file => file.endsWith('.dll'));
      console.log(`Found ${libraryDllFiles.length} DLL files in Library/bin`);
      let copiedCount = 0;
      libraryDllFiles.forEach(dll => {
        if (copyDllOnce(path.join(libraryBinDir, dll), path.join(pythonDir, 'Scripts', dll), dll)) {
          copiedCount++;
        }
      });
      console.log(`  Copied ${copiedCount} unique DLLs from Library/bin`);
    }

    // Copy essential parts of Lib (excluding site-packages for now)
    const libDir = path.join(pythonBaseDir, 'Lib');
    if (fs.existsSync(libDir)) {
      console.log('Copying Python standard library...');
      const libItems = fs.readdirSync(libDir);
      libItems.forEach(item => {
        if (item !== 'site-packages' && item !== '__pycache__') {
          const srcPath = path.join(libDir, item);
          const destPath = path.join(pythonDir, 'Lib', item);
          if (fs.statSync(srcPath).isDirectory()) {
            fs.cpSync(srcPath, destPath, { recursive: true });
          } else {
            fs.copyFileSync(srcPath, destPath);
          }
        }
      });
    }

    // Copy SSL certificates from the base Python installation
    // Check for certificates in the Python installation
    const sslCertPaths = [
      path.join(pythonBaseDir, '..', 'Library', 'ssl'),
      path.join(pythonBaseDir, 'Lib', 'site-packages', 'certifi'),
      path.join(pythonBaseDir, '..', 'ssl'),
      path.join(tempVenvPath, 'Lib', 'site-packages', 'certifi')
    ];
    
    for (const certPath of sslCertPaths) {
      if (fs.existsSync(certPath)) {
        console.log(`Copying SSL certificates from ${certPath}...`);
        const destCertPath = certPath.includes('Library') 
          ? path.join(pythonDir, 'Library', 'ssl')
          : path.join(pythonDir, 'ssl');
        fs.mkdirSync(path.dirname(destCertPath), { recursive: true });
        fs.cpSync(certPath, destCertPath, { recursive: true });
        break;
      }
    }

    // Get Python version for site-packages path
    const pythonVersion = execSync(`"${pythonPath}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"`, { encoding: 'utf8' }).trim();
    console.log(`Python version: ${pythonVersion}`);

    // Create site-packages directory and copy from temp venv
    // Windows: python/Lib/site-packages (no version subdirectory)
    // Unix:    python/lib/pythonX.Y/site-packages (with version subdirectory)
    const sitePackagesDir = process.platform === 'win32'
      ? path.join(pythonDir, 'Lib', 'site-packages')
      : path.join(pythonDir, 'lib', `python${pythonVersion}`, 'site-packages');
    fs.mkdirSync(sitePackagesDir, { recursive: true });

    console.log('Copying installed packages...');
    const tempSitePackages = path.join(tempVenvPath, 'Lib', 'site-packages');
    if (fs.existsSync(tempSitePackages)) {
      fs.cpSync(tempSitePackages, sitePackagesDir, { recursive: true });
    }

    // Debug: Verify site-packages path and contents
    console.log(`DEBUG: sitePackagesDir = ${sitePackagesDir}`);
    console.log(`DEBUG: sitePackagesDir exists: ${fs.existsSync(sitePackagesDir)}`);
    if (fs.existsSync(sitePackagesDir)) {
      const topLevelDirs = fs.readdirSync(sitePackagesDir, { withFileTypes: true })
        .filter(d => d.isDirectory())
        .map(d => d.name)
        .slice(0, 10);  // First 10 directories
      console.log(`DEBUG: First 10 directories in site-packages: ${topLevelDirs.join(', ')}`);

      // Check if numpy exists and where its DLLs are
      const numpyPath = path.join(sitePackagesDir, 'numpy');
      if (fs.existsSync(numpyPath)) {
        console.log(`DEBUG: numpy directory exists`);

        // Check for .libs subdirectory
        const numpyLibsPath = path.join(numpyPath, '.libs');
        console.log(`DEBUG: numpy/.libs exists: ${fs.existsSync(numpyLibsPath)}`);
        if (fs.existsSync(numpyLibsPath)) {
          const libsDlls = fs.readdirSync(numpyLibsPath).filter(f => f.endsWith('.dll'));
          console.log(`DEBUG: numpy/.libs contains ${libsDlls.length} DLLs: ${libsDlls.slice(0, 5).join(', ')}`);
        }

        // Check for DLLs at numpy package root
        const numpyRootDlls = fs.readdirSync(numpyPath).filter(f => f.endsWith('.dll'));
        console.log(`DEBUG: numpy root contains ${numpyRootDlls.length} DLLs: ${numpyRootDlls.slice(0, 5).join(', ')}`);

        // Check for DLLs in numpy subdirectories
        const numpySubdirs = fs.readdirSync(numpyPath, { withFileTypes: true })
          .filter(d => d.isDirectory())
          .map(d => d.name)
          .slice(0, 10);
        console.log(`DEBUG: numpy subdirectories: ${numpySubdirs.join(', ')}`);

        // Check _core subdirectory (where DLLs might be in NumPy 2.x)
        const numpyCorePath = path.join(numpyPath, '_core');
        if (fs.existsSync(numpyCorePath)) {
          const coreDlls = fs.readdirSync(numpyCorePath).filter(f => f.endsWith('.dll'));
          console.log(`DEBUG: numpy/_core contains ${coreDlls.length} DLLs: ${coreDlls.slice(0, 5).join(', ')}`);
        }
      } else {
        console.log(`DEBUG: numpy directory NOT found`);
      }
    }

    // Copy all .libs DLLs to Scripts directory for Windows DLL loading
    console.log('Copying .libs DLLs to Scripts directory for Windows...');

    // Recursively find ALL .libs directories (no hard-coded list)
    // Handles both old style (.libs subdirectory) and new style (*.libs directory at package level)
    function findLibsDirs(dir, results = []) {
      try {
        const entries = fs.readdirSync(dir, { withFileTypes: true });
        for (const entry of entries) {
          const fullPath = path.join(dir, entry.name);
          if (entry.isDirectory()) {
            // Match both '.libs' subdirectories and '*.libs' package-level directories
            // e.g., numpy/.libs (old) or numpy.libs (new in NumPy 2.x)
            if (entry.name === '.libs' || entry.name.endsWith('.libs')) {
              results.push(fullPath);
              // Don't recurse into .libs directories themselves
            } else if (!entry.name.startsWith('.') && !entry.name.includes('__pycache__')) {
              // Recurse into subdirectories, skip hidden and cache dirs
              findLibsDirs(fullPath, results);
            }
          }
        }
      } catch (e) {
        // Ignore permission errors, etc.
      }
      return results;
    }

    const libsDirs = findLibsDirs(sitePackagesDir);
    console.log(`Found ${libsDirs.length} .libs directories in site-packages`);

    // Track DLLs with hash verification to detect conflicts
    // crypto already imported at top
    const dllHashes = new Map();  // dllName.toLowerCase() -> { path, hash, package }
    let totalDllsCopied = 0;

    libsDirs.forEach(libsDir => {
      const pkgName = path.basename(path.dirname(libsDir));
      let dlls;
      try {
        dlls = fs.readdirSync(libsDir).filter(f => f.endsWith('.dll'));
      } catch (e) {
        console.warn(`  Could not read ${libsDir}: ${e.message}`);
        return;
      }

      if (dlls.length > 0) {
        console.log(`  ${pkgName}/.libs: ${dlls.length} DLLs`);
      }

      dlls.forEach(dll => {
        const dllLower = dll.toLowerCase();
        const srcPath = path.join(libsDir, dll);
        const destPath = path.join(pythonDir, 'Scripts', dll);

        // Calculate hash to detect conflicts
        let hash;
        try {
          hash = crypto.createHash('sha256')
            .update(fs.readFileSync(srcPath))
            .digest('hex').substring(0, 16);
        } catch (e) {
          console.warn(`    Failed to hash ${dll}: ${e.message}`);
          return;
        }

        if (dllHashes.has(dllLower)) {
          const existing = dllHashes.get(dllLower);
          if (existing.hash !== hash) {
            console.warn(`    ⚠️  WARNING: ${dll} differs between ${existing.package} and ${pkgName}`);
            console.warn(`    Keeping version from ${existing.package}`);
            return;  // Skip conflicting DLL
          } else {
            // Same DLL, skip silently
            return;
          }
        }

        try {
          fs.copyFileSync(srcPath, destPath);
          dllHashes.set(dllLower, { path: srcPath, hash, package: pkgName });
          totalDllsCopied++;
        } catch (e) {
          console.warn(`    Failed to copy ${dll}: ${e.message}`);
        }
      });
    });

    console.log(`[OK] Copied ${totalDllsCopied} unique DLLs from .libs directories`);

    // Also create a simple .pth file as backup (without the complex code)
    // This adds .libs directories to sys.path, which sometimes helps
    const pthFilePath = path.join(sitePackagesDir, 'steelo_libs_paths.pth');
    const pthContent = libsDirs.join('\n');
    fs.writeFileSync(pthFilePath, pthContent);
    console.log(`[OK] Created .pth file with ${libsDirs.length} .libs paths`);

    // Also check if the temp venv has Library/bin with additional DLLs
    const tempLibraryBin = path.join(tempVenvPath, '..', 'Library', 'bin');
    if (fs.existsSync(tempLibraryBin)) {
      console.log('Found Library/bin in temp venv, copying additional DLLs...');
      const tempDllFiles = fs.readdirSync(tempLibraryBin).filter(file => file.endsWith('.dll'));
      let tempCopiedCount = 0;
      tempDllFiles.forEach(dll => {
        if (copyDllOnce(path.join(tempLibraryBin, dll), path.join(pythonDir, 'Scripts', dll), dll)) {
          tempCopiedCount++;
        }
      });
      console.log(`  Copied ${tempCopiedCount} unique DLLs from temp venv Library/bin`);
    }

    // Test the portable Python installation
    console.log('Testing portable Python installation...');
    const portablePython = path.join(pythonDir, 'Scripts', 'python.exe');

    execSync(`"${portablePython}" --version`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    execSync(`"${portablePython}" -c "import sys; print('Python path:', sys.executable)"`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    execSync(`"${portablePython}" -c "import django; print('Django version:', django.VERSION)"`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    // Final verification of NetCDF4 and HDF5 support in portable Python
    console.log('Final verification of NetCDF4 and HDF5 support...');
    const finalVerifyScript = `
import sys
import os
print("Python executable:", sys.executable)

# Test imports and functionality
try:
    import netCDF4
    print(f"[OK] netcdf4 {netCDF4.__version__} working")
    # Test creating a file
    nc = netCDF4.Dataset("test.nc", "w", format="NETCDF4")
    nc.close()
    os.remove("test.nc")
    print("  [OK] Can create NetCDF4 files")
except Exception as e:
    print(f"[ERROR] netcdf4 error: {e}")

try:
    import h5netcdf
    import h5netcdf.legacyapi
    print(f"[OK] h5netcdf {h5netcdf.__version__} working")
except Exception as e:
    print(f"[ERROR] h5netcdf error: {e}")

try:
    import h5py
    print(f"[OK] h5py {h5py.__version__} working")
    # Test creating HDF5 file
    with h5py.File("test.h5", "w") as f:
        f.create_dataset("test", data=[1,2,3])
    os.remove("test.h5")
    print("  [OK] Can create HDF5 files")
except Exception as e:
    print(f"[ERROR] h5py error: {e}")

# Check for DLLs on Windows
import glob
python_dir = os.path.dirname(sys.executable)
dll_patterns = [
    os.path.join(python_dir, "*.dll"),
    os.path.join(python_dir, "..", "Library", "bin", "*.dll"),
]
hdf5_dlls = []
netcdf_dlls = []
for pattern in dll_patterns:
    for dll in glob.glob(pattern):
        dll_name = os.path.basename(dll).lower()
        if 'hdf5' in dll_name:
            hdf5_dlls.append(dll_name)
        elif 'netcdf' in dll_name:
            netcdf_dlls.append(dll_name)

print(f"\\nFound {len(hdf5_dlls)} HDF5 DLLs: {', '.join(hdf5_dlls[:3])}{'...' if len(hdf5_dlls) > 3 else ''}")
print(f"Found {len(netcdf_dlls)} NetCDF DLLs: {', '.join(netcdf_dlls[:3])}{'...' if len(netcdf_dlls) > 3 else ''}")
`;
    
    try {
      // Set up PATH to include Scripts and Library/bin for DLL loading
      const pythonScriptsDir = path.join(pythonDir, 'Scripts');
      const libraryBinDir = path.join(pythonDir, 'Library', 'bin');
      const extraPath = `${pythonScriptsDir};${libraryBinDir}`;
      
      execSync(`"${portablePython}" -c "${finalVerifyScript}"`, {
        cwd: buildDir,
        stdio: 'inherit',
        env: {
          ...process.env,
          PYTHONIOENCODING: 'utf-8',
          PATH: `${extraPath};${process.env.PATH}`
        }
      });
      console.log('[OK] NetCDF4 and HDF5 support verified successfully');
    } catch (verifyError) {
      console.error('WARNING: NetCDF4/HDF5 verification failed, but continuing build...');
      console.error(verifyError.message);
    }

    // Clean up temp venv with robust error handling
    console.log('Cleaning up temporary venv...');
    if (fs.existsSync(tempVenvPath)) {
      try {
        fs.rmSync(tempVenvPath, { recursive: true, force: true });
        console.log('[OK] Temp venv cleaned up successfully');
      } catch (error) {
        console.error('ERROR: Failed to clean up temp-venv:', error);
        // Force cleanup on Windows
        if (process.platform === 'win32') {
          try {
            execSync(`rmdir /s /q "${tempVenvPath}"`, { stdio: 'inherit' });
          } catch (cmdError) {
            console.error('Failed to remove via rmdir:', cmdError);
          }
        }
      }
    }

    // Verify cleanup
    if (fs.existsSync(tempVenvPath)) {
      throw new Error('CRITICAL: temp-venv still exists after cleanup!');
    }

    console.log('[OK] Portable Python environment created successfully');
    
    // Report DLL deduplication stats
    if (copiedDlls.size > 0) {
      console.log(`\nDLL deduplication: ${copiedDlls.size} unique DLLs copied`);
    }

    // Clean up any .venv if it exists (from previous/failed builds)
    const venvPath = path.join(buildDir, '.venv');
    if (fs.existsSync(venvPath)) {
      console.log('Removing .venv directory (not needed for distribution)...');
      try {
        fs.rmSync(venvPath, { recursive: true, force: true });
        console.log('[OK] .venv directory removed');
      } catch (error) {
        console.error('Warning: Failed to remove .venv:', error.message);
      }
    }

    // Success! Skip the fallback by jumping to the end of the Windows section
    portablePythonCreated = true;

  } catch (error) {
    console.error('Failed to create portable Python environment:', error.message);
    
    // Clean up temp-venv if it exists before falling back
    const tempVenvPath = path.join(buildDir, 'temp-venv');
    if (fs.existsSync(tempVenvPath)) {
      console.log('Cleaning up failed temp-venv...');
      try {
        fs.rmSync(tempVenvPath, { recursive: true, force: true });
        console.log('[OK] Temp venv cleaned up');
      } catch (cleanupError) {
        console.error('Failed to clean up temp-venv:', cleanupError.message);
        if (process.platform === 'win32') {
          try {
            execSync(`rmdir /s /q "${tempVenvPath}"`, { stdio: 'inherit' });
          } catch (cmdError) {
            console.error('Failed to remove via rmdir:', cmdError);
          }
        }
      }
    }
    
    // Only create fallback if portable Python was not successfully created
    if (!portablePythonCreated) {
      console.log('Falling back to relocatable venv approach...');

      // Fallback to the relocatable venv approach
      const venvPath = path.join(buildDir, '.venv');
      try {
        execSync(`uv venv "${venvPath}" --python ${TARGET_PYTHON_VERSION} --relocatable`, {
          cwd: buildDir,
          stdio: 'inherit'
        });

        execSync(`uv pip install --python "${venvPath}" -e "${projectRoot}"`, {
          cwd: buildDir,
          stdio: 'inherit'
        });

        execSync(`uv pip install --python "${venvPath}" django-debug-toolbar pytest pytest-django pytest-mock mypy django-environ whitenoise`, {
          cwd: buildDir,
          stdio: 'inherit'
        });

        console.log('[OK] Fallback virtual environment created');
      } catch (fallbackError) {
        console.error('Fallback also failed:', fallbackError.message);
      }
    }
  }
} else if (IS_MAC || IS_LINUX) {
  const platformLabel = IS_MAC ? 'macOS' : 'Linux';
  console.log(`Creating portable Python environment for ${platformLabel}...`);
  
  // Define tempVenvPath and isPythonStandalone outside try block so they're accessible in catch block
  const tempVenvPath = path.join(buildDir, 'temp-venv');
  let isPythonStandalone = false;

  // Check if python-standalone needs to be downloaded
  const pythonStandalonePath = path.resolve(__dirname, 'python-standalone');
  const pythonStandaloneBinary = path.join(pythonStandalonePath, 'python', 'bin', TARGET_PYTHON_BINARY);
  
  if (!fs.existsSync(pythonStandaloneBinary)) {
    console.log('Python-build-standalone not found, downloading...');
    
    try {
      // Determine architecture triple for python-build-standalone
      const arch = process.arch === 'arm64' ? 'aarch64' : 'x86_64';
      const targetTriple = IS_MAC
        ? `${arch}-apple-darwin`
        : `${arch}-unknown-linux-gnu`;
      const pythonStandaloneUrl = `https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_STANDALONE_RELEASE}/cpython-${TARGET_PYTHON_VERSION}+${PYTHON_STANDALONE_RELEASE}-${targetTriple}-install_only_stripped.tar.gz`;
      
      console.log(`Downloading from: ${pythonStandaloneUrl}`);
      console.log('This may take a few minutes...');
      
      // Download and extract
      execSync(`curl -L "${pythonStandaloneUrl}" -o python-standalone.tar.gz`, {
        cwd: __dirname,
        stdio: 'inherit'
      });
      
      fs.mkdirSync(pythonStandalonePath, { recursive: true });
      
      execSync(`tar -xzf python-standalone.tar.gz -C "${pythonStandalonePath}"`, {
        cwd: __dirname,
        stdio: 'inherit'
      });
      
      // Clean up tarball
      fs.unlinkSync(path.join(__dirname, 'python-standalone.tar.gz'));
      
      // Make Python executable
      execSync(`chmod +x "${pythonStandaloneBinary}"`, { stdio: 'inherit' });
      
      console.log('[OK] Python-build-standalone downloaded and extracted');
    } catch (downloadError) {
      console.error('Failed to download python-build-standalone:', downloadError.message);
      console.error('You can manually download it by running:');
      console.error('  cd src/electron');
      const fallbackArch = process.arch === 'arm64' ? 'aarch64' : 'x86_64';
      const fallbackTriple = IS_MAC
        ? `${fallbackArch}-apple-darwin`
        : `${fallbackArch}-unknown-linux-gnu`;
      console.error(`  curl -L https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_STANDALONE_RELEASE}/cpython-${TARGET_PYTHON_VERSION}+${PYTHON_STANDALONE_RELEASE}-${fallbackTriple}-install_only_stripped.tar.gz -o python-standalone.tar.gz`);
      console.error('  mkdir -p python-standalone');
      console.error('  tar -xzf python-standalone.tar.gz -C python-standalone');
    }
  }

  try {
    // First, create a virtual environment to get the dependencies
    console.log('Creating temporary virtual environment to gather dependencies...');

    execSync(`uv venv "${tempVenvPath}" --python ${TARGET_PYTHON_VERSION}`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    // Install dependencies in temp venv
    execSync(`uv pip install --python "${tempVenvPath}" -e "${projectRoot}"`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    execSync(`uv pip install --python "${tempVenvPath}" django-debug-toolbar pytest pytest-django pytest-mock mypy django-environ whitenoise`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    // Explicitly ensure netcdf4, h5netcdf, h5py, and certifi are installed with all dependencies
    console.log('Ensuring NetCDF4, HDF5, and SSL certificate support...');
    // Force reinstall to ensure we get all dependencies
    execSync(`uv pip install --python "${tempVenvPath}" --force-reinstall netcdf4 h5netcdf h5py certifi`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    console.log('Preparing HDF5/NetCDF shared libraries for verification...');
    const tempPythonBinary = process.platform === 'win32'
      ? path.join(tempVenvPath, 'Scripts', 'python.exe')
      : path.join(tempVenvPath, 'bin', 'python');
    const tempPythonVersion = execSync(
      `"${tempPythonBinary}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"` ,
      { encoding: 'utf8' }
    ).trim();
    const tempSitePackagesForVerify = process.platform === 'win32'
      ? path.join(tempVenvPath, 'Lib', 'site-packages')
      : path.join(tempVenvPath, 'lib', `python${tempPythonVersion}`, 'site-packages');
    const tempLibDirVerify = process.platform === 'win32'
      ? path.join(tempVenvPath, 'DLLs')
      : path.join(tempVenvPath, 'lib');
    fs.mkdirSync(tempLibDirVerify, { recursive: true });
    const sharedLibPackages = ['h5py', 'netCDF4', 'h5netcdf'];
    const verifyLibsCopied = new Set();
    sharedLibPackages.forEach(pkg => {
      SHARED_LIB_DIR_NAMES.forEach(dirName => {
        const pkgLibDir = path.join(tempSitePackagesForVerify, pkg, dirName);
        if (fs.existsSync(pkgLibDir)) {
          const sharedLibFiles = fs.readdirSync(pkgLibDir).filter(isSharedLib);
          sharedLibFiles.forEach(file => {
            if (verifyLibsCopied.has(file)) {
              return;
            }
            const src = path.join(pkgLibDir, file);
            const dest = path.join(tempLibDirVerify, file);
            fs.copyFileSync(src, dest);
            verifyLibsCopied.add(file);
          });
        }
      });
    });

    // Verify critical packages are installed
    console.log('Verifying NetCDF4 and HDF5 packages...');
    const verifyScript = `
import sys
try:
    import h5py
    print(f"[OK] h5py {h5py.__version__} installed")
except ImportError as e:
    print(f"[ERROR] h5py NOT installed: {e}")
    sys.exit(1)

try:
    import netCDF4
    print(f"[OK] netcdf4 {netCDF4.__version__} installed")
except ImportError as e:
    print(f"[ERROR] netcdf4 NOT installed: {e}")
    sys.exit(1)

try:
    import h5netcdf
    print(f"[OK] h5netcdf {h5netcdf.__version__} installed")
except ImportError as e:
    print(f"[ERROR] h5netcdf NOT installed: {e}")
    sys.exit(1)

print("[OK] All NetCDF4 and HDF5 packages are installed")
`;
    
    // Write verification script to a temporary file to avoid shell escaping issues
    const verifyScriptPath = path.join(buildDir, 'verify_packages.py');
    fs.writeFileSync(verifyScriptPath, verifyScript);
    
    const pythonExe = tempPythonBinary;
    
    try {
      const verifyEnv = { ...process.env, PYTHONNOUSERSITE: '1' };
      const libEnvKey = IS_MAC ? 'DYLD_LIBRARY_PATH' : IS_LINUX ? 'LD_LIBRARY_PATH' : undefined;
      if (libEnvKey) {
        const candidatePaths = [
          tempLibDirVerify,
          path.join(tempSitePackagesForVerify, 'h5py', '.dylibs'),
          path.join(tempSitePackagesForVerify, 'netCDF4', '.dylibs'),
          path.join(tempSitePackagesForVerify, 'h5netcdf', '.dylibs'),
          path.join(tempSitePackagesForVerify, 'h5py', '.libs'),
          path.join(tempSitePackagesForVerify, 'netCDF4', '.libs'),
          path.join(tempSitePackagesForVerify, 'h5netcdf', '.libs')
        ].filter(dir => fs.existsSync(dir));
        if (candidatePaths.length > 0) {
          verifyEnv[libEnvKey] = candidatePaths.concat(
            verifyEnv[libEnvKey] ? verifyEnv[libEnvKey].split(':') : []
          ).filter(Boolean).join(':');
        }
        if (process.env.BUILD_DEBUG === '1') {
          console.log(`DEBUG verify ${libEnvKey}:`, verifyEnv[libEnvKey]);
        }
      }

      execSync(`"${pythonExe}" "${verifyScriptPath}"`, {
        cwd: buildDir,
        stdio: 'inherit',
        env: verifyEnv
      });
    } finally {
      // Clean up the temporary script file
      if (fs.existsSync(verifyScriptPath)) {
        fs.unlinkSync(verifyScriptPath);
      }
    }

    // Now create the portable Python directory structure (same as Windows approach)
    console.log(`Creating portable Python structure for ${platformLabel}...`);
    const pythonDir = path.join(buildDir, 'python');
    fs.mkdirSync(pythonDir, { recursive: true });
    fs.mkdirSync(path.join(pythonDir, 'bin'), { recursive: true });
    fs.mkdirSync(path.join(pythonDir, 'lib'), { recursive: true });

    // Find the Python installation that UV is using
    let pythonPath;
    let pythonBaseDir;

    try {
      // First try to find python-build-standalone (if available)
      const standalonePythonPath = path.resolve(__dirname, 'python-standalone', 'python', 'bin', TARGET_PYTHON_BINARY);
      if (fs.existsSync(standalonePythonPath)) {
        pythonPath = standalonePythonPath;
        pythonBaseDir = path.dirname(pythonPath);
        isPythonStandalone = true;
        console.log(`Found python-build-standalone at: ${pythonPath}`);
        console.log('[OK] Will create truly portable Python environment');
      } else {
        // Try to get UV's Python
        const uvPythonOutput = execSync(`uv python find ${TARGET_PYTHON_VERSION}`, { encoding: 'utf8' }).trim();
        pythonPath = uvPythonOutput;
        pythonBaseDir = path.dirname(pythonPath);
        console.log(`Found UV Python ${TARGET_PYTHON_VERSION} at: ${pythonPath}`);
      }
    } catch (uvError) {
      try {
        // Fallback: use system Python but check version
        const systemPython = execSync('which python3', { encoding: 'utf8' }).trim();
        const versionOutput = execSync(`"${systemPython}" --version`, { encoding: 'utf8' });
        console.log(`System Python version: ${versionOutput}`);

        if (versionOutput.includes('3.13') || versionOutput.includes('3.12') || versionOutput.includes('3.11') || versionOutput.includes('3.10')) {
          pythonPath = systemPython;
          pythonBaseDir = path.dirname(pythonPath);
          console.log(`Using system Python: ${pythonPath}`);
        } else {
          throw new Error(`Python version too old: ${versionOutput}. Need Python 3.10+ for Django 4.2+`);
        }
      } catch (fallbackError) {
        throw new Error(`Could not find suitable Python 3.10+ installation: ${fallbackError.message}`);
      }
    }

    // Handle Python installation based on whether we have python-build-standalone
    if (isPythonStandalone) {
      // For python-build-standalone, copy the entire Python installation
      console.log('Copying complete python-build-standalone installation...');
      const standaloneDir = path.resolve(__dirname, 'python-standalone', 'python');
      
      // Copy all directories and files from python-build-standalone
      const itemsToCopy = fs.readdirSync(standaloneDir);
      for (const item of itemsToCopy) {
        const srcPath = path.join(standaloneDir, item);
        const destPath = path.join(pythonDir, item);
        
        try {
          const stat = fs.statSync(srcPath);
          if (stat.isDirectory()) {
            console.log(`  Copying directory: ${item}`);
            fs.cpSync(srcPath, destPath, { recursive: true });
          } else {
            console.log(`  Copying file: ${item}`);
            fs.copyFileSync(srcPath, destPath);
          }
        } catch (e) {
          console.error(`  Warning: Failed to copy ${item}: ${e.message}`);
        }
      }
      
      // Ensure executables are executable
      const binDir = path.join(pythonDir, 'bin');
      if (fs.existsSync(binDir)) {
        const binFiles = fs.readdirSync(binDir);
        for (const file of binFiles) {
          if (file.startsWith('python') || file === 'pip' || file === 'pip3') {
            try {
              execSync(`chmod +x "${path.join(binDir, file)}"`, { stdio: 'inherit' });
            } catch (e) {
              console.error(`  Warning: Failed to make ${file} executable: ${e.message}`);
            }
          }
        }
      }
      
      console.log('[OK] Complete python-build-standalone copied');
      
      // CRITICAL: Verify bundled libpython shared library exists
      const libDir = path.join(pythonDir, 'lib');
      let libpythonFound = false;
      if (fs.existsSync(libDir)) {
        const libCandidates = fs.readdirSync(libDir);
        libpythonFound = libCandidates.some((file) => file.startsWith(`libpython${TARGET_PYTHON_MINOR}`) && isSharedLib(file));
      }
      if (!libpythonFound) {
        throw new Error(`CRITICAL: libpython${TARGET_PYTHON_MINOR} shared library not found in ${libDir}. Python-build-standalone may be corrupted.`);
      }
      console.log(`[OK] Verified libpython${TARGET_PYTHON_MINOR} shared library exists`);
      
      // Fix symlinks in bin directory
      console.log('Fixing Python symlinks...');
      const actualPythonBinary = path.join(pythonDir, 'bin', TARGET_PYTHON_BINARY);
      if (!fs.existsSync(actualPythonBinary)) {
        throw new Error(`CRITICAL: Actual Python binary not found at ${actualPythonBinary}`);
      }
      
      // Remove symlinks and copy actual binary
      const pythonSymlink = path.join(pythonDir, 'bin', 'python');
      const python3Symlink = path.join(pythonDir, 'bin', 'python3');
      
      try {
        // Remove symlinks if they exist
        if (fs.existsSync(pythonSymlink) && fs.lstatSync(pythonSymlink).isSymbolicLink()) {
          fs.unlinkSync(pythonSymlink);
        }
        if (fs.existsSync(python3Symlink) && fs.lstatSync(python3Symlink).isSymbolicLink()) {
          fs.unlinkSync(python3Symlink);
        }
        
        // Copy actual binary to python and python3
        fs.copyFileSync(actualPythonBinary, pythonSymlink);
        fs.copyFileSync(actualPythonBinary, python3Symlink);
        
        // Make them executable
        execSync(`chmod +x "${pythonSymlink}"`, { stdio: 'inherit' });
        execSync(`chmod +x "${python3Symlink}"`, { stdio: 'inherit' });
        
        console.log('[OK] Fixed Python symlinks - copied actual binary');
      } catch (e) {
        throw new Error(`Failed to fix Python symlinks: ${e.message}`);
      }
      
    } else {
      // For system Python, copy just the executable (legacy behavior)
      console.log('Copying Python executable...');
      fs.copyFileSync(pythonPath, path.join(pythonDir, 'bin', 'python'));
      fs.copyFileSync(pythonPath, path.join(pythonDir, 'bin', 'python3'));

      // Make executables executable
      execSync(`chmod +x "${path.join(pythonDir, 'bin', 'python')}"`, { stdio: 'inherit' });
      execSync(`chmod +x "${path.join(pythonDir, 'bin', 'python3')}"`, { stdio: 'inherit' });
    }

    // Find the base Python installation directory
    let pythonInstallDir;
    if (isPythonStandalone) {
      // For python-build-standalone, we've already copied everything
      pythonInstallDir = pythonDir;
      console.log(`Using portable Python at: ${pythonInstallDir}`);
    } else {
      // For system Python, try to find the installation root
      pythonInstallDir = path.dirname(pythonBaseDir);
      console.log(`Using system Python at: ${pythonInstallDir}`);
    }

    console.log('Handling Python shared library dependencies...');
    
    if (IS_MAC) {
      // For python-build-standalone, dylibs should already be included
      if (isPythonStandalone) {
        console.log('Using python-build-standalone - dylibs already included');
        // python-build-standalone includes all necessary dylibs
        // No need to copy or modify them
      } else {
        // For system Python, try to find and copy dylibs
        console.log('Using system Python - searching for dylibs...');
        
        // First try otool if available
        try {
          const otoolOutput = execSync(`otool -L "${pythonPath}"`, { encoding: 'utf8' });
          const dylibMatches = otoolOutput.match(/\s+(.*\.dylib)/gm);
          
          if (dylibMatches) {
            const targetLibDir = path.join(pythonDir, 'lib');
            fs.mkdirSync(targetLibDir, { recursive: true });
            
            dylibMatches.forEach(match => {
              const dylibPath = match.trim().split(' ')[0];
              
              // Only copy Python-related dylibs, not system ones
              if (dylibPath.includes('Python') || dylibPath.includes('python')) {
                const dylibName = path.basename(dylibPath);
                console.log(`Found Python dylib: ${dylibPath}`);
                
                // Handle @executable_path, @loader_path, @rpath references
                let actualDylibPath = dylibPath;
                if (dylibPath.startsWith('@')) {
                  // Try to resolve relative paths
                  const possiblePaths = [
                    path.join(pythonBaseDir, '..', 'lib', dylibName),
                    path.join(pythonInstallDir, 'lib', dylibName),
                    path.join(pythonInstallDir, dylibName),
                    `/usr/local/opt/python@${TARGET_PYTHON_MINOR}/Frameworks/Python.framework/Versions/${TARGET_PYTHON_MINOR}/lib/${dylibName}`
                  ];
                  
                  for (const testPath of possiblePaths) {
                    if (fs.existsSync(testPath)) {
                      actualDylibPath = testPath;
                      break;
                    }
                  }
                }
                
                if (fs.existsSync(actualDylibPath)) {
                  const destPath = path.join(targetLibDir, dylibName);
                  console.log(`Copying ${dylibName} from ${actualDylibPath}`);
                  fs.copyFileSync(actualDylibPath, destPath);
                  
                  // Update the copied Python executable to use the bundled dylib
                  try {
                    execSync(`install_name_tool -change "${dylibPath}" "@executable_path/../lib/${dylibName}" "${path.join(pythonDir, 'bin', 'python')}"`, { stdio: 'inherit' });
                  } catch (e) {
                    console.log(`Warning: Could not update dylib path with install_name_tool: ${e.message}`);
                  }
                } else {
                  console.log(`Warning: Could not find dylib at ${actualDylibPath}`);
                }
              }
            });
          }
        } catch (e) {
          console.log(`Note: Could not use otool to analyze dependencies: ${e.message}`);
          console.log('Falling back to manual dylib search...');
        }
      }
    } else if (IS_LINUX) {
      if (isPythonStandalone) {
        console.log('Using python-build-standalone - shared libraries already included for Linux');
      } else {
        console.log('Using system Python on Linux - ensure required shared libraries are available on target systems');
      }
    }

    // Copy essential parts of Python installation (similar to Windows approach)
    if (!isPythonStandalone) {
      // Only copy lib directory for system Python
      // python-build-standalone already has everything copied
      console.log('Copying Python installation files...');

      // Copy lib directory if it exists
      const srcLibDir = path.join(pythonInstallDir, 'lib');
      if (fs.existsSync(srcLibDir)) {
        console.log('Copying Python standard library...');
        
        // First, copy all dylib files to our python/lib directory
        const targetLibDir = path.join(pythonDir, 'lib');
        fs.mkdirSync(targetLibDir, { recursive: true });
        
        const libItems = fs.readdirSync(srcLibDir);
        libItems.forEach(item => {
          const srcPath = path.join(srcLibDir, item);
          
          if (fs.statSync(srcPath).isFile() && isSharedLib(item)) {
            // Copy shared library files directly to python/lib/
            const destPath = path.join(targetLibDir, item);
            console.log(`Copying shared lib: ${item}`);
            fs.copyFileSync(srcPath, destPath);
          } else if (fs.statSync(srcPath).isDirectory()) {
            // For directories, copy python standard library
            const destPath = path.join(pythonDir, 'lib', item);
            if (item.startsWith('python') && !item.includes('site-packages')) {
              console.log(`Copying Python stdlib directory: ${item}`);
              fs.cpSync(srcPath, destPath, { recursive: true });
            }
          }
        });
      } else {
        console.log(`Warning: lib directory not found at ${srcLibDir}`);
      
      // Try alternate locations for dylib files
      const possibleDylibPaths = [
        path.join(pythonInstallDir, 'lib'),
        path.join(path.dirname(pythonInstallDir), 'lib'),
        pythonInstallDir // Sometimes dylibs are in the root
      ];
      
      for (const libPath of possibleDylibPaths) {
        if (fs.existsSync(libPath)) {
          try {
            const items = fs.readdirSync(libPath);
            const dylibFiles = items.filter(item => isSharedLib(item));
            if (dylibFiles.length > 0) {
              console.log(`Found shared libraries in ${libPath}: ${dylibFiles}`);
              const targetLibDir = path.join(pythonDir, 'lib');
              fs.mkdirSync(targetLibDir, { recursive: true });
              
              dylibFiles.forEach(dylib => {
                console.log(`Copying ${dylib} from ${libPath}`);
                fs.copyFileSync(path.join(libPath, dylib), path.join(targetLibDir, dylib));
              });
              break;
            }
          } catch (e) {
            console.log(`Could not read ${libPath}: ${e.message}`);
          }
        }
      }
      }
    }

    // Create site-packages directory and copy from temp venv
    const pythonVersion = isPythonStandalone ? TARGET_PYTHON_MINOR : execSync(`"${pythonPath}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"`, { encoding: 'utf8' }).trim();
    const sitePackagesDir = path.join(pythonDir, 'lib', `python${pythonVersion}`, 'site-packages');
    fs.mkdirSync(sitePackagesDir, { recursive: true });

    console.log('Copying installed packages...');
    const tempSitePackages = path.join(tempVenvPath, 'lib', `python${pythonVersion}`, 'site-packages');
    if (fs.existsSync(tempSitePackages)) {
      fs.cpSync(tempSitePackages, sitePackagesDir, { recursive: true });
    }

    // Copy HDF5, NetCDF, and other scientific computing shared libraries
    console.log('Copying HDF5 and NetCDF shared libraries from temp venv...');
    
    // Check for shared libraries in site-packages subdirectories (common in wheels)
    const packagesToCheck = ['netCDF4', 'h5py', 'h5netcdf'];
    let sharedLibsCopied = 0;
    
    for (const pkg of packagesToCheck) {
      for (const dirName of SHARED_LIB_DIR_NAMES) {
        const pkgLibDir = path.join(tempSitePackages, pkg, dirName);
        if (fs.existsSync(pkgLibDir)) {
          const sharedLibFiles = fs.readdirSync(pkgLibDir).filter(isSharedLib);
          sharedLibFiles.forEach(sharedLib => {
            const src = path.join(pkgLibDir, sharedLib);
            const dest = path.join(pythonDir, 'lib', sharedLib);
            if (!fs.existsSync(dest)) {
              fs.copyFileSync(src, dest);
              console.log(`  Copied ${sharedLib} from ${pkg}/${dirName}`);
              sharedLibsCopied++;
            }
          });
        }
      }
    }
    
    // Also check venv lib directory for any HDF5/NetCDF shared libraries
    const venvLibDir = path.join(tempVenvPath, 'lib');
    if (fs.existsSync(venvLibDir)) {
      const sharedFiles = fs.readdirSync(venvLibDir)
        .filter(f => isSharedLib(f) && (f.includes('hdf5') || f.includes('netcdf') || f.includes('sz') || f.includes('z')));
      sharedFiles.forEach(sharedLib => {
        const src = path.join(venvLibDir, sharedLib);
        const dest = path.join(pythonDir, 'lib', sharedLib);
        if (!fs.existsSync(dest)) {
          fs.copyFileSync(src, dest);
          console.log(`  Copied ${sharedLib} from venv lib`);
          sharedLibsCopied++;
        }
      });
    }
    
    console.log(`Total HDF5/NetCDF shared libraries copied: ${sharedLibsCopied}`);

    // Test the portable Python installation
    console.log('Testing portable Python installation...');
    const portablePython = path.join(pythonDir, 'bin', 'python');

    // Set up environment for testing
    const testEnv = {
      ...process.env,
      PYTHONIOENCODING: 'utf-8',
      PYTHONHOME: isPythonStandalone ? pythonDir : undefined,
      PYTHONPATH: sitePackagesDir
    };
    const sharedLibEnvKey = IS_MAC ? 'DYLD_LIBRARY_PATH' : IS_LINUX ? 'LD_LIBRARY_PATH' : undefined;
    if (sharedLibEnvKey) {
      const existingValue = process.env[sharedLibEnvKey] || '';
      const prefix = path.join(pythonDir, 'lib');
      testEnv[sharedLibEnvKey] = [prefix, existingValue].filter(Boolean).join(':');
    }

    execSync(`"${portablePython}" --version`, {
      cwd: buildDir,
      stdio: 'inherit',
      env: testEnv
    });

    execSync(`"${portablePython}" -c "import sys; print('Python path:', sys.executable); print('Python home:', sys.prefix)"`, {
      cwd: buildDir,
      stdio: 'inherit',
      env: testEnv
    });

    execSync(`"${portablePython}" -c "import django; print('Django version:', django.VERSION)"`, {
      cwd: buildDir,
      stdio: 'inherit',
      env: testEnv
    });

    // Final verification of NetCDF4 and HDF5 support
    console.log('Final verification of NetCDF4 and HDF5 support...');
    const sharedLibPatternForVerify = IS_MAC ? '*.dylib' : '*.so*';
    const sharedLibLabelForVerify = IS_MAC ? 'dylibs' : 'shared libs';
    const finalVerifyScript = String.raw`
import sys
import os
print("Python executable:", sys.executable)

# Test imports and functionality
try:
    import netCDF4
    print(f"[OK] netcdf4 {netCDF4.__version__} working")
    # Test creating a file
    nc = netCDF4.Dataset("test.nc", "w", format="NETCDF4")
    nc.close()
    os.remove("test.nc")
    print("  [OK] Can create NetCDF4 files")
except Exception as e:
    print(f"[ERROR] netcdf4 error: {e}")

try:
    import h5netcdf
    import h5netcdf.legacyapi
    print(f"[OK] h5netcdf {h5netcdf.__version__} working")
except Exception as e:
    print(f"[ERROR] h5netcdf error: {e}")

try:
    import h5py
    print(f"[OK] h5py {h5py.__version__} working")
    # Test creating HDF5 file
    with h5py.File("test.h5", "w") as f:
        f.create_dataset("test", data=[1,2,3])
    os.remove("test.h5")
    print("  [OK] Can create HDF5 files")
except Exception as e:
    print(f"[ERROR] h5py error: {e}")

# Check for shared libraries packaged with Python (${platformLabel})
import glob
python_dir = os.path.dirname(os.path.dirname(sys.executable))
shared_patterns = [os.path.join(python_dir, "lib", "${sharedLibPatternForVerify}")]
hdf5_libs = []
netcdf_libs = []
for pattern in shared_patterns:
    for shared_lib in glob.glob(pattern):
        shared_name = os.path.basename(shared_lib).lower()
        if 'hdf5' in shared_name:
            hdf5_libs.append(shared_name)
        elif 'netcdf' in shared_name:
            netcdf_libs.append(shared_name)

print(f"\\nFound {len(hdf5_libs)} HDF5 ${sharedLibLabelForVerify}: {', '.join(hdf5_libs[:3])}{'...' if len(hdf5_libs) > 3 else ''}")
print(f"Found {len(netcdf_libs)} NetCDF ${sharedLibLabelForVerify}: {', '.join(netcdf_libs[:3])}{'...' if len(netcdf_libs) > 3 else ''}")
`;
    
    try {
      execSync(`"${portablePython}" -c "${finalVerifyScript}"`, {
        cwd: buildDir,
        stdio: 'inherit',
        env: testEnv
      });
      console.log('[OK] NetCDF4 and HDF5 support verified successfully');
    } catch (verifyError) {
      console.error('WARNING: NetCDF4/HDF5 verification failed, but continuing build...');
      console.error(verifyError.message);
    }

    // Clean up temp venv with robust error handling
    console.log('Cleaning up temporary venv...');
    if (fs.existsSync(tempVenvPath)) {
      if (process.env.KEEP_TEMP_VENV === '1') {
        console.log('[DEBUG] KEEP_TEMP_VENV=1 set — leaving temp-venv in place for inspection');
      } else {
        try {
          fs.rmSync(tempVenvPath, { recursive: true, force: true });
          console.log('[OK] Temp venv cleaned up successfully');
        } catch (error) {
          console.error('ERROR: Failed to clean up temp-venv:', error);
          // Force cleanup on macOS/Linux
          try {
            execSync(`rm -rf "${tempVenvPath}"`, { stdio: 'inherit' });
          } catch (cmdError) {
            console.error('Failed to remove via rm -rf:', cmdError);
          }
        }
      }
    }

    // Verify cleanup
    if (fs.existsSync(tempVenvPath)) {
      throw new Error('CRITICAL: temp-venv still exists after cleanup!');
    }

    console.log('[OK] Portable Python environment created successfully for macOS');

    // Clean up any .venv if it exists (from previous/failed builds)
    const venvPath = path.join(buildDir, '.venv');
    if (fs.existsSync(venvPath)) {
      console.log('Removing .venv directory (not needed for distribution)...');
      try {
        fs.rmSync(venvPath, { recursive: true, force: true });
        console.log('[OK] .venv directory removed');
      } catch (error) {
        console.error('Warning: Failed to remove .venv:', error.message);
      }
    }

  } catch (error) {
    console.error('Failed to create portable Python environment for macOS:', error.message);
    const allowMacVenvFallback = process.env.ALLOW_MAC_VENV_FALLBACK === '1';
    
    // Clean up temp-venv if it exists before falling back
    if (fs.existsSync(tempVenvPath)) {
      if (process.env.KEEP_TEMP_VENV === '1') {
        console.log('[DEBUG] KEEP_TEMP_VENV=1 set — keeping failed temp-venv for debugging');
      } else {
        console.log('Cleaning up failed temp-venv...');
        try {
          fs.rmSync(tempVenvPath, { recursive: true, force: true });
          console.log('[OK] Temp venv cleaned up');
        } catch (cleanupError) {
          console.error('Failed to clean up temp-venv:', cleanupError.message);
          try {
            execSync(`rm -rf "${tempVenvPath}"`, { stdio: 'inherit' });
          } catch (cmdError) {
            console.error('Failed to remove via rm -rf:', cmdError);
          }
        }
      }
    }

    if (!allowMacVenvFallback) {
      throw new Error('Portable Python setup failed; refusing to ship fallback .venv (set ALLOW_MAC_VENV_FALLBACK=1 to override)');
    }

    if (isPythonStandalone) {
      throw error;
    }

    // Only fall back to venv if explicitly allowed (macOS only)
    if (IS_MAC && !isPythonStandalone) {
      console.warn('FALLBACK ENABLED: using relocatable venv for macOS bundle...');

      // Fallback to the relocatable venv approach
      const venvPath = path.join(buildDir, '.venv');
      try {
        execSync(`uv venv "${venvPath}" --python ${TARGET_PYTHON_VERSION} --relocatable`, {
          cwd: buildDir,
          stdio: 'inherit'
        });

        execSync(`uv pip install --python "${venvPath}" -e "${projectRoot}"`, {
          cwd: buildDir,
          stdio: 'inherit'
        });

        execSync(`uv pip install --python "${venvPath}" django-debug-toolbar pytest pytest-django pytest-mock mypy django-environ whitenoise`, {
          cwd: buildDir,
          stdio: 'inherit'
        });

        // Explicitly ensure netcdf4, h5netcdf, h5py are installed
        console.log('Ensuring NetCDF4, HDF5 support in venv...');
        execSync(`uv pip install --python "${venvPath}" --force-reinstall netcdf4 h5netcdf h5py`, {
          cwd: buildDir,
          stdio: 'inherit'
        });

        // Copy dylibs from .venv to a standard location
        console.log('Copying HDF5 and NetCDF dylibs from venv packages...');
        const venvLibDir = path.join(buildDir, 'lib');
        fs.mkdirSync(venvLibDir, { recursive: true });
        
        const venvSitePackages = path.join(venvPath, 'lib', `python${execSync(`"${venvPath}/bin/python" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"`, { encoding: 'utf8' }).trim()}`, 'site-packages');
        const packagesToCheck = ['netCDF4', 'h5py', 'h5netcdf'];
        let dylibsCopied = 0;
        
        for (const pkg of packagesToCheck) {
          const pkgDylibDir = path.join(venvSitePackages, pkg, '.dylibs');
          if (fs.existsSync(pkgDylibDir)) {
            const dylibFiles = fs.readdirSync(pkgDylibDir).filter(f => f.endsWith('.dylib'));
            dylibFiles.forEach(dylib => {
              const src = path.join(pkgDylibDir, dylib);
              const dest = path.join(venvLibDir, dylib);
              if (!fs.existsSync(dest)) {
                fs.copyFileSync(src, dest);
                console.log(`  Copied ${dylib} from ${pkg}/.dylibs`);
                dylibsCopied++;
              }
            });
          }
        }
        
        console.log(`Total dylibs copied: ${dylibsCopied}`);
        console.log('[OK] Fallback virtual environment created for macOS');
      } catch (fallbackError) {
        console.error('Fallback also failed:', fallbackError.message);
        throw fallbackError;
      }
    }
  }
} else {
  console.log('Non-Windows platform detected, using standard virtual environment...');

  // For non-Windows, use the standard relocatable venv approach
  const venvPath = path.join(buildDir, '.venv');
  try {
    execSync(`uv venv "${venvPath}" --python ${TARGET_PYTHON_VERSION} --relocatable`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    execSync(`uv pip install --python "${venvPath}" -e "${projectRoot}"`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    execSync(`uv pip install --python "${venvPath}" django-debug-toolbar pytest pytest-django pytest-mock mypy`, {
      cwd: buildDir,
      stdio: 'inherit'
    });

    console.log('[OK] Virtual environment created successfully');
  } catch (error) {
    console.error('Failed to create virtual environment:', error.message);
  }
}

// Create wrapper scripts for Django execution
if (process.platform === 'win32') {
  console.log('Creating Django wrapper scripts...');

  // Check if we have portable Python or venv
  const portablePython = path.join(buildDir, 'python', 'Scripts', 'python.exe');
  const venvPython = path.join(buildDir, '.venv', 'Scripts', 'python.exe');

  let pythonCommand;
  if (fs.existsSync(portablePython)) {
    pythonCommand = '%BUNDLE_DIR%python\\Scripts\\python.exe';
  } else if (fs.existsSync(venvPython)) {
    pythonCommand = '%BUNDLE_DIR%.venv\\Scripts\\python.exe';
  } else {
    pythonCommand = 'python'; // fallback to system Python
  }

  const wrapperScript = `@echo off
setlocal EnableDelayedExpansion

REM Get the directory where this script is located
set "BUNDLE_DIR=%~dp0"
set "DJANGO_DIR=%BUNDLE_DIR%django"
set "PYTHON_EXE=${pythonCommand}"

REM For now, hardcode Python ${TARGET_PYTHON_MINOR} since dynamic detection has issues in batch files
set "PYTHON_VERSION=${TARGET_PYTHON_MINOR}"

REM Set up the site-packages path - use standard Python structure
set "SITE_PACKAGES=%BUNDLE_DIR%python\lib\python%PYTHON_VERSION%\site-packages"

REM Set Python path to include our modules in the correct order
set "PYTHONPATH=%SITE_PACKAGES%;%BUNDLE_DIR%python\Lib;%DJANGO_DIR%;%BUNDLE_DIR%;%BUNDLE_DIR%steeloweb"

REM Set Django settings module
set "DJANGO_SETTINGS_MODULE=config.settings.local"

REM Add Python Scripts and Library/bin to PATH for DLL loading
set "PATH=%BUNDLE_DIR%python\Scripts;%BUNDLE_DIR%python\Library\bin;%BUNDLE_DIR%python\DLLs;%PATH%"

REM Change to Django directory  
cd /d "%DJANGO_DIR%"

REM Run manage.py with the bundled Python
"%PYTHON_EXE%" manage.py %*
`;

  fs.writeFileSync(path.join(buildDir, 'manage.bat'), wrapperScript);

  // Also create a general Python runner script
  const pythonRunnerScript = `@echo off
setlocal EnableDelayedExpansion

REM Get the directory where this script is located
set "BUNDLE_DIR=%~dp0"
set "PYTHON_EXE=${pythonCommand}"

REM For now, hardcode Python ${TARGET_PYTHON_MINOR} since dynamic detection has issues in batch files
set "PYTHON_VERSION=${TARGET_PYTHON_MINOR}"

REM Set up the site-packages path - use standard Python structure
set "SITE_PACKAGES=%BUNDLE_DIR%python\lib\python%PYTHON_VERSION%\site-packages"

REM Set Python path to include our modules in the correct order
set "PYTHONPATH=%SITE_PACKAGES%;%BUNDLE_DIR%python\Lib;%BUNDLE_DIR%django;%BUNDLE_DIR%;%BUNDLE_DIR%steeloweb"

REM Add Python Scripts and Library/bin to PATH for DLL loading
set "PATH=%BUNDLE_DIR%python\Scripts;%BUNDLE_DIR%python\Library\bin;%BUNDLE_DIR%python\DLLs;%PATH%"

REM Run Python with the bundled interpreter
"%PYTHON_EXE%" %*
`;

  fs.writeFileSync(path.join(buildDir, 'python.bat'), pythonRunnerScript);

  console.log('[OK] Django wrapper scripts created');
} else if (IS_MAC) {
  console.log('Creating Django wrapper scripts for macOS...');

  // Check if we have portable Python or venv
  const portablePython = path.join(buildDir, 'python', 'bin', 'python');
  const venvPython = path.join(buildDir, '.venv', 'bin', 'python');

  let pythonCommand;
  if (fs.existsSync(portablePython)) {
    pythonCommand = '"$BUNDLE_DIR/python/bin/python"';
  } else if (fs.existsSync(venvPython)) {
    pythonCommand = '"$BUNDLE_DIR/.venv/bin/python"';
  } else {
    pythonCommand = 'python3'; // fallback to system Python
  }

  const wrapperScript = `#!/bin/bash

# Get the directory where this script is located
BUNDLE_DIR="$(cd "$(dirname "\${BASH_SOURCE[0]}")" && pwd)/"
DJANGO_DIR="\$BUNDLE_DIR/django"

# Set Python path to include our modules
export PYTHONPATH="\$BUNDLE_DIR:\$BUNDLE_DIR/steeloweb:\$DJANGO_DIR"

# Set library path for dylibs (HDF5, NetCDF)
# Check if we have portable python or venv
if [ -d "\$BUNDLE_DIR/python/lib" ]; then
  export DYLD_LIBRARY_PATH="\$BUNDLE_DIR/python/lib:\$DYLD_LIBRARY_PATH"
elif [ -d "\$BUNDLE_DIR/lib" ]; then
  # Fallback: dylibs copied to bundle/lib when using .venv
  export DYLD_LIBRARY_PATH="\$BUNDLE_DIR/lib:\$DYLD_LIBRARY_PATH"
fi

# Change to Django directory  
cd "\$DJANGO_DIR"

# Run manage.py with the bundled Python
${pythonCommand} manage.py "\$@"
`;

  fs.writeFileSync(path.join(buildDir, 'manage.sh'), wrapperScript);

  // Make it executable
  execSync(`chmod +x "${path.join(buildDir, 'manage.sh')}"`, { stdio: 'inherit' });

  // Also create a general Python runner script
  const pythonRunnerScript = `#!/bin/bash

# Get the directory where this script is located
BUNDLE_DIR="$(cd "$(dirname "\${BASH_SOURCE[0]}")" && pwd)/"

# Set Python path to include our modules
export PYTHONPATH="\$BUNDLE_DIR:\$BUNDLE_DIR/steeloweb:\$BUNDLE_DIR/django"

# Set library path for dylibs (HDF5, NetCDF)
# Check if we have portable python or venv
if [ -d "\$BUNDLE_DIR/python/lib" ]; then
  export DYLD_LIBRARY_PATH="\$BUNDLE_DIR/python/lib:\$DYLD_LIBRARY_PATH"
elif [ -d "\$BUNDLE_DIR/lib" ]; then
  # Fallback: dylibs copied to bundle/lib when using .venv
  export DYLD_LIBRARY_PATH="\$BUNDLE_DIR/lib:\$DYLD_LIBRARY_PATH"
fi

# Run Python with the bundled interpreter
${pythonCommand} "\$@"
`;

  fs.writeFileSync(path.join(buildDir, 'python.sh'), pythonRunnerScript);

  // Make it executable
  execSync(`chmod +x "${path.join(buildDir, 'python.sh')}"`, { stdio: 'inherit' });

  console.log('[OK] Django wrapper scripts created for macOS');
} else if (IS_LINUX) {
  console.log('Creating Django wrapper scripts for Linux...');

  const portablePython = path.join(buildDir, 'python', 'bin', 'python');
  const venvPython = path.join(buildDir, '.venv', 'bin', 'python');

  let pythonCommand;
  if (fs.existsSync(portablePython)) {
    pythonCommand = '"$BUNDLE_DIR/python/bin/python"';
  } else if (fs.existsSync(venvPython)) {
    pythonCommand = '"$BUNDLE_DIR/.venv/bin/python"';
  } else {
    pythonCommand = 'python3';
  }

  const wrapperScript = `#!/bin/bash

# Get the directory where this script is located
BUNDLE_DIR="$(cd "$(dirname "\${BASH_SOURCE[0]}")" && pwd)/"
DJANGO_DIR="\$BUNDLE_DIR/django"

# Set Python path to include our modules
export PYTHONPATH="\$BUNDLE_DIR:\$BUNDLE_DIR/steeloweb:\$DJANGO_DIR"

# Ensure bundled shared libraries are discoverable
if [ -d "\$BUNDLE_DIR/python/lib" ]; then
  export LD_LIBRARY_PATH="\$BUNDLE_DIR/python/lib:\${LD_LIBRARY_PATH:-}"
elif [ -d "\$BUNDLE_DIR/lib" ]; then
  export LD_LIBRARY_PATH="\$BUNDLE_DIR/lib:\${LD_LIBRARY_PATH:-}"
fi

# Change to Django directory
cd "\$DJANGO_DIR"

# Run manage.py with the bundled Python
${pythonCommand} manage.py "\$@"
`;

  fs.writeFileSync(path.join(buildDir, 'manage.sh'), wrapperScript);
  execSync(`chmod +x "${path.join(buildDir, 'manage.sh')}"`, { stdio: 'inherit' });

  const pythonRunnerScript = `#!/bin/bash

# Get the directory where this script is located
BUNDLE_DIR="$(cd "$(dirname "\${BASH_SOURCE[0]}")" && pwd)/"

# Set Python path to include our modules
export PYTHONPATH="\$BUNDLE_DIR:\$BUNDLE_DIR/steeloweb:\$BUNDLE_DIR/django"

# Ensure bundled shared libraries are discoverable
if [ -d "\$BUNDLE_DIR/python/lib" ]; then
  export LD_LIBRARY_PATH="\$BUNDLE_DIR/python/lib:\${LD_LIBRARY_PATH:-}"
elif [ -d "\$BUNDLE_DIR/lib" ]; then
  export LD_LIBRARY_PATH="\$BUNDLE_DIR/lib:\${LD_LIBRARY_PATH:-}"
fi

# Run Python with the bundled interpreter
${pythonCommand} "\$@"
`;

  fs.writeFileSync(path.join(buildDir, 'python.sh'), pythonRunnerScript);
  execSync(`chmod +x "${path.join(buildDir, 'python.sh')}"`, { stdio: 'inherit' });

  console.log('[OK] Django wrapper scripts created for Linux');
}

// Generate Sentry configuration file if DSNs are provided
if (process.env.SENTRY_DSN_ELECTRON || process.env.SENTRY_DSN_DJANGO) {
  console.log('Creating Sentry configuration...');
  
  const sentryConfig = {
    electron: process.env.SENTRY_DSN_ELECTRON || '',
    django: process.env.SENTRY_DSN_DJANGO || '',
    environment: process.env.BUILD_ENV || 'production',
    enabled: !!(process.env.SENTRY_DSN_ELECTRON || process.env.SENTRY_DSN_DJANGO)
  };

  fs.writeFileSync(
    path.join(buildDir, 'sentry-config.json'),
    JSON.stringify(sentryConfig, null, 2)
  );
  
  console.log('[OK] Sentry configuration file created');
} else {
  console.log('[INFO] No Sentry DSN provided, skipping error reporting setup');
}

console.log('Django bundle created at:', buildDir);

// Collect static files for offline capability
console.log('\n=== Collecting static files ===');
const managePy = path.join(buildDir, 'django', 'manage.py');

// Determine which Python to use
let pythonExe;
if (process.platform === 'win32') {
  const portablePython = path.join(buildDir, 'python', 'Scripts', 'python.exe');
  const venvPython = path.join(buildDir, '.venv', 'Scripts', 'python.exe');

  if (fs.existsSync(portablePython)) {
    pythonExe = portablePython;
  } else if (fs.existsSync(venvPython)) {
    pythonExe = venvPython;
  } else {
    console.error('❌ CRITICAL: No Python executable found for collectstatic');
    throw new Error('Cannot run collectstatic without Python');
  }
} else {
  const portablePython = path.join(buildDir, 'python', 'bin', 'python');
  const venvPython = path.join(buildDir, '.venv', 'bin', 'python');

  if (fs.existsSync(portablePython)) {
    pythonExe = portablePython;
  } else if (fs.existsSync(venvPython)) {
    pythonExe = venvPython;
  } else {
    console.error('❌ CRITICAL: No Python executable found for collectstatic');
    throw new Error('Cannot run collectstatic without Python');
  }
}

try {
  console.log(`Running collectstatic with ${pythonExe}...`);

  // Set up environment for collectstatic
  const collectEnv = {
    ...process.env,
    DJANGO_SETTINGS_MODULE: 'config.settings.production',
    // collectstatic doesn't need a real secret key - only used for crypto operations at runtime
    DJANGO_SECRET_KEY: process.env.DJANGO_SECRET_KEY || 'build-time-placeholder-key-not-used-for-crypto-operations',
    // collectstatic doesn't use cache - placeholder path inside build dir (cross-platform)
    DJANGO_CACHE_LOCATION: process.env.DJANGO_CACHE_LOCATION || path.join(buildDir, '.tmp', 'django-cache'),
    PYTHONIOENCODING: 'utf-8'
  };

  // Add Python paths for Windows
  if (IS_WIN) {
    collectEnv.PYTHONPATH = [
      path.join(buildDir, 'python', 'Lib', 'site-packages'),
      path.join(buildDir, 'python', 'Lib'),
      path.join(buildDir, 'django'),
      path.join(buildDir),
      path.join(buildDir, 'steeloweb')
    ].join(';');
    collectEnv.PATH = `${path.join(buildDir, 'python', 'Scripts')};${path.join(buildDir, 'python', 'Library', 'bin')};${process.env.PATH}`;
  } else {
    // macOS/Linux
    const pythonVersion = TARGET_PYTHON_MINOR; // Match bundled Python version
    collectEnv.PYTHONPATH = [
      path.join(buildDir, 'python', 'lib', `python${pythonVersion}`, 'site-packages'),
      path.join(buildDir, 'django'),
      path.join(buildDir),
      path.join(buildDir, 'steeloweb')
    ].join(':');

    const libDir = path.join(buildDir, 'python', 'lib');
    const libEnvKey = IS_MAC ? 'DYLD_LIBRARY_PATH' : IS_LINUX ? 'LD_LIBRARY_PATH' : undefined;
    if (libEnvKey && fs.existsSync(libDir)) {
      collectEnv[libEnvKey] = `${libDir}:${process.env[libEnvKey] || ''}`;
    }
  }

  execSync(`"${pythonExe}" "${managePy}" collectstatic --noinput --clear`, {
    cwd: buildDir,
    stdio: 'inherit',
    env: collectEnv
  });

  console.log('✅ Static files collected successfully');

  // Verify vendor files were collected
  // Static files are collected to django/staticfiles (STATIC_ROOT in Django settings)
  const staticfilesDir = path.join(buildDir, 'django', 'staticfiles', 'vendor');
  if (fs.existsSync(staticfilesDir)) {
    console.log('[OK] Vendor static files directory exists');

    // Check for key vendor files
    const keyFiles = [
      'bootstrap-5.3.0/css/bootstrap.min.css',
      'fontawesome-6.0.0/css/all.min.css',
      'highlightjs-11.9.0/highlight.min.js',
      'mapping-libs/deck.gl@8.9.35/dist.min.js'
    ];

    let allFilesPresent = true;
    for (const file of keyFiles) {
      const filePath = path.join(staticfilesDir, file);
      if (fs.existsSync(filePath)) {
        console.log(`  ✓ ${file}`);
      } else {
        console.error(`  ✗ ${file} MISSING`);
        allFilesPresent = false;
      }
    }

    if (!allFilesPresent) {
      throw new Error('Some vendor static files are missing after collectstatic');
    }

    // Verify Mapbox GL is NOT bundled (should stay on CDN)
    const mapboxPath = path.join(staticfilesDir, 'mapbox-gl');
    if (fs.existsSync(mapboxPath)) {
      console.error('❌ ERROR: Mapbox GL found in vendor directory!');
      console.error('Mapbox GL must stay on CDN due to licensing.');
      throw new Error('Mapbox GL incorrectly bundled');
    }
    console.log('  ✓ Mapbox GL correctly NOT bundled (stays on CDN)');

  } else {
    console.error('❌ ERROR: Static files vendor directory not found!');
    throw new Error('Vendor static files were not collected');
  }

} catch (error) {
  console.error('❌ CRITICAL: Failed to collect static files:', error.message);
  console.error('Build cannot continue - offline mode requires all static assets');
  throw error;  // FAIL the build - don't ship without vendor assets
}

// Final build verification and size reporting
console.log('\n=== Final build verification ===');

// Check if we're using portable Python or venv
const hasPortablePython = fs.existsSync(path.join(buildDir, 'python'));
const hasVenv = fs.existsSync(path.join(buildDir, '.venv'));

let hasIssues = false;

// temp-venv should NEVER exist
const tempVenvPath = path.join(buildDir, 'temp-venv');
if (fs.existsSync(tempVenvPath)) {
  console.error(`❌ ERROR: temp-venv should not exist in final bundle!`);
  hasIssues = true;
  try {
    fs.rmSync(tempVenvPath, { recursive: true, force: true });
    console.log(`  [OK] Removed temp-venv`);
  } catch (e) {
    console.error(`  [FAIL] Failed to remove temp-venv:`, e.message);
  }
}

// .venv is only a problem if we also have a portable python directory
if (hasVenv && hasPortablePython) {
  console.warn(`⚠️  WARNING: Both .venv and portable python exist! This will bloat the bundle.`);
  // Remove .venv since we have portable Python
  try {
    fs.rmSync(path.join(buildDir, '.venv'), { recursive: true, force: true });
    console.log(`  [OK] Removed .venv (using portable Python instead)`);
    // Successfully cleaned up, so no issues
  } catch (e) {
    console.error(`  [ERROR] Failed to remove .venv:`, e.message);
    hasIssues = true;
  }
} else if (hasVenv && !hasPortablePython) {
  console.log('[INFO] Using .venv as the Python environment (portable Python creation failed)');
} else if (hasPortablePython && !hasVenv) {
  console.log('[OK] Using portable Python environment');
} else {
  console.warn('[WARNING] No Python environment found in bundle!');
  hasIssues = true;
}

// Helper function to calculate directory size
const getDirSize = (dirPath) => {
  let size = 0;
  if (fs.existsSync(dirPath)) {
    const files = fs.readdirSync(dirPath);
    for (const file of files) {
      const filePath = path.join(dirPath, file);
      try {
        const stat = fs.statSync(filePath);
        if (stat.isDirectory()) {
          size += getDirSize(filePath);
        } else {
          size += stat.size;
        }
      } catch (e) {
        // Ignore errors for files we can't access
      }
    }
  }
  return size;
};

// Report bundle size
const bundleSize = getDirSize(buildDir);
const bundleSizeMB = (bundleSize / 1024 / 1024).toFixed(2);
console.log(`\n📦 Django bundle size: ${bundleSizeMB} MB`);

// Report sizes of key directories
const keyDirs = ['python', '.venv', 'django', 'steeloweb', 'steelo'];
console.log('\nDirectory sizes:');
for (const dir of keyDirs) {
  const dirPath = path.join(buildDir, dir);
  if (fs.existsSync(dirPath)) {
    const dirSize = getDirSize(dirPath);
    const dirSizeMB = (dirSize / 1024 / 1024).toFixed(2);
    console.log(`  ${dir}: ${dirSizeMB} MB`);
  }
}

// Check Python directory structure
if (hasPortablePython) {
  const pythonDir = path.join(buildDir, 'python');
  const pythonSubDirs = ['Lib', 'Scripts', 'DLLs', 'lib'];
  console.log('\nPython subdirectory sizes:');
  for (const subDir of pythonSubDirs) {
    const subDirPath = path.join(pythonDir, subDir);
    if (fs.existsSync(subDirPath)) {
      const subDirSize = getDirSize(subDirPath);
      const subDirSizeMB = (subDirSize / 1024 / 1024).toFixed(2);
      console.log(`  python/${subDir}: ${subDirSizeMB} MB`);
    }
  }
} else if (hasVenv) {
  const venvDir = path.join(buildDir, '.venv');
  const venvSubDirs = process.platform === 'win32' ? ['Lib', 'Scripts'] : ['lib', 'bin'];
  console.log('\nVenv subdirectory sizes:');
  for (const subDir of venvSubDirs) {
    const subDirPath = path.join(venvDir, subDir);
    if (fs.existsSync(subDirPath)) {
      const subDirSize = getDirSize(subDirPath);
      const subDirSizeMB = (subDirSize / 1024 / 1024).toFixed(2);
      console.log(`  .venv/${subDir}: ${subDirSizeMB} MB`);
    }
  }
}

// Warn if bundle is too large or too small
if (bundleSize > 1000 * 1024 * 1024) { // 1 GB
  console.warn(`\n⚠️  WARNING: Bundle size exceeds 1 GB (${bundleSizeMB} MB)!`);
  console.warn('This may indicate temporary directories were not properly cleaned up.');
} else if (bundleSize < 100 * 1024 * 1024) { // 100 MB
  console.warn(`\n⚠️  WARNING: Bundle size is suspiciously small (${bundleSizeMB} MB)!`);
  console.warn('This may indicate Python packages were not properly included.');
  if (!hasPortablePython && !hasVenv) {
    console.error('❌ No Python environment found!');
    hasIssues = true;
  }
}

if (hasIssues) {
  console.error('\n❌ Build completed with issues');
  process.exit(1); // Exit with error code to fail the build
} else {
  console.log('\n✅ Build verification passed');
}
