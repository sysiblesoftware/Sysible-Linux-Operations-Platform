# Portal marks

The app logos shown on the portal tiles.

`controller.svg` and `slep.svg` are copied VERBATIM from each app's own
`branding/` directory — they are the canonical marks, and if branding changes
there, re-copy them here. `connect.svg` keeps Connect's own glyph but is re-cut
onto the canonical tile geometry (Connect's favicon uses a heavier 4px ring that
reads wrong beside the others at 40px). `flashback.svg`, `visualizer.svg` and
`admin.svg` are drawn here, in the same family, because those modules live in
this repo and have no separate branding directory.

The family is: a dark rounded-square tile (`#161d29` → `#0a0d14`), a 2px
brand-green ring (`#6ddb73`), and one glyph in brand green and/or brand blue
(`#7aa2ff`). Every mark is a self-contained 128×128 tile, so it renders the same
in the portal's light and dark themes without recolouring.
