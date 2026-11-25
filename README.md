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
