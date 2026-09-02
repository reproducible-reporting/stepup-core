<!--
SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Documentation

Note that docstrings are written in Markdown, not reStructuredText!
The docstring conventions themselves are in the top-level `CLAUDE.md`.

## Documentation Examples

Each `docs/getting_started/<example>/` directory contains a `main.sh`
that generates `stdout.txt` (the terminal output shown in the tutorial page).
To regenerate after changing example scripts, run:

```bash
cd docs/getting_started/<example>
bash main.sh
```

This runs StepUp locally and captures the output via `sed -f ../../clean_stdout.sed`.
Commit the updated `stdout.txt` alongside any source changes.

## Social Card

`docs/social-card.svg` is the editable source of the link preview image
referenced by the Open Graph and Twitter meta tags in `overrides/main.html`.
Only the exported `docs/social-card.jpg` is published,
because `mkdocs.yaml` excludes the SVG from the site.
Regenerate the JPEG after editing the SVG:

```bash
inkscape docs/social-card.svg --export-type=png --export-width=1200 --export-filename=- \
  | magick png:- -quality 90 -sampling-factor 4:2:0 -interlace JPEG -strip docs/social-card.jpg
```

The detour through PNG on standard output is deliberate.
Inkscape 1.4.4 can write a JPEG from its export dialog,
but the same export from the command line fails,
because it hands the SVG to the JPEG output extension instead of a rasterized image.

Three properties of the source must hold, and none of them is checked automatically:

- The card measures 1200 by 630 pixels, the size the meta tags declare.
- The text is set in IBM Plex Sans and Source Code Pro as live text, not as paths,
  so those fonts have to be installed for the export to look right.
  Without them, Inkscape silently substitutes and the card changes.
- The tagline is kept in step with the other copies by `snipwise.md`,
  which locates it through the `id="tagline"` attribute of its `tspan`.
  A `tspan` that lost that attribute is no longer found,
  and `snipwise check` then passes while the card drifts,
  so keep the attribute when the text is redrawn.
