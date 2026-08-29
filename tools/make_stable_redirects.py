#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2024 Toon Verstraelen <Toon.Verstraelen@UGent.be>
# SPDX-License-Identifier: LGPL-3.0-or-later
"""Write redirects from the retired `stable/` URL space to the flat documentation site.

Up to StepUp 3.2, the documentation was published per version with `mike`,
and `stable/` was the alias that readers and search engines were pointed to.
The site is now published flat, so every `stable/...` URL would return a 404.

`overrides/404.html` already rewrites such URLs in the browser,
but GitHub Pages serves that page with an HTTP 404 status,
which tells search engines to drop the old URL instead of following it.
The stubs written here are served with an HTTP 200 status
and carry both a canonical link and an instant meta refresh,
which search engines do treat as a redirect that passes ranking signals.

Usage
-----
```bash
mkdocs build --strict
python tools/make_stable_redirects.py site
```

When These Redirects Can Be Removed
-----------------------------------
They exist only to hand the ranking of the old `stable/` URLs to their flat counterparts.
Remove them once Google Search Console reports no impressions
on `stable/` URLs for several consecutive months,
and the flat URLs are indexed in their place.
Google advises keeping a redirect in place for at least a year,
so the earliest sensible moment to revisit this is one year after the first flat deployment.

Removal is a soft landing rather than a cliff:
`overrides/404.html` keeps rewriting `stable/` URLs afterwards,
so readers still reach the right page,
and only the transfer of ranking signals is lost.
"""

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

REDIRECT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Redirecting to {target}</title>
<link rel="canonical" href="{target}">
<meta http-equiv="refresh" content="0; url={target}">
</head>
<body>
<p>This page has moved to <a href="{target}">{target}</a>.</p>
</body>
</html>
"""


def read_page_urls(sitemap_path: Path) -> list[str]:
    """Collect the page URLs from a sitemap written by MkDocs.

    Parameters
    ----------
    sitemap_path
        The `sitemap.xml` in the root of a built site.

    Returns
    -------
    urls
        The page URLs, sorted, with the home page first.
    """
    root = ET.parse(sitemap_path).getroot()
    urls = [element.text.strip() for element in root.iterfind("sm:url/sm:loc", SITEMAP_NS)]
    if not urls:
        raise ValueError(f"No URLs found in {sitemap_path}")
    return sorted(urls, key=len)


def write_redirects(site_dir: Path) -> int:
    """Write a redirect stub under `stable/` for every page of a built site.

    Parameters
    ----------
    site_dir
        The output directory of `mkdocs build`.

    Returns
    -------
    count
        The number of stubs written.
    """
    urls = read_page_urls(site_dir / "sitemap.xml")
    # The home page is the shortest URL and is the prefix of all the others.
    home = urls[0]
    count = 0
    for url in urls:
        if not url.startswith(home):
            raise ValueError(f"URL {url} does not start with the home page URL {home}")
        relative = url[len(home) :]
        destination = site_dir / "stable" / relative / "index.html"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(REDIRECT_TEMPLATE.format(target=url))
        count += 1
    return count


def main() -> None:
    """Command line interface."""
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    site_dir = Path(sys.argv[1])
    count = write_redirects(site_dir)
    print(f"Wrote {count} redirect stubs under {site_dir / 'stable'}")


if __name__ == "__main__":
    main()
