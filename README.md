# Haapsalu Drive — v0.1

A local-first browser driving prototype using the supplied Haapsalu 2025 orthophotos, 1 m DTM, official LOD2 building GDB, OpenStreetMap export and BMW E36 GLB.

## First run (PowerShell, Windows)

Install Node 22, Python 3.11/3.12 and Git LFS first.

    git pull
    git lfs pull
    python -m pip install -r requirements.txt
    npm install
    python tools/build_world.py
    npm run dev

Open http://127.0.0.1:5173. Use WASD / arrows, space handbrake, C camera, R reset, Escape pause. Production: npm run build.

The compiler logs actual GIS bounds and coverage, reads the original archives, clips the building geometry and creates public/world (ignored by Git). If the source files are still LFS pointers, it fails with a useful message. Do not substitute invented geography. Rerun the compiler after changing assets. For lower-memory machines: python tools/build_world.py --step 10 --texture-size 2048.

## v0.1 scope and known limitations

Real terrain, aerial texture, detailed roof meshes, collidable buildings, road overlay, deterministic approximate vegetation, chase/hood/overhead views, RWD raycast vehicle and HUD. Road surface geometry is approximate; widths are inferred if absent. Bridge/tunnel surfaces are omitted. Missing terrain cells use sea level (coverage logged); coastal water and land gaps are not reliably distinguished. This is not yet a polished city simulator. Wheels in the imported car may be baked into one mesh; the GLB is normalized and its orientation can be adjusted in src/main.js. Physics and visuals require in-browser tuning. No multiplayer, audio, advanced foliage models, street furniture, facade photographic textures or manual gear simulation yet.

## Attribution

Buildings, elevation and imagery: Republic of Estonia Land and Spatial Development Board / Maa- ja Ruumiamet, source extraction November 2026; imagery filename date 2025-06-01. https://geoportaal.maaruum.ee/
Roads and vegetation tags: © OpenStreetMap contributors, ODbL, https://www.openstreetmap.org/copyright
BMW model: supplied user asset; original author/licence must be verified and credited before public distribution. Other downloaded asset packs are not redistributed by the runtime compiler. Code is independently written; Rapier and Three.js are dependencies.

## Verification status

Initial implementation committed against the visible repository asset manifest. GitHub connector only exposed LFS pointers; real archives and model, npm installation, full map build and browser driving could not be exercised in this environment. No claim of completed playtesting.

## v0.2 visual and navigation pass

Rebuild world data before running: `python tools/build_world.py` (default aerial texture now 8192; use `--texture-size 4096` on low memory). M or click minimap opens a searchable, pan/zoom city map; choose a road and teleport. Current street uses OSM names, not guessed names. Aerial map toggle uses the same orthophoto. Teleport targets only terrain-validated road segments. Materials are extracted from supplied texture ZIPs where possible; procedural facade modules, windows and roof texture with aerial-derived approximate roof colours are not photographic facade reconstructions. Roads have dense draping, opaque textured surfaces and approximate shoulders. This is not a surveyed sidewalk or road marking dataset. The original vegetation is still placeholder. No new full runtime test was possible in the connector environment.

## v0.3 environment pass (requires rebuilding)

- Reads the new ETAK/EHR building GPKG, tries registry ID matching before unambiguous spatial matching, and uses readable exterior finish / floor count fields when present. Structural material is only a proxy. The report records matched and inferred counts. No claim of exact individual facade colors, doors or windows.
- Reads official measured vegetation GPKG; real positions and heights, crown diameter when readable, broadleaf/conifer when the field contains recognized text. Unknown types are reported and rendered as broadleaf. Uses original alpha-tested foliage impostors, not the uninspected third-party pine GLB. Groups have distance-based LOD and culling.
- Ground tiles are compiled from the supplied original orthophotos and streamed around the car (default 1280 px tiles). Main aerial texture is a 4096 overview. Use --no-hd-tiles only to debug. Default terrain step now 4 m.
- Sky, lighting, approximate road lamps, rounded road joins and mapped water surfaces. Coastline/sea coverage depends on input OSM polygons; no fictional sea plane. Lamps are approximate and not surveyed. 
- New report: public/world/build_report.json. Share this if source schemas do not match. Compilation fails explicitly when the supplied vegetation GPKG cannot be interpreted; adjust mappings in tools/world_sources.py rather than inventing heights.
- The 1917px Fotoladu photos have geographic footprints but not calibrated camera poses. They are retained as reference; do not project them directly as if camera calibration were known.

Run `python tools/build_world.py`, then `npm run dev`. Optional `--tile-pixels 2048` increases near-ground detail and GPU memory. Runtime and actual data schema have not been verified in the GitHub connector environment.
