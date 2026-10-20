(() => {
'use strict';
const data=JSON.parse(document.getElementById('comparison-data').textContent);
const $=id=>document.getElementById(id);
const cmp=(a,b)=>typeof a==='number'&&typeof b==='number'?a-b:String(a).localeCompare(String(b),undefined,{numeric:true});
const ranked=[...Object.values(data.models)].filter(x=>x.score!==null);
function ordered(rows,state,value){if(!state)return [...rows];return [...rows].sort((a,b)=>{const x=value(a,state.key),y=value(b,state.key);if(x===null||x===undefined)return y===null||y===undefined?0:1;if(y===null||y===undefined)return -1;return cmp(x,y)*(state.dir==='asc'?1:-1);});}
function headers(table,keys,click){[...table.tHead.rows[0].cells].forEach((th,i)=>{const text=th.textContent;const b=document.createElement('button');b.type='button';b.className='sort-button';b.dataset.sort=keys[i];b.textContent=text;b.title='Sort by '+text;th.replaceChildren(b);b.addEventListener('click',()=>click(keys[i]));});}
function arrows(table,state){for(const th of table.tHead.rows[0].cells){th.removeAttribute('aria-sort');const b=th.querySelector('[data-sort]');if(state&&b.dataset.sort===state.key)th.setAttribute('aria-sort',state.dir==='asc'?'ascending':'descending');}}
const mt=$('model-table'),mb=mt.tBodies[0],original=[...mb.children],modelRows=[...mb.querySelectorAll('[data-model]')],groups=[...mb.querySelectorAll('.group')];
const groupKeys=['voice-agent','transcription','audio','tts','azure-speech'];groups.forEach((g,i)=>g.dataset.category=groupKeys[i]);
let modelSort=null;
const mids=['model-search','category-filter','stream-filter','speaker-filter','tested-filter','cost-unit-filter'];
function modelValue(r,key){const m=data.models[r.dataset.model],cells=r.cells;const cols={purpose:2,stream:3,speaks:4,speakers:5,finding:8};if(key==='model')return m.model;if(key==='score')return m.score;if(key==='cost')return m.cost;if(key==='continuity'){const b=data.benchmark.find(x=>x.model===m.model);return b?b.frank_continuity_passes/b.completed:null;}return cells[cols[key]].innerText;}
function updateModels(){const q=$('model-search').value.trim().toLowerCase(),cat=$('category-filter').value,stream=$('stream-filter').value,speaker=$('speaker-filter').value,test=$('tested-filter').value,unit=$('cost-unit-filter').value;let visible=0;
 for(const r of modelRows){const m=data.models[r.dataset.model];r.hidden=Boolean((q&&!r.textContent.toLowerCase().includes(q))||(cat&&cat!==m.category)||(stream&&stream!==m.stream)||(speaker&&speaker!==m.speaker)||(test&&test!==m.test)||(unit&&unit!==m.costUnit));if(!r.hidden)visible++;}
 if(modelSort){for(const g of groups)g.hidden=true;for(const r of ordered(modelRows,modelSort,modelValue))mb.append(r);}else{for(const node of original)mb.append(node);for(const g of groups){const all=modelRows.filter(r=>data.models[r.dataset.model].category===g.dataset.category),n=all.filter(r=>!r.hidden).length;g.hidden=!n;g.querySelector('span').textContent=n+' of '+all.length+' shown';}}
 $('model-count').textContent=visible+' of '+modelRows.length+' models shown';$('model-empty').hidden=visible!==0;arrows(mt,modelSort);
}
headers(mt,['model','score','purpose','stream','speaks','speakers','continuity','cost','finding'],key=>{if(key==='cost'&&(!$('cost-unit-filter').value||$('cost-unit-filter').value==='unknown')){$('sort-notice').hidden=false;return;}$('sort-notice').hidden=true;modelSort={key,dir:modelSort?.key===key&&modelSort.dir==='asc'?'desc':'asc'};updateModels();});
for(const id of mids)$(id).addEventListener(id==='model-search'?'input':'change',()=>{if(id==='cost-unit-filter'){$('sort-notice').hidden=true;if(modelSort?.key==='cost'&&!$(id).value)modelSort=null;}updateModels();});
$('model-reset').addEventListener('click',()=>{for(const id of mids)$(id).value='';modelSort=null;$('sort-notice').hidden=true;updateModels();});
const bt=$('result-table'),bb=bt.tBodies[0],benchRows=[...bb.rows];let benchSort={key:'score',dir:'desc'};
for(const r of benchRows)r.dataset.model=r.cells[0].textContent.trim();
function benchValue(r,key){const b=data.benchmark.find(x=>x.model===r.dataset.model);if(key==='model')return b.model;if(key==='score')return data.models[b.model].score;if(key==='completed')return b.completed;if(key==='failures')return b.setup_failed;if(key==='continuity')return b.frank_continuity_passes/b.completed;if(key==='separation')return b.three_longer_voices_distinct_passes/b.completed;if(key==='latency')return b.first_text_after_commit_median_s;return r.cells[7].innerText;}
function updateBench(){const q=$('bench-search').value.trim().toLowerCase();for(const r of benchRows)r.hidden=Boolean(q&&!r.textContent.toLowerCase().includes(q));for(const r of ordered(benchRows,benchSort,benchValue))bb.append(r);$('bench-count').textContent=benchRows.filter(r=>!r.hidden).length+' of '+benchRows.length+' results shown';arrows(bt,benchSort);}
headers(bt,['model','score','completed','failures','continuity','separation','latency','finding'],key=>{benchSort={key,dir:benchSort?.key===key&&benchSort.dir==='asc'?'desc':'asc'};updateBench();});
$('bench-search').addEventListener('input',updateBench);$('bench-reset').addEventListener('click',()=>{$('bench-search').value='';benchSort={key:'score',dir:'desc'};updateBench();});
updateModels();updateBench();
})();
