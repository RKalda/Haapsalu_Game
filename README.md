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

Additional v0.3 refinements: normal maps extracted from supplied packs when possible, with conservative generated fallback; the supplied pine GLB is copied and used for a capped number of near conifers only if an identifiable single low-cost LOD passes validation. Otherwise alpha-tested foliage remains the fallback. See console for selection. The exact tree mesh hierarchy and GPKG schemas remain unverified until local runtime/build.

## v0.4 — observed facades, not procedural accuracy claims

Download the separate landscape Lisa 1 from https://www.muinsuskaitseamet.ee/sites/default/files/documents/2024-05/Haapsalu%20muinsuskaitseala%20inventeerimise%20l%C3%B5pparuanne.%20Lisa%201.pdf outside the public repo. Run `python -m pip install pymupdf` and `python tools/extract_heritage.py path/to/pdf`. The resulting local reference photos have ETAK IDs. Inspect catalogue matches. Explicit heritage descriptions inform material families; photos are not automatically baked or redistributed.

To bake rights-cleared photographs, create data/raw/facades/verified.json as an array of objects with etak_id, bearing (0=N,90=E,180=S,270=W, outward), image (path relative to facades), source, license, and rectified:true or quad:[[topLeftX,topLeftY],[topRightX,topRightY],[bottomRightX,bottomRightY],[bottomLeftX,bottomLeftY]]. Photo must cover the selected wall, not a full street view. Compiler chooses the largest compatible wall within 25 degrees and packs photos into 2048 atlases. public/world/facade_wall_catalog.json provides ETAK IDs, local center and wall face index/bearing/width/height. Inspect selection; complex walls may need manual adjustment. Unverified walls use more restrained generic materials/windows. Report counts photographicFaces and heritageReferences separately.

Rebuild with `python tools/build_world.py --reuse-hd-tiles` (uses previous aerial tiles if same bounds/grid). No photographic texture is produced until verified inputs are provided. The existing 2022 Haapsalu center 3Di SLPK is an alternative data acquisition route; availability/license unconfirmed. Full local build and runtime validation pending.

## v0.5 — actual map completeness and road surfaces

Run `python tools/fetch_world_sources.py` once to download bounded public ETAK WFS layers plus additional OSM historic/barrier/cemetery/path features, then `python tools/build_world.py --reuse-hd-tiles`. Existing export remains untouched. Official field codes for barriers/cemeteries/ruins/parking follow Maa- ja Ruumiamet's ETAK layer/domain specification. `build_report.json` lists the counts; a zero count means missing source data, not successful reconstruction. Castle walls are reconstructed from mapped geometry with **estimated** heights and thickness unless provided; no exact mesh claim. Individual cemetery headstones are only rendered for mapped points. Posti and Karja use the supplied grey cobblestone PavingStones033 texture, not sidewalk slabs; generic center stripes removed from stone roads. Roofs now use real orthophoto UVs at overview resolution. Actual facade photos are still dependent on rights-cleared imagery and validated placement. Runtime untested in connector environment.

### Actual aerial facade pilot

`pip install -r requirements.txt` adds OpenCV. The compiler now attempts to align downloaded Fotoladu preview photos to the real orthophoto using SIFT/RANSAC, rejects roof control points using ETAK footprints, estimates camera pose and focal length against terrain and reported flight altitude, and bakes only outward-facing wall surfaces meeting minimum pixel and reprojection confidence. Real image pixels are packed into small texture atlases. See aerialPoseDiagnostics, aerialCamerasAccepted, aerialPhotographicFaces and photographicFaces in build_report.json. This is an experimental low-resolution reconstruction, **not** surveyed facade accuracy; even accepted poses may have occlusion and alignment errors. The current photo collection covers only parts of the city, and some downloaded IDs lack matching footprints. No image is accepted if pose estimation fails. No scraping high-res tiles. `--skip-aerial-facades` disables the experiment. Recommended build: `python tools/build_world.py --texture-size 8192 --reuse-hd-tiles` (uses existing terrain tile cache when compatible). Existing manual verified facade pipeline takes priority. Source: Maa- ja Ruumiamet Fotoladu (attribution retained). Verify public-use terms before distributing the baked photos.

### Build-report fixes

The first full build confirmed that the enriched ETAK file does not contain exterior finishes, wall construction or roof covering fields. No successful photographic facade alignment was obtained. Field mappings now recognize ehr_gid, kaos_nimetus_peamine and max_korruste_arv. Vegetation uses the yksikpuud_keskpunkt point layer and puukroon measured crown diameter, skipping the duplicate crown polygon layer and deduplicating by puu_id. Aerial alignment now reports missing photo footprints and match/inlier counts, with contrast normalization and a wider search margin. This remains an experiment and may reject all photos; it is not proof of successful texturing.
