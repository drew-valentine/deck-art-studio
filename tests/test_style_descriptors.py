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


def _fake_mlx(monkeypatch, image_read, reconciled, medium_label=''):
    """Fake mlx_llm whose chat() answers the constrained medium-classification
    question with `medium_label` and every other chat with `reconciled`."""
    fake = types.ModuleType('mlx_llm')
    fake.vision = lambda *a, **k: image_read

    def chat(messages=None, **k):
        last = (messages or [{}])[-1].get('content', '')
        if last.startswith('Style: '):
            return medium_label
        return reconciled
    fake.chat = chat
    monkeypatch.setitem(sys.modules, 'mlx_llm', fake)


class TestMediumAnchors:
    """A named style gets canonical medium anchors (classified via constrained
    multiple choice) PREPENDED as an additive floor — never stripped."""

    def test_cartoon_anchors_lead(self, monkeypatch, tmp_path):
        _fake_mlx(monkeypatch,
                  image_read="2D animation, cool tones, high contrast",
                  reconciled="wacky, sci-fi, dark humor, neon",
                  medium_label="cel animation")
        img = tmp_path / "insp.png"; img.write_bytes(b"x")
        out = build_flux_style_descriptors(str(img), style_source='Wubba Cartoon')
        parts = [p.strip() for p in out.split(',')]
        # anchors first (stable prefix), LLM tail kept
        assert parts[0] == 'cel animation'
        assert 'bold clean outlines' in parts and 'flat cel shading' in parts
        for kept in ('wacky', 'sci-fi', 'dark humor', 'neon'):
            assert kept in parts, kept

    def test_good_descriptors_never_stripped(self, monkeypatch, tmp_path):
        # "3D render" was part of the empirically BEST line — additive design
        # must keep it when the LLM emits it alongside cartoon anchors.
        _fake_mlx(monkeypatch,
                  image_read="2D animation, vibrant colors",
                  reconciled="3D render, cel animation, cartoonish, vibrant, neon",
                  medium_label="cel animation")
        img = tmp_path / "insp.png"; img.write_bytes(b"x")
        out = build_flux_style_descriptors(str(img), style_source='Wubba Cartoon')
        assert '3D render' in out
        assert out.split(',')[0].strip() == 'cel animation'

    def test_unknown_medium_no_anchors(self, monkeypatch, tmp_path):
        _fake_mlx(monkeypatch,
                  image_read="ink illustration, bold outlines",
                  reconciled="cartoonish, flat shading, bold outlines",
                  medium_label="i have no idea")
        img = tmp_path / "insp.png"; img.write_bytes(b"x")
        out = build_flux_style_descriptors(str(img), style_source='Mystery Show')
        assert out == "cartoonish, flat shading, bold outlines"

    def test_palette_appended_when_line_has_no_color(self, monkeypatch, tmp_path):
        _fake_mlx(monkeypatch,
                  image_read="2D animation, warm earth tones, pastel hues",
                  reconciled="wacky, dynamic, exaggerated",
                  medium_label="cel animation")
        img = tmp_path / "insp.png"; img.write_bytes(b"x")
        out = build_flux_style_descriptors(str(img), style_source='Wubba Cartoon')
        assert 'warm earth tones' in out and 'pastel hues' in out

    def test_palette_not_duplicated_when_line_has_color(self, monkeypatch, tmp_path):
        _fake_mlx(monkeypatch,
                  image_read="2D animation, warm earth tones",
                  reconciled="wacky, neon, vibrant",   # 'neon' counts as color
                  medium_label="cel animation")
        img = tmp_path / "insp.png"; img.write_bytes(b"x")
        out = build_flux_style_descriptors(str(img), style_source='Wubba Cartoon')
        assert 'warm earth tones' not in out

    def test_image_only_keeps_medium(self, monkeypatch, tmp_path):
        # No named style -> the VLM medium is the source of truth, keep it.
        _fake_mlx(monkeypatch,
                  image_read="3D render, vibrant, dynamic", reconciled="(x)")
        img = tmp_path / "insp.png"; img.write_bytes(b"x")
        out = build_flux_style_descriptors(str(img), style_source='')
        assert '3D render' in out


