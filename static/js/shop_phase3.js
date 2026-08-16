(function(){
  function ready(fn){if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',fn);else fn();}
  ready(function(){
    const packages=document.querySelectorAll('[data-topup-package]');
    const selectedAmount=document.getElementById('topupSelectedAmount');
    const selectedZcoin=document.getElementById('topupSelectedZcoin');
    const transferExample=document.getElementById('topupTransferExample');
    const formatVnd=value=>new Intl.NumberFormat('vi-VN').format(Number(value||0))+'đ';
    const formatNumber=value=>new Intl.NumberFormat('vi-VN').format(Number(value||0));
    function selectTopupPackage(button){
      if(!button)return;
      packages.forEach(item=>item.classList.toggle('is-selected',item===button));
      if(selectedAmount)selectedAmount.textContent=formatVnd(button.dataset.amount);
      if(selectedZcoin)selectedZcoin.textContent=formatNumber(button.dataset.zcoin);
      if(transferExample)transferExample.textContent='NGUYEN VAN A '+String(button.dataset.amount||'');
    }
    if(packages.length){
      packages.forEach(button=>button.addEventListener('click',()=>selectTopupPackage(button)));
      selectTopupPackage(Array.from(packages).find(button=>button.dataset.amount==='300000')||packages[0]);
    }

    const modal=document.getElementById('shop3Preview');
    if(!modal)return;
    const banner=document.getElementById('shop3PreviewBanner');
    const frame=document.getElementById('shop3PreviewFrame');
    const badge=document.getElementById('shop3PreviewBadge');
    const utility=document.getElementById('shop3PreviewUtility');
    const playerName=document.getElementById('shop3PreviewPlayerName');
    const title=document.getElementById('shop3PreviewName');
    const desc=document.getElementById('shop3PreviewDescription');
    const nameClasses=['name-style-neon-blue','name-style-elite-purple','name-style-champion-gold'];

    function reset(){
      banner.style.backgroundImage=''; frame.src=''; badge.src=''; utility.src='';
      frame.hidden=true; badge.hidden=true; utility.hidden=true;
      nameClasses.forEach(c=>playerName.classList.remove(c));
    }
    function open(card){
      reset();
      const type=card.dataset.itemType||'';
      const image=card.dataset.itemImage||'';
      title.textContent=card.dataset.itemName||'Vật phẩm';
      desc.textContent=card.dataset.itemDescription||'';
      if(type==='profile_banner') banner.style.backgroundImage='url("'+image.replace(/"/g,'')+'")';
      else if(type==='avatar_frame'){frame.src=image;frame.hidden=false;}
      else if(type==='profile_badge'){badge.src=image;badge.hidden=false;}
      else if(type==='name_style'){
        const cssClass=card.dataset.nameStyle||'';
        if(nameClasses.includes(cssClass))playerName.classList.add(cssClass);
        utility.src=image;utility.hidden=false;
      }else{utility.src=image;utility.hidden=false;}
      modal.hidden=false;document.body.style.overflow='hidden';
    }
    function close(){modal.hidden=true;document.body.style.overflow='';}
    document.querySelectorAll('.js-shop3-preview').forEach(btn=>btn.addEventListener('click',()=>open(btn.closest('[data-shop-item]'))));
    modal.querySelectorAll('[data-shop3-close]').forEach(btn=>btn.addEventListener('click',close));
    modal.addEventListener('click',e=>{if(e.target===modal)close();});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!modal.hidden)close();});
  });
})();
