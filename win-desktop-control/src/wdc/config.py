"""配置加载：config.json + 环境变量覆盖。

优先级：环境变量 > 配置文件 > 内置默认值。
配置文件查找顺序：--config 参数 > 环境变量 WDC_CONFIG > 包目录同级 config.json
                    > %APPDATA%/WinDesktopControl/config.json
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "WinDesktopControl"


def user_config_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP_NAME


@dataclass
class Config:
    # ---- 截图 ----
    max_image_edge: int = 1280          # 返回图片最长边，超出则等比缩小以省 token
                                        # 1280 约为 1600 的 64% 像素量，文字仍可读
    default_grid: bool = True           # 是否在截图上叠加坐标网格
    grid_step: int = 100                # 网格间距（截图像素）
    image_format: str = "png"           # png | jpeg
    jpeg_quality: int = 80
    save_screenshots: bool = False
    screenshot_dir: str = ""

    # ---- 输入 ----
    allow_input: bool = True            # 总开关：False 时仅可截屏/查询
    read_only: bool = False             # 强制只读（优先级最高）
    allow_shell: bool = False           # 是否允许 run_command 工具
    allow_launch: bool = True           # 是否允许 launch_app 工具
    blocked_shortcuts: list[str] = field(
        default_factory=lambda: [
            "win+l",            # 锁屏后 Agent 无法自行恢复
            "ctrl+alt+delete",  # 系统保留，模拟无效
            "ctrl+alt+del",
        ]
    )
    max_actions_per_second: float = 20.0
    move_duration: float = 0.15
    type_interval: float = 0.02

    # ---- 会话 ----
    audit_log: bool = True
    audit_path: str = ""
    record_text_preview: bool = False   # 审计日志是否记录输入文本的前 60 字（默认关闭，防泄露密码）
    record_images: bool = False         # 审计日志里是否包含截图的 base64（默认关闭）

    # ---- 服务 ----
    server_name: str = "win-desktop-control"
    host_whitelist: list[str] = field(default_factory=list)  # 预留：非 stdio 传输时的来源限制

    # ------------------------------------------------------------------ #
    def __post_init__(self) -> None:
        if self.read_only:
            self.allow_input = False
            self.allow_shell = False
            self.allow_launch = False
        self.blocked_shortcuts = [s.lower().replace(" ", "") for s in self.blocked_shortcuts]
        self.max_actions_per_second = max(1.0, float(self.max_actions_per_second))
        if not self.screenshot_dir:
            self.screenshot_dir = str(user_config_dir() / "shots")
        if not self.audit_path:
            self.audit_path = str(user_config_dir() / "audit.log")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _apply_env(cfg: Config) -> Config:
    mapping = {
        "WDC_READ_ONLY": ("read_only", bool),
        "WDC_ALLOW_INPUT": ("allow_input", bool),
        "WDC_ALLOW_SHELL": ("allow_shell", bool),
        "WDC_ALLOW_LAUNCH": ("allow_launch", bool),
        "WDC_MAX_IMAGE_EDGE": ("max_image_edge", int),
        "WDC_DEFAULT_GRID": ("default_grid", bool),
        "WDC_AUDIT": ("audit_log", bool),
        "WDC_SAVE_SCREENSHOTS": ("save_screenshots", bool),
        "WDC_SCREENSHOT_DIR": ("screenshot_dir", str),
        "WDC_AUDIT_PATH": ("audit_path", str),
    }
    for env_key, (attr, caster) in mapping.items():
        raw = os.environ.get(env_key)
        if raw is None or raw == "":
            continue
        if caster is bool:
            value: Any = raw.strip().lower() in {"1", "true", "yes", "on", "y"}
        elif caster is int:
            try:
                value = int(raw)
            except ValueError:
                continue
        else:
            value = raw
        setattr(cfg, attr, value)
    if cfg.read_only:  # 环境变量把 read_only 打开时，同步关掉所有写操作
        cfg.allow_input = False
        cfg.allow_shell = False
        cfg.allow_launch = False
    return cfg


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    env_path = os.environ.get("WDC_CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path.cwd() / "config.json")
    candidates.append(Path(__file__).resolve().parent.parent.parent / "config.json")
    candidates.append(user_config_dir() / "config.json")

    data: dict[str, Any] = {}
    for candidate in candidates:
        try:
            if candidate.is_file():
                with candidate.open("r", encoding="utf-8-sig") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    data = loaded
                    break
        except (OSError, json.JSONDecodeError):
            continue

    known = {f for f in Config.__dataclass_fields__}  # type: ignore[attr-defined]
    clean = {k: v for k, v in data.items() if k in known}
    return _apply_env(Config(**clean))
