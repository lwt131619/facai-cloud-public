# 打包 FACAI 桌面客户端：安装依赖 + 产出 Windows 安装包
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)

Write-Host "[1/3] 安装依赖（npm 国内镜像）..."
npm install --registry=https://registry.npmmirror.com

Write-Host "[2/3] 设置 Electron 二进制镜像..."
$env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
$env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"

Write-Host "[3/3] 打包 Windows x64 安装包..."
npm run dist

Write-Host "完成。产物："
Get-ChildItem dist -Filter "*.exe" | ForEach-Object { Write-Host "  " $_.FullName }
