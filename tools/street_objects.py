"""Official stop records, mapped roadside features and reference-driven materials."""
import csv,io,json,math,re,zipfile,urllib.request
from pathlib import Path
from collections import Counter
import numpy as np
from PIL import Image,ImageDraw
from shapely.geometry import Point,LineString
from shapely.strtree import STRtree

GTFS='https://www.peatus.ee/gtfs/gtfs.zip'
def fetch_stops(root):
    out=root/'data/raw/topography/gtfs_stops.json'
    req=urllib.request.Request(GTFS,headers={'User-Agent':'HaapsaluDrive/0.6 public transport open data'})
    with urllib.request.urlopen(req,timeout=120) as f:raw=f.read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        name=next(n for n in z.namelist() if n.rsplit('/',1)[-1]=='stops.txt')
        rows=list(csv.DictReader(io.TextIOWrapper(z.open(name),encoding='utf-8-sig')))
    stops=[]
    for row in rows:
        try:x,y=float(row['stop_lon']),float(row['stop_lat'])
        except (ValueError,KeyError):continue
        if 23.49<=x<=23.59 and 58.92<=y<=58.96 and row.get('location_type','') in ('','0'):
            stops.append({k:row.get(k,'') for k in ('stop_id','stop_code','stop_name','stop_lat','stop_lon')})
    if not stops:raise RuntimeError('GTFS returned no local platform stops')
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(stops,ensure_ascii=False),encoding='utf-8')
    print('Official GTFS stops:',len(stops),flush=True)

def street_key(name):
    return re.sub(r'\s+(tänav|tn)\.?$','',str(name).lower().strip())

def granite_setts(out):
    """Original rough grey stone setts inspired by the supplied road reference.
    2m repeat; no reuse of copyrighted Street View pixels."""
    rng=np.random.default_rng(438);size=1024
    # Generate oversized staggered rows, wrap the same grid at the tile edges.
    im=Image.new('RGB',(size,size),(70,70,67));draw=ImageDraw.Draw(im)
    relief=Image.new('L',(size,size),25);rd=ImageDraw.Draw(relief)
    rows=12;cols=11;dy=size/rows;dx=size/cols
    for row in range(rows):
        for col in range(cols):
            x=(col+(.5 if row%2 else 0))*dx;y=row*dy
            color=int(rng.integers(115,177));jitter=rng.uniform(-5,5,(8,2))
            coords=np.array([[6,3],[dx-8,4],[dx-3,12],[dx-5,dy-8],[dx-12,dy-3],[8,dy-5],[3,dy-14],[3,10]])+jitter
            for shift in (-size,0,size):
                points=[(float(a+x+shift),float(b+y)) for a,b in coords]
                draw.polygon(points,fill=(color,color+1,color+2))
                rd.polygon(points,fill=180)
                draw.line(points[:3],fill=(color+13,color+14,color+15),width=2)
    arr=np.asarray(im,dtype=np.float32)+rng.normal(0,5,(size,size,1))
    Image.fromarray(np.uint8(np.clip(arr,0,255))).save(out/'granite.jpg',quality=94)
    h=np.asarray(relief,dtype=np.float32)/255;gy,gx=np.gradient(h)
    n=np.stack((-gx*2.2,gy*2.2,np.ones_like(h)),axis=-1);n/=np.linalg.norm(n,axis=-1,keepdims=True)
    Image.fromarray(np.uint8((n*.5+.5)*255)).save(out/'granite_normal.png')

def compile_street_objects(root,osm,project,bounds,roads,report):
    from topography import read_osm
    from shapely.geometry import shape,box
    from shapely.ops import transform
    top=root/'data/raw/topography';clip=box(*bounds);items=[]
    for f in osm.get('features',[]):
        if f.get('geometry'):
            p=f.get('properties') or {};items.append(({**p,**(p.get('tags') or {})},shape(f['geometry'])))
    for name in ('osm_details.json','osm_street_objects.json'):
        p=top/name
        if p.exists():items.extend(read_osm(json.loads(p.read_text(encoding='utf-8'))))
    segments=[];lines=[]
    for r in roads:
        for a,b in zip(r['points'],r['points'][1:]):
            line=LineString([a,b])
            if line.length>.1:lines.append(line);segments.append((a,b,r))
    tree=STRtree(lines) if lines else None
    def orient(x,z):
        if tree is None:return 0
        a,b,_=segments[int(tree.nearest(Point(x,z)))]
        return math.atan2(b[0]-a[0],b[1]-a[1])
    result=[];seen=set();stats=Counter()
    def add(kind,name,x,z,source,**props):
        if not clip.covers(Point(x,z)):return
        key=(kind,name,round(x/2),round(z/2))
        if key in seen:return
        seen.add(key);result.append(dict(kind=kind,name=name,x=round(x,3),z=round(z,3),yaw=orient(x,z),source=source,**props));stats[kind]+=1
    stops=top/'gtfs_stops.json'
    if stops.exists():
        for row in json.loads(stops.read_text(encoding='utf-8')):
            x,z=project(float(row['stop_lon']),float(row['stop_lat']))
            add('bus',row['stop_name'],float(x),float(z),'GTFS',code=row.get('stop_code',''))
    for p,g in items:
        if g.is_empty:continue
        try:q=transform(project,g).representative_point()
        except Exception:continue
        x,z=q.x,q.y;hw=p.get('highway','');name=p.get('name','')
        if hw=='bus_stop' or p.get('public_transport')=='platform' and p.get('bus')=='yes':
            if not any(a['kind']=='bus' and math.hypot(a['x']-x,a['z']-z)<12 for a in result):add('bus',name,x,z,'OSM')
        if p.get('traffic_sign') and g.geom_type=='Point':add('sign',str(p['traffic_sign']),x,z,'OSM',direction=str(p.get('direction','')))
        if hw=='stop':add('sign','stop',x,z,'OSM')
        if hw=='give_way':add('sign','give_way',x,z,'OSM')
        if hw=='crossing':add('crossing','',x,z,'OSM',crossing=p.get('crossing',''))
        if hw=='traffic_signals':add('signal','',x,z,'OSM')
        if hw=='street_lamp':add('lamp','',x,z,'OSM')
        if p.get('barrier')=='bollard':add('bollard','',x,z,'OSM')
        if p.get('amenity')=='bench':add('bench','',x,z,'OSM')
        if p.get('amenity')=='shelter' and p.get('shelter_type')=='public_transport':add('shelter','',x,z,'OSM')
    report['streetObjects']=dict(stats)
    return result

if __name__=='__main__':fetch_stops(Path(__file__).resolve().parents[1])
