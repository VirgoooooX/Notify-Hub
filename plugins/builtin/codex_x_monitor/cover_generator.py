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


WECHAT_OFFICIAL_ASPECT_RATIO = 2.35
WECHAT_LOGO_TOP = 66
WECHAT_LOGO_BOTTOM = 460
WECHAT_LOGO_HEIGHT = 394


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
    center_y: int = TITLE_ZONE_CENTER,
) -> None:
    """Draw all lines centered vertically around center_y and horizontally centered."""
    n = len(lines)
    for i, ln in enumerate(lines):
        lw, _ = _measure_mixed_text(ln, text_font, emoji_font)
        sx = (total_w - lw) // 2 + dx
        # Centered vertically around center_y regardless of line count
        cy = center_y - (n - 1) * line_h / 2 + i * line_h + dy
        _draw_mixed_on_layer(layer, ln, sx, cy, text_font, emoji_font, fill, is_color_emoji=is_color_emoji)


def _composite_neon_title(
    base_image: Image.Image,
    lines: list[str],
    text_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    emoji_font: ImageFont.FreeTypeFont | None,
    total_w: int,
    total_h: int,
    center_y: int,
    line_height: int = 66,
) -> Image.Image:
    """Render 5-layer neon aura, deep drop-shadow and crisp foreground text onto base_image."""
    # 1. Deep drop shadow for contrast against bright background
    shadow_layer = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    _render_title_lines(
        shadow_layer, lines, text_font, emoji_font, total_w,
        (2, 4, 18, 240), is_color_emoji=False, dx=2, dy=5, line_h=line_height, center_y=center_y,
    )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(8))

    # 2. Broad radiant blue-violet aura
    glow_far = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    _render_title_lines(
        glow_far, lines, text_font, emoji_font, total_w,
        (80, 140, 255, 230), is_color_emoji=False, line_h=line_height, center_y=center_y,
    )
    glow_far = glow_far.filter(ImageFilter.GaussianBlur(32))

    # 3. Mid electric cyan glow
    glow_mid = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    _render_title_lines(
        glow_mid, lines, text_font, emoji_font, total_w,
        (100, 210, 255, 240), is_color_emoji=False, line_h=line_height, center_y=center_y,
    )
    glow_mid = glow_mid.filter(ImageFilter.GaussianBlur(14))

    # 4. Tight electric white aura
    glow_tight = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    _render_title_lines(
        glow_tight, lines, text_font, emoji_font, total_w,
        (230, 245, 255, 255), is_color_emoji=False, line_h=line_height, center_y=center_y,
    )
    glow_tight = glow_tight.filter(ImageFilter.GaussianBlur(4))

    # 5. Foreground text layer (crisp white + embedded color emojis)
    text_layer = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    _render_title_lines(
        text_layer, lines, text_font, emoji_font, total_w,
        (255, 255, 255, 255), is_color_emoji=True, line_h=line_height, center_y=center_y,
    )

    result = base_image
    for layer in (shadow_layer, glow_far, glow_mid, glow_tight, text_layer):
        result = Image.alpha_composite(result, layer)
    return result


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

        # ── 3. Composite all layers ──
        composited = Image.alpha_composite(template, top_layer)
        final_img = _composite_neon_title(
            composited,
            lines,
            main_font,
            emoji_font,
            w,
            h,
            center_y=TITLE_ZONE_CENTER,
            line_height=line_height,
        ).convert("RGB")

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


WECHAT_LEFT_LOGO_LEFT = 95
WECHAT_LEFT_LOGO_WIDTH = 350
WECHAT_LEFT_GAP = 95
WECHAT_LEFT_TITLE_BOX_LEFT = 540
WECHAT_LEFT_TITLE_BOX_WIDTH = 741


