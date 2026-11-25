"""Acquire a bounded Mapillary pilot dataset, not a finished facade reconstruction.
Uses only the documented Graph API. No credentials are written to disk.
"""
import argparse,getpass,json,math,os,time,urllib.parse,urllib.request,urllib.error
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/raw/facades/mapillary'
PILOT=(23.530,58.939,23.553,58.952)
WORLD=(23.490,58.920,23.590,58.960)
API='https://graph.mapillary.com/'
SEARCH='id,geometry,computed_geometry,captured_at,compass_angle,computed_compass_angle,is_pano,sequence,creator'
DETAIL='id,geometry,computed_geometry,captured_at,compass_angle,computed_compass_angle,is_pano,sequence,camera_type,camera_parameters,computed_rotation,computed_altitude,altitude,width,height,exif_orientation,thumb_2048_url'
def write(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
def api(token,path,params):
    url=API+path+'?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'Authorization':'OAuth '+token,'User-Agent':'HaapsaluDrive/0.7'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req,timeout=45) as f:result=json.load(f)
            if 'error' in result:raise RuntimeError('Mapillary returned API error')
            return result
        except urllib.error.HTTPError as e:
            if e.code not in (429,500,502,503,504) or attempt==3:raise RuntimeError(f'Mapillary API HTTP {e.code} on {path}') from None
        except (TimeoutError,urllib.error.URLError):
            if attempt==3:raise RuntimeError(f'Mapillary request failed on {path}') from None
        time.sleep(2**attempt)
    raise RuntimeError('Mapillary request failed')
def coords(row):
    g=row.get('computed_geometry') or row.get('geometry') or {}
    p=g.get('coordinates',[])
    return tuple(p[:2]) if len(p)>=2 else None
def search(token,bbox):
    rows={};requests=0
    def query(box,depth=0):
        nonlocal requests
        if requests>=180:raise RuntimeError('Metadata query budget reached; use a smaller bbox')
        requests+=1
        result=api(token,'images',{'bbox':','.join(map(str,box)),'fields':SEARCH,'limit':1000})
        data=result.get('data')
        if not isinstance(data,list):raise RuntimeError('Missing image search data')
        if len(data)>=1000:
            if depth>=7:raise RuntimeError('Dense tile still saturated; reduce bbox')
            w,s,e,n=box;x=(w+e)/2;y=(s+n)/2
            for q in ((w,s,x,y),(x,s,e,y),(w,y,x,n),(x,y,e,n)):query(q,depth+1)
        else:
            for r in data:
                if r.get('id') and coords(r):rows[str(r['id'])]=r
        print('Search tiles:',requests,'unique images:',len(rows),flush=True)
    w,s,e,n=bbox
    # Small requests, split automatically if capped. Bbox queries lack paging.
    for i in range(4):
        for j in range(4):
            query((w+(e-w)*i/4,s+(n-s)*j/4,w+(e-w)*(i+1)/4,s+(n-s)*(j+1)/4))
    return list(rows.values())
def select(rows,max_images,spacing):
    cells={}
    for r in rows:
        p=coords(r)
        if not p:continue
        x,y=p
        h=r.get('computed_compass_angle')
        if h is None:h=r.get('compass_angle')
        sector=0 if r.get('is_pano') else 1+int(float(h or 0)%360//90)
        cell=(math.floor(x*111320*math.cos(math.radians(58.94))/spacing),math.floor(y*111320/spacing),sector)
        old=cells.get(cell)
        if old is None or (r.get('captured_at') or 0)>(old.get('captured_at') or 0):cells[cell]=r
    candidates=list(cells.values())
    # Distribute a limited pilot over the area, rather than choosing just one sequence.
    candidates.sort(key=lambda r:(coords(r)[1],coords(r)[0],str(r['id'])))
    if len(candidates)>max_images:
        candidates=[candidates[int(i*len(candidates)/max_images)] for i in range(max_images)]
    return candidates
def main():
    p=argparse.ArgumentParser();p.add_argument('--area',choices=['pilot','city'],default='pilot');p.add_argument('--metadata-only',action='store_true');p.add_argument('--max-images',type=int,default=160);p.add_argument('--spacing',type=float,default=12);p.add_argument('--refresh',action='store_true');a=p.parse_args()
    if a.max_images<1 or a.max_images>5000 or a.spacing<3:raise SystemExit('Invalid image limit or spacing')
    token=os.environ.get('MAPILLARY_TOKEN') or getpass.getpass('Mapillary client access token (hidden): ')
    if not token.strip():raise SystemExit('Token required')
    folder=OUT/a.area;folder.mkdir(parents=True,exist_ok=True);(folder/'images').mkdir(exist_ok=True)
    catalog=folder/'catalog.json'
    rows=json.loads(catalog.read_text(encoding='utf-8')) if catalog.exists() and not a.refresh else search(token,PILOT if a.area=='pilot' else WORLD)
    write(catalog,rows)
    picked=select(rows,a.max_images,a.spacing)
    years=Counter(time.strftime('%Y',time.gmtime(r['captured_at']/1000)) for r in rows if r.get('captured_at'))
    report={'area':a.area,'imagesFound':len(rows),'panoramas':sum(bool(r.get('is_pano')) for r in rows),'captureYears':dict(years),'selected':len(picked),'downloaded':0,'withCameraParameters':0,'withComputedRotation':0,'withComputedGeometry':0,'withCreator':0,'errors':[]}
    if not a.metadata_only:
        for i,row in enumerate(picked,1):
            ident=str(row['id'])
            if not ident.isdigit():continue
            dst=folder/'images'/(ident+'.jpg');meta=dst.with_suffix('.json')
            try:
                if dst.exists() and meta.exists() and not a.refresh:d=json.loads(meta.read_text(encoding='utf-8'))
                else:
                    d=api(token,ident,{'fields':DETAIL})
                    url=d.pop('thumb_2048_url',None)
                    if not url or not url.startswith('https://'):raise RuntimeError('No HTTPS image URL')
                    req=urllib.request.Request(url,headers={'User-Agent':'HaapsaluDrive/0.7'})
                    with urllib.request.urlopen(req,timeout=45) as f:raw=f.read(32*1024*1024+1)
                    if not raw.startswith(b'\xff\xd8') or len(raw)>32*1024*1024:raise RuntimeError('Unexpected image response')
                    tmp=dst.with_suffix('.tmp');tmp.write_bytes(raw);tmp.replace(dst)
                    d={**row,**d,'source':'https://www.mapillary.com/app/?pKey='+ident,'license':'CC BY-SA 4.0 (verify applicable image terms)'}
                    write(meta,d)
                report['downloaded']+=1
                for key,field in [('withCameraParameters','camera_parameters'),('withComputedRotation','computed_rotation'),('withComputedGeometry','computed_geometry'),('withCreator','creator')]:
                    report[key]+=int(bool(d.get(field)))
            except Exception as e:report['errors'].append({'id':ident,'error':type(e).__name__})
            if i%10==0 or i==len(picked):
                write(folder/'coverage_report.json',report);print(i,'/',len(picked),'downloaded:',report['downloaded'],flush=True)
            time.sleep(.15)
    write(folder/'coverage_report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2));print('Report:',folder/'coverage_report.json')
if __name__=='__main__':main()
