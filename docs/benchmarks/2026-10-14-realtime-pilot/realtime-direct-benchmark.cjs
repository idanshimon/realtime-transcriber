const {execFileSync}=require('node:child_process');const fs=require('node:fs');const crypto=require('node:crypto');
const WS=require('/Users/idanshimon/projects/research/live-speech/node_modules/ws');
const root='/Users/idanshimon/.hermes/cache/scratch/mai2-meeting-benchmark';const out=root+'/realtime-direct';fs.mkdirSync(out,{recursive:true});
const sub='b3a032cf-f672-4071-b7c8-2bcbe087bbd0';const token=JSON.parse(execFileSync('az',['account','get-access-token','--subscription',sub,'--resource','https://cognitiveservices.azure.com/','-o','json'],{encoding:'utf8'})).accessToken;
const pcm=execFileSync('ffmpeg',['-v','error','-ss','300','-i','/Users/idanshimon/projects/ai/rtt/test.mp3','-t','30','-ar','24000','-ac','1','-f','s16le','pipe:1'],{maxBuffer:10000000});
const instructions='You are a transcription service, not a participant. Transcribe only the newly supplied audio, verbatim in English. Prefix each human turn with Speaker A, Speaker B, etc. Keep the same labels for the same voices throughout this session. Do not answer the speakers or summarize. Speaker labels are your best audio-based estimate. Do not invent names.';
const temps=JSON.parse(fs.readFileSync(root+'/realtime-temp-deployments.json'));const models=temps.filter(x=>x.created).map(x=>({model:x.model,deployment:x.deployment,endpoint:'https://rtt-transcribe-1783144256.cognitiveservices.azure.com',version:x.readback.properties.model.version}));models.push({model:'gpt-realtime-2.1',deployment:'gpt-realtime-2.1',endpoint:'https://caldova-ai.cognitiveservices.azure.com',version:'2026-07-07'});
fs.writeFileSync(out+'/fixture.json',JSON.stringify({audio_sha256:crypto.createHash('sha256').update(pcm).digest('hex'),instructions_sha256:crypto.createHash('sha256').update(instructions).digest('hex'),source_start_seconds:300,duration_seconds:pcm.length/48000,commit_seconds:15,models,reasoning:'provider default',output:'text',native_diarization:false},null,2));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
function speakerFor(text,needle){for(const line of text.split('\n'))if(new RegExp(needle,'i').test(line)){const m=line.match(/^\s*Speaker\s+([^:]+):/i);if(m)return m[1].trim();}return null;}
async function run(cfg,rep){
 const start=Date.now();let ws,finished=false,sessionReady=false,frames=0,current='',first=null,commitAt=[],outputs=[],events=[],error=null,allTimer;
 const t=()=>Math.round((Date.now()-start))/1000;
 let readyResolve;const ready=new Promise(r=>readyResolve=r);
 const result=()=>{let a=outputs[0]?.text||'',b=outputs[1]?.text||'';let frank1=speakerFor(a,'Frank Tomasello|solutions engineer|You probably'),frank2=speakerFor(b,'worked with John'),previous=speakerFor(a,'know me already|plugged in'),next=speakerFor(b,'Awesome');return {...cfg,rep,audio_seconds:30,elapsed_seconds:t(),completed_responses:outputs.length,error,outputs,checks:{frank_first:frank1,frank_second:frank2,frank_continuity:frank1!==null&&frank2!==null?frank1===frank2:null,preceding_speaker:previous,following_speaker:next,three_longer_voices_distinct:previous&&frank1&&next?new Set([previous,frank1,next]).size===3:null},events};};
 try{
  const session={type:'realtime',model:cfg.deployment,output_modalities:['text'],instructions,audio:{input:{format:{type:'audio/pcm',rate:24000},turn_detection:null}}};
  ws=new WS(cfg.endpoint.replace(/^https:/,'wss:')+'/openai/v1/realtime?model='+encodeURIComponent(cfg.deployment),{headers:{Authorization:'Bearer '+token},handshakeTimeout:45000});
  allTimer=setTimeout(()=>{error='Overall deadline';ws.terminate();readyResolve();},100000);
  ws.on('message',raw=>{const e=JSON.parse(raw);if(e.type==='session.created'){const {model,...update}=session;ws.send(JSON.stringify({type:'session.update',session:update}));}if(e.type==='session.updated'){sessionReady=true;readyResolve();}if(e.type==='error')error=JSON.stringify(e.error);
   if(['response.output_text.delta','response.text.delta'].includes(e.type)){if(first===null)first=t();current+=e.delta;}
   if(e.type==='response.done'){const index=outputs.length;outputs.push({text:current,first_text_after_commit_s:first===null?null:Number((first-commitAt[index]).toFixed(3)),done_after_commit_s:Number((t()-commitAt[index]).toFixed(3)),status:e.response?.status,usage:e.response?.usage||null});current='';first=null;}
   if(!e.type.includes('.delta'))events.push({seconds:t(),...e});
  });ws.on('error',e=>{error='WS '+e.message;readyResolve();});ws.on('close',(code,reason)=>{if(!finished&&outputs.length<2){error=error||'Premature close '+code+' '+reason;readyResolve();}});
  await ready;if(!sessionReady||error)throw Error(error||'Session never ready');
  for(let segment=0;segment<2;segment++){
   for(let pos=segment*720000;pos<(segment+1)*720000;pos+=4800){if(error)throw Error(error);ws.send(JSON.stringify({type:'input_audio_buffer.append',audio:pcm.subarray(pos,Math.min(pos+4800,(segment+1)*720000)).toString('base64')}));await sleep(100);}
   commitAt.push(t());ws.send(JSON.stringify({type:'input_audio_buffer.commit'}));ws.send(JSON.stringify({type:'response.create'}));
   const deadline=Date.now()+30000;while(outputs.length<segment+1&&!error&&Date.now()<deadline)await sleep(50);
   if(error)throw Error(error);if(outputs.length<segment+1)throw Error('Response deadline');
  }
 }catch(e){error=e.message;}finally{finished=true;clearTimeout(allTimer);ws?.terminate();}
 const r=result();fs.writeFileSync(out+'/'+cfg.model+'-rep'+rep+'.json',JSON.stringify(r,null,2));console.log(JSON.stringify({model:cfg.model,rep,completed:r.completed_responses,error:r.error,checks:r.checks,latencies:r.outputs.map(x=>({first:x.first_text_after_commit_s,done:x.done_after_commit_s})),text:r.outputs.map(x=>x.text)}));return r;
}
(async()=>{let index=0,results=[];async function worker(){while(index<models.length){const cfg=models[index++];for(let rep=1;rep<=3;rep++)results.push(await run(cfg,rep));}}await Promise.all([worker(),worker()]);fs.writeFileSync(out+'/all-results.json',JSON.stringify(results,null,2));console.log('FINISHED',results.length,'runs',models.length,'models');})().catch(e=>{console.error(e);process.exitCode=1;});