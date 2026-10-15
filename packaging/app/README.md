# 打包 FACAI 桌面客户端（Windows）

把 whstapp.lol 打成独立安装包（.exe），发给客户双击即用，无需记网址。

## 一步打包（国内网络已配镜像加速）

```powershell
cd packaging\app
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

产物在 `packaging\app\dist\FACAI Setup 1.0.0.exe`。

## 手动步骤（等价）

```powershell
npm install --registry=https://registry.npmmirror.com
# electron 二进制用国内镜像：
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
npm run dist
```

## 说明

- 客户端 = Electron 壳 + 打开 `https://whstapp.lol`，所有功能与网页版一致
- 外链自动跳系统浏览器；应用内永远留在工作台
- 版本更新：直接重跑 `build.ps1`（版本号改 `package.json` 的 `version`）

## 另一条更轻的「打包」路径（已上线）

whstapp.lol 本身就是 **PWA（可安装应用）**：
- 手机浏览器打开 → 添加到主屏幕 = 全屏 App（含图标）
- 电脑 Chrome/Edge 地址栏右侧「安装」按钮 = 独立窗口应用
- 零打包成本，与网页版同步更新
