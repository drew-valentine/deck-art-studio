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
    assert block.index('palette of') < block.index('wobbly')   # colour first, idiom next


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


def test_staging_reads_the_reference_first_then_recalls(monkeypatch):
    import sys, types
    import vision_analyzer as va
    monkeypatch.setattr(va, '_preferred_idiom_model', lambda m: m)
    fake = types.SimpleNamespace(
        chat=lambda **kw: "Scenes are staged in grim gothic ruins. The register is foreboding.",
        vision=lambda *a, **kw: "The scene is a sunlit desert plateau with a lone tower, seen from far away. The register is serene wonder.")
    monkeypatch.setitem(sys.modules, 'mlx_llm', fake)
    # with a reference: the read wins over the name recall
    out = va.style_staging_recall('Some Painter', 'm', image_path='ref.png', vision_model='v')
    assert out.startswith('The scene is a sunlit desert') and 'gothic' not in out
    # no reference: name recall
    assert 'grim gothic ruins' in va.style_staging_recall('Some Painter', 'm')
    # neither name nor reference
    assert va.style_staging_recall('', 'm') == ''
    # UNKNOWN recall -> nothing
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda **kw: "UNKNOWN"))
    assert va.style_staging_recall('Nobody', 'm') == ''
    va._IDIOM_RECALL_CACHE.clear()
    assert va.style_idiom_recall('Nobody', 'm') == []


def test_idiom_seen_needs_no_name(monkeypatch):
    import sys, types
    import vision_analyzer as va
    asked = {}
    def vision(path, prompt, **kw):
        asked['prompt'] = prompt; return "bold ink outlines, halftone dots, dynamic diagonal panels"
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(vision=vision))
    out = va.style_idiom_seen('ref.png', '', 'v')
    assert out == ['bold ink outlines', 'halftone dots', 'dynamic diagonal panels']
    assert asked['prompt'].startswith('This is a reference illustration.')


def test_style_lineage_recall_filters_names_and_unknown(monkeypatch):
    import sys, types
    import vision_analyzer as va
    monkeypatch.setattr(va, '_preferred_idiom_model', lambda m: m)
    va._LINEAGE_CACHE.clear()
    replies = iter(["late-night adult animation on a cable comedy network", "UNKNOWN", "a Rick and Morty style cartoon"])
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda **kw: next(replies)))
    assert va.style_lineage_recall('Rick & Morty', 'm') == 'late-night adult animation on a cable comedy network'
    assert va.style_lineage_recall('Nobody Knows This', 'm') == ''
    assert va.style_lineage_recall('Rick and Morty Show', 'm') == ''   # name leaked into the phrase -> dropped


def test_style_source_kind_recall(monkeypatch):
    import sys, types
    import vision_analyzer as va
    monkeypatch.setattr(va, '_preferred_idiom_model', lambda m: m)
    va._KIND_CACHE.clear()
    replies = iter(["FRANCHISE", "Artist.", "UNKNOWN", "banana"])
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda **kw: next(replies)))
    assert va.style_source_kind('Smiling Friends', 'm') == 'franchise'
    assert va.style_source_kind('Some Painter', 'm') == 'artist'
    assert va.style_source_kind('Nobody', 'm') == ''
    assert va.style_source_kind('Garbage', 'm') == ''
    assert va.style_source_kind('', 'm') == ''


def test_block_states_colour_coverage_from_evidence(monkeypatch):
    import vision_analyzer as va
    monkeypatch.setattr(va, 'analyze_inspiration_style', lambda *a, **k: {})
    monkeypatch.setattr(va, 'style_idiom_recall', lambda *a, **k: [])
    monkeypatch.setattr(va, 'style_idiom_seen', lambda *a, **k: [])
    coloured = ("Art Style: whimsical hand-drawn illustration\nMedium: pen and ink with flat color fills\n"
                "Colors: teal, bright yellow, red\nTechnique: loose wobbly line, saturated flat colour fills")
    blk = va.build_flux_style_block('x.png', style_source='', text_model='m', stored_descriptions=coloured)
    assert 'fully coloured with saturated flat colour fills' in blk
    assert blk.index('fully coloured') < blk.index('palette of')
    mono = ("Art Style: pen and ink illustration\nMedium: black and white ink\nColors: black, white\n"
            "Technique: monochrome crosshatching, uncoloured")
    blk2 = va.build_flux_style_block('x.png', style_source='', text_model='m', stored_descriptions=mono)
    assert 'monochrome, uncoloured ink on white paper' in blk2


def test_declared_source_medium_prefers_stored_evidence_over_raw_read(monkeypatch):
    import sys, types
    import vision_analyzer as va
    monkeypatch.setattr(va, 'analyze_inspiration_style', lambda *a, **k: {'style_prose': 'a photograph of a painted temple wall, dramatic shadows'})
    monkeypatch.setattr(va, 'style_idiom_recall', lambda *a, **k: [])
    monkeypatch.setattr(va, 'style_idiom_seen', lambda *a, **k: [])
    # the LLM would say photograph from the raw read; it must not be consulted
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda **kw: "photograph"))
    stored = ("Source: Original\nArt Style: Papyrus illustration rendering\n- Medium: Papyrus parchment\n"
              "Colors: deep brown, vibrant green\nSource: Ancient Egyptian Hieroglyphs\n"
              "Art Style: Digital painting with flat figures\n- Medium: Digital painting")
    blk = va.build_flux_style_block('x.png', style_source='Ancient Egyptian Hieroglyphs',
                                    text_model='m', stored_descriptions=stored)
    assert 'photograph' not in blk
    assert va._evidence_medium_vote(stored) != ''


def test_pixel_palette_measures_paper_and_hues(tmp_path):
    from PIL import Image, ImageDraw
    import vision_analyzer as va
    # white page with a saturated teal figure and a red object
    im = Image.new('RGB', (200, 200), (255, 255, 255)); d = ImageDraw.Draw(im)
    d.rectangle([20, 20, 100, 180], fill=(30, 140, 140)); d.ellipse([120, 120, 180, 180], fill=(210, 30, 30))
    p = tmp_path / 'ref.png'; im.save(p)
    st = va.pixel_palette(p)
    assert st['paper'] > 0.5 and st['hues'][0] == 'teal' and 'red' in st['hues']
    assert va.pixel_coverage_phrase(st) == 'coloured figures and objects on open white paper'
    # a fully painted saturated image
    im2 = Image.new('RGB', (200, 200), (240, 200, 40)); ImageDraw.Draw(im2).rectangle([0, 100, 200, 200], fill=(40, 90, 210))
    p2 = tmp_path / 'ref2.png'; im2.save(p2)
    st2 = va.pixel_palette(p2)
    assert st2['paper'] == 0 and va.pixel_coverage_phrase(st2).startswith('fully coloured with saturated')
    assert va.pixel_coverage_phrase(None) == ''
    assert va.pixel_coverage_from_refs(p, [p, p2]).startswith(('coloured figures and objects on open white paper',
                                                                 'fully coloured with soft muted fills, no bare white paper',
                                                                 'fully coloured with saturated flat colour fills, no bare white paper'))   # tonal/sky keys may follow


