/* DOM + real HTTP/worker integration; supplements, does not replace, browser rendering tests.
   Optional dependency: jsdom 26.1.0. Provide it through NODE_PATH or a local dev install. */
const {JSDOM,CookieJar,VirtualConsole}=require('jsdom');
const {spawn,spawnSync}=require('node:child_process');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),crypto=require('node:crypto'),net=require('node:net');
const assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..');
const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function waitFor(fn,label){for(let i=0;i<200;i++){if(fn())return;await pause(100);}throw new Error('Timed out: '+label);}
async function main(){
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'adpe-dom-'));
  const socket=net.createServer();await new Promise(resolve=>socket.listen(0,'127.0.0.1',resolve));const port=socket.address().port;await new Promise(resolve=>socket.close(resolve));
  const origin=`http://127.0.0.1:${port}`,password=crypto.randomBytes(24).toString('hex');
  const env={...process.env,ADPE_ENVIRONMENT:'test',ADPE_DATABASE_URL:'sqlite:///'+path.join(temp,'db.sqlite'),ADPE_DATA_DIR:path.join(temp,'data'),ADPE_PUBLIC_ORIGIN:origin,ADPE_ALLOWED_HOSTS:'["localhost","127.0.0.1"]',ADPE_SECURE_COOKIES:'false',ADPE_OLLAMA_ENABLED:'false'};
  function run(args,extra={}){const r=spawnSync('python',args,{cwd:root,env:{...env,...extra},encoding:'utf8'});if(r.status!==0)throw new Error(r.stderr);}
  run(['-m','engine.cli','migrate']);
  run(['-c',"from engine.db import session_factory; from engine.security import create_user; import os; db=session_factory()(); create_user(db,'domuser',os.environ['ADPE_TEST_PASSWORD'],True); db.commit()"],{ADPE_TEST_PASSWORD:password});
  const api=spawn('python',['-m','engine.cli','serve','--port',String(port)],{cwd:root,env,stdio:'ignore'});
  const worker=spawn('python',['-m','engine.cli','worker'],{cwd:root,env,stdio:'ignore'});
  let dom;
  try{
    for(let i=0;i<100;i++){try{if((await fetch(origin+'/health/ready')).ok)break;}catch{}await pause(100);}
    const errors=[],jar=new CookieJar(),virtualConsole=new VirtualConsole();
    virtualConsole.on('jsdomError',e=>errors.push(String(e)));
    dom=await JSDOM.fromURL(origin,{runScripts:'dangerously',resources:'usable',pretendToBeVisual:true,cookieJar:jar,virtualConsole,beforeParse(window){
      window.fetch=async(input,options={})=>{const url=new URL(input,origin).href,headers=new Headers(options.headers||{});const cookie=jar.getCookieStringSync(url);if(cookie)headers.set('Cookie',cookie);const response=await fetch(url,{...options,headers});for(const value of response.headers.getSetCookie())jar.setCookieSync(value,url);return response;};
      window.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
      window.HTMLDialogElement.prototype.close=function(){this.open=false;};
      window.confirm=()=>true;
    }});
    const w=dom.window,$=id=>w.document.getElementById(id),submit=id=>$(id).dispatchEvent(new w.Event('submit',{bubbles:true,cancelable:true}));
    await waitFor(()=>!!$('login-form')&&w.document.readyState==='complete','scripts loaded');
    $('username').value='domuser';$('password').value=password;submit('login-form');
    await waitFor(()=>!$('workspace').hidden&&$('upload-limit').textContent,'login');
    $('open-upload').click();
    const filename='<img src=x onerror=alert(1)>.csv';
    const file=new w.File([fs.readFileSync(path.join(root,'examples/drone_telemetry.csv'))],filename,{type:'text/csv'});
    Object.defineProperty($('file-input'),'files',{value:[file],configurable:true});submit('upload-form');
    await waitFor(()=>$('analysis-dialog').open,'upload');
    $('thresholds').value='{"motor_temperature":{"max":80},"battery_voltage":{"min":10}}';submit('analysis-form');
    await waitFor(()=>$('report-content').textContent.includes('breach configured limits'),'worker/report');
    assert($('report-content').textContent.includes('9 records breach'));
    assert($('report-content').textContent.includes('2 records breach'));
    assert.equal($('report-title').textContent,filename);
    assert.equal(w.document.querySelectorAll('#report-title img').length,0);
    assert.equal($('exports').querySelectorAll('a').length,3);
    const pdfURL=$('exports').querySelectorAll('a')[2].href;
    const report=await w.fetch(pdfURL);assert(report.ok);assert(Buffer.from(await report.arrayBuffer()).subarray(0,4).equals(Buffer.from('%PDF')));
    $('admin-nav').click();await waitFor(()=>$('users-list').textContent.includes('domuser'),'admin');
    $('logout').click();await waitFor(()=>$('workspace').hidden,'logout');
    assert.deepEqual(errors,[]);
    const result={status:'passed',checks:['login','raw file upload','analysis dialog','thresholds','real worker','report rendering','XSS filename escaping','PDF export','administration','logout','no script errors'],limitation:'JSDOM: no visual layout or browser security enforcement verification'};
    const output=path.join(root,'test-results');fs.mkdirSync(output,{recursive:true});fs.writeFileSync(path.join(output,'dom-result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
  }finally{if(dom)dom.window.close();worker.kill('SIGTERM');api.kill('SIGTERM');await pause(700);fs.rmSync(temp,{recursive:true,force:true});}
}
main().catch(error=>{console.error(error);process.exitCode=1;});
