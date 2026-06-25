const { app, BrowserWindow, shell, Tray, Menu, nativeImage, dialog, ipcMain, Notification } = require('electron');
const path = require('path');
const fs   = require('fs');
const { spawn, execFileSync } = require('child_process');

let mainWindow;
let backendProcess;
let tray;
let isQuitting = false;

// ── 路徑決策：打包版用 app.isPackaged 判斷，開發版相對 __dirname ──
const ROOT_DIR     = path.join(__dirname, '..');          // electron/../  = project root
const srcFrontend  = path.join(ROOT_DIR, 'frontend', 'dist', 'frontend', 'browser', 'index.html');
const srcBackendPy = path.join(ROOT_DIR, 'backend', 'main.py');
const useSrc       = !app.isPackaged && fs.existsSync(srcFrontend) && fs.existsSync(srcBackendPy);

// 打包後的路徑
const bundledExe      = path.join(__dirname, '..', 'backend', 'claude-backend.exe');
const bundledFrontend = path.join(__dirname, '..', 'frontend', 'dist', 'frontend', 'browser', 'index.html');

// ── 偵測 Claude Code 是否已安裝 ───────────────────────────
function detectClaude() {
  const bins = ['claude'];
  for (const bin of bins) {
    try {
      execFileSync(bin, ['--version'], { stdio: 'pipe', windowsHide: true, shell: true, timeout: 5000 });
      return bin;
    } catch {}
  }
  return null;
}

// ── 啟動後端 ──────────────────────────────────────────────
function startBackend() {
  if (useSrc) {
    // 開發者模式：用 Python 直接跑原始碼
    const candidates = ['python', 'python3', 'py'];
    for (const py of candidates) {
      try {
        backendProcess = spawn(py, [srcBackendPy], {
          cwd: path.dirname(srcBackendPy),
          stdio: 'pipe', windowsHide: true, shell: true,
        });
        backendProcess.on('error', () => {});
        break;
      } catch {}
    }
  } else {
    // 發行版：用編譯好的 exe
    try {
      backendProcess = spawn(bundledExe, [], {
        cwd: path.dirname(bundledExe),
        stdio: 'pipe', windowsHide: true, shell: false,
      });
      backendProcess.on('error', () => {});
    } catch {}
  }
}

// ── 等待後端就緒 ──────────────────────────────────────────
async function waitForBackend(port = 8765, maxMs = 20000) {
  const deadline = Date.now() + maxMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`http://localhost:${port}/api/status`);
      if (res.ok) return true;
    } catch {}
    await new Promise(r => setTimeout(r, 300));
  }
  return false;
}

// ── Claude Code 未安裝時顯示引導頁 ───────────────────────
function showNoClaudePage() {
  mainWindow = new BrowserWindow({
    width: 640, height: 400,
    title: 'Claude 桌面版 — 設定',
    backgroundColor: '#0d0d0d',
    autoHideMenuBar: true,
    resizable: false,
    show: false,
  });

  const html = `<!doctype html><html><head>
  <meta charset="utf-8">
  <style>
    body{font-family:sans-serif;background:#0d0d0d;color:#e8e8e8;
         display:flex;flex-direction:column;align-items:center;
         justify-content:center;height:100vh;margin:0;gap:16px;text-align:center}
    h2{color:#d4a853;margin:0}
    code{background:#1f1f1f;padding:6px 14px;border-radius:6px;font-size:15px;
         color:#7c6fff;display:block;margin:8px auto;width:fit-content}
    p{color:#888;font-size:13px;max-width:480px;line-height:1.6}
    a{color:#7c6fff}
  </style></head><body>
  <div style="font-size:48px">⚡</div>
  <h2>需要先安裝 Claude Code</h2>
  <p>此應用程式需要 <strong>Claude Code CLI</strong> 才能運作。</p>
  <p>請先在終端機執行：</p>
  <code>npm install -g @anthropic-ai/claude-code</code>
  <p>安裝完成後執行登入：</p>
  <code>claude login</code>
  <p>完成後重新啟動此應用程式即可。<br>
  詳情請參閱 <a href="https://claude.ai/code">claude.ai/code</a></p>
  </body></html>`;

  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`);
  mainWindow.once('ready-to-show', () => mainWindow.show());
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url); return { action: 'deny' };
  });
}

// ── IPC handlers ──────────────────────────────────────────
ipcMain.handle('dialog:openDirectory', async () => {
  const result = await dialog.showOpenDialog({ properties: ['openDirectory'] });
  return result.filePaths[0] ?? null;
});

ipcMain.handle('notify', (_, { title, body }) => {
  if (Notification.isSupported()) new Notification({ title, body }).show();
});

// ── 建立主視窗 ────────────────────────────────────────────
function createWindow() {
  const preloadPath = path.join(__dirname, 'preload.js');
  mainWindow = new BrowserWindow({
    width: 1280, height: 800, minWidth: 800, minHeight: 600,
    title: 'Claude 桌面版',
    backgroundColor: '#0d0d0d',
    webPreferences: { nodeIntegration: false, contextIsolation: true, preload: preloadPath },
    autoHideMenuBar: true,
    show: false,
  });

  const isDev = process.argv.includes('--dev');
  const url = isDev
    ? 'http://localhost:4200'
    : `file://${useSrc ? srcFrontend : bundledFrontend}`;

  mainWindow.loadURL(url);
  mainWindow.once('ready-to-show', () => mainWindow.show());

  mainWindow.on('close', (e) => {
    if (!isQuitting) {
      e.preventDefault();
      mainWindow.hide();
    }
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url); return { action: 'deny' };
  });
}

