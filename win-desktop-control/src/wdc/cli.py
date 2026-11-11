"""命令行入口：wdc serve / selftest / tools / shot / config / version。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__, capture, platform_win
from .config import Config, load_config, user_config_dir


def _setup_console() -> None:
    """Windows 控制台默认 GBK，中文会乱码，尽量切到 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def _print(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2) if not isinstance(obj, str) else obj)


# --------------------------------------------------------------------------- #
# 子命令
# --------------------------------------------------------------------------- #
def cmd_serve(args: argparse.Namespace) -> int:
    from .mcp_server import run_stdio

    cfg = load_config(args.config)
    print(
        f"[wdc] v{__version__} MCP server (stdio) | "
        f"read_only={cfg.read_only} allow_input={cfg.allow_input} "
        f"allow_shell={cfg.allow_shell} | python={sys.executable}",
        file=sys.stderr, flush=True,
    )
    run_stdio(cfg)
    return 0


def cmd_selftest(args: argparse.Namespace) -> int:
    from . import selftest

    cfg = load_config(args.config)
    checks = selftest.run_all(cfg, safe=args.safe, include_protocol=not args.no_protocol)
    counts = selftest.summarize(checks)
    print(selftest.format_text(checks, counts))
    selftest.write_reports(checks, counts, args.json, args.markdown)
    if args.json:
        print(f"JSON 报告：{os.path.abspath(args.json)}")
    if args.markdown:
        print(f"Markdown 报告：{os.path.abspath(args.markdown)}")
    return 0 if counts["fail"] == 0 else 1


def cmd_tools(args: argparse.Namespace) -> int:
    from . import schemas

    if args.format == "openai":
        print(schemas.dumps())
    elif args.format == "names":
        print("\n".join(schemas.names()))
    else:
        lines = ["| 工具 | 说明 |", "| --- | --- |"]
        for spec in schemas.tool_specs():
            lines.append(f"| `{spec['name']}` | {spec['description']} |")
        print("\n".join(lines))
    return 0


