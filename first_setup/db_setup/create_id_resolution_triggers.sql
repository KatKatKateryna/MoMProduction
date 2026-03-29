-- =============================================================================
-- create_id_resolution_triggers.sql
--
-- BEFORE INSERT row-level triggers for summary_glofas_latest and
-- summary_final_alert_latest.
--
-- The caller inserts all columns EXCEPT matching_id_station /
-- matching_id_watershed (omit them or pass NULL). The trigger:
--   1. Looks up the reference table using the same key as the original
--      update scripts (update_glofas.py / update_final_alert.py).
--   2. If found  → sets the matching ID on the row.
--   3. If not found → inserts a new entry into the reference table using
--      a sequence-generated ID, then sets it on the row.
--
-- Sequences are initialised from the current max ID in each reference table
-- so they never collide with existing data.
--
-- Run AFTER create_all_tables.sql.
-- Run with:
--   sudo -u postgres psql -d postgres -f create_id_resolution_triggers.sql
-- =============================================================================


-- =============================================================================
-- Sequences for ID generation
-- (matching_id_station and matching_id_watershed have no SERIAL defined)
-- =============================================================================

CREATE SEQUENCE IF NOT EXISTS seq_glofas_station_id START 1;
SELECT setval('seq_glofas_station_id',
    GREATEST(1, COALESCE((SELECT MAX(matching_id_station) FROM all_glofas_stations), 0)));

CREATE SEQUENCE IF NOT EXISTS seq_watershed_id START 1;
SELECT setval('seq_watershed_id',
    GREATEST(1, COALESCE((SELECT MAX(matching_id_watershed) FROM all_watersheds), 0)));


-- =============================================================================
-- GloFAS station ID resolution
--
-- Lookup key: (Station, Country, Lat, Lon, pfaf_id)
-- Matches the uq_station unique constraint and CSV_STATIC_COLS in
-- update_glofas.py / update_db_glofas.py.
--
-- All station metadata columns (Station, Basin, Country, ...) are taken
-- from the incoming row and written to all_glofas_stations if the station
-- is new. Existing stations are not updated — their metadata is stable.
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_resolve_glofas_station()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_id INTEGER;
BEGIN
    -- Look up existing station by unique key
    SELECT matching_id_station INTO v_id
    FROM all_glofas_stations
    WHERE "Station"  = NEW."Station"
      AND "Country"  = NEW."Country"
      AND "Lat"      = NEW."Lat"
      AND "Lon"      = NEW."Lon"
      AND pfaf_id    = NEW.pfaf_id;

    IF v_id IS NULL THEN
        -- New station — generate ID and insert into reference table
        v_id := nextval('seq_glofas_station_id');
        INSERT INTO all_glofas_stations (
            matching_id_station,
            "Station", "Basin", "Country", "Country_code",
            "Continent", "Location",
            "Lat", "Lon", "Upstream area",
            pfaf_id
        ) VALUES (
            v_id,
            NEW."Station", NEW."Basin", NEW."Country", NEW."Country_code",
            NEW."Continent", NEW."Location",
            NEW."Lat", NEW."Lon", NEW."Upstream area",
            NEW.pfaf_id
        );
    END IF;

    -- summary_glofas_latest uses pfaf_id as PK — no matching_id_station column.
    -- The trigger's sole job is to ensure all_glofas_stations is populated.
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_resolve_glofas_station ON summary_glofas_latest;
CREATE TRIGGER trg_resolve_glofas_station
BEFORE INSERT ON summary_glofas_latest
FOR EACH ROW EXECUTE FUNCTION fn_resolve_glofas_station();


-- =============================================================================
-- Watershed ID resolution
--
-- Lookup key: (pfaf_id, name, name_1, CentroidX, CentroidY)
-- Matches the uq_watershed unique constraint and LOOKUP_KEY in
-- update_final_alert.py / update_db_final_alert.py.
--
-- All watershed metadata columns are taken from the incoming row and written
-- to all_watersheds if the watershed is new.
-- =============================================================================

CREATE OR REPLACE FUNCTION fn_resolve_watershed()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_id INTEGER;
BEGIN
    -- Look up existing watershed by unique key
    SELECT matching_id_watershed INTO v_id
    FROM all_watersheds
    WHERE pfaf_id    = NEW.pfaf_id
      AND "name"     = NEW."name"
      AND "name_1"   = NEW."name_1"
      AND "CentroidX" = NEW."CentroidX"
      AND "CentroidY" = NEW."CentroidY";

    IF v_id IS NULL THEN
        -- New watershed — generate ID and insert into reference table
        v_id := nextval('seq_watershed_id');
        INSERT INTO all_watersheds (
            matching_id_watershed,
            pfaf_id, "name", "name_1",
            "CentroidX", "CentroidY",
            "Admin1_count", "Admin1_names"
        ) VALUES (
            v_id,
            NEW.pfaf_id, NEW."name", NEW."name_1",
            NEW."CentroidX", NEW."CentroidY",
            NEW."Admin1_count", NEW."Admin1_names"
        );
    END IF;

    -- summary_final_alert_latest uses pfaf_id as PK — no matching_id_watershed column.
    -- The trigger's sole job is to ensure all_watersheds is populated.
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_resolve_watershed ON summary_final_alert_latest;
CREATE TRIGGER trg_resolve_watershed
BEFORE INSERT ON summary_final_alert_latest
FOR EACH ROW EXECUTE FUNCTION fn_resolve_watershed();
