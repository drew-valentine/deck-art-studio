"""Regression tests for card_frame_renderer code-review fixes.

Covers:
  (a) SVG injection via unescaped P/T (text_overrides) — must escape, not raise.
  (b) Unknown mana token (e.g. {XYZ}) still produces valid, rasterizable SVG.
  (c) A huge user rules_font_size is clamped so the render returns quickly.
  (d) Battle detection honors a type_line text override (designer/composite parity).
"""

import time

import cairosvg
import pytest

import card_frame_renderer as cfr
from card_frame_renderer import (
    CardData,
    create_card_frame_svg,
    parse_mana_cost,
    resolve_frame_settings,
    composite_card_preview,
    _max_fitting_rules_font,
    _build_card_data,
    _is_battle,
    _esc,
    _start_loyalty_badge_svg,
    _battle_defense_shield_svg,
)


def _make_art(tmp_path):
    """Write a tiny solid PNG to act as card art and return its path."""
    from PIL import Image
    p = tmp_path / "art.png"
    Image.new("RGB", (120, 120), (60, 90, 140)).save(p)
    return str(p)


# ---------------------------------------------------------------------------
# (a) SVG injection through power/toughness (user-editable text_overrides)
# ---------------------------------------------------------------------------
class TestPTInjection:
    def test_pt_with_angle_brackets_is_escaped_not_raw(self):
        card = CardData(
            name="Injection Test",
            mana_cost="{2}{R}",
            type_line="Creature — Goblin",
            oracle_text="Haste.",
            power="<script>alert(1)</script>",
            toughness="1<2",
            colors=["R"],
            color_identity=["R"],
        )
        svg = create_card_frame_svg(card, {})
        # No raw markup from the P/T values leaks into the document.
        assert "<script>" not in svg
        # It IS present, but escaped.
        assert "&lt;script&gt;" in svg
        assert "1&lt;2" in svg
        # And cairosvg can rasterize it (a raw '<' would raise / blank the card).
        cairosvg.svg2png(bytestring=svg.encode("utf-8"),
                         output_width=100, output_height=140)

    def test_pt_override_composites_without_raising(self, tmp_path):
        art = _make_art(tmp_path)
        card_dict = {
            "name": "Injection Test",
            "mana_cost": "{2}{R}",
            "type_line": "Creature — Goblin",
            "oracle_text": "Haste.",
            "colors": ["R"],
            "color_identity": ["R"],
            "frame_overrides": {
                "text_overrides": {"power": "<script>", "toughness": "1<2"},
            },
        }
        fs = resolve_frame_settings(card_dict)
        # Should return PNG bytes, not raise from cairosvg.
        png = composite_card_preview(card_dict, art, fs)
        assert png[:8] == b"\x89PNG\r\n\x1a\n"

    def test_loyalty_badge_helper_escapes(self):
        parts = "".join(_start_loyalty_badge_svg("4<5", 100.0, 100.0))
        assert ">4<5<" not in parts
        assert "4&lt;5" in parts

    def test_defense_shield_helper_escapes(self):
        parts = "".join(_battle_defense_shield_svg("<b>", 100.0, 100.0))
        assert "<b>" not in parts
        assert "&lt;b&gt;" in parts

    def test_esc_helper(self):
        assert _esc('a<b>&"c') == 'a&lt;b&gt;&amp;&quot;c'


