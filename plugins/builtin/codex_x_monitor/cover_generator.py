"""Dynamic 3:4 Xiaohongshu cover image generator for Codex X Monitor with full Color Emoji support."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

# Base fonts directory for bundled fonts
FONTS_DIR = Path(__file__).resolve().parent / "fonts"
ALIMAMA_FONT_PATH = FONTS_DIR / "AlimamaFangYuanTiVF-Thin.ttf"

# Candidate text font paths across Windows and Linux
CANDIDATE_TEXT_FONTS = [
    str(ALIMAMA_FONT_PATH),
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

# Candidate color emoji font paths
CANDIDATE_EMOJI_FONTS = [
    "C:/Windows/Fonts/seguiemj.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/opentype/noto/NotoColorEmoji.ttf",
]

EMOJI_PATTERN = re.compile(
    r"([\U00010000-\U0010ffff]|\u26A1|\u2728|\u274C|\u2705|\u203C|\u2049|[\u2600-\u26FF]|[\u2700-\u27BF])"
)

# Vertical center of the zone between Codex glass icon bottom (~765px) and image bottom (1119px)
TITLE_ZONE_CENTER = 940


def _get_text_font(size: int, weight: int = 700, bevel: int = 50) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for fp in CANDIDATE_TEXT_FONTS:
        if Path(fp).exists():
            try:
                font = ImageFont.truetype(fp, size)
                # Apply variable font axes if supported (e.g. AlimamaFangYuanTi VF)
                try:
                    font.set_variation_by_axes([weight, bevel])
                except Exception:
                    pass
                return font
            except Exception:
                continue
    return ImageFont.load_default()


def _get_emoji_font(size: int) -> ImageFont.FreeTypeFont | None:
    for fp in CANDIDATE_EMOJI_FONTS:
        if Path(fp).exists():
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return None


def _measure_mixed_text(
    text: str,
    text_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    emoji_font: ImageFont.FreeTypeFont | None,
) -> tuple[int, int]:
    """Calculate the exact width and height of mixed text + color emojis."""
    if not emoji_font:
        clean = "".join(c for c in text if c < "\U00010000").strip()
        bbox = text_font.getbbox(clean)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    parts = EMOJI_PATTERN.split(text)
    total_w = 0
    max_h = 0
    for part in parts:
        if not part:
            continue
        if EMOJI_PATTERN.match(part):
            ebbox = emoji_font.getbbox(part)
            total_w += (ebbox[2] - ebbox[0]) + 10
            max_h = max(max_h, ebbox[3] - ebbox[1])
        else:
            tbbox = text_font.getbbox(part)
            total_w += tbbox[2] - tbbox[0]
            max_h = max(max_h, tbbox[3] - tbbox[1])
    return total_w, max_h


def _wrap_mixed_text(
    text: str,
    text_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    emoji_font: ImageFont.FreeTypeFont | None,
    max_width: int,
) -> list[str]:
    """Wrap text if the total width exceeds max_width."""
    total_w, _ = _measure_mixed_text(text, text_font, emoji_font)
    if total_w <= max_width:
        return [text]

    lines: list[str] = []
    cur = ""
    # Process by segments (preserving emoji units)
    parts = EMOJI_PATTERN.split(text)
    for part in parts:
        if not part:
            continue
        if EMOJI_PATTERN.match(part):
            test = cur + part
            w, _ = _measure_mixed_text(test, text_font, emoji_font)
            if w > max_width and cur:
                lines.append(cur)
                cur = part
            else:
                cur = test
        else:
            for ch in part:
                test = cur + ch
                w, _ = _measure_mixed_text(test, text_font, emoji_font)
                if w > max_width and cur:
                    lines.append(cur)
                    cur = ch
                else:
                    cur = test
    if cur:
        lines.append(cur)
    return lines


def _draw_mixed_on_layer(
    layer: Image.Image,
    text: str,
    start_x: int,
    center_y: float,
    text_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    emoji_font: ImageFont.FreeTypeFont | None,
    fill: tuple[int, int, int, int] = (255, 255, 255, 255),
    is_color_emoji: bool = True,
) -> None:
    """Render mixed text + color emojis on a transparent RGBA layer with vertical centerline alignment."""
    draw = ImageDraw.Draw(layer)
    ref_bbox = text_font.getbbox("用量重置")
    char_cy = (ref_bbox[1] + ref_bbox[3]) / 2

    if not emoji_font:
        clean = "".join(c for c in text if c < "\U00010000").strip()
        ty = int(center_y - char_cy)
        draw.text((start_x, ty), clean, font=text_font, fill=fill)
        return

    parts = EMOJI_PATTERN.split(text)
    cur_x = start_x
    for part in parts:
        if not part:
            continue
        if EMOJI_PATTERN.match(part):
            ebbox = emoji_font.getbbox(part)
            emoji_cy = (ebbox[1] + ebbox[3]) / 2
            ey = int(center_y - emoji_cy)
            if is_color_emoji:
                draw.text((cur_x, ey), part, font=emoji_font, embedded_color=True)
            else:
                draw.text((cur_x, ey), part, font=emoji_font, fill=fill)
            cur_x += (ebbox[2] - ebbox[0]) + 10
        else:
            ty = int(center_y - char_cy)
            draw.text((cur_x, ty), part, font=text_font, fill=fill)
            tbbox = text_font.getbbox(part)
            cur_x += tbbox[2] - tbbox[0]


def _render_title_lines(
    layer: Image.Image,
    lines: list[str],
    text_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    emoji_font: ImageFont.FreeTypeFont | None,
    total_w: int,
    fill: tuple[int, int, int, int],
    is_color_emoji: bool = True,
    dx: int = 0,
    dy: int = 0,
    line_h: int = 66,
) -> None:
    """Draw all lines centered vertically around TITLE_ZONE_CENTER and horizontally centered."""
    n = len(lines)
    for i, ln in enumerate(lines):
        lw, _ = _measure_mixed_text(ln, text_font, emoji_font)
        sx = (total_w - lw) // 2 + dx
        # Centered vertically in the red box area regardless of line count
        cy = TITLE_ZONE_CENTER - (n - 1) * line_h / 2 + i * line_h + dy
        _draw_mixed_on_layer(layer, ln, sx, cy, text_font, emoji_font, fill, is_color_emoji=is_color_emoji)


def generate_dynamic_xhs_cover(
    title: str,
    post_id: str,
    *,
    subtext: str = "· 官 方 额 度 动 态 ·",
    template_path: Path | None = None,
    output_dirs: list[Path] | None = None,
) -> str:
    """Generate dynamic 3:4 Xiaohongshu cover with Alimama FangYuanTi VF typography and punchy neon glow.

    The title automatically wraps if it exceeds width, and remains vertically centered
    between the Codex icon bottom and the image bottom.
    Returns the relative path under static directory, e.g. 'xhs_covers/cover_{post_id}.png'.
    Falls back to 'codex_xhs_cover.png' on any failure.
    """
    rel_path = f"xhs_covers/cover_{post_id}.png"
    fallback_static = "codex_xhs_cover.png"

    try:
        base_dir = Path(__file__).resolve().parent
        tpl_path = template_path or (base_dir / "codex_xhs_template.png")
        if not tpl_path.exists():
            logger.warning("xhs_template_not_found", extra={"path": str(tpl_path)})
            return fallback_static

        clean_title = title.strip()
        if not clean_title:
            clean_title = "Codex 用量重置提醒"

        # Ensure bullet points render cleanly across all font CMAPs
        clean_subtext = subtext.replace("•", "·")

        template = Image.open(tpl_path).convert("RGBA")
        w, h = template.size

        # ── 1. Top Subtext Badge (Alimama font) ──
        top_font = _get_text_font(22, weight=600, bevel=50)
        t_bbox = top_font.getbbox(clean_subtext)
        t_tw = t_bbox[2] - t_bbox[0]
        t_th = t_bbox[3] - t_bbox[1]
        top_cy = 148
        top_pad_x, top_pad_y = 25, 9
        top_bw = t_tw + top_pad_x * 2
        top_bh = t_th + top_pad_y * 2
        top_bx = (w - top_bw) // 2
        top_by = top_cy - top_bh // 2

        top_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        top_draw = ImageDraw.Draw(top_layer)
        top_draw.rounded_rectangle(
            [top_bx, top_by, top_bx + top_bw, top_by + top_bh],
            radius=top_bh // 2,
            fill=(255, 255, 255, 25),
            outline=(255, 255, 255, 80),
            width=1,
        )
        top_draw.text(
            ((w - t_tw) // 2, top_cy - t_th // 2 - t_bbox[1]),
            clean_subtext,
            font=top_font,
            fill=(215, 230, 255, 235),
        )

        # ── 2. Bottom Dynamic Title with Auto-Wrap & Center Alignment ──
        font_size = 50
        line_height = 66
        max_title_width = w - 90  # ~749px max width

        main_font = _get_text_font(font_size, weight=700, bevel=50)
        emoji_font = _get_emoji_font(font_size)
        lines = _wrap_mixed_text(clean_title, main_font, emoji_font, max_width=max_title_width)

        # Glow layer 1: Deep drop shadow for intense contrast against bright background
        shadow_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        _render_title_lines(
            shadow_layer, lines, main_font, emoji_font, w,
            (2, 4, 18, 240), is_color_emoji=False, dx=2, dy=5, line_h=line_height,
        )
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(8))

        # Glow layer 2: Broad radiant blue-violet aura
        glow_far = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        _render_title_lines(
            glow_far, lines, main_font, emoji_font, w,
            (80, 140, 255, 230), is_color_emoji=False, line_h=line_height,
        )
        glow_far = glow_far.filter(ImageFilter.GaussianBlur(32))

        # Glow layer 3: Mid electric cyan glow
        glow_mid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        _render_title_lines(
            glow_mid, lines, main_font, emoji_font, w,
            (100, 210, 255, 240), is_color_emoji=False, line_h=line_height,
        )
        glow_mid = glow_mid.filter(ImageFilter.GaussianBlur(14))

        # Glow layer 4: Tight electric white aura
        glow_tight = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        _render_title_lines(
            glow_tight, lines, main_font, emoji_font, w,
            (230, 245, 255, 255), is_color_emoji=False, line_h=line_height,
        )
        glow_tight = glow_tight.filter(ImageFilter.GaussianBlur(4))

        # Foreground text layer (crisp white + embedded color emojis)
        text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        _render_title_lines(
            text_layer, lines, main_font, emoji_font, w,
            (255, 255, 255, 255), is_color_emoji=True, line_h=line_height,
        )

        # ── 3. Composite all layers ──
        final_img = Image.alpha_composite(template, top_layer)
        final_img = Image.alpha_composite(final_img, shadow_layer)
        final_img = Image.alpha_composite(final_img, glow_far)
        final_img = Image.alpha_composite(final_img, glow_mid)
        final_img = Image.alpha_composite(final_img, glow_tight)
        final_img = Image.alpha_composite(final_img, text_layer)
        final_img = final_img.convert("RGB")

        # Determine target output paths
        repo_root = base_dir.parent.parent.parent
        target_dirs = output_dirs or [
            repo_root / "frontend" / "dist" / "xhs_covers",
            repo_root / "frontend" / "public" / "xhs_covers",
        ]

        saved = False
        for d in target_dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                out_file = d / f"cover_{post_id}.png"
                final_img.save(out_file, "PNG", optimize=True)
                saved = True
            except Exception as exc:
                logger.warning("save_xhs_cover_failed", extra={"dir": str(d), "error": str(exc)})

        return rel_path if saved else fallback_static
    except Exception as exc:
        logger.exception("generate_xhs_cover_failed", extra={"post_id": post_id, "error": str(exc)})
        return fallback_static

