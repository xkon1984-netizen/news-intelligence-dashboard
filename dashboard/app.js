const state={items:[],generatedAt:null};
const labels={geopolitico:'Geopolitico',pontos_voice:'Pontos Voice',sportdog:'Sportdog'};

async function loadNews(){
  const res=await fetch('../data/news.json',{cache:'no-store'});
  if(!res.ok) throw new Error('news.json not found');
  const data=await res.json();
  state.items=data.items||[];
  state.generatedAt=data.generated_at||null;
  render();
}

function topScore(item,mode){
  if(mode!=='all') return item.scores?.[mode]||0;
  return Math.max(0,...Object.values(item.scores||{}));
}

function scoreClass(score){
  if(score>=80) return 'high';
  if(score>=60) return 'medium';
  return 'low';
}

function fmtDate(value){
  if(!value) return 'Unknown time';
  return new Date(value).toLocaleString('el-GR',{dateStyle:'short',timeStyle:'short'});
}

function render(){
  const mode=document.querySelector('#mode').value;
  const minScore=Number(document.querySelector('#minScore').value);
  const search=document.querySelector('#search').value.trim().toLowerCase();
  const sort=document.querySelector('#sort').value;

  let items=state.items.filter(item=>{
    const score=topScore(item,mode);
    if(score<minScore) return false;
    if(mode!=='all' && !item.scores?.[mode]) return false;
    const keywords=Object.values(item.matched_keywords||{}).flat().join(' ');
    const haystack=`${item.title||''} ${item.source||''} ${keywords}`.toLowerCase();
    return !search || haystack.includes(search);
  });

  items.sort((a,b)=>{
    if(sort==='latest') return new Date(b.published)-new Date(a.published);
    return topScore(b,mode)-topScore(a,mode) || new Date(b.published)-new Date(a.published);
  });

  document.querySelector('#count').textContent=`${items.length} items`;
  document.querySelector('#updated').textContent=state.generatedAt?`Updated: ${fmtDate(state.generatedAt)}`:'Not updated yet';

  const feed=document.querySelector('#feed');
  if(!items.length){
    feed.innerHTML='<div class="empty">No matching stories.</div>';
    return;
  }

  feed.innerHTML=items.map(item=>{
    const score=topScore(item,mode);
    const modes=Object.entries(item.scores||{}).map(([key,value])=>`<span class="badge">${labels[key]||key}: ${value}</span>`).join('');
    const matched=[...new Set(Object.values(item.matched_keywords||{}).flat())].slice(0,8).map(k=>`<span class="badge">${k}</span>`).join('');
    return `<article class="card">
      <div class="card-head">
        <div>
          <p class="title">${escapeHtml(item.title||'Untitled')}</p>
          <div class="meta">${escapeHtml(item.source||'Unknown source')} · ${fmtDate(item.published)}</div>
        </div>
        <div class="score ${scoreClass(score)}">${score}</div>
      </div>
      <div class="badges">${modes}${matched}</div>
      <a class="open" href="${item.url}" target="_blank" rel="noopener noreferrer">Open original source</a>
    </article>`;
  }).join('');
}

function escapeHtml(value){
  return String(value).replace(/[&<>'"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
}

document.querySelectorAll('#mode,#minScore,#sort').forEach(el=>el.addEventListener('change',render));
document.querySelector('#search').addEventListener('input',render);

loadNews().catch(err=>{
  document.querySelector('#feed').innerHTML=`<div class="empty">${escapeHtml(err.message)}</div>`;
});