def test_idiom_phrases_drop_writing_words():
    import vision_analyzer as va
    out = va._idiom_phrases('bold black outline, hieroglyphic symbols, striped patterns, calligraphic lettering, symmetrical composition', 'Ancient Egyptian Hieroglyphs', 40)
    assert out == ['bold black outline', 'striped patterns', 'symmetrical composition']


def test_flat_media_line_has_no_example_nouns():
    """The writer parrots concrete example nouns into scenes (a red cushion and
    a gold ring turned up in two unrelated cards). The flat-media instruction
    must describe the rule without naming props."""
    import re
    src = open('prompt_generator.py').read()
    block = src[src.index('This medium ({medium_word}) is FLAT'):]
    block = block[:block.index('elif medium_word')]
    assert not re.search(r'\((?:a|an) [a-z]+ [a-z]+', block), block


def test_person_check_on_land_and_artifact():
    from prompt_generator import _person_problems, _strip_unpaintable, _LIGHT_WORD_RE
    land = {'name': 'Command Tower', 'card_type': 'land'}
    assert 'her' in _person_problems("King Celestia stands tall atop Command Tower, her imposing form.", land)
    assert _person_problems("A tower of golden stone rises over a lotus moat.", land) == ''
    # words from the card's own name are allowed
    assert _person_problems("The king's hall stands empty.", {'name': "King's Hall", 'card_type': 'land'}) == ''
    assert _person_problems("A scribe reads.", {'name': 'X', 'card_type': 'enchantment'}) == ''
    # an absolute phrase around an abstraction goes with it, leaving no fragment
    out = _strip_unpaintable("A gold ring rests on a cloth, its delicate curves a testament to ancient ingenuity.")
    assert out == "A gold ring rests on a cloth."
    for w in ('bright sunlight', 'sparkled', 'shining', 'late afternoon sun'):
        assert _LIGHT_WORD_RE.search(w), w


def test_body_line_names_the_first_subtype():
    from prompt_generator import _body_line
    assert "is a bat" in _body_line({'card_type': 'creature', 'type_line': 'Legendary Creature — Bat God'})
    assert "is a human" in _body_line({'card_type': 'creature', 'type_line': 'Creature — Human Wizard'})
    assert _body_line({'card_type': 'creature', 'type_line': 'Creature'}) == ''
    assert _body_line({'card_type': 'artifact', 'type_line': 'Artifact — Equipment'}) == ''


def test_tidy_strips_writer_notes():
    from prompt_generator import _tidy_prompt
    t = "Krark lunges forward, his hand grasping a staff. The air shudders. - The focal subject is Krark, the Goblin Wizard"
    assert _tidy_prompt(t) == "Krark lunges forward, his hand grasping a staff. The air shudders."
    t = "A ring rests on a cloth.\nNote: keep the ring central."
    assert _tidy_prompt(t) == "A ring rests on a cloth."
    assert _tidy_prompt("A composition of towers rises.") == "A composition of towers rises."
    assert _tidy_prompt("The composition is balanced. Fog rolls in.") == "Fog rolls in."


def test_colour_helpers():
    from prompt_generator import _names_a_colour, _is_coloured_style
    assert _names_a_colour("A turquoise ring on a grey pedestal.")
    assert not _names_a_colour("A ring on a pedestal, its surface catching the eye.")
    assert _is_coloured_style("ink illustration, coloured figures on open white paper, palette of light blue, pink")
    assert not _is_coloured_style("monochrome ink illustration, black ink only on white paper")


def test_preamble_strip_drops_headings():
    from prompt_generator import _strip_chat_preamble
    assert _strip_chat_preamble("**Scene: The Signet of Power** A silver signet ring rests atop a pedestal.") == "A silver signet ring rests atop a pedestal."
    assert _strip_chat_preamble("Scene: Signet\nA silver ring rests.") == "A silver ring rests."
    assert _strip_chat_preamble("A silver ring rests on a pedestal.") == "A silver ring rests on a pedestal."


def test_tidy_strips_markdown_and_lettering():
    from prompt_generator import _tidy_prompt, _fix_invented_cyclops
    t = "**Attrition**, a crumbling **beige** stone monument, stands at the edge of a cracked **brown** landscape."
    assert _tidy_prompt(t) == "Attrition, a crumbling beige stone monument, stands at the edge of a cracked brown landscape."
    t = "The ring sits on a cushion, with a small, polished silver 'A' prominently displayed on its face, its engravings green."
    assert "'A'" not in _tidy_prompt(t) and "engravings green" in _tidy_prompt(t)
    t = "A ring engraved with the letter K rests on cloth."
    assert _tidy_prompt(t) == "A ring rests on cloth."
    assert _fix_invented_cyclops("Its single, unblinking, red stare fixes you.", "") == "Its unblinking, red stare fixes you."


def test_evidence_medium_phrase_keeps_the_specific_surface():
    from vision_analyzer import _evidence_medium_phrase
    stored = "Art Style: Papyrus illustration\n- Medium: Papyrus parchment\nSource: X\nArt Style: Digital painting\n- Medium: Digital painting"
    assert _evidence_medium_phrase(stored, 'painted illustration') == 'papyrus parchment'
    assert _evidence_medium_phrase("- Medium: Ink illustration", 'ink illustration') == ''
    assert _evidence_medium_phrase("", 'cel animation') == ''


def test_camera_line_per_type():
    from prompt_generator import _camera_line
    assert 'face clearly visible' in _camera_line('creature')
    assert 'whole object' in _camera_line('artifact')
    assert 'establishing' in _camera_line('land')
    assert _camera_line('enchantment') == ''


def test_tidy_strips_inline_labels():
    from prompt_generator import _tidy_prompt
    t = "Keiga spreads its wings against waves. Scene: As she glides, her scales a bright blue-green."
    assert _tidy_prompt(t) == "Keiga spreads its wings against waves. As she glides, her scales a bright blue-green."


def test_unpaintable_strip_takes_any_adjective():
    from prompt_generator import _strip_unpaintable
    assert _strip_unpaintable("A ring lies on a desk, a whimsical echo of forgotten days.") == "A ring lies on a desk."
    assert _strip_unpaintable("A ring lies on a desk, an odd little nod to the past.") == "A ring lies on a desk."


def test_tidy_strips_generic_labels_and_instruction_echo():
    from prompt_generator import _tidy_prompt
    t = "Glissa strides through a forest. Color contrast: The deep green hues of the floor contrast with her grey skin."
    assert _tidy_prompt(t) == "Glissa strides through a forest. The deep green hues of the floor contrast with her grey skin."
    t = "The copper ring fills the frame, centered and large, with nothing cropped. The green study is behind it, while the ring remains the focal point."
    out = _tidy_prompt(t)
    assert 'nothing cropped' not in out and 'centered and large' not in out and 'focal point' not in out
    assert out.startswith("The copper ring") and "green study" in out


