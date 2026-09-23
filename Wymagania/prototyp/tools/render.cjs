/* Renderowanie artefaktu projektowego i sprawdzenie podstawowych interakcji. */
const fs = require('fs');
const path = require('path');
const {pathToFileURL} = require('url');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/Taner/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const assert = (value, message) => { if (!value) throw new Error(message); };
(async()=>{
 const browser=await chromium.launch({headless:true});
 const page=await browser.newPage({viewport:{width:1440,height:1080},deviceScaleFactor:1});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base=pathToFileURL(path.join(root,'index.html')).href;
 await page.goto(base);await page.evaluate(()=>document.fonts.ready);
 const manifest=await page.evaluate(()=>screens.map(([route,id,title])=>({route,id,title})));
 const checks=[];
 for(const s of manifest){
  await page.goto(base+'#'+s.route);await page.evaluate(()=>document.fonts.ready);
  await page.evaluate(()=>document.body.classList.add('export-mode'));
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
  assert(!overflow,'Horizontal document overflow: '+s.route);
  const filename=s.id.slice(3)+'-'+s.route+'.png';
  await page.screenshot({path:path.join(root,'screens',filename),fullPage:true});
  s.image='screens/'+filename;s.width=1440;s.height=await page.evaluate(()=>document.documentElement.scrollHeight);
  checks.push({screen:s.id,route:s.route,pageOverflow:false});
 }
 const dataset=await page.evaluate(()=>({currency:'PLN',period:{start:'2026-09-01',end:'2027-02-28'},months,hours,positions,additionalCosts:extras,monthly,totals,plannedHours,billableHours,disclaimer:'Synthetic design fixture; not current rates or a production engine.'}));
 assert(dataset.totals.revenue===1124676,'Revenue mismatch');assert(dataset.totals.cost===789020,'Cost mismatch');assert(dataset.totals.profit===335656,'Profit mismatch');
 assert(dataset.additionalCosts.reduce((s,x)=>s+x,0)===42000,'Extra costs mismatch');
 fs.writeFileSync(path.join(root,'spec','demo-data.json'),JSON.stringify(dataset,null,2));
 await page.goto(base+'#projects');
 await page.getByRole('searchbox').fill('ERP');
 assert(await page.locator('#project-rows tr:visible').count()===1,'Search result mismatch');
 await page.getByRole('searchbox').fill('');await page.locator('#project-filter').selectOption('Archived');
 assert(await page.locator('#project-rows tr:visible').count()===1,'Archive filter mismatch');
 await page.goto(base+'#sensitivity');await page.locator('#salary-slider').fill('10');await page.locator('#rate-slider').fill('-5');
 assert(await page.locator('#sim-profit').innerText()==='204,720','Sensitivity profit mismatch');
 await page.goto(base+'#staffing');await page.locator('[data-unit="Hours"]').click();
 assert(await page.locator('.allocation').first().innerText()==='176','Hours conversion mismatch');
 await page.locator('[data-unit="FTE"]').click();await page.locator('.allocation').first().click();
 assert(await page.locator('dialog').isVisible(),'Allocation dialog missing');
 await page.keyboard.press('Escape');assert(!await page.locator('dialog').isVisible(),'Escape did not close dialog');
 await page.goto(base+'#versions');await page.locator('[data-action="approve"]').click();
 assert(!await page.locator('dialog').isVisible(),'Approval bypassed confirmation');
 await page.locator('#approve-check').check();await page.locator('[data-action="approve"]').click();assert(await page.locator('dialog').isVisible(),'Approval dialog missing');await page.locator('[data-action="confirm-approval"]').click();await page.waitForURL('**#approved');assert(await page.locator('.context').innerText().then(t=>t.includes('Version 3')),'Approved preview version mismatch');
 await page.goto(base+'#create');await page.locator('#new-project-name').fill('Prototype verification');await page.getByRole('button',{name:'Create project',exact:true}).click();
 await page.waitForURL('**#projects');assert(await page.locator('#project-rows').innerText().then(t=>t.includes('Prototype verification')),'Project not added');
 const mobile=[];
 await page.setViewportSize({width:390,height:844});
 for(const [route,title] of [['overview','Mobile summary'],['projects','Mobile project list'],['points','Mobile commercial terms']]){
  await page.goto(base+'#'+route);await page.evaluate(()=>document.fonts.ready);await page.evaluate(()=>document.body.classList.add('export-mode'));
  assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Mobile overflow: '+route);
  const filename='mobile-'+route+'.png';await page.screenshot({path:path.join(root,'screens',filename),fullPage:true});mobile.push({route,title,image:'screens/'+filename,width:390});
 }
 assert(errors.length===0,'Browser errors: '+errors.join('; '));
 fs.writeFileSync(path.join(root,'spec','screen-manifest.json'),JSON.stringify({desktop:manifest,mobile},null,2));
 fs.writeFileSync(path.join(root,'spec','verification.json'),JSON.stringify({date:'2026-09-19',scope:'Standalone design prototype only',viewport:{width:1440,height:1080},screens:checks,mobile,checks:['24 screen routes render','No document overflow at desktop and mobile widths','Search and archive filtering','Sensitivity values','FTE / hours toggle','Allocation dialog and Escape','Explicit approval confirmation','In-memory project creation','Fixture arithmetic'],browserErrors:errors},null,2));
 await browser.close();console.log(JSON.stringify({desktop:manifest.length,mobile:mobile.length,errors,totals:dataset.totals}));
})().catch(e=>{console.error(e);process.exit(1)});
