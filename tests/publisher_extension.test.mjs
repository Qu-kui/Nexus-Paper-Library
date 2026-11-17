import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const {chromium}=createRequire('C:/Users/10498/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/package.json')('playwright');
const source=readFileSync(new URL('../extension/popup.js',import.meta.url),'utf8');
const extract=source.slice(source.indexOf('async function extractPageMetadata('),source.indexOf('\nasync function probeBase'));
const download=source.slice(source.indexOf('async function fetchPdfInPage('),source.indexOf('\nasync function cookieHeader'));
test('ScienceDirect embedded parameters, intermediate redirect and bounded browser transfer',async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try {
    const page=await browser.newPage();
    const base='https://www.sciencedirect.com/science/article/pii/S0143816619313120';
    await page.route('**/*',route=>route.fulfill({body:'<html><title>Paper</title><script type="application/json">'+JSON.stringify({article:{pdfDownload:{urlMetadata:{path:'science/article/pii',pii:'S0143816619313120',pdfExtension:'/pdfft',queryParams:{md5:'a&b',pid:'main.pdf'}}}}})+'</script></html>',contentType:'text/html'}));
    await page.goto(base);
    const meta=await page.evaluate('('+extract+')()');
    assert.match(meta.pdf_url,/md5=a%26b&pid=main.pdf/);
    const result=await page.evaluate(async ({download,base})=>{
      const calls=[];
      globalThis.fetch=async (url,options)=>{
        calls.push({url,credentials:options.credentials});
        if(url.includes('/pdfft'))return new Response('<meta http-equiv="Refresh" content="0;URL=https://cdn.example/full.pdf?token=x">');
        return new Response('%PDF-1.7\n'+'x'.repeat(2000));
      };
      const value=await (0,eval)('('+download+')')(base+'/pdfft');
      return {value,calls};
    },{download,base});
    assert.ok(result.value.startsWith('JVBER'));assert.equal(result.calls.length,2);
    assert.ok(result.calls.every(call=>call.credentials==='include'));
    const cycle=await page.evaluate(async download=>{
      let count=0;globalThis.fetch=async()=>{count++;return new Response('<meta http-equiv="refresh" content="0;URL=https://example.org/loop">');};
      const result=await (0,eval)('('+download+')')('https://example.org/loop');return {count,result};
    },download);
    assert.equal(cycle.result,'');assert.equal(cycle.count,1);
  } finally {await browser.close();}
});


test('save-time ScienceDirect menu expansion preserves state and proxy origin',async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try {
    const page=await browser.newPage();
    await page.route('**/*',route=>route.fulfill({contentType:'text/html',body:`<title>Paper</title>
      <link rel="canonical" href="https://www.sciencedirect.com/science/article/pii/S1234567890?via=ihub">
      <button id="pdfLink" aria-haspopup="true" aria-expanded="false">PDF</button><div class="PdfDropDownMenu"></div>
      <script>let clicks=0;document.querySelector('button').onclick=()=>{clicks++;let b=document.querySelector('button');b.setAttribute('aria-expanded',b.getAttribute('aria-expanded')==='true'?'false':'true');if(b.getAttribute('aria-expanded')==='false')document.querySelector('.PdfDropDownMenu').innerHTML='';if(clicks===1)setTimeout(()=>{document.querySelector('.PdfDropDownMenu').innerHTML='<a href="/science/article/pii/S1234567890/pdfft?token=live">Download PDF</a><a href="/supplement.pdf">Supplement</a>';},30);};</script>`}));
    await page.goto('https://library.proxy.test/science/article/pii/S1234567890?via=proxy');
    const initial=await page.evaluate('('+extract+')()');
    assert.equal(await page.evaluate('clicks'),0);
    const meta=await page.evaluate('('+extract+')({expandPdfMenu:true})');
    assert.equal(meta.pdf_url,'https://library.proxy.test/science/article/pii/S1234567890/pdfft?token=live');
    assert.equal(await page.evaluate('clicks'),2);
    assert.equal(await page.getAttribute('#pdfLink','aria-expanded'),'false');
    assert.ok(meta.pdf_candidates.length>=2);
    await page.evaluate("document.querySelector('button').setAttribute('aria-expanded','true')");
    await page.evaluate('('+extract+')({expandPdfMenu:true})');
    assert.equal(await page.evaluate('clicks'),2);
    assert.equal(await page.getAttribute('#pdfLink','aria-expanded'),'true');
    assert.match(initial.pdf_url,/library.proxy.test/);
  } finally {await browser.close();}
});


test('publisher identity evidence must match page PII; real page fetch includes cookies',async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try {
    const context=await browser.newContext();const page=await context.newPage();
    const origin='https://library.proxy.test';
    let cookie='';
    await context.addCookies([{name:'institution',value:'session',domain:'library.proxy.test',path:'/'}]);
    await page.route('**/*',async route=>{
      if(route.request().url().endsWith('/file.pdf')){cookie=route.request().headers().cookie||'';return route.fulfill({body:'%PDF-1.7\n'+'x'.repeat(2000),contentType:'application/pdf'});}
      return route.fulfill({contentType:'text/html',body:'<title>Paper</title><link rel="canonical" href="https://www.sciencedirect.com/science/article/pii/S999"><a id="pdfLink" href="/secret-pdf">PDF</a>'});
    });
    await page.goto(origin+'/science/article/pii/S123');
    const meta=await page.evaluate('('+extract+')()');assert.equal(meta.pdf_url,'');
    const pdf=await page.evaluate('('+download+')('+JSON.stringify(origin+'/file.pdf')+')');
    assert.ok(pdf.startsWith('JVBER'));assert.match(cookie,/institution=session/);
    const expired=await page.evaluate('('+download+')('+JSON.stringify(origin+'/file.pdf')+',1)');assert.equal(expired,'');
  } finally {await browser.close();}
});

test('browser diagnostics trace HTML refresh, success and redirect cycles without query secrets',async()=>{
  const browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  try {
    const page=await browser.newPage();
    const results=await page.evaluate(async download=>{
      const grab=(0,eval)('('+download+')');
      globalThis.fetch=async url=>url.includes('/landing')?new Response('<meta http-equiv="refresh" content="0;URL=https://cdn.example/full.pdf?token=secret#hidden">'):new Response('%PDF-1.7\n'+'x'.repeat(2000));
      const success=await grab('https://example.org/landing?token=start',Date.now()+2000,'',true);
      globalThis.fetch=async()=>new Response('<html><title>Access page</title></html>');
      const html=await grab('https://example.org/html',Date.now()+2000,'',true);
      globalThis.fetch=async()=>new Response('<meta http-equiv="refresh" content="0;URL=https://example.org/loop?token=cycle">');
      const cycle=await grab('https://example.org/loop?token=cycle',Date.now()+2000,'',true);
      return {success,html,cycle};
    },download);
    assert.ok(results.success.base64.startsWith('JVBER'));
    assert.deepEqual(results.success.attempts.map(a=>a.code),['not_pdf','ok']);
    assert.deepEqual(results.html.attempts.map(a=>a.code),['not_pdf']);
    assert.deepEqual(results.cycle.attempts.map(a=>a.code),['not_pdf','redirect_limit']);
    assert.doesNotMatch(JSON.stringify(results),/token|secret|hidden/);
  } finally {await browser.close();}
});
