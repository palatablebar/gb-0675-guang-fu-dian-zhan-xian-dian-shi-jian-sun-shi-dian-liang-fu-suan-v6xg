const solar={busy:false};const status=document.querySelector('#feedback');document.querySelector('#recompute').addEventListener('click',async()=>{if(solar.busy)return;solar.busy=true;status.textContent='复算中…';try{const dataset=document.querySelector('#dataset').value;const response=await fetch(dataset===document.body.dataset.source?'/api/runs/'+document.body.dataset.runId+'/rerun':'/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset})});const run=await response.json();if(!response.ok)throw Error(run.detail);const url=new URL(location.href);url.searchParams.set('run_id',run.manifest.run_id);url.searchParams.set('dataset',dataset);location.href=url;}catch(error){status.textContent=error.message;}finally{solar.busy=false;}});
async function post(url,body){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});const data=await response.json();if(!response.ok)throw Error(data.detail||'操作失败');return data;}
const issueButton=document.querySelector('#cert-submit');
if(issueButton){
 issueButton.addEventListener('click',async()=>{if(solar.busy)return;solar.busy=true;issueButton.disabled=true;status.textContent='签发中…';try{const mode=document.querySelector('#cert-mode').value;const note=document.querySelector('#cert-note').value;const data=await post('/api/certificate/issue',{run_id:document.body.dataset.runId,mode,note});status.textContent=data.reused?'该证据下已存在同一证书，未覆盖已确认内容':'已签发 '+data.certificate.certificate_id;location.reload();}catch(error){status.textContent=error.message;}finally{solar.busy=false;issueButton.disabled=false;}});
}
const sealButton=document.querySelector('#seal-run');
if(sealButton){
 sealButton.addEventListener('click',async()=>{if(solar.busy)return;solar.busy=true;sealButton.disabled=true;status.textContent='归档中…';try{const data=await post('/api/runs/'+document.body.dataset.runId+'/seal');status.textContent=data.reused?'该批次已正式归档，未覆盖既有归档':'已正式归档 '+data.archive.archive_id;location.reload();}catch(error){status.textContent=error.message;}finally{solar.busy=false;sealButton.disabled=false;}});
}
const filePicker=document.querySelector('#csv-file');
if(filePicker){
 const editor=document.querySelector('#csv-text');
 filePicker.addEventListener('change',async()=>{const file=filePicker.files[0];if(!file)return;if(file.size>2000000){editor.value='';alert('文件超过2MB，请缩小后导入');return;}const bytes=await file.arrayBuffer();try{editor.value=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch(e){editor.value=new TextDecoder('gb18030',{fatal:true}).decode(bytes);}});
 document.querySelector('#import-csv').addEventListener('click',async event=>{event.target.disabled=true;try{const response=await fetch('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({csv_text:editor.value})});const data=await response.json();if(!response.ok)throw Error(data.detail);location.href=location.pathname+'?run_id='+data.manifest.run_id;}catch(e){editor.setCustomValidity(e.message);editor.reportValidity();}finally{event.target.disabled=false;}});
 editor.addEventListener('input',()=>editor.setCustomValidity(''));
}
