"""Fetch missing mapped features from official WFS and OpenStreetMap.
No credentials; downloads are bounded to the existing Haapsalu world.
"""
from pathlib import Path
import argparse,json,urllib.request,urllib.parse,xml.etree.ElementTree as ET,time
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/raw/topography'
BBOX='23.49,58.92,23.59,58.96'
WFS='https://gsavalik.envir.ee/geoserver/etak/wfs'
LAYERS=['E_405_piire_j','E_301_muu_kolvik_ka','E_301_muu_kolvik_p','E_401_hoone_ka','E_403_muu_rajatis_ka','E_501_tee_a','E_201_meri_a','E_202_seisuveekogu_a']
def fetch(url,params=None,post=None):
    if params:url+='?'+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,data=post,headers={'User-Agent':'HaapsaluDrive/0.5 (bounded open geodata import)','Accept':'*/*'})
    with urllib.request.urlopen(req,timeout=100) as r:return r.read()
def save(name,obj):
    p=OUT/name;tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(obj,ensure_ascii=False),encoding='utf-8');tmp.replace(p)
def wfs():
    caps=ET.fromstring(fetch(WFS,{'service':'WFS','version':'1.0.0','request':'GetCapabilities'}))
    names=[n.text for e in caps.iter() if e.tag.split('}')[-1]=='FeatureType' for n in e if n.tag.split('}')[-1]=='Name' and n.text]
    results={}
    for layer in LAYERS:
        match=next((n for n in names if n.split(':')[-1].lower()==layer.lower()),None)
        if match is None:
            # Some service layer names omit the numeric prefix.
            suffix=layer.split('_',2)[-1].lower()
            options=[n for n in names if n.split(':')[-1].lower().endswith(suffix)]
            if len(options)==1:match=options[0]
        if not match:print('WFS layer not found:',layer,flush=True);continue
        try:
            obj=json.loads(fetch(WFS,{'service':'WFS','version':'1.0.0','request':'GetFeature','typeName':match,'bbox':BBOX+',EPSG:4326','srsName':'EPSG:4326','outputFormat':'application/json','maxFeatures':20000}))
            if obj.get('type')!='FeatureCollection':raise ValueError('Not GeoJSON')
            fs=obj['features']
            if len(fs)>=20000:raise ValueError('Feature cap reached; refine query')
            # GeoServer GeoJSON normally uses longitude/latitude.
            save(layer+'.geojson',obj);results[layer]=len(fs)
            print(layer, len(fs), 'features',flush=True)
        except Exception as e:print('Layer failed:',layer,str(e),flush=True)
    return results
def osm(extra=False):
    b='(58.92,23.49,58.96,23.59)'
    filters=['["barrier"]','["historic"]','["landuse"="cemetery"]','["amenity"="grave_yard"]','["amenity"="parking"]','["highway"~"^(footway|path|pedestrian|steps|cycleway)$"]','["leisure"~"^(park|garden|pitch)$"]','["man_made"="pier"]']
    if extra:filters=['["highway"~"^(bus_stop|crossing|stop|give_way|traffic_signals|street_lamp)$"]','["traffic_sign"]','["public_transport"]','["amenity"~"^(bench|shelter)$"]','["barrier"="bollard"]','["shop"]["name"]','["building"]["name"]','["highway"]["name"="Posti"]','["highway"]["name"="Posti tänav"]','["highway"]["name"="Posti tn"]']
    q='[out:json][timeout:90];('+''.join('nwr'+f+b+';' for f in filters)+');out geom;'
    for endpoint in ('https://overpass-api.de/api/interpreter','https://overpass.kumi.systems/api/interpreter'):
        try:
            obj=json.loads(fetch(endpoint,post=urllib.parse.urlencode({'data':q}).encode()))
            if 'elements' not in obj:raise ValueError('No elements')
            save('osm_street_objects.json' if extra else 'osm_details.json',obj);print('OSM street objects' if extra else 'OSM additional objects:',len(obj['elements']),flush=True);return len(obj['elements'])
        except Exception as e:print('OSM endpoint failed:',str(e),flush=True)
    return 0
def main():
    p=argparse.ArgumentParser();p.add_argument('--osm-only',action='store_true');p.add_argument('--street-only',action='store_true');args=p.parse_args();OUT.mkdir(parents=True,exist_ok=True)
    if args.street_only:
        from street_objects import fetch_stops
        try:fetch_stops(ROOT)
        except Exception as e:print('GTFS fetch failed:',e,flush=True)
        if not osm(extra=True):raise SystemExit('No new OSM street objects retrieved')
        return
    try:results={} if args.osm_only else wfs()
    except Exception as e:print('WFS discovery failed:',str(e),flush=True);results={}
    count=osm()
    save('source_report.json',{'etak':results,'osm':count,'source':'Maa- ja Ruumiamet WFS; OpenStreetMap contributors'})
    if not results and not count:raise SystemExit('No new data retrieved. Existing local files were preserved.')
if __name__=='__main__':main()
