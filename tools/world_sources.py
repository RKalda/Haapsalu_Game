"""Additional world sources. All inference is explicit in the build report."""
import math, re, unicodedata, json
from pathlib import Path
from collections import Counter
from contextlib import ExitStack
import numpy as np
import pyogrio
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from pyproj import CRS, Transformer
from shapely import from_wkb
from shapely.geometry import Point
from shapely.strtree import STRtree
from PIL import Image, ImageDraw

def norm(s):
    return re.sub(r'[^a-z0-9]','',unicodedata.normalize('NFKD',str(s).lower()).encode('ascii','ignore').decode())
def field(names, exact, contains=()):
    normalized={norm(n):n for n in names}
    for key in exact:
        if norm(key) in normalized:return normalized[norm(key)]
    for key in contains:
        for n,original in normalized.items():
            if norm(key) in n:return original
    return None
def num(x):
    try:
        x=float(x)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError):return None
def value(row,key):
    return row.get(key) if key else None
def features(path,bounds,report,tag,allowed_layers=None):
    for layer,kind in pyogrio.list_layers(path):
        if not kind or (allowed_layers is not None and layer not in allowed_layers):continue
        info=pyogrio.read_info(path,layer=layer)
        if not info.get('crs'):continue
        crs=CRS.from_user_input(info['crs'])
        if crs.to_epsg()!=3301 and not (crs.sub_crs_list and crs.sub_crs_list[0].to_epsg()==3301):
            raise RuntimeError(f'Unexpected CRS for {path}: {crs}')
        meta,_,geoms,cols=pyogrio.raw.read(path,layer=layer,bbox=bounds)
        names=list(meta['fields'])
        report.setdefault(tag,[]).append({'layer':str(layer),'fields':[str(n) for n in names],'features':len(geoms),'sample':{str(n):str(col[0])[:160] for n,col in zip(names,cols) if len(col)}})
        yield names,from_wkb(geoms),cols
def identifier(v):
    if v is None:return None
    t=str(v).strip()
    return t[:-2] if t.endswith('.0') else t if t and t.lower() not in ('nan','none','0') else None

class BuildingInfo:
    def __init__(self,root,bounds,report):
        self.rows=[];self.geoms=[];self.ids={};self.report=report;self.stats=Counter()
        paths=list((root/'data/raw/building_metadata').rglob('*.gpkg'))
        for path in paths:
            for names,geoms,cols in features(path,bounds,report,'buildingLayers'):
                ek=field(names,['etak_id','etakid','etak_oid'],['etakid'])
                ehr=field(names,['ehr_gid','ehr_kood','ehr_code','ehrkood','ehr_id'],['ehrkood'])
                finish=field(names,['valisviimistlus','valisviimistluse_materjal','valisseina_valisviimistlus'],['valisviimist','fassaadimaterjal','fassaad'])
                structure=field(names,['valisseina_materjal','valisseinad','seinamaterjal'],['valisseina','seinamaterjal'])
                floors=field(names,['max_korruste_arv','korruste_arv','maapealsete_korruste_arv','korruselisus','korruseid'],['maapeal','korrus'])
                use=field(names,['kaos_nimetus_peamine','kaos_nimetus','kasutusotstarve','kasutusotstarbed','hoone_tyyp','nimetus','tyyp_tekst'],['kasutusotstar','hoonetyyp'])
                year=field(names,['esmane_kasutus','esmase_kasutuse_aasta','ehitusaasta'],['esmasekasut','ehitusaasta'])
                roof=field(names,['katusekate','katusekatte_materjal'],['katusekat'])
                report.setdefault('buildingFieldMapping',[]).append(dict(etak=ek,ehr=ehr,finish=finish,structure=structure,floors=floors,use=use,year=year,roof=roof))
                for i,g in enumerate(geoms):
                    if g is None or g.is_empty or g.geom_type not in ('Polygon','MultiPolygon'):continue
                    row={n:c[i] for n,c in zip(names,cols)}
                    entry={k:value(row,v) for k,v in dict(finish=finish,structure=structure,floors=floors,use=use,year=year,roof=roof).items()}
                    entry['etak']=identifier(value(row,ek))
                    index=len(self.rows);self.rows.append(entry);self.geoms.append(g)
                    for key,name in [('etak',ek),('ehr',ehr)]:
                        v=identifier(value(row,name))
                        if v:self.ids.setdefault((key,v),index)
        self.tree=STRtree(self.geoms) if self.geoms else None
    def match(self,row,geometry):
        names=list(row)
        for key,n in [('etak',field(names,['etak_id','etakid'],['etakid'])),('ehr',field(names,['ehr_gid','ehr_kood','ehr_id'],['ehrkood']))]:
            v=identifier(value(row,n))
            if v and (key,v) in self.ids:
                self.stats['idMatched']+=1;return self.rows[self.ids[key,v]]
        if self.tree is not None:
            a,b,c,d=geometry.bounds
            pt=Point((a+c)/2,(b+d)/2)
            hits=self.tree.query(pt,predicate='intersects')
            if len(hits)==1:self.stats['spatialMatched']+=1;return self.rows[int(hits[0])]
        self.stats['unmatched']+=1;return {}
    def style(self,row,geometry,h,rng,wall,family):
        rec=self.match(row,geometry)
        finish=norm(rec.get('finish') or '')
        structure=norm(rec.get('structure') or '')
        # Structure is a weak proxy, not an observed exterior finish.
        for material,kind in ((('laudis','puit','wood'),1),(('tellis','brick'),2),(('krohv','plaster','betoon','concrete'),0)):
            if any(x in finish for x in material):
                family=kind;self.stats['finishClassified']+=1;break
        else:
            if any(x in structure for x in ('puit','wood','palk')):
                family=1;self.stats['structureProxy']+=1
            else:self.stats['proceduralMaterial']+=1
        if family==2:wall=np.array([.72,.44,.34])
        if family==1:wall=np.clip(wall*1.04,.42,.94)
        floors=num(rec.get('floors'))
        if floors is not None and 1<=floors<=18 and 2.0<=h/floors<=7:
            self.stats['floorCountUsed']+=1
            floor_h=float(np.clip((h-(1.3 if h<11 else .4))/floors,2.35,3.65))
        else:floor_h=2.8
        use=norm(rec.get('use') or '')
        plain=any(x in use for x in ('kuur','garaaz','ladu','abihoone','angaar'))
        bid=rec.get('etak') or identifier(value(row,field(list(row),['etak_id','etakid','etak_oid'],['etakid'])))
        return family,wall,floor_h,plain,bid

