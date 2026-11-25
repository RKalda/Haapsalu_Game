"""Bake only explicitly matched and rights-cleared wall photos."""
import json,math
import numpy as np
from PIL import Image,ImageOps

def frame(xyz,center):
    q=xyz[:,[0,2]]
    d=q[np.argmax(np.linalg.norm(q-q[0],axis=1))]-q[0]
    l=np.linalg.norm(d)
    if l<.3:return None
    t=d/l;n=np.array([-t[1],t[0]])
    if np.dot(n,q.mean(axis=0)-center)<0:n=-n
    t=np.array([n[1],-n[0]])
    u=q@t;y=xyz[:,1];w=float(np.ptp(u));h=float(np.ptp(y))
    if w<.3 or h<.3:return None
    return math.degrees(math.atan2(n[0],-n[1]))%360,w,h,u,y

class PhotoFacades:
    def __init__(self,root,out):
        self.out=out;self.records={};self.atlases=[];self.catalog=[];self.used=0;self.refs=0
        ref=root/'data/raw/facades/heritage_local/catalog.json'
        self.heritage={str(r['etak_id']):r for r in json.loads(ref.read_text(encoding='utf-8'))} if ref.exists() else {}
        p=root/'data/raw/facades/verified.json'
        if not p.exists():return
        for i,item in enumerate(json.loads(p.read_text(encoding='utf-8-sig'))):
            if not item.get('license') or not item.get('source'):raise ValueError('Source and license required')
            photo=(p.parent/item['image']).resolve()
            if not photo.is_relative_to(p.parent.resolve()):raise ValueError('Invalid facade path')
            with Image.open(photo) as im:
                im=ImageOps.exif_transpose(im).convert('RGB')
                if 'quad' in item:
                    a,b,c,d=[tuple(map(float,v)) for v in item['quad']]
                    # Projective rectification: solve output-to-source homography.
                    A=[];B=[]
                    for (u,v),(xx,yy) in zip(((0,0),(499,0),(499,499),(0,499)),(a,b,c,d)):
                        A.extend([[u,v,1,0,0,0,-u*xx,-v*xx],[0,0,0,u,v,1,-u*yy,-v*yy]])
                        B.extend([xx,yy])
                    coeff=np.linalg.solve(np.asarray(A,dtype=float),np.asarray(B,dtype=float))
                    im=im.transform((500,500),Image.Transform.PERSPECTIVE,tuple(coeff),Image.Resampling.BICUBIC)
                elif not item.get('rectified'):raise ValueError('Provide rectified:true or quad')
                im=im.resize((500,500),Image.Resampling.LANCZOS)
            page=i//16;cell=i%16
            if page==len(self.atlases):self.atlases.append(Image.new('RGB',(2048,2048),(128,128,128)))
            x=cell%4*512+6;y=cell//4*512+6
            patch=np.pad(np.asarray(im),((6,6),(6,6),(0,0)),mode='edge')
            self.atlases[page].paste(Image.fromarray(patch),(x-6,y-6))
            self.records.setdefault(str(item['etak_id']),[]).append((float(item['bearing'])%360,page,x,y))
    def style(self,bid,family,wall):
        r=self.heritage.get(str(bid))
        if not r:return family,wall
        self.refs+=1;s=r.get('description','').lower()
        if any(t in s for t in ('puithoone','puitelamu','puitmaja','puitlaudis','laudisega')):family=1
        elif any(t in s for t in ('tellistest','tellisfassaad','tellishistoritsism')):family=2;wall=np.array([.70,.42,.32])
        elif any(t in s for t in ('krohvitud','krohvfassaad')):family=0
        return family,wall
    def select(self,bid,frames,x,z):
        if bid and frames:self.catalog.append(dict(etak_id=bid,center=[round(x,2),round(z,2)],walls=[[i,round(f[0],1),round(f[1],2),round(f[2],2)] for i,f in frames.items()]))
        picked={}
        for bearing,page,x,y in self.records.get(str(bid),[]):
            options=[]
            for i,(b,w,h,*_) in frames.items():
                diff=abs((b-bearing+180)%360-180)
                if diff<=25 and w>=2 and h>=2:options.append((w*h,-diff,i))
            if options:picked[max(options)[2]]=(page,x,y)
        return picked
    def uv(self,f,entry):
        _,_,_,u,y=f;page,x,v=entry
        uu=(u-u.min())/np.ptp(u);vv=(y-y.min())/np.ptp(y);self.used+=1
        return 7+page,np.column_stack(((x+.5+uu*499)/2048,1-(v+.5+(1-vv)*499)/2048))
    def save(self,report):
        files=[]
        for i,im in enumerate(self.atlases):
            name=f'actual_facades_{i}.jpg';im.save(self.out/name,quality=92);files.append(name)
        (self.out/'facade_wall_catalog.json').write_text(json.dumps(self.catalog),encoding='utf-8')
        report['heritageReferences']=self.refs;report['photographicFaces']=self.used
        return files
