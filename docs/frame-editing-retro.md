# Frame Editing — Retrospective (iteration 1)

A candid look back at how we built the frame styles (Godzilla, gradients, the
rules-text controls, and the now-removed 1993/ABU "Retro" frame), so the **n+1**
frame is faster and lands closer to its reference on the first try.

## The honest verdict

We shipped working frames, but we were **not objective about "good."** The
Godzilla frame went through synthetic component systems, an extracted nameplate,
and authentic-tint composites — all rejected as "hideous" / "tacked on" — because
we kept declaring victory from tiny crops, Python renders, and our own taste
instead of a side-by-side against the real card. The 1993/ABU frame we just
removed is the clearest example: it was in the registry and "passed" the quality
gate, yet it never actually resembled an Alpha/Beta/Unlimited card closely enough
to keep. **A frame passing our automated checks is not the same as a frame that
looks like the thing it emulates.** That gap is the core lesson.

## What went well

- **Standing on cardconjurer's assets.** Every time we hand-synthesized frame
  geometry it looked flat/plastic; every time we composited cardconjurer's real
  PNG layers it looked right. Reuse beat re-creation, decisively.
- **The quality gate caught mechanical defects.** `tools/card_quality_check.py`
  (60 renders across a battery of edge-case cards) reliably flagged text
  overflow, blank frames, and illegible boxes — the objective, measurable
  failures. Keep it and expand it.
- **Small, reversible commits once we had a good state.** The rules-text work
  (live slider → numeric readout → all styles → authoritative WYSIWYG) landed as
  four clean commits, each independently verified. That cadence worked.
- **Programmatic proof for numeric changes.** Hash-diffing renders across
  parameter values, and measuring ink density in a text band, gave fast,
  objective confirmation that a control actually changed output.

## What could have gone better

- **We iterated PAST approved states without committing them.** The user said
  "this is much much better," we didn't bank it, kept "improving," every rewrite
  landed worse, and we had to reconstruct the good version from memory. Approval
  is a checkpoint to **commit**, not a base to build past.
- **We trusted proxies for "looks right."** Hash diffs and ink counts prove a
  control *works*; they say nothing about whether a frame is *beautiful* or
  *faithful*. We repeatedly conflated "the pixels changed" with "the pixels are
  good." Only a side-by-side with the reference answers the second question.
- **No objective fidelity bar per frame.** We never defined, up front, "here is
  the reference card and here is how close we must get." So "done" drifted to
  "the assistant is tired of iterating," which is how a frame like ABU/1993
  survived without ever really matching.
- **Over-engineering under token pressure.** Synthetic `frame_system`, silhouette
  gold-fill, etc. — each "holistic" rewrite discarded what worked and burned
  tokens. The user named this directly ("waste of expensive tokens").

## The fidelity gap: why the quality gate isn't enough

`card_quality_check.py` measures **defects** (overflow, blank, illegible). It does
not measure **resemblance**. A frame can be defect-free and still look nothing
like its target. Nothing in our loop ever asked "does this look like the
reference?" in an objective, repeatable way — that judgment lived only in the
user's eyes, invoked late, after we'd already called things done.

## Playbook for the n+1 frame

1. **Pin the reference first.** Before any code: save the exact reference card
   image(s) to `docs/frame-refs/<style>/` and write a one-paragraph spec of what
   defines the style (border color/material, box shapes, fonts, art window inset,
   title treatment). No reference on disk → don't start.
2. **Source real assets before drawing anything.** Check cardconjurer's frame set
   for the era. If layered PNGs exist, composite them — do not synthesize
   geometry. Synthesizing is the last resort, not the first.
3. **Build a side-by-side proof sheet, not a crop.** A script that renders our
   frame next to the reference at the same size, for 3–5 representative cards
   (short oracle, wall-of-text, creature w/ P/T, land, multicolor). Full card,
   full frame — never a transparent-background crop.
4. **Score fidelity objectively before self-approving.** Add a resemblance pass
   to the gate: overlay/diff against the reference (structural similarity on the
   frame chrome region, palette distance on the border, box-position alignment).
   Set a numeric threshold per frame. If it fails, it is not done — regardless of
   how it "feels."
5. **One implementation, verified empirically.** Resist re-architecting a working
   render. If tempted, propose it in one sentence and let the user decide.
6. **Commit the moment it's approved.** Bank the win before touching anything
   else. Reverting from a committed good state is cheap; reconstructing an
   uncommitted one is expensive.
7. **Validate in the real browser, not just Python.** The WYSIWYG canvas and the
   final composite are different code paths; verify both, hard-refreshed.

## Concrete follow-ups this retro surfaces

- **Dead ABU code.** Removing the `oldschool` style leaves `ABU_LAYOUT`,
  `_create_abu_text_svg`, and the `frame_set == 'abu'` branches in
  `_render_image_frame` as dead code. Prune them in a dedicated cleanup commit
  (kept out of this change to stay low-risk).
- **Add a resemblance/fidelity check** to `tools/card_quality_check.py` (item 4
  above) — the single highest-leverage improvement to stop shipping
  defect-free-but-unfaithful frames.
- **Reference library.** Establish `docs/frame-refs/` so every existing and
  future frame has its target on disk to diff against.
- **Retro/SVG gradient depth.** Gradients now cover image-mode (m15, godzilla)
  and classic SVG boxes; SVG styles with no colored field boxes (e.g. the old
  retro) show no effect. A future pass could gradient the frame border/pinlines
  themselves for a fuller multi-type-land look.