def measured_trees(root,bounds,cx,cy,ground,valid,blocked,report):
    paths=list((root/'data/raw/vegetation').rglob('*.gpkg'))
    if not paths:return None
    result=[];stats=Counter();seen_ids=set()
    for path in paths:
        layers=pyogrio.list_layers(path)
        point_layers={layer for layer,kind in layers if kind and ('keskpunkt' in layer.lower() or kind.lower().startswith('point'))}
        report.setdefault('vegetationSkippedLayers',[]).extend(str(layer) for layer,kind in layers if point_layers and layer not in point_layers)
        for names,geoms,cols in features(path,bounds,report,'vegetationLayers',point_layers or None):
            hk=field(names,['korgus','height','puu_korgus','h','height_m','tree_height'],['puukorgus','height','korgus'])
            dk=field(names,['puukroon','vora_diameeter','diameter','crown_diameter','diameeter','vora_laius','d'],['diameet','diameter','voralai','voralabi','labimoot','crownwidth','crowndiameter'])
            tk=field(names,['tyyp','liik','puuliik','class','type','puu_tyyp'],['puuliik','puutyyp','treeclass','okasleht','liigiryhm'])
            idk=field(names,['puu_id','tree_id'])
            report.setdefault('vegetationFieldMapping',[]).append(dict(height=hk,diameter=dk,type=tk,id=idk))
            if not hk:continue
            for i,g in enumerate(geoms):
                if g is None or g.is_empty:continue
                row={n:c[i] for n,c in zip(names,cols)}
                h=num(value(row,hk));d=num(value(row,dk))
                if h is None or not 1.8<=h<=45:stats['invalidHeight']+=1;continue
                tid=identifier(value(row,idk))
                if tid and tid in seen_ids:stats['duplicateId']+=1;continue
                pt=g if g.geom_type=='Point' else g.centroid
                x,z=pt.x-cx,cy-pt.y
                if not valid(x,z) or len(blocked.query(Point(x,z),predicate='intersects')):stats['excluded']+=1;continue
                t=norm(value(row,tk) or '')
                conifer=1 if any(s in t for s in ('okas','conifer','pine','spruce')) else 0
                if not t or t.isdigit():stats['unknownType']+=1
                if d is None or not .3<=d<=40:d=h*(.38 if conifer else .57);stats['estimatedDiameter']+=1
                else:stats['measuredDiameter']+=1
                if tid:seen_ids.add(tid)
                result.append([round(x,2),round(ground(x,z),2),round(z,2),round(h,2),round(d,2),conifer]);stats['accepted']+=1
    report['vegetationStats']=dict(stats)
    if not result:raise RuntimeError('Vegetation GPKG supplied but no usable height field/trees. See public/world/build_report.json and adjust field mapping.')
    # Distinct measured IDs may legitimately stand close together; retain them.
    report['measuredTrees']=len(result)
    return result

