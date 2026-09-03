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


class TestDeclaredStyleAuthority:
    """The user's declared style source overrides the model's interpretation —
    in every direction, with no medium taxonomy in the loop. Root case: a deck
    declared 'Dr. Seuss hand drawn illustration' whose per-image analyses
    called clean scans 'digital watercolor' / 'digital vector illustration'
    because the analyst was never shown the declaration."""

    def _capture_vision(self, monkeypatch):
        import vision_analyzer as va
        captured = {}
        fake = types.ModuleType('mlx_llm')
        def _vision(image_path=None, prompt=None, model=None, **kw):
            captured['prompt'] = prompt
            return 'Source: X\nArt Style: pen and ink drawing'
        fake.vision = _vision
        monkeypatch.setitem(sys.modules, 'mlx_llm', fake)
        return va, captured

    def test_analysis_prompt_carries_declaration_as_authority(self, monkeypatch):
        va, captured = self._capture_vision(monkeypatch)
        out = va.analyze_inspiration_style(
            __file__, backend='local', local_model='m',
            style_source='Dr. Seuss hand drawn illustration')
        assert 'Dr. Seuss hand drawn illustration' in captured['prompt']
        assert 'overrides your interpretation' in captured['prompt']
        assert out.startswith('Source:')

    def test_declaration_is_generic_not_handmade_specific(self, monkeypatch):
        # ANY declaration gets authority — not just hand-made ones.
        va, captured = self._capture_vision(monkeypatch)
        va.analyze_inspiration_style(
            __file__, backend='local', local_model='m',
            style_source='painterly Pixar-grade 3D render')
        assert 'painterly Pixar-grade 3D render' in captured['prompt']
        assert 'overrides your interpretation' in captured['prompt']

    def test_analysis_prompt_unchanged_without_declaration(self, monkeypatch):
        va, captured = self._capture_vision(monkeypatch)
        va.analyze_inspiration_style(__file__, backend='local', local_model='m')
        assert 'USER-DECLARED' not in captured['prompt']


class TestMotifSubjectLeakGuard:
    """_SUBJECT_LEAK_WORDS was referenced but never defined (PR #31): the first
    deck whose analyses matched the motif regex NameError'd the distillation."""

    def test_motif_extraction_does_not_raise(self):
        from vision_analyzer import _extract_motif_phrases
        out = _extract_motif_phrases(
            'whimsical swirling clouds patterns over curling waves motifs')
        assert isinstance(out, list) and out

    def test_subject_phrases_filtered(self):
        from vision_analyzer import _extract_motif_phrases
        out = _extract_motif_phrases('character swirling clouds everywhere')
        assert all('character' not in m for m in out)


class TestEvidenceDerivedInkAxes:
    """Line weight, line character, and detail density are independent
    evidence-derived axes. The old fine-line variant welded 'uniform
    technical-pen' + 'dense detail filling every surface' onto ANY fine-line
    evidence — a whimsical sparse style (Dr. Seuss) got dense technical
    draftsmanship its references never showed, drowning the declared name."""

    SEUSS_STORED = ("Source: hand drawn illustration\n"
                    "Art Style: Whimsical hand-drawn illustration with "
                    "whimsical doodling | Fine line ink drawing\n"
                    "Colors: light blue, pastel purple, muted yellow, "
                    "dusty coral\n"
                    "Technique: loose playful pen line, minimal shading, "
                    "flat graphic space, white background")

    def test_loose_sparse_evidence_yields_loose_sparse_anchors(self, monkeypatch):
        from vision_analyzer import build_flux_style_block
        _fake_vlm(monkeypatch, prose="")
        out = build_flux_style_block('/nope/x.png',
                                     style_source='hand drawn illustration',
                                     stored_descriptions=self.SEUSS_STORED)
        assert 'loose expressive hand-drawn linework' in out
        assert 'sparse airy composition' in out
        assert 'technical-pen' not in out
        assert 'dense intricate detail' not in out

    def test_tight_dense_evidence_unchanged_from_old_behavior(self, monkeypatch):
        # The Moebius-style deck keeps its exact historical anchors.
        from vision_analyzer import build_flux_style_block
        _fake_vlm(monkeypatch, prose="")
        out = build_flux_style_block('/nope/x.png', style_source='Moebius',
                                     stored_descriptions=QM_STORED)
        assert out.startswith('fine-line ink illustration, '
                              'uniform fine technical-pen linework, '
                              'flat color fills over black line art, '
                              'dense intricate detail filling every surface')

    def test_axes_are_independent(self, monkeypatch):
        # Fine line weight + sparse density: fine anchor WITHOUT dense anchor.
        from vision_analyzer import build_flux_style_block
        _fake_vlm(monkeypatch, prose="")
        out = build_flux_style_block(
            '/nope/x.png', style_source='ligne claire ink',
            stored_descriptions=("Colors: red, blue\nTechnique: fine delicate "
                                 "hairline strokes, minimal sparse detail, "
                                 "white background"))
        assert out.startswith('fine-line ink illustration')
        assert 'sparse airy composition' in out
        assert 'dense intricate detail' not in out


