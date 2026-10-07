#!/usr/bin/env python
"""Builds one georeferenced PNG overlay per report conclusion, plus a manifest.

Each figure is a small sample window where a single claim from one of the reports
can be seen: the layers it involves are read straight from the rasters the report
was computed from, combined into mutually exclusive classes (first rule wins), and
written as an RGBA PNG with the window's bounds. figs/web/manifest.json drives the
MapLibre page that is screenshotted by shoot.py, and carries the local numbers for
the caption, so each figure states the same statistic for its own window.
"""
import json, math, os
import numpy as np
from PIL import Image
from osgeo import gdal

gdal.UseExceptions()
V = "/root/MoMProduction/validation_scripts"
W = "/root/validation_external/work"
OUT = "/root/validation_external/figs/web"
R = 6371.0072

# grids: every layer of one figure must come from the same grid
GRIDS = {
    "30s": dict(res=1 / 120.0, top=90.0, left=-180.0, w=43200, h=21600),
    "dfo": dict(res=1 / 480.0, top=80.0, left=-180.0, w=172800, h=67200),
}
LAYERS = {
    # 30 arcsec grid
    "plain":      ("30s", f"{W}/plains_exact_30s.tif", 2, "ge", 8),
    "plain_any":  ("30s", f"{W}/plains_exact_30s.tif", 2, "gt", 0),
    "plain_dec":  ("30s", f"{W}/plains_pct_30s.tif", 1, "ge", 50),
    "extent":     ("30s", f"{W}/extent_pct_30s.tif", 1, "ge", 50),
    "ai4g_flood": ("30s", "/root/validation_external/ai4g_30s.tif", 1, "eq", 2),
    "ai4g_mask":  ("30s", "/root/validation_external/ai4g_30s.tif", 1, "eq", 1),
    "ai4g_dry":   ("30s", "/root/validation_external/ai4g_30s.tif", 1, "eq", 0),
    "dfo_s":      ("30s", f"{W}/dfo_30s.tif", 2, "gt", 0),
    "dfo_s5":     ("30s", f"{W}/dfo_30s.tif", 3, "ge", 5),
    "dfo_r":      ("30s", f"{W}/dfo_30s.tif", 4, "gt", 0),
    "v1":         ("30s", f"{W}/viirs1d_30s.tif", 2, "gt", 0),
    "v1_5":       ("30s", f"{W}/viirs1d_30s.tif", 3, "ge", 5),
    "v5":         ("30s", f"{W}/viirs5d_30s.tif", 2, "gt", 0),
    "land":       ("30s", f"{W}/land_30s.tif", 2, "ge", 8),
    # native DFO grid (1/480 deg), as the DFO and VIIRS reports used
    "n_plain":    ("dfo", f"{V}/dfo_accumulation/river_plains_mask.tiff", 1, "gt", 0),
    "n_land":     ("dfo", f"{V}/dfo_accumulation/dfo_land_mask.tiff", 1, "gt", 0),
    "n_dfo_s":    ("dfo", f"{V}/dfo_accumulation/dfo_accumulater.tiff", 1, "gt", 0),
    "n_dfo_s5":   ("dfo", f"{V}/dfo_accumulation/dfo_accumulater.tiff", 1, "ge", 5),
    "n_dfo_r":    ("dfo", f"{V}/dfo_accumulation/dfo_accumulater.tiff", 2, "gt", 0),
    "n_v1":       ("dfo", f"{V}/viirs_accumulation/viirs_on_dfo_grid.tiff", 1, "gt", 0),
    "n_v5":       ("dfo", f"{V}/viirs5day_merged/viirs5day_janaug_on_dfo_grid.tiff", 1, "gt", 0),
}
# depth, drawn as a graded layer rather than a class
DEPTH = f"{W}/flood_max_30s_cm.tif"

_open = {}


def read(name, bbox):
    grid, path, band, op, val = LAYERS[name]
    g = GRIDS[grid]
    ds = _open.get(path) or gdal.Open(path)
    _open[path] = ds
    x0 = int(math.floor((bbox[0] - g["left"]) / g["res"]))
    x1 = int(math.ceil((bbox[2] - g["left"]) / g["res"]))
    y0 = int(math.floor((g["top"] - bbox[3]) / g["res"]))
    y1 = int(math.ceil((g["top"] - bbox[1]) / g["res"]))
    a = ds.GetRasterBand(band).ReadAsArray(x0, y0, x1 - x0, y1 - y0).astype(np.int64)
    m = {"gt": a > val, "ge": a >= val, "eq": a == val}[op]
    bounds = (g["left"] + x0 * g["res"], g["top"] - y1 * g["res"],
              g["left"] + x1 * g["res"], g["top"] - y0 * g["res"])
    return m, bounds, grid