def aerial_tiles(paths,out,bounds,nx,nz,step=96,pixels=1536):
    """Independent per-terrain-chunk orthophotos, sampled without full-source arrays."""
    w,s,e,n=bounds;tiles=[]
    with ExitStack() as stack:
        sources=[stack.enter_context(rasterio.open(p)) for p in paths]
        for row in range(0,nz-1,step):
            for col in range(0,nx-1,step):
                c1=min(col+step,nx-1);r1=min(row+step,nz-1)
                a=w+(e-w)*col/(nx-1);b=w+(e-w)*c1/(nx-1)
                top=n-(n-s)*row/(nz-1);bottom=n-(n-s)*r1/(nz-1)
                ww=pixels;hh=max(64,round(pixels*(top-bottom)/(b-a)))
                if hh>2048:ww=max(64,round(ww*2048/hh));hh=2048
                rgb=np.empty((3,hh,ww),np.uint8);rgb[:]=np.array([48,83,91])[:,None,None]
                covered=np.zeros((hh,ww),bool)
                for src in sources:
                    with WarpedVRT(src,crs='EPSG:3301',transform=from_bounds(a,bottom,b,top,ww,hh),width=ww,height=hh,resampling=Resampling.bilinear,add_alpha=True) as vrt:
                        if vrt.count<3:continue
                        good=vrt.dataset_mask()>0
                        if good.any():rgb[:,good]=vrt.read([1,2,3])[:,good];covered|=good
                if not covered.any():continue
                file=f'ortho_{row}_{col}.jpg'
                Image.fromarray(np.moveaxis(rgb,0,-1)).save(out/file,quality=88)
                tiles.append({'row':row,'col':col,'file':file})
    return tiles

def foliage_sprites(out):
    """Original transparent botanical impostors, not flattened opaque tree shapes."""
    for pine in (False,True):
        for variant in range(3):
            rng=np.random.default_rng(175+variant+10*pine)
            size=512;im=Image.new('RGBA',(size,size));d=ImageDraw.Draw(im)
            d.polygon([(248,508),(267,508),(260,91),(253,91)],fill=(97,77,55,255))
            centers=[]
            if pine:
                for i in range(12):
                    y=64+i*29;span=18+i*15
                    for side in (-1,1):
                        x=256+side*span*rng.uniform(.55,1)
                        d.line((256,y-15,x,y+24),fill=(86,69,48,255),width=3)
                        centers.append((x,y,span*.47+13,26+i*1.3))
            else:
                for i in range(34):
                    t=rng.uniform(0,2*math.pi);r=math.sqrt(rng.random())
                    x=256+math.cos(t)*185*r;y=205+math.sin(t)*155*r
                    d.line((256,375,x,y),fill=(88,73,53,255),width=max(2,int(6*(1-r*.6))))
                    centers.append((x,y,rng.uniform(36,82),rng.uniform(27,68)))
            for x,y,rx,ry in centers:
                for j in range(600 if not pine else 370):
                    t=rng.uniform(0,2*math.pi);r=math.sqrt(rng.random())
                    xx=x+math.cos(t)*rx*r;yy=y+math.sin(t)*ry*r
                    shade=rng.uniform(.65,1.3)*(1.12-yy/1700)
                    base=np.array([52,91,43] if pine else [72,111,44])
                    col=tuple(np.clip(base*shade+rng.normal(0,5,3),0,255).astype(int))+(255,)
                    a=rng.uniform(1.5,5 if not pine else 3);b=rng.uniform(1.5,4)
                    d.ellipse((xx-a,yy-b,xx+a,yy+b),fill=col)
            im.save(out/f'foliage_{int(pine)}_{variant}.png')
