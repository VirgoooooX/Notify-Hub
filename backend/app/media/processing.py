from __future__ import annotations

import io

from PIL import Image, ImageFilter, ImageOps

from app.media.errors import MediaError

_JPEG_QUALITIES = (88, 80, 72, 64, 56, 48, 40, 32)
_JPEG_SCALES = (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.25, 0.2, 0.15, 0.1)


def _as_rgb(image: Image.Image) -> Image.Image:
    if image.mode == "RGB":
        return image
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return image.convert("RGB")


def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
    return buffer.getvalue()


def _compress_to_limit(image: Image.Image, max_bytes: int) -> bytes:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")

    rgb = _as_rgb(image)
    original_width, original_height = rgb.size
    for scale in _JPEG_SCALES:
        width = max(1, int(original_width * scale))
        height = max(1, int(original_height * scale))
        candidate = (
            rgb
            if width == original_width and height == original_height
            else rgb.resize((width, height), Image.Resampling.LANCZOS)
        )
        for quality in _JPEG_QUALITIES:
            encoded = _encode_jpeg(candidate, quality)
            if len(encoded) <= max_bytes:
                return encoded

    raise MediaError(
        "image_compression_failed",
        "Image could not be reduced to the channel size limit",
    )


def make_blurred_background_cover(
    image_bytes: bytes,
    target_ratio: float = 2.25,
    *,
    max_bytes: int | None = None,
) -> bytes:
    """If the image aspect ratio is less than 2.0 (i.e. narrow or portrait),

    create a wide canvas (default 2.25:1 aspect ratio for WeCom cards) with a
    Gaussian-blurred background from the scaled original image, and paste the
    original image centered on top.
    """
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(image_bytes)))
        img.load()
    except Exception:
        # If PIL fails to open the image, fallback to original bytes
        return image_bytes

    width, height = img.size
    if height <= 0 or width <= 0:
        return image_bytes

    current_ratio = width / height
    # If the image is already wider than or equal to 2.0, it fits WeCom landscape layout nicely.
    if current_ratio >= 2.0 and (max_bytes is None or len(image_bytes) <= max_bytes):
        return image_bytes

    rgb = _as_rgb(img)
    if current_ratio >= 2.0:
        return _compress_to_limit(rgb, max_bytes) if max_bytes is not None else image_bytes

    # Calculate target dimensions based on original height
    target_width = int(height * target_ratio)
    target_height = height

    # 1. Create the blurred background
    # Scale the original image up to cover the target dimensions, then apply Gaussian Blur
    bg_width = target_width
    bg_height = int(target_width / current_ratio)

    # Resize original image to act as background
    bg = rgb.resize((bg_width, bg_height), Image.Resampling.LANCZOS)

    # Crop the background to target_width x target_height centered
    crop_y1 = (bg_height - target_height) // 2
    crop_y2 = crop_y1 + target_height
    bg_cropped = bg.crop((0, crop_y1, target_width, crop_y2))

    # Apply a strong Gaussian blur to the background for premium glassmorphic effect
    bg_blurred = bg_cropped.filter(ImageFilter.GaussianBlur(radius=40))

    # 2. Paste the original image centered on top of the blurred background
    paste_x = (target_width - width) // 2
    bg_blurred.paste(rgb, (paste_x, 0))

    if max_bytes is not None:
        return _compress_to_limit(bg_blurred, max_bytes)
    return _encode_jpeg(bg_blurred, quality=90)
