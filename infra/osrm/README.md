# OSRM road routing (S11)

The hub measures road distance with [OSRM](https://project-osrm.org/) when `OSRM_URL` is set,
and falls back to straight-line distance x 1.3 (haversine, business-rules.md §4) when it is
not set, when OSRM answers with an error, or when it does not answer within 2 s. After a
failure the hub skips OSRM for 30 s, so a stopped server costs one timeout, not one per
distance. Matching (candidate ETAs and transport costs) and shipment assignment (route,
geometry and ETA) both use it. ETA is always distance ÷ 40 km/h + 1 h; OSRM's own duration
is not used.

## Prepare the map data (once)

```
infra/osrm/prepare.sh
```

This downloads Geofabrik's India "southern zone" extract (it contains Bengaluru, the demo
city) into `infra/osrm/map.osm.pbf` and runs `osrm-extract` (car profile), `osrm-partition`
and `osrm-customize` in the `osrm/osrm-backend` image. The output files `map.osrm*` stay in
this folder, which git ignores except for this README and the script.

Options (environment variables):

| Variable       | Default                 | Use                                       |
| -------------- | ----------------------- | ----------------------------------------- |
| `OSRM_PBF_URL` | Geofabrik southern zone | Another `.osm.pbf` download               |
| `OSRM_PBF`     |                         | A local `.osm.pbf` instead of downloading |
| `OSRM_IMAGE`   | `osrm/osrm-backend`     | Another OSRM image or tag                 |

The zone extract needs several GB of RAM to process. For a faster demo, clip it to the city
first with [osmium](https://osmcode.org/osmium-tool/) and pass the result:

```
osmium extract -b 77.35,12.75,77.85,13.20 southern-zone-latest.osm.pbf -o bengaluru.osm.pbf
OSRM_PBF=bengaluru.osm.pbf infra/osrm/prepare.sh
```

## Run it

```
docker compose -f infra/docker-compose.yml --profile routing up -d osrm
curl 'http://127.0.0.1:5000/route/v1/driving/77.6271,12.9279;77.6974,12.9592?overview=false'
```

Then set `OSRM_URL=http://127.0.0.1:5000` in `services/hub-api/.env` and restart `make hub`
and `make worker`. The port is `OSRM_PORT` in the root `.env` (default 5000).

## Check that the hub uses it

Assign a shipment (`POST /api/v1/shipments/{id}/assign`): the response's `route_provider` is
`OSRM` when the route came from OSRM and `HAVERSINE` when the hub fell back. The hub logs
`OSRM unavailable; using haversine` with the error on each fallback.

The hub tests never call a real OSRM: `app/routing/tests/test_osrm.py` and
`app/shipments/tests/test_routing.py` use a fake OSRM (httpx `MockTransport`), and every
other test pins routing to haversine.
