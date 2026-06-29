const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  openDirectory: () => ipcRenderer.invoke('dialog:openDirectory'),
  openExternal:  (url) => ipcRenderer.invoke('shell:openExternal', url),
  notify: (title, body) => ipcRenderer.invoke('notify', { title, body }),
  onUpdateProgress: (cb) => ipcRenderer.on('update-progress', (_, pct) => cb(pct)),
  onUpdateAvailable: (cb) => ipcRenderer.on('update-available', () => cb()),
  onUpdateReady: (cb) => ipcRenderer.on('update-ready', () => cb()),
  getLoginItem: () => ipcRenderer.invoke('loginItem:get'),
  setLoginItem: (enabled) => ipcRenderer.invoke('loginItem:set', enabled),
});
