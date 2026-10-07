#!/usr/bin/env bash
# Build the rasters the report/ analysis needs beyond the accumulations
# themselves. Run from validation_scripts/ with a GDAL that has gdalwarp and
# gdal_rasterize (e.g. the myenv conda env).
#
#   dfo_accumulation/dfo_on_viirs_grid.tiff   DFO counts resampled (nearest)
#                                             onto the VIIRS grid; mirror of
#                                             viirs_accumulation/viirs_on_dfo_grid.tiff
#   viirs5day_merged/viirs5day_janaug_on_dfo_grid.tiff
#                                             VIIRS 5-day counts (Jan-Aug) resampled
#                                             (nearest) onto the DFO grid
#   viirs_accumulation/viirs_land_mask.tiff   1 = land, on the VIIRS grid
#   dfo_accumulation/dfo_land_mask.tiff       1 = land, on the DFO grid
#   report/data/watersheds_0p1deg.tiff        MoM pfaf_id per 0.1 degree cell
#
# Land is the union of MoM's watershed polygons (data/watershed_shp). It is
# needed because GFPLAIN's "not floodplain" value also covers the ocean.
set -euo pipefail
BIN=${GDAL_BIN:-/root/miniconda3/envs/myenv/bin}
CO="-co TILED=YES -co BLOCKXSIZE=256 -co BLOCKYSIZE=256 -co COMPRESS=LZW -co BIGTIFF=YES"
mkdir -p report/data

# VIIRS grid: left -180, top 75, res 0.0033720001, 106761 x 40035.
VIIRS_TE="-180 -59.99802400350001 179.99810267610002 75"
VIIRS_TS="106761 40035"
# DFO grid: left -180, top 80, res 1/480, 172800 x 67200.
DFO_TE="-180 -60 180 80"
DFO_TS="172800 67200"

SHP_DIR=$(mktemp -d)
trap 'rm -rf "$SHP_DIR"' EXIT
python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" \
    ../data/watershed_shp/Watershed_pfaf_id.shp.zip "$SHP_DIR"
cp ../data/watershed_shp/Watershed_pfaf_id.{dbf,prj,shx,cpg} "$SHP_DIR"/
SHP="$SHP_DIR/Watershed_pfaf_id.shp"

if [ ! -f dfo_accumulation/dfo_on_viirs_grid.tiff ]; then
    "$BIN"/gdalwarp -r near -te $VIIRS_TE -ts $VIIRS_TS -ot UInt16 -wm 800 -multi \
        -wo NUM_THREADS=2 $CO -co NUM_THREADS=2 \
        dfo_accumulation/dfo_accumulater.tiff dfo_accumulation/dfo_on_viirs_grid.tiff
fi
if [ ! -f viirs5day_merged/viirs5day_janaug_on_dfo_grid.tiff ]; then
    "$BIN"/gdalwarp -r near -te $DFO_TE -ts $DFO_TS -ot UInt16 -wm 500 \
        $CO viirs5day_merged/viirs5day_janaug.tiff viirs5day_merged/viirs5day_janaug_on_dfo_grid.tiff
fi
[ -f viirs_accumulation/viirs_land_mask.tiff ] || "$BIN"/gdal_rasterize --config GDAL_CACHEMAX 1024 \
    -burn 1 -init 0 -ot Byte $CO -te $VIIRS_TE -ts $VIIRS_TS "$SHP" viirs_accumulation/viirs_land_mask.tiff
[ -f dfo_accumulation/dfo_land_mask.tiff ] || "$BIN"/gdal_rasterize --config GDAL_CACHEMAX 1024 \
    -burn 1 -init 0 -ot Byte $CO -te $DFO_TE -ts $DFO_TS "$SHP" dfo_accumulation/dfo_land_mask.tiff
[ -f report/data/watersheds_0p1deg.tiff ] || "$BIN"/gdal_rasterize -a pfaf_id -init 0 -ot Int32 \
    -te -180 -60 180 80 -ts 3600 1400 -co COMPRESS=LZW "$SHP" report/data/watersheds_0p1deg.tiff
echo "inputs ready"
