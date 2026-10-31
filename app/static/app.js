const $ = id => document.getElementById(id);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const labels = {none:'Unmarked',ignored:'Ignored',owned:'Already have',download:'For download'};
const state = {view:'discover',page:1,selected:null,film:null,request:0,detailRequest:0,job:null,move:null};
function error(message) { $('error').textContent = message; $('error').classList.toggle('hidden',!message); }
async function api(path, options={}) {
 const response = await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json',...options.headers}});
 let data; try { data = await response.json(); } catch { throw new Error('The server returned an unreadable response.'); }
 if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Please check the supplied values.');
 return data;
}
const send = (path,body={},method='POST') => api(path,{method,body:JSON.stringify(body)});
const cover = (f,hero=false) => `<div class="cover tone${f.id%4} ${hero?'hero-cover':''}" aria-hidden="true">${esc(f.title.slice(0,1))}</div>`;
const options = (current,release=false) => Object.entries(labels).map(([key,value])=>`<option value="${key}" ${key===current?'selected':''}>${release&&key==='none'?'Inherit film status':value}</option>`).join('');
function facet(id,values,label) {
 const current=$(id).value;
 $(id).innerHTML=`<option value="">${label}</option>`+values.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join('');
 $(id).value=values.map(String).includes(current)?current:'';
}
async function load() {
 const request=++state.request;
 const params=new URLSearchParams({view:state.view==='plex'?'plex':'discover',page:state.page});
 for(const id of ['year','genre','flag','category','sort']) params.set(id,$(id).value);
 params.set('q',$('search').value);
 if(state.view==='queue')params.set('flag','download');
 try {
  const data=await api('/films?'+params);
  if(request!==state.request)return;
  for(const [id,key] of [['discover-count','discovered'],['plex-count','plex'],['queue-count','queued'],['metric-discovered','discovered'],['metric-plex','plex'],['metric-queued','queued']])$(id).textContent=data.stats[key];
  facet('year',data.facets.years,'All years');facet('genre',data.facets.genres,'All genres');
  $('result-count').textContent=`${data.total} film${data.total===1?'':'s'}`;
  $('page-number').textContent=`Page ${state.page} of ${Math.max(1,Math.ceil(data.total/50))}`;
  $('previous').disabled=state.page===1;$('next').disabled=state.page*50>=data.total;
  $('film-list').innerHTML=data.films.length?data.films.map(f=>`<button class="film-row ${f.id===state.selected?'selected':''}" data-film="${f.id}" aria-pressed="${f.id===state.selected}">${cover(f)}<div class="film-copy"><strong>${esc(f.title)}</strong><p>${f.year||'Year unknown'} · ${esc(f.genres.slice(0,2).join(' / ')||'Genre not available')}</p><div class="row-meta"><span class="pill ${f.effective_flag}">${labels[f.effective_flag]}</span><span>${f.release_count} release${f.release_count===1?'':'s'}</span></div></div><span class="score">${f.rating?'★ '+f.rating:'—'}</span></button>`).join(''):`<div class="empty"><span class="empty-icon">⌕</span><h2>${$('search').value||$('year').value||$('genre').value||$('flag').value?'No matching films':'Your collection starts here'}</h2><p>${state.view==='plex'?'Connect Plex and sync a movie library.':state.view==='queue'?'Mark a film or release “For download” to keep it here.':'Scan the four sources to discover films.<br>Already scanned? Try adjusting your filters.'}</p></div>`;
  if(state.selected) await selectFilm(state.selected,false);
 } catch(e){error(e.message);}
}
async function selectFilm(id,scroll=true) {
 const request=++state.detailRequest;state.selected=id;
 try {
  const f=await api('/films/'+id);if(request!==state.detailRequest)return;state.film=f;
  document.querySelectorAll('[data-film]').forEach(el=>{el.classList.toggle('selected',Number(el.dataset.film)===id);el.setAttribute('aria-pressed',Number(el.dataset.film)===id);});
  const ratings=f.ratings.length?f.ratings.map(r=>`<div class="rating-box"><b>${esc(r.Value)}</b><small>${esc(r.Source)}</small></div>`).join(''):'<p class="help">No ratings yet. Connect OMDb, then refresh ratings.</p>';
  const releases=f.releases.map(r=>`<article class="release"><h4>${esc(r.title)}</h4><div class="release-meta"><span>${esc(r.category)}</span><span>${esc(r.size||'Size unknown')}</span><span class="seed">↑ ${r.seeds??'—'} seeds</span><span>↓ ${r.peers??'—'} peers</span><span>Added ${esc(r.recorded_at)}</span></div><div class="release-actions"><a href="${esc(r.url)}" target="_blank" rel="noreferrer">Open source ↗</a><button class="button subtle" data-identity="${r.id}">Correct grouping</button><select data-release-flag="${r.id}" aria-label="Status for ${esc(r.title)}">${options(r.flag,true)}</select></div><p class="help">Effective status: ${labels[r.flag==='none'?f.effective_flag:r.flag]} · First seen ${esc(r.first_seen.slice(0,10))}</p></article>`).join('');
  const plex=f.plex.map(p=>`<article class="release"><h4>Plex · ${esc(p.title)} <span class="pill owned">In library ${esc(p.library)}</span></h4>${p.details.files.map(file=>`<div class="file-path">${esc(file.file||'Path unavailable')}</div><div class="file-grid"><span>Size: ${file.size?(Number(file.size)/1073741824).toFixed(2)+' GB':'—'}</span><span>Video: ${esc(file.videoCodec||'—')} · ${esc(file.videoResolution||'—')}</span><span>Audio: ${esc(file.audioCodec||'—')} · ${esc(file.audioChannels||'—')} channels</span><span>Container: ${esc(file.container||'—')}</span><span>Duration: ${file.duration?Math.round(Number(file.duration)/60000)+' min':'—'}</span><span>Bitrate: ${esc(file.bitrate||'—')} kbps</span></div>`).join('<hr>')||'<p class="help">No media file details returned by Plex.</p>'}<p class="help">Synced ${esc(p.details.synced_at.slice(0,10))}</p></article>`).join('');
  $('detail').innerHTML=`<div class="film-hero">${cover(f,true)}<div><div class="detail-label">${f.in_plex?'IN YOUR COLLECTION':'FILM DETAILS'}</div><h2>${esc(f.title)}</h2><p>${f.year||'Unknown year'} <span>·</span> ${esc(f.genres.join(' / ')||'Genre not available')}</p><span class="pill ${f.effective_flag}">${labels[f.effective_flag]}</span></div></div><div class="ratings">${ratings}</div>${f.metadata_error?`<p class="help">${esc(f.metadata_error)}</p>`:''}<p class="overview">${esc(f.plot&&f.plot!=='N/A'?f.plot:f.plex[0]?.details.summary||'A synopsis will appear after a matching movie is found through OMDb or synced from Plex.')}</p><div class="flag-bar"><span>Keep this film on your radar</span><select id="film-flag" aria-label="Film status">${options(f.flag)}</select></div><p class="help">Film status applies to releases unless a release has its own flag. Unmarked films in Plex are automatically marked as already owned.</p><div class="section-title"><h3>Available releases</h3><span>${f.releases.length}</span></div>${releases||'<p class="help">No source releases linked to this film yet.</p>'}${f.plex.length?`<div class="section-title"><h3>Your Plex files</h3><span>${f.plex.length}</span></div>${plex}`:''}`;
  if(scroll&&innerWidth<800)$('detail').scrollIntoView({behavior:'smooth'});
 }catch(e){error(e.message);}
}
async function settingsOpen() {
 try {
  const s=await api('/settings');$('plex-url').value=s.plex_url;$('plex-token').value='';$('omdb-key').value='';
  $('plex-library').innerHTML=`<option value="">Select a library…</option>${s.plex_library?`<option value="${esc(s.plex_library)}" selected>Saved library ${esc(s.plex_library)}</option>`:''}`;
  $('connection-status').textContent=`Plex token: ${s.plex_token_set?'saved':'not set'} · OMDb key: ${s.omdb_key_set?'saved':'not set'}${s.environment_overrides.length?' · Environment overrides: '+s.environment_overrides.join(', '):''}`;
  const source=await api('/source/status');
  $('source-status').textContent=source.saved?'A source browser session is saved on this computer.':'No saved source session. Sign in below if scanning requires login.';
  $('settings-error').classList.add('hidden');$('settings-dialog').showModal();
 }catch(e){error(e.message);}
}
async function saveConnections() {
 return send('/settings',{plex_url:$('plex-url').value,plex_library:$('plex-library').value,plex_token:$('plex-token').value||null,omdb_key:$('omdb-key').value||null},'PUT');
}
function settingsError(e){$('settings-error').textContent=e.message;$('settings-error').classList.remove('hidden');}
async function begin(path,body={}) {
 error('');try{await send(path,body);await poll();}catch(e){error(e.message);}
}
async function poll() {
 try {
  const jobs=await api('/jobs');if(!jobs.length)return;
  const j=jobs[0];$('job').classList.remove('hidden');$('job').classList.toggle('failed',['failed','interrupted'].includes(j.state));
  $('browser-controls').classList.toggle('hidden',!j.browser_waiting);
  $('job').textContent=`${j.state==='running'?'↻':j.state==='complete'?'✓':'!'} ${j.kind.toUpperCase()} · ${j.message}`;
  for(const id of ['scan','ratings','sync-plex','source-login','source-clear'])$(id).disabled=j.state==='running';
  if(state.job!==`${j.id}:${j.state}`){state.job=`${j.id}:${j.state}`;await load();}
 }catch(e){error(e.message);}
}
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{
 state.view=button.dataset.view;state.page=1;$('flag').value='';$('year').value='';$('genre').value='';$('category').value='';$('search').value='';
 document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b===button));
 const titles={discover:['Discover','Find your next film.','New releases, organized around the films you care about.','Release radar'],plex:['Plex library','Your personal cinema.','Browse the films and files already in your Plex library.','Your collection'],queue:['Download list','Good films, on your list.','The films and releases you have marked for download.','Your shortlist']};
 const t=titles[state.view];$('crumb').textContent=t[0];$('page-title').textContent=t[1];$('page-description').textContent=t[2];$('list-title').textContent=t[3];$('flag').disabled=state.view==='queue';load();
});
$('film-list').onclick=e=>{const row=e.target.closest('[data-film]');if(row)selectFilm(Number(row.dataset.film));};
$('detail').onchange=async e=>{
 const target=e.target;try{
 if(target.id==='film-flag')await send(`/films/${state.selected}/flag`,{flag:target.value},'PATCH');
 else if(target.dataset.releaseFlag)await send(`/releases/${target.dataset.releaseFlag}/flag`,{flag:target.value},'PATCH');
 await load();}catch(err){error(err.message);await selectFilm(state.selected,false);}
};
$('detail').onclick=e=>{const button=e.target.closest('[data-identity]');if(button){state.move=Number(button.dataset.identity);$('identity-title').value=state.film.title;$('identity-year').value=state.film.year||'';$('identity-dialog').showModal();}};
$('identity-form').onsubmit=async e=>{e.preventDefault();try{const result=await send(`/releases/${state.move}/identity`,{title:$('identity-title').value,year:$('identity-year').value?Number($('identity-year').value):null},'PATCH');$('identity-dialog').close();state.selected=result.film_id;await load();}catch(err){$('identity-dialog').close();error(err.message);}};
let debounce; $('search').oninput=()=>{clearTimeout(debounce);debounce=setTimeout(()=>{state.page=1;load();},250);};
for(const id of ['year','genre','flag','category','sort'])$(id).onchange=()=>{state.page=1;load();};
$('previous').onclick=()=>{state.page--;load();};$('next').onclick=()=>{state.page++;load();};
$('connections').onclick=settingsOpen;
$('source-login').onclick=async()=>{try{await send('/source/login');$('settings-dialog').close();await poll();}catch(e){settingsError(e);}};
$('source-clear').onclick=async()=>{try{await send('/source/session',{},'DELETE');$('source-status').textContent='Saved source session cleared.';}catch(e){settingsError(e);}};
$('source-continue').onclick=async()=>{try{await send('/source/continue');$('browser-controls').classList.add('hidden');}catch(e){error(e.message);}};
$('source-cancel').onclick=async()=>{try{await send('/source/cancel');$('browser-controls').classList.add('hidden');}catch(e){error(e.message);}};
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$(b.dataset.close).close());
$('settings-form').onsubmit=async e=>{e.preventDefault();try{await saveConnections();$('settings-dialog').close();}catch(err){settingsError(err);}};
$('load-libraries').onclick=async()=>{try{await saveConnections();const libs=await api('/plex/libraries');const current=$('plex-library').value;$('plex-library').innerHTML='<option value="">Select a library…</option>'+libs.map(l=>`<option value="${esc(l.id)}">${esc(l.title)}</option>`).join('');if(libs.some(l=>l.id===current))$('plex-library').value=current;$('settings-error').classList.add('hidden');$('connection-status').textContent=`Connected. Found ${libs.length} movie libraries.`;}catch(e){settingsError(e);}};
$('sync-plex').onclick=async()=>{try{await saveConnections();await send('/plex/sync');$('settings-dialog').close();poll();}catch(e){settingsError(e);}};
$('scan').onclick=()=>$('scan-dialog').showModal();
$('scan-form').onsubmit=e=>{e.preventDefault();$('scan-dialog').close();begin('/scan',{cutoff:$('cutoff').value,max_pages:Number($('max-pages').value)});};
$('ratings').onclick=()=>begin('/ratings');
load();poll();setInterval(poll,2500);
