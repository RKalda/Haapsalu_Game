"""Acquire a bounded Mapillary pilot dataset, not a finished facade reconstruction.
Uses public vector tiles for discovery and the Graph API for individual images. No credentials are written to disk.
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
DETAIL='id,geometry,computed_geometry,captured_at,compass_angle,computed_compass_angle,sequence,camera_type,camera_parameters,computed_rotation,computed_altitude,altitude,width,height,exif_orientation,thumb_2048_url'
MINIMAL='id,thumb_2048_url'
EXTRA_FIELDS=DETAIL.split(',')[1:-1]+['creator']
def write(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(path)
def api(token,path,params):
    url=API+path+'?'+urllib.parse.urlencode({**params,'access_token':token})
    req=urllib.request.Request(url,headers={'User-Agent':'HaapsaluDrive/0.9'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req,timeout=45) as f:result=json.load(f)
            if 'error' in result:raise RuntimeError('Mapillary returned API error')
            return result
        except urllib.error.HTTPError as e:
            if e.code not in (429,500,502,503,504) or attempt==3:
                try:
                    err=json.loads(e.read(65536)).get('error',{})
                    msg=str(err.get('message','')).replace(token,'[REDACTED]').replace(urllib.parse.quote(token,safe=''),'[REDACTED]')
                    # Never log URLs or arbitrary server response bodies.
                    import re
                    msg=re.sub(r'https?://\S+','[URL]',msg)
                    msg=re.sub(r'MLY\|[^\s,;]+','[REDACTED]',msg)
                    detail=f"code={err.get('code','?')}, subcode={err.get('error_subcode','?')}: {msg[:300]}"
                except Exception:detail='No parseable API error message'
                raise RuntimeError(f'Mapillary API HTTP {e.code} on {path}: {detail}') from None
        except (TimeoutError,urllib.error.URLError):
            if attempt==3:raise RuntimeError(f'Mapillary request failed on {path}') from None
        time.sleep(2**attempt)
    raise RuntimeError('Mapillary request failed')
def coords(row):
    g=row.get('computed_geometry') or row.get('geometry') or {}
    p=g.get('coordinates',[])
    return tuple(p[:2]) if len(p)>=2 else None

def negotiate_fields(token,ident):
    """Verify the simplest request first, then find the supported metadata fields once."""
    basic=api(token,ident,{'fields':MINIMAL})
    if not basic.get('thumb_2048_url'):raise RuntimeError('Basic Graph request returned no thumbnail URL')
    print('Graph preflight passed: image URL available',flush=True)
    accepted=MINIMAL.split(',')
    wanted=list(dict.fromkeys(EXTRA_FIELDS))
    try:
        detail=api(token,ident,{'fields':','.join(accepted+wanted)})
        return ','.join(accepted+wanted),detail,[]
    except RuntimeError as e:
        print('Full metadata request rejected:',str(e),flush=True)
    rejected=[]
    def probe(group):
        if not group:return
        try:
            api(token,ident,{'fields':','.join(accepted+group)})
            accepted.extend(group)
        except RuntimeError as e:
            if 'HTTP 400' not in str(e):raise
            if len(group)==1:
                rejected.append(group[0]);print('Unsupported metadata field:',group[0],flush=True)
            else:
                mid=len(group)//2;probe(group[:mid]);probe(group[mid:])
    probe(wanted)
    fields=','.join(accepted)
    return fields,api(token,ident,{'fields':fields}),rejected

def search(token,bbox):
    # Same public image coverage tiles used by Mapillary's own API demo.
    import mercantile,mapbox_vector_tile,gzip
    rows={};w,s,e,n=bbox
    tiles=list(mercantile.tiles(w,s,e,n,14))
    for layer in ('mly1_public','mly1_computed_public'):
        for tile in tiles:
            url=f'https://tiles.mapillary.com/maps/vtp/{layer}/2/{tile.z}/{tile.x}/{tile.y}?'+urllib.parse.urlencode({'access_token':token})
            try:
                req=urllib.request.Request(url,headers={'User-Agent':'HaapsaluDrive/0.8'})
                with urllib.request.urlopen(req,timeout=45) as response:raw=response.read()
                if raw[:2]==bytes([31,139]):raw=gzip.decompress(raw)
                decoded=mapbox_vector_tile.decode(raw,default_options={'y_coord_down':True}) if raw else {}
            except urllib.error.HTTPError as exc:
                if exc.code==204:continue
                raise RuntimeError(f'Tile HTTP {exc.code} ({layer}, {tile.x}, {tile.y})') from None
            except Exception as exc:raise RuntimeError(f'Tile download/decode failed: {type(exc).__name__}') from None
            image=decoded.get('image',{})
            extent=image.get('extent',4096);features=image.get('features',[])
            count=0
            for f in features:
                g=f.get('geometry') or {}
                if g.get('type')!='Point':continue
                px,py=g['coordinates'][:2]
                tx=(tile.x+px/extent)/2**tile.z;ty=(tile.y+py/extent)/2**tile.z
                lon=tx*360-180;lat=math.degrees(math.atan(math.sinh(math.pi*(1-2*ty))))
                if not(w<=lon<=e and s<=lat<=n):continue
                p=f.get('properties') or {};ident=str(p.get('id') or f.get('id') or '')
                if not ident.isdigit():continue
                r=rows.setdefault(ident,{'id':ident})
                key='computed_geometry' if layer=='mly1_computed_public' else 'geometry'
                r[key]={'type':'Point','coordinates':[lon,lat]}
                for k in ('captured_at','compass_angle','is_pano','sequence_id','quality_score','creator_id'):
                    if k in p:r[k]=p[k]
                count+=1
            print(f'{layer} {tile.x}/{tile.y}: {len(features)} tile points, {count} inside bbox; {len(rows)} unique',flush=True)
    if not rows:raise RuntimeError('Both coverage layers returned zero local image points. Open a visible image in Mapillary and use --image-id with its pKey to diagnose.')
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
    p=argparse.ArgumentParser();p.add_argument('--area',choices=['pilot','city'],default='pilot');p.add_argument('--metadata-only',action='store_true');p.add_argument('--max-images',type=int,default=160);p.add_argument('--spacing',type=float,default=12);p.add_argument('--refresh',action='store_true');p.add_argument('--image-id',help='Known Mapillary pKey for direct API diagnosis');a=p.parse_args()
    if a.max_images<1 or a.max_images>5000 or a.spacing<3:raise SystemExit('Invalid image limit or spacing')
    token=os.environ.get('MAPILLARY_TOKEN') or getpass.getpass('Mapillary client access token (hidden): ')
    if not token.strip():raise SystemExit('Token required')
    folder=OUT/a.area;folder.mkdir(parents=True,exist_ok=True);(folder/'images').mkdir(exist_ok=True)
    catalog=folder/'catalog.json'
    if a.image_id:
        if not a.image_id.isdigit():raise SystemExit('Image ID must be numeric')
        rows=[api(token,a.image_id,{'fields':'id,geometry,computed_geometry,captured_at,compass_angle'})]
    else:
        cached=json.loads(catalog.read_text(encoding='utf-8')) if catalog.exists() and not a.refresh else []
        rows=cached or search(token,PILOT if a.area=='pilot' else WORLD)
        write(catalog,rows)
    picked=select(rows,a.max_images,a.spacing)
    years=Counter(time.strftime('%Y',time.gmtime(r['captured_at']/1000)) for r in rows if r.get('captured_at'))
    report={'area':a.area,'imagesFound':len(rows),'panoramas':sum(bool(r.get('is_pano')) for r in rows),'captureYears':dict(years),'selected':len(picked),'downloaded':0,'withCameraParameters':0,'withComputedRotation':0,'withComputedGeometry':0,'withCreator':0,'errors':[]}
    if not a.metadata_only and picked:
        first_id=str(picked[0]['id'])
        try:
            fields,first,rejected=negotiate_fields(token,first_id)
        except RuntimeError as e:
            report['preflightError']=str(e);write(folder/'coverage_report.json',report)
            raise SystemExit('Stopped before bulk download: '+str(e)) from None
        report['graphFields']=fields;report['rejectedFields']=rejected
        consecutive=0
        for i,row in enumerate(picked,1):
            ident=str(row['id'])
            if not ident.isdigit():continue
            dst=folder/'images'/(ident+'.jpg');meta=dst.with_suffix('.json')
            try:
                if dst.exists() and meta.exists() and not a.refresh:d=json.loads(meta.read_text(encoding='utf-8'))
                else:
                    d=dict(first) if ident==first_id else api(token,ident,{'fields':fields})
                    url=d.pop('thumb_2048_url',None)
                    if not url or not url.startswith('https://'):raise RuntimeError('No HTTPS image URL')
                    req=urllib.request.Request(url,headers={'User-Agent':'HaapsaluDrive/0.7'})
                    with urllib.request.urlopen(req,timeout=45) as f:raw=f.read(32*1024*1024+1)
                    if not raw.startswith(b'\xff\xd8') or len(raw)>32*1024*1024:raise RuntimeError('Unexpected image response')
                    tmp=dst.with_suffix('.tmp');tmp.write_bytes(raw);tmp.replace(dst)
                    d={**row,**d,'source':'https://www.mapillary.com/app/?pKey='+ident,'license':'CC BY-SA 4.0 (verify applicable image terms)'}
                    write(meta,d)
                consecutive=0
                report['downloaded']+=1
                for key,field in [('withCameraParameters','camera_parameters'),('withComputedRotation','computed_rotation'),('withComputedGeometry','computed_geometry'),('withCreator','creator')]:
                    report[key]+=int(bool(d.get(field)))
            except Exception as e:
                consecutive+=1
                report['errors'].append({'id':ident,'error':type(e).__name__,'detail':str(e) if isinstance(e,RuntimeError) else 'Request or decode failed'})
                if consecutive>=5:
                    print('Stopped after 5 consecutive failures; check report.',flush=True);break
            if i%10==0 or i==len(picked):
                write(folder/'coverage_report.json',report);print(i,'/',len(picked),'downloaded:',report['downloaded'],flush=True)
            time.sleep(.15)
    write(folder/'coverage_report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2));print('Report:',folder/'coverage_report.json')
if __name__=='__main__':main()
