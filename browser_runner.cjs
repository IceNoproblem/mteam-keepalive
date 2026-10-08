/**
 * mteam-keepalive · 浏览器驱动
 *
 * 用法: node browser_runner.cjs <payload.json>
 * 约定: 结果以 __RESULT__<json> 单行输出，供 Python 解析
 *
 * 流程：打开站点 → 判断登录态 → （必要时）账号密码 + 邮箱 2FA → 随机漫游站内页面
 */
const path = require('path');
const fs = require('fs');
const puppeteer = require(path.join(__dirname, 'node_modules', 'puppeteer-core'));

const sleep = ms => new Promise(r => setTimeout(r, ms));
const rnd = (a, b) => a + Math.floor(Math.random() * (b - a + 1));
const log = m => console.log('[browser] ' + m);
const out = obj => console.log('__RESULT__' + JSON.stringify(obj));

/**
 * 候选页面。优先用 config.json 的 pages 字段覆盖，
 * 这样换站点时无需改代码。
 */
const DEFAULT_PAGES = [
  { u: '/index', label: '首页' },
  { u: '/torrents', label: '种子列表' },
  { u: '/forums', label: '论坛' },
  { u: '/upload', label: '发布页' },
  { u: '/top', label: '热门' },
  { u: '/new', label: '最新' },
  { u: '/messages', label: '私信' },
];

