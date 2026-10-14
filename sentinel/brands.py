"""任务来源与本地品牌图标；不从模型名称猜测工具来源。"""
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

from .config import resource_path, data_directory
from .providers import PROVIDERS


def provider_for(task: dict) -> str:
    explicit = task.get("provider", "")
    if explicit in PROVIDERS:
        return explicit
    prefix = str(task.get("id", "")).split(":", 1)[0].lower()
    if prefix in PROVIDERS:
        return prefix
    source = str(task.get("source", "")).lower()
    if source.startswith("codex"):
        return "codex"
    if source.startswith("claude"):
        return "claude"
    return ""


@lru_cache(maxsize=8)
def brand_icon(provider: str, size: int) -> Image.Image:
    if provider not in PROVIDERS:
        raise ValueError("未知工具图标")
    for path in (resource_path(f"assets/{provider}.png"), data_directory() / 'icons' / f'{provider}.png'):
        try:
            with Image.open(path) as image:
                return image.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        except (OSError, ValueError):
            continue
    # 没有本机图标时用明确的名称缩写，不伪造官方标志。
    _, initials, color, _ = PROVIDERS[provider]
    image = Image.new('RGBA', (size, size))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size-1, size-1), radius=max(2, size//5), fill=color)
    font = ImageFont.load_default(size=max(8, int(size/(len(initials)*.7+1))))
    draw.text((size/2, size/2), initials, font=font, fill='#102233', anchor='mm')
    return image
