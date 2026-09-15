# 智润 Windows 桌面端

这是智润服务器前端的 Windows 原生桌面容器，使用 Electron 打包为独立窗口，不会跳转系统浏览器。

## 使用

- 安装 `ZhiRun-Setup-1.0.0.exe`，或直接运行 `ZhiRun-1.0.0-portable.exe`。
- 程序打开后加载 `http://8.145.49.45/`，登录、设备绑定和多设备切换沿用服务器页面功能。
- 程序启动或重新获得焦点时检查 `/app/version`。服务器页面更新后会弹窗，点击“立即更新”即可刷新页面，无需重新安装桌面端。

## 构建

需要 Node.js 20+ 和 npm：

```powershell
cd windows-app
npm install
npm run dist
```

产物在 `windows-app/dist/`：

- `ZhiRun-Setup-1.0.0.exe`：Windows 安装包
- `ZhiRun-1.0.0-portable.exe`：免安装便携版

页面、模型和服务器接口的修改只需部署服务器。修改桌面原生行为、权限或窗口逻辑时才需要重新打包安装。
