# ComputerUse-DSH

DeepSeek Harness 侧的 Node 插件。它负责静态注册 `wdc_*` 工具、按需启动 Python MCP 服务、执行权限检查、处理停止与不确定输入结果，并向浏览器客户端提供“电脑操作”菜单和截图呈现策略。

DSH 安装标识为 `dsh-wdc-computer-use`。安装时在 `cordis.patch.yml` 中填写 Python、运行目录、状态文件和停止文件。截图使用 DSH 原生附件服务。

发生故障时，插件在自身目录创建 `failures.log`。每行记录时间、失败阶段、错误码和适用的工具名。正常运行、用户拒绝授权和主动取消不会写入。日志达到 256 KiB 上限时从当前故障重新记录。

版本核对可运行 `node lib/check-install.js --profile <DSH profile 目录> --status <statusPath>`。命令以 JSON 显示安装源、磁盘副本、运行状态上报的版本，以及最近一次 Python MCP 握手和工具清单检查结果。没有调用过电脑工具时，Python 显示为未检查。

安装方式、升级兼容性和权限设置见仓库根目录 [README](../README.md) 与 [安装说明](../docs/INSTALL.md)。

## 会话权限

`permissionMode: ask` 会遵循 DSH 的自动审查与完全权限预设。其他可询问预设在首次观察、首次输入时分别提示，批准后记住同类权限到当前会话。宿主不提供权限预设服务时继续逐次询问。通过 `rememberSessionApprovals: false` 可以在新版宿主保留逐次询问。

在 DSH 对话框提交 `/wdc-access` 可查看插件模式和宿主预设，提交 `/wdc-access revoke` 可撤销本会话授权并取消在途操作。还可使用 `/wdc-access read-only`、`/wdc-access disabled`、`/wdc-access stop` 和 `/wdc-access ask`。这些命令走 DSH 用户输入入口。
