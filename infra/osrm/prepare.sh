#!/usr/bin/env bash
# Prepare OSRM road data for the demo city (Bengaluru) so the hub can route shipments.
#
# Downloads an OpenStreetMap extract (default: Geofabrik's India "southern zone", which
# contains Karnataka) and runs osrm-extract (car profile), osrm-partition and
# osrm-customize in the osrm/osrm-backend image, producing infra/osrm/map.osrm*. The compose
# service `osrm` (profile `routing`) then serves /data/map.osrm with the MLD algorithm.
#
#   infra/osrm/prepare.sh                      # download (if missing) and prepare
#   OSRM_PBF_URL=https://.../other.osm.pbf infra/osrm/prepare.sh
#   OSRM_PBF=/path/to/local.osm.pbf infra/osrm/prepare.sh
#
# Needs Docker and curl. The extract is a few hundred MB and extracting it takes several
# minutes and several GB of RAM; see README.md for a smaller, clipped extract.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${OSRM_IMAGE:-osrm/osrm-backend}"
URL="${OSRM_PBF_URL:-https://download.geofabrik.de/asia/india/southern-zone-latest.osm.pbf}"
PBF="$DIR/map.osm.pbf"

command -v docker >/dev/null || { echo "prepare.sh: docker is required" >&2; exit 1; }

if [[ -n "${OSRM_PBF:-}" ]]; then
  echo "Using local extract $OSRM_PBF"
  cp -- "$OSRM_PBF" "$PBF"
elif [[ ! -s "$PBF" ]]; then
  command -v curl >/dev/null || { echo "prepare.sh: curl is required" >&2; exit 1; }
  echo "Downloading $URL"
  curl -fL --retry 3 -o "$PBF.part" -- "$URL"
  mv -- "$PBF.part" "$PBF"
else
  echo "Reusing $PBF (delete it to download again)"
fi

run() { docker run --rm -t -v "$DIR:/data" "$IMAGE" "$@"; }

echo "osrm-extract (car profile)"
run osrm-extract -p /opt/car.lua /data/map.osm.pbf
echo "osrm-partition"
run osrm-partition /data/map.osrm
echo "osrm-customize"
run osrm-customize /data/map.osrm

cat <<EOF
Done: $DIR/map.osrm is ready.
Start it:  docker compose -f infra/docker-compose.yml --profile routing up -d osrm
Check it:  curl 'http://127.0.0.1:\${OSRM_PORT:-5000}/route/v1/driving/77.6271,12.9279;77.6974,12.9592?overview=false'
Use it:    set OSRM_URL=http://127.0.0.1:5000 in services/hub-api/.env and restart the hub.
EOF
