"""Regression tests for style-descriptor cleanup (vision_analyzer._clean_descriptors).

Small vision/LLM models sometimes fall into a repetition loop and emit the same
phrase dozens of times. That runaway string used to flow straight into a deck's
`flux_style_prompt`, poisoning the style of every generated card. The cleanup
must collapse such loops to their unique descriptor set.
"""

import sys
import types

import pytest

from vision_analyzer import _clean_descriptors, build_flux_style_descriptors


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


class TestReconcilePreservesImageRead:
    """When a style_source is set, the named-style reconcile must NOT erase an
    accurate image read (medium/technique/palette) with generic style clichés."""

    def _fake_mlx(self, monkeypatch, image_read, reconciled):
        fake = types.ModuleType('mlx_llm')
        fake.vision = lambda *a, **k: image_read
        fake.chat = lambda *a, **k: reconciled
        monkeypatch.setitem(sys.modules, 'mlx_llm', fake)

    def test_image_read_survives_generic_reconcile(self, monkeypatch, tmp_path):
        # LLM collapses to generic single-word clichés; the concrete image read
        # (clean-line, flat cel, palette) must still lead the descriptors.
        self._fake_mlx(
            monkeypatch,
            image_read=("clean black ink linework, flat cel shading, dense "
                        "detailed illustration, pastel gradient palette"),
            reconciled="surreal, dreamlike, ethereal, abstract")
        img = tmp_path / "insp.png"
        img.write_bytes(b"not-a-real-png")
        out = build_flux_style_descriptors(str(img), style_source='surrealism',
                                           backend='local').lower()
        assert 'clean black ink linework' in out
        assert 'flat cel shading' in out
        assert 'pastel gradient palette' in out

    def test_image_only_path_unchanged(self, monkeypatch, tmp_path):
        self._fake_mlx(monkeypatch,
                       image_read="ink illustration, flat color, bold outlines",
                       reconciled="(unused)")
        img = tmp_path / "insp.png"
        img.write_bytes(b"x")
        out = build_flux_style_descriptors(str(img), style_source='',
                                           backend='local')
        assert out == "ink illustration, flat color, bold outlines"
