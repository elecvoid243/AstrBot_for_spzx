"""AstrBot 更新配置管理器.

管理独立的更新配置文件 data/update_config.json，支持环境变量覆盖。
优先级: 环境变量 > 配置文件 > 硬编码默认值.

作者: AstrBot Agent
时间: 2026-05-25
"""

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from astrbot.core.utils.astrbot_path import get_astrbot_data_path

logger = logging.getLogger("astrbot")

UPDATE_CONFIG_PATH = os.path.join(get_astrbot_data_path(), "update_config.json")

# Environment variable name mapping.
ENV_VAR_MAP = {
    "core_update.release_api_url": "ASTRBOT_CORE_RELEASE_API_URL",
    "core_update.github_archive_url_template": "ASTRBOT_GITHUB_ARCHIVE_URL",
    "core_update.package_base_url": "ASTRBOT_CORE_PACKAGE_BASE_URL",
    "dashboard_update.registry_url_template": "ASTRBOT_DASHBOARD_REGISTRY_URL",
    "dashboard_update.github_release_api_url": "ASTRBOT_DASHBOARD_GITHUB_RELEASE_API_URL",
    "dashboard_update.github_release_download_url_template": "ASTRBOT_DASHBOARD_GITHUB_RELEASE_DOWNLOAD_URL",
    "dashboard_update.harbour_url_template": "ASTRBOT_DASHBOARD_HARBOUR_URL",
    "proxy.enabled": "ASTRBOT_UPDATE_PROXY_ENABLED",
    "proxy.url": "ASTRBOT_UPDATE_PROXY_URL",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "update_config_version": 1,
    "core_update": {
        "release_api_url": "https://api.soulter.top/releases",
        "github_archive_url_template": "https://github.com/AstrBotDevs/AstrBot/archive/{version}.zip",
        "package_base_url": "https://astrbot-registry.soulter.top/download/astrbot-core",
    },
    "dashboard_update": {
        "registry_url_template": "https://astrbot-registry.soulter.top/download/astrbot-dashboard/{version}/dist.zip",
        "github_release_api_url": "https://api.github.com/repos/AstrBotDevs/AstrBot/releases/latest",
        "github_release_download_url_template": "https://github.com/AstrBotDevs/AstrBot/releases/download/{tag}/AstrBot-{tag}-dashboard.zip",
        "harbour_url_template": "https://github.com/AstrBotDevs/astrbot-release-harbour/releases/download/release-{version}/dist.zip",
    },
    "proxy": {
        "enabled": False,
        "url": "",
    },
}

# 设置页可编辑的更新源: 接口字段名 -> 配置文件路径。
# 只暴露主下载源；其余模板仍可在 update_config.json 中手工修改。
EDITABLE_UPDATE_SOURCES: dict[str, str] = {
    "core_release_api_url": "core_update.release_api_url",
    "core_package_base_url": "core_update.package_base_url",
    "dashboard_registry_url_template": "dashboard_update.registry_url_template",
}

# 各字段必须保留的占位符。缺失时 str.format 会在下载/检查更新阶段抛 KeyError，
# 因此在保存前就拦下来。
REQUIRED_PLACEHOLDERS: dict[str, tuple[str, ...]] = {
    "core_release_api_url": (),
    "core_package_base_url": (),
    "dashboard_registry_url_template": ("version",),
}


def validate_update_source(field: str, value: str) -> str | None:
    """校验设置页提交的单个更新源取值。

    Args:
        field: 接口字段名，必须存在于 EDITABLE_UPDATE_SOURCES。
        value: 用户填写的地址或模板。

    Returns:
        合法时返回 None，否则返回可直接展示给用户的错误描述。
    """
    if field not in EDITABLE_UPDATE_SOURCES:
        return f"未知的更新源字段: {field}"

    candidate = (value or "").strip()
    if not candidate:
        return "地址不能为空，如需恢复内置地址请点击“恢复默认值”。"
    if any(char.isspace() for char in candidate):
        return "地址中不能包含空白字符。"

    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return "地址必须是 http 或 https 开头的完整 URL。"

    required = REQUIRED_PLACEHOLDERS[field]
    for placeholder in required:
        if f"{{{placeholder}}}" not in candidate:
            return f"该模板必须保留 {{{placeholder}}} 占位符。"

    # 试跑一次 format，捕获 {} / {0} / 未闭合花括号等非法写法。
    try:
        candidate.format(**dict.fromkeys(required, "placeholder"))
    except (KeyError, IndexError, ValueError):
        if required:
            return "模板中含有无法解析的占位符，请检查花括号用法。"
        return "该地址不支持占位符，请填写不含花括号的固定地址。"
    return None


