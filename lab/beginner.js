/* Trading Lab: five-minute scenarios and local executable paper orders. */
'use strict';
function intraExit(bars, entry, stop, target) {
  for (const b of bars) {
    if (b.o <= stop) return {kind:'stop',price:b.o};
    if (b.o >= target) return {kind:'target',price:b.o};
    if (b.l <= stop) return {kind:'stop',price:stop};
    if (b.h >= target) return {kind:'target',price:target};
  }
  return {kind:'neither',price:bars.length?bars.at(-1).c:entry};
}
function intraModel(bars, horizon, count=10000) {
  if (!Number.isInteger(horizon)||horizon<1||horizon>78||![1000,10000].includes(count)) throw Error('Invalid simulation settings');
  if (bars.length<80) throw Error('Need 80 completed five-minute candles');
  bars.forEach((b,i)=>{if(![b.o,b.h,b.l,b.c,b.t].every(Number.isFinite)||Math.min(b.o,b.h,b.l,b.c)<=0||b.h<Math.max(b.o,b.c)||b.l>Math.min(b.o,b.c)||(i&&b.t<=bars[i-1].t))throw Error('Invalid five-minute candle');});
  let blocks=[];
  for(let i=Math.max(1,bars.length-1600);i+4<bars.length;i++) {
    let segment=bars.slice(i-1,i+5);
    if(segment.every((b,j)=>b.session===segment[0].session&&(!j||b.t===segment[j-1].t+300))) blocks.push(segment.slice(1).map((b,j)=>({o:b.o/segment[j].c,h:b.h/segment[j].c,l:b.l/segment[j].c,c:b.c/segment[j].c})));
  }
  if(blocks.length<60)throw Error('Need 60 contiguous within-session return blocks; missing/overnight candles are not stitched');
  let entry=bars.at(-1).c,seed=horizon+count;
  for(let b of bars.slice(-1600))for(let ch of JSON.stringify(b))seed=(Math.imul(seed,31)+ch.charCodeAt(0))>>>0;
  let rng=labRng(seed),paths=[],lows=[],highs=[];
  for(let n=0;n<count;n++){
    let p=entry,closes=[p],candles=[],lo=p,hi=p,block;
    for(let d=0;d<horizon;d++){
      if(d%5===0)block=blocks[Math.floor(rng()*blocks.length)];
      let r=block[d%5],b={o:p*r.o,h:p*r.h,l:p*r.l,c:p*r.c};
      if(!Object.values(b).every(v=>Number.isFinite(v)&&v>0))throw Error('Model overflow');
      candles.push(b);closes.push(b.c);lo=Math.min(lo,b.l);hi=Math.max(hi,b.h);p=b.c;
    }
    paths.push({closes,candles,end:p});lows.push(lo);highs.push(hi);
  }
  lows.sort((a,b)=>a-b);highs.sort((a,b)=>a-b);
  let stop=Math.floor(Math.max(.01,Math.min(entry-.01,labQ(lows,.25)))*100)/100,target=Math.ceil(Math.max(entry+.01,labQ(highs,.60))*100)/100;
  if(stop>=entry)throw Error('Price too low for valid stop');
  let ends=paths.map(p=>p.end).sort((a,b)=>a-b),min=ends[0],max=ends.at(-1),width=(max-min)/24,bins=Array.from({length:24},(_,i)=>({low:min+i*width,high:min+(i+1)*width,count:0}));
  paths.forEach(p=>bins[width?Math.min(23,Math.floor((p.end-min)/width)):0].count++);
  let modal=bins.reduce((a,b)=>b.count>a.count?b:a),fan=Array.from({length:horizon+1},(_,d)=>{let a=paths.map(p=>p.closes[d]).sort((a,b)=>a-b);return {lo:labQ(a,.10),median:labQ(a,.50),hi:labQ(a,.90)};}),exits={stop:0,target:0,neither:0};
  paths.forEach(p=>exits[intraExit(p.candles,entry,stop,target).kind]++);
  return {history:bars.slice(-253),asOf:bars.at(-1).date,entry,stop,target,horizon,count,paths,bins,modal,fan,exits,p10:labQ(ends,.10),p50:labQ(ends,.50),p90:labQ(ends,.90),seed,sampleSize:blocks.length,timeframe:'5m'};
}
function intraHistory(bars,sr,tr){
  let groups=new Map(),out={sessions:0,triggers:0,target:0,stop:0,neither:0};
  bars.forEach(b=>{if(!groups.has(b.session))groups.set(b.session,[]);groups.get(b.session).push(b);});
  for(let session of groups.values()){
    let first=new Date(session[0].date),last=new Date(session.at(-1).date);
    // Completed historical sessions only, starting 09:30 local and ending at least 15:55.
    if(!session[0].date.includes('T09:30:')||!session.at(-1).date.includes('T15:55:')||session.length!==78)continue;
    out.sessions++;let high=Math.max(...session.slice(0,3).map(b=>b.h));
    let i=session.findIndex((b,j)=>j>=3&&j<session.length-1&&b.c>high);
    if(i<0)continue;out.triggers++;let entry=session[i+1].o;
    out[intraExit(session.slice(i+1),entry,entry*sr,entry*tr).kind]++;
  }
  return out;
}
class IntradayPaper {
  constructor(saved=null){this.cash=10000;this.pending=null;this.position=null;this.ledger=[];this.last=0;if(saved){if(!Number.isFinite(saved.cash)||saved.cash<0)throw Error('Invalid paper account');Object.assign(this,saved);}}
  equity(mark){return this.cash+(this.position?this.position.qty*mark:0);}
  queue(symbol,qty,entry,stop,target,bar){
    if(this.position||this.pending)throw Error('Finish the current paper order first');
    if(!/^[A-Z0-9.^-]{1,15}$/.test(symbol)||!Number.isInteger(qty)||qty<1||!Number.isFinite(entry)||!Number.isFinite(stop)||!Number.isFinite(target)||stop<=0||stop>=entry||target<=entry)throw Error('Use whole shares and Stop < Entry < Target');
    if(entry*qty>this.cash)throw Error('Not enough paper cash');
    if((entry-stop)*qty>this.cash*.01)throw Error('Planned risk exceeds 1% of paper cash');
    this.pending={symbol,qty,entry,stop,target,after:bar.t};this.last=bar.t;
    this.ledger.push({side:'QUEUED',symbol,qty,time:bar.date,entry,stop,target,status:'PAPER_PENDING_NEXT_OPEN'});
  }
  step(b){
    if(b.t<=this.last)return;
    if(![b.o,b.h,b.l,b.c,b.t].every(Number.isFinite)||Math.min(b.o,b.h,b.l,b.c)<=0||b.h<Math.max(b.o,b.c)||b.l>Math.min(b.o,b.c))throw Error('Invalid execution candle');
    this.last=b.t;
    if(this.pending&&b.t>this.pending.after){
      let p=this.pending;this.pending=null;
      if(b.o<=p.stop||b.o>=p.target||b.o*p.qty>this.cash||(b.o-p.stop)*p.qty>this.cash*.01){this.ledger.push({side:'REJECTED',symbol:p.symbol,time:b.date,reason:'Opening gap invalidates bracket/cash/1% risk limit'});return;}
      this.cash-=b.o*p.qty;this.position={...p,entry:b.o,time:b.date,t:b.t};
      this.ledger.push({side:'BUY',symbol:p.symbol,time:b.date,t:b.t,qty:p.qty,price:b.o,stop:p.stop,target:p.target,status:'PAPER_FILLED'});
    }
    if(this.position){let p=this.position,e=intraExit([b],p.entry,p.stop,p.target);if(e.kind!=='neither'){this.cash+=e.price*p.qty;this.ledger.push({side:'SELL',symbol:p.symbol,time:b.date,t:b.t,qty:p.qty,price:e.price,reason:e.kind,pnl:(e.price-p.entry)*p.qty,status:'PAPER_CLOSED'});this.position=null;}}
  }
}
if(typeof document!=='undefined'){
  let intradayResponse=null,livePaper,replayPaper=new IntradayPaper(),practice=null,mode='5m',requestBusy=false;
  try{livePaper=new IntradayPaper(JSON.parse(localStorage.getItem('tradingLabIntradayPaperV1')||'null'));}catch{livePaper=new IntradayPaper();}
  function book(){return practice?replayPaper:livePaper;}
  function save(){if(!practice)localStorage.setItem('tradingLabIntradayPaperV1',JSON.stringify(livePaper));}
  const style=document.createElement('style');style.textContent='.lab-grid{grid-template-columns:repeat(3,minmax(0,1fr));max-width:920px;margin:26px auto}.lab-tool{background:#faf9f6;color:#20272a;border:1px solid #e2dfd9;box-shadow:0 5px 14px #0002;min-height:160px;border-radius:18px;gap:7px}.lab-tool svg{width:120px;height:86px}.lab-tool strong{font-size:15px}.tool-banner{text-align:center;letter-spacing:.1em;font:700 22px monospace;background:#2c3033;color:#fff;padding:16px;border-radius:12px;grid-column:1/-1}.paper-layout{display:grid;grid-template-columns:minmax(220px,.7fr) minmax(0,1.4fr);gap:22px}.paper-ledger{font:12px/1.6 system-ui;max-height:200px;overflow:auto}.paper-buy{font-size:24px;color:#75dfb7}.paper-stamp{display:inline-block;border:2px solid #789fc9;transform:rotate(-8deg);padding:1px 8px;color:#bfd7ff;font-weight:700}.paper-brackets{display:flex;gap:10px;flex-wrap:wrap}.paper-chip{padding:5px 10px;border-radius:20px;background:#0c1726;color:#c7dcf2}.paper-controls{display:flex;gap:10px;flex-wrap:wrap;margin:15px 0}.paper-controls input{max-width:110px}.lab-ticket-chart{height:300px}.intraday-select{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}.source-line{font-size:12px;color:#a9c0dd}@media(max-width:650px){.lab-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.paper-layout{grid-template-columns:1fr}.lab-tool{min-height:145px}.lab-tool svg{width:104px}.tool-banner{font-size:18px}}';document.head.append(style);
  let selector=document.createElement('div');selector.className='intraday-select';selector.innerHTML='<label>Timeframe <select id="intraMode"><option value="5m">5-minute · to session close</option><option value="daily">Daily · future trading days</option></select></label><span class="lab-tip">Paper only. No brokerage connection.</span>';labGet('labFutureForm').before(selector);
  labGet('intraMode').onchange=()=>{mode=labGet('intraMode').value;practice=null;labGet('labHorizon').closest('label').hidden=mode==='5m';labGet('labResult').hidden=true;labGet('labStatus').textContent=mode==='5m'?'Real completed 5-minute candles; simulation to the supplied session close. Closed market uses a standard next session.':'Daily bootstrap. No intraday accuracy claim.';};labGet('labHorizon').closest('label').hidden=true;
  labGet('future').querySelector('h1').textContent='Time Machine';labGet('future').querySelector('.lab-tip').textContent='See how the price could move from the latest candle. Blue paths are practice scenarios, not guaranteed predictions.';
  labGet('labFutureForm').onsubmit=e=>{e.preventDefault();practice=null;mode==='daily'?labLoad():loadIntraday();};
  const ticket=labGet('labTicketTitle').closest('.panel');ticket.id='paperTicket';ticket.innerHTML='<div class="paper-layout"><div><div class="lab-small">Opening Range Breakout · first 15 minutes</div><h2 id="paperTitle">Stock trade</h2><p>Practice this breakout for me. Paper only.</p><p id="paperTrigger"></p><div class="paper-buy">BUY <span id="paperSymbol"></span> <span class="paper-stamp">PAPER</span></div><p id="paperEntry" class="lab-small"></p><div id="paperBrackets" class="paper-brackets"></div><div class="paper-controls"><label>Shares <input id="paperQty" type="number" min="1" step="1" value="1"></label><button id="paperExecute" class="primary">Place paper order</button></div><p id="paperStatus" class="lab-small"></p><div class="paper-controls"><button id="paperRefresh">Refresh & process next candle</button><button id="paperPractice">Practice a completed session</button><button id="paperNext" hidden>Next 5-minute candle</button></div><p id="paperCash" class="lab-small"></p><details class="lab-fold"><summary>Paper order ledger & method</summary><div id="paperLedger" class="paper-ledger"></div><p class="lab-small">Whole shares; no leverage; 1% planned-risk cap. Queue after the visible candle, fill only at a later candle open. Opening gaps can reject a bracket or exceed stop loss. Conservative stop-first handling if both levels are touched. No commissions, spread or slippage model. Local paper account only, not a real order. Historical practice uses a separate temporary account with hidden future candles.</p><button id="paperExport">Export paper ledger</button></details></div><div><div class="lab-small" id="paperChartLabel">5-minute candles</div><div class="lab-ticket-chart"><canvas id="labTicketCanvas" aria-label="5-minute paper trade candles, BUY marker, red Stop line and green Target line"></canvas></div></div></div>';
  // Existing daily render expects these nodes; they are safely retained off-screen.
  const compat=document.createElement('div');compat.hidden=true;compat.innerHTML='<h2 id="labTicketTitle"></h2><p id="labTrigger"></p><p id="labTicketStatus"></p><div id="labTicketLevels"></div>';ticket.append(compat);
  function currentBars(){return practice?practice.bars.slice(0,practice.cursor+1):intradayResponse?.bars||[];}
  function drawPaper(){
    let bars=currentBars(),m=labData,s=labCanvas('labTicketCanvas');if(!m||!s||!bars.length)return;
    let {ctx,w,h}=s,bk=book(),fill=bk.ledger.filter(e=>e.side==='BUY').at(-1),stop=bk.position?.stop||bk.pending?.stop||m.stop,target=bk.position?.target||bk.pending?.target||m.target,entry=fill?.price||m.entry;
    let last=bars.at(-1),visible=bars.filter(b=>b.session===last.session).slice(-48),left=18,right=106,top=24,bottom=33,cw=(w-left-right)/visible.length,lo=Math.min(stop,...visible.map(b=>b.l)),hi=Math.max(target,...visible.map(b=>b.h)),pad=(hi-lo)*.15||1;lo-=pad;hi+=pad;let y=v=>top+(h-top-bottom)*(hi-v)/(hi-lo);
    ctx.fillStyle='#b3cbe6';ctx.fillText('5-minute · '+last.session+' · '+(practice?'historical practice':'delayed public data'),left,14);
    visible.forEach((b,i)=>{let x=left+(i+.5)*cw,color=b.c>=b.o?'#75dfb7':'#ff8f9f';ctx.strokeStyle=color;ctx.fillStyle=color;ctx.beginPath();ctx.moveTo(x,y(b.h));ctx.lineTo(x,y(b.l));ctx.stroke();ctx.fillRect(x-cw*.28,Math.min(y(b.o),y(b.c)),Math.max(2,cw*.56),Math.max(1,Math.abs(y(b.o)-y(b.c))));if(fill&&b.t===fill.t){ctx.fillStyle='#79e9c4';ctx.beginPath();ctx.arc(x,y(fill.price)-12,7,0,Math.PI*2);ctx.fill();ctx.fillText('BUY',Math.max(left,x-10),y(fill.price)-23);}});
    [[stop,'Stop','#ff8f9f'],[target,'Target','#79e9c4'],[entry,fill?'Entry':'Reference','#badbff']].forEach(([v,label,color])=>{ctx.strokeStyle=color;ctx.setLineDash([5,5]);ctx.beginPath();ctx.moveTo(left,y(v));ctx.lineTo(w-right+3,y(v));ctx.stroke();ctx.setLineDash([]);ctx.fillStyle=color;ctx.fillText(label+' '+fmt(v,2),w-right+8,y(v)+4);});
    ctx.fillStyle='#b3cbe6';ctx.fillText(visible[0].date.slice(11,16),left,h-12);ctx.textAlign='right';ctx.fillText(last.date.slice(11,16)+' ET',w-right,h-12);ctx.textAlign='left';
  }
  function renderPaper(){
    let m=labData;if(!m)return;let bk=book(),bars=currentBars(),last=bars.at(-1),session=bars.filter(b=>b.session===last?.session),range=session.slice(0,3),high=range.length===3?Math.max(...range.map(b=>b.h)):null,trigger=high!==null&&session.length>3&&last.c>high,fill=bk.ledger.filter(e=>e.side==='BUY').at(-1);
    labGet('paperTitle').textContent=labData.symbol+' trade';labGet('paperSymbol').textContent=labData.symbol;
    labGet('paperTrigger').textContent=mode==='daily'?'Daily simulation shown above. Switch to five-minute mode for executable paper practice.':trigger?'Closed above the opening-range high '+labPrice(m,high)+' at '+last.date.slice(11,16)+' ET on '+last.session+'. You can queue a paper BUY; it fills only at the next visible 5-minute open.':'No breakout trigger at the visible candle. A paper BUY requires a close above the first 15-minute high'+(high?' ('+labPrice(m,high)+')':'')+'. No automatic order placed.';
    labGet('paperEntry').textContent=fill?fill.time+' · '+labPrice(m,fill.price)+' · '+fill.qty+' share(s) · PAPER FILLED':'Reference '+labPrice(m,m.entry)+' · not yet filled';
    let stop=bk.position?.stop||bk.pending?.stop||m.stop,target=bk.position?.target||bk.pending?.target||m.target;
    labGet('paperBrackets').innerHTML='<span class="paper-chip" style="color:#ff9caf">Stop '+labPrice(m,stop)+'</span><span class="paper-chip" style="color:#79e9c4">Target '+labPrice(m,target)+'</span>';
    let state=bk.pending?'PAPER ORDER QUEUED · waiting for next completed 5-minute candle':bk.position?'PAPER FILLED · bracket active':fill?'PAPER CLOSED · see execution ledger':'No paper order placed.';
    labGet('paperStatus').textContent=(practice?'Historical paper practice · ':'Local paper account · ')+state;
    labGet('paperExecute').disabled=mode!=='5m'||!trigger||!!bk.pending||!!bk.position||!last||(practice&&practice.cursor>=practice.bars.length-1);
    labGet('paperCash').textContent='Paper cash '+money(bk.cash)+' · equity '+money(bk.equity(last?.c||m.entry))+' · planned risk for '+(labGet('paperQty').value||1)+' shares: '+money((m.entry-m.stop)*Number(labGet('paperQty').value||1));
    labGet('paperLedger').replaceChildren();bk.ledger.slice(-20).reverse().forEach(e=>{let d=document.createElement('div');d.textContent=[e.side,e.symbol,e.time,e.qty?e.qty+' shares':'',e.price?money(e.price):'',e.reason||'',e.status||''].filter(Boolean).join(' · ');labGet('paperLedger').append(d);});
    labGet('paperNext').hidden=!practice;labGet('paperNext').disabled=!!practice&&practice.cursor>=practice.bars.length-1;labGet('paperChartLabel').textContent=practice?'5-minute historical candles · future hidden':'5-minute regular-session candles · latest supplied data';drawPaper();
  }
  function renderIntraday(){
    let d=practice?{...intradayResponse,mode:'historical-to-close',sessionClose:null}:intradayResponse,bars=currentBars(),horizon=practice?practice.bars.length-1-practice.cursor:d.horizon;
    if(!horizon){renderPaper();return;}
    let m=intraModel(bars,horizon,Number(labGet('labCount').value));labData={...m,symbol:d.symbol,currency:d.currency,source:d.source,note:d.note,backtest:intraHistory(bars,m.stop/m.entry,m.target/m.entry)};labRender();
    labGet('labSubtitle').textContent=d.symbol+' · 5-minute candle '+m.asOf+' → '+horizon+' five-minute steps '+(practice?'to this historical session close':d.mode==='to-close'?'to session close '+d.sessionClose:'into a standard next session');
    labGet('labMeta').innerHTML='<span class="lab-pill">5-minute regular session</span> <span class="lab-pill">'+horizon+' modeled intervals per replay</span>';
    labGet('labCountTitle').textContent=m.count.toLocaleString('en-US');labGet('labCountTitle').nextElementSibling.textContent='simulated session replays';
    labGet('labRecapTitle').textContent=labData.symbol+': entry, stop & target';
    labGet('labBacktest').textContent='Completed-session check: '+labData.backtest.sessions+' full 78-candle historical sessions; '+labData.backtest.triggers+' first 15-minute opening-range breakouts, '+labData.backtest.target+' target-first, '+labData.backtest.stop+' stop-first, '+labData.backtest.neither+' neither by close. Current percentage brackets applied retrospectively; not out-of-sample validation. Shortened/incomplete sessions excluded.';
    labGet('labHistTitle').textContent='Where '+labData.symbol+' closes across '+m.count.toLocaleString('en-US')+' simulated replays';
    labGet('labSource').textContent=d.source+' · '+d.currency+' · '+m.sampleSize+' within-session five-candle blocks. No overnight/missing-bar stitching. Only visible completed candles; 5m bootstrap, not daily data or predictive AI. Stop/target learned from these same paths. '+d.note;
    let status=practice?'Historical practice as of '+m.asOf+'. Later candles are hidden and excluded.':d.mode==='to-close'?'Latest supplied completed candle '+d.asOf+'; projecting to '+d.sessionClose+'. Data can be delayed.':'Market closed or latest supplied session is old. Reference '+d.asOf+'; modeling a standard next session, not today’s close. Future calendar/gaps not modeled.';
    labGet('labStatus').textContent=status;renderPaper();
  }
  async function loadIntraday(){
    if(requestBusy)return;requestBusy=true;let b=labGet('labFutureForm').querySelector('button');b.disabled=true;labGet('labStatus').textContent='Loading real completed five-minute candles…';
    try{let d=await labFetch('/api/intraday',{s:sym(labGet('labSymbol').value)});if(livePaper.pending&&livePaper.pending.symbol!==d.symbol||livePaper.position&&livePaper.position.symbol!==d.symbol)throw Error('Refresh the symbol of the active paper order first');intradayResponse=d;for(let bar of d.bars)livePaper.step(bar);save();renderIntraday();}
    catch(e){labGet('labStatus').textContent='Five-minute data/model unavailable: '+e.message+'. No daily data relabeled as intraday. Daily mode remains available.';if(!intradayResponse)labGet('labResult').hidden=true;}
    finally{requestBusy=false;b.disabled=false;}
  }
  labGet('paperExecute').onclick=()=>{try{let last=currentBars().at(-1),m=labData;book().queue(labData.symbol,Number(labGet('paperQty').value),m.entry,m.stop,m.target,last);save();renderPaper();}catch(e){labGet('paperStatus').textContent='Paper order rejected: '+e.message;}};
  labGet('paperQty').oninput=renderPaper;
  labGet('paperRefresh').onclick=()=>{practice=null;mode='5m';labGet('intraMode').value='5m';loadIntraday();};
  labGet('paperPractice').onclick=()=>{
    if(!intradayResponse){labGet('paperStatus').textContent='Load five-minute candles first.';return;}
    let bars=intradayResponse.bars,groups=new Map();bars.forEach((b,i)=>{if(!groups.has(b.session))groups.set(b.session,[]);groups.get(b.session).push(i);});
    let selected=null;
    for(let indices of [...groups.values()].reverse()){
      if(indices.length!==78||indices[0]<80||!bars[indices[0]].date.includes('T09:30:'))continue;
      let range=Math.max(...indices.slice(0,3).map(i=>bars[i].h));let cursor=indices.find((i,j)=>j>=3&&j<indices.length-1&&bars[i].c>range);
      if(cursor!==undefined){selected={bars:bars.slice(0,indices.at(-1)+1),cursor};break;}
    }
    if(!selected){labGet('paperStatus').textContent='No completed-session breakout example in this real feed. No example fabricated.';return;}
    practice=selected;replayPaper=new IntradayPaper();mode='5m';labGet('intraMode').value='5m';renderIntraday();
  };
  labGet('paperNext').onclick=()=>{if(!practice||practice.cursor>=practice.bars.length-1)return;practice.cursor++;replayPaper.step(practice.bars[practice.cursor]);renderIntraday();};
  labGet('paperExport').onclick=()=>download({app:'Trading Lab',mode:'paper-only',account:book(),historicalPractice:!!practice},'trading-lab-paper-executions.json');
  // Render a true five-minute fan; original daily rendering remains intact.
  const originalFan=labPaintFan;
  labPaintFan=function(id='labFan',ticket=false){if(id==='labTicketCanvas'){drawPaper();return;}originalFan(id,ticket);if(labData?.timeframe==='5m'){let s=labCanvas(id);if(!s)return; /* restore chart after canvas sizing */ originalFan(id,ticket);let c=labGet(id),ctx=c.getContext('2d'),w=c.clientWidth,h=c.clientHeight;ctx.fillStyle='#0b1525';ctx.fillRect(0,h-37,w,37);ctx.fillStyle='#b5cde8';ctx.fillText('Past 5-minute candles',12,h-14);ctx.textAlign='right';ctx.fillText('+'+labData.horizon+' × 5m · modeled close',w-110,h-14);ctx.textAlign='left';}};
  const originalRender=labRender;labRender=function(){originalRender();renderPaper();};
  const originalDaily=labLoad;labLoad=async function(){await originalDaily();if(labData){labGet('labCountTitle').nextElementSibling.textContent='simulated paths';renderPaper();}};
  labGet('labExport').onclick=()=>{if(!labData)return;let m=labData;download({app:'Trading Lab',mode:'paper-only',symbol:labData.symbol,timeframe:m.timeframe||'daily',asOf:m.asOf,horizon:m.horizon,count:m.count,seed:m.seed,entry:m.entry,stop:m.stop,target:m.target,fan:m.fan,bins:m.bins,exits:m.exits,backtest:m.backtest,source:m.source,paths:m.paths.map(p=>p.closes),limitations:'Unvalidated bootstrap frequencies, not calibrated odds. Future news, gaps, costs and liquidity not modeled.'},'trading-lab-scenarios.json');};
  // Original pixel-bull illustrations inspired by the reference, not remote image placeholders.
  function bull(icon,color){return '<svg viewBox="0 0 150 105" aria-hidden="true"><path fill="#d98669" d="M24 43h21V24h63v15h17v22h-12v26h-11V68H87v19H76V68H61v19H50V61H24z"/><rect x="52" y="35" width="7" height="7" fill="#292b2b"/><rect x="93" y="35" width="7" height="7" fill="#292b2b"/><path d="M43 24V13h10v11M98 24V13h10v11" fill="#d98669"/><rect x="106" y="4" width="38" height="36" rx="6" fill="'+color+'" stroke="#35464c" stroke-width="2"/><text x="125" y="30" text-anchor="middle" font-size="23" fill="white">'+icon+'</text></svg>';}
  let tools=[['terminal','Chart','▥','#399979'],['liveNews','News Monitor','▤','#496774'],['future','Entry','◴','#77989b'],['future','Exit Planner','⇥','#328557'],['future','Time Machine','◷','#b18a38'],['risk','Risk Manager','✓','#426491'],['risk','Budget Manager','▦','#568276'],['future','Order Executor','▣','#596c87'],['alerts','Price Alerts','♧','#b79937'],['earnings','Earnings Analyst','▥','#69965d'],['congress','Pelosi Tracker','◎','#41474d']];
  let grid=labGet('labToolsGrid');grid.replaceChildren();tools.forEach(([id,label,icon,color],i)=>{if(i===6){let banner=document.createElement('div');banner.className='tool-banner';banner.textContent='A DIFFERENT JOB';grid.append(banner);}let b=document.createElement('button');b.className='lab-tool';b.innerHTML=bull(icon,color)+'<strong>'+label+'</strong>';b.onclick=()=>{show(labGet(id)?id:'engines');if(label==='Order Executor')labGet('paperTicket').scrollIntoView({behavior:'smooth'});};grid.append(b);});
  const more=document.createElement('details');more.className='lab-fold';more.innerHTML='<summary>More tools & historical practice</summary>';['replay','screener','whale','rebalance','tax','engines','about'].filter(id=>labGet(id)).forEach(id=>{let b=document.createElement('button');b.textContent=labGet(id).querySelector('h1')?.textContent||id;b.onclick=()=>show(id);more.append(b);});labGet('tools').append(more);
  // Add transparent source and relative freshness to each real headline card.
  new MutationObserver(()=>{document.querySelectorAll('.lab-news').forEach(card=>{if(card.querySelector('.source-line'))return;let a=card.querySelector('h2 a'),published=card.querySelector('p')?.textContent.match(/published ([0-9T:+.-]+)/)?.[1],stamp=published?Date.parse(published):NaN,line=document.createElement('div');line.className='source-line';let source='Public Yahoo RSS';try{if(a)source=new URL(a.href).hostname;}catch{}let age=Number.isFinite(stamp)?Math.max(0,Math.floor((Date.now()-stamp)/60000)):null;line.textContent='Source: '+source+' · '+(age===null?'Publication age unavailable':age<60?age+' minutes old':Math.floor(age/60)+' hours old')+' · Headline wording only; affected tickers and price impact are uncertain.';card.append(line);});}).observe(labGet('labNewsFeed'),{childList:true});
  const baseTests=labSelfTests;
  labSelfTests=function(){return baseTests().concat(intraTests());};
  window.addEventListener('resize',drawPaper);
  labGet('labStatus').textContent='Real five-minute candles feed forward session simulations. Delayed data is labeled. Paper trading only; never a guaranteed forecast.';
}
function intraFixtures(){let bars=[];for(let day=1;day<=5;day++)for(let i=0;i<78;i++){let t=Date.UTC(2026,9,day,13,30)+i*300000,p=100+Math.sin(i/4)*.4+day*.1+i*.005;bars.push({t:t/1000,date:new Date(t).toISOString(),session:'2026-10-'+String(day).padStart(2,'0'),o:p,h:p+.2,l:p-.2,c:p+.05});}return bars;}
function intraTests(){
  let out=[],test=(n,f)=>{if(!f())throw Error('FAILED '+n);out.push('PASS '+n);},bars=intraFixtures(),m=intraModel(bars,20,1000);
  test('Five-minute exact count',()=>m.paths.length===1000&&m.paths.every(p=>p.closes.length===21));
  test('Five-minute latest candle origin',()=>m.paths.every(p=>p.closes[0]===bars.at(-1).c));
  test('Within-session seeded determinism',()=>JSON.stringify(m.fan)===JSON.stringify(intraModel(bars,20,1000).fan));
  test('Five-minute histogram exhausts all paths',()=>m.bins.reduce((n,b)=>n+b.count,0)===1000);
  test('Five-minute exits exhaust all paths',()=>Object.values(m.exits).reduce((a,b)=>a+b,0)===1000);
  test('Overnight gap not sampled',()=>{let altered=bars.map(b=>({...b,o:b.o,c:b.c}));return m.sampleSize<bars.length;});
  test('As-of boundary excludes future',()=>JSON.stringify(m.fan)===JSON.stringify(intraModel(bars.concat([{...bars.at(-1),t:9e9,c:999}]).slice(0,bars.length),20,1000).fan));
  test('Paper next-open not signal close',()=>{let p=new IntradayPaper();p.queue('TSLA',1,100,95,110,{t:1,date:'signal'});p.step({t:2,date:'next',o:101,h:103,l:100,c:102});return p.position.entry===101&&p.cash===9899;});
  test('No fill on current candle',()=>{let p=new IntradayPaper();p.queue('TSLA',1,100,95,110,{t:1,date:'signal'});p.step({t:1,o:100,h:101,l:99,c:100});return !!p.pending&&!p.position;});
  test('Paper both levels prioritizes stop',()=>{let p=new IntradayPaper();p.queue('TSLA',1,100,95,110,{t:1,date:'signal'});p.step({t:2,date:'next',o:100,h:111,l:94,c:102});return p.cash===9995&&p.ledger.at(-1).reason==='stop';});
  test('Paper gap-stop worse open',()=>{let p=new IntradayPaper();p.queue('TSLA',1,100,95,110,{t:1,date:'signal'});p.step({t:2,date:'fill',o:100,h:103,l:99,c:102});p.step({t:3,date:'gap',o:90,h:92,l:88,c:91});return p.cash===9990;});
  test('Reject invalid opening bracket',()=>{let p=new IntradayPaper();p.queue('TSLA',1,100,95,110,{t:1,date:'signal'});p.step({t:2,date:'gap',o:120,h:121,l:119,c:120});return !p.position&&p.cash===10000&&p.ledger.at(-1).side==='REJECTED';});
  test('Whole shares only',()=>{try{new IntradayPaper().queue('TSLA',.5,100,95,110,{t:1});return false;}catch{return true;}});
  test('One-percent risk limit',()=>{try{new IntradayPaper().queue('TSLA',30,100,95,110,{t:1});return false;}catch{return true;}});
  test('No duplicate processing',()=>{let p=new IntradayPaper();p.queue('TSLA',1,100,95,110,{t:1,date:'signal'});let b={t:2,date:'fill',o:100,h:101,l:99,c:100};p.step(b);p.step(b);return p.cash===9900&&p.ledger.filter(e=>e.side==='BUY').length===1;});
  test('Insufficient intraday history rejected',()=>{try{intraModel(bars.slice(0,79),20,1000);return false;}catch{return true;}});
  test('Intraday malformed candle rejected',()=>{try{intraModel(bars.map((b,i)=>i?b:{...b,l:999}),20,1000);return false;}catch{return true;}});
  test('Maximum 78-interval model',()=>intraModel(bars,78,1000).fan.length===79);
  return out;
}
if(typeof module!=='undefined')module.exports={intraModel,intraExit,IntradayPaper,intraTests};