def test_tidy_strips_the_moment_label_and_trailing_fragment():
    from prompt_generator import _tidy_prompt
    t = "A silver signet ring sits atop a cushion. The Moment: Caught in a moment of repose, the ring."
    assert _tidy_prompt(t) == "A silver signet ring sits atop a cushion. Caught in a moment of repose."
    assert _tidy_prompt("Sol Ring: a gold ring rests on cloth.") == "Sol Ring: a gold ring rests on cloth."
    assert _tidy_prompt("A knight rides on, his cloak blue.") == "A knight rides on, his cloak blue."


def test_object_line_uses_a_memoised_gloss(monkeypatch):
    import sys, types
    import prompt_generator as pg
    calls = []
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda **k: (calls.append(1) or 'a finger ring with a flat engraved top.')))
    monkeypatch.setenv('OBJECT_GLOSS', '1')
    pg._OBJECT_GLOSS.clear(); pg._OBJECT_SYNONYMS.clear()
    line = pg._object_line({'name': 'Arcane Signet', 'card_type': 'artifact'}, 'm')
    assert line.startswith('Object (REQUIRED): a signet ring — a finger ring with a flat engraved top.')
    pg._object_line({'name': 'Arcane Signet', 'card_type': 'artifact'}, 'm')
    assert len(calls) == 2          # one gloss call + one synonym call, both memoised
    assert pg._object_line({'name': 'Command Tower', 'card_type': 'land'}, 'm') == ''


def test_dangling_verb_tail_is_cut():
    from prompt_generator import _fix_dangling_tail
    assert _fix_dangling_tail("The ring's filigree, its patterns dancing in the stillness, is.") == "The ring's filigree, its patterns dancing in the stillness."


def test_light_strip_cuts_phrases_when_every_sentence_has_light():
    from prompt_generator import _strip_light_words
    out = _strip_light_words("Karazikar, a Beholder, stands tall with its ivory skin glistening in the desert sun, its forty eyes fixed on the horizon.")
    assert 'glisten' not in out and out.startswith('Karazikar, a Beholder, stands tall') and 'forty eyes' in out


def test_static_opening_detection():
    from prompt_generator import _is_static_opening
    assert _is_static_opening("Loran, a Human Artificer, stands tall in worn leather. Dust blows.")
    assert not _is_static_opening("Krark lunges forward, his hand grasping a staff. Dust blows.")
    assert not _is_static_opening("Keiga rears up over the waves, wings spread wide.")
    assert not _is_static_opening("")


def test_invented_names_are_stripped():
    from prompt_generator import _strip_invented_names, _dictionary as _dict_words
    if not _dict_words():
        return
    card = {'name': 'Beast Within', 'type_line': 'Instant'}
    t = "Beast Within, a raging beast, bursts from a glade, its body tearing through the underbrush, as Benzir's voice echoes across the valley. The beast roars."
    out = _strip_invented_names(t, card)
    assert 'Benzir' not in out and out.startswith('Beast Within, a raging beast, bursts from a glade') and 'The beast roars.' in out
    # real names from the card survive; dictionary capitals survive
    t2 = "Keiga, the Tide Star, a Dragon Spirit, soars over the Pacific swell."
    assert _strip_invented_names(t2, {'name': 'Keiga, the Tide Star', 'type_line': 'Legendary Creature — Dragon Spirit'}) == t2


def test_light_strip_never_erodes_the_subject_sentence():
    from prompt_generator import _strip_light_words
    t = ("Two delicate glass vials hang suspended in mid-air, their glass glowing softly amid the greenery. "
         "Wicker beams frame the garden, sunlight glinting on the leaves. A faint trail of sawdust drifts behind the vials.")
    out = _strip_light_words(t)
    assert out.startswith("Two delicate glass vials hang suspended in mid-air") and 'sawdust' in out
    assert 'glow' not in out and 'glint' not in out and 'sunlight' not in out and len(out.split()) >= 20


def test_bold_framing_label_is_stripped():
    from prompt_generator import _tidy_prompt
    t = "Eshki bursts from the water. Bold framing: Eshki, its bare chest a sharp contrast against the mist."
    assert _tidy_prompt(t) == "Eshki bursts from the water. Eshki, its bare chest a sharp contrast against the mist."


def test_unpaintable_strip_takes_that_clauses():
    from prompt_generator import _strip_unpaintable
    assert _strip_unpaintable("The stone lies undisturbed, a stillness that belies the power it holds, its grey a contrast to the moss.") == "The stone lies undisturbed, its grey a contrast to the moss."


def test_body_line_carries_a_memoised_gloss(monkeypatch):
    import sys, types
    import prompt_generator as pg
    monkeypatch.setenv('OBJECT_GLOSS', '1')
    pg._BODY_GLOSS.clear()
    calls = []
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=lambda **k: (calls.append(1) or 'a small slender winged humanoid with pointed ears.')))
    card = {'card_type': 'creature', 'type_line': 'Legendary Creature — Faerie Warlock'}
    line = pg._body_line(card, 'm')
    assert 'is a faerie warlock — a small slender winged humanoid with pointed ears' in line
    pg._body_line(card, 'm')
    assert len(calls) == 1


def test_tidy_strips_quoted_slogans_and_camera_directions():
    from prompt_generator import _tidy_prompt
    t = "Gears rotate before the stage, as 'Wubba lubba dub dub, the machinery will never be still'."
    assert _tidy_prompt(t) == "Gears rotate before the stage."
    t = "Invasion of Zendikar,, stands proudly amid foliage. The camera cuts to a low angle, with the dryad's figure, as the hues contrast."
    out = _tidy_prompt(t)
    assert ',,' not in out and 'camera' not in out and out.startswith('Invasion of Zendikar, stands proudly')
    assert _tidy_prompt("A ring sits on a cloth.") == "A ring sits on a cloth."


def test_rune_clauses_are_stripped_as_lettering():
    from prompt_generator import _tidy_prompt
    t = "A golden signet ring, its surface etched with intricate runes, rests on a pedestal, its band a deep yellow."
    out = _tidy_prompt(t)
    assert out == 'A golden signet ring, rests on a pedestal, its band a deep yellow.'


def test_light_words_catch_casting_with_adjectives():
    from prompt_generator import _LIGHT_WORD_RE
    assert _LIGHT_WORD_RE.search('embers dance, casting a warm, golden tone on the ring')
    assert not _LIGHT_WORD_RE.search('the wizard casts a spell of binding')


def test_body_line_uses_the_whole_subtype_and_yields_to_a_steer(monkeypatch):
    import prompt_generator as pg
    monkeypatch.setenv('OBJECT_GLOSS', '0')
    card = {'card_type': 'creature', 'type_line': 'Legendary Creature — Phyrexian Zombie Elf'}
    assert 'is a phyrexian zombie elf' in pg._body_line(card)
    assert pg._body_line(card, steer_present=True).startswith('Anatomy (kept under the USER DIRECTION)')


def test_steer_leads_the_user_message(monkeypatch):
    import sys, types
    import prompt_generator as pg
    seen = []
    def chat(messages, **kw):
        seen.append(messages); return 'Glissa, the Traitor, a Phyrexian Zombie Elf, a beautiful pale elf, steps through the wood. Leaves fall.'
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    card = {'name': 'Glissa, the Traitor', 'type_line': 'Legendary Creature — Phyrexian Zombie Elf', 'oracle_text': '', 'card_type': 'creature'}
    out = pg.generate_subject_with_ai(card, None, backend='local', local_model='m', steer='Glissa is a beautiful zombie elf')
    user = seen[0][1]['content']
    assert user.startswith('USER DIRECTION (HIGHEST PRIORITY') and 'beautiful zombie elf' in user
    assert 'beautiful' in out