def generate_dynamic_wechat_cover(
    title: str,
    post_id: str,
    *,
    layout: str = "left",
    template_path: Path | None = None,
    output_dirs: list[Path] | None = None,
) -> str:
    """Generate dynamic WeChat Official Account cover directly at 2.35:1.

    Supports two distinct aesthetic layouts:
      1. 'left' (Option 2): Logo on the left (refined 350px size with generous breathing room),
         title on the right (large font size 62px, line height 114px).
         Strictly enforces 3 horizontal equidistant gaps:
         Gap1 (image left to logo) = Gap2 (logo to title) = Gap3 (title to image right) = 95px.
      2. 'center' (Option 1): Logo on top, title below.
         Strictly enforces 3 vertical equidistant gaps:
         D1 (top to logo) = D2 (logo to title) = D3 (title to bottom) = 49px.

    Returns relative path, e.g. 'wechat_covers/cover_{post_id}.png'.
    Falls back to 'codex_wechat_cover.png' on any failure.
    """
    rel_path = f"wechat_covers/cover_{post_id}.png"
    fallback_static = "codex_wechat_cover.png"

    try:
        base_dir = Path(__file__).resolve().parent
        clean_title = title.strip()
        if not clean_title:
            clean_title = "Codex 用量重置提醒"

        if layout == "left":
            tpl_path = template_path or (base_dir / "codex_wechat_left_template.png")
            if not tpl_path.exists():
                logger.warning("wechat_left_template_not_found", extra={"path": str(tpl_path)})
                return fallback_static

            template = Image.open(tpl_path).convert("RGBA")
            w, h = template.size

            font_size = 62
            max_title_width = WECHAT_LEFT_TITLE_BOX_WIDTH
            main_font = _get_text_font(font_size, weight=700, bevel=50)
            emoji_font = _get_emoji_font(font_size)
            lines = _wrap_mixed_text(clean_title, main_font, emoji_font, max_width=max_title_width)

            line_height = 114
            if len(lines) > 2:
                font_size = 50
                line_height = 76
                main_font = _get_text_font(font_size, weight=700, bevel=50)
                emoji_font = _get_emoji_font(font_size)
                lines = _wrap_mixed_text(clean_title, main_font, emoji_font, max_width=max_title_width)

            title_cx = WECHAT_LEFT_TITLE_BOX_LEFT + WECHAT_LEFT_TITLE_BOX_WIDTH // 2  # 910
            cy = h // 2  # 293

            def draw_left_title(layer, fill, is_color=True, dx=0, dy=0):
                n = len(lines)
                for i, ln in enumerate(lines):
                    lw, _ = _measure_mixed_text(ln, main_font, emoji_font)
                    sx = title_cx - lw // 2 + dx
                    cur_cy = cy - (n - 1) * line_height / 2 + i * line_height + dy
                    _draw_mixed_on_layer(layer, ln, sx, cur_cy, main_font, emoji_font, fill, is_color_emoji=is_color)

            shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw_left_title(shadow, (2, 4, 18, 240), is_color=False, dx=2, dy=5)
            shadow = shadow.filter(ImageFilter.GaussianBlur(8))

            glow_far = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw_left_title(glow_far, (80, 140, 255, 230), is_color=False)
            glow_far = glow_far.filter(ImageFilter.GaussianBlur(32))

            glow_mid = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw_left_title(glow_mid, (100, 210, 255, 240), is_color=False)
            glow_mid = glow_mid.filter(ImageFilter.GaussianBlur(14))

            glow_tight = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw_left_title(glow_tight, (230, 245, 255, 255), is_color=False)
            glow_tight = glow_tight.filter(ImageFilter.GaussianBlur(4))

            text_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw_left_title(text_layer, (255, 255, 255, 255), is_color=True)

            composited = template
            for layer in (shadow, glow_far, glow_mid, glow_tight, text_layer):
                composited = Image.alpha_composite(composited, layer)
            final_img = composited.convert("RGB")

        else:
            # Option 1: Top-down centered layout
            tpl_path = template_path or (base_dir / "codex_wechat_template.png")
            if not tpl_path.exists():
                logger.warning("wechat_template_not_found", extra={"path": str(tpl_path)})
                return fallback_static

            template = Image.open(tpl_path).convert("RGBA")
            w, h = template.size
            target_h = int(round(w / WECHAT_OFFICIAL_ASPECT_RATIO))  # 586 for w=1376

            font_size = 46
            max_title_width = w - 160
            main_font = _get_text_font(font_size, weight=700, bevel=50)
            emoji_font = _get_emoji_font(font_size)
            lines = _wrap_mixed_text(clean_title, main_font, emoji_font, max_width=max_title_width)

            ref_bbox = main_font.getbbox("用量重置")
            char_cy = (ref_bbox[1] + ref_bbox[3]) / 2

            if len(lines) == 1:
                line_height = 54
                text_bbox = main_font.getbbox(lines[0])
                h_ink = text_bbox[3] - text_bbox[1]
                D = max(10, int(round((target_h - WECHAT_LOGO_HEIGHT - h_ink) / 3)))
                crop_top = max(0, int(round(WECHAT_LOGO_TOP - D)))
                target_center_y = int(round(WECHAT_LOGO_BOTTOM + D - text_bbox[1] + char_cy))
            else:
                font_size = 42
                line_height = 54
                main_font = _get_text_font(font_size, weight=700, bevel=50)
                emoji_font = _get_emoji_font(font_size)
                lines = _wrap_mixed_text(clean_title, main_font, emoji_font, max_width=max_title_width)
                ref_bbox = main_font.getbbox("用量重置")
                char_cy = (ref_bbox[1] + ref_bbox[3]) / 2
                first_bbox = main_font.getbbox(lines[0])
                h_ink = first_bbox[3] - first_bbox[1]
                total_text_h = (len(lines) - 1) * line_height + h_ink
                D = max(10, int(round((target_h - WECHAT_LOGO_HEIGHT - total_text_h) / 3)))
                crop_top = max(0, int(round(WECHAT_LOGO_TOP - D)))
                target_center_y = int(round(WECHAT_LOGO_BOTTOM + D + total_text_h / 2 - h_ink / 2 + char_cy))

            full_img = _composite_neon_title(
                template,
                lines,
                main_font,
                emoji_font,
                w,
                h,
                center_y=target_center_y,
                line_height=line_height,
            ).convert("RGB")

            crop_box = (0, crop_top, w, crop_top + target_h)
            final_img = full_img.crop(crop_box)

        # Determine target output paths
        repo_root = base_dir.parent.parent.parent
        target_dirs = output_dirs or [
            repo_root / "frontend" / "dist" / "wechat_covers",
            repo_root / "frontend" / "public" / "wechat_covers",
        ]

        saved = False
        for d in target_dirs:
            try:
                d.mkdir(parents=True, exist_ok=True)
                out_file = d / f"cover_{post_id}.png"
                final_img.save(out_file, "PNG", optimize=True)
                saved = True
            except Exception as exc:
                logger.warning("save_wechat_cover_failed", extra={"dir": str(d), "error": str(exc)})

        return rel_path if saved else fallback_static
    except Exception as exc:
        logger.exception("generate_wechat_cover_failed", extra={"post_id": post_id, "error": str(exc)})
        return fallback_static


def generate_all_dynamic_covers(
    title: str,
    post_id: str,
    *,
    layout: str = "left",
    subtext: str = "· 官 方 额 度 动 态 ·",
    xhs_template_path: Path | None = None,
    wechat_template_path: Path | None = None,
    output_dirs_xhs: list[Path] | None = None,
    output_dirs_wechat: list[Path] | None = None,
) -> dict[str, str]:
    """Generate both Xiaohongshu cover (3:4) and WeChat Official Account cover (2.35:1) in a single call."""
    xhs_cover = generate_dynamic_xhs_cover(
        title,
        post_id,
        subtext=subtext,
        template_path=xhs_template_path,
        output_dirs=output_dirs_xhs,
    )
    wechat_cover = generate_dynamic_wechat_cover(
        title,
        post_id,
        layout=layout,
        template_path=wechat_template_path,
        output_dirs=output_dirs_wechat,
    )
    return {
        "xhs": xhs_cover,
        "wechat": wechat_cover,
    }