def read_values(path, band, bbox, grid):
    g = GRIDS[grid]
    ds = _open.get(path) or gdal.Open(path)
    _open[path] = ds
    x0 = int(math.floor((bbox[0] - g["left"]) / g["res"]))
    x1 = int(math.ceil((bbox[2] - g["left"]) / g["res"]))
    y0 = int(math.floor((g["top"] - bbox[3]) / g["res"]))
    y1 = int(math.ceil((g["top"] - bbox[1]) / g["res"]))
    return ds.GetRasterBand(band).ReadAsArray(x0, y0, x1 - x0, y1 - y0)


def areas(shape, bounds, grid):
    res = GRIDS[grid]["res"]
    lat_top = bounds[3] - np.arange(shape[0]) * res
    km2 = R * R * np.radians(res) * (np.sin(np.radians(lat_top)) - np.sin(np.radians(lat_top - res)))
    return np.repeat(km2[:, None], shape[1], axis=1)


BOX = {
    "bangladesh": (88.8, 23.2, 91.6, 25.6),
    "amazon":     (-62.0, -4.2, -59.0, -1.8),
    "mississippi": (-91.8, 31.6, -89.4, 34.0),
    "siberia":    (66.0, 60.2, 70.0, 63.0),
    "patagonia":  (-71.5, -50.5, -68.0, -47.5),
    "niger":      (-5.5, 13.8, -3.0, 15.8),
    "mekong":     (104.6, 9.6, 107.0, 11.6),
    "indus":      (68.0, 25.2, 71.0, 28.0),
    "pantanal":   (-58.2, -19.2, -55.8, -16.8),
    "ganges_up":  (80.0, 25.0, 83.0, 27.0),
    # windows picked from the 0.1 degree table as the places where these products
    # disagree most: lake-rich Canadian shield, and the salt lakes of SW Australia
    "shield":     (-103.0, 54.6, -100.0, 57.0),
    "quebec":     (-73.0, 52.6, -70.0, 55.0),
    "australia":  (122.0, -33.4, 125.0, -31.0),
}
BLUE, RED, ORANGE, PURPLE, GREEN, YELLOW, GREY, PINK, TEAL = (
    "#3182bd", "#e6191c", "#ff7f00", "#7a3fa0", "#2ca25f", "#e8c601", "#9e9e9e", "#f768a1", "#1fa8a0")

