// Render native SVG masters using an isolated browser, without a user profile.
import {createRequire} from 'node:module';
import {readFileSync} from 'node:fs';
import {join, dirname, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
let chromium;
try {({chromium}=createRequire(import.meta.url)('playwright'));}
catch {
  const dependencies=process.env.PAPERWHALE_NODE_MODULES || 'C:/Users/10498/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
  ({chromium}=createRequire(join(dependencies,'package.json'))('playwright'));
}
const browser=await chromium.launch({headless:true,executablePath:process.env.PAPERWHALE_EDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
try {
  const page=await browser.newPage({viewport:{width:512,height:512},deviceScaleFactor:1});
  for(const name of ['npl-app','npl-mark']) {
    const svg=readFileSync(join(root,'web/brand',name+'.svg'),'utf8');
    await page.setContent('<style>html,body{margin:0;width:512px;height:512px;background:transparent}body>svg{display:block;width:512px;height:512px}</style>'+svg);
    await page.screenshot({path:join(root,'web/brand',name+'.png'),omitBackground:true});
  }
} finally {await browser.close();}
console.log('Rendered NPL SVG masters to transparent 512px PNG assets');
