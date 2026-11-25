import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';
import {Sky} from 'three/addons/objects/Sky.js';

const $=id=>document.getElementById(id);
const root=import.meta.env.BASE_URL+'world/';
const DT=1/60, AXIS=new THREE.Vector3(0,1,0);
const clamp=THREE.MathUtils.clamp, lerp=THREE.MathUtils.lerp;
const status=s=>{$('status').textContent=s;console.log(s)};
async function data(file){const r=await fetch(root+file);if(!r.ok)throw Error(file+' HTTP '+r.status);return r}
function geom(pos,idx,uv,col){
  const g=new THREE.BufferGeometry();
  g.setAttribute('position',new THREE.BufferAttribute(pos,3));
  if(idx)g.setIndex(new THREE.BufferAttribute(idx,1));
  if(uv)g.setAttribute('uv',new THREE.BufferAttribute(uv,2));
  if(col)g.setAttribute('color',new THREE.BufferAttribute(col,3));
  g.computeVertexNormals();g.computeBoundingSphere();return g;
}

async function run(){
  status('Loading map manifest…');
  let m;
  try{m=await (await data('manifest.json')).json()}catch(e){throw Error('World not compiled. Run python tools/build_world.py from the project folder. '+e.message)}
  if(m.version!==5)throw Error('Old world data. Re-run python tools/build_world.py.');
  const [xmin,zmin,xmax,zmax]=m.bounds;
  const H=new Float32Array(await (await data('height.dat')).arrayBuffer());
  if(H.length!==m.nx*m.nz)throw Error('Height grid size does not match manifest');
  function ground(x,z){
    let u=clamp((x-xmin)/m.dx,0,m.nx-1),v=clamp((z-zmin)/m.dz,0,m.nz-1);
    let i=Math.min(Math.floor(u),m.nx-2),j=Math.min(Math.floor(v),m.nz-2),a=u-i,b=v-j;
    let q=H[j*m.nx+i],r=H[j*m.nx+i+1],t=H[(j+1)*m.nx+i],k=H[(j+1)*m.nx+i+1];
    return a+b<=1?q+(r-q)*a+(t-q)*b:k+(t-k)*(1-a)+(r-k)*(1-b);
  }

  await RAPIER.init();
  const world=new RAPIER.World({x:0,y:-9.81,z:0});world.timestep=DT;
  const scene=new THREE.Scene();scene.background=new THREE.Color(0xb9d4e5);
  scene.fog=new THREE.Fog(0xb9d4e5,250,2200);
  const camera=new THREE.PerspectiveCamera(65,innerWidth/innerHeight,.12,3500);
  const renderer=new THREE.WebGLRenderer({antialias:true,powerPreference:'high-performance'});
  renderer.setSize(innerWidth,innerHeight);renderer.setPixelRatio(Math.min(devicePixelRatio,1.5));
  renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
  renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=.90;
  $('game').appendChild(renderer.domElement);
  const pmrem=new THREE.PMREMGenerator(renderer);
  const room=new RoomEnvironment();const env=pmrem.fromScene(room,.04);scene.environment=env.texture;scene.environmentIntensity=.5;room.dispose();pmrem.dispose();
  scene.add(new THREE.HemisphereLight(0xdceeff,0x706750,.85));
  const sky=new Sky();sky.scale.setScalar(3200);scene.add(sky);
  const skyU=sky.material.uniforms;
  skyU.turbidity.value=3.6;skyU.rayleigh.value=1.65;skyU.mieCoefficient.value=.006;skyU.mieDirectionalG.value=.82;
  skyU.sunPosition.value.set(-65,100,-40);
  const sun=new THREE.DirectionalLight(0xffead4,2.0);sun.castShadow=true;
  sun.shadow.mapSize.set(2048,2048);Object.assign(sun.shadow.camera,{left:-75,right:75,top:75,bottom:-75,near:1,far:300});
  sun.shadow.bias=-.00025;sun.shadow.normalBias=.045;scene.add(sun,sun.target);

  status('Creating terrain and collision mesh…');
  const texture=await new THREE.TextureLoader().loadAsync(root+'ground.jpg');texture.colorSpace=THREE.SRGBColorSpace;texture.anisotropy=Math.min(8,renderer.capabilities.getMaxAnisotropy());
  async function repeatTexture(name){
    const t=await new THREE.TextureLoader().loadAsync(root+name+'.jpg');t.colorSpace=THREE.SRGBColorSpace;
    t.wrapS=t.wrapT=THREE.RepeatWrapping;t.anisotropy=Math.min(8,renderer.capabilities.getMaxAnisotropy());return t;
  }
  const [grassTex,asphaltTex,pavingTex,...facadeTex]=await Promise.all(['grass','asphalt','paving','facade_plaster','facade_wood','facade_brick','roof','wall_plain','wood_plain','brick_plain'].map(repeatTexture));
  const terrainMat=new THREE.MeshStandardMaterial({map:texture,roughness:1});
  // Keep aerial geography but add fine-scale detail to green ground at street level.
  terrainMat.onBeforeCompile=s=>{
    s.uniforms.grassDetail={value:grassTex};
    s.vertexShader='varying vec3 groundPos;\n'+s.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\ngroundPos=position;');
    s.fragmentShader='uniform sampler2D grassDetail; varying vec3 groundPos;\n'+s.fragmentShader.replace('#include <map_fragment>',`
      #include <map_fragment>
      float grassMask=smoothstep(0.005,0.055,diffuseColor.g-max(diffuseColor.r,diffuseColor.b));
      vec3 detail=texture2D(grassDetail,groundPos.xz*.45).rgb;
      diffuseColor.rgb=mix(diffuseColor.rgb,diffuseColor.rgb*(.65+detail*.85),grassMask*.8);
    `);
  };
  const groundChunks=[];
  const aerialIndex=new Map((m.aerialTiles||[]).map(t=>[t.row+':'+t.col,t.file]));
  const imageLoader=new THREE.TextureLoader();let loadingTiles=0;
  function streamAerial(x,z){
    const nearest=groundChunks.filter(c=>c.tileFile).map(c=>[c,Math.hypot(c.x-x,c.z-z)]).sort((a,b)=>a[1]-b[1]);
    const wanted=new Set(nearest.filter(([,d])=>d<950).slice(0,12).map(([c])=>c));
    for(const [c] of nearest){
      if(!wanted.has(c)&&c.detail){c.detail.dispose();c.detail=null;c.mesh.material.dispose();c.mesh.material=terrainMat}
      c.wanted=wanted.has(c);
    }
    for(const [c] of nearest){
      if(!c.wanted||c.detail||c.pending||c.failed||loadingTiles>=3)continue;
      c.pending=true;loadingTiles++;
      imageLoader.load(root+c.tileFile,t=>{
        loadingTiles--;c.pending=false;
        if(!c.wanted){t.dispose();return}
        t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=Math.min(8,renderer.capabilities.getMaxAnisotropy());
        t.repeat.set(1/c.us,1/c.vs);t.offset.set(-c.u0/c.us,-c.v0/c.vs);
        const mat=terrainMat.clone();mat.map=t;mat.onBeforeCompile=terrainMat.onBeforeCompile;mat.needsUpdate=true;
        c.detail=t;c.mesh.material=mat;
      },undefined,e=>{loadingTiles--;c.pending=false;c.failed=true;console.warn('Aerial tile unavailable',c.tileFile,e)});
    }
  }
  for(let row=0;row<m.nz-1;row+=96)for(let col=0;col<m.nx-1;col+=96){
    let w=Math.min(96,m.nx-1-col)+1,d=Math.min(96,m.nz-1-row)+1;
    const p=new Float32Array(w*d*3),uv=new Float32Array(w*d*2),idx=new Uint32Array((w-1)*(d-1)*6);
    let t=0;
    for(let j=0;j<d;j++)for(let i=0;i<w;i++){
      let a=j*w+i,gi=i+col,gj=j+row;
      p.set([xmin+gi*m.dx,H[gj*m.nx+gi],zmin+gj*m.dz],a*3);
      uv.set([gi/(m.nx-1),1-gj/(m.nz-1)],a*2);
      if(i<w-1&&j<d-1){idx.set([a,a+w,a+1,a+1,a+w,a+w+1],t);t+=6}
    }
    const mesh=new THREE.Mesh(geom(p,idx,uv),terrainMat);mesh.receiveShadow=true;scene.add(mesh);
    groundChunks.push({p,idx,mesh,x:xmin+(col+(w-1)/2)*m.dx,z:zmin+(row+(d-1)/2)*m.dz,range:Math.hypot(w*m.dx,d*m.dz)/2,collider:null,
      tileFile:aerialIndex.get(row+':'+col),u0:col/(m.nx-1),v0:1-(row+d-1)/(m.nz-1),us:(w-1)/(m.nx-1),vs:(d-1)/(m.nz-1)});
  }

  status('Loading real LOD2 building meshes…');
  async function normalTexture(name){
    const t=await imageLoader.loadAsync(root+name+'_normal.png');t.wrapS=t.wrapT=THREE.RepeatWrapping;t.anisotropy=4;return t;
  }
  const normalMaps=await Promise.all(['facade_plaster','facade_wood','facade_brick','roof','wall_plain','wood_plain','brick_plain','asphalt','paving'].map(normalTexture));
  const buildingMats=facadeTex.map((map,i)=>new THREE.MeshStandardMaterial({map,normalMap:normalMaps[i],normalScale:new THREE.Vector2(.48,.48),vertexColors:true,roughness:.83,side:THREE.DoubleSide}));
  buildingMats[3].map=texture;buildingMats[3].normalMap=null;
  for(const file of m.facadeAtlases||[]){
    const t=await imageLoader.loadAsync(root+file);t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=4;
    buildingMats.push(new THREE.MeshStandardMaterial({map:t,roughness:.9,side:THREE.DoubleSide,vertexColors:true}));
  }
  const buildingChunks=await Promise.all(m.buildings.map(async b=>{
    const a=new Float32Array(await (await data(b.file)).arrayBuffer());
    if(a.length!==b.vertices*9)throw Error('Invalid building data: '+b.file);
    const p=new Float32Array(b.vertices*3),idx=new Uint32Array(b.vertices);
    const buckets=buildingMats.map(()=>({p:[],c:[],uv:[]}));
    const tint=new THREE.Color();
    for(let i=0;i<b.vertices;i++){
      const j=i*9;const q=a.subarray(j,j+3);p.set(q,i*3);idx[i]=i;
      const k=buckets[a[j+8]|0];k.p.push(...q);
      tint.setRGB(a[j+3],a[j+4],a[j+5],THREE.SRGBColorSpace);k.c.push(tint.r,tint.g,tint.b);k.uv.push(a[j+6],a[j+7]);
    }
    const mesh=new THREE.Group();
    buckets.forEach((k,i)=>{if(!k.p.length)return;const part=new THREE.Mesh(geom(new Float32Array(k.p),null,new Float32Array(k.uv),new Float32Array(k.c)),buildingMats[i]);part.receiveShadow=true;part.castShadow=true;mesh.add(part)});
    scene.add(mesh);
    return {p,idx,x:b.center[0],z:b.center[1],range:400,collider:null,mesh};
  }));
  const allChunks=[...groundChunks,...buildingChunks];
  function updateColliders(x,z){
    for(const c of allChunks){
      const dist=Math.hypot(c.x-x,c.z-z)-c.range;
      if(!c.collider&&dist<350)c.collider=world.createCollider(RAPIER.ColliderDesc.trimesh(c.p,c.idx).setFriction(.95));
      else if(c.collider&&dist>550){world.removeCollider(c.collider,true);c.collider=null}
      if(c.mesh?.isGroup)for(const part of c.mesh.children)part.castShadow=dist<150;
    }
  }
  updateColliders(m.spawn[0],m.spawn[2]);streamAerial(m.spawn[0],m.spawn[2]);

  status('Generating roads from OpenStreetMap…');
  const roadPositions=[],linePositions=[],pavingPositions=[],cobblePositions=[],pathPositions=[];
  const [cobbleTex,masonryTex,cobbleNormal,masonryNormal]=await Promise.all([repeatTexture('cobble'),repeatTexture('masonry'),normalTexture('cobble'),normalTexture('masonry')]);
  function strip(a,b,width,out,offset,lateral=0){
    const dx=b[0]-a[0],dz=b[1]-a[1],len=Math.hypot(dx,dz);if(len<.01)return;
    const ox=-dz/len,oz=dx/len;
    const count=Math.max(1,Math.ceil(len/.85)),across=Math.max(1,Math.ceil(width/.85));
    function v(t,side){const k=(side-.5)*width+lateral,x=a[0]+dx*t+k*ox,z=a[1]+dz*t+k*oz;return [x,ground(x,z)+offset,z]}
    for(let i=0;i<count;i++)for(let j=0;j<across;j++){
      const l=v(i/count,j/across),r=v(i/count,(j+1)/across),l2=v((i+1)/count,j/across),r2=v((i+1)/count,(j+1)/across);
      out.push(...l,...l2,...r,...r,...l2,...r2)
    }
  }
  // Rounded joints close the holes between independent road strips.
  function cap(point,radius,out,offset){
    const [x,z]=point;const center=[x,ground(x,z)+offset,z];
    for(let i=0;i<12;i++){
      const at=a=>{const xx=x+Math.cos(a)*radius,zz=z+Math.sin(a)*radius;return [xx,ground(xx,zz)+offset,zz]};
      out.push(...center,...at((i+1)*Math.PI/6),...at(i*Math.PI/6));
    }
  }
  for(const r of m.roads)for(let i=1;i<r.points.length;i++){
    const a=r.points[i-1],b=r.points[i],len=Math.hypot(b[0]-a[0],b[1]-a[1]);
    if(len<.01)continue;
    const cobble=['paving_stones','sett','cobblestone'].includes(r.surface);
    strip(a,b,r.width,cobble?cobblePositions:roadPositions,.17);
    if(i>1)cap(a,r.width/2,cobble?cobblePositions:roadPositions,.17);
    if(!cobble&&r.marking!=="no"&&r.width>=5.5&&!['track','service','living_street'].includes(r.type)){
      for(let t=0;t<len;t+=8){let q=Math.min(t+3,len);strip([lerp(a[0],b[0],t/len),lerp(a[1],b[1],t/len)],[lerp(a[0],b[0],q/len),lerp(a[1],b[1],q/len)],.10,linePositions,.19)}
    }
    // Approximate paved shoulders only on larger roads, not every residential street.
    if(['primary','secondary','tertiary'].includes(r.type)){
      strip(a,b,1.2,pavingPositions,.12,r.width/2+.6);
      strip(a,b,1.2,pavingPositions,.12,-r.width/2-.6);
    }
  }
  function roadMesh(vertices,map,color,offset,scale=3,normal=null){
    if(!vertices.length)return null;
    const p=new Float32Array(vertices),uv=new Float32Array(p.length/3*2);
    for(let i=0;i<p.length/3;i++){uv[i*2]=p[i*3]/scale;uv[i*2+1]=p[i*3+2]/scale}
    const mesh=new THREE.Mesh(geom(p,null,uv),new THREE.MeshStandardMaterial({map,color,normalMap:normal|| (map===asphaltTex?normalMaps[7]:map===pavingTex?normalMaps[8]:null),normalScale:new THREE.Vector2(.65,.65),roughness:1,side:THREE.DoubleSide,polygonOffset:true,polygonOffsetFactor:offset,polygonOffsetUnits:offset}));
    mesh.receiveShadow=true;scene.add(mesh);return mesh;
  }
  roadMesh(roadPositions,asphaltTex,0xffffff,-2);
  roadMesh(pavingPositions,pavingTex,0xc4c1b6,-1);
  roadMesh(linePositions,null,0xe3e1ce,-4);
  roadMesh(cobblePositions,cobbleTex,0xc6c6c6,-3,2.0,cobbleNormal);
  const details=m.details||{walls:[],paths:[],areas:[],graves:[],pois:[]};
  for(const path of details.paths||[]){
    for(let i=1;i<path.points.length;i++)strip(path.points[i-1],path.points[i],path.width,pathPositions,.095);
  }
  roadMesh(pathPositions,pavingTex,0xc5c3b8,-1,2.2);
  // Land-use polygons are clipped to actual GIS outlines. Subdivide so they follow the DTM.
  function drapeTriangle(a,b,c,out,offset,depth=0){
    const edges=[Math.hypot(a[0]-b[0],a[1]-b[1]),Math.hypot(b[0]-c[0],b[1]-c[1]),Math.hypot(c[0]-a[0],c[1]-a[1])];
    const max=Math.max(...edges);
    if(max>6&&depth<10){
      const mid=(p,q)=>[(p[0]+q[0])/2,(p[1]+q[1])/2];
      if(edges[0]===max){const d=mid(a,b);drapeTriangle(a,d,c,out,offset,depth+1);drapeTriangle(d,b,c,out,offset,depth+1)}
      else if(edges[1]===max){const d=mid(b,c);drapeTriangle(a,b,d,out,offset,depth+1);drapeTriangle(a,d,c,out,offset,depth+1)}
      else{const d=mid(c,a);drapeTriangle(a,b,d,out,offset,depth+1);drapeTriangle(d,b,c,out,offset,depth+1)}
      return;
    }
    for(const [x,z] of [a,b,c])out.push(x,ground(x,z)+offset,z);
  }
  const surfaces={parking:[],paving:[],cemetery:[]};
  const sea=[],inland=[];
  for(const area of details.areas||[]){
    const t=area.triangles;
    if(area.kind==='sea'||area.kind==='water'){
      const arr=area.kind==='sea'?sea:inland;
      const y=area.kind==='sea'?.06:Math.min(...t.map(p=>ground(p[0],p[1])))+.06;
      for(const p of t)arr.push(p[0],y,p[1]);
    }else if(surfaces[area.kind])for(let i=0;i<t.length;i+=3)drapeTriangle(t[i],t[i+1],t[i+2],surfaces[area.kind],area.kind==='cemetery'?.035:.075);
  }
  roadMesh(surfaces.parking,asphaltTex,0xc5c5c5,-1);
  roadMesh(surfaces.paving,pavingTex,0xd0cec4,-1);
  // Do not erase real grave/vegetation detail from the aerial image with a flat grass overlay.
  // Retain cemetery boundaries for the map and render only mapped paths, fences and grave markers.
  const waterSurfaceMat=new THREE.MeshStandardMaterial({color:0x426e7d,roughness:.36,metalness:.15,side:THREE.DoubleSide});
  for(const p of [sea,inland])if(p.length){const mesh=new THREE.Mesh(geom(new Float32Array(p)),waterSurfaceMat);scene.add(mesh);}
  const wallCells=new Map(),wallMat=new THREE.MeshStandardMaterial({map:masonryTex,normalMap:masonryNormal,normalScale:new THREE.Vector2(.65,.65),roughness:.96,side:THREE.DoubleSide});
  const fenceMat=new THREE.MeshStandardMaterial({color:0x51534e,roughness:.9});
  for(const wall of details.walls||[]){
    for(let i=1;i<wall.points.length;i++){
      const a=wall.points[i-1],b=wall.points[i],dx=b[0]-a[0],dz=b[1]-a[1],len=Math.hypot(dx,dz);
      if(len<.2)continue;
      const count=Math.max(1,Math.ceil(len/4));
      for(let j=0;j<count;j++){
        const x0=lerp(a[0],b[0],j/count),z0=lerp(a[1],b[1],j/count),x1=lerp(a[0],b[0],(j+1)/count),z1=lerp(a[1],b[1],(j+1)/count);
        const x=(x0+x1)/2,z=(z0+z1)/2,key=Math.floor(x/128)+':'+Math.floor(z/128);
        if(!wallCells.has(key))wallCells.set(key,{x:Math.floor(x/128)*128+64,z:Math.floor(z/128)*128+64,stone:[],stoneUV:[],fence:[],fenceUV:[]});
        const cell=wallCells.get(key),f=wall.kind==='fence',out=f?cell.fence:cell.stone,uv=f?cell.fenceUV:cell.stoneUV;
        const ox=-dz/len*wall.width/2,oz=dx/len*wall.width/2;
        const pts=[[x0-ox,z0-oz],[x1-ox,z1-oz],[x1+ox,z1+oz],[x0+ox,z0+oz]];
        const bottom=pts.map(([x,z])=>[x,ground(x,z)-.08,z]);
        const top=bottom.map(p=>[p[0],p[1]+wall.height+.08,p[2]]);
        function face(p,q,r,s,w,h){out.push(...p,...q,...r,...p,...r,...s);uv.push(0,0,w,0,w,h,0,0,w,h,0,h)}
        if(f){ // simple metal/wood fence proxy, not a solid masonry wall
          const h=wall.height;
          for(const level of [.2,.8]){
            const v=bottom.map(p=>[p[0],p[1]+h*level,p[2]]);
            const u=v.map(p=>[p[0],p[1]+.07,p[2]]);
            face(v[0],v[1],u[1],u[0],len/count,.07);
          }
          for(const k of [0,1]){const p=bottom[k],q=bottom[(k+1)%4];const u=[p[0]+dx/len*.07,p[1],p[2]+dz/len*.07];face(p,u,[u[0],u[1]+h,u[2]],top[k],.07,h)}
        }else{
          for(let k=0;k<4;k++){const n=(k+1)%4;face(bottom[k],bottom[n],top[n],top[k],Math.hypot(bottom[n][0]-bottom[k][0],bottom[n][2]-bottom[k][2])/1.6,wall.height/1.6)}
          face(top[0],top[1],top[2],top[3],len/count/1.6,wall.width/1.6);
        }
      }
    }
  }
  for(const cell of wallCells.values()){
    const group=new THREE.Group();scene.add(group);
    for(const [p,uv,mat] of [[cell.stone,cell.stoneUV,wallMat],[cell.fence,cell.fenceUV,fenceMat]]){
      if(!p.length)continue;const arr=new Float32Array(p),idx=new Uint32Array(arr.length/3);for(let i=0;i<idx.length;i++)idx[i]=i;
      const mesh=new THREE.Mesh(geom(arr,null,new Float32Array(uv)),mat);mesh.castShadow=true;mesh.receiveShadow=true;group.add(mesh);
      if(mat===wallMat)allChunks.push({p:arr,idx,x:cell.x,z:cell.z,range:95,collider:null,mesh:group});
    }
  }
  updateColliders(m.spawn[0],m.spawn[2]);
  // Real mapped grave/monument points only; no invented rows of graves.
  if(details.graves?.length){
    const graveMesh=new THREE.InstancedMesh(new THREE.BoxGeometry(.45,.75,.16),new THREE.MeshStandardMaterial({color:0x6d6b66,roughness:.96}),details.graves.length);
    const ob=new THREE.Object3D();details.graves.forEach(([x,z],i)=>{ob.position.set(x,ground(x,z)+.375,z);ob.updateMatrix();graveMesh.setMatrixAt(i,ob.matrix)});graveMesh.castShadow=true;scene.add(graveMesh);
  }


  status('Planting measured trees and rendering water…');
  // Alpha-tested botanical foliage cards. Each cell is culled separately.
  const leafMaterials=await Promise.all(Array.from({length:6},(_,i)=>imageLoader.loadAsync(root+'foliage_'+Math.floor(i/3)+'_'+i%3+'.png').then(t=>{
    t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=4;
    return new THREE.MeshStandardMaterial({map:t,alphaTest:.45,side:THREE.DoubleSide,roughness:1,depthWrite:true});
  })));
  const treeCells=new Map(),treeTile=128;
  for(const tree of m.trees){
    const [x,y,z,h,d=h*.55,pine=0]=tree;
    const cx=Math.floor(x/treeTile),cz=Math.floor(z/treeTile),key=cx+':'+cz;
    if(!treeCells.has(key))treeCells.set(key,{x:(cx+.5)*treeTile,z:(cz+.5)*treeTile,trees:[]});
    treeCells.get(key).trees.push([x,y,z,h,d,pine]);
  }
  const plane=new THREE.PlaneGeometry(1,1),dummy=new THREE.Object3D(),color=new THREE.Color();
  // Use an actual game-ready pine prototype only when the pack contains an
  // identifiable single LOD group with a bounded geometry budget.
  let pineProto=null;
  if(m.pineAsset){
    try{
      const model=(await new GLTFLoader().loadAsync(root+'pine.glb')).scene;
      model.updateMatrixWorld(true);
      const candidates=[];
      model.traverse(o=>{
        if(!/lod[ _.-]*[01]($|[^0-9])/i.test(o.name||''))return;
        const meshes=[];let tris=0;
        o.traverse(p=>{if(p.isMesh&&p.geometry&&!Array.isArray(p.material)&&!p.isSkinnedMesh){meshes.push(p);tris+=(p.geometry.index?.count||p.geometry.attributes.position.count)/3}});
        if(!meshes.length||meshes.length>7||tris>11000||tris<30)return;
        const box=new THREE.Box3().setFromObject(o),s=box.getSize(new THREE.Vector3());
        if(s.y<=0||s.x<=0||s.z<=0||s.y<Math.max(s.x,s.z)*.8)return;
        candidates.push({meshes,tris,box,s});
      });
      candidates.sort((a,b)=>a.tris-b.tris);
      const p=candidates[0];
      if(p){
        const center=p.box.getCenter(new THREE.Vector3());
        const norm=new THREE.Matrix4().makeScale(1/Math.max(p.s.x,p.s.z),1/p.s.y,1/Math.max(p.s.x,p.s.z)).multiply(new THREE.Matrix4().makeTranslation(-center.x,-p.box.min.y,-center.z));
        pineProto=p.meshes.map(o=>({geo:o.geometry,mat:o.material,transform:norm.clone().multiply(o.matrixWorld)}));
        console.log('Using supplied pine model:',p.tris,'triangles');
      }else console.warn('Pine GLB has no identifiable low-cost single LOD; using foliage cards');
    }catch(e){console.warn('Pine GLB unavailable, using foliage cards',e)}
  }
  const cellMeshes=[];
  for(const cell of treeCells.values()){
    const groups=Array.from({length:6},()=>[]);
    for(const t of cell.trees){const key=(t[5]?3:0)+Math.abs(Math.floor(t[0]*13+t[2]*17))%3;groups[key].push(t)}
    const near=new THREE.Group(),far=new THREE.Group();
    groups.forEach((list,i)=>{
      if(!list.length)return;
      for(const [group,cardCount] of [[near,3],[far,2]]){
        // Closest conifers use actual model geometry; remaining trees use cards.
        const modelTrees=group===near&&i>=3&&pineProto?list.slice(0,Math.min(18,list.length)):[];
        if(modelTrees.length){
          const matrix=new THREE.Matrix4();
          for(const part of pineProto){
            const inst=new THREE.InstancedMesh(part.geo,part.mat,modelTrees.length);
            modelTrees.forEach(([x,y,z,h,d],j)=>{
              dummy.position.set(x,y,z);dummy.rotation.set(0,(x*.137+z*.753)%Math.PI,0);dummy.scale.set(d,h,d);dummy.updateMatrix();
              matrix.multiplyMatrices(dummy.matrix,part.transform);inst.setMatrixAt(j,matrix);
            });
            inst.instanceMatrix.needsUpdate=true;inst.castShadow=true;inst.receiveShadow=true;inst.computeBoundingSphere();group.add(inst);
          }
        }
        const cardTrees=list.slice(modelTrees.length);
        if(!cardTrees.length)continue;
        const mesh=new THREE.InstancedMesh(plane,leafMaterials[i],cardTrees.length*cardCount);
        let j=0;
        for(const [x,y,z,h,d] of cardTrees){
          const turn=(x*1.163+z*.753)%Math.PI;
          for(let k=0;k<cardCount;k++){
            dummy.position.set(x,y+h*.5,z);dummy.scale.set(Math.max(d,.8)*1.08,h,1);
            dummy.rotation.set(0,turn+k*Math.PI/cardCount,0);dummy.updateMatrix();
            mesh.setMatrixAt(j,dummy.matrix);color.setScalar(.83+Math.abs(Math.sin(x*23+z*17))*.17);mesh.setColorAt(j++,color);
          }
        }
        mesh.instanceMatrix.needsUpdate=true;mesh.castShadow=group===near;mesh.receiveShadow=true;mesh.computeBoundingSphere();group.add(mesh);
      }
    });
    near.visible=false;far.visible=false;scene.add(near,far);cellMeshes.push({...cell,trees:undefined,near,far});
  }
  function updateVegetation(x,z){
    for(const c of cellMeshes){const d=Math.hypot(c.x-x,c.z-z);
      c.near.visible=d<230;c.far.visible=d>=230&&d<1450;
    }
  }
  updateVegetation(m.spawn[0],m.spawn[2]);
  // Render only mapped water polygons; no arbitrary sea plane over low-lying land.
  let waterMat=null;
  if(m.water?.length){
    const p=new Float32Array(m.water.flat()),uv=new Float32Array(p.length/3*2);
    for(let i=0;i<p.length/3;i++){uv[i*2]=p[3*i]/12;uv[i*2+1]=p[3*i+2]/12}
    waterMat=new THREE.MeshStandardMaterial({color:0x397a89,metalness:.28,roughness:.23,side:THREE.DoubleSide,transparent:true,opacity:.92});
    waterMat.onBeforeCompile=s=>{
      s.uniforms.waterTime={value:0};waterMat.userData.time=s.uniforms.waterTime;
      s.vertexShader='varying vec3 waterPos;\n'+s.vertexShader.replace('#include <begin_vertex>','#include <begin_vertex>\nwaterPos=position;');
      s.fragmentShader='uniform float waterTime; varying vec3 waterPos;\n'+s.fragmentShader.replace('#include <normal_fragment_maps>',`
        #include <normal_fragment_maps>
        normal=normalize(normal+vec3(sin(waterPos.x*1.2+waterPos.z*.45+waterTime*.7)*.045,sin(waterPos.z*1.3-waterTime*.5)*.025,cos(waterPos.z*.8+waterTime*.6)*.045));
      `);
    };
    const water=new THREE.Mesh(geom(p,null,uv),waterMat);scene.add(water);
  }

  // Roadside lighting is intentionally approximate, limited to major mapped streets.
  const lampPositions=[],lampGrid=new Set();
  for(const r of m.roads){
    if(!['primary','secondary','tertiary'].includes(r.type))continue;
    let gap=15;
    for(let i=1;i<r.points.length;i++){
      const a=r.points[i-1],b=r.points[i],dx=b[0]-a[0],dz=b[1]-a[1],l=Math.hypot(dx,dz);
      if(l<.01)continue;
      while(gap<l){
        const x=a[0]+dx*gap/l-dz/l*(r.width/2+1.8),z=a[1]+dz*gap/l+dx/l*(r.width/2+1.8);
        const key=Math.round(x/22)+':'+Math.round(z/22);
        if(!lampGrid.has(key)){lampGrid.add(key);lampPositions.push([x,ground(x,z),z,Math.atan2(dx,dz)])}
        gap+=35;
      }gap-=l;
    }
  }
  if(lampPositions.length){
    const poleMat=new THREE.MeshStandardMaterial({color:0x727a7e,metalness:.65,roughness:.48});
    const bulbMat=new THREE.MeshStandardMaterial({color:0xdce2d7,roughness:.3});
    const poles=new THREE.InstancedMesh(new THREE.CylinderGeometry(.045,.085,6.7,8),poleMat,lampPositions.length);
    const arms=new THREE.InstancedMesh(new THREE.BoxGeometry(.055,.055,1.4),poleMat,lampPositions.length);
    const lights=new THREE.InstancedMesh(new THREE.BoxGeometry(.26,.12,.55),bulbMat,lampPositions.length);
    lampPositions.forEach(([x,y,z,yaw],i)=>{
      dummy.scale.setScalar(1);dummy.rotation.set(0,0,0);dummy.position.set(x,y+3.35,z);dummy.updateMatrix();poles.setMatrixAt(i,dummy.matrix);
      dummy.rotation.y=yaw+Math.PI/2;dummy.position.set(x-Math.cos(yaw)*.65,y+6.65,z+Math.sin(yaw)*.65);dummy.updateMatrix();arms.setMatrixAt(i,dummy.matrix);
      dummy.position.set(x-Math.cos(yaw)*1.3,y+6.60,z+Math.sin(yaw)*1.3);dummy.updateMatrix();lights.setMatrixAt(i,dummy.matrix);
    });scene.add(poles,arms,lights);
  }

  status('Loading BMW and vehicle physics…');
  const [sx,sy,sz,heading]=m.spawn;
  const car=new THREE.Group();scene.add(car);
  const body=world.createRigidBody(RAPIER.RigidBodyDesc.dynamic().setTranslation(sx,sy,sz).setCanSleep(false).setCcdEnabled(true).setLinearDamping(.12).setAngularDamping(.7));
  world.createCollider(RAPIER.ColliderDesc.cuboid(.82,.30,1.98).setTranslation(0,.08,0).setMass(1400).setFriction(.5),body);
  const vc=world.createVehicleController(body);
  vc.indexUpAxis=1;vc.setIndexForwardAxis=2;
  const wheelPoints=[[-.78,.03,1.35],[.78,.03,1.35],[-.78,.03,-1.35],[.78,.03,-1.35]];
  const wheels=[];
  const wheelGeo=new THREE.CylinderGeometry(.31,.31,.22,20);wheelGeo.rotateZ(Math.PI/2);
  for(let i=0;i<4;i++){
    const [x,y,z]=wheelPoints[i];vc.addWheel({x,y,z},{x:0,y:-1,z:0},{x:-1,y:0,z:0},.30,.31);
    vc.setWheelSuspensionStiffness(i,36);vc.setWheelSuspensionCompression(i,4.4);vc.setWheelSuspensionRelaxation(i,5);
    vc.setWheelMaxSuspensionForce(i,9500);vc.setWheelMaxSuspensionTravel(i,.24);
    vc.setWheelFrictionSlip(i,2.4);vc.setWheelSideFrictionStiffness(i,1.0);
    const pivot=new THREE.Group();pivot.position.set(x,y,z);
    const wheel=new THREE.Mesh(wheelGeo,new THREE.MeshStandardMaterial({color:0x212326,roughness:.86}));pivot.add(wheel);car.add(pivot);wheels.push({pivot,wheel});pivot.visible=false;
  }
  let modelNote='BMW E36';
  try{
    const gltf=await new GLTFLoader().loadAsync(root+'car.glb');
    const model=gltf.scene;const wrapper=new THREE.Group();wrapper.add(model);
    let box=new THREE.Box3().setFromObject(model),size=box.getSize(new THREE.Vector3());
    if(size.x>size.z)model.rotation.y=Math.PI/2;
    if(new URLSearchParams(location.search).has('flipCar'))model.rotation.y+=Math.PI;
    model.updateMatrixWorld(true);box.setFromObject(model);size=box.getSize(new THREE.Vector3());
    if(size.z<=0||!Number.isFinite(size.z))throw Error('Invalid model bounds');
    const s=4.43/size.z,center=box.getCenter(new THREE.Vector3());
    wrapper.scale.setScalar(s);wrapper.position.set(-center.x*s,-box.min.y*s-.55,-center.z*s);
    model.traverse(o=>{if(o.isMesh){o.castShadow=true;o.receiveShadow=true}});
    car.add(wrapper);
    // Use separate animated wheels only if explicitly named wheel roots can be identified.
    // Until the model hierarchy is inspected, preserve the author's baked wheel geometry.
  }catch(e){
    console.warn('Car GLB could not load, using physical debug car',e);modelNote='DEBUG CAR (GLB LOAD FAILED)';
    const shell=new THREE.Mesh(new THREE.BoxGeometry(1.72,.62,4.43),new THREE.MeshStandardMaterial({color:0x37596c,metalness:.45,roughness:.35}));shell.castShadow=true;car.add(shell);
    const roof=new THREE.Mesh(new THREE.BoxGeometry(1.38,.52,1.85),new THREE.MeshStandardMaterial({color:0x263e4a}));roof.position.set(0,.55,-.2);car.add(roof);
    for(const w of wheels)w.pivot.visible=true;
  }

  function reset(x=sx,z=sz,yaw=heading){
    const q=new THREE.Quaternion().setFromAxisAngle(AXIS,yaw);
    body.setTranslation({x,y:ground(x,z)+1.0,z},true);body.setRotation(q,true);
    body.setLinvel({x:0,y:0,z:0},true);body.setAngvel({x:0,y:0,z:0},true);
    body.resetForces(true);body.resetTorques(true);
    updateColliders(x,z);world.step();
  }
  reset();car.position.copy(body.translation());car.quaternion.copy(body.rotation());
  const keys=new Set();let paused=false,mapOpen=false,mode=0,steer=0,force=0,gear=1,rpm=850;
  const ratios=[0,4.23,2.52,1.66,1.22,1.0];
  const forward=new THREE.Vector3(),vel=new THREE.Vector3();
  addEventListener('keydown',e=>{
    const k=e.key.toLowerCase();
    if(e.target instanceof HTMLInputElement){if(k==='escape'){e.preventDefault();toggleMap(false)}return}
    if(['w','a','s','d','arrowup','arrowdown','arrowleft','arrowright',' ','r','c','m','escape'].includes(k))e.preventDefault();
    if(!e.repeat&&k==='m'){toggleMap(!mapOpen);return}
    if(mapOpen){if(k==='escape')toggleMap(false);return}
    if(!e.repeat){if(k==='r'){reset();force=steer=0}if(k==='c')mode=(mode+1)%3;if(k==='escape'){paused=!paused;keys.clear()}}
    if(!paused)keys.add(k)
  });
  addEventListener('keyup',e=>keys.delete(e.key.toLowerCase()));addEventListener('blur',()=>keys.clear());
  addEventListener('resize',()=>{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight)});
  const down=(...names)=>names.some(n=>keys.has(n));
  let speed=0;
  function step(){
    const p=body.translation();const q=body.rotation();
    forward.set(0,0,1).applyQuaternion(q);vel.copy(body.linvel());
    const longitudinal=vel.dot(forward);speed=Math.hypot(vel.x,vel.z);
    const up=down('w','arrowup'),back=down('s','arrowdown'),hand=down(' ');
    const service=(up&&longitudinal<-.8)||(back&&longitudinal>.8);
    const dir=service?0:up?1:back?-1:0;
    if(dir<0)gear=-1;else if(dir>0||longitudinal>1){
      if(gear<1)gear=1;
      if(gear<5&&speed*3.6>[0,48,82,122,165][gear])gear++;
      if(gear>1&&speed*3.6<[0,0,32,60,95,135][gear])gear--;
    }
    const target=dir===1?Math.min(4200,280*(ratios[Math.max(1,gear)]||1)*2.93*.83/.31/2)*Math.max(.12,1-speed/65):dir===-1?-1800:0;
    force=lerp(force,target,.10);
    const steering=(down('a','arrowleft')?1:0)-(down('d','arrowright')?1:0);
    steer=lerp(steer,steering*lerp(.59,.17,clamp(speed/38,0,1)),.16);
    for(let i=0;i<4;i++){
      vc.setWheelEngineForce(i,i>=2&&!hand&&!service?force:0);
      vc.setWheelSteering(i,i<2?steer:0);
      vc.setWheelBrake(i,service?115:hand&&i>=2?95:dir===0?1.6:0);
      vc.setWheelFrictionSlip(i,hand&&i>=2?.8:2.4);
      vc.setWheelSideFrictionStiffness(i,hand&&i>=2?.32:1.0);
    }
    vc.updateVehicle(DT,RAPIER.QueryFilterFlags.EXCLUDE_DYNAMIC);
    world.step();
    const t=body.translation();
    if(t.y<ground(t.x,t.z)-8||t.x<xmin+2||t.x>xmax-2||t.z<zmin+2||t.z>zmax-2)reset();
    const driveRatio=gear===-1?3.4:ratios[gear]||1;
    rpm=lerp(rpm,clamp(speed/.31*60/(Math.PI*2)*driveRatio*2.93,850,6500),.12);
  }

  const canvas=$('map'),ctx=canvas.getContext('2d');
  const mapScale=.34;
  function mini(){
    const p=car.position;ctx.clearRect(0,0,220,220);ctx.save();ctx.translate(110,110);
    ctx.fillStyle='#16282d';ctx.fillRect(-110,-110,220,220);
    ctx.strokeStyle='#657775';ctx.lineWidth=1.4;
    for(const r of m.roads){
      if(!r.points.some(a=>Math.abs(a[0]-p.x)<430&&Math.abs(a[1]-p.z)<430))continue;
      ctx.beginPath();r.points.forEach((a,i)=>{const x=(a[0]-p.x)*mapScale,z=(a[1]-p.z)*mapScale;i?ctx.lineTo(x,z):ctx.moveTo(x,z)});ctx.stroke();
    }
    forward.set(0,0,1).applyQuaternion(car.quaternion);ctx.rotate(Math.atan2(-forward.x,forward.z));
    ctx.fillStyle='#ffc878';ctx.beginPath();ctx.moveTo(0,9);ctx.lineTo(-5,-7);ctx.lineTo(0,-3);ctx.lineTo(5,-7);ctx.closePath();ctx.fill();ctx.restore();
    ctx.fillStyle='#fff';ctx.font='11px Arial';ctx.fillText('N',106,15);
  }
  // Searchable full map, using exactly the same road geometry as the game.
  const full=$('fullmap'),mc=$('worldmap'),mx=mc.getContext('2d');
  const named=new Map();const segments=[];
  for(const r of m.roads){
    const allowed=new Set(r.teleport||[]);
    for(let i=0;i<r.points.length-1;i++){
      const a=r.points[i],b=r.points[i+1],dx=b[0]-a[0],dz=b[1]-a[1],l2=dx*dx+dz*dz;
      if(l2<1)continue;
      const seg={a,b,dx,dz,l2,r,allowed:allowed.has(i)};segments.push(seg);
      if(r.name&&seg.allowed){const old=named.get(r.name);if(!old||old.l2<l2)named.set(r.name,seg)}
    }
  }
  function nearest(x,z,teleport=false){
    let best=null,d=Infinity;
    for(const s of segments){
      if(teleport&&!s.allowed)continue;
      const t=clamp(((x-s.a[0])*s.dx+(z-s.a[1])*s.dz)/s.l2,0,1);
      const qx=s.a[0]+t*s.dx,qz=s.a[1]+t*s.dz,dist=(qx-x)**2+(qz-z)**2;
      if(dist<d){d=dist;best={x:qx,z:qz,yaw:Math.atan2(s.dx,s.dz),name:s.r.name||s.r.ref||'Unnamed road',distance:Math.sqrt(d)}}
    }return best;
  }
  function updateStreet(){
    const s=nearest(car.position.x,car.position.z);
    $('street').textContent=s&&s.distance<25?s.name:'Off road';
  }
  let mapX=0,mapZ=0,zoom=.15,selected=null,drag=null,showAerial=false;
  function fit(){mapX=(xmin+xmax)/2;mapZ=(zmin+zmax)/2;zoom=Math.min(mc.clientWidth/(xmax-xmin),mc.clientHeight/(zmax-zmin))*.92}
  function drawMap(){
    if(!mapOpen)return;
    const w=mc.clientWidth,h=mc.clientHeight;if(w<1||h<1)return;
    const ratio=Math.min(devicePixelRatio,2);
    if(mc.width!==Math.round(w*ratio)||mc.height!==Math.round(h*ratio)){mc.width=Math.round(w*ratio);mc.height=Math.round(h*ratio)}
    mx.setTransform(ratio,0,0,ratio,0,0);mx.fillStyle='#10282e';mx.fillRect(0,0,w,h);
    const px=x=>w/2+(x-mapX)*zoom,pz=z=>h/2+(z-mapZ)*zoom;
    if(showAerial&&texture.image)mx.drawImage(texture.image,px(xmin),pz(zmin),(xmax-xmin)*zoom,(zmax-zmin)*zoom);
    else{mx.fillStyle='#21383a';mx.fillRect(px(xmin),pz(zmin),(xmax-xmin)*zoom,(zmax-zmin)*zoom)}
    for(const area of m.details?.areas||[]){
      if(area.kind!=='cemetery')continue;mx.fillStyle='#46634c';const t=area.triangles;for(let i=0;i<t.length;i+=3){mx.beginPath();mx.moveTo(px(t[i][0]),pz(t[i][1]));mx.lineTo(px(t[i+1][0]),pz(t[i+1][1]));mx.lineTo(px(t[i+2][0]),pz(t[i+2][1]));mx.closePath();mx.fill()}
    }
    for(const r of m.roads){
      mx.beginPath();r.points.forEach((a,i)=>i?mx.lineTo(px(a[0]),pz(a[1])):mx.moveTo(px(a[0]),pz(a[1])));
      mx.lineWidth=Math.max(1,r.width*zoom);mx.lineCap='round';mx.lineJoin='round';mx.strokeStyle=r.width>=7?'#e2b877':'#a1b4b4';mx.stroke();
    }
    mx.font='12px Arial';mx.textAlign='center';mx.textBaseline='middle';
    const labels=[];
    for(const [name,s] of named){
      if(zoom<.14&&s.r.width<7)continue;
      const x=px((s.a[0]+s.b[0])/2),y=pz((s.a[1]+s.b[1])/2),width=mx.measureText(name).width+8;
      if(x<0||x>w||y<0||y>h||labels.some(a=>Math.abs(a[0]-x)<(a[2]+width)/2&&Math.abs(a[1]-y)<17))continue;
      labels.push([x,y,width]);mx.lineWidth=3;mx.strokeStyle='#132326';mx.strokeText(name,x,y);mx.fillStyle='#fff';mx.fillText(name,x,y);
    }
    function pin(x,z,color,r){mx.fillStyle=color;mx.strokeStyle='#101b20';mx.lineWidth=2;mx.beginPath();mx.arc(px(x),pz(z),r,0,Math.PI*2);mx.fill();mx.stroke()}
    pin(car.position.x,car.position.z,'#69d5ff',6);if(selected)pin(selected.x,selected.z,'#ffbd68',8);
    mx.textAlign='left';mx.fillStyle='#fff';mx.fillText(Math.round(100/zoom)+' m',16,h-16);
    mx.fillRect(16,h-34,100,2);
  }
  function select(s){
    selected=s;$('destination').textContent=s?s.name:'No valid road found';
    $('teleport').disabled=!s;
    if(s)mapX=s.x,mapZ=s.z;
    drawMap();
  }
  function toggleMap(open){
    mapOpen=open;keys.clear();full.hidden=!open;
    if(open){selected=null;$('destination').textContent='Select a road or search a street';$('teleport').disabled=true;fit();drawMap()}
    else{$('streetsearch').blur()}
  }
  $('openmap').onclick=()=>toggleMap(true);$('map').onclick=()=>toggleMap(true);$('closemap').onclick=()=>toggleMap(false);
  $('fitmap').onclick=()=>{fit();drawMap()};$('aerialmap').onclick=()=>{showAerial=!showAerial;drawMap()};
  $('centerme').onclick=()=>{mapX=car.position.x;mapZ=car.position.z;zoom=.8;drawMap()};
  const search=$('streetsearch'),results=$('streetresults');
  function searchStreets(){
    results.replaceChildren();const q=search.value.trim().toLocaleLowerCase('et');if(!q)return;
    for(const [name,s] of [...named].sort((a,b)=>a[0].localeCompare(b[0],'et')).filter(([name])=>name.toLocaleLowerCase('et').includes(q)).slice(0,12)){
      const button=document.createElement('button');button.textContent=name;button.onclick=()=>{select(nearest((s.a[0]+s.b[0])/2,(s.a[1]+s.b[1])/2,true));zoom=Math.max(zoom,.7);drawMap();results.replaceChildren();search.value=name};results.appendChild(button)
    }
  }
  search.addEventListener('input',searchStreets);
  function zoomAt(f,x=mc.clientWidth/2,y=mc.clientHeight/2){
    const a=mapX+(x-mc.clientWidth/2)/zoom,b=mapZ+(y-mc.clientHeight/2)/zoom;
    zoom=clamp(zoom*f,.05,5);mapX=a-(x-mc.clientWidth/2)/zoom;mapZ=b-(y-mc.clientHeight/2)/zoom;drawMap();
  }
  $('zoomin').onclick=()=>zoomAt(1.5);$('zoomout').onclick=()=>zoomAt(1/1.5);
  mc.addEventListener('wheel',e=>{e.preventDefault();const r=mc.getBoundingClientRect();zoomAt(Math.exp(-e.deltaY*.0015),e.clientX-r.left,e.clientY-r.top)},{passive:false});
  mc.addEventListener('pointerdown',e=>{mc.setPointerCapture(e.pointerId);drag={x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY}});
  mc.addEventListener('pointermove',e=>{if(!drag)return;mapX-=(e.clientX-drag.x)/zoom;mapZ-=(e.clientY-drag.y)/zoom;drag.x=e.clientX;drag.y=e.clientY;drawMap()});
  mc.addEventListener('pointerup',e=>{if(!drag)return;if(Math.hypot(e.clientX-drag.startX,e.clientY-drag.startY)<5){
    const r=mc.getBoundingClientRect(),s=nearest(mapX+(e.clientX-r.left-r.width/2)/zoom,mapZ+(e.clientY-r.top-r.height/2)/zoom,true);
    select(s&&s.distance<Math.max(50,18/zoom)?s:null);
  }drag=null});mc.addEventListener('pointercancel',()=>drag=null);
  $('teleport').onclick=()=>{
    if(!selected)return;reset(selected.x,selected.z,selected.yaw);force=steer=speed=0;gear=1;rpm=850;car.position.copy(body.translation());car.quaternion.copy(body.rotation());
    camera.position.copy(car.position).add(new THREE.Vector3(0,4,-8).applyAxisAngle(AXIS,selected.yaw));
    paused=false;toggleMap(false);
  };
  camera.position.copy(car.position).add(new THREE.Vector3(0,4,-8));
  const target=new THREE.Vector3(),eye=new THREE.Vector3(),look=new THREE.Vector3();
  let last=performance.now(),acc=0,frames=0,elapsed=0,fps=0,lastMap=0,colliderTick=0;
  $('loading').hidden=true;$('hud').hidden=false;
  renderer.setAnimationLoop(()=>{
    const now=performance.now(),dt=Math.min((now-last)/1000,.1);last=now;
    if(!paused&&!mapOpen){acc=Math.min(acc+dt,.15);while(acc>=DT){step();acc-=DT}}else acc=0;
    car.position.copy(body.translation());car.quaternion.copy(body.rotation());
    for(let i=0;i<4;i++){
      const {pivot,wheel}=wheels[i];
      pivot.position.y=wheelPoints[i][1]-(vc.wheelSuspensionLength(i)??.3);pivot.rotation.y=vc.wheelSteering(i)??0;
      wheel.rotation.x=-(vc.wheelRotation(i)??0)
    }
    forward.set(0,0,1).applyQuaternion(car.quaternion);forward.y=0;forward.normalize();
    if(forward.lengthSq()<.1)forward.set(0,0,1);
    look.copy(car.position).addScaledVector(forward,mode===1?25:3);look.y+=.7;
    eye.copy(car.position);
    if(mode===0){eye.addScaledVector(forward,-(7+speed*.07));eye.y+=3.0+speed*.025}
    if(mode===1){eye.addScaledVector(forward,.9);eye.y+=1.0}
    if(mode===2){eye.addScaledVector(forward,-12);eye.y+=21}
    eye.y=Math.max(eye.y,ground(eye.x,eye.z)+1.1);
    if(mode!==1){
      const d=eye.clone().sub(look),l=d.length();
      if(l>0.1){
        const hit=world.castRay(new RAPIER.Ray(look,d.normalize()),l,true,RAPIER.QueryFilterFlags.EXCLUDE_DYNAMIC);
        if(hit)eye.copy(look).addScaledVector(d,Math.max(.7,hit.timeOfImpact-.4));
      }
    }
    if(mode===1)camera.position.copy(eye);else camera.position.lerp(eye,1-Math.exp(-dt*7));
    camera.lookAt(look);
    camera.fov=lerp(camera.fov,mode===1?75:65+Math.min(12,speed*.23),.06);camera.updateProjectionMatrix();
    car.visible=mode!==1;
    sun.position.copy(car.position).add(new THREE.Vector3(-65,100,-40));sun.target.position.copy(car.position);
    if(now-colliderTick>300){updateColliders(car.position.x,car.position.z);streamAerial(car.position.x,car.position.z);updateVegetation(car.position.x,car.position.z);colliderTick=now}
    frames++;elapsed+=dt;if(elapsed>.6){fps=Math.round(frames/elapsed);frames=0;elapsed=0}
    $('speed').textContent=String(Math.round(speed*3.6)).padStart(3,'0');
    $('gear').textContent=gear===-1?'R':String(gear);
    $('rpm').style.setProperty('--rpm',Math.round(rpm/6500*100)+'%');
    $('detail').textContent=paused?'PAUSED — ESC TO RESUME':modelNote+' / '+Math.round(rpm)+' RPM / '+fps+' FPS';
    if(now-lastMap>120){mini();updateStreet();lastMap=now}
    sky.position.copy(camera.position);if(waterMat?.userData.time)waterMat.userData.time.value=now/1000;
    renderer.render(scene,camera);
  });
  window.haapsalu={world,body,vehicle:vc,manifest:m,reset};
}
run().catch(e=>{console.error(e);status('Startup failed:\n'+(e.stack||e.message||e))});
