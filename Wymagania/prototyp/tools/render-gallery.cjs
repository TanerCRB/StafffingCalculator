const path=require('path');
const {pathToFileURL}=require('url');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'C:/Users/Taner/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..');
(async()=>{const b=await chromium.launch({headless:true});const p=await b.newPage({viewport:{width:1600,height:1100},deviceScaleFactor:1});
await p.goto(pathToFileURL(path.join(root,'design-overview.html')).href);await p.evaluate(()=>document.fonts.ready);await p.screenshot({path:path.join(root,'design-overview.png'),fullPage:true});
for(let i=1;i<=6;i++){await p.goto(pathToFileURL(path.join(root,'tools','review-'+i+'.html')).href);await p.evaluate(()=>document.fonts.ready);await p.screenshot({path:path.join(root,'tools','review-'+i+'.png'),fullPage:true});}
await p.goto(pathToFileURL(path.join(root,'gallery.html')).href);await p.locator('#search').fill('F-06');const count=await p.locator('#desktop article:visible').count();if(count!==4)throw new Error('Gallery search mismatch: '+count);await b.close();console.log('Overview + six visual review sheets rendered; gallery search verified.');})().catch(e=>{console.error(e);process.exit(1)});