class UpdateConfig(dict):
    """更新配置管理类.

    从独立的 JSON 配置文件加载更新相关配置，支持环境变量覆盖。
    继承自 dict，支持字典式访问。
    """

    config_path: str

    def __init__(self, config_path: str = UPDATE_CONFIG_PATH) -> None:
        super().__init__()
        object.__setattr__(self, "config_path", config_path)

        config = self._load_config()
        self.update(config)

    def _load_config(self) -> dict[str, Any]:
        """加载配置文件，不存在或无效时返回默认值."""
        config_path = Path(self.config_path)

        if not config_path.exists():
            logger.info("更新配置文件不存在，创建默认配置: %s", self.config_path)
            self._save_default_config(config_path)
            return DEFAULT_CONFIG.copy()

        try:
            with open(config_path, encoding="utf-8-sig") as f:
                content = f.read()
                if content.startswith("\ufeff"):
                    content = content[1:]
                user_config = json.loads(content)

            # 合并用户配置和默认配置
            merged = self._merge_config(DEFAULT_CONFIG.copy(), user_config)
            return merged

        except json.JSONDecodeError as e:
            logger.warning(
                "更新配置文件格式错误 (%s)，使用默认配置: %s", e, self.config_path
            )
            return DEFAULT_CONFIG.copy()
        except Exception as e:
            logger.warning(
                "读取更新配置文件失败 (%s)，使用默认配置: %s", e, self.config_path
            )
            return DEFAULT_CONFIG.copy()

    @staticmethod
    def _merge_config(default: dict, user: dict) -> dict:
        """递归合并配置，用户配置覆盖默认值."""
        result = default.copy()
        for key, value in user.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = UpdateConfig._merge_config(result[key], value)
            else:
                result[key] = value
        return result

    def _save_default_config(self, config_path: Path) -> None:
        """保存默认配置到文件."""
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_CONFIG, f, indent=4, ensure_ascii=False)

    def _get_value(self, path: str, default: Any = None) -> Any:
        """通过路径获取配置值，支持环境变量覆盖.

        Args:
            path: 配置路径，如 "core_update.release_api_url"
            default: 默认值

        Returns:
            配置值，环境变量存在时返回环境变量值
        """
        # 检查环境变量
        env_var = ENV_VAR_MAP.get(path)
        if env_var and env_var in os.environ:
            env_value = os.environ[env_var]
            # 布尔值转换
            if isinstance(default, bool):
                return env_value.lower() in ("true", "1", "yes", "on")
            return env_value

        # 从配置字典获取
        keys = path.split(".")
        value = dict(self)
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value if value is not None else default

    # --- Core Update URLs ---

    def get_core_release_api_url(self) -> str:
        """获取 Core 版本检查 API 地址."""
        return self._get_value(
            "core_update.release_api_url",
            DEFAULT_CONFIG["core_update"]["release_api_url"],
        )

    def get_github_archive_url(self, version: str) -> str:
        """获取 GitHub 归档下载地址.

        Args:
            version: 版本号或 commit hash
        """
        template = self._get_value(
            "core_update.github_archive_url_template",
            DEFAULT_CONFIG["core_update"]["github_archive_url_template"],
        )
        return template.format(version=version)

    def get_core_package_base_url(self) -> str:
        """Get the base URL prefix for hosted core package downloads.

        The final download URL is built as
        ``f"{base_url.rstrip('/')}/{version}/source.zip"``.

        Returns:
            Base URL string. Empty string when the hosted package download
            is disabled via configuration.
        """
        return self._get_value(
            "core_update.package_base_url",
            DEFAULT_CONFIG["core_update"]["package_base_url"],
        )

    # --- Dashboard Update URLs ---

    def get_dashboard_registry_url(self, version: str) -> str:
        """获取 Dashboard Registry 下载地址.

        Args:
            version: 版本号或 "latest"
        """
        template = self._get_value(
            "dashboard_update.registry_url_template",
            DEFAULT_CONFIG["dashboard_update"]["registry_url_template"],
        )
        return template.format(version=version)

    def get_dashboard_github_release_api_url(self) -> str:
        """获取 Dashboard GitHub Release API 地址."""
        return self._get_value(
            "dashboard_update.github_release_api_url",
            DEFAULT_CONFIG["dashboard_update"]["github_release_api_url"],
        )

    def get_dashboard_github_release_download_url(self, tag: str) -> str:
        """获取 Dashboard GitHub Release 下载地址.

        Args:
            tag: 版本标签，如 "v4.25.1"
        """
        template = self._get_value(
            "dashboard_update.github_release_download_url_template",
            DEFAULT_CONFIG["dashboard_update"]["github_release_download_url_template"],
        )
        return template.format(tag=tag)

    def get_dashboard_harbour_url(self, version: str) -> str:
        """获取 Dashboard Harbour 下载地址.

        Args:
            version: 版本号或 commit hash
        """
        template = self._get_value(
            "dashboard_update.harbour_url_template",
            DEFAULT_CONFIG["dashboard_update"]["harbour_url_template"],
        )
        return template.format(version=version)

    # --- Proxy ---

    def is_proxy_enabled(self) -> bool:
        """是否启用代理."""
        return self._get_value("proxy.enabled", DEFAULT_CONFIG["proxy"]["enabled"])

    def get_proxy_url(self) -> str:
        """获取代理地址."""
        return self._get_value("proxy.url", DEFAULT_CONFIG["proxy"]["url"])

    def get_effective_proxy_url(self) -> str:
        """获取实际生效的代理地址（考虑 enabled 状态）."""
        if self.is_proxy_enabled():
            return self.get_proxy_url()
        return ""

    # --- 设置页可编辑的更新源 ---

    def get_update_sources(self) -> dict[str, dict[str, Any]]:
        """描述设置页可编辑的更新源字段。

        环境变量优先级高于配置文件，被环境变量覆盖的字段标记为 env_locked，
        前端据此提示用户"改了也不生效"，避免出现难以排查的无效修改。

        Returns:
            字段名到 {value, default, env_var, env_locked} 的映射。
        """
        sources: dict[str, dict[str, Any]] = {}
        for field, path in EDITABLE_UPDATE_SOURCES.items():
            default = DEFAULT_CONFIG
            for key in path.split("."):
                default = default[key]

            env_var = ENV_VAR_MAP.get(path)
            sources[field] = {
                "value": str(self._get_value(path, default) or ""),
                "default": str(default),
                "env_var": env_var,
                "env_locked": bool(env_var and env_var in os.environ),
            }
        return sources

    def save_update_sources(self, values: dict[str, str]) -> None:
        """保存更新源到配置文件，并保留文件中其他所有键。

        只读原始文件而不合并默认值，避免把内置默认值写进用户文件、破坏
        "文件里只放改过的键" 这一约定；写入使用临时文件 + os.replace 的
        原子替换，防止写一半损坏配置。

        Args:
            values: 字段名 -> 新取值，未在 EDITABLE_UPDATE_SOURCES 中的字段被忽略。

        Raises:
            ValueError: 取值未通过 validate_update_source 校验。
            OSError: 配置文件写入失败。
        """
        pending = {
            field: (value or "").strip()
            for field, value in values.items()
            if field in EDITABLE_UPDATE_SOURCES
        }
        if not pending:
            return
        for field, value in pending.items():
            error = validate_update_source(field, value)
            if error:
                raise ValueError(error)

        config_path = Path(self.config_path)
        raw: dict[str, Any] = {}
        if config_path.exists():
            try:
                with open(config_path, encoding="utf-8-sig") as f:
                    loaded = json.loads(f.read())
                if isinstance(loaded, dict):
                    raw = loaded
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(
                    "更新配置文件无法解析，将按可读内容重写 (%s): %s",
                    e,
                    self.config_path,
                )

        for field, value in pending.items():
            keys = EDITABLE_UPDATE_SOURCES[field].split(".")
            node = raw
            for key in keys[:-1]:
                child = node.get(key)
                if not isinstance(child, dict):
                    child = {}
                    node[key] = child
                node = child
            node[keys[-1]] = value

        config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = config_path.with_name(f"{config_path.name}.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, config_path)
        logger.info("已更新更新源配置 (%s): %s", self.config_path, sorted(pending))
