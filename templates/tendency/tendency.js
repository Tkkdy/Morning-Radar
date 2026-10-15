"use strict";
const feedback = document.getElementById("feedback");
function message(text) { if(feedback) feedback.textContent=text; }
const filter=document.getElementById("filter");
if(filter) filter.addEventListener("change",()=>document.querySelectorAll("[data-standing]").forEach(card=>{card.hidden=filter.value!=="all" && card.dataset.standing!==filter.value;}));
const login=document.getElementById("login");
if(login) login.addEventListener("submit",async(event)=>{event.preventDefault();message("正在验证会话…");try{
 const response=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({passphrase:login.passphrase.value})});
 login.passphrase.value="";message(response.ok?"会话已授权，可返回投递页面。":"授权失败，请核实本机运行口令。");
}catch{message("服务不可用；尚未授权。");}});
const form=document.getElementById("submission");
if(form){let operation=crypto.randomUUID();
 document.getElementById("new-operation").addEventListener("click",()=>{operation=crypto.randomUUID();message("已建立新的草稿操作，尚未提交。");});
 form.addEventListener("submit",async(event)=>{event.preventDefault();const button=form.querySelector('button[type="submit"]');button.disabled=true;message("正在保存原文…");try{
  const auth=await fetch("/api/session");if(!auth.ok){message("请先授权本机写入。草稿仍在当前页面。");return;}
  const session=await auth.json();const values=Object.fromEntries(new FormData(form));
  values.operation_id=operation;values.hypothesis_id=form.dataset.target||null;values.expected_revision=form.dataset.revision||null;values.source_url=values.source_url||null;
  const response=await fetch("/api/submissions",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":session.csrf},body:JSON.stringify(values)});
  const result=await response.json();message(result.message);
  if(result.receipt_url){const link=document.createElement("a");link.href=result.receipt_url;link.textContent="打开持久化回执与原文";feedback.append(document.createElement("br"),link);}
 }catch{message("未收到确认，请保留草稿并重试同一操作；回执列表可核实服务是否已收到。");}finally{button.disabled=false;}});
}

const confirmation=document.getElementById("confirmation");
if(confirmation){let operation=crypto.randomUUID();
 confirmation.addEventListener("change",()=>{operation=crypto.randomUUID();});
 confirmation.addEventListener("submit",async(event)=>{event.preventDefault();
 const button=confirmation.querySelector('button[type="submit"]');button.disabled=true;
 try{const auth=await fetch("/api/session");if(!auth.ok){message("请先授权本机写入，再返回此版本操作。");return;}
 const session=await auth.json();const values=Object.fromEntries(new FormData(confirmation));
 Object.assign(values,{operation_id:operation,revision_id:confirmation.dataset.revision,body_sha256:confirmation.dataset.body,evidence_sha256:confirmation.dataset.evidence});
 const response=await fetch("/api/confirmations",{method:"POST",headers:{"Content-Type":"application/json","X-CSRF-Token":session.csrf},body:JSON.stringify(values)});
 const result=await response.json();message(result.message);
 }catch{message("未收到保存回执。请保留当前决定，刷新详情核对后重试。");}finally{button.disabled=false;}});
}