FIGURES = [
 # ── DFO_report.md ────────────────────────────────────────────────────────────
 dict(id="dfo_1_plain_vs_sudden", report="DFO_report.md", box="shield", grid="dfo",
      title="DFO sudden flood against river floodplains",
      claim="Floodplain is 10.5% of land and holds 28% of the pixels ever flagged as sudden flood (risk ratio 3.3x) - so most sudden flood is off the floodplain.",
      rules=[("sudden flood on floodplain", RED, "n_dfo_s & n_plain"),
             ("sudden flood off floodplain", ORANGE, "n_dfo_s & ~n_plain"),
             ("floodplain, never flagged", BLUE, "n_plain")]),
 dict(id="dfo_2_recurrent_follows_plain", report="DFO_report.md", box="bangladesh", grid="dfo",
      title="DFO recurrent flood hugs the floodplain twice as closely",
      claim="A floodplain pixel is 7.2x more likely to be flagged as recurrent flood (class 2) than other land, against 3.3x for sudden flood.",
      rules=[("recurrent flood on floodplain", RED, "n_dfo_r & n_plain"),
             ("recurrent flood off floodplain", ORANGE, "n_dfo_r & ~n_plain"),
             ("floodplain, never flagged", BLUE, "n_plain")]),
 dict(id="dfo_3_plain_mostly_dry", report="DFO_report.md", box="amazon", grid="dfo",
      title="Inside a region that floods, almost all of the floodplain stays dry",
      claim="Within flooded 1 degree regions the floodplain holds 38% of the sudden-flood pixels, yet 97-98% of floodplain pixels were never flagged in eight months.",
      rules=[("sudden flood on floodplain", RED, "n_dfo_s & n_plain"),
             ("sudden flood off floodplain", ORANGE, "n_dfo_s & ~n_plain"),
             ("floodplain, never flagged", BLUE, "n_plain")]),
 dict(id="dfo_4_north_no_plain", report="DFO_report.md", box="siberia", grid="dfo",
      title="North of 60N the floodplain map is empty but DFO still flags flood",
      claim="52% of sudden-flood pixels are north of 50N, and GFPLAIN maps essentially no floodplain north of 60N, so there it cannot help at all.",
      rules=[("sudden flood on floodplain", RED, "n_dfo_s & n_plain"),
             ("sudden flood, no floodplain mapped", ORANGE, "n_dfo_s & ~n_plain"),
             ("floodplain, never flagged", BLUE, "n_plain")]),
 dict(id="dfo_5_sudden_vs_recurrent", report="DFO_report.md", box="bangladesh", grid="dfo",
      title="DFO sudden and recurrent flood are different places",
      claim="Class 2 (recurrent) is water where water is expected seasonally; class 3 (sudden) is the unusual flood MoM actually scores.",
      rules=[("both classes", PURPLE, "n_dfo_s & n_dfo_r"),
             ("sudden only (class 3, scored by MoM)", RED, "n_dfo_s"),
             ("recurrent only (class 2, never used)", TEAL, "n_dfo_r"),
             ("floodplain", BLUE, "n_plain")]),
 # ── VIIRS_report.md ──────────────────────────────────────────────────────────
 dict(id="viirs_1_vs_dfo_area", report="VIIRS_report.md", box="mississippi", grid="dfo",
      title="VIIRS flags about ten times more land than DFO",
      claim="9.4% of the land domain was flagged by VIIRS at least once in eight months, against 0.97% for DFO sudden flood.",
      rules=[("flagged by both", PURPLE, "n_v1 & n_dfo_s"),
             ("VIIRS only", YELLOW, "n_v1"),
             ("DFO sudden only", RED, "n_dfo_s")]),
 dict(id="viirs_2_plain_enrichment", report="VIIRS_report.md", box="mekong", grid="dfo",
      title="VIIRS flood against river floodplains",
      claim="Floodplain is 10.5% of land and holds 27% of VIIRS-flagged pixels; a floodplain pixel is 3.2x more likely to be flagged than other land.",
      rules=[("VIIRS flood on floodplain", RED, "n_v1 & n_plain"),
             ("VIIRS flood off floodplain", YELLOW, "n_v1 & ~n_plain"),
             ("floodplain, never flagged", BLUE, "n_plain")]),
 dict(id="viirs_3_plain_unflagged", report="VIIRS_report.md", box="amazon", grid="dfo",
      title="Three quarters of the floodplain is never flagged, even where VIIRS floods",
      claim="Within flooded 1 degree regions the floodplain holds 33% of VIIRS flood pixels, but 75% of floodplain pixels there were never flagged.",
      rules=[("VIIRS flood on floodplain", RED, "n_v1 & n_plain"),
             ("VIIRS flood off floodplain", YELLOW, "n_v1 & ~n_plain"),
             ("floodplain, never flagged", BLUE, "n_plain")]),
 dict(id="viirs_4_patagonia", report="VIIRS_report.md", box="patagonia", grid="dfo",
      title="Where VIIRS flags the most, the floodplain means nothing",
      claim="At 40-50S VIIRS flagged 27% of all land at least once with no floodplain preference (risk ratio 1.1x); 45% of its flood pixels are north of 50N.",
      rules=[("VIIRS flood on floodplain", RED, "n_v1 & n_plain"),
             ("VIIRS flood off floodplain", YELLOW, "n_v1 & ~n_plain"),
             ("floodplain, never flagged", BLUE, "n_plain")]),
 dict(id="viirs_5_one_vs_five_day", report="VIIRS_report.md", box="mekong", grid="dfo",
      title="The 5-day composite flags more land than the 1-day one",
      claim="12.4% of the land domain ever flagged by the 5-day composite against 9.4% by the 1-day one, and the extra area is less floodplain-bound.",
      rules=[("flagged by both", PURPLE, "n_v1 & n_v5"),
             ("5-day only", ORANGE, "n_v5"),
             ("1-day only", YELLOW, "n_v1"),
             ("floodplain", BLUE, "n_plain")]),
 # ── DFO_vs_VIIRS_report.md ───────────────────────────────────────────────────
 dict(id="x_1_dfo_subset_of_viirs", report="DFO_vs_VIIRS_report.md", box="bangladesh", grid="dfo",
      title="DFO is mostly a subset of VIIRS, and a small one",
      claim="68% of the pixels DFO ever flagged were also flagged by VIIRS, VIIRS's ever-flagged area is 7.2x DFO's, and the pixel Jaccard is only 0.09.",
      rules=[("DFO flood confirmed by VIIRS", PURPLE, "(n_dfo_s | n_dfo_r) & n_v1"),
             ("DFO flood not flagged by VIIRS", RED, "n_dfo_s | n_dfo_r"),
             ("VIIRS only", YELLOW, "n_v1")]),
 dict(id="x_2_persistence_confirms", report="DFO_vs_VIIRS_report.md", box="shield", grid="dfo",
      title="The longer DFO sees water, the more often VIIRS agrees",
      claim="A DFO pixel flagged once is confirmed by VIIRS 53% of the time; one flagged on 5 or more days, 76-85%.",
      rules=[("DFO 5+ days, confirmed", GREEN, "n_dfo_s5 & n_v1"),
             ("DFO 5+ days, not confirmed", RED, "n_dfo_s5"),
             ("DFO 1-4 days, confirmed", TEAL, "n_dfo_s & n_v1"),
             ("DFO 1-4 days, not confirmed", ORANGE, "n_dfo_s")]),
 dict(id="x_3_coarse_agreement_artefact", report="DFO_vs_VIIRS_report.md", box="quebec", grid="dfo",
      title="At 1 degree almost every cell is flagged by VIIRS, so coarse agreement says little",
      claim="Over eight months 96% of 1 degree cells and 94% of watersheds were flagged by VIIRS at least once, which is why their Jaccard reaches 0.91-0.95.",
      grid_lines=1.0,
      rules=[("flagged by both", PURPLE, "n_v1 & n_dfo_s"),
             ("VIIRS only", YELLOW, "n_v1"),
             ("DFO sudden only", RED, "n_dfo_s")]),
 dict(id="x_4_viirs_beats_plain", report="DFO_vs_VIIRS_report.md", box="shield", grid="dfo",
      title="The other satellite locates the water better than the floodplain does",
      claim="Within regions where both flooded, VIIRS contains 69% of the DFO flood pixels (lift 6.5x) against 26% for the floodplain (lift 2.4x) - about 2.7x better.",
      rules=[("DFO flood inside VIIRS and floodplain", GREEN, "n_dfo_s & n_v1 & n_plain"),
             ("DFO flood inside VIIRS only", PURPLE, "n_dfo_s & n_v1"),
             ("DFO flood inside floodplain only", RED, "n_dfo_s & n_plain"),
             ("DFO flood in neither", ORANGE, "n_dfo_s"),
             ("floodplain", BLUE, "n_plain")]),
 dict(id="x_5_five_day_further", report="DFO_vs_VIIRS_report.md", box="bangladesh", grid="dfo",
      title="The 5-day composite is even further from DFO",
      claim="Its ever-flagged area is 9.5x DFO's (1-day: 7.2x) and the pixel Jaccard falls to 0.074 from 0.090.",
      rules=[("DFO flood confirmed by VIIRS 5-day", PURPLE, "n_dfo_s & n_v5"),
             ("DFO flood not confirmed", RED, "n_dfo_s"),
             ("VIIRS 5-day only", ORANGE, "n_v5")]),
 # ── AI4G_and_model_extents_report.md ─────────────────────────────────────────
 dict(id="a_1_plain_vs_model_extent", report="AI4G_and_model_extents_report.md", box="pantanal", grid="30s",
      title="Floodplains against the merged model extent",
      claim="73% of the floodplain lies inside the merged Aqueduct/GloFAS extent, but only 39% of that extent is floodplain; it covers 20.6% of land against 10.9%.",
      rules=[("both", PURPLE, "plain & extent"),
             ("model extent only", YELLOW, "extent"),
             ("floodplain only", BLUE, "plain")]),
 dict(id="a_2_depth_follows_plain", report="AI4G_and_model_extents_report.md", box="pantanal", grid="30s",
      title="The models put their deep water on the floodplain, their shallow water everywhere",
      claim="Of model cells shallower than 0.1 m only 11% are floodplain; at 8-16 m it is 43%.",
      rules=[("deep model flood (>=4 m) on floodplain", RED, "deep & plain"),
             ("deep model flood off floodplain", ORANGE, "deep"),
             ("shallow model flood (<0.5 m) on floodplain", TEAL, "shallow & plain"),
             ("shallow model flood off floodplain", YELLOW, "shallow"),
             ("floodplain", BLUE, "plain")]),
 dict(id="a_3_north_models_only", report="AI4G_and_model_extents_report.md", box="siberia", grid="30s",
      title="North of 60N the models flood a quarter of the land and the floodplain map is blank",
      claim="North of 60N GFPLAIN maps essentially no floodplain while the merged model extent covers 26% of the land.",
      rules=[("both", PURPLE, "plain & extent"),
             ("model extent only", YELLOW, "extent"),
             ("floodplain only", BLUE, "plain")]),
 dict(id="a_4_dfo_inside_ai4g", report="AI4G_and_model_extents_report.md", box="bangladesh", grid="30s",
      title="DFO sudden flood against ten years of Sentinel-1 flood (AI4G)",
      claim="On land AI4G assessed, 68% of the DFO sudden-flood area is inside AI4G-flooded and 32% is on land AI4G judged never flooded; in reverse DFO covers only 10.5% of the AI4G flooded area.",
      rules=[("DFO flood confirmed by AI4G", GREEN, "dfo_s & ai4g_flood"),
             ("DFO flood where AI4G saw none", RED, "dfo_s & ai4g_dry"),
             ("DFO flood under AI4G exclusion mask", GREY, "dfo_s & ai4g_mask"),
             ("AI4G flood only", TEAL, "ai4g_flood")]),
 dict(id="a_5_exclusion_mask", report="AI4G_and_model_extents_report.md", box="indus", grid="30s",
      title="AI4G's exclusion mask covers most of the land it tiles",
      claim="AI4G tiles 97.5% of the land domain but 73% of that is its exclusion mask (rough terrain, arid, urban), so it returns a verdict on only 25.9% of land.",
      rules=[("AI4G flooded", TEAL, "ai4g_flood"),
             ("AI4G assessed, never flooded", GREEN, "ai4g_dry"),
             ("AI4G exclusion mask (no verdict)", GREY, "ai4g_mask")]),
 dict(id="a_6_pmtiles_inflated", report="AI4G_and_model_extents_report.md", box="mekong", grid="30s",
      title="The floodplain PMTiles layer is drawn about 1.5x too large",
      claim="Decoding river_plains_30s.pmtiles gives 21.25 million km2 against 13.49 million in the 250 m raster (1.58x); ai4g_flood_1km.pmtiles is 1.52x too large. Both are tiled with resample=max.",
      rules=[("floodplain in both", BLUE, "plain & plain_dec"),
             ("added by the PMTiles tiling only", PINK, "plain_dec"),
             ("in the raster only", RED, "plain")]),
 dict(id="x_1b_dfo_not_confirmed", report="DFO_vs_VIIRS_report.md", box="australia", grid="dfo",
      title="Where DFO flags flood and VIIRS does not: the salt lakes of SW Australia",
      claim="32% of DFO's ever-flagged pixels were never flagged by VIIRS. The two products disagree most over shallow saline and ephemeral water, not over river flood.",
      rules=[("DFO flood confirmed by VIIRS", PURPLE, "(n_dfo_s | n_dfo_r) & n_v1"),
             ("DFO flood not flagged by VIIRS", RED, "n_dfo_s | n_dfo_r"),
             ("VIIRS only", YELLOW, "n_v1"),
             ("floodplain", BLUE, "n_plain")]),
 dict(id="a_4b_observed_but_no_sar", report="AI4G_and_model_extents_report.md", box="shield", grid="30s",
      title="Where the optical products flood and ten years of SAR saw nothing",
      claim="32% of the DFO sudden-flood area on AI4G-assessed land sits where AI4G judged the land never flooded (VIIRS 1-day: 33%). Lake-rich terrain is where the two sensor types part company.",
      rules=[("DFO flood confirmed by AI4G", GREEN, "dfo_s & ai4g_flood"),
             ("DFO flood where AI4G saw none", RED, "dfo_s & ai4g_dry"),
             ("DFO flood under AI4G exclusion mask", GREY, "dfo_s & ai4g_mask"),
             ("VIIRS flood where AI4G saw none", ORANGE, "v1 & ai4g_dry"),
             ("AI4G flood only", TEAL, "ai4g_flood")]),
]

