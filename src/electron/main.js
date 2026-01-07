import { app, BrowserWindow, ipcMain, shell, Menu, dialog } from 'electron';
import { spawn, execSync } from 'child_process';
import path from 'path';
import { pathToFileURL, fileURLToPath } from 'url';
import fs from 'fs';
import http from 'http';
import { createRequire } from 'module';
import Store from 'electron-store';

// ESM doesn't have __dirname, so we need to create it
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Create require function for dynamic imports
const require = createRequire(import.meta.url);

const store = new Store({ name: 'gpu-health' });

const isDev = !app.isPackaged;
const crashes = store.get('consecutiveCrashes', 0);

// Disable after 2 bad launches on Windows; otherwise keep GPU on
if (process.platform === 'win32' && !isDev && crashes >= 2) {
  app.disableHardwareAcceleration();
  process.env.STEELO_GPU_DISABLED = 'auto-after-crashes';
  console.log('[INFO] HW acceleration disabled after repeated crashes');
}

// Count GPU-process deaths
app.on('child-process-gone', (_e, details) => {
  if (details?.type === 'GPU') {
    const currentCrashes = store.get('consecutiveCrashes', 0);
    store.set('consecutiveCrashes', Math.min(5, currentCrashes + 1));
    console.log(`[WARN] GPU process died, consecutive crashes: ${currentCrashes + 1}`);
  }
});
let djangoProcess = null;
let taskWorkerProcess = null;
let pythonVersion = null;  // Cache Python version
let currentDjangoBundlePath = null;  // Capture bundle path for shutdown
let shutdownInProgress = false;  // Prevent multiple shutdown attempts

// Wrap console methods to handle EIO errors in packaged apps
function safeConsole() {
  if (!isDev && app.isPackaged) {
    // Store original methods before any library can wrap them
    const originalWrite = process.stdout.write;
    const originalErrWrite = process.stderr.write;

    // Wrap stdout/stderr write methods to catch EIO errors at the source
    process.stdout.write = function(...args) {
      try {
        return originalWrite.apply(process.stdout, args);
      } catch (err) {
        if (err.code === 'EIO' || err.code === 'EPIPE' || err.message?.includes('write EIO')) {
          // Silently ignore I/O errors - these are expected when no terminal is attached
          return true;
        }
        throw err;
      }
    };

    process.stderr.write = function(...args) {
      try {
        return originalErrWrite.apply(process.stderr, args);
      } catch (err) {
        if (err.code === 'EIO' || err.code === 'EPIPE' || err.message?.includes('write EIO')) {
          // Silently ignore I/O errors
          return true;
        }
        throw err;
      }
    };

    // Also wrap console methods as a secondary defense
    const originalLog = console.log;
    const originalError = console.error;
    const originalWarn = console.warn;
    const originalInfo = console.info;

    const safeWrite = (original, ...args) => {
      try {
        original.apply(console, args);
      } catch (err) {
        // Silently ignore write errors in production
        // These happen when there's no stdout/stderr available
        if (err.code !== 'EIO' && err.code !== 'EPIPE' && !err.message?.includes('write EIO')) {
          // If it's not an expected I/O error, try to log it to a file
          try {
            // fs and path are already imported at top
            const logPath = path.join(app.getPath('userData'), 'electron-errors.log');
            fs.appendFileSync(logPath, `${new Date().toISOString()} Console error: ${err.message}\n`);
          } catch (e) {
            // Give up - can't log anywhere
          }
        }
      }
    };

    console.log = (...args) => safeWrite(originalLog, ...args);
    console.error = (...args) => safeWrite(originalError, ...args);
    console.warn = (...args) => safeWrite(originalWarn, ...args);
    console.info = (...args) => safeWrite(originalInfo, ...args);
  }
}

// Initialize safe console wrapper early before any libraries load
safeConsole();

// ============================================================================
// GLOBAL UNCAUGHT EXCEPTION HANDLER - Additional EIO error safety net
// ============================================================================
process.on('uncaughtException', (error) => {
  // Filter out EIO errors - these are expected when there's no terminal attached
  if (error.code === 'EIO' || error.code === 'EPIPE' ||
      error.message?.includes('write EIO')) {
    // Log to file instead of console to avoid recursive error
    try {
      const logPath = path.join(app.getPath('userData'), 'electron-errors.log');
      fs.appendFileSync(logPath, `${new Date().toISOString()} [Suppressed EIO]: ${error.message}\n`);
    } catch (e) {
      // Give up - can't log anywhere
    }
    return; // Don't crash the app
  }

  // For other errors, log and re-throw (will crash app, but that's intentional)
  console.error('[FATAL] Uncaught exception:', error);
  throw error;
});

// ============================================================================
// SINGLE INSTANCE LOCK - Prevent multiple instances from running
// ============================================================================
const gotTheLock = app.requestSingleInstanceLock();

