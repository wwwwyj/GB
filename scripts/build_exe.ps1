## 打包脚本：使用现有 Pytorch 环境中的 PyInstaller 生成单文件 exe
$ErrorActionPreference = "Stop" # 任一步失败就停止，避免生成不完整产物

## Python 路径：固定使用用户现有 Pytorch 环境
$python = "C:\ProgramData\anaconda3\envs\Pytorch\python.exe" # Python 3.11.15 环境

## 检查 PyInstaller 是否已安装；未安装时给出明确提示
& $python -m PyInstaller --version *> $null # 测试 PyInstaller 命令是否可用
if ($LASTEXITCODE -ne 0) { # 上一步返回非 0 表示未安装或不可用
    Write-Host "PyInstaller is not installed in the Pytorch environment." # 提示缺少打包工具
    Write-Host "Install it first with: $python -m pip install pyinstaller" # 给出安装命令
    exit 1 # 退出脚本
}

## 正式打包：把 config.yaml 和 prompts 目录一起带入 exe 运行目录
& $python -m PyInstaller `
    --name standard_extraction `
    --onefile `
    --add-data "config.yaml;." `
    --add-data "prompts;prompts" `
    main.py

## 打包完成提示：最终 exe 位于 dist 目录
Write-Host "Executable created under dist\standard_extraction.exe" # 输出可执行文件位置
