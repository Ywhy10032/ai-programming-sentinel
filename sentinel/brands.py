"""任务来源与本地品牌图标；不从模型名称猜测工具来源。"""
from functools import lru_cache

from PIL import Image

from .config import resource_path


def provider_for(task: dict) -> str:
    explicit = task.get("provider", "")
    if explicit in ("codex", "claude"):
        return explicit
    prefix = str(task.get("id", "")).split(":", 1)[0].lower()
    if prefix in ("codex", "claude"):
        return prefix
    source = str(task.get("source", "")).lower()
    if source.startswith("codex"):
        return "codex"
    if source.startswith("claude"):
        return "claude"
    return ""


@lru_cache(maxsize=8)
def brand_icon(provider: str, size: int) -> Image.Image:
    if provider not in ("codex", "claude"):
        raise ValueError("未知工具图标")
    with Image.open(resource_path(f"assets/{provider}.png")) as image:
        return image.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
