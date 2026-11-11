# ComputerUse-DSH

面向 DeepSeek Harness 的 Windows Computer Use 插件。它让 DSH Agent 通过标准 MCP 调用完成截图、鼠标、键盘、窗口和剪贴板操作，同时提供按次授权、紧急停止、空闲回收和操作位置提示。DSH 安装标识为 `dsh-wdc-computer-use`，便于更新已安装的副本。

> This repository provides a Windows Computer Use integration for DeepSeek Harness. The documentation is Chinese-first; issues and pull requests in English are welcome.

## 功能

- Python MCP 服务提供 18 个底层工具；DSH 标准档默认注册 14 个工具。
- 支持截图、屏幕信息、点击、输入、按键、滚动、窗口枚举与控制、剪贴板读取、等待、停止和恢复。
- 首次工具调用时才启动 Python 服务，空闲 60 秒后自动回收。
- 默认按次申请 DSH 权限；拒绝、取消或超时后不会启动截图或输入服务。
- 工具调用期间显示状态提示；输入动作可显示代理当前位置的小黑色箭头，提示层不抢焦点并尝试从截图中排除。
- DSH “＋”菜单可选择或取消本次草稿的“电脑操作”意图。
- 聊天中不展示桌面截图预览，但模型内部仍能读取截图用于定位。
- 输入结果不确定时停止后续输入，不自动重放点击、按键或文字。
- 与 Codex 没有运行时连接或依赖。

## 仓库结构

```text
ComputerUse-DSH/
├── dsh-wdc-computer-use/   # DSH Node 插件和浏览器客户端策略
├── win-desktop-control/    # Windows Python MCP 服务
├── host-patch/             # DSH 0.2.2-rc.1 宿主补丁清单
├── scripts/                # 客户端、工具清单和 ASAR 构建脚本
├── test_support/           # 隔离测试与桌面边界 Mock
└── docs/                   # 架构、安装和隐私说明
```

## 兼容性

- Windows 10/11 x64
- Python 3.9 或更高版本
- Node.js 22.19 或更高版本
- 已验证宿主：DSH 0.2.2-rc.1

宿主补丁采用文件 SHA-256 和唯一文本锚点双重校验。DSH 更新后，如果任一宿主文件哈希变化，构建脚本会停止；请重新审计补丁，不能强行套用旧版本。

## 安装

当前提供面向熟悉 DSH profile 的手动安装方式。安装前请退出 DSH，并备份 DSH 的 `app.asar`、桌面 profile 的 `package.json` 和同名旧插件目录。

### 1. 安装 Python 服务

```powershell
git clone https://github.com/wjszjm/ComputerUse-DSH.git
cd ComputerUse-DSH
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".\win-desktop-control"
.\.venv\Scripts\python.exe -m pip check
```

复制 `win-desktop-control/config.example.json` 为一个不提交到 Git 的运行配置，例如 `.runtime/config.json`。建议先保留：

```json
{
  "read_only": true,
  "allow_input": false,
  "allow_shell": false,
  "allow_launch": false,
  "save_screenshots": false,
  "audit_log": false
}
```

确认只读观察正常后，再根据自己的权限策略开启输入。

### 2. 安装 DSH 插件

把 `dsh-wdc-computer-use` 复制到当前 DSH 桌面 profile 的 `node_modules/dsh-wdc-computer-use`，并在 profile `package.json` 中：

1. 将 `dsh-wdc-computer-use` 加入 `dsh.profile.bundles`。
2. 在 `dependencies` 中添加指向该目录或仓库子目录的 `file:` 依赖。

在复制后的 `cordis.patch.yml` 中显式配置本机路径：

```yaml
- insert:
    - id: wdc-computer-use
      name: dsh-wdc-computer-use
      config:
        python: 'C:\path\to\ComputerUse-DSH\.venv\Scripts\python.exe'
        cwd: 'C:\path\to\ComputerUse-DSH\.runtime'
        statusPath: 'C:\path\to\ComputerUse-DSH\.runtime\status.json'
        stopFile: 'C:\path\to\ComputerUse-DSH\.runtime\stop.flag'
        shotDir: 'C:\path\to\ComputerUse-DSH\.runtime\shots'
        profile: standard
        permissionMode: ask
        feedbackEnabled: true
        feedbackNativeEnabled: true
```

不要把包含真实绝对路径的本机配置提交到公开仓库。

### 3. 构建宿主补丁

截图预览隐藏和完成后工具详情收起依赖版本绑定的宿主补丁。构建脚本只在指定输出位置生成候选，不会自动覆盖正式 DSH：

```powershell
.\.venv\Scripts\python.exe .\scripts\build-candidate-asar.py `
  --source-asar 'C:\path\to\DSH\resources\app.asar' `
  --manifest '.\host-patch\manifest.json' `
  --output '.\.build\app.candidate.asar' `
  --backup '.\.build\app.original.asar'
```

核对脚本输出和备份哈希后，由你在 DSH 完全退出时替换正式 `app.asar`。完整步骤和回退边界见 [安装说明](docs/INSTALL.md)。

## 权限与隐私

- 默认 `permissionMode: ask`，每次调用由 DSH 发起授权。
- 模型参数不能自行提高权限。
- `run_command` 默认不注册，Python 的 `allow_shell` 默认应保持关闭。
- 截图只保存到明确配置的本地目录，不应提交到 Git。
- 状态提示只接收固定状态和坐标，不接收输入文字、截图内容或任意工具结果。
- 任何截图、日志、会话、账号、Cookie、令牌、用户目录和绝对路径都不属于本仓库内容。

更完整的说明见 [隐私与安全](docs/PRIVACY.md) 和 [SECURITY.md](SECURITY.md)。

## 测试

Python 测试使用自动隔离夹具，屏蔽真实截图、键鼠、剪贴板、应用启动和未批准子进程：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\win-desktop-control[dev]"
pwsh -NoProfile -File .\run-tests.ps1
```

Node 测试也可单独运行：

```powershell
npm test
```

## 已知限制

- 宿主补丁只绑定已验证的 DSH 0.2.2-rc.1。
- 多显示器、混合 DPI、不同截图后端和长时间资源占用需要在目标机器继续验证。
- 提示窗口的截图排除依赖 Windows 和捕获后端能力；系统标志成功不代表每个第三方捕获程序都会忽略它。
- 当前没有自动安装器；手动安装错误可能导致 DSH 无法启动，请务必保留回退文件。

## 贡献

欢迎提交 issue 和 pull request。开始前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。涉及安全问题时请按照 [SECURITY.md](SECURITY.md) 报告，不要公开真实截图、凭据或个人数据。

## 许可证

本项目采用 [MIT License](LICENSE)。DeepSeek Harness 及其商标、程序和资源文件不包含在本仓库中；本项目不是 DeepSeek 官方项目。