def shape(bbox, ratio=1.85):
    """Grow the window to the screenshot's aspect ratio, in Web Mercator.

    A window is stretched vertically by Mercator the further it is from the
    equator, so a 3x2.4 degree box at 56N is taller than wide on screen and the
    figure ends up fitted to the height with empty space either side. Widening
    (or heightening) the box to match the frame fills it and shows more context.
    """
    w, s, e, n = bbox
    lat_c = (s + n) / 2.0
    cos = max(math.cos(math.radians(lat_c)), 0.15)
    dlon, dlat = e - w, n - s
    merc_h = dlat / cos
    want_lon = ratio * merc_h
    if want_lon > dlon:
        grow = min(want_lon, 4 * dlon) - dlon
        w, e = w - grow / 2, e + grow / 2
    else:
        want_lat = dlon * cos / ratio
        grow = min(want_lat, 4 * dlat) - dlat
        s, n = s - grow / 2, n + grow / 2
    return (max(w, -180), max(s, -89), min(e, 180), min(n, 89))


def build(fig):
    bbox = shape(BOX[fig["box"]])
    grid = fig["grid"]
    names = sorted({n for n in LAYERS if n in " ".join(r[2] for r in fig["rules"])},
                   key=len, reverse=True)
    env, bounds = {}, None
    for n in names:
        if LAYERS[n][0] != grid:
            continue
        m, bounds, _ = read(n, bbox)
        env[n] = m
    landname = "land" if grid == "30s" else "n_land"
    land, bounds, _ = read(landname, bbox)
    env[landname] = land
    if "deep" in " ".join(r[2] for r in fig["rules"]):
        d = read_values(DEPTH, 1, bbox, grid).astype(np.int64)
        d = np.where(d >= 65535, 0, d)
        env["deep"] = d >= 400
        env["shallow"] = (d > 0) & (d < 50)
    cls = np.zeros(land.shape, dtype=np.uint8)
    for i, (label, color, expr) in enumerate(fig["rules"], start=1):
        m = eval(expr, {"__builtins__": {}}, env) & land & (cls == 0)
        cls[m] = i
    km2 = areas(land.shape, bounds, grid)
    land_km2 = float(km2[land].sum())
    stats = [f"window: {land_km2:,.0f} km2 of land"]
    for i, (label, color, expr) in enumerate(fig["rules"], start=1):
        a = float(km2[cls == i].sum())
        stats.append(f"{label}: {a:,.0f} km2 ({100*a/land_km2:.1f}% of this window)")
    rgba = np.zeros(land.shape + (4,), dtype=np.uint8)
    for i, (label, color, expr) in enumerate(fig["rules"], start=1):
        r, g, b = (int(color[k:k+2], 16) for k in (1, 3, 5))
        sel = cls == i
        rgba[sel] = (r, g, b, 235)
    Image.fromarray(rgba, "RGBA").save(f"{OUT}/{fig['id']}.png")
    return dict(id=fig["id"], report=fig["report"], title=fig["title"], claim=fig["claim"],
                png=f"{fig['id']}.png", bounds=list(bounds), stats=stats,
                grid_lines=fig.get("grid_lines"),
                legend=[(l, c) for l, c, _ in fig["rules"]],
                res_note=("30 arcsec cells (~1 km)" if grid == "30s"
                          else "native DFO grid, 1/480 degree (~230 m)"))


def main():
    os.makedirs(OUT, exist_ok=True)
    man = []
    for fig in FIGURES:
        m = build(fig)
        man.append(m)
        print(f"{m['id']:34s} {m['stats'][0]}")
        for s in m["stats"][1:]:
            print(f"    {s}")
    json.dump(man, open(f"{OUT}/manifest.json", "w"), indent=1)
    print(f"\n{len(man)} figures -> {OUT}")


if __name__ == "__main__":
    main()