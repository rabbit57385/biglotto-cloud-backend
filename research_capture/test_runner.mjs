// Synthetic fixtures only. fetch is mocked; no Production requests or synchronization.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { main, verifyBinding, classifyCaptureError } from './capture-runner.mjs';
const marketing = process.env.CAPTURE_TEST_MARKETING_ROOT ?? resolve(import.meta.dirname, '../../../../sbd-data-lab');
const contract = await import(pathToFileURL(join(marketing,'src/lib/capture/contract.ts')).href);
const { renderApiFixture } = await import(pathToFileURL(join(marketing,'tests/fixtures/render-api.ts')).href);
const PIN='7d6d8f6bd56dfade8d6814fb698bb65f8e7bffb1';
const producer={...contract.captureSources.biglotto,commit:'1'.repeat(40),collector_commit:PIN,run_id:'123',attempt:1};
const at='2026-01-05T14:00:10Z', basis='2026-01-05T14:00:00Z';
globalThis.fetch=()=>{throw Error('LIVE_NETWORK_FORBIDDEN')};
async function fixture(change=()=>{}) {
 const f=renderApiFixture('biglotto'); for(const p of [f.before,f.after])p.last_successful_sync_at=basis;
 change(f);let count=0;
 const fetcher=async(url,init)=>{
   assert.equal(init.method,'GET');assert.equal(init.credentials,'omit');
   const u=new URL(url);assert.equal(u.origin,'https://five39-cloud-backend.onrender.com');
   const resource=u.pathname.split('/').at(-1);
   if(resource!=='freshness')assert.equal(u.search,'?include_special=false');
   return Response.json(resource==='freshness'?(++count===1?f.before:f.after):f.resources[resource]);
 };
 return contract.collectCapture({lottery:'biglotto',producer,fetcher,baseUrl:'https://five39-cloud-backend.onrender.com',now:()=>at,sleep:async()=>{}});
}
function proof() {const {collector_commit,...source}=producer; return {schema_version:'biglotto-sync-verification-v1',lottery_type:'biglotto',producer:source,draw_id:'900001',draw_date:'2026-01-05',numbers:[1,2,3,4,5,6],special_number:7,sync_revision:7,last_successful_sync_at:basis,verified_at:at,outcome_sha256:'0'.repeat(64)};}
const childEnv={GITHUB_REPOSITORY:producer.repository,CAPTURE_SOURCE_WORKFLOW:producer.workflow,GITHUB_REF_NAME:'master',GITHUB_SHA:producer.commit,GITHUB_RUN_ID:'123',GITHUB_RUN_ATTEMPT:'1',CAPTURE_COLLECTOR_COMMIT:PIN};

test('fixed module handles Big Lotto main_only, after_draw and unchanged contract',async()=>{
 const c=await fixture();verifyBinding(c,proof(),producer,at);
 assert.equal(c.snapshot.analysis_mode,'main_only');assert.equal(c.snapshot.grade_basis,'after_draw');
 assert.equal(c.snapshot.draw.special_number,7);assert.equal('sync_verification' in c,false);
 assert.equal(c.auto_publish,false);assert.equal(c.review_required,true);
});
test('before/after revision, period, date, six main numbers and special mismatch fail',async()=>{
 const c=await fixture();
 for(const key of ['before','after']) for(const [field,value] of [['source_revision',8],['special_number',8],['main_numbers',[1,2,3,4,5,8]],['basis_at','2026-01-05T14:00:01Z']]){
   const bad=structuredClone(c);bad[key][field]=value;assert.throws(()=>verifyBinding(bad,proof(),producer,at));
 }
 for(const [field,value] of [['draw_id','900002'],['draw_date','2026-01-06'],['source_revision',8],['grade_basis','before_draw']]){
   const bad=structuredClone(c);bad.snapshot[field]=value;assert.throws(()=>verifyBinding(bad,proof(),producer,at));
 }
});
test('original success time, not a newer freshness timestamp, bounds 180 seconds',async()=>{
 const c=await fixture();c.completed_at='2026-01-05T14:03:00Z';
 assert.throws(()=>verifyBinding(c,proof(),producer,c.completed_at),/CAPTURE_STALE/);
});
test('stale and before/after source changes fail through existing Adapter',async()=>{
 await assert.rejects(fixture(f=>{f.before.status='stale'}));
 await assert.rejects(fixture(f=>{f.after.sync_revision=8}));
 await assert.rejects(fixture(f=>{f.after.latest_draw.draw_no='900002'}));
});
test('complete wrapper validates using fixed module and refuses duplicate artifact writes',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'biglotto-capture-test-'));
 try {
   const file=join(dir,'proof.json');await writeFile(file,JSON.stringify(proof()));const c=await fixture();
   const load=async()=>({...contract,collectCapture:async options=>{
     assert.equal(options.lottery,'biglotto');assert.equal('syncVerification' in options,false);return c;
   }});
   assert.equal(await main(['--code',dir,'--proof',file,'--report',join(dir,'first.json')],{env:childEnv,load,clock:()=>at}),0);
   const report=JSON.parse(await readFile(join(dir,'first.json'),'utf8'));assert.match(report.file_sha256,/^[a-f0-9]{64}$/);
   assert.equal(await main(['--code',dir,'--proof',file,'--report',join(dir,'second.json')],{env:childEnv,load,clock:()=>at}),1);
   assert.equal(JSON.parse(await readFile(join(dir,'second.json'),'utf8')).reason,'CAPTURE_ARTIFACT_FAILED');
 } finally {if (!resolve(dir).startsWith(resolve(tmpdir())+sep)) throw Error('UNSAFE_TEST_CLEANUP'); await rm(dir,{recursive:true,force:true});}
});
test('hash corruption is rejected by unchanged contract validator before writing',async()=>{
 const c=await fixture();c.data_sha256='0'.repeat(64);assert.throws(()=>contract.validateCapture(c));
});
test('error classification never emits raw credential exceptions',()=>{
 for(const [error,expected] of [[{code:'TIMEOUT'},'CAPTURE_TIMEOUT'],[{code:'REVISION_CHANGED'},'CAPTURE_REVISION_CHANGED'],[{message:'CAPTURE_TTL_EXPIRED'},'CAPTURE_STALE'],[Error('SYNTHETIC_SECRET'),'CAPTURE_FAILED']])assert.equal(classifyCaptureError(error),expected);
});