(async () => {
  const cfgPath = process.argv[2];
  const P = JSON.parse(fs.readFileSync(cfgPath, 'utf8'));
  const PAGES = (P.pages && P.pages.length) ? P.pages : DEFAULT_PAGES;
  const LOGIN_PATH = P.loginPath || '/login';

  const visited = [];
  const result = { visited, login_ok: false, code_needed: false, error: null };

  // ---------- 启动浏览器 ----------
  const args = [
    '--no-sandbox', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-sync',
    '--window-size=1440,900', `--user-data-dir=${P.profile}`,
  ];
  if (P.proxy) args.push(`--proxy-server=${P.proxy}`);

  let browser;
  try {
    browser = await puppeteer.launch({
      executablePath: P.browser,
      headless: P.headless !== false,
      userDataDir: P.profile,
      args,
      defaultViewport: { width: 1440, height: 900 },
    });
  } catch (e) {
    result.error = '浏览器启动失败: ' + e.message;
    return out(result);
  }

  const page = await browser.newPage();

  // 抹平自动化指纹：与真实浏览器保持一致，避免被误判为机器人
  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  });

  page.setDefaultNavigationTimeout(60000);
  page.setDefaultTimeout(30000);

  // 拟人化抖动：所有等待在基准值上下浮动 40%
  const sleepA = ms => sleep(rnd(Math.floor(ms * 0.6), Math.ceil(ms * 1.4)));

  /**
   * 真实鼠标点击。
   * 前端框架（React/Vue）只认真实指针事件，DOM 的 el.click() 会被忽略，
   * 必须走 puppeteer 的 mouse.click 派发指针序列。
   */
  async function realClick(target) {
    const el = typeof target === 'string' ? await page.$(target) : target;
    if (!el) return false;
    try {
      const box = await el.boundingBox();
      if (!box) return false;
      await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
      return true;
    } catch (e) {
      return false;
    }
  }

  /** 点击「获取验证码」类按钮（识别中英文文案） */
  async function clickGetCode() {
    const h = await page.evaluateHandle(() =>
      [...document.querySelectorAll('button[type=button], button')]
        .find(x => /獲取|获取/.test(x.innerText || '')));
    return realClick(h.asElement());
  }

  /** 清空并聚焦验证码输入框 */
  async function clearCodeInput() {
    await page.evaluate(() => {
      const i = document.querySelector('input[maxlength="6"]');
      if (i) { i.focus(); i.value = ''; }
    });
  }

  /** 提交验证码并判定结果 */
  async function submitCode(code) {
    await clearCodeInput();
    await page.type('input[maxlength="6"]', code, { delay: rnd(60, 120) });
    await sleepA(600);
    await realClick('form button[type=submit]');
    await sleep(9000);
    if (!new RegExp(LOGIN_PATH).test(page.url())) {
      result.login_ok = true;
      result.code_needed = false;
      result.error = null;
      result.usedCode = code;
      log('2FA 通过，登录成功');
      return true;
    }
    return false;
  }

  try {
    // ---------- 1. 打开站点 ----------
    await page.goto(P.url, { waitUntil: 'domcontentloaded' });
    await sleep(3500);
    let url = page.url();
    log('打开首页 -> ' + url);

    // ---------- 2. 判断是否需要登录 ----------
    if (!new RegExp(LOGIN_PATH).test(url)) {
      log('检测到已有登录态，跳过登录');
      result.login_ok = true;
    } else {
      log('需要登录，填写账号密码');
      await page.waitForSelector('#username', { timeout: 20000 });
      await page.type('#username', P.user, { delay: rnd(40, 90) });
      await page.type('#password', P.pass, { delay: rnd(40, 90) });
      await sleepA(600);
      await page.click('button[type=submit]').catch(() => page.keyboard.press('Enter'));
      await sleep(6000);

      url = page.url();
      const needEmail = await page.$('#email');
      const codeInput = await page.$('input[maxlength="6"]');

      // ---------- 3. 邮箱验证码 2FA ----------
      if (needEmail && codeInput) {
        result.code_needed = true;
        if (P.code) {
          // payload里已有验证码：直接走完流程
          log('使用已有验证码');
          await page.type('#email', P.email, { delay: rnd(30, 70) });
          await sleepA(1000);
          await clickGetCode();
          await sleepA(2500);
          if (!await submitCode(P.code)) {
            result.error = '验证码提交后仍停留在登录页';
            const txt = await page.evaluate(() => document.body.innerText);
            log('2FA 失败: ' + txt.replace(/\n/g, ' | ').slice(0, 150));
            await page.screenshot({ path: P.shotdir + '/2fa-fail.png' }).catch(() => {});
          }
        } else {
          // 首次进入：点发码，然后原地等待 Python 把验证码写到文件
          log('检测到 2FA，请求发送到配置邮箱');
          await page.type('#email', P.email, { delay: rnd(30, 70) });
          await sleepA(1000);
          await clickGetCode();
          await sleepA(2500);

          const codeFile = P.payloadPath + '.code';
          log('等待验证码文件: ' + codeFile);
          let got = null;
          const t0 = Date.now();
          while (Date.now() - t0 < 180000) {
            await sleep(3000);
            try {
              if (fs.existsSync(codeFile)) {
                const c = fs.readFileSync(codeFile, 'utf8').trim();
                if (/^\d{6}$/.test(c)) { got = c; break; }
              }
            } catch (e) { /* 文件尚未写入，继续等 */ }
          }
          if (got) {
            log('已获取验证码，继续提交');
            if (!await submitCode(got)) {
              result.error = '验证码提交后仍停留在登录页';
              const txt = await page.evaluate(() => document.body.innerText);
              log('2FA 失败: ' + txt.replace(/\n/g, ' | ').slice(0, 150));
            }
          } else {
            result.error = '等待验证码超时（180 秒）';
            log(result.error);
          }
        }
      } else if (!new RegExp(LOGIN_PATH).test(url)) {
        result.login_ok = true;
        log('登录成功（无需 2FA）');
      } else {
        result.error = '登录后状态异常：未见 2FA 也未跳转 -> ' + url;
      }
    }

    // ---------- 4. 随机漫游 ----------
    if (result.login_ok) {
      log('开始随机漫游');
      const pool = PAGES.slice().sort(() => Math.random() - 0.5);
      const nTarget = rnd(P.minPages, P.maxPages);
      const budgetMs = rnd(P.minMs, P.maxMs);
      const t0 = Date.now();
      let n = 0;
      const base = P.siteBase || P.url.replace(/\/(index|login).*$/, '');

      for (const pg of pool) {
        if (n >= nTarget) break;
        if (Date.now() - t0 > budgetMs) break;
        try {
          await page.goto(base + pg.u, { waitUntil: 'domcontentloaded' });
          await sleepA(rnd(2500, 5500));
          const title = await page.title();
          visited.push(pg.label + ' ' + pg.u + ' → ' + String(title).slice(0, 40));
          n++;
          log('访问 ' + n + '/' + nTarget + ' ' + pg.u);

          // 偶尔点进详情页，让访问轨迹更接近真人
          if (Math.random() < 0.3 && /\/(torrents|new|top)/.test(pg.u)) {
            const link = await page.$('a[href*="/detail/"], a[href*="/view/"]');
            if (link) {
              await link.click().catch(() => {});
              await sleepA(rnd(2500, 5000));
              visited.push('详情页 (随机进入)');
              n++;
            }
          }
        } catch (e) {
          log('跳过 ' + pg.u + ': ' + e.message);
        }
      }
      const secs = Math.round((Date.now() - t0) / 1000);
      log('漫游完成: ' + visited.length + ' 页 / ' + secs + ' 秒');
      await page.screenshot({ path: P.shotdir + '/last.png' }).catch(() => {});
    }

    if (!result.error && !result.login_ok && !result.code_needed) {
      result.error = '任务未完成登录';
    }
  } catch (e) {
    result.error = '执行异常: ' + e.message;
    console.error(e.stack);
  } finally {
    try { await browser.close(); } catch (e) { /* 忽略关闭异常 */ }
    out(result);
  }
})().catch(e => out({ visited: [], login_ok: false, error: '致命: ' + e.message }));