// ── 系統匣 ────────────────────────────────────────────────
function createTray() {
  const iconPath = path.join(__dirname, 'icon.ico');
  const icon = fs.existsSync(iconPath)
    ? nativeImage.createFromPath(iconPath)
    : nativeImage.createEmpty();

  tray = new Tray(icon);
  tray.setToolTip('Claude 桌面版');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '顯示視窗', click: () => { mainWindow?.show(); mainWindow?.focus(); } },
    { type: 'separator' },
    { label: '檢查更新', click: checkForUpdates },
    { type: 'separator' },
    { label: '退出', click: () => { isQuitting = true; app.quit(); } },
  ]));
  tray.on('click',        () => { mainWindow?.show(); mainWindow?.focus(); });
  tray.on('double-click', () => { mainWindow?.show(); mainWindow?.focus(); });
}

// ── 自動更新 ──────────────────────────────────────────────
function checkForUpdates() {
  if (process.argv.includes('--dev')) {
    dialog.showMessageBox({ message: 'Dev 模式不支援自動更新。' }); return;
  }
  try {
    const { autoUpdater } = require('electron-updater');
    autoUpdater.on('update-available', () => {
      if (mainWindow) mainWindow.webContents.send('update-available');
    });
    autoUpdater.on('download-progress', (prog) => {
      const pct = Math.round(prog.percent ?? 0);
      if (mainWindow) mainWindow.webContents.send('update-progress', pct);
    });
    autoUpdater.on('update-downloaded', () => {
      if (mainWindow) mainWindow.webContents.send('update-ready');
      dialog.showMessageBox({ message: '更新已下載，將重啟應用程式。', buttons: ['立即重啟', '稍後'] })
        .then(({ response }) => { if (response === 0) autoUpdater.quitAndInstall(); });
    });
    autoUpdater.on('update-not-available', () => dialog.showMessageBox({ message: '目前已是最新版本。' }));
    autoUpdater.on('error', (e) => dialog.showMessageBox({ message: '更新失敗：' + e.message }));
    autoUpdater.checkForUpdates();
  } catch {
    dialog.showMessageBox({ message: '尚未設定更新伺服器。\n請先將專案 push 到 GitHub 並建立 Release。' });
  }
}

// ── 應用程式生命週期 ──────────────────────────────────────
app.whenReady().then(async () => {
  createTray();

  // 偵測 Claude Code
  const claudeBin = detectClaude();
  if (!claudeBin) {
    showNoClaudePage();
    return;
  }

  startBackend();
  const ready = await waitForBackend();
  if (!ready) {
    dialog.showMessageBox({ message: '後端啟動逾時，請重新啟動應用程式。' });
    app.quit(); return;
  }
  createWindow();
});

app.on('before-quit', () => {
  isQuitting = true;
  backendProcess?.kill();
});

app.on('window-all-closed', () => { /* 保持在系統匣 */ });

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
  else mainWindow?.show();
});
