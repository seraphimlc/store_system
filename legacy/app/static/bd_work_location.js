/* Browser coordinates stay on this page; one watcher is released when leaving. */
window.startWorkLocation=(map,text,onFirstPosition)=>{
  'use strict';
  const button=document.getElementById('work-location');
  const status=document.getElementById('work-location-status');
  let watcher=null,marker,accuracy,position,following=false,receivedPosition=false;
  const follow=value=>{following=value;button.setAttribute('aria-pressed',String(value));};
  const stop=()=>{if(watcher!==null)navigator.geolocation.clearWatch(watcher);watcher=null;};
  const start=()=>{
    if(!navigator.geolocation){status.textContent=text.unsupported;follow(false);return;}
    if(watcher!==null)return;
    status.textContent=text.loading;
    watcher=navigator.geolocation.watchPosition(result=>{
      const {latitude:lat,longitude:lng,accuracy:radius}=result.coords;
      if(!Number.isFinite(lat)||!Number.isFinite(lng))return;
      position={lat,lng};
      if(!receivedPosition){receivedPosition=true;onFirstPosition?.(position);}
      if(!marker)marker=new google.maps.Marker({map,title:text.mine,zIndex:1000,icon:{path:google.maps.SymbolPath.CIRCLE,scale:8,fillColor:'#1677ff',fillOpacity:1,strokeColor:'#fff',strokeWeight:3}});
      marker.setPosition(position);
      if(!accuracy)accuracy=new google.maps.Circle({map,strokeWeight:0,fillColor:'#1677ff',fillOpacity:.12,clickable:false});
      accuracy.setCenter(position);accuracy.setRadius(Number.isFinite(radius)?Math.max(0,radius):0);
      status.textContent='';
      if(following)map.panTo(position);
    },()=>{stop();follow(false);status.textContent=text.failed;},
    {enableHighAccuracy:true,timeout:15000,maximumAge:10000});
  };
  button.addEventListener('click',()=>{follow(!following);if(following){start();if(position)map.panTo(position);}});
  map.addListener('dragstart',()=>follow(false));
  window.addEventListener('pagehide',stop);
  window.addEventListener('pageshow',event=>{if(event.persisted)start();});
  follow(false);start();
};
