#!/usr/bin/env python
"""Appends a Figures section to each report: one map per conclusion, with the
same statistic measured inside the figure's own window."""
import json, collections
MAN = json.load(open("/root/validation_external/figs/web/manifest.json"))
REP = "/root/MoMProduction/validation_scripts/report"
by = collections.OrderedDict()
for f in MAN:
    by.setdefault(f["report"], []).append(f)

INTRO = """
## Figures

One map per conclusion above, each on a small sample window, rendered in the same stack
as the MoM map itself (MapLibre GL with the OpenFreeMap positron basemap) and
screenshotted with Playwright. The classes are mutually exclusive and come from the very
rasters the report is computed from, so a figure can be read as a picture of one row of
one table. Every figure repeats its statistic for its own window, which is why those
numbers differ from the global ones: a window is chosen to show the effect, not to be
representative. Code: `figures_src/make_figures.py`, `figures_src/web/fig_page.html`,
`figures_src/shoot.js`.
"""

def ns(v, pos, neg):
    return f"{abs(v):.1f}{pos if v >= 0 else neg}"

for report, figs in by.items():
    out = [INTRO]
    for f in figs:
        w, s, e, n = f["bounds"]
        out.append(f"### {f['title']}\n")
        out.append(f"![{f['title']}](figures/{f['png']})\n")
        out.append(f"{f['claim']}\n")
        out.append(f"Window {ns(s,'N','S')}-{ns(n,'N','S')}, {ns(w,'E','W')}-{ns(e,'E','W')}, "
                   f"{f['res_note']}. {f['stats'][0]}:\n")
        for line in f["stats"][1:]:
            out.append(f"- {line}")
        out.append("")
    with open(f"{REP}/{report}", "a") as fh:
        fh.write("\n" + "\n".join(out))
    print(f"{report}: appended {len(figs)} figures")
