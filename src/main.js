import * as THREE from 'three';
import RAPIER from '@dimforge/rapier3d-compat';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {RoomEnvironment} from 'three/addons/environments/RoomEnvironment.js';

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
  renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.05;
  $('game').appendChild(renderer.domElement);
  const pmrem=new THREE.PMREMGenerator(renderer);
  const room=new RoomEnvironment();const env=pmrem.fromScene(room,.04);scene.environment=env.texture;room.dispose();pmrem.dispose();
  scene.add(new THREE.HemisphereLight(0xdceeff,0x77735c,2.0));
  const sun=new THREE.DirectionalLight(0xffead4,2.6);sun.castShadow=true;
  sun.shadow.mapSize.set(2048,2048);Object.assign(sun.shadow.camera,{left:-75,right:75,top:75,bottom:-75,near:1,far:300});
  sun.shadow.bias=-.00025;sun.shadow.normalBias=.12;scene.add(sun,sun.target);

  status('Creating terrain and collision mesh…');
  const texture=await new THREE.TextureLoader().loadAsync(root+'ground.jpg');texture.colorSpace=THREE.SRGBColorSpace;texture.anisotropy=Math.min(8,renderer.capabilities.getMaxAnisotropy());
  const terrainMat=new THREE.MeshStandardMaterial({map:texture,roughness:1});
  const groundChunks=[];
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
    groundChunks.push({p,idx,x:xmin+(col+w/2)*m.dx,z:zmin+(row+d/2)*m.dz,range:Math.hypot(w*m.dx,d*m.dz)/2,collider:null});
  }

  status('Loading real LOD2 building meshes…');
  const buildingMat=new THREE.MeshStandardMaterial({vertexColors:true,roughness:.9,side:THREE.DoubleSide});
  const buildingChunks=await Promise.all(m.buildings.map(async b=>{
    const a=new Float32Array(await (await data(b.file)).arrayBuffer());
    if(a.length!==b.vertices*6)throw Error('Invalid building data: '+b.file);
    const p=new Float32Array(b.vertices*3),c=new Float32Array(b.vertices*3),idx=new Uint32Array(b.vertices);
    for(let i=0;i<b.vertices;i++){p.set(a.subarray(i*6,i*6+3),i*3);c.set(a.subarray(i*6+3,i*6+6),i*3);idx[i]=i}
    const mesh=new THREE.Mesh(geom(p,null,null,c),buildingMat);mesh.receiveShadow=true;mesh.castShadow=true;scene.add(mesh);
    return {p,idx,x:b.center[0],z:b.center[1],range:400,collider:null,mesh};
  }));
  const allChunks=[...groundChunks,...buildingChunks];
  function updateColliders(x,z){
    for(const c of allChunks){
      const dist=Math.hypot(c.x-x,c.z-z)-c.range;
      if(!c.collider&&dist<350)c.collider=world.createCollider(RAPIER.ColliderDesc.trimesh(c.p,c.idx).setFriction(.95));
      else if(c.collider&&dist>550){world.removeCollider(c.collider,true);c.collider=null}
      if(c.mesh)c.mesh.castShadow=dist<150;
    }
  }
  updateColliders(m.spawn[0],m.spawn[2]);

  status('Generating roads from OpenStreetMap…');
  const roadPositions=[],linePositions=[];
  function strip(a,b,width,out,offset){
    const dx=b[0]-a[0],dz=b[1]-a[1],len=Math.hypot(dx,dz);if(len<.01)return;
    const ox=-dz/len*width/2,oz=dx/len*width/2;
    const count=Math.max(1,Math.ceil(len/2));
    function v(t,side){const x=a[0]+dx*t+side*ox,z=a[1]+dz*t+side*oz;return [x,ground(x,z)+offset,z]}
    for(let i=0;i<count;i++){const l=v(i/count,-1),r=v(i/count,1),l2=v((i+1)/count,-1),r2=v((i+1)/count,1);out.push(...l,...l2,...r,...r,...l2,...r2)}
  }
  for(const r of m.roads)for(let i=1;i<r.points.length;i++){
    strip(r.points[i-1],r.points[i],r.width,roadPositions,.055);
    if(['primary','secondary','tertiary','trunk'].includes(r.type)){
      const a=r.points[i-1],b=r.points[i],len=Math.hypot(b[0]-a[0],b[1]-a[1]);
      for(let t=0;t<len;t+=8){let q=Math.min(t+3,len);strip([lerp(a[0],b[0],t/len),lerp(a[1],b[1],t/len)],[lerp(a[0],b[0],q/len),lerp(a[1],b[1],q/len)],.10,linePositions,.08)}
    }
  }
  const road=new THREE.Mesh(geom(new Float32Array(roadPositions)),new THREE.MeshStandardMaterial({color:0x555758,roughness:1,transparent:true,opacity:.76,depthWrite:false,side:THREE.DoubleSide}));road.receiveShadow=true;road.renderOrder=1;scene.add(road);
  const markings=new THREE.Mesh(geom(new Float32Array(linePositions)),new THREE.MeshStandardMaterial({color:0xd9d7bd,roughness:1,side:THREE.DoubleSide}));markings.renderOrder=2;scene.add(markings);

  // Low-cost instanced placeholder vegetation, only on mapped forest/tree positions.
  const n=m.trees.length;
  if(n){
    const trunk=new THREE.InstancedMesh(new THREE.CylinderGeometry(.12,.22,1,6),new THREE.MeshStandardMaterial({color:0x6e5b44}),n);
    const crown=new THREE.InstancedMesh(new THREE.IcosahedronGeometry(1,1),new THREE.MeshStandardMaterial({color:0x527343,roughness:1}),n*2);
    const o=new THREE.Object3D();
    for(let i=0;i<n;i++){const [x,y,z,h]=m.trees[i];o.rotation.set(0,i*2.4,0);o.position.set(x,y+h*.26,z);o.scale.set(h*.17,h*.52,h*.17);o.updateMatrix();trunk.setMatrixAt(i,o.matrix);
      for(let j=0;j<2;j++){o.position.set(x+(j? h*.10:0),y+h*(j?.77:.63),z);o.scale.set(h*(j?.22:.28),h*.33,h*(j?.22:.28));o.updateMatrix();crown.setMatrixAt(i*2+j,o.matrix);crown.setColorAt(i*2+j,new THREE.Color().setHSL(.24+(i%7)*.008,.27+(i%5)*.04,.29+(i%4)*.025))}
    }trunk.instanceMatrix.needsUpdate=true;crown.instanceMatrix.needsUpdate=true;scene.add(trunk,crown);
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

  function reset(){
    const q=new THREE.Quaternion().setFromAxisAngle(AXIS,heading);
    body.setTranslation({x:sx,y:sy,z:sz},true);body.setRotation(q,true);
    body.setLinvel({x:0,y:0,z:0},true);body.setAngvel({x:0,y:0,z:0},true);
    body.resetForces(true);body.resetTorques(true);
    updateColliders(sx,sz);world.step();
  }
  reset();car.position.copy(body.translation());car.quaternion.copy(body.rotation());
  const keys=new Set();let paused=false,mode=0,steer=0,force=0,gear=1,rpm=850;
  const ratios=[0,4.23,2.52,1.66,1.22,1.0];
  const forward=new THREE.Vector3(),vel=new THREE.Vector3();
  addEventListener('keydown',e=>{
    const k=e.key.toLowerCase();
    if(['w','a','s','d','arrowup','arrowdown','arrowleft','arrowright',' ','r','c','escape'].includes(k))e.preventDefault();
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
  camera.position.copy(car.position).add(new THREE.Vector3(0,4,-8));
  const target=new THREE.Vector3(),eye=new THREE.Vector3(),look=new THREE.Vector3();
  let last=performance.now(),acc=0,frames=0,elapsed=0,fps=0,lastMap=0,colliderTick=0;
  $('loading').hidden=true;$('hud').hidden=false;
  renderer.setAnimationLoop(()=>{
    const now=performance.now(),dt=Math.min((now-last)/1000,.1);last=now;
    if(!paused){acc=Math.min(acc+dt,.15);while(acc>=DT){step();acc-=DT}}else acc=0;
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
    if(now-colliderTick>300){updateColliders(car.position.x,car.position.z);colliderTick=now}
    frames++;elapsed+=dt;if(elapsed>.6){fps=Math.round(frames/elapsed);frames=0;elapsed=0}
    $('speed').textContent=String(Math.round(speed*3.6)).padStart(3,'0');
    $('gear').textContent=gear===-1?'R':String(gear);
    $('rpm').style.setProperty('--rpm',Math.round(rpm/6500*100)+'%');
    $('detail').textContent=paused?'PAUSED — ESC TO RESUME':modelNote+' / '+Math.round(rpm)+' RPM / '+fps+' FPS';
    if(now-lastMap>120){mini();lastMap=now}
    renderer.render(scene,camera);
  });
  window.haapsalu={world,body,vehicle:vc,manifest:m,reset};
}
run().catch(e=>{console.error(e);status('Startup failed:\n'+(e.stack||e.message||e))});
