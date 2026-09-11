import tempfile
from pathlib import Path

from PIL import Image

from plugins.builtin.codex_x_monitor.cover_generator import (
    _get_emoji_font,
    _get_text_font,
    _wrap_mixed_text,
    generate_all_dynamic_covers,
    generate_dynamic_wechat_cover,
    generate_dynamic_xhs_cover,
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


def test_generate_dynamic_wechat_cover():
    with tempfile.TemporaryDirectory() as tmp_dir:
        out_dir = Path(tmp_dir)

        # Test left layout (Option 2)
        rel_left = generate_dynamic_wechat_cover(
            "ChatGPT Work与Codex可储存重置额度异常已修复",
            "post_wechat_left",
            layout="left",
            output_dirs=[out_dir],
        )
        assert rel_left == "wechat_covers/cover_post_wechat_left.png"
        path_left = out_dir / "cover_post_wechat_left.png"
        assert path_left.exists()
        with Image.open(path_left) as im:
            assert im.size == (1376, 586)
            assert im.mode == "RGB"

        # Test center layout (Option 1)
        rel_center = generate_dynamic_wechat_cover(
            "ChatGPT Work与Codex可储存重置额度异常已修复",
            "post_wechat_center",
            layout="center",
            output_dirs=[out_dir],
        )
        assert rel_center == "wechat_covers/cover_post_wechat_center.png"
        path_center = out_dir / "cover_post_wechat_center.png"
        assert path_center.exists()
        with Image.open(path_center) as im:
            assert im.size == (1376, 586)
            assert im.mode == "RGB"


def test_generate_all_dynamic_covers():
    with tempfile.TemporaryDirectory() as tmp_dir_xhs, tempfile.TemporaryDirectory() as tmp_dir_wx:
        out_xhs = Path(tmp_dir_xhs)
        out_wx = Path(tmp_dir_wx)

        res = generate_all_dynamic_covers(
            "ChatGPT Work与Codex可储存重置额度异常已修复",
            "post_unified_1",
            layout="left",
            output_dirs_xhs=[out_xhs],
            output_dirs_wechat=[out_wx],
        )

        assert res["xhs"] == "xhs_covers/cover_post_unified_1.png"
        assert res["wechat"] == "wechat_covers/cover_post_unified_1.png"

        assert (out_xhs / "cover_post_unified_1.png").exists()
        assert (out_wx / "cover_post_unified_1.png").exists()
