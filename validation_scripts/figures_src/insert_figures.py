#!/usr/bin/env python
"""Puts each figure inline, right after the sentence it illustrates.

Replaces the appended "## Figures" sections: every figure is inserted at the end of
the paragraph or bullet list that states its conclusion, so the map sits with the
claim instead of in a gallery at the bottom. Anchors are a unique phrase from that
sentence; the first occurrence (the summary) is the one used.
"""
import json, re

MAN = {f["id"]: f for f in json.load(open("/root/validation_external/figs/web/manifest.json"))}
REP = "/root/MoMProduction/validation_scripts/report"
ANCHORS = {
 "DFO_report.md": [
  ("3.3x more likely", "dfo_1_plain_vs_sudden"),
  ("Recurrent floods follow", "dfo_2_recurrent_follows_plain"),
  ("97–98% of floodplain", "dfo_3_plain_mostly_dry"),
  ("no floodplain north of 60", "dfo_4_north_no_plain"),
  ("Class 2 is never used", "dfo_5_sudden_vs_recurrent")],
 "VIIRS_report.md": [
  ("VIIRS flags about 10x", "viirs_1_vs_dfo_area"),
  ("3.2x more likely to be flagged", "viirs_2_plain_enrichment"),
  ("75% of floodplain pixels in these regions", "viirs_3_plain_unflagged"),
  ("Patagonia", "viirs_4_patagonia"),
  ("12.4% ever flagged", "viirs_5_one_vs_five_day")],
 "DFO_vs_VIIRS_report.md": [
  ("DFO is mostly a subset", "x_1_dfo_subset_of_viirs"),
  ("Jaccard index", "x_1b_dfo_not_confirmed"),
  ("flagged 5+ times", "x_2_persistence_confirms"),
  ("nearly every 1° cell", "x_3_coarse_agreement_artefact"),
  ("2.7x better", "x_4_viirs_beats_plain"),
  ("if anything, further from DFO", "x_5_five_day_further")],
 "AI4G_and_model_extents_report.md": [
  ("73% of the floodplain lies inside", "a_1_plain_vs_model_extent"),
  ("rising monotonically to 43%", "a_2_depth_follows_plain"),
  ("North of 60°N GFPLAIN", "a_3_north_models_only"),
  ("88% of DFO recurrent", "a_4_dfo_inside_ai4g"),
  ("Counting all observed area", "a_4b_observed_but_no_sar"),
  ("73% of that is its own exclusion mask", "a_5_exclusion_mask"),
  ("Both inflated layers are the ones tiled", "a_6_pmtiles_inflated")],
}
NOTE = ("Figures are rendered from the same rasters the tables are computed from, in the "
        "stack the MoM map itself uses (MapLibre GL, OpenFreeMap positron), and screenshotted "
        "with Playwright; `figures_src/` holds the generator. A window is chosen to show the "
        "effect, not to be representative, so its numbers differ from the global ones.")


def ns(v, pos, neg):
    return f"{abs(v):.1f}{pos if v >= 0 else neg}"


def caption(f):
    w, s, e, n = f["bounds"]
    parts = []
    for line in f["stats"][1:]:
        label, rest = line.split(": ", 1)
        pct = rest.split('(')[1].rstrip(')').replace(' of this window', '')
        parts.append(f"{label} {pct}")
    return (f"*{f['title']}. Window {ns(s,'N','S')}–{ns(n,'N','S')}, {ns(w,'E','W')}–{ns(e,'E','W')}, "
            f"{f['res_note']}: " + "; ".join(parts) + ".*")


FIG_RE = re.compile(r"^\s*!\[.*\]\(figures/.*\.png\)\s*$")
CAP_RE = re.compile(r"^\s*\*.*Window .*\*\s*$")


def strip_previous(text):
    """Remove an earlier run's gallery and any inline figures, so this is idempotent."""
    cut = text.rfind("\n## Figures\n")
    if cut != -1:
        text = text[:cut].rstrip() + "\n"
    out = []
    for line in text.split("\n"):
        if FIG_RE.match(line) or CAP_RE.match(line):
            continue
        out.append(line)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text)


for report, items in ANCHORS.items():
    path = f"{REP}/{report}"
    text = strip_previous(open(path).read())
    lines = text.split("\n")
    for anchor, fid in items:
        f = MAN[fid]
        idx = next((i for i, l in enumerate(lines) if anchor in l), None)
        if idx is None:
            print(f"  !! anchor not found in {report}: {anchor}")
            continue
        m = re.match(r"^(\s*)([-*]|\d+\.)\s+", lines[idx])
        if m:                      # inside a list: indent to the item's text,
            pad = " " * len(m.group(0))   # and go past its wrapped lines
            at = idx + 1
            while (at < len(lines) and lines[at].strip()
                   and not re.match(r"^\s*([-*]|\d+\.)\s", lines[at])):
                at += 1
        else:                      # plain paragraph: after its last line
            pad = ""
            at = idx + 1
            while at < len(lines) and lines[at].strip():
                at += 1
        block = ["", f"{pad}![{f['title']}](figures/{f['png']})", "", pad + caption(f), ""]
        lines[at:at] = block
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    if NOTE not in text:
        text = text.rstrip() + "\n\n---\n\n" + NOTE + "\n"
    open(path, "w").write(text)
    print(f"{report}: {len(items)} figures inline")
