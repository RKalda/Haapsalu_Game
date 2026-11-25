"""Experimental, confidence-gated projection of the *downloaded* Fotoladu photographs.
Four ground corners are only initialization; match real orthophoto features and solve
a camera pose before using a single wall pixel. No network or full-resolution tile scraping.
"""
import json,math,re
from collections import Counter
import numpy as np
from PIL import Image
from shapely.geometry import Point,LineString
try:
    import cv2
except ImportError:
    cv2=None

def metadata_for(root):
    result={}
    folder=root/'data/raw/facades'
    files=list(folder.glob('fotoladu_*.json'))
    for f in files:
        # The exported gallery stores the exact viewer's pilt(...) parameters.
        txt=f.read_text(encoding='utf-8-sig')
        # JSON escapes quotes but numeric arguments and single quotes are unchanged.
        for match in re.finditer(r"pilt\('([0-9]+)','([^']+)','[^']*','[^']*','(\d+)',(\d+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),([^,]+),",txt):
            fields=match.groups()
            try:result[fields[0]]={'corners':np.array([float(v) for v in fields[6:14]]).reshape(4,2),'altitude':float(fields[14]),'name':fields[1]}
            except (ValueError,IndexError):continue
    return result

def match_pose(image, corners, altitude, rgb, bounds, ground, metadata, origin):
    ih,iw=image.shape[:2];xmin,zmin,xmax,zmax=bounds;oh,ow=rgb.shape[1:]
    # Work in the local game coordinate frame (north = -Z).
    pixels=np.column_stack(((corners[:,0]-xmin)/(xmax-xmin)*(ow-1),(corners[:,1]-zmin)/(zmax-zmin)*(oh-1)))
    margin=max(40,int(np.ptp(pixels[:,0])*.12),int(np.ptp(pixels[:,1])*.12));lo=np.maximum(np.floor(pixels.min(axis=0)).astype(int)-margin,0);hi=np.minimum(np.ceil(pixels.max(axis=0)).astype(int)+margin,[ow,oh])
    cw,ch=hi-lo
    if cw<70 or ch<70:return None,'footprint_outside'
    ortho=np.ascontiguousarray(np.moveaxis(rgb[:,lo[1]:hi[1],lo[0]:hi[0]],0,-1))
    target=cv2.cvtColor(ortho,cv2.COLOR_RGB2GRAY)
    src=cv2.cvtColor(image,cv2.COLOR_RGB2GRAY)
    clahe=cv2.createCLAHE(clipLimit=2.0,tileGridSize=(8,8))
    src=clahe.apply(src);target=clahe.apply(target)
    sift=cv2.SIFT_create(nfeatures=10000,contrastThreshold=.012)
    kp2,d2=sift.detectAndCompute(target,None)
    if d2 is None:return None,'no_ortho_features'
    photo=np.float32([[0,0],[iw-1,0],[iw-1,ih-1],[0,ih-1]])
    best=None
    max_good=0;max_inliers=0
    for direction in (pixels,pixels[::-1]):
        for shift in range(4):
            dst=np.float32(np.roll(direction,shift,axis=0)-lo)
            H=cv2.getPerspectiveTransform(photo,dst)
            warped=cv2.warpPerspective(src,H,(int(cw),int(ch)))
            mask=cv2.warpPerspective(np.full_like(src,255),H,(int(cw),int(ch)))
            mask=cv2.erode(mask,np.ones((9,9),np.uint8))
            kp1,d1=sift.detectAndCompute(warped,mask)
            if d1 is None or len(d1)<10:continue
            pairs=cv2.BFMatcher().knnMatch(d1,d2,k=2)
            good=[a for pair in pairs if len(pair)==2 for a,b in [pair] if a.distance<.78*b.distance]
            max_good=max(max_good,len(good))
            if len(good)<16:continue
            p1=np.float32([kp1[a.queryIdx].pt for a in good]);p2=np.float32([kp2[a.trainIdx].pt for a in good])
            correction,inlier=cv2.findHomography(p1,p2,cv2.RANSAC,2.5)
            if correction is None:continue
            keep=inlier.ravel().astype(bool);n=int(keep.sum())
            max_inliers=max(max_inliers,n)
            if n<16 or n/len(good)<.20:continue
            source=cv2.perspectiveTransform(p1[keep,None,:],np.linalg.inv(H))[:,0,:]
            targetxy=p2[keep]+lo
            if best is None or n>best[0]:best=(n,source,targetxy)
    if best is None:return None,f'no_geographic_alignment (matches={max_good}, inliers={max_inliers})'
    n,uv,xy=best
    wx=xmin+xy[:,0]/(ow-1)*(xmax-xmin);wz=zmin+xy[:,1]/(oh-1)*(zmax-zmin)
    keep=np.ones(len(wx),bool)
    # Exclude building footprints: a roof feature is not a ground control point.
    if metadata.tree is not None:
        cx,cy=origin
        for i,(x,z) in enumerate(zip(wx,wz)):
            if len(metadata.tree.query(Point(x+cx,cy-z),predicate='intersects')):keep[i]=False
    uv=uv[keep];wx=wx[keep];wz=wz[keep]
    if len(uv)<18:return None,'insufficient_ground_control'
    if np.ptp(uv[:,0])<iw*.18 or np.ptp(uv[:,1])<ih*.15:return None,'poor_control_spread'
    # ENU coordinates, centred close to the photo for numerical conditioning.
    center=np.array([np.mean(wx),np.mean(-wz),0.])
    world=np.column_stack((wx,-wz,[ground(x,z) for x,z in zip(wx,wz)]))-center
    world=np.ascontiguousarray(world,np.float64);uv=np.ascontiguousarray(uv,np.float64)
    best=None;cv2.setRNGSeed(831)
    for f in np.geomspace(iw*.55,iw*3.5,15):
        K=np.array([[f,0,(iw-1)/2],[0,f,(ih-1)/2],[0,0,1]],dtype=np.float64)
        try:
            ok,r,t,ins=cv2.solvePnPRansac(world,uv,K,None,iterationsCount=400,reprojectionError=4.,confidence=.999,flags=cv2.SOLVEPNP_EPNP)
            if not ok or ins is None or len(ins)<15:continue
            ids=ins.ravel();r,t=cv2.solvePnPRefineLM(world[ids],uv[ids],K,None,r,t)
            R=cv2.Rodrigues(r)[0];camera=(-R.T@t).ravel()+center
            # Recorded altitude is an independent physical sanity check, not ground truth.
            if camera[2]<altitude*.60 or camera[2]>altitude*1.4:continue
            pred=cv2.projectPoints(world[ids],r,t,K,None)[0][:,0,:]
            error=float(np.median(np.linalg.norm(pred-uv[ids],axis=1)))
            if error>2.7:continue
            score=len(ids)/(1+error*.5+abs(camera[2]-altitude)/max(altitude,1))
            if best is None or score>best[0]:best=(score,dict(K=K,R=R,t=t.ravel(),center=center,camera=camera,error=error,inliers=len(ids)))
        except cv2.error:continue
    if best is None:return None,'pose_rejected'
    return best[1],'accepted'

