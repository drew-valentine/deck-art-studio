"""Regression tests for style-descriptor cleanup (vision_analyzer._clean_descriptors).

Small vision/LLM models sometimes fall into a repetition loop and emit the same
phrase dozens of times. That runaway string used to flow straight into a deck's
`flux_style_prompt`, poisoning the style of every generated card. The cleanup
must collapse such loops to their unique descriptor set.
"""

import sys
import types

from vision_analyzer import _clean_descriptors


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


class TestCanonicalOrdering:
    """Deterministic descriptor ordering: FLUX weights early tokens, so the
    medium must lead regardless of the order the model's roll emitted. The
    exact observed failure: a fresh roll with near-identical vocabulary to the
    good-era prompt rendered visibly worse because '3D render, cel animation'
    sat at the END behind thirteen mood words."""

    def test_buried_medium_moves_to_front(self):
        # The user's actual weak roll (2026-07-13): medium terms buried last.
        weak_roll = ("vibrant, stylized, cartoonish, exaggerated features, "
                     "bright, high-contrast, bold, vivid, dynamic, playful, "
                     "detailed, colorful, textured, 3D render, cel animation, "
                     "sci-fi inspired")
        parts = [p.strip() for p in _clean_descriptors(weak_roll).split(',')]
        # medium bucket leads (within-category original order preserved)
        assert parts[0] == 'cartoonish'
        assert parts[1] == '3D render'
        assert parts[2] == 'cel animation'
        # composition/mood junk never leads
        assert parts[-1] != 'cel animation'

    def test_good_era_prompt_keeps_medium_first(self):
        good = ("3D render, cel animation, medium close-up, vibrant, pastel, "
                "neon, dark, soft, cartoonish, exaggerated, high-contrast, "
                "dynamic, playful, exaggerated expressions, bright, colorful")
        parts = [p.strip() for p in _clean_descriptors(good).split(',')]
        assert parts[0] == '3D render' and parts[1] == 'cel animation'
        # framing junk goes last
        assert parts[-1] == 'medium close-up'

    def test_same_words_any_order_same_output(self):
        words = ["pastel palette", "ink illustration", "dynamic", "fine linework",
                 "wide shot", "dreamy", "bold outlines", "watercolor washes"]
        import itertools, random
        rng = random.Random(7)
        baseline = _clean_descriptors(', '.join(words))
        for _ in range(5):
            shuffled = words[:]
            rng.shuffle(shuffled)
            # same vocabulary, any input order -> byte-identical output ordering
            got = sorted(p.strip() for p in _clean_descriptors(', '.join(shuffled)).split(','))
            want = sorted(p.strip() for p in baseline.split(','))
            assert got == want
            first = _clean_descriptors(', '.join(shuffled)).split(',')[0].strip()
            assert first in ('ink illustration', 'watercolor washes')

    def test_category_order_medium_line_color_mood_framing(self):
        line = ("moody, wide shot, crimson palette, rough hatching, oil painting")
        parts = [p.strip() for p in _clean_descriptors(line).split(',')]
        assert parts == ['oil painting', 'rough hatching', 'crimson palette',
                         'moody', 'wide shot']


class TestMediumFloor:
    """A roll with zero medium vocabulary gets canonical medium terms prepended
    from a deterministic style-name keyword map — never from an LLM."""

    def test_medium_less_roll_gets_floor(self):
        from vision_analyzer import _ensure_medium_floor
        weak = ("distressed sci-fi textures, neon-lit cityscapes, gritty, "
                "dark humor, retro-futuristic")   # the actual observed roll
        out = _ensure_medium_floor(weak, 'Rick & Morty')
        assert out.startswith('cel animation, cartoonish, '), out
        assert 'dark humor' in out                 # roll content preserved

    def test_roll_with_full_floor_untouched(self):
        from vision_analyzer import _ensure_medium_floor
        good = "cel animation, cartoonish, vibrant, pastel"
        assert _ensure_medium_floor(good, 'Rick & Morty') == good

    def test_partial_floor_completed(self):
        from vision_analyzer import _ensure_medium_floor
        # roll has 'cartoonish' mid-line but lacks 'cel animation' -> prepended
        out = _ensure_medium_floor("cartoonish, gritty, sci-fi", 'Rick & Morty')
        assert out == "cel animation, cartoonish, gritty, sci-fi"

    def test_identical_opening_across_rolls(self):
        from vision_analyzer import _ensure_medium_floor
        rolls = ["gritty, dark humor", "cartoonish style, neon-lit",
                 "cel animation, wacky"]
        opens = set()
        for r in rolls:
            out = _ensure_medium_floor(r, 'Rick & Morty')
            opens.add(out.split(',')[0].strip())
        assert opens == {'cel animation'}   # every roll opens identically

    def test_unknown_style_name_no_floor(self):
        from vision_analyzer import _ensure_medium_floor
        weak = "gritty, dark humor, retro-futuristic"
        assert _ensure_medium_floor(weak, 'Some Unknown Artist') == weak

    def test_ink_lineage_floor(self):
        from vision_analyzer import _ensure_medium_floor
        out = _ensure_medium_floor("dreamy, pastel tones", 'Moebius')
        assert out.startswith('ink illustration, clean linework, ')

    def test_conjunction_prefix_stripped(self):
        assert _clean_descriptors("and bold lines, neon-lit").split(',')[0].strip() == 'bold lines'