if (!gotTheLock) {
  console.log('[Single Instance] Another instance is already running, exiting...');
  app.quit();
} else {
  // ============================================================================
  // MAIN APPLICATION INITIALIZATION
  // All code below only runs if we successfully acquired the single instance lock
  // ============================================================================

  // Handle second instance launch attempts
  app.on('second-instance', (event, commandLine, workingDirectory) => {
    console.log('[Single Instance] User tried to launch second instance - focusing existing window');

    // Get the existing window (if any)
    const windows = BrowserWindow.getAllWindows();
    if (windows.length > 0) {
      const mainWindow = windows[0];
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
      mainWindow.show();
    }
  });

// Initialize Sentry error reporting
function initializeSentry() {
  try {
    // Check for Sentry config file
    const sentryConfigPath = isDev
      ? null  // Don't use Sentry in development
      : path.join(process.resourcesPath, 'django-bundle', 'sentry-config.json');

    if (!isDev && sentryConfigPath && fs.existsSync(sentryConfigPath)) {
      const sentryConfig = JSON.parse(fs.readFileSync(sentryConfigPath, 'utf8'));

      if (sentryConfig.enabled && sentryConfig.electron) {
        // Check user consent
        const userDataPath = app.getPath('userData');
        const consentFile = path.join(userDataPath, 'error-reporting-consent.json');

        let hasConsent = false;
        if (fs.existsSync(consentFile)) {
          try {
            const consentData = JSON.parse(fs.readFileSync(consentFile, 'utf8'));
            hasConsent = consentData.enabled === true;
          } catch (e) {
            console.error('Failed to read consent file:', e);
          }
        }

        if (hasConsent) {
          // Dynamic import using require (created via createRequire)
          const Sentry = require('@sentry/electron/main');
          Sentry.init({
            dsn: sentryConfig.electron,
            environment: sentryConfig.environment || 'production',
            initialScope: {
              tags: {
                gpu_disabled: process.env.STEELO_GPU_DISABLED || 'no',
                consecutive_crashes: crashes
              }
            },
            beforeSend(event, hint) {
              // Filter out EIO errors from console writes
              if (hint && hint.originalException) {
                const error = hint.originalException;
                if (error.code === 'EIO' || error.code === 'EPIPE' ||
                    (error.message && error.message.includes('write EIO'))) {
                  // Don't send I/O errors to Sentry - these are expected in packaged apps
                  return null;
                }
              }

              // Sanitize sensitive data
              if (event.user) {
                delete event.user.email;
                delete event.user.ip_address;
              }

              // Remove user paths from stack traces
              if (event.exception && event.exception.values) {
                event.exception.values.forEach(exception => {
                  if (exception.stacktrace && exception.stacktrace.frames) {
                    exception.stacktrace.frames.forEach(frame => {
                      if (frame.filename) {
                        frame.filename = frame.filename
                          .replace(/\/Users\/[^\/]+/, '/HOME')
                          .replace(/\\Users\\[^\\]+/, '\\HOME');
                      }
                    });
                  }
                });
              }

              return event;
            },
            tracesSampleRate: 0.1,
            integrations: (integrations) => {
              // Filter out the Console integration to prevent console capture issues
              return integrations.filter(integration => {
                return integration.name !== 'Console';
              });
            }
          });

          console.log('[OK] Sentry error reporting initialized');
        } else {
          console.log('[INFO] Sentry disabled - user has not consented to error reporting');
        }
      }
    }
  } catch (error) {
    console.error('Failed to initialize Sentry:', error);
    // Don't throw - app should work even if Sentry fails
  }
}

// Initialize Sentry early in app lifecycle
initializeSentry();

// Setup IPC handler for renderer to get Sentry config
ipcMain.handle('get-sentry-config', async () => {
  try {
    if (isDev) return null;

    const configPath = path.join(process.resourcesPath, 'django-bundle', 'sentry-config.json');
    if (!fs.existsSync(configPath)) return null;

    const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));

    // Check consent
    const userDataPath = app.getPath('userData');
    const consentFile = path.join(userDataPath, 'error-reporting-consent.json');

    if (!fs.existsSync(consentFile)) return null;

    const consentData = JSON.parse(fs.readFileSync(consentFile, 'utf8'));
    if (!consentData.enabled) return null;

    return config;
  } catch (error) {
    console.error('Error getting Sentry config for renderer:', error);
    return null;
  }
});

// Setup IPC handler for opening log files in system file manager
ipcMain.handle('open-log-file', async (event, logFilePath) => {
  try {
    if (!logFilePath) {
      console.error('No log file path provided');
      return { success: false, error: 'No log file path provided' };
    }

    // Verify file exists before trying to open
    if (!fs.existsSync(logFilePath)) {
      console.error('Log file does not exist:', logFilePath);
      return { success: false, error: 'Log file does not exist' };
    }

    // Open the file using the system's default handler
    const result = await shell.openPath(logFilePath);

    if (result) {
      // openPath returns error string if failed, empty string if successful
      console.error('Failed to open log file:', result);
      return { success: false, error: result };
    }

    console.log('Opened log file:', logFilePath);
    return { success: true };
  } catch (error) {
    console.error('Error opening log file:', error);
    return { success: false, error: error.message };
  }
});

// Determine which Django settings to use
const DJANGO_SETTINGS_MODULE = isDev ? 'config.settings.local' : 'config.settings.standalone';

function getPythonVersion(pythonPath) {
  if (pythonVersion) return pythonVersion;  // Return cached version
  
  try {
    const { execSync } = require('child_process');
    pythonVersion = execSync(`"${pythonPath}" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"`, { encoding: 'utf8' }).trim();
    console.log(`Detected Python version: ${pythonVersion}`);
    return pythonVersion;
  } catch (error) {
    console.error('Failed to detect Python version, defaulting to 3.13:', error);
    pythonVersion = '3.13';  // Fallback
    return pythonVersion;
  }
}

function checkDjangoServer(url = 'http://127.0.0.1:8000/') {
  return new Promise((resolve) => {
    const request = http.get(url, (res) => {
      resolve(res.statusCode === 200);
      res.resume();
    });
    request.on('error', () => resolve(false));
    request.setTimeout(10000, () => {
      request.destroy();
      resolve(false);
    });
  });
}

function waitForHttp(url = 'http://127.0.0.1:8000/', timeout = 60000, interval = 500) {
  const start = Date.now();
  return new Promise((resolve, reject) => {
    function check() {
      http.get(url, res => {
        if (res.statusCode === 200) {
          console.log('[OK] Django server is responding');
          resolve();
        } else {
          retry();
        }
        res.resume();
      }).on('error', retry);
    }
    function retry() {
      if (Date.now() - start > timeout) {
        console.error(`Timeout waiting for Django server at ${url}`);
        reject(new Error('Timeout waiting for Django server'));
      } else {
        setTimeout(check, interval);
      }
    }
    check();
  });
}

function findDjangoBundlePath() {
  if (isDev) {
    // In development, look for django-bundle in the electron directory
    return path.join(__dirname, 'django-bundle');
  }

  // In production (after electron-builder packaging)
  const possiblePaths = [
    path.join(process.resourcesPath, 'django-bundle'),
    path.join(__dirname, '..', 'resources', 'django-bundle'),
    path.join(__dirname, 'resources', 'django-bundle'),
  ];

  for (const bundlePath of possiblePaths) {
    if (fs.existsSync(bundlePath)) {
      console.log('Found Django bundle at:', bundlePath);
      return bundlePath;
    }
  }

  throw new Error('Django bundle not found in any expected location');
}

function getPythonPath(bundlePath) {
  // Check if we have portable Python first
  if (process.platform === 'win32') {
    const portablePython = path.join(bundlePath, 'python', 'Scripts', 'python.exe');
    if (fs.existsSync(portablePython)) {
      return portablePython;
    }
  } else {
    // macOS/Linux portable Python
    const portablePython = path.join(bundlePath, 'python', 'bin', 'python');
    if (fs.existsSync(portablePython)) {
      return portablePython;
    }
  }

  // Fallback to virtual environment
  const venvPath = path.join(bundlePath, '.venv');
  if (process.platform === 'win32') {
    return path.join(venvPath, 'Scripts', 'python.exe');
  } else {
    return path.join(venvPath, 'bin', 'python');
  }
}

function getEnhancedPath(bundlePath) {
  // Add Scripts and Library/bin to PATH for DLL loading (Windows)
  if (process.platform === 'win32') {
    const pythonDir = path.join(bundlePath, 'python');
    const scriptsDir = path.join(pythonDir, 'Scripts');
    const libraryBinDir = path.join(pythonDir, 'Library', 'bin');
    const venvScriptsDir = path.join(bundlePath, '.venv', 'Scripts');
    
    // Check which directories exist and add them to PATH
    const extraPaths = [];
    if (fs.existsSync(scriptsDir)) extraPaths.push(scriptsDir);
    if (fs.existsSync(libraryBinDir)) extraPaths.push(libraryBinDir);
    if (fs.existsSync(venvScriptsDir)) extraPaths.push(venvScriptsDir);
    
    if (extraPaths.length > 0) {
      return extraPaths.join(';') + ';' + process.env.PATH;
    }
  }
  return process.env.PATH;
}

