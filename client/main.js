/* main.js — Everything 复刻版桌面客户端主进程（Electron）
 * 标准标题栏窗口（对齐 Everything 原版形态）+ 自动拉起本地 Python 引擎
 * + 托盘常驻 + 记住窗口位置尺寸。数据全程本地，不出本机。 */
'use strict';
const { app, BrowserWindow, Tray, Menu, nativeImage } = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const http = require('node:http');
const { spawn } = require('node:child_process');

const APP_ROOT = path.join(__dirname, '..');
const ICON_PATH = path.join(__dirname, 'assets', 'icon.png');
const STATE_PATH = path.join(__dirname, 'window-state.json');
const PORT = process.env.EC_PORT ? Number(process.env.EC_PORT) : 8888;
const URL = 'http://127.0.0.1:' + PORT + '/';

let win = null;
let tray = null;
let engine = null;   // 子进程句柄（仅当我们自己拉起引擎时）

function loadWindowState() {
  try { return JSON.parse(fs.readFileSync(STATE_PATH, 'utf8')); } catch (e) {
    return { width: 1100, height: 720 };
  }
}
function saveWindowState() {
  if (!win) return;
  const b = win.getBounds();
  const state = { x: b.x, y: b.y, width: b.width, height: b.height, maximized: win.isMaximized() };
  try { fs.writeFileSync(STATE_PATH, JSON.stringify(state)); } catch (e) { /* ignore */ }
}

function engineUp(cb) {
  const req = http.get(URL + 'api/stats', { timeout: 2000 }, res => {
    res.resume();
    cb(res.statusCode === 200);
  });
  req.on('error', () => cb(false));
  req.on('timeout', () => { req.destroy(); cb(false); });
}

function startEngine() {
  const args = ['everything.py', '--server', String(PORT), '--etp', '8899', '--ipc',
                '--file-list', path.join('analysis', 'demo-filelist.txt')];
  engine = spawn('python', args, { cwd: APP_ROOT, stdio: 'ignore',
                                   windowsHide: true, detached: false });
  engine.on('exit', () => { engine = null; });
}

function waitForEngine(tries, cb) {
  engineUp(up => {
    if (up) return cb();
    if (tries <= 0) return cb();   // 超时也照常开窗，页面里会显示加载状态
    setTimeout(() => waitForEngine(tries - 1, cb), 2000);
  });
}

function createWindow() {
  const state = loadWindowState();
  win = new BrowserWindow({
    width: Math.max(860, state.width || 1100),
    height: Math.max(560, state.height || 720),
    x: Number.isFinite(state.x) ? state.x : undefined,
    y: Number.isFinite(state.y) ? state.y : undefined,
    minWidth: 860,
    minHeight: 560,
    backgroundColor: '#1e1f22',
    show: false,
    icon: ICON_PATH,
    title: 'Everything 克隆',
    webPreferences: { contextIsolation: true, nodeIntegration: false, spellcheck: false }
  });
  if (state.maximized) win.maximize();
  win.on('close', () => saveWindowState());
  win.loadURL(URL);
  win.once('ready-to-show', () => win.show());
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
}

function createTray() {
  const icon = nativeImage.createFromPath(ICON_PATH).resize({ width: 16, height: 16 });
  tray = new Tray(icon);
  tray.setToolTip('Everything 克隆（本地索引运行中）');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '显示主窗口', click: () => { if (!win) createWindow(); else { win.show(); win.focus(); } } },
    { type: 'separator' },
    { label: '退出', click: () => { app.isQuitting = true; app.quit(); } }
  ]));
  tray.on('double-click', () => { if (win) { win.show(); win.focus(); } });
}

app.whenReady().then(() => {
  engineUp(up => {
    if (!up) startEngine();          // 双击即用：引擎不在就自己拉起（冷启动约 1 分钟建索引）
    waitForEngine(60, createWindow); // 引擎就绪即开窗；超时也开窗
  });
  createTray();
});

app.on('window-all-closed', e => {
  // 关窗驻留托盘（Everything 关闭到托盘的同款行为），托盘菜单里真正退出
  if (!app.isQuitting) e.preventDefault();
});

app.on('before-quit', () => {
  saveWindowState();
  if (tray) tray.destroy();
  if (engine) { try { engine.kill(); } catch (e) { /* ignore */ } }
});

// 截图验收模式：--shot 参数打开窗口并在就绪后截图退出（CI/验收用）
if (process.argv.includes('--shot')) {
  app.disableHardwareAcceleration();
  app.whenReady().then(() => {
    const t = setInterval(() => {
      if (!win) return;
      clearInterval(t);
      setTimeout(() => {
        win.webContents.executeJavaScript(
          "document.getElementById('q').value='readme'; doSearch(); 'ok'"
        ).catch(() => {});
        setTimeout(() => {
          win.webContents.capturePage().then(img => {
            fs.writeFileSync(path.join(__dirname, 'preview.png'), img.toPNG());
            console.log('preview.png saved');
            app.isQuitting = true;
            app.exit(0);
          }).catch(() => { app.isQuitting = true; app.exit(1); });
        }, 2500);
      }, 4000);
    }, 500);
  });
}
