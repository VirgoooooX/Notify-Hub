from pathlib import Path
import tempfile
from PIL import Image
from plugins.builtin.codex_x_monitor.cover_generator import (
    generate_dynamic_xhs_cover,
    _wrap_mixed_text,
    _get_text_font,
    _get_emoji_font,
)


def test_measure_and_wrap_mixed_text():
    text_font = _get_text_font(50)
    emoji_font = _get_emoji_font(50)

    # 1. Short text fits in one line
    short_text = "🔥Codex用量已重置！快查额度"
    lines_short = _wrap_mixed_text(short_text, text_font, emoji_font, max_width=750)
    assert len(lines_short) == 1
    assert lines_short[0] == short_text

    # 2. Long text wraps cleanly into multiple lines
    long_text = "🔥Codex 额度已全面刷新！包含最新 5.0 补足额度以及历史恢复额度"
    lines_long = _wrap_mixed_text(long_text, text_font, emoji_font, max_width=750)
    assert len(lines_long) >= 2
    # Verify emojis and words preserved
    assert "".join(lines_long) == long_text


def test_generate_dynamic_xhs_cover_single_and_multiline():
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        # Single line generation
        rel_1 = generate_dynamic_xhs_cover(
            "🔥Codex用量已重置！快查额度",
            "post_unit_1",
            output_dirs=[out_dir],
        )
        assert rel_1 == "xhs_covers/cover_post_unit_1.png"
        img_path_1 = out_dir / "cover_post_unit_1.png"
        assert img_path_1.exists()

        with Image.open(img_path_1) as im:
            assert im.size == (839, 1119)
            assert im.mode == "RGB"

        # Multi line generation
        rel_2 = generate_dynamic_xhs_cover(
            "🔥Codex 额度已全面刷新！包含最新 5.0 补足额度",
            "post_unit_2",
            output_dirs=[out_dir],
        )
        assert rel_2 == "xhs_covers/cover_post_unit_2.png"
        img_path_2 = out_dir / "cover_post_unit_2.png"
        assert img_path_2.exists()


def test_generate_dynamic_xhs_cover_fallback_on_missing_template():
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)
        missing_tpl = out_dir / "non_existent.png"

        result = generate_dynamic_xhs_cover(
            "Test fallback",
            "post_missing",
            template_path=missing_tpl,
            output_dirs=[out_dir],
        )
        assert result == "codex_xhs_cover.png"