function getSSLCertPath(bundlePath) {
  // Find SSL certificate bundle for Python
  const possibleCertPaths = [
    path.join(bundlePath, 'python', 'Library', 'ssl', 'cacert.pem'),
    path.join(bundlePath, 'python', 'Library', 'ssl', 'cert.pem'),
    path.join(bundlePath, 'python', 'ssl', 'cacert.pem'),
    path.join(bundlePath, 'python', 'ssl', 'cert.pem'),
    path.join(bundlePath, 'python', 'Lib', 'site-packages', 'certifi', 'cacert.pem'),
    path.join(bundlePath, '.venv', 'Lib', 'site-packages', 'certifi', 'cacert.pem')
  ];
  
  for (const certPath of possibleCertPaths) {
    if (fs.existsSync(certPath)) {
      console.log('Found SSL certificates at:', certPath);
      return certPath;
    }
  }
  
  console.warn('No SSL certificate bundle found in expected locations');
  return null;
}

function getPythonEnv(djangoBundlePath, pythonPathEnv) {
  const sslCertPath = getSSLCertPath(djangoBundlePath);

  // Create logs directory for performance logging
  const logsDir = path.join(app.getPath('userData'), 'logs');
  if (!fs.existsSync(logsDir)) {
    fs.mkdirSync(logsDir, { recursive: true });
  }

  // Get app version for database isolation
  // Using require (created via createRequire) for JSON file
  const packageJson = require('./package.json');
  const appVersion = packageJson.version || '1.2.0';

  const env = {
    ...process.env,
    PYTHONUNBUFFERED: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    PYTHONIOENCODING: 'utf-8',
    PATH: getEnhancedPath(djangoBundlePath),
    // Set Django secret key for standalone builds
    DJANGO_SECRET_KEY: process.env.DJANGO_SECRET_KEY || 'standalone-electron-app-secret-key-' + Date.now(),
    // Pass Electron user data path for consent storage
    ELECTRON_USER_DATA: app.getPath('userData'),
    // Pass logs directory for performance logging
    STEELO_LOG_DIR: logsDir,
    // Mark as standalone mode for worker cleanup behavior
    STEELO_STANDALONE: '1',
    // Pass app version for database/storage isolation
    STEELO_APP_VERSION: appVersion
  };
  
  // Add PYTHONPATH if provided
  if (pythonPathEnv) {
    env.PYTHONPATH = pythonPathEnv;
  }
  
  // Check if we're using portable Python (python-build-standalone)
  const portablePythonPath = path.join(djangoBundlePath, 'python', 'bin', 'python');
  const isPortablePython = fs.existsSync(portablePythonPath);
  
  if (isPortablePython && process.platform === 'darwin') {
    // Set PYTHONHOME for portable Python to prevent it from looking for system Python
    env.PYTHONHOME = path.join(djangoBundlePath, 'python');
    console.log('Setting PYTHONHOME for portable Python:', env.PYTHONHOME);
  }
  
  // Add SSL certificate paths if found
  if (sslCertPath) {
    env.SSL_CERT_FILE = sslCertPath;
    env.REQUESTS_CA_BUNDLE = sslCertPath;
    env.CURL_CA_BUNDLE = sslCertPath;
  }
  
  // Set STEELO_HOME to a writable location for Windows standalone
  // Make it version-specific to isolate outputs between versions
  if (process.platform === 'win32' && !isDev) {
    // Use the user's AppData folder for Windows
    const appDataPath = process.env.APPDATA || path.join(process.env.USERPROFILE, 'AppData', 'Roaming');
    env.STEELO_HOME = path.join(appDataPath, 'SteelModel', `v${appVersion}`);
    console.log('Setting version-specific STEELO_HOME for Windows:', env.STEELO_HOME);
  }
  
  // Add DYLD_LIBRARY_PATH for macOS to find HDF5/NetCDF dylibs
  if (process.platform === 'darwin') {
    // Check for portable python lib dir first, then fallback lib dir
    const portableLibPath = path.join(djangoBundlePath, 'python', 'lib');
    const fallbackLibPath = path.join(djangoBundlePath, 'lib');
    
    let libPaths = [];
    if (fs.existsSync(portableLibPath)) {
      libPaths.push(portableLibPath);
    }
    if (fs.existsSync(fallbackLibPath)) {
      libPaths.push(fallbackLibPath);
    }
    
    if (libPaths.length > 0) {
      env.DYLD_LIBRARY_PATH = libPaths.join(':') + ':' + (env.DYLD_LIBRARY_PATH || '');
      console.log('Setting DYLD_LIBRARY_PATH for macOS:', env.DYLD_LIBRARY_PATH);
    }
  }
  
  return env;
}

function runMigrations(djangoBundlePath) {
  return new Promise((resolve, reject) => {
    const djangoPath = path.join(djangoBundlePath, 'django');
    const pythonPath = getPythonPath(djangoBundlePath);

    console.log('Running Django migrations...');
    console.log('Django path:', djangoPath);
    console.log('Python path:', pythonPath);
    console.log('Platform:', process.platform);
    console.log('Django settings:', DJANGO_SETTINGS_MODULE);

    // Check if Python executable exists
    if (!fs.existsSync(pythonPath)) {
      const error = `Python executable not found at: ${pythonPath}`;
      console.error(error);
      reject(new Error(error));
      return;
    }

    // Check if Django directory exists
    if (!fs.existsSync(djangoPath)) {
      const error = `Django directory not found at: ${djangoPath}`;
      console.error(error);
      reject(new Error(error));
      return;
    }

    // Set up PYTHONPATH with correct order to avoid name conflicts
    const pyVersion = getPythonVersion(pythonPath);
    const sitePackagesPath = path.join(djangoBundlePath, 'python', 'lib', `python${pyVersion}`, 'site-packages');
    const pythonPathEnv = [
      sitePackagesPath,  // Django package from site-packages
      djangoPath,        // Django project (for config package)
      djangoBundlePath,  // Root bundle directory (contains steelo and steeloweb packages)
      path.join(djangoBundlePath, 'steeloweb')  // steeloweb (kept for backwards compatibility)
    ].join(path.delimiter) + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : '');

    console.log('PYTHONPATH:', pythonPathEnv);

    // Simplified migration script
    const migrationScript = `
import sys
import os

print("Python path:")
for i, p in enumerate(sys.path[:8]):
    print(f"  {i}: {p}")

# Test imports
try:
    import django
    print(f"Django imported from: {django.__file__}")
    
    import config.settings.local
    print(f"Config.settings.local imported from: {config.settings.local.__file__}")
    
except ImportError as e:
    print(f"Import error: {e}")
    import traceback
    traceback.print_exc()
    raise

# Run migrations
from django.core.management import execute_from_command_line

if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', '${DJANGO_SETTINGS_MODULE}')
    execute_from_command_line(['manage.py', 'migrate', '--verbosity=2'])
`;

    const migrate = spawn(pythonPath, ['-c', migrationScript], {
      cwd: djangoPath,
      env: getPythonEnv(djangoBundlePath, pythonPathEnv),
      stdio: ['ignore', 'pipe', 'pipe']
    });

    let migrationOutput = '';
    let migrationError = '';

    migrate.stdout.on('data', data => {
      const output = data.toString();
      migrationOutput += output;
      console.log(`[Django MIGRATE] ${output.trim()}`);
    });

    migrate.stderr.on('data', data => {
      const output = data.toString();
      migrationError += output;
      console.log(`[Django MIGRATE Error] ${output.trim()}`);
    });

    migrate.on('exit', code => {
      if (code === 0) {
        console.log('[OK] Migrations completed successfully');
        resolve();
      } else {
        const error = `Migration process exited with code ${code}. Output: ${migrationOutput}. Error: ${migrationError}`;
        console.error(error);
        reject(new Error(error));
      }
    });

    migrate.on('error', err => {
      const error = `Failed to run migrations: ${err.message}`;
      console.error(error);
      reject(new Error(error));
    });
  });
}

