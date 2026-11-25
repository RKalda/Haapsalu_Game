// Street fixtures: real source coordinates, approximate pole/face geometry.
// No inferred sign is presented as surveyed traffic infrastructure.
export function addStreetObjects(scene,m,ground,THREE){
  const objects=m.streetObjects||[];
  const poleMat=new THREE.MeshStandardMaterial({color:0x8b9397,metalness:.45,roughness:.6});
  const dark=new THREE.MeshStandardMaterial({color:0x343a3a,roughness:.85});
  const glass=new THREE.MeshStandardMaterial({color:0x9eb6ba,metalness:.08,roughness:.15,transparent:true,opacity:.38,depthWrite:false});
  const wood=new THREE.MeshStandardMaterial({color:0x856c4e,roughness:.9});
  const poleGeo=new THREE.CylinderGeometry(.032,.044,1,8);
  const boxGeo=new THREE.BoxGeometry(1,1,1);
  const textureCache=new Map();
  function label(text,kind='label'){
    const key=kind+':'+text;if(textureCache.has(key))return textureCache.get(key);
    const c=document.createElement('canvas');c.width=512;c.height=kind==='street'?128:kind==='shop'?128:256;
    const x=c.getContext('2d');x.clearRect(0,0,c.width,c.height);
    if(kind==='bus'){
      x.fillStyle='#075da9';x.fillRect(86,4,340,248);x.fillStyle='white';x.fillRect(106,24,300,208);
      x.fillStyle='#16232b';x.fillRect(164,52,184,124);x.fillStyle='white';x.fillRect(179,65,154,49);
      x.fillStyle='#16232b';for(const a of [188,304]){x.beginPath();x.arc(a,185,18,0,Math.PI*2);x.fill()}
      x.fillStyle='white';x.fillRect(182,139,26,14);x.fillRect(304,139,26,14);
    }else if(kind==='stop'){
      x.translate(256,128);x.beginPath();for(let i=0;i<8;i++){let a=(i+.5)*Math.PI/4;x.lineTo(Math.cos(a)*119,Math.sin(a)*119)}x.closePath();x.fillStyle='#b51e25';x.fill();x.lineWidth=6;x.strokeStyle='white';x.stroke();
      x.fillStyle='white';x.font='bold 54px Arial';x.textAlign='center';x.textBaseline='middle';x.fillText('STOP',0,2);
    }else if(kind==='yield'){
      x.beginPath();x.moveTo(130,15);x.lineTo(382,15);x.lineTo(256,243);x.closePath();x.fillStyle='#c32029';x.fill();
      x.beginPath();x.moveTo(161,35);x.lineTo(351,35);x.lineTo(256,207);x.closePath();x.fillStyle='white';x.fill();
    }else if(kind==='crossing'){
      x.fillStyle='#1261a6';x.fillRect(130,0,252,256);x.fillStyle='white';x.beginPath();x.moveTo(256,17);x.lineTo(151,230);x.lineTo(361,230);x.closePath();x.fill();x.fillStyle='#202626';
      for(let i=0;i<5;i++)x.fillRect(190+i*28,204,19,15);
      x.beginPath();x.arc(259,85,13,0,Math.PI*2);x.fill();x.strokeStyle='#202626';x.lineWidth=12;x.lineCap='round';x.beginPath();x.moveTo(257,108);x.lineTo(248,145);x.lineTo(222,191);x.moveTo(248,145);x.lineTo(277,190);x.moveTo(257,117);x.lineTo(227,140);x.moveTo(257,117);x.lineTo(288,136);x.stroke();
    }else if(kind==='speed'||kind==='noentry'){
      x.fillStyle='#c5222a';x.beginPath();x.arc(256,128,120,0,Math.PI*2);x.fill();
      x.fillStyle='white';
      if(kind==='noentry')x.fillRect(170,107,172,42);
      else{x.beginPath();x.arc(256,128,98,0,Math.PI*2);x.fill();x.fillStyle='#202020';x.font='bold 100px Arial';x.textAlign='center';x.textBaseline='middle';x.fillText(text,256,132)}
    }else{
      x.fillStyle=kind==='shop'?'rgba(0,0,0,0)':kind==='street'?'#f0f0e8':'#f6f6f0';x.fillRect(0,0,c.width,c.height);
      if(kind!=='shop'){x.strokeStyle='#282f32';x.lineWidth=5;x.strokeRect(3,3,c.width-6,c.height-6)}
      x.fillStyle=kind==='shop'?'#f5f5f1':'#142127';x.font='bold '+(kind==='shop'?74:kind==='street'?64:47)+'px Arial';x.textAlign='center';x.textBaseline='middle';
      if(kind==='shop'){x.shadowColor='#151515';x.shadowBlur=5;x.shadowOffsetY=3}
      x.fillText(text,256,c.height/2,480);
    }
    const t=new THREE.CanvasTexture(c);t.colorSpace=THREE.SRGBColorSpace;t.anisotropy=4;textureCache.set(key,t);return t;
  }
  function mesh(g,geo,mat,x,y,z,sx=1,sy=1,sz=1){
    const o=new THREE.Mesh(geo,mat);o.position.set(x,y,z);o.scale.set(sx,sy,sz);g.add(o);return o;
  }
  function board(g,text,kind,x,y,z,w,h){
    const p=new THREE.Mesh(new THREE.PlaneGeometry(w,h),new THREE.MeshBasicMaterial({map:label(text,kind),transparent:true,alphaTest:.1,side:THREE.DoubleSide}));
    p.position.set(x,y,z);g.add(p);return p;
  }
  function roadSideYaw(o){
    const d=Number(o.direction);
    if(o.direction!==undefined&&o.direction!==''&&Number.isFinite(d))return d*Math.PI/180+Math.PI;
    return o.yaw||0;
  }
  function signKind(s){
    const codes=s.toLowerCase().split(/[;,]/).map(v=>v.trim().replace(/^ee:/,''));
    for(const c of codes){
      if(c==='stop'||c==='222')return ['stop',''];
      if(c==='give_way'||c==='221')return ['yield',''];
      if(c==='331')return ['noentry',''];
      if(c==='543'||c==='544')return ['crossing',''];
      const match=c.match(/^351\[(\d+)\]$/)||c.match(/^maxspeed[:=](\d+)$/);
      if(match)return ['speed',match[1]];
    }return null;
  }
  const cells=new Map();
  function cell(o){
    const k=Math.floor(o.x/128)+':'+Math.floor(o.z/128);
    if(!cells.has(k)){const g=new THREE.Group();g.userData.center=[Math.floor(o.x/128)*128+64,Math.floor(o.z/128)*128+64];scene.add(g);cells.set(k,g)}
    return cells.get(k);
  }
  for(const o of objects){
    const g=new THREE.Group();g.position.set(o.x,ground(o.x,o.z),o.z);g.rotation.y=roadSideYaw(o);
    if(o.kind==='bus'){
      mesh(g,poleGeo,poleMat,0,1.55,0,1,3.1,1);
      board(g,'','bus',0,2.72,.06,.9,.65);
      if(o.name)board(g,o.name,'label',0,2.12,.065,.94,.30);
    }else if(o.kind==='sign'){
      const k=signKind(o.name||'');if(!k)continue;
      mesh(g,poleGeo,poleMat,0,1.3,0,1,2.6,1);board(g,k[1],k[0],0,2.3,.06,.96,.60);
    }else if(o.kind==='crossing'){
      // A mapped crossing point need not have a zebra or sign. Mark only explicitly tagged zebra crossings.
      if(!['marked','zebra','uncontrolled'].includes(o.crossing))continue;
      const white=new THREE.MeshStandardMaterial({color:0xe4e4df,roughness:.85});
      // Signs only where mapped independently; zebra dimensions approximate.
      for(let i=-3;i<=3;i++)mesh(g,boxGeo,white,i*.74,.08,0,.45,.025,2.2);
    }else if(o.kind==='signal'){
      mesh(g,poleGeo,poleMat,0,1.7,0,1,3.4,1);
      mesh(g,boxGeo,dark,0,3.0,.10,.28,.78,.26);
      for(let i=0;i<3;i++){const c=[0xc22b22,0xe3b82c,0x349244][i];mesh(g,new THREE.SphereGeometry(.09,10,6),new THREE.MeshStandardMaterial({color:c,roughness:.55}),0,3.25-i*.25,.24,1,1,.45)}
    }else if(o.kind==='bollard'){
      mesh(g,poleGeo,dark,0,.45,0,2.4,.9,2.4);mesh(g,poleGeo,poleMat,0,.75,0,2.5,.09,2.5);
    }else if(o.kind==='bench'){
      for(const x of [-.63,.63])mesh(g,boxGeo,dark,x,.22,0,.08,.44,.42);
      mesh(g,boxGeo,wood,0,.48,0,1.55,.07,.42);mesh(g,boxGeo,wood,0,.79,-.20,1.55,.36,.06);
    }else if(o.kind==='shelter'){
      for(const x of [-1.4,1.4])for(const z of [-.65,.65])mesh(g,poleGeo,poleMat,x,1.15,z,1.4,2.3,1.4);
      mesh(g,boxGeo,glass,0,1.15,-.65,2.8,2.05,.035);mesh(g,boxGeo,glass,-1.4,1.15,0,.035,2.05,1.3);
      mesh(g,boxGeo,dark,0,2.37,0,3.05,.10,1.6);mesh(g,boxGeo,wood,0,.5,-.34,2,.08,.38);
    }else if(o.kind==='lamp'){
      mesh(g,poleGeo,poleMat,0,3.2,0,2,6.4,2);mesh(g,boxGeo,poleMat,.5,6.35,0,1,.07,.07);
      mesh(g,boxGeo,dark,1,6.3,0,.4,.12,.25);
    }else continue;
    cell(o).add(g);
  }
  // Street-name boards are generated at intersections from mapped names.
  // Their locations are approximate, not existing sign surveys.
  const nodes=new Map();
  for(const r of m.roads){
    if(!r.name)continue;
    for(const a of [r.points[0],r.points[r.points.length-1]]){
      const k=Math.round(a[0]/4)+':'+Math.round(a[1]/4);
      if(!nodes.has(k))nodes.set(k,{names:new Map(),point:a});
      const other=a===r.points[0]?r.points[1]:r.points[r.points.length-2];
      if(other)nodes.get(k).names.set(r.name,{yaw:Math.atan2(other[0]-a[0],other[1]-a[1]),width:r.width});
    }
  }
  for(const n of nodes.values()){
    if(n.names.size<2)continue;
    const first=[...n.names.values()][0],yaw=first.yaw,side=first.width/2+1.2;
    const x=n.point[0]+Math.cos(yaw)*side,z=n.point[1]-Math.sin(yaw)*side;
    const g=new THREE.Group();g.position.set(x,ground(x,z),z);mesh(g,poleGeo,poleMat,0,1.3,0,1,2.6,1);
    let i=0;for(const [name,v] of [...n.names.entries()].slice(0,2)){const p=board(g,name,'street',0,2.42-i++*.29,0,1.05,.24);p.rotation.y=v.yaw+Math.PI/2}
    cell({x,z}).add(g);
  }
  // Address-matched landmark lettering. Put on a single largest exterior candidate.
  const candidates=(m.landmarkWalls||[]).slice().sort((a,b)=>b.width*b.height-a.width*a.height);
  const selected=candidates[0];
  if(selected){
    const a=selected.bearing*Math.PI/180,nx=Math.sin(a),nz=-Math.cos(a),g=new THREE.Group();
    g.position.set(selected.center[0]+nx*.10,selected.bottom+selected.height*.72,selected.center[1]+nz*.10);
    g.rotation.y=Math.atan2(nx,nz);
    board(g,selected.name,'shop',0,0,0,Math.min(10,selected.width*.65),Math.min(1.4,selected.height*.25));scene.add(g);
  }
  console.info('Street fixtures:',objects.length,'source records; sign placement is approximate where not mapped');
}
