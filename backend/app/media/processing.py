from __future__ import annotations

import io

from PIL import Image, ImageFilter


def make_blurred_background_cover(image_bytes: bytes, target_ratio: float = 2.25) -> bytes:
    """If the image aspect ratio is less than 2.0 (i.e. narrow or portrait),

    create a wide canvas (default 2.25:1 aspect ratio for WeCom cards) with a
    Gaussian-blurred background from the scaled original image, and paste the
    original image centered on top.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        orig_format = img.format or "JPEG"
        if orig_format not in ("JPEG", "PNG"):
            orig_format = "JPEG"
    except Exception:
        # If PIL fails to open the image, fallback to original bytes
        return image_bytes

    width, height = img.size
    if height <= 0 or width <= 0:
        return image_bytes

    current_ratio = width / height
    # If the image is already wider than or equal to 2.0, it fits WeCom landscape layout nicely.
    if current_ratio >= 2.0:
        return image_bytes

    # Calculate target dimensions based on original height
    target_width = int(height * target_ratio)
    target_height = height

    # 1. Create the blurred background
    # Scale the original image up to cover the target dimensions, then apply Gaussian Blur
    bg_width = target_width
    bg_height = int(target_width / current_ratio)

    # Resize original image to act as background
    bg = img.resize((bg_width, bg_height), Image.Resampling.LANCZOS)

    # Crop the background to target_width x target_height centered
    crop_y1 = (bg_height - target_height) // 2
    crop_y2 = crop_y1 + target_height
    bg_cropped = bg.crop((0, crop_y1, target_width, crop_y2))

    # Apply a strong Gaussian blur to the background for premium glassmorphic effect
    bg_blurred = bg_cropped.filter(ImageFilter.GaussianBlur(radius=40))

    # 2. Paste the original image centered on top of the blurred background
    paste_x = (target_width - width) // 2
    bg_blurred.paste(img, (paste_x, 0))

    # 3. Save back to bytes
    out_buf = io.BytesIO()
    bg_blurred.save(out_buf, format=orig_format, quality=90)
    return out_buf.getvalue()
