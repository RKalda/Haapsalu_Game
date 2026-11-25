"""Local, reproducible EPSG:3301 map compiler. Input archives remain untouched."""
from pathlib import Path
import argparse, json, math, shutil, tempfile, zipfile
from collections import defaultdict
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.transform import from_origin
from PIL import Image, ImageDraw, ImageOps
from pyproj import Transformer
import pyogrio
from shapely import from_wkb
from shapely.geometry import shape, box, LineString, Point
from shapely.ops import transform as geom_transform
from shapely.strtree import STRtree
import mapbox_earcut
from world_sources import BuildingInfo, measured_trees, aerial_tiles, foliage_sprites
from photo_facades import PhotoFacades, frame as wall_frame
from topography import compile_details
from aerial_facades import AerialFacades
from street_objects import compile_street_objects, granite_setts, street_key

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public/world"
CRS = "EPSG:3301"
PROJ = Transformer.from_crs(4326, 3301, always_xy=True)
BBOX = (23.490, 58.920, 23.590, 58.960)
CENTER = PROJ.transform(23.540, 58.940)
WALL = np.array([[.77,.72,.63],[.65,.68,.63],[.78,.77,.72],[.70,.58,.46],[.56,.64,.65],[.8,.74,.53]])
ROOF = np.array([[.30,.32,.33],[.47,.23,.18],[.35,.35,.31],[.40,.42,.45],[.39,.29,.24]])
ROAD_WIDTH = {"motorway":10,"trunk":9,"primary":8,"secondary":7.5,"tertiary":7,"residential":5.5,"unclassified":5.5,"living_street":4.5,"service":3.5,"track":3}
def is_pointer(path):
    with path.open("rb") as f: return f.read(42).startswith(b"version https://git-lfs.github.com/spec/v1")
def extract(path, dest):
    if is_pointer(path): raise RuntimeError(f"Git LFS pointer, not actual asset: {path}. Run git lfs pull.")
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if not (dest / info.filename).resolve().is_relative_to(dest.resolve()):
                raise RuntimeError("Unsafe ZIP path")
        z.extractall(dest)
def rasters(paths, bbox, width, height, bands, dtype, resampling):
    west,south,east,north=bbox
    t=from_origin(west,north,(east-west)/width,(north-south)/height)
    out=np.full((bands,height,width), np.nan if dtype=="float32" else 0,dtype=dtype)
    covered=np.zeros((height,width),bool)
    for path in paths:
        if is_pointer(path): raise RuntimeError(f"Missing LFS data: {path}")
        with rasterio.open(path) as s:
            if not s.crs: raise RuntimeError(f"Missing CRS: {path}")
            bb=transform_bounds(s.crs,CRS,*s.bounds)
            print(path.name, s.crs, [round(x) for x in bb])
            if bb[2]<=west or bb[0]>=east or bb[3]<=south or bb[1]>=north: continue
            mask=np.zeros((height,width),np.uint8)
            reproject(s.dataset_mask(),mask,src_transform=s.transform,src_crs=s.crs,dst_transform=t,dst_crs=CRS,src_nodata=0,dst_nodata=0,resampling=Resampling.nearest)
            good=mask>0
            if bands>s.count: raise RuntimeError(f"Not enough bands: {path}")
            for b in range(bands):
                layer=np.zeros((height,width),dtype=dtype)
                reproject(rasterio.band(s,b+1),layer,src_transform=s.transform,src_crs=s.crs,src_nodata=s.nodata,dst_transform=t,dst_crs=CRS,resampling=resampling,dst_nodata=np.nan if dtype=="float32" else 0)
                out[b,good]=layer[good]
            covered|=good
    return out,covered
def polygons(g):
    if g is None or g.is_empty: return
    if g.geom_type=="Polygon": yield g
    elif hasattr(g,"geoms"):
        for sub in g.geoms: yield from polygons(sub)