// prepareDefaultData function removed - data preparation is now handled through Django's first-time-setup view

/**
 * Kill ALL orphaned Python processes from previous runs
 * This includes Django server AND worker processes
 * This is critical for preventing port conflicts and memory leaks from crashed/force-quit app instances
 */
async function killOrphanedWorkers(djangoBundlePath) {
  console.log('[Startup] Checking for orphaned Python processes from previous runs...');
  console.log(`[Startup] Bundle path: ${djangoBundlePath}`);

  try {
    // execSync already imported at top

    if (process.platform === 'win32') {
      // Windows: Use PowerShell (wmic is deprecated/removed in Windows 11+)
      // KEY CHANGE: Removed steelo_worker filter - now kills ALL Python processes with bundle path
      // This includes Django server (runserver) AND workers (steelo_worker)
      // Note: PowerShell -like operator inside single quotes - backslashes are literal
      // Escape single quotes for PowerShell: ' becomes ''
      const escapedPath = djangoBundlePath.replace(/'/g, "''");
      const psCmd = `
        Get-WmiObject Win32_Process |
        Where-Object {
          $_.Name -eq 'python.exe' -and
          $_.CommandLine -like '*${escapedPath}*'
        } |
        Select-Object -ExpandProperty ProcessId
      `;

      try {
        console.log('[Startup] Running PowerShell query for orphaned Python processes...');
        const output = execSync(`powershell -Command "${psCmd}"`, {
          encoding: 'utf8',
          timeout: 5000
        });

        const pids = output.split('\n')
          .map(line => line.trim())
          .filter(line => /^\d+$/.test(line));

        console.log(`[Startup] Found ${pids.length} orphaned Python process(es)`);

        for (const pid of pids) {
          console.log(`[Startup] Killing orphaned Python process PID ${pid}`);
          try {
            execSync(`taskkill /F /PID ${pid}`, { stdio: 'ignore', timeout: 2000 });
            console.log(`[Startup] Successfully killed PID ${pid}`);
          } catch (e) {
            console.warn(`[Startup] Failed to kill PID ${pid}:`, e.message);
          }
        }

        if (pids.length > 0) {
          console.log(`[Startup] Cleaned up ${pids.length} orphaned Python process(es)`);
        } else {
          console.log('[Startup] No orphaned Python processes found');
        }
      } catch (psErr) {
        // PowerShell failed - DO NOT use fallback that kills all python.exe
        // tasklist cannot filter by command line, so fallback would be catastrophic
        console.error('[Startup] PowerShell query failed:', psErr.message);
        console.error('[Startup] Command that failed:', psCmd.substring(0, 100) + '...');
        console.warn('[Startup] Skipping orphan cleanup - Python processes may remain from previous crashes');
        console.warn('[Startup] You may need to manually kill orphaned python.exe processes');
      }

    } else {
      // macOS/Linux: Use ps with full command line matching
      // KEY CHANGE: Removed steelo_worker filter - now kills ALL Python processes with bundle path
      // This includes Django server (runserver) AND workers (steelo_worker)
      // Escape single quotes in path for safe shell interpolation: ' becomes '"'"'
      const escapedPath = djangoBundlePath.replace(/'/g, `'"'"'`);
      const cmd = `ps aux | grep '[p]ython' | grep '${escapedPath}' | awk '{print $2}'`;

      try {
        console.log('[Startup] Running ps/grep query for orphaned Python processes...');
        console.log(`[Startup] Command: ${cmd}`);
        const output = execSync(cmd, { encoding: 'utf8', timeout: 5000 });

        const pids = output.split('\n')
          .map(line => line.trim())
          .filter(line => /^\d+$/.test(line));

        console.log(`[Startup] Found ${pids.length} orphaned Python process(es)`);

        for (const pid of pids) {
          console.log(`[Startup] Killing orphaned Python process PID ${pid}`);
          try {
            process.kill(parseInt(pid), 'SIGKILL');
            console.log(`[Startup] Successfully killed PID ${pid}`);
          } catch (e) {
            console.warn(`[Startup] Failed to kill PID ${pid}:`, e.message);
          }
        }

        if (pids.length > 0) {
          console.log(`[Startup] Cleaned up ${pids.length} orphaned Python process(es)`);
        } else {
          console.log('[Startup] No orphaned Python processes found');
        }
      } catch (grepErr) {
        console.error('[Startup] ps/grep query failed:', grepErr.message);
        console.error('[Startup] Command that failed:', cmd);
        console.warn('[Startup] Skipping orphan cleanup - Python processes may remain from previous crashes');
        console.warn('[Startup] You may need to manually kill orphaned Python processes');
        // NOTE: Removed fallback that only searched for steelo_worker
        // The primary command now kills ALL Python processes, so no need for worker-specific fallback
      }
    }

    console.log('[Startup] Orphan cleanup complete');
  } catch (err) {
    console.error('[Startup] Critical error in orphan cleanup:', err.message);
    // Don't fail startup if cleanup fails
  }
}

/**
 * Check if port 8000 is available before starting Django
 * This prevents silent failures and provides user-friendly error messages
 */
async function checkAndReservePort(port) {
  return new Promise((resolve, reject) => {
    const net = require('net');
    const server = net.createServer();

    server.once('error', async (err) => {
      if (err.code === 'EADDRINUSE') {
        console.error(`[Port Check] Port ${port} is already in use`);

        // Try to identify the process
        try {
          let processInfo = 'unknown';

          if (process.platform === 'win32') {
            const output = execSync(`netstat -ano | findstr :${port}`).toString();
            const match = output.match(/LISTENING\s+(\d+)/);
            if (match) {
              processInfo = `PID ${match[1]}`;
            }
          } else {
            const pid = execSync(`lsof -ti :${port}`).toString().trim();
            if (pid) {
              processInfo = `PID ${pid}`;
            }
          }

          console.error(`[Port Check] Process occupying port: ${processInfo}`);
        } catch (e) {
          // Ignore errors in process identification
        }

        // Show error to user
        dialog.showErrorBox(
          'Port Already In Use',
          `STEEL-IQ cannot start because port ${port} is already in use.\n\n` +
          `This usually means another instance is running or didn't shut down properly.\n\n` +
          `Please close all STEEL-IQ instances and try again.`
        );

        reject(new Error(`Port ${port} is already in use`));
      } else {
        reject(err);
      }
    });

    server.once('listening', () => {
      console.log(`[Port Check] Port ${port} is available`);
      // Close immediately - we just wanted to check availability
      server.close();
      resolve();
    });

    server.listen(port, '127.0.0.1');
  });
}

function startDjangoServer(djangoBundlePath) {
  return new Promise((resolve, reject) => {
    const djangoPath = path.join(djangoBundlePath, 'django');
    const pythonPath = getPythonPath(djangoBundlePath);

    console.log('Starting Django server...');
    console.log('Django path:', djangoPath);
    console.log('Python path:', pythonPath);
    console.log('Django settings:', DJANGO_SETTINGS_MODULE);

    // Check if Django directory exists
    if (!fs.existsSync(djangoPath)) {
      console.error('Django directory not found at:', djangoPath);
      reject(new Error('Django directory not found'));
      return;
    }

    // Check if Python executable exists
    if (!fs.existsSync(pythonPath)) {
      console.error('Python executable not found at:', pythonPath);
      reject(new Error('Python executable not found'));
      return;
    }

    // Same PYTHONPATH setup as migrations with correct order
    const pyVersion = getPythonVersion(pythonPath);
    const sitePackagesPath = path.join(djangoBundlePath, 'python', 'lib', `python${pyVersion}`, 'site-packages');
    const pythonPathEnv = [
      sitePackagesPath,  // Django package from site-packages
      djangoPath,        // Django project (for config package)
      djangoBundlePath,  // Root bundle directory (contains steelo and steeloweb packages)
      path.join(djangoBundlePath, 'steeloweb')  // steeloweb (kept for backwards compatibility)
    ].join(path.delimiter) + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : '');

    // Simplified server script
    const serverScript = `
import sys
import os

print("Final Python path for server:")
for i, p in enumerate(sys.path[:8]):
    print(f"  {i}: {p}")

# Test imports
try:
    import django
    print(f"Django imported from: {django.__file__}")
    
    import config.settings.local
    print(f"Config.settings.local imported from: {config.settings.local.__file__}")
    
except ImportError as e:
    print(f"Import error: {e}")
    import traceback
    traceback.print_exc()
    raise

# Run server
from django.core.management import execute_from_command_line

if __name__ == '__main__':
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', '${DJANGO_SETTINGS_MODULE}')
    execute_from_command_line(['manage.py', 'runserver', '8000', '--noreload'])
`;

    // Platform-specific spawn: Use job wrapper on Windows for bulletproof cleanup
    let spawnCmd, spawnArgs;
    if (process.platform === 'win32') {
      // Windows: Wrap Django in job object for guaranteed cleanup
      // Pass pythonPath to the wrapper so it can spawn Django correctly

      // Handle ASAR unpacked path: files marked with asarUnpack are in app.asar.unpacked/
      let wrapperPath = path.join(__dirname, 'windows_job_wrapper.py');
      if (wrapperPath.includes('app.asar')) {
        wrapperPath = wrapperPath.replace('app.asar', 'app.asar.unpacked');
      }

      spawnCmd = pythonPath;
      spawnArgs = [
        wrapperPath,
        pythonPath,  // The wrapper will use this to spawn Django
        '-c',
        serverScript
      ];
      console.log('[Windows] Starting Django with Job Object wrapper');
      console.log(`[Windows] Wrapper path: ${wrapperPath}`);
    } else {
      // macOS/Linux: Direct spawn
      spawnCmd = pythonPath;
      spawnArgs = ['-c', serverScript];
    }

    djangoProcess = spawn(
      spawnCmd,
      spawnArgs,
      {
        cwd: djangoPath,
        env: getPythonEnv(djangoBundlePath, pythonPathEnv),
        stdio: ['ignore', 'pipe', 'pipe']
      }
    );

    djangoProcess.stdout.on('data', data => {
      const output = data.toString();
      console.log(`[Django] ${output}`);
    });

    djangoProcess.stderr.on('data', data => {
      const output = data.toString();
      console.log(`[Django] ${output}`);
    });

    djangoProcess.on('error', err => {
      console.error(`Failed to start Django: ${err}`);
      reject(err);
    });

    djangoProcess.on('exit', code => {
      console.log(`Django process exited with code ${code}`);
      if (code !== 0) {
        reject(new Error(`Django exited with code ${code}`));
      }
    });

    // Don't wait for log output - resolve immediately and let waitForHttp check
    console.log('Django process started, will wait for HTTP response...');
    resolve();
  });
}

function startTaskWorker(djangoBundlePath) {
  return new Promise((resolve, reject) => {
    // NOTE: Workers are now created via HTTP API by the renderer process
    // This ensures consistent behavior and allows for memory warnings on startup
    // The Django server handles worker management through the /api/workers endpoints
    console.log('[INFO] Task infrastructure ready - workers will be created via UI');
    console.log('[INFO] Renderer will check memory and prompt user if needed');
    resolve();
  });
}

function setupApplicationMenu() {
  // Using require (created via createRequire) for JSON file
  const packageJson = require('./package.json');
  const appVersion = packageJson.version || '1.2.0';

  const template = [
    // macOS app menu (only on macOS)
    ...(process.platform === 'darwin' ? [{
      label: app.name,
      submenu: [
        { role: 'about' },
        { type: 'separator' },
        { role: 'services' },
        { type: 'separator' },
        { role: 'hide' },
        { role: 'hideOthers' },
        { role: 'unhide' },
        { type: 'separator' },
        { role: 'quit' }
      ]
    }] : []),
    // File menu
    {
      label: 'File',
      submenu: [
        process.platform === 'darwin' ? { role: 'close' } : { role: 'quit' }
      ]
    },
    // Edit menu
    {
      label: 'Edit',
      submenu: [
        { role: 'undo' },
        { role: 'redo' },
        { type: 'separator' },
        { role: 'cut' },
        { role: 'copy' },
        { role: 'paste' },
        ...(process.platform === 'darwin' ? [
          { role: 'pasteAndMatchStyle' },
          { role: 'delete' },
          { role: 'selectAll' },
          { type: 'separator' },
          {
            label: 'Speech',
            submenu: [
              { role: 'startSpeaking' },
              { role: 'stopSpeaking' }
            ]
          }
        ] : [
          { role: 'delete' },
          { type: 'separator' },
          { role: 'selectAll' }
        ])
      ]
    },
    // View menu
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'forceReload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' }
      ]
    },
    // Window menu
    {
      label: 'Window',
      submenu: [
        { role: 'minimize' },
        { role: 'zoom' },
        ...(process.platform === 'darwin' ? [
          { type: 'separator' },
          { role: 'front' },
          { type: 'separator' },
          { role: 'window' }
        ] : [
          { role: 'close' }
        ])
      ]
    },
    // Help menu
    {
      label: 'Help',
      submenu: [
        {
          label: 'FAQ',
          click: async () => {
            await shell.openExternal('https://www.systemiq.earth/reports/steel-iq/docs#faq');
          }
        },
        {
          label: 'User Guide',
          click: async () => {
            await shell.openExternal('https://www.systemiq.earth/reports/steel-iq/docs/#guide');
          }
        },
        {
          label: 'Project page on GitHub',
          click: async () => {
            await shell.openExternal('https://github.com/systemiqofficial/steel-iq');
          }
        },
        {
          label: 'Connect with the Team',
          click: async () => {
            await shell.openExternal('mailto:Steel-IQ@systemiq.earth');
          }
        },
        {
          label: 'Provide Feedback',
          click: async () => {
            await shell.openExternal('https://forms.office.com/e/tvk4pRJm9V');
          }
        },
        { type: 'separator' },
        {
          label: 'About STEEL-IQ',
          click: async () => {
            await shell.openExternal('https://www.systemiq.earth/reports/steel-iq/');
          }
        },
        {
          label: 'About Systemiq',
          click: async () => {
            await shell.openExternal('https://www.systemiq.earth/');
          }
        },
        {
          label: 'License',
          click: async () => {
            await shell.openExternal('https://www.apache.org/licenses/LICENSE-2.0');
          }
        }
      ]
    }
  ];

  const menu = Menu.buildFromTemplate(template);
  Menu.setApplicationMenu(menu);
}