def test_steer_survives_rewrites():
    from prompt_generator import _ensure_steer_in_prompt, _steer_phrase
    card = {'name': 'Glissa, the Traitor', 'type_line': 'Legendary Creature — Phyrexian Zombie Elf'}
    assert _steer_phrase('Glissa is a beautiful zombie elf', card) == 'a beautiful zombie elf'
    assert _steer_phrase('make her a beautiful zombie elf', card) == 'a beautiful zombie elf'
    t = "Glissa, the Traitor, a Phyrexian Zombie Elf, lunges forward, her slender limbs unfolding. Her leafy disguise blends with the foliage."
    out = _ensure_steer_in_prompt(t, 'Glissa is a beautiful zombie elf', card)
    assert out.startswith('Glissa, the Traitor — a beautiful zombie elf —')
    kept = "Glissa, the Traitor, a beautiful zombie elf with a serene face, steps through the wood."
    assert _ensure_steer_in_prompt(kept, 'Glissa is a beautiful zombie elf', card) == kept
    assert _ensure_steer_in_prompt(t, '', card) == t


def test_hint_without_palette_stops_at_the_hue_list():
    from prompt_generator import hint_without_palette
    block = ('fine-line ink illustration, flat color fills, palette of soft pink, muted green, deep brown, '
             'dusty orange, exaggerated proportions, stiff elegant poses, recurring spirals')
    assert hint_without_palette(block) == ('fine-line ink illustration, flat color fills, exaggerated proportions, '
                                           'stiff elegant poses, recurring spirals')


def test_anticipation_and_past_tense_are_detected():
    from prompt_generator import _is_anticipation_or_past
    assert _is_anticipation_or_past("Blast Zone, a colossal volcanic peak, stood tall amidst the landscape. The ground trembled, threatening to unleash another blast.")
    assert _is_anticipation_or_past("The volcano is about to erupt, its slopes quiet.")
    assert not _is_anticipation_or_past("Blast Zone erupts, hurling purple rock and turquoise fire skyward as the peak tears itself apart.")
    assert not _is_anticipation_or_past("")


def test_event_named_land_frames_the_event():
    from prompt_generator import _camera_line, _event_in_name
    assert _event_in_name('Blast Zone') == 'blast' and _event_in_name('Wandering Fumarole') == 'fumarole'
    assert _event_in_name('Forest') == ''
    assert 'the blast itself fills the frame' in _camera_line('land', 'Blast Zone')
    assert 'establishing view' in _camera_line('land', 'Forest')


def test_flightless_creatures_lose_their_wings():
    from prompt_generator import _strip_wings, _card_flies, _body_line
    fox = {'name': 'Filigree Familiar', 'card_type': 'creature', 'type_line': 'Artifact Creature — Fox', 'oracle_text': 'When this creature enters, you gain 2 life.'}
    t = "Filigree Familiar, a cunning Fox with green eyes and a fluffy golden body, hangs from a dim blue sky by its retractable yellow wings. Its tail curls."
    out = _strip_wings(t, fox)
    assert 'wing' not in out and out.startswith('Filigree Familiar, a cunning Fox') and 'Its tail curls.' in out
    assert not _card_flies(fox) and _card_flies({'oracle_text': 'Flying\nWhen this enters, draw a card.'})
    assert 'NO wings' in _body_line(fox)
    bird = {'card_type': 'creature', 'type_line': 'Creature — Bird', 'oracle_text': 'Flying'}
    assert _strip_wings("A bird spreads its wings.", bird) == "A bird spreads its wings."


def test_light_strip_keeps_card_name_words_and_name_strip_keeps_the_subject():
    from prompt_generator import _strip_light_words, _strip_invented_names, _dictionary
    out = _strip_light_words("Radiant Dawn, an Angel, spreads her wings over the halo of the city, the glow fading.", protect=('Radiant', 'Dawn'))
    assert out.startswith('Radiant Dawn, an Angel')
    if _dictionary():
        card = {'name': 'Krark, the Thumbless', 'type_line': 'Legendary Creature — Goblin Wizard'}
        t = "Snarling Krark, the Thumbless, lunges from the shadows, as Benzir's voice echoes. He grins."
        out = _strip_invented_names(t, card)
        assert out.startswith('Snarling Krark, the Thumbless, lunges') and 'Benzir' not in out
        assert _strip_invented_names("Glittering coins tumble across the table.", {'name': 'Chance Encounter', 'type_line': 'Enchantment'}) == "Glittering coins tumble across the table."


def test_person_words_skip_hyphenated_compounds():
    from prompt_generator import _person_problems
    assert _person_problems("A hand-forged blade rests on an anvil.", {'name': 'Sunforger', 'card_type': 'artifact'}) == ''
    assert 'hand' in _person_problems("A hand grips the blade.", {'name': 'Sunforger', 'card_type': 'artifact'})
    assert _person_problems("A smith works the anvil.", {'name': 'Sunforger', 'card_type': 'artifact'}).startswith('a person in an artifact scene')


def test_setting_line_and_three_sentence_close_in_user_message(monkeypatch):
    """H79: every strip removes words; the Setting line and a consistent
    'three sentences' close put the setting back."""
    import sys, types
    import prompt_generator as pg
    seen = []
    def chat(messages, **kw):
        seen.append(messages)
        return ('Arcane Signet, a gold signet ring, rests on cracked stone. Around it a wide '
                'courtyard of pale flagstones stretches to low walls under a grey sky. Dust drifts '
                'across the stones in the still air.')
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    card = {'name': 'Arcane Signet', 'type_line': 'Artifact', 'oracle_text': '', 'card_type': 'artifact'}
    pg.generate_subject_with_ai(card, None, backend='local', local_model='m')
    user = seen[0][1]['content']
    assert 'Setting (REQUIRED): the second sentence places the object where such a thing is found' in user
    assert 'three sentences, about sixty words' in user
    assert 'two short sentences' not in user
    assert 'Setting (REQUIRED)' in pg._setting_line(False, False)
    assert 'yields to the USER DIRECTION' in pg._setting_line(False, True)
    assert 'never light' in pg._setting_line(True, False)


def test_stub_and_restarted_sentences_are_dropped():
    from prompt_generator import _limit_scene_sentences, _is_thin_scene
    t = ("A stone altar, plain and unmistakable. A stone altar, its weathered limestone surface a warm "
         "beige, stands alone. The altar's presence. A faint layer of dust coats the altar.")
    out = _limit_scene_sentences(t)
    assert out == ("A stone altar, its weathered limestone surface a warm beige, stands alone. "
                   "A faint layer of dust coats the altar.")
    assert _limit_scene_sentences('Only one.') == 'Only one.'
    assert _is_thin_scene('A ring on a cushion.')
    assert _is_thin_scene(' '.join(['word'] * 50) + '.')          # one sentence, no setting
    assert not _is_thin_scene(' '.join(['word'] * 30) + '. ' + ' '.join(['more'] * 10) + '.')


