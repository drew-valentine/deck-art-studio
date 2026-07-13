"""Regression tests for style-descriptor cleanup (vision_analyzer._clean_descriptors).

Small vision/LLM models sometimes fall into a repetition loop and emit the same
phrase dozens of times. That runaway string used to flow straight into a deck's
`flux_style_prompt`, poisoning the style of every generated card. The cleanup
must collapse such loops to their unique descriptor set.
"""

import sys
import types

from vision_analyzer import (_clean_descriptors, _is_color_descriptor,
                             _palette_descriptors, build_flux_style_descriptors)


# The actual degenerate value that shipped in the queen-marchesa-b3-v2 deck.
DEGENERATE = (
    "ink illustration, dreamy watercolor washes, vibrant yet muted color palette, "
    "soft focus, layered paper textures, organic shapes, subtle gradient effects, "
    "ethereal atmosphere, detailed fantastical elements, textured backgrounds, "
    "soft focus, muted pastel hues, subtle gradient effects, soft focus, muted "
    "pastel hues, subtle gradient effects, soft focus, muted pastel hues, soft "
    "focus, muted pastel hues, soft focus, muted pastel hues, soft focus, muted "
    "pastel hues, soft focus, muted pastel hues, soft focus"
)


def _descriptors(s):
    return [p.strip() for p in s.split(',') if p.strip()]


class TestDeduplication:
    def test_repetition_loop_collapses_to_unique_set(self):
        out = _clean_descriptors(DEGENERATE)
        parts = _descriptors(out)
        # No descriptor appears twice (case-insensitive).
        lowered = [p.lower() for p in parts]
        assert len(lowered) == len(set(lowered)), f"duplicate descriptors: {parts}"

    def test_degenerate_shrinks_dramatically(self):
        before = len(_descriptors(DEGENERATE))
        after = len(_descriptors(_clean_descriptors(DEGENERATE)))
        assert before > 20            # the raw loop is long
        assert after <= 16            # collapsed + capped

    def test_first_seen_order_preserved(self):
        parts = _descriptors(_clean_descriptors(DEGENERATE))
        assert parts[0] == "ink illustration"
        assert parts[1] == "dreamy watercolor washes"
        # 'soft focus' kept once, at its first-seen position (before the loop)
        assert parts.count("soft focus") == 1


class TestCleanupPreserved:
    def test_normal_line_passes_through(self):
        line = ("clean black ink linework, flat cel shading, dense detailed "
                "illustration, gradient color washes, isometric composition")
        assert _clean_descriptors(line) == line

    def test_label_prefix_stripped(self):
        assert _clean_descriptors("Descriptors: oil painting, warm palette") == \
            "oil painting, warm palette"

    def test_source_name_leak_stripped(self):
        out = _clean_descriptors("surrealism, dreamlike, muted palette",
                                 style_source="surrealism")
        assert "surrealism" not in out.lower()
        assert "dreamlike" in out and "muted palette" in out

    def test_empty_input(self):
        assert _clean_descriptors("") == ""
        assert _clean_descriptors("   ") == ""

    def test_cap_enforced(self):
        many = ", ".join(f"descriptor {i}" for i in range(40))
        assert len(_descriptors(_clean_descriptors(many, max_descriptors=16))) == 16


class TestColorDetection:
    def test_color_names_detected(self):
        for c in ("teal", "warm earth tones", "monochromatic", "pastel hues",
                  "neon lights", "muted magenta palette"):
            assert _is_color_descriptor(c), c

    def test_non_color_not_detected(self):
        for c in ("clean linework", "cartoonish", "2D animation", "bold outlines",
                  "intricate details", "high contrast", "dynamic composition"):
            assert not _is_color_descriptor(c), c

    def test_palette_extraction(self):
        line = "ink illustration, pastel hues, bold outlines, warm earth tones, cartoonish"
        assert _palette_descriptors(line) == ["pastel hues", "warm earth tones"]


class TestPalettePreservation:
    """The named-style reconcile must not let a style NAME wash out the image's
    real colors (e.g. 'ligne claire' -> monochrome)."""

    def _fake_mlx(self, monkeypatch, image_read, reconciled):
        fake = types.ModuleType('mlx_llm')
        fake.vision = lambda *a, **k: image_read
        fake.chat = lambda *a, **k: reconciled
        monkeypatch.setitem(sys.modules, 'mlx_llm', fake)

    def test_pastel_survives_monochrome_reconcile(self, monkeypatch, tmp_path):
        # The exact queen-marchesa failure: named style washes color to monochrome.
        self._fake_mlx(
            monkeypatch,
            image_read=("ink illustration, delicate lines, warm earth tones, "
                        "pastel hues, intricate details"),
            reconciled=("clean, linear, minimalist, white, black, gray, "
                        "monochromatic, sharp"))
        img = tmp_path / "insp.png"; img.write_bytes(b"x")
        out = build_flux_style_descriptors(str(img), style_source='ligne claire',
                                           backend='local').lower()
        # image palette preserved
        assert 'warm earth tones' in out and 'pastel hues' in out
        # monochrome color terms from the named-style reconcile stripped out
        for mono in ('monochromatic', 'white', 'black', 'gray'):
            assert mono not in out
        # non-color style descriptors from the reconcile kept
        assert 'clean' in out and 'linear' in out

    def test_strong_style_keeps_its_descriptors(self, monkeypatch, tmp_path):
        # Rick & Morty: rich named-style knowledge must survive; palette added.
        self._fake_mlx(
            monkeypatch,
            image_read=("2D animation, cartoonish, cool tones, pastel hues, "
                        "high contrast"),
            reconciled=("cartoonish, 2D animation, dark humor, sci-fi, "
                        "bold outlines, wacky characters"))
        img = tmp_path / "insp.png"; img.write_bytes(b"x")
        out = build_flux_style_descriptors(str(img), style_source='Rick & Morty').lower()
        for kept in ('dark humor', 'sci-fi', 'bold outlines', 'wacky characters'):
            assert kept in out, kept
        assert 'cool tones' in out or 'pastel hues' in out

    def test_no_palette_in_image_leaves_reconcile_untouched(self, monkeypatch, tmp_path):
        self._fake_mlx(monkeypatch,
                       image_read="ink illustration, bold outlines, clean linework",
                       reconciled="cartoonish, flat shading, bold outlines")
        img = tmp_path / "insp.png"; img.write_bytes(b"x")
        out = build_flux_style_descriptors(str(img), style_source='Rick & Morty')
        assert out == "cartoonish, flat shading, bold outlines"