class TestUndeclaredSourceMedium:
    """A deck with NO declared style source must still get medium anchors —
    from its own analyses. The gate `if style_source else ''` left a
    hieroglyph/papyrus deck with a bare palette block ("palette of dusty
    coral, muted gold, ...") that rendered nothing like its references."""

    PAPYRUS = ("Source: Original\nArt Style: Hieroglyphic ink drawing\n"
               "Colors: dusty coral, muted gold, tan, black\n"
               "Technique:\n- Medium: Ink on papyrus\n"
               "Source: Original\nArt Style: Egyptian-inspired illustration\n"
               "- Medium: flat ink and pigment on papyrus")

    def test_evidence_vote_picks_medium(self):
        from vision_analyzer import _classify_medium_from_evidence
        assert _classify_medium_from_evidence(self.PAPYRUS, '', 'm') == 'ink illustration'

    def test_vote_reads_only_medium_lines(self):
        # 'film' in a Themes line must not vote for photograph
        from vision_analyzer import _classify_medium_from_evidence
        text = ("Art Style: loose watercolor sketch\nThemes: film noir detectives\n"
                "- Medium: watercolour and gouache")
        assert _classify_medium_from_evidence(text, '', 'm') == 'watercolor'

    def test_undeclared_deck_block_has_anchors(self, monkeypatch):
        from vision_analyzer import build_flux_style_block
        _fake_vlm(monkeypatch, prose="")
        out = build_flux_style_block('/nope/x.png', style_source='',
                                     stored_descriptions=self.PAPYRUS)
        assert out.startswith('ink illustration'), out
        assert 'palette of' in out

    def test_no_evidence_no_anchors(self):
        from vision_analyzer import _classify_medium_from_evidence
        assert _classify_medium_from_evidence('', '', 'm') == ''


# ── H12: named-style idiom expansion ────────────────────────────────────────

def test_style_idiom_descriptors_uses_llm_and_caps_words(monkeypatch):
    import sys, types
    import vision_analyzer as va
    monkeypatch.setattr(va, '_preferred_idiom_model', lambda m: m)
    fake = types.SimpleNamespace(
        chat=lambda **kw: (
            "wobbly thin outlines, bulging eyes with pinprick pupils, drooling deadpan faces, "
            "lumpy simplified anatomy, muted and dark color palette, scribbly line detail, "
            "flat cel shading, Rick Sanchez"),
        vision=lambda *a, **kw: "wobbly thin outlines, vibrant colors, sci-fi gadgetry and machinery")
    monkeypatch.setitem(sys.modules, 'mlx_llm', fake)
    out = va.style_idiom_descriptors('Rick & Morty', 'm', image_path='x.png', vision_model='v', max_words=40)
    assert out[0] == 'wobbly thin outlines'
    assert 'sci-fi gadgetry and machinery' in out          # merged from the vision read
    assert out.count('wobbly thin outlines') == 1          # de-duplicated
    assert sum(len(p.split()) for p in out) <= 40
    assert not any(w.lower() in ('rick', 'sanchez', 'morty') for p in out for w in p.split())
    assert not any('palette' in p or 'colors' in p for p in out)   # palette is evidence work


