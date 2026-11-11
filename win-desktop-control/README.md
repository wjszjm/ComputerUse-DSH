# win-desktop-control

ComputerUse-DSH 的 Windows Python MCP 服务。它提供截图、鼠标、键盘、窗口、剪贴板、等待、停止和恢复等 18 个底层工具。

## 安装

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\win-desktop-control"
.\.venv\Scripts\python.exe -m pip check
```

## 安全起步

把 `config.example.json` 复制到仓库外的运行目录。示例默认只读、禁止输入、禁止 shell、禁止启动应用且不记录审计正文。确认截图和屏幕信息符合预期后，再按部署策略逐项开启能力。

```powershell
.\.venv\Scripts\python.exe -m wdc selftest --safe
.\.venv\Scripts\python.exe -m wdc serve
```

DSH 集成、权限模型和截图生命周期见仓库根目录 [README](../README.md)。