def triangulate(face):
    rings=[np.asarray(face.exterior.coords)[:-1]]+[np.asarray(r.coords)[:-1] for r in face.interiors]
    if len(rings[0])<3 or any(r.ndim!=2 or r.shape[1]<3 for r in rings): return None
    a=rings[0][:,:3]; normal=np.cross(a,np.roll(a,-1,axis=0)).sum(axis=0)
    length=np.linalg.norm(normal)
    if length<1e-8: return None
    keep=[i for i in range(3) if i!=int(np.argmax(abs(normal)))]
    coords=np.vstack(rings)[:,:3]
    ind=mapbox_earcut.triangulate_float64(np.ascontiguousarray(coords[:,keep]),np.cumsum([len(r) for r in rings],dtype=np.uint32))
    return coords[ind],abs(normal[2])/length
def main(report):
    p=argparse.ArgumentParser();p.add_argument("--step",type=float,default=4);p.add_argument("--texture-size",type=int,default=4096);p.add_argument("--tile-pixels",type=int,default=1280);p.add_argument("--no-hd-tiles",action="store_true");p.add_argument("--reuse-hd-tiles",action="store_true");p.add_argument("--skip-aerial-facades",action="store_true")
    args=p.parse_args()
    if args.step<2 or args.texture_size<256: raise ValueError("Invalid resolution")
    OUT.mkdir(parents=True,exist_ok=True)
    make_materials()
    granite_setts(OUT)
    foliage_sprites(OUT)
    previous=json.loads((OUT/"manifest.json").read_text(encoding="utf-8")) if args.reuse_hd_tiles and (OUT/"manifest.json").exists() else {}
    (OUT/"manifest.json").unlink(missing_ok=True)
    photos=PhotoFacades(ROOT,OUT)
    # Fixed local origin, north is -Z. Grid is aligned to the requested geographic bounding box.
    w,s,e,n=transform_bounds("EPSG:4326",CRS,*BBOX,densify_pts=21)
    metadata=BuildingInfo(ROOT,(w,s,e,n),report)
    nx,nz=math.ceil((e-w)/args.step)+1,math.ceil((n-s)/args.step)+1
    dx,dz=(e-w)/(nx-1),(n-s)/(nz-1)
    grid_bounds=(w-dx/2,s-dz/2,e+dx/2,n+dz/2)
    dem=sorted((ROOT/"data/raw/terrain").rglob("*.tif"))
    if not dem: raise RuntimeError("No terrain TIFFs found")
    h,valid=rasters(dem,grid_bounds,nx,nz,1,"float32",Resampling.bilinear);h=h[0]; valid &= np.isfinite(h)
    print("Valid terrain coverage:",round(100*valid.mean(),1),"%")
    if not valid.any(): raise RuntimeError("No terrain overlaps Haapsalu. Download the correct map sheets.")
    # Missing cells commonly include sea; use sea level, explicitly record coverage.
    h[~valid]=0
    h.astype("<f4").tofile(OUT/"height.dat")
    cx,cy=CENTER
    bounds=[w-cx,cy-n,e-cx,cy-s]
    def local(easting,northing): return [float(easting-cx),float(cy-northing)]
    def height(x,z):
        u=np.clip((x-bounds[0])/dx,0,nx-1);v=np.clip((z-bounds[1])/dz,0,nz-1)
        i=min(int(u),nx-2);j=min(int(v),nz-2);a=u-i;b=v-j
        # Same diagonal as the runtime terrain triangles.
        q,r,t,k=map(float,(h[j,i],h[j,i+1],h[j+1,i],h[j+1,i+1]))
        return q+(r-q)*a+(t-q)*b if a+b<=1 else k+(t-k)*(1-a)+(r-k)*(1-b)
    def valid_at(x,z):
        i=int(round((x-bounds[0])/dx));j=int(round((z-bounds[1])/dz))
        return 0<=i<nx and 0<=j<nz and bool(valid[j,i])
    with tempfile.TemporaryDirectory(prefix="haapsalu-") as td:
        temp=Path(td)
        ortho=[]
        for folder in ["ortophotos","orthophotos"]:
            source=ROOT/"data/raw"/folder
            for i,archive in enumerate(sorted(source.glob("*.zip"))):
                dest=temp/(folder+str(i));dest.mkdir();extract(archive,dest)
                ortho+=list(dest.rglob("*.tif"))+list(dest.rglob("*.tiff"))
            ortho+=list(source.rglob("*.tif"))+list(source.rglob("*.tiff"))
        if not ortho: raise RuntimeError("No orthophotos found")
        tw=args.texture_size;th=max(256,round(tw*(n-s)/(e-w)))
        rgb,mask=rasters(ortho,(w,s,e,n),tw,th,3,"uint8",Resampling.bilinear)
        if not mask.any(): raise RuntimeError("No orthophoto coverage in the requested map")
        rgb[:,~mask]=np.array([48,83,91],dtype=np.uint8)[:,None]
        Image.fromarray(np.moveaxis(rgb,0,-1)).save(OUT/"ground.jpg",quality=88)
        if args.reuse_hd_tiles and previous.get("nx")==nx and previous.get("nz")==nz and previous.get("bounds")==bounds and previous.get("aerialTiles") and all((OUT/t["file"]).is_file() for t in previous["aerialTiles"]) and not args.no_hd_tiles:
            tiles=previous["aerialTiles"];print("Reusing",len(tiles),"existing aerial tiles")
        else:tiles=[] if args.no_hd_tiles else aerial_tiles(ortho,OUT,(w,s,e,n),nx,nz,pixels=max(256,min(2048,args.tile_pixels)))
        report["aerialTiles"]=len(tiles)
        def photo_project(lon,lat):
            east,north=PROJ.transform(lon,lat);return np.asarray(east)-cx,cy-np.asarray(north)
        aerial=None if args.skip_aerial_facades else AerialFacades(ROOT,photos,rgb,bounds,height,metadata,CENTER,photo_project,report)
        # Read real 3D faces directly from official file geodatabase.
        gdbs=list(ROOT.glob("*gdb.zip"))
        if not gdbs: raise RuntimeError("Building GDB ZIP is missing")
        gd=temp/"gdb";gd.mkdir();extract(gdbs[0],gd)
        db=next(gd.rglob("*.gdb"),None)
        if db is None: raise RuntimeError("Could not locate .gdb in archive")
        chunks=defaultdict(list); footprints=[]; rng=np.random.default_rng(4729); count=0; landmark_walls=[]
        for layer,typ in pyogrio.list_layers(db):
            if not typ: continue
            info=pyogrio.read_info(db,layer=layer)
            if not info.get("crs"): continue
            # pyogrio expects bbox in the source CRS. Do not silently reproject unknown 3D geometry.
            if rasterio.crs.CRS.from_user_input(info["crs"]).to_epsg()!=3301:
                from pyproj import CRS as Pcrs
                c=Pcrs.from_user_input(info["crs"])
                if not c.sub_crs_list or c.sub_crs_list[0].to_epsg()!=3301:
                    print("Skipping non-L-EST97 layer",layer,info["crs"]);continue
            print("Reading buildings:",layer,typ)
            meta,fids,geom,fields=pyogrio.raw.read(db,layer=layer,bbox=(w,s,e,n),return_fids=True)
            report.setdefault("lod2Layers",[]).append({"layer":str(layer),"fields":list(meta["fields"])})
            for gi,g in enumerate(from_wkb(geom)):
                if g is None or g.is_empty: continue
                bw,bs,be,bn=g.bounds
                if not (w<(bw+be)/2<e and s<(bs+bn)/2<n): continue
                faces=[]
                for face in polygons(g):
                    result=triangulate(face)
                    if result is not None and len(result[0]): faces.append(result)
                if not faces: continue
                allpts=np.vstack([f[0] for f in faces])
                low=float(allpts[:,2].min())
                x,z=local((bw+be)/2,(bs+bn)/2)
                wall=WALL[rng.integers(len(WALL))];roof=ROOF[rng.integers(len(ROOF))]
                bh=float(allpts[:,2].max()-low)
                # Approximate facade families. No source facade photographs are present.
                family=1 if bh<9 and rng.random()<.65 else 2 if rng.random()<.28 else 0
                row={name:col[gi] for name,col in zip(meta["fields"],fields)}
                family,wall,floor_h,plain,bid=metadata.style(row,g,bh,rng,wall,family)
                family,wall=photos.style(bid,family,wall)
                # Sample actual roof colour from the aerial image, not a random palette.
                samples=[]
                for pts,slope in faces:
                    if slope<.35 or pts[:,2].mean()<low+.5: continue
                    for tri in pts.reshape(-1,3,3):
                        ee,nn,_=tri.mean(axis=0)
                        ix=int((ee-w)/(e-w)*tw);iy=int((n-nn)/(n-s)*th)
                        if 0<=ix<tw and 0<=iy<th and mask[iy,ix]:
                            samples.append(rgb[:,iy,ix]/255)
                if samples:roof=np.clip(np.median(samples,axis=0),.12,.82)
                key=(math.floor(x/500),math.floor(z/500))
                frames={}
                for fi,(pts,slope) in enumerate(faces):
                    if slope>.25:continue
                    xyz=np.column_stack((pts[:,0]-cx,pts[:,2],cy-pts[:,1]))
                    f=wall_frame(xyz,np.array([x,z]))
                    if f is not None:frames[fi]=f
                selected=photos.select(bid,frames,x,z)
                addr=str(row.get('ads_lahiaadress','')).lower().strip()
                if addr in ('tallinna mnt 1','tallinna maantee 1') and frames:
                    # Verified address; exact letter placement is not surveyed.
                    for fi,f in frames.items():
                        bearing,width,fh,u,yy=f
                        if width<10 or fh<3:continue
                        pts=faces[fi][0];xyz=np.column_stack((pts[:,0]-cx,pts[:,2],cy-pts[:,1]))
                        landmark_walls.append(dict(name='KAUBAMAJA',center=xyz[:,[0,2]].mean(axis=0).tolist(),bearing=bearing,width=width,bottom=float(yy.min()),height=fh,etak_id=bid))
                for fi,(pts,slope) in enumerate(faces):
                    if slope>.5 and pts[:,2].max()<low+.15: continue
                    xyz=np.column_stack((pts[:,0]-cx,pts[:,2],cy-pts[:,1]))
                    isroof=slope>.35
                    col=np.tile(roof if isroof else wall,(len(pts),1))
                    # Per-face planar metric UVs; windows use floor-relative height.
                    if isroof:
                        uv=np.column_stack(((xyz[:,0]-bounds[0])/(bounds[2]-bounds[0]),1-(xyz[:,2]-bounds[1])/(bounds[3]-bounds[1])))
                    else:
                        horizontal=xyz[:,[0,2]]
                        a=horizontal[np.argmax(np.linalg.norm(horizontal-horizontal[0],axis=1))]-horizontal[0]
                        a=a/max(np.linalg.norm(a),1e-6)
                        uv=np.column_stack(((horizontal-horizontal[0])@a/2.7,(xyz[:,1]-low)/floor_h))
                    f=frames.get(fi)
                    # Use actual orthophoto roof pixels, rather than a repeated generic roof.
                    kind=3 if isroof else [4,5,6][family]
                    if isroof:col=np.ones_like(col)
                    if not isroof and f is not None:
                        _,width,fh,u,yy=f
                        if not plain and width>=3.4 and fh>=2.8 and abs(fh/floor_h-round(fh/floor_h))<.33 and yy.min()<low+.8:
                            kind=family
                            bays=max(1,round(width/3.2))
                            uv=np.column_stack(((u-u.min())/width*bays,(yy-yy.min())/floor_h))
                        else:uv=np.column_stack((u/2.7,yy/2.8))
                    if fi in selected:
                        kind,uv=photos.uv(f,selected[fi]);col=np.ones_like(col)
                    elif aerial is not None and not isroof and f is not None:
                        result=aerial.apply(xyz,f)
                        if result is not None:
                            kind,uv=result;col=np.ones_like(col)
                    chunks[key].append(np.column_stack((xyz,col,uv,np.full(len(pts),kind))).astype("<f4"))
                footprints.append(box(bw-cx,cy-bn,be-cx,cy-bs));count+=1
        if not count: raise RuntimeError("No usable 3D buildings in this area; inspect GDB layers and CRS.")
        buildings=[]
        for (a,b),data in sorted(chunks.items()):
            name=f"b_{a}_{b}.dat";v=np.concatenate(data);v.tofile(OUT/name)
            buildings.append({"file":name,"center":[a*500+250,b*500+250],"vertices":len(v)})
    if aerial is not None:aerial.finish()
    photo_atlases=photos.save(report)
    report["buildingStats"]=dict(metadata.stats)
    raw=ROOT/"export.geojson"
    if is_pointer(raw): raise RuntimeError("OSM file is only an LFS pointer")
    osm=json.loads(raw.read_text(encoding="utf-8-sig"))
    clip=box(*bounds);roads=[];trees=[];forest=[];spawn_options=[];water=[];crossings=[]
    def tolocal(x,y,z=None):
        a,b=PROJ.transform(x,y);return np.asarray(a)-cx,cy-np.asarray(b)
    def parts(g,kind):
        if g.is_empty:return
        if g.geom_type==kind:yield g
        elif hasattr(g,"geoms"):
            for item in g.geoms:yield from parts(item,kind)
    for f in osm.get("features",[]):
        prop=f.get("properties") or {};prop={**prop,**(prop.get("tags") or {})}
        if not f.get("geometry"):continue
        try:g=geom_transform(tolocal,shape(f["geometry"])).intersection(clip)
        except Exception:continue
        highway=prop.get("highway")
        if highway in ROAD_WIDTH and prop.get("bridge") not in ("yes","viaduct") and prop.get("tunnel")!="yes":
            try:width=float(str(prop.get("width",ROAD_WIDTH[highway])).split()[0])
            except ValueError:width=ROAD_WIDTH[highway]
            width=float(np.clip(width,2.5,16))
            for line in parts(g,"LineString"):
                pts=[[round(x,2),round(z,2)] for x,z,*_ in line.coords]
                if len(pts)<2:continue
                roadname=prop.get("name") or prop.get("name:et") or ""
                surface=prop.get("surface","")
                # Posti was reconstructed with stone setts (2022); Karja is also stone-paved.
                if street_key(roadname) in ("posti","karja"):
                    surface="sett"
                roads.append({"points":pts,"width":width,"type":highway,"name":roadname, "surface":surface,"ref":prop.get("ref",""),"lanes":prop.get("lanes",""),"marking":prop.get("lane_markings",""),"sidewalk":prop.get("sidewalk",""),"sidewalkLeft":prop.get("sidewalk:left",""),"sidewalkRight":prop.get("sidewalk:right","")})
                if highway not in ("track","service") and line.length>20:
                    for a,b in zip(pts,pts[1:]):
                        x,z=(a[0]+b[0])/2,(a[1]+b[1])/2
                        if math.dist(a,b)>12 and valid_at(x,z):
                            spawn_options.append((math.hypot(x,z),x,z,math.atan2(b[0]-a[0],b[1]-a[1])))
        if prop.get("natural")=="water" or prop.get("water") or prop.get("waterway")=="riverbank":
            for poly in parts(g,"Polygon"):
                if poly.area<20:continue
                rings=[np.asarray(poly.exterior.coords)[:-1]]+[np.asarray(r.coords)[:-1] for r in poly.interiors]
                if len(rings[0])<3:continue
                xy=np.ascontiguousarray(np.vstack(rings),dtype=np.float64)
                ind=mapbox_earcut.triangulate_float64(xy,np.cumsum([len(r) for r in rings],dtype=np.uint32))
                # inland water elevation from perimeter DTM; use sea level for bays where known
                level=0.0 if prop.get("water") in ("sea","bay","lagoon") else float(np.median([height(x,z) for x,z in rings[0]]))-.08
                water.extend(np.column_stack((xy[ind,0],np.full(len(ind),level),xy[ind,1])).round(3).tolist())
        if highway=="crossing" and g.geom_type=="Point":crossings.append([g.x,g.y])
        if prop.get("natural")=="tree" and g.geom_type=="Point":
            trees.append([g.x,g.y])
        if prop.get("natural")=="wood" or prop.get("landuse")=="forest":
            forest.extend(parts(g,"Polygon"))
    details=compile_details(ROOT,osm,tolocal,bounds,height,footprints,report)
    street_objects=compile_street_objects(ROOT,osm,tolocal,bounds,roads,report)
    report['postiSegments']=sum(len(r['points'])-1 for r in roads if street_key(r['name'])=='posti')
    report['postiLengthM']=round(sum(LineString(r['points']).length for r in roads if street_key(r['name'])=='posti'),1)
    report['kaubamajaWallCandidates']=len(landmark_walls)
    if not roads or not spawn_options:raise RuntimeError("No valid drivable road in terrain coverage. Check tiles and OSM.")
    # Seeded forest sampling, avoiding real buildings and mapped streets.
    blocked=footprints+[LineString(r["points"]).buffer(r["width"]/2+2) for r in roads]
    treeindex=STRtree(blocked)
    def tree_ok(x,z):
        return valid_at(x,z) and not len(treeindex.query(Point(x,z),predicate="intersects"))
    # Use official measured vegetation, not random OSM trees, when the GPKG is available.
    official=measured_trees(ROOT,(w,s,e,n),cx,cy,height,valid_at,treeindex,report)
    for poly in ([] if official is not None else forest):
        a,b,c,d=poly.bounds
        for x in np.arange(a,c,17):
            for z in np.arange(b,d,17):
                xx=x+rng.uniform(0,12);zz=z+rng.uniform(0,12)
                if poly.contains(Point(xx,zz)) and tree_ok(xx,zz):
                    trees.append([xx,zz])
    trees=official if official is not None else [[round(x,2),round(height(x,z),2),round(z,2),round(float(rng.uniform(5,13)),2)] for x,z in trees if tree_ok(x,z)]
    # Export only validated on-ground road segments for teleportation.
    for road in roads:
        road["teleport"]=[i for i,(a,b) in enumerate(zip(road["points"],road["points"][1:])) if math.dist(a,b)>2 and valid_at(*a) and valid_at(*b) and valid_at((a[0]+b[0])/2,(a[1]+b[1])/2)]
    _,sx,sz,yaw=min(spawn_options)
    car=ROOT/"assets/cars/bmw_3-series_e36_street.glb"
    if not car.exists() or is_pointer(car):raise RuntimeError("Download the BMW GLB with git lfs pull.")
    shutil.copyfile(car,OUT/"car.glb")
    pine=ROOT/"assets/nature/pine_trees_pack_lowpoly_game_ready_lods.glb"
    pine_asset=pine.exists() and not is_pointer(pine)
    if pine_asset:shutil.copyfile(pine,OUT/"pine.glb")
    report["pineAssetAvailable"]=pine_asset
    report["waterTriangles"]=len(water)//3
    manifest={"version":6,"details":details,"streetObjects":street_objects,"landmarkWalls":landmark_walls,"facadeAtlases":photo_atlases,"buildingStride":9,"bounds":bounds,"origin":CENTER,"nx":nx,"nz":nz,"dx":dx,"dz":dz,
      "terrainCoverage":round(float(valid.mean()),4),"orthoCoverage":round(float(mask.mean()),4),
      "buildings":buildings,"buildingCount":count,"roads":roads,"trees":trees,"aerialTiles":tiles,"pineAsset":pine_asset,"water":water,"crossings":crossings,
      "spawn":[sx,height(sx,sz)+1.0,sz,yaw]}
    (OUT/"manifest.json").write_text(json.dumps(manifest,separators=(",",":")),encoding="utf-8")
    print(f"Built {count} buildings, {len(roads)} road segments, {len(trees)} trees, {nx}x{nz} height grid.")
    print("Map ready: npm run dev")
