# ComputerUse-DSH

DeepSeek Harness 侧的 Node 插件。它负责静态注册 `wdc_*` 工具、按需启动 Python MCP 服务、执行权限检查、处理停止与不确定输入结果，并向浏览器客户端提供“电脑操作”菜单和截图呈现策略。

DSH 安装标识为 `dsh-wdc-computer-use`。安装时在 `cordis.patch.yml` 中填写 Python、运行目录、状态文件、停止文件和截图目录。

完整安装方式、宿主补丁和安全边界见仓库根目录 [README](../README.md) 与 [安装说明](../docs/INSTALL.md)。
