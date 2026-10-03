(() => {
  'use strict';
  const paths={plus:'<path d="M12 5v14M5 12h14"/>',save:'<path d="M12 3v12m-4-4 4 4 4-4M4 16v4h16v-4"/>',refresh:'<path d="M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1"/>'};
  const icon=name=>`<svg viewBox="0 0 24 24" aria-hidden="true">${paths[name]||''}</svg>`;
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const $=id=>document.getElementById(id);
  const preview=new URLSearchParams(location.search).get('preview')==='1'||document.documentElement.dataset.preview==='true';
  let state=null,ready=false,busy=false,modalAction=null,priorFocus;
  const usageCache=new Map();
  let usageRunning=false,usageQueued=false;
  let activeView='accounts',localData=null,localRunning=false,localAttempted=0,localError='',localPeriod='all';
  const periodNames={today:'今日','7d':'近 7 天','30d':'近 30 天',all:'全部'};
  const localFields=[['inputTokens','输入 Token'],['outputTokens','输出 Token'],['cacheReadTokens','缓存读取'],['cacheCreationTokens','缓存创建'],['thinkingTokens','思考 Token'],['factoryCredits','Factory Credits']];
  const exactCounter=value=>Number.isSafeInteger(value)&&value>=0?new Intl.NumberFormat('zh-CN').format(value):'未记录';
  function formatCounter(value){
    if(!Number.isSafeInteger(value)||value<0)return '—';
    const unit=value>=100000000||(value>=10000&&Math.round(value/100)>=1000000)?100000000:value>=10000?10000:1;
    if(unit===1)return new Intl.NumberFormat('zh-CN').format(value);
    return new Intl.NumberFormat('zh-CN',{maximumFractionDigits:2,useGrouping:false}).format(value/unit)+(unit===100000000?'亿':'万');
  }
  const accounts=()=>state?.saved_accounts||[];
  const currentId=()=>state?.local_identity?.account_id;
  function status(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error);}
  function localDate(value){
    if(!value)return '活动时间未记录';
    const time=new Date(value);
    return Number.isNaN(time.getTime())?'活动时间未记录':time.toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit',hour12:false});
  }
  function localModel(session){
    if(session.models?.length>1)return '混合模型';
    if(session.models?.length)return session.models[0];
    return session.model_setting==='auto'?'自动（当前设置）':session.model_setting?`${session.model_setting}（当前设置）`:'模型未记录';
  }
  function renderLocalUsage(){
    const root=$('local-usage');
    if(!localData){root.innerHTML=`<div class="empty">${esc(localError||'正在读取本机用量…')}</div>`;return;}
    const ranged=localPeriod!=='all',data=ranged?localData.periods?.[localPeriod]:localData;
    if(!data){root.innerHTML='<div class="empty">暂无可用请求日志，无法按时间统计。<p>可选择「全部」查看会话累计。</p></div>';return;}
    const open=new Set([...root.querySelectorAll('details[open]')].map(el=>el.dataset.session));
    const metrics=localFields.map(([field,label])=>{
      const covered=data.field_counts[field],partial=covered<data.session_count;
      const note=ranged&&field==='factoryCredits'?'请求日志未提供':data.totals[field]===null?'未记录':partial?`部分记录 · ${covered}/${data.session_count} 个会话`:field==='factoryCredits'?'客户端原始值':ranged?'所选时间内的请求':'现存会话累计';
      return `<div class="local-metric"><span class="local-metric-label">${label}</span><b class="local-metric-value" title="${esc(`${label}：${exactCounter(data.totals[field])}`)}">${formatCounter(data.totals[field])}</b><span class="local-metric-note">${esc(note)}</span></div>`;
    }).join('');
    const totalNote=ranged&&!data.available?'暂无可用请求日志，无法按时间统计':data.total_tokens==null?(data.total_session_count>0?'总量超出显示精度':'没有完整的 Token 记录'):data.total_session_count<data.session_count?`部分记录 · ${data.total_session_count}/${data.session_count} 个会话`:ranged?`${periodNames[localPeriod]} · 按请求日志统计 · 含缓存与思考`:'现存会话累计 · 含缓存与思考';
    const totalTitle=`${exactCounter(data.total_tokens)} Tokens；按 Factory 本地统计口径：输入 + 输出 + 缓存创建 + 缓存读取 + 思考，不含 Factory Credits`;
    const hero=`<div class="local-total"><span class="local-total-label">真实消耗 Tokens</span><b class="local-total-value" title="${esc(totalTitle)}">${formatCounter(data.total_tokens)}</b><span class="local-total-note">${esc(totalNote)}</span></div>`;
    const warnings=[];
    if(localError)warnings.push(`上次数据 · ${localError}`);
    if(data.skipped_count)warnings.push(`${data.skipped_count} 份记录暂时无法读取，仅汇总可用记录`);
    if(data.duplicate_count)warnings.push(`已忽略 ${data.duplicate_count} 份重复会话快照`);
    if(ranged&&data.available&&!data.reconciled)warnings.push('请求日志与会话累计有差异，时间统计仅覆盖现存日志；「全部」查看会话累计');
    if(ranged&&data.skipped_log_count)warnings.push(`${data.skipped_log_count} 份日志暂时无法读取`);
    if(ranged&&data.invalid_log_count)warnings.push(`${data.invalid_log_count} 条请求记录不完整，未计入`);
    if(ranged&&data.conflicting_log_count)warnings.push(`${data.conflicting_log_count} 条重复请求数值冲突，已去重`);
    const sessions=data.sessions.map(session=>{
      const title=session.title||`会话 ${session.session_id.slice(0,8)}`;
      const details=localFields.map(([field,label])=>`<span>${label}<b title="${esc(`${label}：${exactCounter(session.usage[field])}`)}">${formatCounter(session.usage[field])}</b></span>`).join('');
      const modelNote=session.models?.length>1?`使用过：${session.models.join('、')}。${ranged?'':'累计记录无法准确拆分到各模型。'}`:'';
      return `<details class="local-session" data-session="${esc(session.session_id)}" ${open.has(session.session_id)?'open':''}><summary><span class="local-session-copy"><span class="local-session-title" title="${esc(title)}">${esc(title)}</span><span class="local-session-meta">${esc(localModel(session))} · ${ranged?`${session.request_count} 次请求 · `:''}${esc(localDate(session.last_active_at))}</span></span><span class="local-session-value" title="${esc(`${exactCounter(session.total_tokens)} Tokens`)}">${formatCounter(session.total_tokens)}<small>真实消耗 Tokens</small></span></summary><div class="local-session-details"><div class="local-session-metrics">${details}</div><p>${esc(modelNote)}${modelNote?'<br>':''}${ranged?'范围内首条请求':'首次活动'}：${esc(localDate(session.started_at))}<br>会话 ID：${esc(session.session_id)}</p></div></details>`;
    }).join('');
    const caption=ranged?`${esc(data.range_start)} 至 ${esc(data.range_end)} · 本机日期 · ${data.available?`${data.request_count} 次请求 · ${data.session_count} 个会话`:'无可用请求记录'}`:`${data.session_count} 个会话 · ${data.used_session_count} 个有消耗`;
    const explanation=ranged?'按现存请求日志的时间统计，近 7 / 30 天包含今日。包含缓存与思考，日志未提供 Factory Credits。悬停查看完整数字。':'按 Factory 本地总 Token 口径统计，包含缓存与思考；Factory Credits 单列。悬停查看完整数字。时间筛选使用请求日志；会话累计不按模型或账号精确拆分。';
    const empty=ranged?(data.available?'所选时间没有请求记录':'暂无可用请求日志，无法按时间统计'):'还没有本机会话记录';
    root.innerHTML=`<p class="local-caption">${caption} · ${localRunning?'正在更新…':`${esc(localDate(data.checked_at))} 更新`}</p>${warnings.length?`<p class="local-caption local-warning">${esc(warnings.join('；'))}</p>`:''}${hero}<div class="local-metrics">${metrics}</div><p class="local-explanation">${explanation}</p><h2 class="local-section-title">会话明细 <span class="local-caption">· 点击展开</span></h2><div class="local-session-list">${sessions||`<div class="empty">${empty}<p>${ranged?'可选择「全部」查看会话累计。':'在 Droid 使用后，点击右下角刷新。'}</p></div>`}</div>`;
  }
  function updateRefresh(){
    $('refresh').disabled=busy||!ready||(activeView==='local'&&localRunning);
    $('refresh').classList.toggle('loading-local',activeView==='local'&&localRunning);
  }
  async function refreshLocalUsage(force=false){
    if(preview){renderLocalUsage();return;}
    if(!ready||localRunning||(!force&&localData&&Date.now()-localAttempted<15000))return;
    localRunning=true;localAttempted=Date.now();updateRefresh();renderLocalUsage();
    try{
      const result=await window.pywebview.api.get_local_usage();
      if(!result?.ok)throw new Error(result?.error||'本机用量读取失败');
      localData=result.usage;localError='';
    }catch(error){localError=error.message||'本机用量读取失败，请刷新重试。';}
    finally{localRunning=false;updateRefresh();renderLocalUsage();}
  }
  function showView(view){
    if(busy||!ready)return;
    activeView=view;
    for(const [name,tab,panel] of [['accounts','tab-accounts','accounts-panel'],['local','tab-local','local-panel']]){
      const active=view===name;$(tab).setAttribute('aria-selected',String(active));$(tab).tabIndex=active?0:-1;$(panel).hidden=!active;
    }
    $('add').hidden=$('save').hidden=view==='local';
    $('refresh').title=view==='local'?'刷新本机用量':'刷新账号和用量';
    $('refresh').setAttribute('aria-label',$('refresh').title);
    status(preview?'设计预览 · 示例数据':'');updateRefresh();
    if(view==='local')refreshLocalUsage();
    else refreshUsage();
  }
  function refreshCurrent(){
    if(activeView==='local')refreshLocalUsage(true);
    else run('get_state',[],'','账号已刷新',true);
  }
  function resetText(value){
    if(!value)return '未提供重置时间';
    const time=new Date(value);
    return Number.isNaN(time.getTime())?'重置时间未知':`${time.toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'})} 重置`;
  }
  function meter(percent,label,title){
    const valid=Number.isFinite(percent),width=valid?Math.min(100,Math.max(0,percent)):0;
    return `<span class="usage-meter ${width>=90?'high':''}" title="${esc(title)}"><span class="usage-value">${esc(label)} <b>${valid?`${Math.round(percent*10)/10}%`:'—'}</b></span><span class="usage-track" aria-hidden="true"><i style="width:${width}%"></i></span></span>`;
  }
  function usageHtml(id){
    const entry=usageCache.get(id);
    if(!entry)return '<span class="usage-note">用量待查询</span>';
    const data=entry.data;
    const pools=data?.pools||[];
    const body=pools.map(pool=>{
      if(pool.windows)return `<div class="usage-pool"><span class="usage-label">${esc(pool.label)}</span><div class="usage-windows">${pool.windows.map(w=>meter(w.used_percent,w.label,`已用 ${w.used_percent}% · ${resetText(w.resets_at)}`)).join('')}</div></div>`;
      const fmt=n=>new Intl.NumberFormat('zh-CN',{maximumFractionDigits:0}).format(n);
      return `<div class="usage-pool"><span class="usage-label">${esc(pool.label)}</span><div class="usage-quota">${meter(pool.used_percent,pool.scope,resetText(pool.resets_at))}<span>${fmt(pool.used)} / ${fmt(pool.limit)}</span></div></div>`;
    }).join('');
    const checked=data?.checked_at?new Date(data.checked_at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'';
    const note=entry.loading?'正在查询用量…':entry.error?`${pools.length?'上次数据 · ':''}${entry.error}`:`已用比例 · ${checked} 更新 · 悬停查看重置时间`;
    return `${body}<span class="usage-note ${entry.error?'usage-error':''}">${esc(note)}</span>`;
  }
  function renderUsage(){
    document.querySelectorAll('[data-usage]').forEach(el=>{el.innerHTML=usageHtml(el.dataset.usage);});
  }
  async function refreshUsage(force=false){
    if(preview||!ready||!state)return;
    if(usageRunning){usageQueued=usageQueued||force;return;}
    usageRunning=true;
    try{
      for(const account of accounts()){
        const id=account.account_id,old=usageCache.get(id);
        if(!force&&old&&Date.now()-old.attempted<60000)continue;
        const entry={...old,loading:true,attempted:Date.now()};
        usageCache.set(id,entry);renderUsage();
        try{
          const result=await window.pywebview.api.get_usage(id);
          if(!result?.ok)throw new Error(result?.error||'用量查询失败');
          if(result.usage.status==='ok'){entry.data=result.usage;entry.error='';}
          else entry.error=result.usage.error||'暂无用量数据';
        }catch(error){entry.error=error.message||'用量查询失败';}
        finally{entry.loading=false;renderUsage();}
      }
    }finally{
      usageRunning=false;
      if(usageQueued){usageQueued=false;refreshUsage(true);}
    }
  }
  function render(){
    if(!state)return;
    const focused=document.activeElement?.dataset.switch;
    $('account-list').innerHTML=accounts().length?accounts().map((a,i)=>`<div class="account-row"><span class="avatar ${i%2?'teal':''}" aria-hidden="true">${esc((a.email||a.label||'?').slice(0,2).toUpperCase())}</span><div class="account-copy"><span class="account-name">${esc(a.label)}</span><span class="account-email">${esc(a.email||'已保存的账号')}</span></div>${a.account_id===currentId()?'<span class="current">使用中</span>':`<button class="button row-action" data-switch="${esc(a.account_id)}" aria-label="切换到${esc(a.label)}">切换</button>`}<div class="account-usage" data-usage="${esc(a.account_id)}">${usageHtml(a.account_id)}</div></div>`).join(''):'<div class="empty"><strong>还没有保存的账号</strong><p>在 Factory 登录后，保存当前账号。</p></div>';
    $('account-list').querySelectorAll('[data-switch]').forEach(button=>{
      button.disabled=busy;button.onclick=()=>switchAccount(accounts().find(a=>a.account_id===button.dataset.switch));
      if(button.dataset.switch===focused)button.focus({preventScroll:true});
    });
    $('save').disabled=busy||!currentId();
    $('add').disabled=busy||!currentId();
    $('add').title=currentId()?'添加另一个账号':'请先在 Factory 完成登录';
    $('login-hint').hidden=Boolean(currentId()&&accounts().some(a=>a.account_id===currentId()));
  }
  function setBusy(value){
    busy=value;document.body.classList.toggle('busy',value);$('account-list').setAttribute('aria-busy',String(value));
    document.querySelectorAll('main button').forEach(button=>button.disabled=value||!ready||(!state&&button.id!=='refresh'&&button.getAttribute('role')!=='tab'&&!button.dataset.period));
    if(!value)render();
    updateRefresh();
  }
  async function run(method,args=[],message='',success='',forceUsage=false){
    if(busy||!ready)return;
    if(preview&&method!=='get_state'){status('设计预览，不操作真实账号');return;}
    if(message)status(message);setBusy(true);
    try{
      const result=preview?{ok:true,state}:await window.pywebview.api[method](...args);
      if(!result?.ok)throw new Error(result?.error||'操作未完成，请重试。');
      state=result.state;
      if(success)status(success);
    }catch(error){
      status(error.message||'连接失败，请重试。',true);
      if(!state)$('account-list').innerHTML='<div class="empty">无法读取账号，请点击右下角刷新。</div>';
    }finally{setBusy(false);refreshUsage(forceUsage||method!=='get_state');}
  }
  function closeDialog(){ $('modal').close();modalAction=null;priorFocus?.focus({preventScroll:true}); }
  function dialog(title,body,confirm,action,focus){
    if(busy||$('modal').open)return;
    priorFocus=document.activeElement;modalAction=action;
    $('modal-title').textContent=title;$('modal-body').innerHTML=body;$('confirm').textContent=confirm;
    $('modal').showModal();requestAnimationFrame(()=>focus?$(focus).focus():$('confirm').focus());
  }
  function saveAccount(){
    if(!currentId())return;
    const active=accounts().find(a=>a.account_id===currentId());
    dialog('保存当前账号',`<p>${esc(state.local_identity.email||'当前账号')}</p><label class="field-label" for="account-label">备注（选填）</label><input class="modal-input" id="account-label" maxlength="64" value="${esc(active?.label||'')}" placeholder="例如：个人账号">`,'保存',()=>run('save_account',[$('account-label').value],'正在保存…','已保存'),'account-label');
  }
  function switchAccount(account){
    if(!account)return;
    dialog(`切换到 ${account.label}`,`<p>将保存当前账号并重启 Factory，所有账号共用本机历史会话。<br>请先完成正在运行的任务。</p>`,'切换',()=>run('switch_account',[account.account_id],'正在切换…','已切换，Factory 正在启动'));
  }
  function addAccount(){
    dialog('添加账号','<p>将保存当前账号并重启 Factory，所有账号共用本机历史会话。<br>登录新账号后，回来点击「保存当前账号」。</p>','前往登录',()=>run('begin_login',[],'正在准备登录…','请在 Factory 登录新账号'));
  }
  function init(){
    document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
    $('save').onclick=saveAccount;$('add').onclick=addAccount;$('refresh').onclick=refreshCurrent;
    $('tab-accounts').onclick=()=>showView('accounts');$('tab-local').onclick=()=>showView('local');
    document.querySelectorAll('[data-period]').forEach(button=>button.onclick=()=>{
      localPeriod=button.dataset.period;
      document.querySelectorAll('[data-period]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
      renderLocalUsage();
    });
    document.querySelector('.view-tabs').addEventListener('keydown',event=>{
      if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
        event.preventDefault();const view=event.key==='Home'?'accounts':event.key==='End'?'local':activeView==='accounts'?'local':'accounts';
        showView(view);$(view==='accounts'?'tab-accounts':'tab-local').focus();
      }
    });
    $('cancel').onclick=closeDialog;
    $('modal').addEventListener('cancel',event=>{event.preventDefault();closeDialog();});
    $('modal-form').onsubmit=event=>{event.preventDefault();const action=modalAction;action?.();closeDialog();};
    document.querySelectorAll('[data-window]').forEach(button=>button.onclick=async()=>{
      if(!preview&&window.pywebview?.api){const result=await window.pywebview.api.window_action(button.dataset.window);if(!result.ok&&result.error)status(result.error,true);}
    });
    document.querySelector('.titlebar-drag').ondblclick=()=>{if(!preview)window.pywebview?.api.window_action('maximize');};
    document.addEventListener('pointerdown',()=>document.body.classList.remove('keyboard'));
    document.addEventListener('keydown',event=>{document.body.classList.add('keyboard');if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='r'){event.preventDefault();if(!$('modal').open)refreshCurrent();}});
    setBusy(false);
    if(preview){
      state={local_identity:{account_id:'a'.repeat(24),email:'alex.chen@example.com'},saved_accounts:[{account_id:'a'.repeat(24),label:'个人账号',email:'alex.chen@example.com'},{account_id:'b'.repeat(24),label:'工作账号',email:'alex@studio.example'}]};
      usageCache.set('a'.repeat(24),{data:{checked_at:new Date().toISOString(),pools:[{label:'Standard',windows:[{label:'5 小时',used_percent:12},{label:'周',used_percent:70,resets_at:'2026-10-06T10:33:10Z'},{label:'月',used_percent:35}]},{label:'Droid Core',windows:[{label:'5 小时',used_percent:0},{label:'周',used_percent:6},{label:'月',used_percent:4}]}]}});
      usageCache.set('b'.repeat(24),{data:{checked_at:new Date().toISOString(),pools:[{label:'Standard',scope:'组织额度',used:0,limit:20000000,used_percent:0}]}});
      const sampleUsage={inputTokens:2857548,outputTokens:1488469,cacheCreationTokens:5337946,cacheReadTokens:170782418,thinkingTokens:249440,factoryCredits:40766897};
      localData={checked_at:new Date().toISOString(),session_count:3,used_session_count:3,skipped_count:0,duplicate_count:0,totals:sampleUsage,field_counts:Object.fromEntries(localFields.map(([field])=>[field,3])),sessions:[{session_id:'sample-1',title:'修复构建问题并核对发布包',models:['claude-opus-5-5'],started_at:'2026-10-03T08:38:34Z',last_active_at:'2026-10-03T09:50:56Z',usage:{inputTokens:522018,outputTokens:280558,cacheCreationTokens:697823,cacheReadTokens:29550315,thinkingTokens:55526,factoryCredits:6838875}},{session_id:'sample-2',title:'界面调整与会话共享验证',models:['claude-opus-5-5','kimi-k3'],started_at:'2026-10-02T09:09:09Z',last_active_at:'2026-10-02T12:19:00Z',usage:{inputTokens:968620,outputTokens:544509,cacheCreationTokens:1598135,cacheReadTokens:46105069,thinkingTokens:102370,factoryCredits:12795838}},{session_id:'sample-3',title:'整理项目并检查配置',models:['claude-opus-5-5','kimi-k3'],started_at:'2026-10-01T04:59:30Z',last_active_at:'2026-10-02T04:41:36Z',usage:{inputTokens:1366910,outputTokens:663402,cacheCreationTokens:3041988,cacheReadTokens:95127034,thinkingTokens:91544,factoryCredits:21132184}}]};
      localData.total_tokens=localFields.filter(([field])=>field!=='factoryCredits').reduce((sum,[field])=>sum+sampleUsage[field],0);
      localData.total_session_count=localData.session_count;
      localData.sessions.forEach(session=>{session.total_tokens=localFields.filter(([field])=>field!=='factoryCredits').reduce((sum,[field])=>sum+session.usage[field],0);});
      localData.periods={};
      const previewNow=new Date(),dateString=date=>`${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
      localData.sessions.forEach((row,index)=>{const when=new Date(previewNow);when.setDate(when.getDate()-[0,3,14][index]);row.started_at=row.last_active_at=when.toISOString();});
      for(const [key,days,count] of [['today',1,1],['7d',7,2],['30d',30,3]]){
        const rows=localData.sessions.slice(0,count).map((row,index)=>({...row,request_count:10+index,usage:{...row.usage,factoryCredits:null}}));
        const totals=Object.fromEntries(localFields.map(([field])=>[field,field==='factoryCredits'?null:rows.reduce((sum,row)=>sum+row.usage[field],0)]));
        const start=new Date(previewNow);start.setDate(start.getDate()-days+1);
        localData.periods[key]={checked_at:localData.checked_at,source:'request_logs',available:true,reconciled:true,range_start:dateString(start),range_end:dateString(previewNow),session_count:count,used_session_count:count,total_session_count:count,request_count:rows.reduce((sum,row)=>sum+row.request_count,0),totals,field_counts:Object.fromEntries(localFields.map(([field])=>[field,field==='factoryCredits'?0:count])),total_tokens:rows.reduce((sum,row)=>sum+row.total_tokens,0),sessions:rows};
      }
      ready=true;setBusy(false);status('设计预览 · 示例账号');
    }else{
      const connect=()=>{if(ready)return;ready=true;run('get_state');};
      if(window.pywebview?.api)connect();else window.addEventListener('pywebviewready',connect,{once:true});
      setTimeout(()=>{if(!ready){$('account-list').innerHTML='<div class="empty">请双击 Launch.cmd 启动桌面工具。</div>';status('尚未连接桌面服务');}},6000);
    }
    window.addEventListener('focus',()=>{if(ready&&!busy&&!$('modal').open&&!preview){if(activeView==='local')refreshLocalUsage();else run('get_state');}});
    setInterval(()=>{if(activeView==='local'&&document.visibilityState!=='hidden'&&!busy&&!$('modal').open&&!preview)refreshLocalUsage();},15000);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