def test_style_idiom_descriptors_empty_source_or_failure(monkeypatch):
    import sys, types
    import vision_analyzer as va
    assert va.style_idiom_descriptors('', 'm') == []
    def boom(**kw): raise RuntimeError('no model')
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=boom))
    assert va.style_idiom_descriptors('Moebius', 'm') == []


def test_block_carries_idiom_after_anchors(monkeypatch):
    import vision_analyzer as va
    monkeypatch.setattr(va, 'style_idiom_recall', lambda src, model, **kw: ['wobbly thin outlines', 'bulging eyes'])
    monkeypatch.setattr(va, 'style_idiom_seen', lambda *a, **kw: [])
    monkeypatch.setattr(va, 'analyze_inspiration_style', lambda *a, **k: {})
    block = va.build_flux_style_block(
        'unused.png', style_source='Rick & Morty', text_model='m',
        stored_descriptions=("Art Style: cel animation, cartoon\nMedium: digital cel animation\n"
                             "Color Palette: teal, orange\nSource: Rick and Morty"))
    assert 'wobbly thin outlines' in block and 'bulging eyes' in block
    assert block.index('wobbly') < block.index('palette of')


def test_style_staging_recall_drops_named_sentences(monkeypatch):
    import sys, types
    import vision_analyzer as va
    monkeypatch.setattr(va, '_preferred_idiom_model', lambda m: m)
    fake = types.SimpleNamespace(chat=lambda **kw: (
        "Scenes are staged in cluttered garages and alien bazaars with figures slouching mid-argument, "
        "seen at medium distance. Rick usually stands to the left. The register is deadpan absurd."))
    monkeypatch.setitem(sys.modules, 'mlx_llm', fake)
    out = va.style_staging_recall('Rick & Morty', 'm')
    assert out.startswith('Scenes are staged') and 'deadpan absurd' in out
    assert 'Rick' not in out
    assert va.style_staging_recall('', 'm') == ''


def test_style_idiom_recall_is_memoized(monkeypatch):
    import sys, types
    import vision_analyzer as va
    monkeypatch.setattr(va, '_preferred_idiom_model', lambda m: m)
    calls = []
    def chat(**kw):
        calls.append(1); return "thin wobbly outlines, bulging eyes"
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    va._IDIOM_RECALL_CACHE.clear()
    a = va.style_idiom_recall('Some Artist Nobody Knows', 'm')
    b = va.style_idiom_recall('some artist nobody knows', 'm')
    assert a == b == ['thin wobbly outlines', 'bulging eyes'] and len(calls) == 1


def test_staging_recall_unknown_falls_back_to_reference_read(monkeypatch):
    import sys, types
    import vision_analyzer as va
    monkeypatch.setattr(va, '_preferred_idiom_model', lambda m: m)
    fake = types.SimpleNamespace(
        chat=lambda **kw: "UNKNOWN",
        vision=lambda *a, **kw: "The scene is a sunlit desert plateau with a lone tower, seen from far away. The register is serene wonder.")
    monkeypatch.setitem(sys.modules, 'mlx_llm', fake)
    out = va.style_staging_recall('Some Painter Nobody Knows', 'm', image_path='ref.png', vision_model='v')
    assert out.startswith('The scene is a sunlit desert') and 'serene wonder' in out
    # no source at all -> reference read only when an image is given
    assert va.style_staging_recall('', 'm') == ''
    assert va.style_staging_recall('', 'm', image_path='ref.png', vision_model='v') == ''
    # idiom recall honours UNKNOWN too
    va._IDIOM_RECALL_CACHE.clear()
    assert va.style_idiom_recall('Some Painter Nobody Knows', 'm') == []
