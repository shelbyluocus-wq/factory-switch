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
  const accounts=()=>state?.saved_accounts||[];
  const currentId=()=>state?.local_identity?.account_id;
  function status(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error);}
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
    document.querySelectorAll('main button').forEach(button=>button.disabled=value||!ready||(!state&&button.id!=='refresh'));
    if(!value)render();
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
    dialog(`切换到 ${account.label}`,`<p>将保存当前账号并重启 Factory。<br>请先完成正在运行的任务。</p>`,'切换',()=>run('switch_account',[account.account_id],'正在切换…','已切换，Factory 正在启动'));
  }
  function addAccount(){
    dialog('添加账号','<p>将保存当前账号并重启 Factory。<br>登录新账号后，回来点击「保存当前账号」。</p>','前往登录',()=>run('begin_login',[],'正在准备登录…','请在 Factory 登录新账号'));
  }
  function init(){
    document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
    $('save').onclick=saveAccount;$('add').onclick=addAccount;$('refresh').onclick=()=>run('get_state',[],'','账号已刷新',true);
    $('cancel').onclick=closeDialog;
    $('modal').addEventListener('cancel',event=>{event.preventDefault();closeDialog();});
    $('modal-form').onsubmit=event=>{event.preventDefault();const action=modalAction;action?.();closeDialog();};
    document.querySelectorAll('[data-window]').forEach(button=>button.onclick=async()=>{
      if(!preview&&window.pywebview?.api){const result=await window.pywebview.api.window_action(button.dataset.window);if(!result.ok&&result.error)status(result.error,true);}
    });
    document.querySelector('.titlebar-drag').ondblclick=()=>{if(!preview)window.pywebview?.api.window_action('maximize');};
    document.addEventListener('pointerdown',()=>document.body.classList.remove('keyboard'));
    document.addEventListener('keydown',event=>{document.body.classList.add('keyboard');if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='r'){event.preventDefault();if(!$('modal').open)run('get_state',[],'','账号已刷新',true);}});
    setBusy(false);
    if(preview){
      state={local_identity:{account_id:'a'.repeat(24),email:'alex.chen@example.com'},saved_accounts:[{account_id:'a'.repeat(24),label:'个人账号',email:'alex.chen@example.com'},{account_id:'b'.repeat(24),label:'工作账号',email:'alex@studio.example'}]};
      usageCache.set('a'.repeat(24),{data:{checked_at:new Date().toISOString(),pools:[{label:'Standard',windows:[{label:'5 小时',used_percent:12},{label:'周',used_percent:70,resets_at:'2026-10-06T10:33:10Z'},{label:'月',used_percent:35}]},{label:'Droid Core',windows:[{label:'5 小时',used_percent:0},{label:'周',used_percent:6},{label:'月',used_percent:4}]}]}});
      usageCache.set('b'.repeat(24),{data:{checked_at:new Date().toISOString(),pools:[{label:'Standard',scope:'组织额度',used:0,limit:20000000,used_percent:0}]}});
      ready=true;setBusy(false);status('设计预览 · 示例账号');
    }else{
      const connect=()=>{if(ready)return;ready=true;run('get_state');};
      if(window.pywebview?.api)connect();else window.addEventListener('pywebviewready',connect,{once:true});
      setTimeout(()=>{if(!ready){$('account-list').innerHTML='<div class="empty">请双击 Launch.cmd 启动桌面工具。</div>';status('尚未连接桌面服务');}},6000);
    }
    window.addEventListener('focus',()=>{if(ready&&!busy&&!$('modal').open&&!preview)run('get_state');});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
