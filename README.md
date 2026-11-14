# ComputerUse-DSH

面向 DeepSeek Harness 的 Windows Computer Use 插件。它让 DSH Agent 通过标准 MCP 调用完成截图、鼠标、键盘、窗口和剪贴板操作，同时提供会话授权、紧急停止、空闲回收和操作位置提示。DSH 安装标识为 `dsh-wdc-computer-use`，便于更新已安装的副本。

> This repository provides a Windows Computer Use integration for DeepSeek Harness. The documentation is Chinese-first; issues and pull requests in English are welcome.

## 功能

- Python MCP 服务提供 24 个底层工具，DSH 标准档默认注册 14 个工具。窗口截图、UIA、OCR 和批量窗口操作按需启用。
- 支持截图、屏幕信息、点击、输入、按键、滚动、窗口枚举与控制、剪贴板读取、等待、停止和恢复。
- 首次工具调用时才启动 Python 服务，空闲 60 秒后自动回收。
- 首次授权后在当前会话记住同类电脑操作权限。DSH 自动权限审查放行后直接执行。
- 工具调用期间显示状态提示；输入动作可显示代理当前位置的小黑色箭头，提示层不抢焦点并尝试从截图中排除。
- DSH “＋”菜单可选择或取消本次草稿的“电脑操作”意图。
- 聊天中不展示桌面截图预览，但模型内部仍能读取截图用于定位。
- 输入结果不确定时停止后续输入，不自动重放点击、按键或文字。

## 2.2 设置与桌面工具

打开 DSH **设置 → 电脑操控**，可选择工具档位、启用可选工具、调整截图缩放、桌面提示和紧急停止快捷键。插件详情页也提供同一表单。安装 UIA 和 OCR 可选依赖后，可以按窗口句柄和 PID 读取控件、直接调用控件动作，或在有效截图上使用 Windows 本机中英文 OCR。定位工具优先查 UIA，需要回退时使用该窗口的 OCR。找到目标后，通过单独的输入工具操作。

2.2 精简了模型看到的英文工具定义，窗口列表、UIA 和 OCR 默认返回紧凑结果，原有详情可用 `detail: "full"` 获取。窗口截图可传 `crop: [x,y,width,height]` 指定窗口内的物理像素区域，`scale: 1` 保留局部原尺寸。条件等待能在指定窗口可见或控件可用时立即返回，控件等待需要启用「读取控件」。窗口批次支持等待目标、激活及独立确认前台，任何一步失败都会停止。

「上下文优化」的两个开关默认关闭，保存后可独立启用。**按需加载高级工具**保留基础工具和截图能力，`wdc_load_tools` 可在当前会话加载用户已经启用的高级工具。**精简已替代的历史观察**只压缩已完成轮次中被同目标新观察替代的插件结果，每个目标至少保留两份最近观察。原始记录与附件仍在 DSH，关闭后停止后续压缩。旧宿主缺少对应接口时继续使用固定工具档位和原有历史行为。

窗口截图默认采集前台窗口的屏幕范围。2.0.1 起，显式传入 `background: true` 可按窗口句柄和 PID 采集被遮挡的窗口，或读取及操作其 UIA 控件。后台帧可用于 OCR，坐标输入需要激活目标并重新截图。最小化、受截图保护或未提供有效画面的窗口会返回错误。部分应用的后台画面可能不完整，UIA 提供程序也可能自行改变焦点，工具会报告调用返回时检测到的焦点变化。

最多六步的窗口批次只接受有系统状态读回的窗口操作。步骤失败时返回已验证的步骤和失败位置。

Esc 或 Ctrl + Shift + F12 默认关闭。启用后只在 Python 输入调用的检查点检测，不拦截用户按键。Windows 或控件提供程序已经开始执行的单次调用需要返回后才能停止。