function createLoadingWindow() {
  const loadingWin = new BrowserWindow({
    width: 600,
    height: 400,
    frame: false,
    center: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.cjs')
    }
  });

  // Read the logo and encode as base64 data URL to avoid file:// access from data: URL context
  // Chromium blocks file:// URLs from data: URL documents for security reasons
  let logoDataURL = '';
  try {
    let assetsDir = __dirname;
    if (assetsDir.includes('app.asar')) {
      assetsDir = assetsDir.replace('app.asar', 'app.asar.unpacked');
    }
    const logoPath = path.join(assetsDir, 'assets', 'systemiq-logo-blue.png');
    const logoBuffer = fs.readFileSync(logoPath);
    logoDataURL = `data:image/png;base64,${logoBuffer.toString('base64')}`;
  } catch (err) {
    console.error('Failed to load logo for splash screen:', err);
    // Leave logoDataURL empty - image will fail to load but app will still start
  }

  let fontFaceRule = '';
  try {
    const fontPath = path.join(
      __dirname,
      '..',
      'steeloweb',
      'static',
      'steeloweb',
      'fonts',
      'saira',
      'Saira-latin-variable.woff2'
    );
    if (fs.existsSync(fontPath)) {
      const fontUrl = pathToFileURL(fontPath).toString();
      fontFaceRule = `
        @font-face {
          font-family: 'Saira';
          src: url('${fontUrl}') format('woff2');
          font-weight: 100 900;
          font-style: normal;
          font-display: swap;
        }
      `;
    }
  } catch (err) {
    console.warn('Failed to load Saira font for splash screen:', err);
  }

  const loadingHtml = `
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <style>
        ${fontFaceRule}
        body {
          margin: 0;
          padding: 40px;
          font-family: 'Saira', -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', 'Helvetica Neue', sans-serif;
          text-align: center;
          background: #f0f0f0;
          display: flex;
          flex-direction: column;
          justify-content: center;
          height: calc(100vh - 80px);
          position: relative;
        }
        .logo {
          position: absolute;
          top: 20px;
          right: 30px;
          height: 40px;
        }
        h1 {
          color: #333;
          font-weight: 600;
          margin-bottom: 5px;
        }
        .byline {
          color: #666;
          font-size: 16px;
          margin-bottom: 30px;
        }
        p {
          color: #666;
          margin-bottom: 30px;
        }
        .progress-container {
          margin: 20px auto;
          width: 300px;
          height: 20px;
          background: #ddd;
          border-radius: 10px;
          overflow: hidden;
        }
        .progress-bar {
          width: 10%;
          height: 100%;
          background: #0d6efd;
          animation: pulse 1.5s ease-in-out infinite;
          transition: width 0.3s ease;
        }
        @keyframes pulse {
          0% { opacity: 1; }
          50% { opacity: 0.6; }
          100% { opacity: 1; }
        }
        .status {
          margin-top: 20px;
          font-size: 14px;
          color: #888;
        }
      </style>
    </head>
    <body>
      <img src="${logoDataURL}" alt="Systemiq" class="logo">
      <div>
        <h1>STEEL-IQ</h1>
        <p class="byline">Global steel sector decarbonisation model</p>
        <p>Starting application...it may take a few minutes</p>
        <div class="progress-container">
          <div class="progress-bar" id="progress"></div>
        </div>
        <div class="status" id="status">Initializing...</div>
      </div>
    </body>
    </html>
  `;

  loadingWin.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(loadingHtml)}`);
  return loadingWin;
}

function createWindow(initialUrl = 'http://127.0.0.1:8000/') {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.cjs')
    },
    show: false // Don't show until Django is ready
  });

  // Load Django application
  win.loadURL(initialUrl);

  win.once('ready-to-show', () => {
    win.show();
    // Mark healthy after window shows for 5 seconds
    setTimeout(() => {
      store.set('consecutiveCrashes', 0);
      console.log('[OK] Healthy launch detected, resetting crash counter');
    }, 5000);
  });

  win.on('closed', () => {
    console.log('[SHUTDOWN] Window closed event fired at', Date.now());
    console.log('[SHUTDOWN] Remaining windows:', BrowserWindow.getAllWindows().length);
    if (BrowserWindow.getAllWindows().length === 0) {
      console.log('[SHUTDOWN] No windows remaining, calling app.quit()');
      app.quit();
    }
  });

  // Open DevTools in development
  if (isDev) {
    win.webContents.openDevTools();
  }
  
  return win;
}

app.whenReady().then(() => {
  // Set up application menu
  setupApplicationMenu();

  if (isDev) {
    // Development mode: check if Django is running, if not show fallback
    checkDjangoServer().then(djangoRunning => {
      if (djangoRunning) {
        console.log('Django server detected, creating window...');
        createWindow();
      } else {
        console.log('Django server not found, creating fallback window...');
        const win = new BrowserWindow({
          width: 1024,
          height: 768,
          webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.cjs')
          }
        });
        // Load a fallback page or show error
        win.loadURL('data:text/html,<h1>Django server not found</h1><p>Please start the Django development server first.</p>');
        win.webContents.openDevTools();
      }
    });
  } else {
    // Production mode: start Django first, then create window
    console.log('Electron app ready, starting Django...');

    let djangoBundlePath;
    try {
      djangoBundlePath = findDjangoBundlePath();
      currentDjangoBundlePath = djangoBundlePath;  // Store for shutdown
    } catch (error) {
      console.error('Failed to find Django bundle:', error.message);
      // Show error window
      const win = new BrowserWindow({
        width: 1024,
        height: 768,
        webPreferences: {
          nodeIntegration: false,
          contextIsolation: true
        }
      });
      win.loadURL('data:text/html,<h1>Error</h1><p>Django bundle not found</p>');
      return;
    }

    // Show loading window immediately (provides instant feedback)
    const loadingWindow = createLoadingWindow();
    loadingWindow.show();

    // Helper to update loading window status
    const updateLoadingStatus = (message, progress = null) => {
      if (loadingWindow && !loadingWindow.isDestroyed()) {
        loadingWindow.webContents.executeJavaScript(`
          document.getElementById('status').textContent = '${message}';
          ${progress !== null ? `document.getElementById('progress').style.width = '${progress}%';` : ''}
        `);
      }
    };

    // Kill orphaned workers first, check port availability, then run migrations, then start server
    killOrphanedWorkers(djangoBundlePath)
      .then(() => {
        updateLoadingStatus('Checking port availability...', 5);
        console.log('[Startup] Checking if port 8000 is available...');
        return checkAndReservePort(8000);
      })
      .then(() => {
        updateLoadingStatus('Running database migrations...', 10);
        return runMigrations(djangoBundlePath);
      })
      .then(() => {
        updateLoadingStatus('Starting web server...', 30);
        console.log('Starting Django server...');
        return startDjangoServer(djangoBundlePath);
      })
      .then(() => {
        updateLoadingStatus('Waiting for server to respond...', 50);
        console.log('Waiting for Django server to respond...');
        return waitForHttp('http://127.0.0.1:8000/', 60000); // 60 second timeout
      })
      .then(() => {
        updateLoadingStatus('Starting background services...', 70);
        console.log('Django server is ready, starting task worker...');
        // Start the task worker BEFORE creating the main window
        // This ensures background tasks work immediately
        return startTaskWorker(djangoBundlePath);
      })
      .then(() => {
        updateLoadingStatus('Loading application...', 90);
        console.log('Task worker started, creating main window...');
        // Small delay to ensure task worker is fully initialized
        return new Promise(resolve => setTimeout(resolve, 1000));
      })
      .then(() => {
        // Update to 100% before creating main window
        updateLoadingStatus('Loading application...', 100);
        
        // Small delay to show 100% completion
        return new Promise(resolve => setTimeout(resolve, 300));
      })
      .then(() => {
        // Navigate to first-time-setup which will handle data preparation if needed
        const mainWindow = createWindow('http://127.0.0.1:8000/first-time-setup/');
        
        // Close loading window once main window is ready
        mainWindow.once('ready-to-show', () => {
          loadingWindow.close();
        });
        
        return mainWindow;
      })
      .then(() => {
        console.log('[OK] All services started successfully');
      })
      .catch(err => {
        console.error(`Startup error: ${err}`);
        console.log('Creating error window...');
        
        // Close loading window if still open
        if (loadingWindow && !loadingWindow.isDestroyed()) {
          loadingWindow.close();
        }
        
        const win = new BrowserWindow({
          width: 1024,
          height: 768,
          webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.cjs')
          }
        });
        win.loadURL(`data:text/html,<h1>Startup Error</h1><p>${err.message}</p>`);
      });
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  console.log('[SHUTDOWN] window-all-closed fired at', Date.now());
  console.log('[SHUTDOWN] Platform:', process.platform);
  console.log('[SHUTDOWN] shutdownInProgress:', shutdownInProgress);
  console.log('[SHUTDOWN] djangoProcess PID:', djangoProcess?.pid || 'none');
  console.log('[SHUTDOWN] taskWorkerProcess PID:', taskWorkerProcess?.pid || 'none');

  // On macOS, let before-quit handle all cleanup to avoid race condition
  if (process.platform === 'darwin') {
    console.log('[SHUTDOWN] macOS: Skipping process kill, letting before-quit handle shutdown');
    return;
  }

  // Windows/Linux: Kill processes and quit
  if (djangoProcess) {
    console.log('[SHUTDOWN] Killing Django process...');
    djangoProcess.kill();
  }
  if (taskWorkerProcess) {
    console.log('[SHUTDOWN] Killing Task Worker process...');
    taskWorkerProcess.kill();
  }
  console.log('[SHUTDOWN] Non-macOS platform, calling app.quit()');
  app.quit();
});

// ============================================================================
// SHUTDOWN HELPER FUNCTIONS
// ============================================================================

async function attemptGracefulDrain(timeout) {
  try {
    console.log('[SHUTDOWN] Calling drain API at', Date.now());
    console.log('[SHUTDOWN] Drain timeout:', timeout, 'ms');

    // Call drain API with AbortSignal.timeout (Node.js 20+)
    const response = await fetch('http://127.0.0.1:8000/api/workers/drain-all/', {
      method: 'POST',
      signal: AbortSignal.timeout(2000)  // 2 second timeout for API call
    });

    if (!response.ok) {
      console.error('[SHUTDOWN] Drain API returned non-OK status:', response.status);
      return false;
    }

    const data = await response.json();
    console.log(`[SHUTDOWN] Drain API success: Marked ${data.workers_draining} workers as DRAINING`);

    // Wait for workers to exit
    const startTime = Date.now();
    let checkCount = 0;
    while (Date.now() - startTime < timeout) {
      try {
        checkCount++;
        // Check status with timeout
        const status = await fetch('http://127.0.0.1:8000/api/workers/status/', {
          signal: AbortSignal.timeout(1000)  // 1 second timeout for status check
        });
        const statusData = await status.json();

        const active = statusData.workers.filter(w =>
          ['STARTING', 'RUNNING', 'DRAINING'].includes(w.state)
        ).length;

        console.log(`[SHUTDOWN] Status check #${checkCount}: ${active} active workers (elapsed: ${Date.now() - startTime}ms)`);

        if (active === 0) {
          console.log('[SHUTDOWN] All workers exited cleanly');
          return true;
        }
      } catch (err) {
        // Timeout or network error - continue waiting
        console.log(`[SHUTDOWN] Status check #${checkCount} failed:`, err.message);
      }

      await new Promise(resolve => setTimeout(resolve, 500));
    }

    console.log('[SHUTDOWN] Graceful drain timeout after', Date.now() - startTime, 'ms');
    return false;  // Timeout
  } catch (err) {
    console.error('[SHUTDOWN] Graceful drain failed:', err.message);
    console.error('[SHUTDOWN] Error details:', err);
    return false;
  }
}

