// Isolated browser with mocked HTTP. Does not start PaperWhale or touch user data.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {join} from 'node:path';

const require = createRequire(join(process.env.PAPERWHALE_NODE_MODULES || 'C:/Users/10498/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules', 'package.json'));
const {chromium} = require('playwright');
const read = path => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');
const html = read('web/index.html');
const section = html.slice(html.indexOf('<section class="page" data-page="terms"'), html.indexOf('<section class="page" data-page="terms"') + 10000);

async function harness(run) {
  const browser = await chromium.launch({headless: true, channel: process.env.PAPERWHALE_BROWSER_CHANNEL || 'chrome'});
  try {
    const page = await browser.newPage({reducedMotion: 'reduce'});
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    let fail = false, holdAnswer = null, holdQuiz = null, nextRound = 0;
    const posts = [];
    const questions = round => [0, 1].map(i => ({id:`term${i}`, question_id:`q${round}-${i}`, prompt:`题目 ${round}-${i}`, answer:'正确选项', options:['正确选项', '错误选项'], direction:'meaning2term', kind:'word'}));
    await page.route('http://terms.test/**', async route => {
      const url = new URL(route.request().url());
      const path = url.pathname;
      if (path.startsWith('/js/')) return route.fulfill({contentType:'text/javascript', body:read('web' + path)});
      if (path === '/style.css') return route.fulfill({contentType:'text/css', body:read('web/css/style.css')});
      if (path === '/api/terms/quiz') {
        const round = ++nextRound;
        if (holdQuiz) await holdQuiz;
        return route.fulfill({json:{items:questions(round), stats:{total:2}}});
      }
      if (path.endsWith('/answer')) {
        posts.push(route.request().postDataJSON());
        if (holdAnswer) await holdAnswer;
        return route.fulfill(fail ? {status:500,json:{detail:'模拟保存失败'}} : {json:{item:{},stats:{total:2}}});
      }
      if (path === '/api/terms') return route.fulfill({json:{items:[],total:1001,stats:{total:1001}}});
      if (path.startsWith('/api/')) return route.fulfill({json:{items:[]}});
      return route.fulfill({contentType:'text/html', body:`<link rel="stylesheet" href="/style.css"><style>.hidden{display:none!important}</style>${section.split('</section>')[0]}</section><div id="toasts"></div><script type="module">import {initTerms} from '/js/terms-page.js';initTerms();document.querySelector('.page').classList.add('active');</script>`});
    });
    await page.goto('http://terms.test/');
    await page.waitForFunction(() => document.querySelector('#terms-list-count')?.textContent.includes('1001'));
    await run({page, posts, fail(value) {fail=value;}, answerGate(p) {holdAnswer=p;}, quizGate(p) {holdQuiz=p;}});
    assert.deepEqual(errors, []);
  } finally {await browser.close();}
}

test('keyboard fuzzy submits fuzzy; failure keeps card; retry reuses question; manual next and focus', async () => {
  await harness(async ({page,posts,fail}) => {
    await page.locator('#btn-terms-start').click();
    await page.waitForFunction(() => document.activeElement?.id === 'terms-quiz-prompt');
    fail(true);
    await page.locator('.quiz-option-fuzzy').first().focus();
    await page.keyboard.press('Enter');
    await page.locator('#btn-terms-retry').waitFor({state:'visible'});
    assert.equal(posts.length, 1);
    assert.equal(posts[0].fuzzy, true);
    assert.equal(posts[0].chosen_option, '正确选项');
    assert.match(await page.locator('#terms-quiz-feedback').innerText(), /保存失败/);
    assert.match(await page.locator('#terms-quiz-prompt').innerText(), /1-0/);
    fail(false);
    await page.locator('#btn-terms-retry').click();
    await page.waitForFunction(() => document.querySelector('#terms-quiz-feedback').textContent.includes('已保存'));
    assert.deepEqual(posts[0], posts[1]);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'btn-terms-next');
    await page.waitForTimeout(1000);
    assert.match(await page.locator('#terms-quiz-prompt').innerText(), /1-0/);
    await page.locator('#btn-terms-next').click();
    assert.match(await page.locator('#terms-quiz-prompt').innerText(), /1-1/);
    assert.equal(await page.evaluate(() => document.activeElement.id), 'terms-quiz-prompt');
    assert.equal(await page.locator('.quiz-option').first().evaluate(e => getComputedStyle(e).animationName), 'none');
  });
});

test('late answer and canceled auto timer cannot modify a new round', async () => {
  await harness(async ({page, answerGate}) => {
    await page.locator('#btn-terms-start').click();
    await page.locator('#terms-auto-advance').check();
    let release;
    answerGate(new Promise(resolve => {release=resolve;}));
    await page.locator('.quiz-option-text').first().click();
    await page.locator('#btn-terms-start').click();
    await page.waitForFunction(() => document.querySelector('#terms-quiz-prompt').textContent.includes('2-0'));
    release();
    answerGate(null);
    await page.waitForTimeout(4300);
    assert.match(await page.locator('#terms-quiz-prompt').innerText(), /2-0/);
    await page.locator('.quiz-option-text').first().click();
    await page.waitForFunction(() => document.querySelector('#terms-quiz-feedback').textContent.includes('已保存'));
    await page.locator('#btn-terms-quiz-stop').click();
    await page.locator('#btn-terms-start').click();
    await page.waitForTimeout(4300);
    assert.match(await page.locator('#terms-quiz-prompt').innerText(), /3-0/);
  });
});
