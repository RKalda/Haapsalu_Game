"""Local, reproducible EPSG:3301 map compiler. Input archives remain untouched."""
from pathlib import Path
import argparse, json, math, shutil, tempfile, zipfile
from collections import defaultdict
import numpy as np
import rasterio
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.transform import from_origin
from PIL import Image
from pyproj import Transformer
import pyogrio
from shapely import from_wkb
from shapely.geometry import shape, box, LineString, Point
from shapely.ops import transform as geom_transform
from shapely.strtree import STRtree
import mapbox_earcut

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
def main():
    p=argparse.ArgumentParser();p.add_argument("--step",type=float,default=6);p.add_argument("--texture-size",type=int,default=4096)
    args=p.parse_args()
    if args.step<2 or args.texture_size<256: raise ValueError("Invalid resolution")
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"manifest.json").unlink(missing_ok=True)
    # Fixed local origin, north is -Z. Grid is aligned to the requested geographic bounding box.
    w,s,e,n=transform_bounds("EPSG:4326",CRS,*BBOX,densify_pts=21)
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
        # Read real 3D faces directly from official file geodatabase.
        gdbs=list(ROOT.glob("*gdb.zip"))
        if not gdbs: raise RuntimeError("Building GDB ZIP is missing")
        gd=temp/"gdb";gd.mkdir();extract(gdbs[0],gd)
        db=next(gd.rglob("*.gdb"),None)
        if db is None: raise RuntimeError("Could not locate .gdb in archive")
        chunks=defaultdict(list); footprints=[]; rng=np.random.default_rng(4729); count=0
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
            meta,fids,geom,fields=pyogrio.raw.read(db,layer=layer,bbox=(w,s,e,n),return_fids=True,columns=[])
            for g in from_wkb(geom):
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
                key=(math.floor(x/500),math.floor(z/500))
                for pts,slope in faces:
                    if slope>.5 and pts[:,2].max()<low+.15: continue
                    xyz=np.column_stack((pts[:,0]-cx,pts[:,2],cy-pts[:,1]))
                    col=np.tile(roof if slope>.35 else wall,(len(pts),1))
                    chunks[key].append(np.column_stack((xyz,col)).astype("<f4"))
                footprints.append(box(bw-cx,cy-bn,be-cx,cy-bs));count+=1
        if not count: raise RuntimeError("No usable 3D buildings in this area; inspect GDB layers and CRS.")
        buildings=[]
        for (a,b),data in sorted(chunks.items()):
            name=f"b_{a}_{b}.dat";v=np.concatenate(data);v.tofile(OUT/name)
            buildings.append({"file":name,"center":[a*500+250,b*500+250],"vertices":len(v)})
    raw=ROOT/"export.geojson"
    if is_pointer(raw): raise RuntimeError("OSM file is only an LFS pointer")
    osm=json.loads(raw.read_text(encoding="utf-8-sig"))
    clip=box(*bounds);roads=[];trees=[];forest=[];spawn_options=[]
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
                roads.append({"points":pts,"width":width,"type":highway,"name":prop.get("name","")})
                if highway not in ("track","service") and line.length>20:
                    for a,b in zip(pts,pts[1:]):
                        x,z=(a[0]+b[0])/2,(a[1]+b[1])/2
                        if math.dist(a,b)>12 and valid_at(x,z):
                            spawn_options.append((math.hypot(x,z),x,z,math.atan2(b[0]-a[0],b[1]-a[1])))
        if prop.get("natural")=="tree" and g.geom_type=="Point":
            trees.append([g.x,g.y])
        if prop.get("natural")=="wood" or prop.get("landuse")=="forest":
            forest.extend(parts(g,"Polygon"))
    if not roads or not spawn_options:raise RuntimeError("No valid drivable road in terrain coverage. Check tiles and OSM.")
    # Seeded forest sampling, avoiding real buildings and mapped streets.
    blocked=footprints+[LineString(r["points"]).buffer(r["width"]/2+2) for r in roads]
    treeindex=STRtree(blocked)
    def tree_ok(x,z):
        return valid_at(x,z) and not len(treeindex.query(Point(x,z),predicate="intersects"))
    for poly in forest:
        a,b,c,d=poly.bounds
        for x in np.arange(a,c,17):
            for z in np.arange(b,d,17):
                xx=x+rng.uniform(0,12);zz=z+rng.uniform(0,12)
                if poly.contains(Point(xx,zz)) and tree_ok(xx,zz):
                    trees.append([xx,zz])
    trees=[[round(x,2),round(height(x,z),2),round(z,2),round(float(rng.uniform(5,13)),2)] for x,z in trees if tree_ok(x,z)]
    _,sx,sz,yaw=min(spawn_options)
    car=ROOT/"assets/cars/bmw_3-series_e36_street.glb"
    if not car.exists() or is_pointer(car):raise RuntimeError("Download the BMW GLB with git lfs pull.")
    shutil.copyfile(car,OUT/"car.glb")
    manifest={"bounds":bounds,"origin":CENTER,"nx":nx,"nz":nz,"dx":dx,"dz":dz,
      "terrainCoverage":round(float(valid.mean()),4),"orthoCoverage":round(float(mask.mean()),4),
      "buildings":buildings,"buildingCount":count,"roads":roads,"trees":trees,
      "spawn":[sx,height(sx,sz)+1.0,sz,yaw]}
    (OUT/"manifest.json").write_text(json.dumps(manifest,separators=(",",":")),encoding="utf-8")
    print(f"Built {count} buildings, {len(roads)} road segments, {len(trees)} trees, {nx}x{nz} height grid.")
    print("Map ready: npm run dev")
if __name__=="__main__":main()
