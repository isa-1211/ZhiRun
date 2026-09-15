const { app, BrowserWindow, dialog, session, shell } = require('electron');
const fs = require('fs');
const path = require('path');

const APP_URL = 'http://8.145.49.45/';
const VERSION_URL = 'http://8.145.49.45/app/version';
const USER_AGENT_SUFFIX = ' ZhiRunWindows/1.0';
const stateFile = () => path.join(app.getPath('userData'), 'content-version.json');

let mainWindow = null;
let updateDialogVisible = false;
let focusCheckTimer = null;

function readInstalledVersion() {
  try {
    return JSON.parse(fs.readFileSync(stateFile(), 'utf8')).content_version || '';
  } catch (_) {
    return '';
  }
}

function writeInstalledVersion(contentVersion) {
  try {
    fs.mkdirSync(path.dirname(stateFile()), { recursive: true });
    fs.writeFileSync(stateFile(), JSON.stringify({ content_version: contentVersion }), 'utf8');
  } catch (_) {
    // A failed preference write must not prevent the dashboard from opening.
  }
}

async function fetchContentVersion() {
  try {
    const response = await fetch(`${VERSION_URL}?desktop_check=${Date.now()}`, {
      headers: { 'User-Agent': 'ZhiRunWindowsUpdater/1.0', 'Cache-Control': 'no-cache' },
      signal: AbortSignal.timeout(6000),
    });
    if (!response.ok) return '';
    const payload = await response.json();
    return typeof payload.content_version === 'string' ? payload.content_version : '';
  } catch (_) {
    return '';
  }
}

function loadDashboard(contentVersion = '') {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const query = contentVersion ? `?app_version=${encodeURIComponent(contentVersion)}` : '';
  mainWindow.loadURL(`${APP_URL}${query}`);
}

async function applyContentUpdate(contentVersion) {
  writeInstalledVersion(contentVersion);
  await session.defaultSession.clearCache();
  loadDashboard(contentVersion);
}

async function checkForContentUpdate(initial = false) {
  const remoteVersion = await fetchContentVersion();
  if (!remoteVersion || !mainWindow || mainWindow.isDestroyed()) {
    if (initial) loadDashboard();
    return;
  }
  const installedVersion = readInstalledVersion();
  if (!installedVersion) {
    writeInstalledVersion(remoteVersion);
    loadDashboard(remoteVersion);
    return;
  }
  if (installedVersion === remoteVersion) {
    if (initial) loadDashboard(remoteVersion);
    return;
  }
  if (updateDialogVisible) return;
  updateDialogVisible = true;
  const result = await dialog.showMessageBox(mainWindow, {
    type: 'info',
    title: '发现智润更新',
    message: '服务器上的智润页面和功能已有新版本。',
    detail: '点击“立即更新”即可同步，无需重新安装 Windows 桌面端。',
    buttons: ['立即更新', '稍后'],
    defaultId: 0,
    cancelId: 1,
    noLink: true,
  });
  updateDialogVisible = false;
  if (result.response === 0) await applyContentUpdate(remoteVersion);
  else if (initial) loadDashboard(remoteVersion);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 960,
    minHeight: 640,
    title: '智润 · 智慧农业水肥一体系统',
    backgroundColor: '#0b1017',
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      spellcheck: false,
    },
  });
  mainWindow.webContents.setUserAgent(`${mainWindow.webContents.getUserAgent()}${USER_AGENT_SUFFIX}`);
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(APP_URL)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (!url.startsWith(APP_URL)) shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('did-fail-load', (_event, _code, _description, _validatedURL, isMainFrame) => {
    if (isMainFrame) {
      dialog.showMessageBox(mainWindow, {
        type: 'warning',
        title: '暂时无法连接智润',
        message: '请检查电脑网络后点击重试。',
        buttons: ['重试', '关闭'],
        defaultId: 0,
        cancelId: 1,
      }).then(result => { if (result.response === 0) checkForContentUpdate(true); });
    }
  });
  mainWindow.on('closed', () => { mainWindow = null; });
}

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  createWindow();
  checkForContentUpdate(true);
  app.on('browser-window-focus', () => {
    clearTimeout(focusCheckTimer);
    focusCheckTimer = setTimeout(() => checkForContentUpdate(false), 800);
  });
  app.on('activate', () => {
    if (!mainWindow) { createWindow(); checkForContentUpdate(true); }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});
