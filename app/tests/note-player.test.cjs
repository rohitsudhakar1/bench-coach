const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync('app/static/note-player.js','utf8');
function fixture() {
  const videos = [], handlers = {};
  const original = {muted:false, defaultMuted:false, autoplay:true, pause(){}, getAttribute(){return '/runs/demo/cam0.mp4';}, replaceWith(area){this.area=area;}};
  const document = {
    baseURI:'http://localhost:8885/note/demo?format=html',
    querySelectorAll(){return [original];},
    getElementById(id){return id === 'vids' ? {textContent:'["cam0.mp4","cam1.mp4"]'} : original;},
    createElement(tag){ const element = {style:{}, children:[], append(...items){this.children.push(...items);}, pause(){this.paused=true;}, readyState:1, duration:30}; if(tag==='video') videos.push(element); return element;},
    addEventListener(name,handler,capture){assert.equal(capture,true); handlers[name]=handler;}
  };
  vm.runInNewContext(code,{document,URL,Map,Number,JSON});
  const click = offsets => {const event={target:{closest(){return {dataset:{off:JSON.stringify(offsets)}};}},preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;}}; handlers.click(event); assert.ok(event.prevented && event.stopped);};
  return {videos,click,original};
}
test('seeks both muted cameras to their own offsets and stays paused',()=>{
  const {videos,click,original}=fixture();
  assert.equal(original.autoplay,false);
  click({'cam0.mp4':4.2,'cam1.mp4':5.8});
  assert.equal(videos[0].currentTime,4.2); assert.equal(videos[1].currentTime,5.8);
  for(const video of videos){assert.ok(video.muted && video.defaultMuted && video.paused);}
  assert.equal(videos[1].src,'http://localhost:8885/runs/demo/cam1.mp4');
});
test('delayed metadata uses latest click, clamps duration, skips unavailable video',()=>{
  const {videos,click}=fixture(); videos[0].readyState=0; videos[1].error={};
  click({'cam0.mp4':4,'cam1.mp4':5}); click({'cam0.mp4':80,'cam1.mp4':7});
  assert.equal(videos[0].currentTime,undefined); videos[0].onloadedmetadata();
  assert.equal(videos[0].currentTime,30); assert.equal(videos[1].currentTime,undefined);
});
