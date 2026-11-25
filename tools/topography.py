"""Compile mapped non-building features that were lost in the first world versions."""
from pathlib import Path
import json, math
import numpy as np
from shapely.geometry import shape,Point,LineString,Polygon,box
from shapely.ops import transform,polygonize,unary_union
from shapely.strtree import STRtree
import mapbox_earcut

def pieces(g,kind):
    if g.is_empty:return
    if g.geom_type==kind:yield g
    elif hasattr(g,'geoms'):
        for p in g.geoms:yield from pieces(p,kind)

def tagnum(v,default,low,high):
    try:return float(np.clip(float(str(v).split()[0]),low,high))
    except (ValueError,TypeError):return default
def read_osm(raw):
    for e in raw.get('elements',[]):
        tags=e.get('tags',{})
        if e['type']=='node':yield tags,Point(e['lon'],e['lat'])
        elif e['type']=='way' and len(e.get('geometry',[]))>=2:
            coords=[(p['lon'],p['lat']) for p in e['geometry']]
            area=(tags.get('area')=='yes' or tags.get('landuse') or tags.get('amenity') in ('parking','grave_yard') or tags.get('leisure') or tags.get('building'))
            if area and len(coords)>=4 and coords[0]==coords[-1]:yield tags,Polygon(coords)
            else:yield tags,LineString(coords)
        elif e['type']=='relation':
            outer=[];inner=[]
            for m in e.get('members',[]):
                coords=[(p['lon'],p['lat']) for p in m.get('geometry',[]) if 'lon' in p]
                if len(coords)<2:continue
                (inner if m.get('role')=='inner' else outer).append(LineString(coords))
            if tags.get('type')=='multipolygon' and outer:
                a=unary_union(list(polygonize(unary_union(outer))))
                if inner:a=a.difference(unary_union(list(polygonize(unary_union(inner)))))
                if not a.is_empty:yield tags,a
            elif tags.get('historic') in ('citywalls','castle_wall') or tags.get('barrier')=='wall':
                for line in outer:yield tags,line