# ---------------------------------------------------------------------------
# (b) Unknown mana token must not produce invalid SVG
# ---------------------------------------------------------------------------
class TestUnknownManaToken:
    def test_unknown_token_parses(self):
        assert parse_mana_cost("{XYZ}") == ["XYZ"]

    def test_unknown_token_renders_valid_svg(self):
        card = CardData(
            name="Weird Cost",
            mana_cost="{XYZ}{2}",
            type_line="Instant",
            oracle_text="Do a thing.",
            colors=["R"], color_identity=["R"],
        )
        svg = create_card_frame_svg(card, {})
        # cairosvg rasterizes cleanly (the fallback gray-circle glyph is valid).
        cairosvg.svg2png(bytestring=svg.encode("utf-8"),
                         output_width=100, output_height=140)

    def test_adversarial_mana_token_is_escaped(self):
        # A token containing markup must never emit a raw tag via the fallback.
        card = CardData(
            name="Nasty Cost",
            mana_cost="{<img>}",
            type_line="Instant",
            oracle_text="",
            colors=["R"], color_identity=["R"],
        )
        svg = create_card_frame_svg(card, {})
        assert "<img>" not in svg
        cairosvg.svg2png(bytestring=svg.encode("utf-8"),
                         output_width=100, output_height=140)


# ---------------------------------------------------------------------------
# (c) Huge rules_font_size is clamped — must return quickly
# ---------------------------------------------------------------------------
class TestRulesFontClamp:
    def test_huge_desired_returns_quickly_and_clamped(self):
        box_h = 100.0

        def measure(f):
            # Text taller than the box for any font above box_h.
            return float(f)

        start = time.perf_counter()
        result = _max_fitting_rules_font(measure, box_h, 10_000_000)
        elapsed = time.perf_counter() - start

        # Clamp caps the starting font at 200, so at most ~100 decrements run.
        assert elapsed < 1.0
        assert result <= 200
        # Largest font <= box_h given measure(f) == f.
        assert result == 100

    def test_normal_desired_unaffected(self):
        # A reasonable request that already fits returns the request unchanged.
        assert _max_fitting_rules_font(lambda f: 0.0, 500.0, 40) == 40


# ---------------------------------------------------------------------------
# (d) Battle detection honors a type_line override
# ---------------------------------------------------------------------------
class TestBattleDetectionOverride:
    def test_build_card_data_reflects_type_line_override(self):
        card_dict = {
            "name": "Not A Battle",
            "type_line": "Creature — Human",
            "mana_cost": "{R}",
            "oracle_text": "",
            "colors": ["R"], "color_identity": ["R"],
        }
        fs = resolve_frame_settings(card_dict)
        assert _is_battle(_build_card_data(card_dict, fs)) is False

        card_dict["frame_overrides"] = {
            "text_overrides": {"type_line": "Battle — Siege"}
        }
        fs2 = resolve_frame_settings(card_dict)
        assert _is_battle(_build_card_data(card_dict, fs2)) is True

    def test_composite_routes_to_battle_on_override(self, tmp_path, monkeypatch):
        art = _make_art(tmp_path)
        calls = []
        real = cfr._render_battle_composite

        def spy(card_dict, card, fs, art_img):
            calls.append(card.type_line)
            return real(card_dict, card, fs, art_img)

        monkeypatch.setattr(cfr, "_render_battle_composite", spy)

        # Raw type is a creature; override promotes it to a Battle.
        promoted = {
            "name": "Overridden Siege",
            "type_line": "Creature — Human",
            "mana_cost": "{1}{R}",
            "oracle_text": "",
            "defense": "5",
            "colors": ["R"], "color_identity": ["R"],
            "frame_overrides": {
                "text_overrides": {"type_line": "Battle — Siege"},
            },
        }
        fs = resolve_frame_settings(promoted)
        composite_card_preview(promoted, art, fs)
        assert calls, "type_line override to Battle must route through battle composite"

        # Raw type is a Battle; override demotes it to a creature.
        calls.clear()
        demoted = {
            "name": "Demoted",
            "type_line": "Battle — Siege",
            "mana_cost": "{1}{R}",
            "oracle_text": "Haste.",
            "colors": ["R"], "color_identity": ["R"],
            "frame_overrides": {
                "text_overrides": {
                    "type_line": "Creature — Goblin",
                    "power": "2", "toughness": "2",
                },
            },
        }
        fs2 = resolve_frame_settings(demoted)
        composite_card_preview(demoted, art, fs2)
        assert not calls, "type_line override away from Battle must NOT route through battle composite"
