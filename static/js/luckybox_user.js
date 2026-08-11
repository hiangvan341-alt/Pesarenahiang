(function(){
  const page=document.querySelector('[data-lb3-page]');
  if(!page) return;

  const openButton=page.querySelector('[data-lb3-open]');
  const againButton=page.querySelector('[data-lb3-open-again]');
  const resultPanel=page.querySelector('[data-lb3-result]');
  const resultGrid=page.querySelector('[data-lb3-result-grid]');
  const resultNote=page.querySelector('[data-lb3-result-note]');
  const openingLink=page.querySelector('[data-lb3-opening-link]');
  const errorBox=page.querySelector('[data-lb3-error]');
  const balanceNode=page.querySelector('[data-lb3-balance]');

  const overlay=page.querySelector('[data-lb4-overlay]');
  const stage=page.querySelector('[data-lb4-stage]');
  const overlayRewards=page.querySelector('[data-lb4-rewards]');
  const overlayStatus=page.querySelector('[data-lb4-status]');
  const skipButton=page.querySelector('[data-lb4-skip]');
  const continueButton=page.querySelector('[data-lb4-continue]');

  const previewMode=page.dataset.previewMode==='1';
  const formatter=new Intl.NumberFormat('vi-VN');
  const reducedMotion=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const rarityOrder={common:1,rare:2,epic:3,elite:4,legendary:5};
  let skipRequested=false;
  let animationRunning=false;

  function requestId(){
    if(window.crypto&&crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{
      const r=Math.random()*16|0,v=c==='x'?r:(r&3|8);return v.toString(16);
    });
  }

  function escapeHtml(value){
    return String(value??'').replace(/[&<>'"]/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[ch]));
  }

  function showError(message){
    errorBox.textContent=message||'Không thể mở Lucky Box lúc này.';
    errorBox.hidden=false;
    window.setTimeout(()=>{errorBox.hidden=true;},5000);
  }

  function rewardCard(reward,index){
    const amount=Number(reward.reward_amount||0);
    const duplicate=Number(reward.duplicate_conversion||0);
    const rarity=reward.rarity_label||reward.reward_rarity||reward.rarity||'Phần thưởng';
    const image=reward.image_url||'';
    return `<article class="lb3-result-card rarity-${escapeHtml(reward.reward_rarity||reward.rarity||'common')}">
      ${image?`<img src="${escapeHtml(image)}" alt="${escapeHtml(reward.reward_name||'Phần thưởng')}">`:''}
      <small>Ô ${escapeHtml(reward.reward_slot||reward.slot||index+1)} · ${escapeHtml(rarity)}</small>
      <h3>${escapeHtml(reward.reward_name||'Phần thưởng')}</h3>
      ${amount?`<strong>${formatter.format(amount)} Zcoin</strong>`:''}
      ${duplicate?`<p>Đã quy đổi vật phẩm trùng: ${formatter.format(duplicate)} Zcoin</p>`:''}
    </article>`;
  }

  function overlayRewardCard(reward,index){
    const amount=Number(reward.reward_amount||0);
    const rarityCode=reward.reward_rarity||reward.rarity||'common';
    const rarity=reward.rarity_label||rarityCode||'Phần thưởng';
    const image=reward.image_url||'';
    return `<article class="lb4-reward-card rarity-${escapeHtml(rarityCode)}" data-lb4-card>
      <span class="lb4-slot">Ô ${escapeHtml(reward.reward_slot||reward.slot||index+1)}</span>
      <div class="lb4-reward-media">${image?`<img src="${escapeHtml(image)}" alt="${escapeHtml(reward.reward_name||'Phần thưởng')}">`:''}</div>
      <small>${escapeHtml(rarity)}</small>
      <h3>${escapeHtml(reward.reward_name||'Phần thưởng')}</h3>
      ${amount?`<strong>${formatter.format(amount)} Zcoin</strong>`:''}
    </article>`;
  }

  function strongestRarity(rewards){
    return rewards.reduce((best,reward)=>{
      const code=reward.reward_rarity||reward.rarity||'common';
      return (rarityOrder[code]||0)>(rarityOrder[best]||0)?code:best;
    },'common');
  }

  function setStagePhase(phase){
    if(!stage) return;
    stage.classList.remove('is-charging','is-bursting','is-revealing','is-complete');
    if(phase) stage.classList.add(phase);
  }

  function waitOrSkip(ms){
    if(reducedMotion||skipRequested) return Promise.resolve();
    return new Promise(resolve=>{
      const started=Date.now();
      const timer=window.setInterval(()=>{
        if(skipRequested||Date.now()-started>=ms){
          window.clearInterval(timer);
          resolve();
        }
      },40);
    });
  }

  function showOverlay(){
    if(!overlay) return;
    skipRequested=false;
    animationRunning=true;
    overlay.hidden=false;
    overlay.setAttribute('aria-hidden','false');
    overlay.className='lb4-opening is-active rarity-common';
    document.body.classList.add('lb4-no-scroll');
    if(overlayRewards){overlayRewards.hidden=true;overlayRewards.innerHTML='';}
    if(continueButton) continueButton.hidden=true;
    if(skipButton){skipButton.hidden=false;skipButton.disabled=false;}
    if(overlayStatus) overlayStatus.textContent='Đang tập trung năng lượng...';
    setStagePhase('is-charging');
  }

  function hideOverlay(){
    if(!overlay) return;
    overlay.classList.remove('is-active');
    overlay.setAttribute('aria-hidden','true');
    overlay.hidden=true;
    document.body.classList.remove('lb4-no-scroll');
    animationRunning=false;
  }

  function renderResult(data,rewards){
    resultGrid.innerHTML=rewards.map(rewardCard).join('');
    resultNote.textContent=previewMode?'Kết quả mô phỏng · Không thay đổi dữ liệu':`Rate Version ${data.rate_version||'-'}`;
    resultPanel.hidden=false;
    if(!previewMode&&balanceNode&&Number.isFinite(Number(data.balance_after))){
      balanceNode.textContent=`${formatter.format(Number(data.balance_after))} Zcoin`;
      document.querySelectorAll('.topbar-zcoin strong').forEach(node=>{node.textContent=formatter.format(Number(data.balance_after));});
    }
    if(openingLink){
      if(!previewMode&&data.opening_id){
        openingLink.href=`/lucky-box/openings/${encodeURIComponent(data.opening_id)}`;
        openingLink.textContent='Xem chi tiết';
        openingLink.hidden=false;
      }else{
        openingLink.href='/lucky-box/history';
        openingLink.textContent='Lịch sử Lucky Box';
      }
    }
  }

  async function playOpeningAnimation(rewards){
    if(!overlay||!stage||!overlayRewards) return;
    const rarity=strongestRarity(rewards);
    overlay.className=`lb4-opening is-active rarity-${rarity}`;

    setStagePhase('is-charging');
    if(overlayStatus) overlayStatus.textContent='Hộp đang tích tụ năng lượng...';
    await waitOrSkip(650);

    setStagePhase('is-bursting');
    if(overlayStatus) overlayStatus.textContent='Lucky Box đang mở!';
    await waitOrSkip(700);

    setStagePhase('is-revealing');
    overlayRewards.innerHTML=rewards.map(overlayRewardCard).join('');
    overlayRewards.hidden=false;
    const cards=Array.from(overlayRewards.querySelectorAll('[data-lb4-card]'));
    for(let index=0;index<cards.length;index+=1){
      cards[index].classList.add('is-visible');
      if(overlayStatus) overlayStatus.textContent=`Đang hé lộ phần thưởng ${index+1}/3...`;
      await waitOrSkip(480);
    }
    if(skipRequested||reducedMotion) cards.forEach(card=>card.classList.add('is-visible'));

    setStagePhase('is-complete');
    if(overlayStatus) overlayStatus.textContent='Chúc mừng! Bạn đã nhận đủ 3 phần thưởng.';
    if(skipButton) skipButton.hidden=true;
    if(continueButton) continueButton.hidden=false;
  }

  async function openBox(){
    if(!openButton||openButton.disabled||animationRunning) return;
    if(!previewMode){
      const price=Number(page.dataset.openPrice||0);
      if(!window.confirm(`Mở Lucky Box với ${formatter.format(price)} Zcoin?`)) return;
    }

    openButton.disabled=true;
    if(againButton) againButton.disabled=true;
    errorBox.hidden=true;
    showOverlay();

    try{
      const payload=previewMode
        ?{rate_version_id:page.dataset.rateVersionId}
        :{request_id:requestId(),box_code:page.dataset.boxCode};
      const response=await fetch(page.dataset.openUrl,{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify(payload)
      });
      const data=await response.json().catch(()=>({ok:false,message:'Server không trả về dữ liệu hợp lệ.'}));
      if(!response.ok||!data.ok) throw new Error(data.message||'Không thể mở Lucky Box.');
      const rewards=Array.isArray(data.rewards)?data.rewards:[];
      if(rewards.length!==3) throw new Error('Kết quả Lucky Box không đủ 3 phần thưởng.');
      renderResult(data,rewards);
      await playOpeningAnimation(rewards);
    }catch(error){
      hideOverlay();
      showError(error.message);
    }finally{
      openButton.disabled=false;
      if(againButton) againButton.disabled=false;
    }
  }

  skipButton?.addEventListener('click',()=>{
    skipRequested=true;
    skipButton.disabled=true;
    if(overlayStatus) overlayStatus.textContent='Đang chuyển nhanh tới phần thưởng...';
  });

  continueButton?.addEventListener('click',()=>{
    hideOverlay();
    resultPanel?.scrollIntoView({behavior:reducedMotion?'auto':'smooth',block:'start'});
  });

  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape'||!animationRunning) return;
    if(continueButton&&!continueButton.hidden){
      continueButton.click();
    }else{
      skipRequested=true;
      if(skipButton) skipButton.disabled=true;
    }
  });

  openButton?.addEventListener('click',openBox);
  againButton?.addEventListener('click',openBox);
})();