def test_thin_scene_grows_and_keeps_the_steer(monkeypatch):
    import sys, types
    import prompt_generator as pg
    calls = []
    def chat(messages, **kw):
        calls.append(messages[-1]['content'])
        if 'too thin' in messages[-1]['content']:
            return ('Glissa, the Traitor, a beautiful zombie elf, steps through the wood. Around her '
                    'the black trunks of a dead forest rise from grey mud under a low white sky. '
                    'Pale leaves drift down through the still air.')
        return 'Glissa, the Traitor, a beautiful zombie elf, steps through the wood.'
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    card = {'name': 'Glissa, the Traitor', 'type_line': 'Legendary Creature — Phyrexian Zombie Elf',
            'oracle_text': '', 'card_type': 'creature'}
    monkeypatch.setenv('SCENE_FLOOR', '1')
    out = pg.generate_subject_with_ai(card, None, backend='local', local_model='m',
                                      steer='Glissa is a beautiful zombie elf')
    assert any('too thin' in c for c in calls)
    assert 'beautiful zombie elf' in out and 'dead forest' in out
    monkeypatch.setenv('SCENE_FLOOR', '0')
    calls.clear()
    out2 = pg.generate_subject_with_ai(card, None, backend='local', local_model='m',
                                       steer='Glissa is a beautiful zombie elf')
    assert not any('too thin' in c for c in calls) and 'beautiful zombie elf' in out2


def test_scene_opens_with_the_subject_for_every_type():
    """H83: an artifact whose opening clause was cut rendered as a goblet."""
    from prompt_generator import _opens_with_subject, _ensure_subject_opening
    altar = {'name': 'Phyrexian Altar', 'type_line': 'Artifact', 'card_type': 'artifact'}
    t = 'sits atop a pedestal of dark, weathered stone, its flat surface worn smooth. A leaf lies on cracked earth.'
    assert not _opens_with_subject(t, altar)          # "stone" is not the subject
    out = _ensure_subject_opening(t, altar)
    assert out.startswith('Phyrexian Altar, a stone altar — sits atop')
    assert _ensure_subject_opening(out, altar) == out
    land = {'name': 'Arid Mesa', 'card_type': 'land'}
    assert _ensure_subject_opening('A twisting ravine bursts with life.', land).startswith('Arid Mesa — ')
    assert _ensure_subject_opening('Arid Mesa rises from the sand.', land) == 'Arid Mesa rises from the sand.'


def test_light_cut_takes_the_noun_phrase_and_a_leading_tail():
    from prompt_generator import _cut_light_phrases
    assert _cut_light_phrases('In a garden, the hands of a scale lie bare, amidst the rustle of plants and the soft '
                              'glow of afternoon sunlight through the wooden beams.') == \
        'In a garden, the hands of a scale lie bare, amidst the rustle of plants.'
    assert _cut_light_phrases('Its gemstone polished to a warm sheen, the ring rests on a stump under a pale light of dawn.') == \
        'the ring rests on a stump.'
    assert _cut_light_phrases('The ring rests on a stump.') == 'The ring rests on a stump.'


def test_second_scene_draft_is_picked_by_a_deterministic_score(monkeypatch):
    """H86b: a second, different draft; drafts are scored arithmetically
    (action verbs and colours up, static verbs and abstractions down) —
    the 8B scored every draft 9/10 and an A/B ask picked 'A' regardless."""
    import sys, types
    import prompt_generator as pg
    calls = []
    first = 'Sol Ring, a gold ring, rests on a stump. Grey mud lies around it under a white sky. Dust drifts.'
    second = 'Sol Ring, a gold ring, hangs from a chain over a red canyon. Wind hurls orange dust past it as the chain swings.'
    def chat(messages, **kw):
        last = messages[-1]['content']; calls.append(last)
        return second if 'DIFFERENT scene' in last else first
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    monkeypatch.setenv('SCENE_TAKES', '2')
    card = {'name': 'Sol Ring', 'type_line': 'Artifact', 'oracle_text': '', 'card_type': 'artifact'}
    assert pg._scene_score(second, card) > pg._scene_score(first, card)
    out = pg.generate_subject_with_ai(card, None, backend='local', local_model='m')
    assert 'canyon' in out and sum('DIFFERENT scene' in c for c in calls) == 1
    assert pg._pick_scene(first, first, card) == 'a'                   # a tie keeps the first
    assert pg._scene_score('A stump sits in the mud.', card) < pg._scene_score(first, card)   # buried subject


def test_figure_idiom_keeps_only_figure_terms():
    from prompt_generator import _figure_idiom_items
    assert _figure_idiom_items('delicate hatching, elongated faces with sparse features, flat shaded forms, '
                               'stiff elegant poses, recurring spirals and curves') == \
        ['elongated faces with sparse features', 'stiff elegant poses']
    assert _figure_idiom_items('') == []


def test_abstraction_similes_and_waiting_idioms_are_cut():
    from prompt_generator import _strip_unpaintable, _is_anticipation_or_past
    out = _strip_unpaintable('The slopes unfold like a tapestry against the sky. Amidst the peace, secrets wait to be '
                             'unlocked, hidden beneath the leaves. The table is a canvas of chance, with five coins spinning.')
    assert 'tapestry' not in out and 'secrets' not in out and 'canvas of' not in out and 'five coins spinning' in out
    assert 'anticipation' not in _strip_unpaintable("The rack's swaying fills the air with an ominous anticipation, while a figure smiles.")
    assert _is_anticipation_or_past("The rack's swaying fills the air with an ominous anticipation.")


def test_dangling_copula_after_a_strip_is_removed():
    from prompt_generator import _fix_dangling_tail, _strip_unpaintable
    assert _fix_dangling_tail(_strip_unpaintable('The table is a canvas of chance, with five coins spinning.')) == \
        'The table, with five coins spinning.'
    assert _fix_dangling_tail('The ring is red.') == 'The ring is red.'


def test_staging_read_is_composition_only_and_merged_across_references(monkeypatch):
    """H87: one picture's props (a garden, a raven on a book) were pasted into
    every card; the read names no props and several reads merge to what they share."""
    import sys, types
    import vision_analyzer as va
    seen_prompts = []
    def vision(path, prompt, **kw):
        seen_prompts.append(prompt)
        return {'a.png': 'Scenes are staged close, the subject filling the frame, dense detail, still air. The tone is calm.',
                'b.png': 'Scenes are staged from afar, the subject small under a high horizon, sparse detail, hazy air. The tone is calm.'}[path]
    def chat(messages, **kw):
        assert 'IN GENERAL' in messages[-1]['content']
        return 'Scenes are staged with dense detail and still, hazy air. The tone is calm.'
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(vision=vision, chat=chat))
    out = va.style_staging_seen('a.png', 'vm', reference_paths=['a.png', 'b.png'], text_model='tm')
    assert out == 'Scenes are staged with dense detail and still, hazy air. The tone is calm.'
    assert all('Do NOT name any object, prop' in p for p in seen_prompts) and len(seen_prompts) == 2
    assert va.style_staging_seen('a.png', 'vm').startswith('Scenes are staged close')