本次升级对照了 [dsh-computer-use-win](https://github.com/Yu-tao-Li/dsh-computer-use-win/tree/92d6b35) 的控件定位、批次结果和 DSH 设置设计。当前实现继续使用 Python MCP 和 DSH 原生图片附件。

| 对照项 | ComputerUse-DSH 2.0 的实现 |
| --- | --- |
| 控件定位 | 用窗口句柄与 PID 限定 UIA 查询，按控件身份调用语义动作。定位失败时可在当前窗口截图上使用 Windows OCR。 |
| 批次操作 | 同一窗口最多六步。每步读取系统状态确认结果，失败时返回已完成步骤和失败位置。 |
| DSH 设置 | 使用宿主设置侧栏的原生插槽和鼠标图标，保存后更新工具注册。权限审批和截图附件继续由 DSH 管理。 |

## 仓库结构

```text
ComputerUse-DSH/
├── dsh-wdc-computer-use/   # DSH Node 插件和浏览器客户端策略
├── win-desktop-control/    # Windows Python MCP 服务
├── scripts/                # 客户端和工具清单构建脚本
├── test_support/           # 隔离测试与桌面边界 Mock
└── docs/                   # 架构、安装和隐私说明
```

## 兼容性

- Windows 10/11 x64
- Python 3.9 或更高版本
- Node.js 22.19 或更高版本
- 已验证宿主：DSH 0.2.2-rc.1 至 rc.3。2.2.1 在 RC3 桌面配置验证了历史精简与回放。

从 1.1.0 起，安装和更新插件只涉及 DSH 用户 profile。DSH 更新替换 `app.asar` 时，插件通过宿主的工具、附件和客户端插槽接口重新加载。

## 安装

可以通过 `npm run manage-install` 预演安装、升级或更新后的修复。准备 Python 环境后，按[安装说明](docs/INSTALL.md)指定 DSH Profile、运行目录和 Python 解释器。

当前提供面向熟悉 DSH profile 的手动安装方式。安装前请退出 DSH，并备份桌面 profile 的 `package.json` 和同名旧插件目录。

### 1. 安装 Python 服务

```powershell
git clone https://github.com/wjszjm/ComputerUse-DSH.git
cd ComputerUse-DSH
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".\win-desktop-control"
.\.venv\Scripts\python.exe -m pip check
```

需要 UIA 和本机 OCR 时安装 `".\win-desktop-control[uia,ocr]"`。OCR 使用系统已安装的识别语言。

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
        profile: standard
        permissionMode: ask
        feedbackEnabled: true
        feedbackNativeEnabled: true
```

不要把包含真实绝对路径的本机配置提交到公开仓库。

### 3. 启动与升级检查

启动 DSH 后检查插件是否启用，以及“＋”菜单是否有“电脑操作”。旧版已修改的 `app.asar` 可由 DSH 官方更新替换。安装步骤、版本要求和旧截图的显示边界见 [安装说明](docs/INSTALL.md)。

## 权限与隐私

默认 `permissionMode: ask`。在支持权限预设的 DSH 中，首次观察与首次输入各询问一次。观察包括截图、屏幕和窗口查询、剪贴板读取与等待。输入和控制包括点击、键盘、滚动和窗口操作。授权只记在当前会话内存中，切换预设、撤销、停止或卸载插件后清除相应授权。显式开启的应用启动与 shell 在人工审批模式下逐次询问。

| DSH 当前预设 | 插件行为 |
| --- | --- |
| 自动权限审查 | 由宿主执行审查，按宿主放行、拒绝或转人工审批的结果执行。 |
| 完全权限 | 确认宿主处于完全权限预设后，插件直接执行。 |
| 工作区内修改 | 首次观察、首次输入与控制分别申请授权。 |
| 自定义 | 遵循宿主只读和审批策略。允许询问时按会话记住同类授权。 |

在 DSH 输入框中提交 `/wdc-access revoke` 可撤销插件授权并取消在途操作。用 `/wdc-access read-only` 禁止输入，用 `/wdc-access disabled` 禁止新的电脑操作，用 `/wdc-access stop` 紧急停止。用 `/wdc-access ask` 恢复默认授权流程，宿主预设仍然生效。切换宿主预设也会撤销已记住的插件授权。较旧的宿主缺少权限预设服务时保留逐次审批。将 `rememberSessionApprovals` 设为 `false` 也可保留逐次审批。

- 模型参数不能自行提高权限。
- `run_command` 默认不注册，Python 的 `allow_shell` 默认应保持关闭。
- 截图保存在 DSH 的会话附件存储中，聊天画廊按来源隐藏插件截图。所选模型服务可能收到图片。
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

- 截图要求 DSH 提供原生附件服务，所选模型支持图像输入。将来的 DSH 如果更改插件接口，需要更新对应适配。
- 多显示器、混合 DPI、不同截图后端和长时间资源占用需要在目标机器继续验证。
- 提示窗口的截图排除依赖 Windows 和捕获后端能力；系统标志成功不代表每个第三方捕获程序都会忽略它。
- 若未来 DSH 更新改变插件 API，可能需要升级插件。Profile 文件缺失时可用安装器的 `repair` 模式恢复已保存的配置和注册。

## 贡献

欢迎提交 issue 和 pull request。开始前请阅读 [CONTRIBUTING.md](CONTRIBUTING.md)。涉及安全问题时请按照 [SECURITY.md](SECURITY.md) 报告，不要公开真实截图、凭据或个人数据。

## 许可证

本项目采用 [MIT License](LICENSE)。DeepSeek Harness 及其商标、程序和资源文件不包含在本仓库中；本项目不是 DeepSeek 官方项目。
