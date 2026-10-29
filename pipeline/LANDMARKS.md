# Landmark models: format for the modelling agent

Landmarks (churches, castles, summit sites, special buildings) are modelled by
hand in three.js pages like `landmarks/src/church.html` (examples in `landmarks/examples/`), then exported and placed on
the map automatically. Reconstruct them from photos, Street View and satellite images.
Don't extract or trace Google's 3D tiles: their terms forbid reusing that geometry.

## What a landmark page must do

1. Build the building into one `THREE.Group` and expose it as `window.LANDMARK = group;`.
2. Units are metres; Y is up (three.js default).
3. The origin is on the ground under the middle of the main facade. The facade faces local
   +Z and the building extends towards −Z.
4. Give every material a `name` (`material.name = "stone"`), since textures and overrides
   are keyed by name.
5. Keep site dressing out of the group (ground disc, piazza, roads, lawns), or give it
   materials named `grass` / `cobble`; the real terrain and roads replace it. Flat sheets
   at ground level are dropped automatically. Steps and plinths are kept.
6. Plain `MeshStandardMaterial` colours and canvas textures export fine. Shader tricks
   (`onBeforeCompile`) don't export: declare them as an override in `landmarks.json`
   instead, as with the example church's banded stone (`"stripes"`).
7. Keep it under about 100k triangles per landmark (the example church is about 33k).

## Registering a landmark (`landmarks/landmarks.json`)

```json
"church": {
  "name": "Village church",
  "source": "church.html",
  "osm_way": 123456789,            // OSM building outline it stands on
  "facade_azimuth": 135,           // compass bearing the facade faces (0 = N, 90 = E)
  "exclude_materials": ["grass", "cobble", "default"],
  "materials": {"wall": {"stripes": ["#cf8430", "#e8dcbf"], "period": 1.0, "offset": 0.9}}
}
```

## Pipeline

1. `scripts/landmark_server.py` serves `landmarks/src/` on http://127.0.0.1:8765. Open the
   page, then run the GLTFExporter snippet; it saves `landmarks/glb/<id>.glb`.
2. `scripts/landmarks.py` does the rest:
   - converts the model to a Z-up Collada shape with textures,
   - centres it on the OSM outline and turns the facade to `facade_azimuth` (snapped to
     the outline's axes when within 25°),
   - sets it on the lowest ground of its footprint.
3. `scripts/export_beamng.py` removes the generated or Italy building on that footprint
   and adds the landmark as a TSStatic in the `landmarks` group.

Candidate landmarks, with OSM way ids from `data/osm/raw.json`:

| Landmark | OSM way | Notes |
|----------|---------|-------|
| Village church | 123456789 | done |
| Castle keep | 123456790 | keep only; the walls are separate |
| Summit site | n/a | masts, camouflage wall, restaurant |