def test_human_subtypes_are_drawn_as_humans_and_artifact_guidance_has_no_example_nouns():
    """V24: a Human Soldier got claws, a Human Cleric a tail; two artifacts with no
    literal object became 'signet rings' parroted from the guidance's examples."""
    import prompt_generator as pg
    line = pg._body_line({'name': 'Esper Sentinel', 'type_line': 'Artifact Creature — Human Soldier',
                          'card_type': 'creature', 'oracle_text': ''}, '')
    assert 'a human being with a human face and body' in line and 'no claws' in line and 'NO wings' in line
    flier = pg._body_line({'name': 'Aven', 'type_line': 'Creature — Human Wizard', 'card_type': 'creature',
                           'oracle_text': 'Flying'}, '')
    assert 'NO wings' not in flier
    assert pg._body_line({'name': 'Glissa', 'type_line': 'Creature — Human', 'card_type': 'creature',
                          'oracle_text': ''}, '', steer_present=True).startswith('Anatomy (kept under the USER DIRECTION)')
    src = open(pg.__file__).read()
    guidance = src[src.index("'artifact': 'Depict the artifact OBJECT"):]
    guidance = guidance[:guidance.index("',\n")]
    for noun in ('signet ring', 'glass trinket', 'ornate box', 'phylactery', 'bauble'):
        assert noun not in guidance


def test_artifact_setting_line_puts_the_object_where_it_is_found():
    from prompt_generator import _setting_line
    art = _setting_line(False, False, 'artifact')
    assert 'where such a thing is found or used' in art and 'never presented for display' in art
    assert 'cushion' not in art and 'pedestal' not in art          # no example nouns
    assert 'where such a thing' not in _setting_line(False, False, 'creature')


def test_scene_score_rewards_the_name_head_noun_in_the_first_sentence():
    from prompt_generator import _scene_score
    land = {'name': 'Footfall Crater', 'card_type': 'land'}
    on = 'Footfall Crater, a gargantuan crater, splits the dry earth as boulders tumble into it.'
    off = 'Footfall Crater — a towering twisted tree, its canopy a deep green, sways as boulders tumble.'
    assert _scene_score(on, land) > _scene_score(off, land)


def test_card_back_is_a_design_and_single_limbs_are_plural():
    import prompt_generator as pg
    back = {'name': 'Card Back', 'type_line': 'Card Back', 'card_type': 'other'}
    assert pg._is_card_back(back)
    desc = pg.generate_subject_description(back)
    assert desc.startswith('An ornamental card-back design') and 'No figures' in desc
    assert pg.generate_subject_with_ai(back, None, backend='local', local_model='m') == desc   # no writer call
    assert pg._fix_invented_cyclops('Okaun lunges, his single arm raised and his one large eye blazing.', 'cyclops one eye') == \
        'Okaun lunges, his arm raised and his one large eye blazing.'
    import deck_studio as ds
    out = ds._assemble_flux_prompt(['lead'], 'ornate symmetrical decorative pattern', '', 'card_back')
    assert out.endswith('No people, no characters, no hands.')


def test_film_still_grammar_is_the_default_and_events_keep_their_force(monkeypatch):
    import sys, types
    import prompt_generator as pg
    seen = []
    def chat(messages, **kw):
        seen.append(messages[0]['content'])
        return 'Blast Zone, a volcanic crater, erupts as lava tears the ground open. Ash fills the valley. Rock rains down.'
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    monkeypatch.delenv('SCENE_MODE', raising=False)
    pg.generate_subject_with_ai({'name': 'Blast Zone', 'type_line': 'Land', 'oracle_text': '', 'card_type': 'land'},
                                None, backend='local', local_model='m')
    assert 'COMPOSITION OVERRIDE' in seen[0] and 'calm, artful film still' in seen[0]
    assert 'named for an event' in seen[0]
    seen.clear()
    pg.generate_subject_with_ai({'name': 'Arid Mesa', 'type_line': 'Land', 'oracle_text': '', 'card_type': 'land'},
                                None, backend='local', local_model='m')
    assert 'named for an event' not in seen[0]
    monkeypatch.setenv('SCENE_MODE', 'moment'); seen.clear()
    pg.generate_subject_with_ai({'name': 'Arid Mesa', 'type_line': 'Land', 'oracle_text': '', 'card_type': 'land'},
                                None, backend='local', local_model='m')
    assert 'COMPOSITION OVERRIDE' not in seen[0]


def test_second_draft_must_still_name_the_thing():
    from prompt_generator import _names_the_thing, _pick_scene
    tower = {'name': 'Command Tower', 'card_type': 'land'}
    assert _names_the_thing('Command Tower, a stone tower, rises from a lake of lilies.', tower)
    assert not _names_the_thing('Command Tower, a majestic squatting tree with gnarled branches, stands on a hill.', tower)
    tal = {'name': 'Talisman of Resilience', 'card_type': 'artifact'}
    assert not _names_the_thing('Talisman of Resilience, a small ornate box adorned with leaves, lies on its side.', tal)
    assert _names_the_thing('Talisman of Resilience, a bronze talisman, lies among ivy.', tal)
    assert _names_the_thing('Anything at all.', {'name': 'Kardur', 'card_type': 'creature'})
    a = 'Command Tower, a stone tower, rises from a lake.'
    b = 'Command Tower, a majestic squatting tree, lunges and bursts with red and gold leaves as wind hurls petals across a violet sky.'
    assert _pick_scene(a, b, tower) == 'a'


def test_compound_names_yield_their_literal_object_and_humans_count_as_persons(monkeypatch):
    import prompt_generator as pg, vision_analyzer as va
    assert pg._literal_object_from_name('Shadowspear') == 'a spear'
    assert pg._literal_object_from_name('Crawlspace') is None
    assert 'soldier' in va._PERSON_NOUNS and 'woman' in va._PERSON_NOUNS
    import sys, types
    seen = []
    def chat(messages, **kw):
        seen.append(messages[0]['content']); return 'Arid Mesa, a red canyon, drops away under a high vantage. Cliffs. Dust.'
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    pg.generate_subject_with_ai({'name': 'Arid Mesa', 'type_line': 'Land', 'oracle_text': '', 'card_type': 'land'},
                                None, backend='local', local_model='m')
    assert 'vast or towering feature' in seen[0]
    seen.clear()
    pg.generate_subject_with_ai({'name': 'Kardur', 'type_line': 'Creature — Demon', 'oracle_text': '', 'card_type': 'creature'},
                                None, backend='local', local_model='m')
    assert 'LARGE in the frame' in seen[0] and 'vast or towering feature' not in seen[0]