def compile_details(root,osm,project,bounds,ground,footprints,report):
    top=root/'data/raw/topography';clip=box(*bounds)
    walls=[];wall_geoms=[];paths=[];areas=[];graves=[];pois=[];ruins=[];stats={}
    building_tree=STRtree(footprints) if footprints else None
    def local(g):
        if g.is_empty:return g
        if not g.is_valid:g=g.buffer(0)
        return transform(project,g).intersection(clip)
    def xy(pt):return [round(float(pt[0]),3),round(float(pt[1]),3)]
    def code(p):
        try:return int(float(p.get('tyyp',p.get('TYYP',0))))
        except (ValueError,TypeError):return 0
    def record_wall(g,p,castle=False,kind='stone',source='osm'):
        for line in pieces(g,'LineString'):
            if line.length<.5:continue
            # Avoid walls already represented as LOD2 building polygons.
            if building_tree is not None and source=='etak' and len(building_tree.query(line.interpolate(.5,normalized=True),predicate='within')):continue
            h=tagnum(p.get('height',p.get('korgus_m')),4.5 if castle else 1.25 if kind=='stone' else 1.6,.3,16)
            width=tagnum(p.get('width'),1.5 if castle else .48 if kind=='stone' else .16,.12,5)
            if source=='etak' and wall_geoms:
                if any(q.distance(line.interpolate(.5,normalized=True))<1.25 for q in wall_geoms):continue
            walls.append(dict(points=[xy(pt) for pt in line.coords],height=h,width=width,kind=kind,source=source,castle=castle))
            if source=='osm':wall_geoms.append(line)
    def add_area(g,kind,name=''):
        for poly in pieces(g,'Polygon'):
            if poly.area<4:continue
            rings=[np.asarray(poly.exterior.coords)[:-1]]+[np.asarray(r.coords)[:-1] for r in poly.interiors]
            pts=np.ascontiguousarray(np.vstack(rings)[:,:2],dtype=np.float64)
            ids=mapbox_earcut.triangulate_float64(pts,np.cumsum([len(r) for r in rings],dtype=np.uint32))
            areas.append(dict(kind=kind,name=name,triangles=np.round(pts[ids],3).tolist()))
            if name and kind=='cemetery':
                q=poly.representative_point();pois.append(dict(name=name,kind=kind,x=q.x,z=q.y))
    # Existing OSM export plus additional detailed query. IDs/geometry deduplicated.
    items=[]
    for f in osm.get('features',[]):
        if f.get('geometry'):
            p=f.get('properties') or {};items.append(({**p,**(p.get('tags') or {})},shape(f['geometry'])))
    extra=top/'osm_details.json'
    if extra.exists():items.extend(read_osm(json.loads(extra.read_text(encoding='utf-8'))))
    seen=set();castle_center=None;cemetery_polys=[]
    for p,raw in items:
        try:g=local(raw)
        except Exception:continue
        if g.is_empty:continue
        key=(g.wkb,p.get('barrier'),p.get('historic'),p.get('highway'),p.get('landuse'),p.get('amenity'))
        if key in seen:continue
        seen.add(key)
        name=p.get('name','')
        historic=p.get('historic','');barrier=p.get('barrier','')
        if historic=='castle' and 'haapsalu' in name.lower():castle_center=g.representative_point()
        if barrier in ('wall','retaining_wall','fence') or historic in ('citywalls','castle_wall'):
            castle=historic in ('citywalls','castle_wall') or 'linnus' in name.lower()
            kind='fence' if barrier=='fence' else 'stone'
            record_wall(g.boundary if g.geom_type in ('Polygon','MultiPolygon') else g,p,castle,kind)
        if p.get('landuse')=='cemetery' or p.get('amenity')=='grave_yard':
            if g.geom_type in ('Polygon','MultiPolygon'):cemetery_polys.append(g);add_area(g,'cemetery',name or 'Kalmistu')
        if historic in ('grave','tomb') and g.geom_type=='Point':graves.append([g.x,g.y])
        if p.get('amenity')=='parking':add_area(g,'parking',name)
        if p.get('highway') in ('footway','path','pedestrian','cycleway','steps'):
            if g.geom_type in ('Polygon','MultiPolygon'):add_area(g,'paving',name)
            for line in pieces(g,'LineString'):
                if line.length<1:continue
                paths.append(dict(points=[xy(pt) for pt in line.coords],width=tagnum(p.get('width'),1.6,.6,5),surface=p.get('surface','')))
    # Castle center from the known public landmark, used solely to classify already mapped walls.
    if castle_center is None:
        castle_center=local(Point(23.5382,58.9474))
    for file in sorted(top.glob('E_*.geojson')):
        layer=file.stem;count=0
        obj=json.loads(file.read_text(encoding='utf-8'))
        for f in obj.get('features',[]):
            if not f.get('geometry'):continue
            p=f.get('properties') or {}
            try:g=local(shape(f['geometry']))
            except Exception:continue
            if g.is_empty:continue
            count+=1;t=code(p);name=p.get('nimi') or p.get('name') or ''
            if layer=='E_405_piire_j':
                if t not in (10,20,30,40):continue
                kind='fence' if t==10 else 'stone'
                castle=t in (20,30,40) and g.distance(castle_center)<190
                record_wall(g,p,castle,kind,'etak')
            elif layer=='E_301_muu_kolvik_ka' and t==30:
                if not any(g.intersection(q).area>g.area*.65 for q in cemetery_polys):
                    add_area(g,'cemetery',name or 'Kalmistu');cemetery_polys.append(g)
            elif layer=='E_301_muu_kolvik_p' and t==70 and g.geom_type=='Point':graves.append([g.x,g.y])
            elif layer=='E_501_tee_a' and t in (20,30,60):
                add_area(g,'paving' if t==60 else 'parking',name)
            elif layer=='E_401_hoone_ka' and t==40:
                # Ruins excluded from LOD2 may still exist in ETAK. Only extrude uncovered footprint.
                for poly in pieces(g,'Polygon'):
                    if building_tree is not None:
                        candidates=building_tree.query(poly,predicate='intersects')
                        if any(poly.intersection(footprints[int(i)]).area>poly.area*.2 for i in candidates):continue
                    h=tagnum(p.get('korgus_m'),3.5 if poly.distance(castle_center)<190 else 1.5,.5,18)
                    for ring in [poly.exterior,*poly.interiors]:
                        record_wall(LineString(ring.coords),{'height':h,'width':1.0},poly.distance(castle_center)<190,'stone','etak')
            elif layer in ('E_201_meri_a','E_202_seisuveekogu_a'):
                add_area(g,'sea' if layer=='E_201_meri_a' else 'water',name)
        stats[layer]=count
    graves=list({(round(x,2),round(z,2)) for x,z in graves})
    result=dict(walls=walls,paths=paths,areas=areas,graves=graves,pois=pois)
    report['topography']={'sources':stats,'walls':len(walls),'castleWallLines':sum(w['castle'] for w in walls),'cemeteryAreas':sum(a['kind']=='cemetery' for a in areas),'mappedGraves':len(graves),'paths':len(paths),'areas':len(areas)}
    print('Topography:',report['topography'],flush=True)
    return result