class AerialFacades:
    def __init__(self,root,photos,rgb,bounds,ground,metadata,origin,project,report):
        self.photos=photos;self.poses=[];self.report=report;self.stats=Counter();self.index=0;self.page_start=len(photos.atlases);self.metadata=metadata;self.origin=origin
        self.report['aerialPoseDiagnostics']=[]
        if cv2 is None:
            report['aerialPhotoStatus']='Install opencv-python to enable actual aerial facade projection';return
        data=metadata_for(root)
        for p in sorted((root/'data/raw/facades').glob('*.jpg')):
            match=re.search(r'ID(\d+)',p.name)
            if not match:continue
            if match[1] not in data:
                self.report['aerialPoseDiagnostics'].append({'id':match[1],'status':'missing_footprint_metadata'});continue
            m=data[match[1]]
            try:
                with Image.open(p) as im:image=np.array(im.convert('RGB'))
                x,z=project(m['corners'][:,0],m['corners'][:,1]);corners=np.column_stack((x,z))
                print('Aligning actual aerial photograph',match[1],flush=True)
                pose,status=match_pose(image,corners,m['altitude'],rgb,bounds,ground,metadata,origin)
                diagnostic={'id':match[1],'status':status}
                if pose:
                    pose['image']=image;pose['id']=match[1];self.poses.append(pose)
                    diagnostic.update(errorPx=round(pose['error'],2),groundInliers=pose['inliers'],cameraHeight=round(float(pose['camera'][2]),1))
                self.report['aerialPoseDiagnostics'].append(diagnostic);print(diagnostic,flush=True)
            except Exception as e:self.report['aerialPoseDiagnostics'].append({'id':match[1],'status':'error','message':str(e)})
        report['aerialCamerasAccepted']=len(self.poses)
    def project(self,p,points):
        q=np.column_stack((points[:,0],-points[:,2],points[:,1]))-p['center']
        cam=q@p['R'].T+p['t']
        if (cam[:,2]<=0).any():return None
        q=cam@p['K'].T
        return q[:,:2]/q[:,2,None]
    def apply(self,xyz,frame):
        if not self.poses or frame is None:return None
        bearing,width,height,u,y=frame
        if width<2 or height<2:return None
        normal=np.array([math.sin(math.radians(bearing)),-math.cos(math.radians(bearing))])
        tangent=np.array([normal[1],-normal[0]])
        center=xyz[:,[0,2]].mean(axis=0)
        line=float(center@normal)
        # top-left/top-right/bottom-right/bottom-left in wall coordinates.
        points=[]
        for uu,yy in ((u.min(),y.max()),(u.max(),y.max()),(u.max(),y.min()),(u.min(),y.min())):
            x,z=tangent*uu+normal*line;points.append((x,yy,z))
        points=np.asarray(points)
        candidates=[]
        for pose in self.poses:
            camera=pose['camera'];direction=np.array([camera[0],-camera[1]])-center
            horizontal=np.linalg.norm(direction)
            if horizontal<1 or np.dot(direction/horizontal,normal)<.55:continue
            # Reject an obstructed near-wall ray using known building footprints.
            if self.metadata.tree is not None:
                cx,cy=self.origin;start=center+normal*.6;end=start+direction/horizontal*min(10,horizontal*.04)
                ray=LineString([(start[0]+cx,cy-start[1]),(end[0]+cx,cy-end[1])])
                if len(self.metadata.tree.query(ray,predicate='intersects')):continue
            uv=self.project(pose,points)
            if uv is None:continue
            im=pose['image'];ih,iw=im.shape[:2]
            if (uv[:,0]<2).any() or (uv[:,0]>iw-3).any() or (uv[:,1]<2).any() or (uv[:,1]>ih-3).any():continue
            pxw=min(np.linalg.norm(uv[1]-uv[0]),np.linalg.norm(uv[2]-uv[3]))
            pxh=min(np.linalg.norm(uv[3]-uv[0]),np.linalg.norm(uv[2]-uv[1]))
            # If the uncertainty approaches the wall height, don't bake wrong pixels.
            if pxw<8 or pxh<max(5,pose['error']*3):continue
            score=pxw*pxh/(1+pose['error']**2)
            candidates.append((score,pose,uv))
        if not candidates:return None
        _,pose,uv=max(candidates,key=lambda a:a[0])
        dst=np.float32([[0,0],[123,0],[123,123],[0,123]])
        H=cv2.getPerspectiveTransform(np.float32(uv),dst)
        crop=cv2.warpPerspective(pose['image'],H,(124,124),flags=cv2.INTER_CUBIC)
        if crop.mean()<24 or crop.std()<3:return None
        page=self.page_start+self.index//256;cell=self.index%256
        if page==len(self.photos.atlases):self.photos.atlases.append(Image.new('RGB',(2048,2048),(128,128,128)))
        x=cell%16*128+2;v=cell//16*128+2
        patch=np.pad(crop,((2,2),(2,2),(0,0)),mode='edge')
        self.photos.atlases[page].paste(Image.fromarray(patch),(x-2,v-2))
        self.index+=1;self.stats[pose['id']]+=1
        coords=np.column_stack(((x+.5+(u-u.min())/np.ptp(u)*123)/2048,1-(v+.5+(1-(y-y.min())/np.ptp(y))*123)/2048))
        return 7+page,coords
    def finish(self):
        self.report['aerialPhotographicFaces']=self.index
        self.report['aerialPhotoFacesBySource']=dict(self.stats)
        print('Actual aerial photograph wall faces:',self.index,flush=True)