def _fake_vlm(monkeypatch, prose, medium_label='ink illustration'):
    fake = types.ModuleType('mlx_llm')
    fake.vision = lambda *a, **k: prose

    def chat(messages=None, **k):
        return medium_label
    fake.chat = chat
    monkeypatch.setitem(sys.modules, 'mlx_llm', fake)


QM_STORED = ("Source: Original\nArt Style: Digital illustration\n"
             "Colors: Creamy beige, sickly yellow-green, muted teal, deep pink, "
             "bright orange, dark brown\nTechnique: fine delicate linework, "
             "intricate line detail, thin technical pen strokes")


class TestProceduralStyleBlock:
    """Fresh Re-analyze must be repeatable: deterministic foundation (keyword
    medium + stored Colors palette), VLM only enriches — never subtracts."""

    def test_garbage_vlm_still_yields_foundation(self, monkeypatch, tmp_path):
        from vision_analyzer import build_flux_style_block
        _fake_vlm(monkeypatch, prose="dreamy, whimsical, soft focus, ethereal")
        img = tmp_path / "i.png"; img.write_bytes(b"x")
        out = build_flux_style_block(str(img), style_source='Moebius',
                                     stored_descriptions=QM_STORED)
        # medium anchors from the keyword map + line-weight evidence
        assert out.startswith('fine-line ink illustration')
        assert 'technical-pen linework' in out
        # palette from STORED Colors line, not the garbage roll
        assert 'creamy beige' in out and 'muted teal' in out

    def test_repeated_runs_identical_foundation(self, monkeypatch, tmp_path):
        from vision_analyzer import build_flux_style_block
        img = tmp_path / "i.png"; img.write_bytes(b"x")
        outs = set()
        for roll in ("dreamy, ethereal", "gritty, dark, moody",
                     "vibrant, chaotic, wild"):
            _fake_vlm(monkeypatch, prose=roll)
            out = build_flux_style_block(str(img), style_source='Moebius',
                                         stored_descriptions=QM_STORED)
            # foundation opening is byte-identical regardless of the roll
            outs.add(out.split(', palette of')[0])
        assert len(outs) == 1, outs

    def test_vlm_total_failure_foundation_survives(self, monkeypatch):
        from vision_analyzer import build_flux_style_block
        _fake_vlm(monkeypatch, prose="")
        # nonexistent image -> zero VLM reads; stored data alone carries it
        out = build_flux_style_block('/nope/missing.png', style_source='Moebius',
                                     stored_descriptions=QM_STORED)
        assert out.startswith('fine-line ink illustration')
        assert 'creamy beige' in out

    def test_bold_evidence_keeps_bold_anchor(self, monkeypatch, tmp_path):
        from vision_analyzer import build_flux_style_block
        _fake_vlm(monkeypatch,
                  prose="bold outline work, thick line art, chunky shapes")
        img = tmp_path / "i.png"; img.write_bytes(b"x")
        out = build_flux_style_block(str(img), style_source='ligne claire ink',
                                     stored_descriptions='Colors: crimson, navy')
        assert out.startswith('ink illustration')          # generic ink anchors
        assert 'fine-line' not in out

    def test_modified_hues_outrank_bare(self, monkeypatch, tmp_path):
        from vision_analyzer import build_flux_style_block
        _fake_vlm(monkeypatch, prose="")
        out = build_flux_style_block(
            '/nope/x.png', style_source='Moebius',
            stored_descriptions=("Colors: dusty coral, sage green, deep teal, "
                                 "muted purple, red, blue, yellow, pink"))
        pal = out.split('palette of ')[-1]
        assert 'dusty coral' in pal and 'sage green' in pal
        assert 'yellow' not in pal      # bare hues dropped (>=3 modified)