def make_materials():
    """Extract only usable images from supplied material archives; make repeated facade tiles."""
    rng=np.random.default_rng(122)
    def texture(prefix, fallback, gray=False):
        matches=sorted((ROOT/"assets/textures").glob(prefix+"*.zip"))
        for path in matches:
            if is_pointer(path):continue
            try:
                with zipfile.ZipFile(path) as z:
                    options=[n for n in z.namelist() if n.lower().endswith((".jpg",".jpeg",".png")) and any(s in n.lower() for s in ("color","diff","albedo","basecolor"))]
                    if not options:continue
                    with z.open(options[0]) as f:
                        img=Image.open(f).convert("RGB").resize((512,512),Image.Resampling.LANCZOS)
                    if gray:
                        g=np.asarray(ImageOps.grayscale(img),dtype=np.float32)
                        img=Image.fromarray(np.clip(205+(g-g.mean())*.65,80,250).astype(np.uint8)).convert("RGB")
                    print("Material:",prefix,path.name);return img
            except Exception as exc:print("Material fallback",prefix,str(exc))
        v=np.clip(rng.normal(fallback,8,(512,512)),0,255).astype(np.uint8)
        return Image.fromarray(v).convert("RGB")
    def facade(name,src):
        im=src.copy();d=ImageDraw.Draw(im)
        # Repeating 2.7m x 2.8m facade module: glazing, frames, sill and shadow.
        d.rectangle((174,142,338,350),fill=(78,76,68))
        d.rectangle((180,147,332,343),fill=(224,220,204))
        d.rectangle((190,157,322,331),fill=(52,65,72))
        d.rectangle((252,157,260,331),fill=(212,208,191))
        d.rectangle((190,222,322,230),fill=(212,208,191))
        d.rectangle((169,341,343,350),fill=(165,160,144))
        im.save(OUT/(name+".jpg"),quality=92)
    plaster=texture("Plaster001",211,True)
    wood=texture("WoodSiding001",195,True)
    brick=texture("Bricks025",187,True)
    facade("facade_plaster",plaster);facade("facade_wood",wood);facade("facade_brick",brick)
    plaster.save(OUT/"wall_plain.jpg",quality=90)
    wood.save(OUT/"wood_plain.jpg",quality=90)
    brick.save(OUT/"brick_plain.jpg",quality=90)
    texture("RoofingTiles001",195,True).save(OUT/"roof.jpg",quality=90)
    asphalt=texture("asphalt_03",80)
    # Asphalt photographed under warm light should not become a brown road.
    asphalt=ImageOps.grayscale(asphalt).convert("RGB")
    asphalt.save(OUT/"asphalt.jpg",quality=92)
    texture("PavingStones033",110).save(OUT/"cobble.jpg",quality=94)
    texture("Bricks100",140).save(OUT/"masonry.jpg",quality=94)
    texture("PavingStones144",155).save(OUT/"paving.jpg",quality=90)
    texture("Grass001",110).save(OUT/"grass.jpg",quality=90)
    # Prefer supplied OpenGL normal maps. Otherwise derive a restrained detail normal.
    for name,prefix in (("asphalt","asphalt_03"),("cobble","PavingStones033"),("masonry","Bricks100"),("paving","PavingStones144"),("roof","RoofingTiles001"),("wall_plain","Plaster001"),("wood_plain","WoodSiding001"),("brick_plain","Bricks025"),("facade_plaster",None),("facade_wood",None),("facade_brick",None)):
        normal=None
        if prefix:
            for path in sorted((ROOT/"assets/textures").glob(prefix+"*.zip")):
                if is_pointer(path):continue
                try:
                    with zipfile.ZipFile(path) as z:
                        candidates=[n for n in z.namelist() if n.lower().endswith((".png",".jpg",".jpeg")) and any(s in n.lower() for s in ("normalgl","normal_gl","nor_gl","normal_opengl"))]
                        if candidates:
                            with z.open(candidates[0]) as f:normal=Image.open(f).convert("RGB").resize((512,512),Image.Resampling.LANCZOS)
                            break
                except Exception as exc:print("Normal map fallback",name,exc)
        if normal is None:
            with Image.open(OUT/(name+".jpg")) as image:
                gray=np.asarray(image.convert("L"),dtype=np.float32)/255
            gy,gx=np.gradient(gray);v=np.stack((-gx*1.3,gy*1.3,np.ones_like(gray)),axis=-1)
            v/=np.linalg.norm(v,axis=-1,keepdims=True)
            normal=Image.fromarray(np.uint8(np.clip(v*.5+.5,0,1)*255),"RGB")
        normal.save(OUT/(name+"_normal.png"))

if __name__=="__main__":
    report={}
    OUT.mkdir(parents=True,exist_ok=True)
    try:main(report)
    finally:
        (OUT/"build_report.json").write_text(json.dumps(report,indent=2,ensure_ascii=False,default=str),encoding="utf-8")
        print("Data inspection report:",OUT/"build_report.json")