def test_creature_check_accepts_any_living_thing_and_objects_stay_large(monkeypatch):
    import vision_analyzer as va, prompt_generator as pg, sys, types
    assert 'cyclops' in va._CREATURE_NOUNS and 'angel' in va._CREATURE_NOUNS
    seen = []
    def chat(messages, **kw):
        seen.append(messages[0]['content']); return 'Sol Ring, a gold ring, rests on a stump. Grey mud around it. Dust drifts.'
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    pg.generate_subject_with_ai({'name': 'Sol Ring', 'type_line': 'Artifact', 'oracle_text': '', 'card_type': 'artifact'},
                                None, backend='local', local_model='m')
    assert 'shown whole and LARGE in the frame' in seen[0]


def test_script_and_handwriting_clauses_are_cut():
    from prompt_generator import _tidy_prompt
    out = _tidy_prompt("Dictate of Erebos, the contract's fine script etched into the cracked stone pedestal, appears carved into a king. The pews are warm.")
    assert 'script' not in out and 'Dictate of Erebos' in out and 'pews' in out
    assert _tidy_prompt('A talisman rests on a stone, its surface etched with fine lines. Dust drifts.') == \
        'A talisman rests on a stone, its surface etched with fine lines. Dust drifts.'


def test_place_draft_that_lost_its_thing_is_retried(monkeypatch):
    import sys, types
    import prompt_generator as pg
    calls = []
    def chat(messages, **kw):
        last = messages[-1]['content']; calls.append(last)
        if 'not about the thing itself' in last:
            return 'Command Tower, a tall stone tower, rises from a hill of red mushrooms. Ferns below. Grey sky.'
        return 'Command Tower, a majestic squatting tree with gnarled branches, stands on a hill. Ferns below. Grey sky.'
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    out = pg.generate_subject_with_ai({'name': 'Command Tower', 'type_line': 'Land', 'oracle_text': '', 'card_type': 'land'},
                                      None, backend='local', local_model='m')
    assert 'stone tower' in out and any('not about the thing itself' in c for c in calls)


def test_creature_type_appositive_never_doubles_a_comma():
    from prompt_generator import _ensure_creature_type_in_prompt
    out = _ensure_creature_type_in_prompt('Ohran Frostfang, a Snake, stretches across the floor.',
                                          {'name': 'Ohran Frostfang', 'type_line': 'Snow Creature — Snake', 'card_type': 'creature'})
    assert ',,' not in out and out.startswith('Ohran Frostfang, a Snake,')


def test_tonal_key_and_subject_items_and_minimal_scene(monkeypatch):
    import vision_analyzer as va, prompt_generator as pg, deck_studio as ds
    dark = va.pixel_coverage_phrase({'paper': 0.02, 'saturation': 0.3, 'luminance': 0.22})
    assert 'dark low-key palette' in dark and 'soft muted fills' in dark
    assert 'high-key' in va.pixel_coverage_phrase({'paper': 0.05, 'saturation': 0.4, 'luminance': 0.8})
    assert 'low-key' not in va.pixel_coverage_phrase({'paper': 0.05, 'saturation': 0.4, 'luminance': 0.5})
    assert va._SUBJECT_ITEM_RE.search('winged creature') and va._SUBJECT_ITEM_RE.search('dynamic pose')
    assert not va._SUBJECT_ITEM_RE.search('elongated faces with sparse features')
    # legacy single reference on a multi-reference deck averages again; a deliberate one stays
    meta = {'style_reference': {'enabled': True, 'tokens': 729, 'strength': 1.0, 'max_images': 1, 'average': True, 'user_set': True},
            'inspiration_images': [{'filename': 'a.png'}, {'filename': 'b.png'}, {'filename': 'c.png'}]}
    assert ds._style_reference_settings(meta)['max_images'] == 4
    meta['style_reference']['max_images_user_set'] = True
    assert ds._style_reference_settings(meta)['max_images'] == 1
    # the minimal scene never carries mana-colour filler
    m = pg.minimal_scene({'name': 'Koma, Cosmos Serpent', 'type_line': 'Legendary Creature — Serpent', 'card_type': 'creature',
                          'color_identity': ['G', 'U']})
    assert m.startswith('Koma, Cosmos Serpent, a Serpent') and 'foliage' not in m


def test_writer_retries_once_after_a_worker_crash(monkeypatch):
    import sys, types
    import prompt_generator as pg
    calls = {'n': 0}
    def chat(messages, **kw):
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('MLX worker exited unexpectedly (code -6)')
        return 'Sol Ring, a gold ring, rests on a stump. Grey mud lies around it under a white sky. Dust drifts.'
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    out = pg.generate_subject_with_ai({'name': 'Sol Ring', 'type_line': 'Artifact', 'oracle_text': '', 'card_type': 'artifact'},
                                      None, backend='local', local_model='m')
    assert 'stump' in out and calls['n'] >= 2
    def chat_dead(messages, **kw): raise RuntimeError('dead')
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat_dead))
    out2 = pg.generate_subject_with_ai({'name': 'Sol Ring', 'type_line': 'Artifact', 'oracle_text': '', 'card_type': 'artifact'},
                                       None, backend='local', local_model='m')
    assert out2.startswith('Sol Ring, a single ornate ring') and 'mist' not in out2


def test_surface_device_reaches_the_creature_body_and_props_leave_the_idiom(monkeypatch):
    import sys, types
    import prompt_generator as pg, vision_analyzer as va
    for item in ('flowing wings', 'intricate chains', 'winged creature', 'dynamic pose'):
        assert va._SUBJECT_ITEM_RE.search(item), item
    assert not va._SUBJECT_ITEM_RE.search('dark ethereal wavy lines')
    seen = []
    def chat(messages, **kw):
        seen.append(messages[1]['content'])
        return 'Koma, Cosmos Serpent, a Serpent, its coils filled with a starfield, rises from a black sea. Storm clouds. A thin glow.'
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    card = {'name': 'Koma, Cosmos Serpent', 'type_line': 'Legendary Creature — Serpent', 'oracle_text': '', 'card_type': 'creature'}
    pg.generate_subject_with_ai(card, None, backend='local', local_model='m', surface_device='a starfield galaxy texture')
    assert "Surface: in this style a figure's whole body IS a starfield galaxy texture" in seen[0]
    seen.clear()
    pg.generate_subject_with_ai({**card, 'card_type': 'land', 'type_line': 'Land'}, None, backend='local', local_model='m',
                                surface_device='a starfield galaxy texture')
    assert 'Surface:' not in seen[0]


def test_style_read_prompt_carries_no_example_hue():
    """'dusty coral' was in seven decks' palettes: the VLM prompt's own example
    hue, parroted. Instructions carry categories, never example nouns."""
    import inspect, vision_analyzer as va
    src = inspect.getsource(va.build_flux_style_block)
    assert "dusty coral" not in src


def test_setting_line_stages_like_the_references_and_vibe_is_majority():
    from prompt_generator import _setting_line
    import vision_analyzer as va
    s = _setting_line(False, False, 'creature', 'Scenes are staged with a low horizon under a vast pale sky. The tone is grim.')
    assert 'Stage it the way this style stages every scene — with a low horizon under a vast pale sky —' in s
    assert 'Stage it' not in _setting_line(False, False, 'creature', '')
    descs = 'Vibe: sinister, ominous, otherworldly.\nVibe: nightmarish, ominous, otherworldly, eerie\nVibe: ominous, ethereal, cosmic'
    assert va._extract_vibe(descs) == ['ominous', 'otherworldly']
    assert va._extract_vibe('Vibe: calm, gentle') == ['calm', 'gentle']
    assert va._extract_vibe('') == []