# Real Qwen2.5-VL prose captured from the queen-marchesa koi reference — the
# paragraph pipeline's deterministic compressor must handle exactly this shape.
QM_PROSE = (
    "The medium is a vibrant, detailed illustration with a whimsical, "
    "fantastical aesthetic. The overall look is reminiscent of a hand-drawn "
    "comic or a digital painting with a retro, comic book feel. The linework "
    "is fine, flowing, and black, with varying weights that add depth and "
    "dimension. Surfaces are handled with a combination of flat colors and "
    "subtle shading, creating a sense of volume and texture. The color "
    "palette includes bright reds, greens, blues, yellows, and oranges. "
    "Specific hues like vermilion, chartreuse, turquoise, and saffron stand "
    "out. Recurring decorative motifs include stylized clouds, swirling "
    "patterns, and intricate patterns on the hot air balloon. The image shows "
    "influences from East Asian art, particularly in the depiction of clouds. "
    "Recurring decorative motifs include the intricate design of the alien "
    "device on the boy's head and the futuristic weapon held by the man."
)


class TestProseToStylePrompt:
    def test_style_dna_survives(self):
        from vision_analyzer import _prose_to_style_prompt
        out = _prose_to_style_prompt(QM_PROSE)
        # specific hues + motifs are the uncanny carriers — must survive the cap
        assert 'vermilion' in out and 'chartreuse' in out
        assert 'stylized clouds' in out and 'swirling patterns' in out

    def test_scaffolding_and_headers_stripped(self):
        from vision_analyzer import _prose_to_style_prompt
        out = _prose_to_style_prompt(QM_PROSE)
        assert 'The medium is' not in out
        assert 'The color palette includes' not in out

    def test_subject_leaks_dropped(self):
        from vision_analyzer import _prose_to_style_prompt
        out = _prose_to_style_prompt(QM_PROSE).lower()
        assert 'balloon' not in out            # end-anchored tail chopped
        assert 'alien' not in out and 'weapon' not in out   # sentence dropped
        assert 'boy' not in out

    def test_word_cap_respected(self):
        from vision_analyzer import _prose_to_style_prompt
        out = _prose_to_style_prompt(QM_PROSE, max_words=65)
        assert len(out.split()) <= 70          # small tolerance over cap

    def test_empty_prose(self):
        from vision_analyzer import _prose_to_style_prompt
        assert _prose_to_style_prompt('') == ''
        assert _prose_to_style_prompt('   ') == ''


class TestParagraphBuilder:
    def test_paragraph_from_vision_prose(self, monkeypatch, tmp_path):
        from vision_analyzer import build_flux_style_paragraph
        fake = types.ModuleType('mlx_llm')
        fake.vision = lambda *a, **k: QM_PROSE
        monkeypatch.setitem(sys.modules, 'mlx_llm', fake)
        img = tmp_path / "insp.png"; img.write_bytes(b"x")
        out = build_flux_style_paragraph(str(img))
        assert 'vermilion' in out and len(out.split()) >= 20

    def test_missing_image_returns_empty(self):
        from vision_analyzer import build_flux_style_paragraph
        assert build_flux_style_paragraph('/nope/missing.png') == ''


class TestMediumClassification:
    def test_label_parsed_from_reply(self, monkeypatch):
        _fake_mlx(monkeypatch, image_read='', reconciled='',
                  medium_label='The label is: cel animation.')
        from vision_analyzer import _classify_style_medium
        assert _classify_style_medium('Rick & Morty', 'm') == 'cel animation'

    def test_longest_label_wins(self, monkeypatch):
        # 'ink illustration' must not lose to any shorter substring label.
        _fake_mlx(monkeypatch, image_read='', reconciled='',
                  medium_label='ink illustration')
        from vision_analyzer import _classify_style_medium
        assert _classify_style_medium('ligne claire', 'm') == 'ink illustration'

    def test_garbage_reply_returns_empty(self, monkeypatch):
        _fake_mlx(monkeypatch, image_read='', reconciled='', medium_label='dunno')
        from vision_analyzer import _classify_style_medium
        assert _classify_style_medium('whatever', 'm') == ''
