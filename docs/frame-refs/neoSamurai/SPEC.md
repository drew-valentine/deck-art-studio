# Kamigawa Samurai showcase — build spec (built)

Source: cardconjurer `img/frames/neo/samurai` (pack `packNeoSamurai.js`,
`card.version = 'neoSamurai'`). Dark brushed frame -> LIGHT text everywhere
(style flag rules_text: 'light' inverts the legibility gate).

Assets (1500×2100): w/u/b/r/g/m/a base frames, crown/ (6, no artifact),
pt/ (6), stamp.png (single rare stamp).

Layout (750×1050, SAMURAI_LAYOUT): title y72–129, type y595–652, rules
y662–946, P/T (573, 930, 147×71) center (646, 971), crown (22, 17, 707×79),
stamp (326, 952, 97×44) always, avoid (922, 499).

**Deliberate divergence:** the set's artifact frame (a.png) is a borderless
variant with a TRANSPARENT rules region — light rules text over arbitrary
art is illegible, so artifact/colorless/land route to the opaque gold 'm'
frame instead. 0.000% chrome parity for the colored frames.
