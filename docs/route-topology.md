# Custom editor topology pilot

The editor and overlay keep PR4's native-pixel waypoints and the existing settings,
reference cache binding, manual mode and explicit no-path failures. `Walkable`
selects the new topology only when the released clean-mask binary SHA and shape
match exactly one bundled floor. Otherwise the existing footprint planner remains.
No main-controller or desktop UI entry point is changed.

## Bounded data contract

`app/route_topology_pilot_data.py` is the portable, generated
`custom-route-topology-v1` payload. It includes only one layout, two floors:

| Runtime ID | Source layout | Floor | Vertices | Admitted source door pairs |
|---|---|---|---:|---:|
| nightmare-09 | 104001 | 1F / world 2 | 329 | 65 |
| nightmare-09 | 104001 | 2F / world 3 | 237 | 39 |

Fields include `mapId`, `sourceLayout`, `floor`, `worldFloor`, `shape`,
`maskSha256`, `resourceSha256`, `sourceBaseSha256`, source versions,
`registration`, `points`, `pointRooms`, typed `links`, source room IDs,
static door IDs and state, and source entrance-marker coordinates.
No raw game files, texture bytes, author pixels or machine-specific paths are
included. The generated module is imported normally, so the existing PyInstaller
build can collect it without collecting the ignored/private references tree.

## Registration is a producer transform, not a bounding-box fit

The public structure metadata explicitly binds `nightmare-09` to source layout
104001, world floors 2/3, and `game-native-pixels:104001:<floor>`.
`sourceGeometry.production` is direct renderer alpha without projection or resize;
`matrixRendererPixelToProduct` is identity; tile pitch is 60 pixels. The existing
native24 producer defines native-pixel to source-renderer coordinates as
`[[128/60,0,minX],[0,128/60,minY],[0,0,1]]`.

For a world cell `[x,worldFloor,z]`, its centre projects to native pixels:

```
u = 60 * (x + 0.5) - minX * 60/128
v = 60 * (-z - 0.5) - minY * 60/128
```

The exporter requires the source layout, floor, producer contract, explicit
identity registration and matching source framing. It does not derive a transform
from image dimensions or occupied bounds. At load time the entire released binary
mask SHA and shape must match, and every admitted point/edge is checked against
that exact clean_mask. Door/room identities are retained even where footprints
touch. Changes to the resource mask disable this pilot instead of reusing a stale
registration.

Topology source: 2.104.3034860 (base SHA
`d1910f7b738c0a6bc0ba2d1f771fd6c1f3a29416fcfd061206aee82d3867444a`).
Public shared metadata source: 2.104.3059230, release
`resources-0B721D65-20261027`. These are separate versions. The static topology
snapshot does not establish current runtime door states or full package equivalence.
The source PNG alpha differs from the current binary mask at 273 pixels on 1F
and 450 on 2F (IoU 0.999775 / 0.999471); no image-equality claim is made. The
registered coordinates come from the explicit producer contract, and paths are
validated against the current mask, not the older PNG.

## Routing and limits

Within ordinary rooms the planner uses source cells and any existing explicit
cell-link restrictions, with cardinal grid edges. Different room IDs connect only
through admitted source-paired doors. The source-reviewed Y corridor uses its
explicit centreline links, preserving diagonal arms. Each line is checked against
the current footprint. Only forward collinear points are merged; no smoothing
or full-map Manhattan replacement occurs. Free native waypoints, including points
midway along diagonal arms, remain the start/end of each solved leg. No arrows or
cross-floor connector lines are added.

The footprint and occupancy are not a complete interior collision model. There
is no complete native-wall channel in this public resource. Static paired doors
use a planning-open policy; their live states remain unknown. Every unknown
transfer is excluded; this pilot does not introduce cross-floor routing or
infer a walkable upper floor from occupied height. The separate, earlier user
confirmation that room 83 of 103001 is double-height is not generalized to 104001.

## Display names

Names are UI mappings; runtime map IDs, package IDs, alignment references and
saved route ownership never change. The sample is
`nightmare-09 → 一楼正门 · 二楼侧门 01`, based on the two recorded entrance
markers and a pilot group ordinal. Other nightmare maps use neutral `地图 NN`
until their classifications are reviewed. The offline contact sheet supplies
the corresponding real footprint thumbnail. This is a pilot naming proposal,
not a complete renamed map library.

## Reproduce and validate

Run with authorized local inputs; paths are arguments, not committed defaults:

```
python tools/maps/export_route_topology.py --base LAYOUT.json --structures STRUCTURE_ROOT --output LOCAL.json --python-module app/route_topology_pilot_data.py
python -m unittest discover -s tools/maps -p test_route_topology.py -v
python tools/maps/render_topology_pilot.py --structures STRUCTURE_ROOT --output LOCAL_EVIDENCE
```

The offline renderer calls the real `Walkable`, `route_polyline` and `render_layer`
used by PR4, using the same native waypoints before/after. The 1F example follows
two diagonal Y arms; the 2F example traverses multiple ordinary source rooms/doors
with 11 orthogonal segments. Seven targeted tests cover missing doors, unknown
transfers, void crossing, exact resource binding, collinear reduction, free diagonal
waypoints, manual mode, no-path semantics and empty saved preferences.

GUI/game-input validation is NOT_RUN (CUA paused). No current gameplay collision,
timing, live door state, exhaustive map conversion or player acceptance is claimed.
Base checkout: `80cc4fb85ab540edba7bc3e2842cb1ec6b5e230d`.
