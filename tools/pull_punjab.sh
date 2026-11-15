#!/bin/bash
# Resumable parallel FIRMS pull for the Punjab box. Safe to rerun: every
# window is cached on disk and finished windows are skipped. Each worker is
# stopped cleanly before the session's command timeout; rerun until
# tools/assemble.py reports full coverage.
set -u
KEY="${FIRMS_KEY:?set FIRMS_KEY}"
BBOX="73.8,29.5,77.5,32.5"
SECS="${1:-520}"

run(){ timeout "$SECS" python3 -m pipeline.fetch_firms --key "$KEY" \
        --source "$1" --bbox "$BBOX" --start "$2" --end "$3" \
        --out data/raw >> ".cache/log_$1_$2.txt" 2>&1; }

mkdir -p .cache
run MODIS_SP        2000-11-01 2013-06-30 &
run MODIS_SP        2013-07-01 2026-08-31 &
run VIIRS_SNPP_SP   2012-01-20 2019-06-30 &
run VIIRS_SNPP_SP   2019-07-01 2026-07-31 &
run MODIS_NRT       2026-09-01 2026-11-14 &
run VIIRS_SNPP_NRT  2026-08-01 2026-11-02 &
run VIIRS_NOAA20_NRT 2026-08-01 2026-11-14 &
wait
echo "round complete"