def test_surface_device_is_guaranteed_in_the_opening():
    from prompt_generator import _ensure_surface_device
    c = {'name': 'Koma, Cosmos Serpent', 'type_line': 'Legendary Creature — Serpent', 'card_type': 'creature'}
    out = _ensure_surface_device('Koma, Cosmos Serpent, a Serpent, coils through the black swamp, the starfield swirling around it.', c, 'starfield')
    assert out.startswith('Koma, Cosmos Serpent, a Serpent, its whole body made of starfield, silhouette and all, coils')
    assert ',,' not in out
    low = _ensure_surface_device('Koma, Cosmos Serpent, a mighty Serpent, coils on the floor.', c, 'starfield')
    assert low.startswith('Koma, Cosmos Serpent, a mighty Serpent, its whole body made of starfield, silhouette and all, coils')
    bare = _ensure_surface_device('Koma, Cosmos Serpent coils on the floor.', c, 'starfield')
    assert bare.startswith('Koma, Cosmos Serpent, its whole body made of starfield, silhouette and all, coils')
    kept = 'Koma, Cosmos Serpent, a Serpent whose body is a starfield, rises.'
    assert _ensure_surface_device(kept, c, 'starfield') == kept
    assert _ensure_surface_device('Sol Ring rests.', {'name': 'Sol Ring', 'card_type': 'artifact'}, 'starfield') == 'Sol Ring rests.'
    assert _ensure_surface_device('x', c, '') == 'x'


def test_medium_vote_is_a_majority_of_reads():
    import vision_analyzer as va
    lines = ['Art Style: Highly detailed digital painting', 'Medium: Digital painting with 3D modeling techniques',
             'Art Style: Digital painting with atmospheric brushwork']
    assert va._majority_medium(lines) == 'painted illustration'
    assert va._evidence_medium_vote('\n'.join(lines)) == 'painted illustration'
    assert va._majority_medium(['Art Style: cel animation', 'Art Style: 3D render', 'Medium: cel-shaded animation']) == 'cel animation'
    assert va._majority_medium(['Vibe: calm']) == ''


def test_sky_versus_subject_key_is_measured():
    import vision_analyzer as va
    pale = va.pixel_coverage_phrase({'paper': 0.02, 'saturation': 0.3, 'luminance': 0.45, 'lum_top': 0.7, 'lum_mid': 0.3})
    assert 'pale luminous sky behind a darker silhouetted subject' in pale
    bright = va.pixel_coverage_phrase({'paper': 0.02, 'saturation': 0.3, 'luminance': 0.45, 'lum_top': 0.2, 'lum_mid': 0.6})
    assert 'bright subject against a dark ground' in bright
    flat = va.pixel_coverage_phrase({'paper': 0.02, 'saturation': 0.3, 'luminance': 0.45, 'lum_top': 0.45, 'lum_mid': 0.44})
    assert 'sky' not in flat and 'ground' not in flat


def test_edge_hardness_and_measured_hues(tmp_path):
    from PIL import Image, ImageDraw
    import vision_analyzer as va
    hard = Image.new('RGB', (200, 200), (250, 250, 250))
    d = ImageDraw.Draw(hard)
    for x in range(0, 200, 8):
        d.line([x, 0, x, 200], fill=(0, 0, 0), width=2)
    soft = Image.new('RGB', (200, 200), (40, 40, 90))
    for y in range(200):
        ImageDraw.Draw(soft).line([0, y, 200, y], fill=(40, 40, 90 + y // 4))
    ph, ps = tmp_path / 'hard.png', tmp_path / 'soft.png'; hard.save(ph); soft.save(ps)
    assert va.pixel_edge_hardness(ph) > 0.13 > va.pixel_edge_hardness(ps)
    st = va.pixel_style_stats(ps, [ps, ps])
    assert st['hardness'] < 0.115 and isinstance(st['hues'], list)


def test_pastel_hue_names():
    import vision_analyzer as va
    assert va._hue_name(340, 0.25, 0.9) == 'pale pink'
    assert va._hue_name(345, 0.4, 0.85) == 'pink'
    assert va._hue_name(20, 0.35, 0.9) == 'coral'
    assert va._hue_name(345, 0.4, 0.3) == 'maroon'
    assert va._hue_name(190, 0.6, 0.6) == 'cyan'


def test_limbless_kinds_keep_their_anatomy_under_a_steer(monkeypatch):
    import sys, types
    import prompt_generator as pg, vision_analyzer as va
    pg._BODY_GLOSS['serpent'] = 'Long, slender body, narrow head, no limbs, no wings, tapering tail'
    card = {'name': 'Koma, Cosmos Serpent', 'type_line': 'Legendary Creature — Serpent', 'card_type': 'creature', 'oracle_text': ''}
    assert pg._anatomy_negatives(card) == ['no limbs', 'no wings'] and pg._limbless(card)
    line = pg._body_line(card, '', steer_present=True)
    assert line.startswith('Anatomy (kept under the USER DIRECTION): a serpent has no limbs, no wings')
    seen = []
    def chat(messages, **kw):
        seen.append(messages[1]['content'])
        return 'Koma, Cosmos Serpent, a Serpent, a long writhing coiling serpent, stretches across the dusty plain. Boulders lie about under the stars.'
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(chat=chat))
    out = pg.generate_subject_with_ai(card, None, backend='local', local_model='m', steer='Koma the long, writhing, coiling serpent')
    assert 'Anatomy (kept under the USER DIRECTION)' in seen[0]
    assert 'no legs and no arms' in out and 'writhing' in out
    # the inspector flags legs on a limbless kind
    def vision(path, prompt, **kw):
        return 'heads=1; arms=0; hands=0; legs=4; wings=0; copies=1; text=no; signature=no; subject=yes; hands_ok=yes; composition=yes; face=yes; body=yes'
    monkeypatch.setitem(sys.modules, 'mlx_llm', types.SimpleNamespace(vision=vision, chat=chat))
    monkeypatch.setenv('INSPECT_SUBJECT', '0'); monkeypatch.setenv('INSPECT_CENTRE_TEXT', '0')
    defects = va.inspect_render('x.png', 'Koma, Cosmos Serpent', 'creature', 'vm', flies=False, limbless=True)
    assert 'limbs on a limbless creature' in defects


def test_film_names_classify_as_photograph_and_camera_idioms_are_dropped():
    import vision_analyzer as va
    assert va._classify_style_medium("70's kung fu movie", 'm') == 'photograph'
    assert va._classify_style_medium('Wes Anderson film', 'm') == 'photograph'
    assert va._classify_style_medium('Sergio Leone westerns', 'm') == ''      # no keyword: the model is asked next
    assert va._UNDRAWABLE_ITEM_RE.search('dramatic sweeping camera movements')
    assert va._UNDRAWABLE_ITEM_RE.search('exaggerated dynamic fight choreography')
    assert not va._UNDRAWABLE_ITEM_RE.search('foggy landscapes')