def cmd_shot(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    platform_win.setup_dpi_awareness()
    try:
        frame = capture.capture(
            monitor=args.monitor, region=args.region, scale=args.scale,
            grid=not args.no_grid, grid_step=cfg.grid_step, max_edge=cfg.max_image_edge,
        )
    except Exception as exc:
        print(f"截屏失败：{exc}", file=sys.stderr)
        return 1
    target = args.output or f"screenshot-{__import__('time').strftime('%Y%m%d-%H%M%S')}.png"
    path = capture.save(frame, target, fmt=cfg.image_format, quality=cfg.jpeg_quality)
    meta = frame.to_dict()
    meta["saved_to"] = str(Path(path).resolve())
    _print(meta)
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    if args.init:
        target = Path(args.init)
        if target.exists() and not args.force:
            print(f"{target} 已存在，加 --force 覆盖", file=sys.stderr)
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(Config().to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已生成默认配置：{target.resolve()}", file=sys.stderr)
        return 0
    cfg = load_config(args.config)
    _print({"loaded": cfg.to_dict(), "user_dir": str(user_config_dir())})
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """打开记事本 → 输入一段中文 → 截图 → 关闭，用于验证端到端效果。"""
    from .tools import ComputerUse

    cfg = load_config(args.config)
    if cfg.read_only or not cfg.allow_input:
        print("当前为只读模式，无法运行演示。", file=sys.stderr)
        return 1
    app = ComputerUse(cfg)
    steps = [
        ("启动记事本", lambda: app.launch_app("notepad", wait=1.5)),
        ("激活窗口", lambda: app.activate_window(title="记事本") if platform_win.IS_WINDOWS else None),
        ("输入中文", lambda: app.type_text("Win Desktop Control 演示：中文输入正常 ✅", press_enter=True)),
        ("截图", lambda: app.screenshot()),
        ("紧急停止", lambda: app.emergency_stop("demo finished")),
    ]
    for title, action in steps:
        result = action()
        if result is None:
            continue
        print(f"[{'OK' if result.ok else 'FAIL'}] {title}: {result.text()[:300]}")
        if not result.ok and result.code not in {"unsupported"}:
            return 1
    print("演示完成。截图数据已生成（本次未落盘，可加 config.save_screenshots 保存）。")
    return 0


def cmd_call(args: argparse.Namespace) -> int:
    """单次工具调用：把 wdc 当成本地 CLI 桥，任何语言都能通过子进程驱动桌面。"""
    from .tools import ComputerUse

    app = ComputerUse(load_config(args.config))
    method = getattr(app, args.tool, None)
    if not callable(method) or args.tool.startswith("_"):
        available = sorted(
            name for name in dir(app)
            if not name.startswith("_") and callable(getattr(app, name))
            and name not in {"diagnostics"}
        )
        print(f"未知工具：{args.tool}\n可用：{', '.join(available)}", file=sys.stderr)
        return 2
    try:
        params = json.loads(args.args or "{}")
    except json.JSONDecodeError as exc:
        print(f"--args 不是合法 JSON：{exc}", file=sys.stderr)
        return 2
    if not isinstance(params, dict):
        print("--args 必须是 JSON 对象，例如 '{\"x\": 100, \"y\": 200}'", file=sys.stderr)
        return 2

    result = method(**params)
    print(result.text())
    if result.image and args.out:
        Path(args.out).write_bytes(result.image)
        print(f"图片已保存：{Path(args.out).resolve()}", file=sys.stderr)
    return 0 if result.ok else 1


def cmd_version(_: argparse.Namespace) -> int:
    print(f"wdc {__version__}")
    print(f"python {sys.version.split()[0]} ({sys.executable})")
    print(f"platform {sys.platform}")
    return 0


# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wdc",
        description="Windows 桌面操控 MCP 插件（Computer Use 风格）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  wdc serve                 # 以 stdio 方式启动 MCP Server\n"
            "  wdc selftest --safe       # 只读自检\n"
            "  wdc shot -o screen.png    # 截一张图看看效果\n"
            "  wdc tools --format openai # 导出 OpenAI function-calling schema\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"wdc {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_serve = sub.add_parser("serve", help="启动 MCP Server（stdio）")
    p_serve.add_argument("--config", help="配置文件路径")
    p_serve.set_defaults(func=cmd_serve)

    p_test = sub.add_parser("selftest", help="兼容性自检")
    p_test.add_argument("--config", help="配置文件路径")
    p_test.add_argument("--safe", action="store_true", help="只读模式，不移动鼠标/不改剪贴板")
    p_test.add_argument("--no-protocol", action="store_true", help="跳过 MCP 协议握手测试")
    p_test.add_argument("--json", help="输出 JSON 报告到指定路径")
    p_test.add_argument("--markdown", help="输出 Markdown 报告到指定路径")
    p_test.set_defaults(func=cmd_selftest)

    p_tools = sub.add_parser("tools", help="列出工具 / 导出 schema")
    p_tools.add_argument("--format", choices=["openai", "names", "markdown"], default="markdown")
    p_tools.set_defaults(func=cmd_tools)

    p_shot = sub.add_parser("shot", help="截屏保存为文件")
    p_shot.add_argument("--config", help="配置文件路径")
    p_shot.add_argument("-o", "--output", help="输出文件路径")
    p_shot.add_argument("--monitor", default="primary", help="primary / all / 显示器序号")
    p_shot.add_argument("--region", type=int, nargs=4, metavar=("X", "Y", "W", "H"))
    p_shot.add_argument("--scale", type=float, default=0.0)
    p_shot.add_argument("--no-grid", action="store_true", help="不叠加坐标网格")
    p_shot.set_defaults(func=cmd_shot)

    p_cfg = sub.add_parser("config", help="查看 / 生成配置")
    p_cfg.add_argument("--config", help="配置文件路径")
    p_cfg.add_argument("--init", nargs="?", const="config.json", help="生成默认配置文件")
    p_cfg.add_argument("--force", action="store_true")
    p_cfg.set_defaults(func=cmd_config)

    p_demo = sub.add_parser("demo", help="端到端演示（记事本 + 中文输入 + 截图）")
    p_demo.add_argument("--config", help="配置文件路径")
    p_demo.set_defaults(func=cmd_demo)

    p_call = sub.add_parser("call", help="单次调用某个工具（CLI 桥）")
    p_call.add_argument("tool", help="工具名，如 screenshot / click / type_text")
    p_call.add_argument("--args", default="{}", help='JSON 参数，如 \'{"x":100,"y":200}\'')
    p_call.add_argument("--out", help="截图类工具的图片保存路径")
    p_call.add_argument("--config", help="配置文件路径")
    p_call.set_defaults(func=cmd_call)

    p_ver = sub.add_parser("version", help="显示版本")
    p_ver.set_defaults(func=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    _setup_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
