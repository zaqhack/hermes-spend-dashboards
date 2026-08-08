    const grafanaUrl=_sessionGrafanaUrl(s);
    if(grafanaUrl){
      costEl.href=grafanaUrl;
      costEl.style.textDecoration='none';
      costEl.style.color='inherit';
      costEl.addEventListener('click',function(e){
        e.preventDefault();
        e.stopPropagation();
        if(typeof window.openGrafanaPopover==='function'){
          window.openGrafanaPopover(grafanaUrl);
        } else {
          window.open(grafanaUrl,'_blank');
        }
      });
    }
    titleRow.appendChild(costEl);