async function forceKillProcessTree(bundlePath) {
  // spawn and execSync already imported at top

  console.log('[SHUTDOWN] Force killing process tree at', Date.now());
  console.log('[SHUTDOWN] Bundle path:', bundlePath);

  if (process.platform === 'win32') {
    // Windows: taskkill with /T flag
    try {
      console.log('[SHUTDOWN] Using taskkill /T /F for PID', djangoProcess.pid);
      spawn('taskkill', ['/pid', djangoProcess.pid, '/T', '/F'], {
        detached: true,
        stdio: 'ignore'
      });
      console.log('[SHUTDOWN] Sent taskkill command');
    } catch (err) {
      console.error('[SHUTDOWN] taskkill failed:', err.message);
    }
  } else {
    // macOS: Enumerate and kill all Python processes matching bundle path
    if (!bundlePath) {
      console.error('[SHUTDOWN] No bundle path available for process enumeration');
      // Fallback: just kill Django
      try {
        console.log('[SHUTDOWN] Fallback: killing Django process directly');
        djangoProcess.kill('SIGKILL');
      } catch (e) {
        console.log('[SHUTDOWN] Django already dead:', e.message);
      }
      return;
    }

    try {
      const escapedPath = bundlePath.replace(/'/g, `'"'"'`);
      const cmd = `ps aux | grep '[p]ython' | grep '${escapedPath}' | awk '{print $2}'`;
      console.log('[SHUTDOWN] Running command:', cmd);

      const output = execSync(cmd, { encoding: 'utf8', timeout: 5000 });
      console.log('[SHUTDOWN] Command output:', output.substring(0, 200)); // First 200 chars

      const pids = output.split('\n')
        .map(line => line.trim())
        .filter(line => /^\d+$/.test(line));

      console.log(`[SHUTDOWN] Found ${pids.length} Python process(es) to kill:`, pids);

      for (const pid of pids) {
        try {
          process.kill(parseInt(pid), 'SIGKILL');
          console.log(`[SHUTDOWN] Killed process ${pid}`);
        } catch (e) {
          console.log(`[SHUTDOWN] Failed to kill ${pid}:`, e.message);
        }
      }
    } catch (err) {
      // grep returns non-zero exit code when no matches
      if (err.status === 1) {
        console.log('[SHUTDOWN] No Python processes found matching bundle path (grep returned no matches)');
      } else {
        console.error('[SHUTDOWN] Process enumeration failed:', err.message);
      }
      // Fallback: try to kill Django directly
      try {
        if (djangoProcess && djangoProcess.pid) {
          console.log('[SHUTDOWN] Fallback: killing Django process', djangoProcess.pid);
          djangoProcess.kill('SIGKILL');
        }
      } catch (e) {
        console.log('[SHUTDOWN] Django already dead:', e.message);
      }
    }
  }

  console.log('[SHUTDOWN] Waiting 1 second for processes to die...');
  await new Promise(resolve => setTimeout(resolve, 1000));
  console.log('[SHUTDOWN] Force kill complete at', Date.now());
}

// ============================================================================
// SHUTDOWN HANDLER - HYBRID APPROACH
// ============================================================================

app.on('before-quit', async (event) => {
  console.log('[SHUTDOWN] before-quit fired at', Date.now());
  console.log('[SHUTDOWN] Platform:', process.platform);
  console.log('[SHUTDOWN] shutdownInProgress:', shutdownInProgress);
  console.log('[SHUTDOWN] djangoProcess PID:', djangoProcess?.pid || 'none');
  console.log('[SHUTDOWN] currentDjangoBundlePath:', currentDjangoBundlePath);

  if (djangoProcess && !shutdownInProgress) {
    event.preventDefault();
    shutdownInProgress = true;
    console.log('[SHUTDOWN] Preventing default quit, starting shutdown sequence');

    if (process.platform === 'win32') {
      // Windows with Job Objects: Simpler shutdown
      // Job Object will automatically kill all workers when Django exits
      console.log('[SHUTDOWN] Phase 1: Graceful drain (10 seconds max)...');

      const gracefulSuccess = await attemptGracefulDrain(10000);

      if (gracefulSuccess) {
        console.log('[SHUTDOWN] Workers exited gracefully');
      } else {
        console.log('[SHUTDOWN] Graceful drain timeout - Job Object will clean up');
      }

      // Kill Django (Job Object automatically kills all workers)
      try {
        djangoProcess.kill('SIGTERM');
        console.log('[SHUTDOWN] Django terminated - Job Object will clean up workers');
      } catch (e) {
        console.log('[SHUTDOWN] Django already dead:', e.message);
      }

      setTimeout(() => {
        console.log('[SHUTDOWN] Complete - quitting app');
        app.quit();
      }, 2000);
    } else {
      // macOS: Use full hybrid approach (graceful drain + force kill)
      console.log('[SHUTDOWN] macOS shutdown - Phase 1: Graceful drain (10 seconds max)...');

      // PHASE 1: Try graceful drain (10 seconds)
      const gracefulSuccess = await attemptGracefulDrain(10000);

      if (gracefulSuccess) {
        console.log('[SHUTDOWN] Phase 1: Success - workers exited cleanly');
      } else {
        console.log('[SHUTDOWN] Phase 1: Timeout or failed - proceeding to force kill');
      }

      // PHASE 2: Force kill process tree
      console.log('[SHUTDOWN] Phase 2: Killing process tree...');
      await forceKillProcessTree(currentDjangoBundlePath);
      console.log('[SHUTDOWN] Phase 2: Force kill complete');

      // PHASE 3: Check for remaining processes
      console.log('[SHUTDOWN] Phase 3: Checking for remaining Python processes...');
      try {
        const escapedPath = currentDjangoBundlePath.replace(/'/g, `'"'"'`);
        const cmd = `ps aux | grep '[p]ython' | grep '${escapedPath}'`;
        const output = execSync(cmd, { encoding: 'utf8', timeout: 2000 });

        if (output.trim()) {
          console.log('[SHUTDOWN] WARNING: Remaining Python processes found:');
          console.log(output);
        } else {
          console.log('[SHUTDOWN] No remaining Python processes found');
        }
      } catch (e) {
        // grep returns non-zero exit code when no matches found
        console.log('[SHUTDOWN] No remaining Python processes found (grep returned no matches)');
      }

      // PHASE 4: Final verification and quit
      setTimeout(() => {
        console.log('[SHUTDOWN] Complete - quitting app at', Date.now());
        app.quit();
      }, 2000);
    }
  } else {
    if (!djangoProcess) {
      console.log('[SHUTDOWN] No Django process to clean up, allowing quit');
    }
    if (shutdownInProgress) {
      console.log('[SHUTDOWN] Shutdown already in progress, allowing quit to proceed');
    }
  }
});

// Handle process termination
process.on('exit', () => {
  if (djangoProcess) {
    djangoProcess.kill();
  }
  if (taskWorkerProcess) {
    taskWorkerProcess.kill();
  }
});

process.on('SIGINT', () => {
  app.quit();
});

process.on('SIGTERM', () => {
  app.quit();
});

} // End of single instance lock else block - all initialization code above only runs if we got the lock
