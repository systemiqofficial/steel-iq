const { contextBridge, ipcRenderer } = require('electron');

// Note: Sentry initialization for renderer is currently disabled
// due to sandbox restrictions. Main process errors are still captured.

// Expose minimal safe APIs to renderer
contextBridge.exposeInMainWorld('electronAPI', {
  platform: process.platform,
  version: process.versions.electron,
  openLogFile: (logFilePath) => ipcRenderer.invoke('open-log-file', logFilePath)
});