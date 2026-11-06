"""SYNTHETIC FIXTURES ONLY. No live HTTP, credentials, DB, or official synchronization."""
import ast
import contextlib
import copy
from datetime import timedelta
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bridge
import preflight
TIME = bridge.timestamp('2026-01-05T14:00:10Z')


def env():
    return {'GITHUB_ACTIONS': 'true', 'GITHUB_REPOSITORY': bridge.UPDATER_REPO,
            'GITHUB_REF_NAME': 'master', 'GITHUB_REF': 'refs/heads/master',
            'GITHUB_WORKFLOW_REF': bridge.UPDATER_REPO + '/' + bridge.WORKFLOW + '@refs/heads/master',
            'GITHUB_SHA': '1' * 40, 'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
            'GITHUB_EVENT_NAME': 'schedule', 'RESEARCH_CAPTURE_ENABLED': 'true',
            'CAPTURE_CODE_TOKEN_AVAILABLE': 'true', 'CAPTURE_AUTH_TOKEN': 'SYNTHETIC_AUTH_TOKEN',
            'DATABASE_URL': 'SYNTHETIC_DATABASE_VALUE', 'BIGLOTTO_CATCHUP_TOKEN': 'SYNTHETIC_SYNC_TOKEN'}


def outcome():
    draw = {'draw_no': '900001', 'draw_date': '2026-01-05', 'numbers': [1,2,3,4,5,6], 'special_number': 7}
    fresh = {'status': 'up_to_date', 'sync_revision': 7, 'latest_draw': draw,
             'latest_verified_draw': copy.deepcopy(draw), 'sync_in_progress': False, 'last_error': None,
             'last_successful_sync_at': '2026-01-05T14:00:00Z'}
    recent = {'status': 'ok', 'data': {'include_special': False, 'analysis_mode': 'main_only', 'count': 1,
              'draws': [{**draw, 'numbers': [{'number': n} for n in draw['numbers']], 'special_number': {'number': 7}}]}}
    return {'disposition': 'completed', 'result_status': 'ok', 'dry_run': False,
            'accepted_freshness': fresh, 'checked_freshness': copy.deepcopy(fresh), 'recent': recent}


def workflow_code():
    text = (bridge.ROOT / bridge.WORKFLOW).read_text(encoding='utf-8')
    block = text.split("          python -B - <<'PY'\n", 1)[1].rsplit('          PY', 1)[0]
    return '\n'.join(line[10:] for line in block.splitlines())


class GateTests(unittest.TestCase):
    def test_official_success_emits_biglotto_sidecar_proof(self):
        proof = bridge.verify_sync(outcome(), 0, env(), TIME)
        self.assertEqual(proof['schema_version'], 'biglotto-sync-verification-v1')
        self.assertEqual(proof['sync_revision'], 7)
        self.assertEqual(proof['numbers'], [1,2,3,4,5,6])
        self.assertEqual(proof['special_number'], 7)
        self.assertNotIn('data_revision', proof)
        self.assertRegex(proof['outcome_sha256'], r'^[a-f0-9]{64}$')

    def test_cooldown_in_progress_failed_uncertain_never_authorize(self):
        for status in ('cooldown','in_progress','failed','uncertain','verification_required'):
            value = outcome(); value['disposition'] = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                bridge.verify_sync(value, 0, env(), TIME)

    def test_dry_run_and_nonzero_exit_rejected(self):
        value = outcome(); value['dry_run'] = True
        for payload, code in ((value,0),(outcome(),1)):
            with self.assertRaises(ValueError): bridge.verify_sync(payload, code, env(), TIME)
        with self.assertRaises(ValueError): bridge.verify_sync(outcome(),0,{**env(),'CAPTURE_DRY_RUN':'true'},TIME)

    def test_missing_fields_and_unverified_result_rejected(self):
        for field in outcome():
            value = outcome(); del value[field]
            with self.subTest(field=field), self.assertRaises((ValueError,KeyError)):
                bridge.verify_sync(value,0,env(),TIME)
        for field in ('status','sync_revision','latest_draw','latest_verified_draw','last_successful_sync_at','last_error','sync_in_progress'):
            value = outcome(); del value['checked_freshness'][field]
            with self.subTest(field=field), self.assertRaises((ValueError,KeyError)):
                bridge.verify_sync(value,0,env(),TIME)
        value=outcome(); value['result_status']='commit_outcome_unknown'
        with self.assertRaises(ValueError): bridge.verify_sync(value,0,env(),TIME)

    def test_stale_expired_future_and_in_progress_rejected(self):
        for changes in ({'status':'stale'}, {'sync_in_progress':True}, {'last_error':'uncertain'},
                        {'last_successful_sync_at':'2026-01-05T13:57:10Z'},
                        {'last_successful_sync_at':'2026-01-05T14:01:00Z'}):
            value=outcome(); value['checked_freshness'].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError): bridge.verify_sync(value,0,env(),TIME)

    def test_revision_identity_date_main_special_mismatch_rejected(self):
        for field,value in (('draw_no','900002'),('draw_date','2026-01-06'),('numbers',[1,2,3,4,5,8]),('special_number',8)):
            data=outcome(); data['checked_freshness']['latest_draw'][field]=value
            with self.subTest(field=field), self.assertRaises(ValueError): bridge.verify_sync(data,0,env(),TIME)
        for field,value in (('sync_revision',8),('last_successful_sync_at','2026-01-05T14:00:01Z')):
            data=outcome(); data['checked_freshness'][field]=value
            with self.assertRaises(ValueError): bridge.verify_sync(data,0,env(),TIME)

    def test_recent_main_only_and_special_identity_required(self):
        for change in ('mode','special','numbers','count'):
            value=outcome(); d=value['recent']['data']
            if change=='mode': d['include_special']=True
            elif change=='special': d['draws'][0]['special_number']['number']=8
            elif change=='numbers': d['draws'][0]['numbers'][0]['number']=9
            else: d['count']=0
            with self.subTest(change=change), self.assertRaises(ValueError): bridge.verify_sync(value,0,env(),TIME)

    def test_same_revision_official_reverification_is_accepted(self):
        self.assertEqual(bridge.verify_sync(outcome(),0,env(),TIME)['sync_revision'],
                         bridge.verify_sync(outcome(),0,env(),TIME+timedelta(seconds=1))['sync_revision'])

    def test_disabled_even_when_secret_present_and_dry_run(self):
        for flag in (None,'','false','TRUE','true ',True):
            values=env()
            if flag is None: values.pop('RESEARCH_CAPTURE_ENABLED')
            else: values['RESEARCH_CAPTURE_ENABLED']=flag
            with self.subTest(flag=flag), patch.object(bridge,'_github_get') as api:
                self.assertFalse(bridge.capture_enabled(values)); self.assertEqual(bridge.plan(values)['ready'],'false')
                api.assert_not_called()
        self.assertEqual(bridge.plan(env())['ready'],'true')
        self.assertEqual(bridge.plan({**env(),'CAPTURE_DRY_RUN':'true'})['ready'],'false')

    def test_disabled_capture_never_reads_or_launches(self):
        values=env(); values.pop('RESEARCH_CAPTURE_ENABLED')
        with patch.object(bridge,'load_json') as load, patch.object(bridge,'verify_checkout') as checkout:
            with self.assertRaisesRegex(ValueError,'CAPTURE_DISABLED'): bridge.collect(values,'unused','unused')
            load.assert_not_called(); checkout.assert_not_called()

    def test_fixed_sha_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError,'CHECKOUT_COMMIT_MISMATCH'):
            bridge.verify_checkout('.',bridge.CAPTURE_COMMIT,run=lambda *a,**kw:types.SimpleNamespace(stdout='0'*40))
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source.json'; path.write_text(json.dumps({'repository':bridge.SOURCE_REPO,'project_path':'.','commit':'0'*40}))
            with self.assertRaises(ValueError): bridge.source_config(path)

    def test_update_code_retains_single_sync_and_isolates_sidecar_failure(self):
        code=workflow_code(); ns={'__name__':'offline_test'}; exec(compile(code,'workflow','exec'),ns)
        value=outcome()
        responses=[(200,{'disposition':'completed','result':{'status':'ok'},'freshness':value['accepted_freshness']}),
                   (200,value['checked_freshness']),(200,value['recent'])]
        calls=[]
        def request(path,token=None): calls.append(path); return responses.pop(0)
        with patch.dict(os.environ,{'BIGLOTTO_CATCHUP_TOKEN':'SYNTHETIC_'+'x'*40,'CAPTURE_AUTHORIZED':'true'}), \
             patch.dict(ns,{'request':request}), patch.object(bridge,'save_sync_evidence',side_effect=RuntimeError('SYNTHETIC_PRIVATE_ERROR')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(ns['main'](),0)
        self.assertEqual(calls,['/catch-up','/freshness','/recent-draws?limit=1&include_special=false'])
        self.assertNotIn('SYNTHETIC_PRIVATE_ERROR',output.getvalue())
        self.assertIn('APP update remains successful',output.getvalue())

    def test_sidecar_proof_file_contains_no_environment_or_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            value=outcome()
            result={'disposition':'completed','result':{'status':'ok'},'freshness':value['accepted_freshness']}
            values={**env(),'RUNNER_TEMP':temp,'CAPTURE_AUTHORIZED':'true'}
            bridge.save_sync_evidence(result,value['checked_freshness'],value['recent'],values)
            text=(Path(temp)/'biglotto-sync-evidence.json').read_text()
            self.assertNotIn('SYNTHETIC_AUTH_TOKEN',text);self.assertNotIn('SYNTHETIC_DATABASE_VALUE',text)
            self.assertEqual(bridge.verify_sync(json.loads(text),0,env(),TIME)['sync_revision'],7)
            with self.assertRaises(FileExistsError): bridge.save_sync_evidence(result,value['checked_freshness'],value['recent'],values)

    def test_cli_masks_raw_exceptions(self):
        with patch.dict(os.environ,env(),clear=True), patch.object(sys,'argv',['bridge.py','capture','--proof','unused']), \
             patch.object(bridge,'collect',side_effect=RuntimeError('SYNTHETIC_SECRET_VALUE')), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(bridge.main(),1)
        self.assertNotIn('SYNTHETIC_SECRET_VALUE',output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['ready'],'false')

    def test_preflight_identity_and_clean_environment(self):
        values=env(); values.update(GITHUB_EVENT_NAME='workflow_dispatch',GITHUB_WORKFLOW_REF=bridge.UPDATER_REPO+'/'+preflight.WORKFLOW+'@refs/heads/master')
        preflight.context(values)
        with self.assertRaises(ValueError): bridge.producer(values)
        calls=[]
        def run(command,**kw):
            calls.append(command); self.assertFalse(any('TOKEN' in k or 'DATABASE' in k for k in kw['env']))
            return types.SimpleNamespace(stdout='v24.21.0\n',returncode=0)
        preflight.check_node(run,values); preflight.check_imports('.',run,values)
        self.assertIn('PREFLIGHT_NETWORK_FORBIDDEN',calls[-1][-1])
        self.assertNotIn('collectCapture(',calls[-1][-1])

    def test_summary_classifies_artifact_failure_without_changing_app(self):
        values={'APP_UPDATE_OUTCOME':'success','CAPTURE_AUTHORIZED':'true','PLAN_READY':'true',
                'CHECKOUT_OUTCOME':'success','COMMIT_OUTCOME':'success','NODE_OUTCOME':'success',
                'CAPTURE_PREPARED':'success','SYNC_ELIGIBLE':'true','CAPTURE_READY':'true','UPLOAD_OUTCOME':'failure'}
        report=bridge.summary(values)
        self.assertEqual(report['capture_status'],'CAPTURE_ARTIFACT_FAILED')
        self.assertEqual(report['app_update_outcome'],'success')
        self.assertIs(report['auto_publish'],False)


class CaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        marketing=Path(os.environ.get('CAPTURE_TEST_MARKETING_ROOT', HERE.parents[3]/'sbd-data-lab'))
        script="""import { renderApiFixture } from './tests/fixtures/render-api.ts';
import { collectCapture, captureSources } from './src/lib/capture/contract.ts';
const f=renderApiFixture('biglotto');
for(const p of [f.before,f.after]) p.last_successful_sync_at='2026-01-05T14:00:00Z';
let reads=0; globalThis.fetch=()=>{throw Error('LIVE_NETWORK_FORBIDDEN')};
const fetcher=async(url,init)=>{
 if(init.method!=='GET'||init.credentials!=='omit') throw Error('UNSAFE');
 const u=new URL(url), name=u.pathname.split('/').at(-1);
 if(name!=='freshness'&&u.search!=='?include_special=false') throw Error('UNSAFE_MODE');
 return Response.json(name==='freshness'?(++reads===1?f.before:f.after):f.resources[name]);
};
const producer={...captureSources.biglotto,commit:'1'.repeat(40),collector_commit:'7d6d8f6bd56dfade8d6814fb698bb65f8e7bffb1',run_id:'123',attempt:1};
console.log(JSON.stringify(await collectCapture({lottery:'biglotto',producer,fetcher,
 baseUrl:'https://five39-cloud-backend.onrender.com',now:()=> '2026-01-05T14:00:10Z',sleep:async()=>{}})));
"""
        result=subprocess.run(['node','--import','tsx','--input-type=module','-e',script],cwd=marketing,capture_output=True,text=True,timeout=30,check=True)
        cls.capture=json.loads(result.stdout)

    def proof(self): return bridge.verify_sync(outcome(),0,env(),TIME)
    def validate(self,capture): return bridge.validate_bound_capture(capture,self.proof(),bridge.producer(env()),bridge.CAPTURE_COMMIT,TIME)

    def test_real_fixed_module_contract_fixture_binds_and_is_public_safe(self):
        self.validate(copy.deepcopy(self.capture)); self.assertEqual(bridge.public_artifact(self.capture,env()),'PASS')
        self.assertNotIn('sync_verification',self.capture)

    def test_changed_before_after_and_snapshot_rejected(self):
        for scope in ('before','after'):
            for field,value in (('source_revision',8),('special_number',8),('main_numbers',[1,2,3,4,5,8]),
                                ('basis_at','2026-01-05T14:00:01Z'),('status','stale')):
                capture=copy.deepcopy(self.capture); capture[scope][field]=value
                with self.subTest(scope=scope,field=field), self.assertRaises(ValueError): self.validate(capture)
        capture=copy.deepcopy(self.capture); capture['snapshot']['draw_date']='2026-01-06'
        with self.assertRaises(ValueError): self.validate(capture)

    def test_original_success_time_expiry_and_before_draw_rejected(self):
        capture=copy.deepcopy(self.capture); capture['completed_at']='2026-01-05T14:03:00Z'
        with self.assertRaisesRegex(ValueError,'CAPTURE_STALE'):
            bridge.validate_bound_capture(capture,self.proof(),bridge.producer(env()),bridge.CAPTURE_COMMIT,TIME+timedelta(minutes=3))
        capture=copy.deepcopy(self.capture); capture['snapshot']['grade_basis']='before_draw'
        with self.assertRaises(ValueError): self.validate(capture)

    def test_public_artifact_rejects_private_payload_at_any_allowed_container(self):
        for scope in ('top','snapshot','metric','source','producer'):
            capture=copy.deepcopy(self.capture)
            target={'top':capture,'snapshot':capture['snapshot'],'metric':capture['snapshot']['metrics']['momentum']['value'][0],
                    'source':capture['snapshot']['source'],'producer':capture['producer']}[scope]
            target['internal_strategy']='SYNTHETIC_PRIVATE'
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError,'PUBLIC_ARTIFACT_BLOCKED'): bridge.public_artifact(capture)

    def test_public_artifact_rejects_credentials_and_nonpublic_source(self):
        for secret in ('postgresql://SYNTHETIC_CONNECTION','github_pat_'+'x'*30,'-----BEGIN PRIVATE KEY-----','SYNTHETIC_AUTH_TOKEN'):
            capture=copy.deepcopy(self.capture); capture['snapshot']['analysis_version']=secret
            with self.subTest(secret=secret[:10]), self.assertRaisesRegex(ValueError,'PUBLIC_ARTIFACT_BLOCKED'): bridge.public_artifact(capture,env())
        capture=copy.deepcopy(self.capture); capture['snapshot']['source']['methodology']='SYNTHETIC_INTERNAL_METHOD'
        with self.assertRaisesRegex(ValueError,'PUBLIC_ARTIFACT_BLOCKED'): bridge.public_artifact(capture)

    def test_child_gets_no_secret_and_parent_validates_file_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            directory=Path(temp); proof=directory/'proof.json'; proof.write_text(json.dumps(self.proof()))
            code=directory/'code'; calls=[]
            artifact=code/'artifacts/captures/research-capture-v1-biglotto-123-1/research-capture.json'
            def run(command,**kwargs):
                calls.append(command)
                if command[0]=='git': return types.SimpleNamespace(stdout=bridge.CAPTURE_COMMIT)
                child=kwargs['env']; self.assertFalse(any('TOKEN' in k or 'DATABASE' in k or 'SECRET' in k for k in child))
                self.assertNotIn('NODE_OPTIONS',child); self.assertNotIn('GITHUB_TOKEN',child)
                artifact.parent.mkdir(parents=True); artifact.write_text(json.dumps(self.capture))
                (directory/'biglotto-capture-status.json').write_text(json.dumps({'ready':'true','reason':'CAPTURE_VALIDATED','file_sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}))
                return types.SimpleNamespace(returncode=0)
            result=bridge.collect({**env(),'NODE_OPTIONS':'--import SYNTHETIC_PRIVATE'},proof,code,run,TIME)
            self.assertEqual(result['ready'],'true'); self.assertEqual(len(calls),2)

    def test_failed_child_and_bad_file_hash_never_return_ready(self):
        for failure in ('timeout_status','bad_hash','not_ready'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temp:
                directory=Path(temp);proof=directory/'proof.json';proof.write_text(json.dumps(self.proof()))
                def run(command,**kwargs):
                    if command[0]=='git':return types.SimpleNamespace(stdout=bridge.CAPTURE_COMMIT)
                    artifact=directory/'artifacts/captures/research-capture-v1-biglotto-123-1/research-capture.json'
                    artifact.parent.mkdir(parents=True);artifact.write_text(json.dumps(self.capture))
                    status={'ready':'true','reason':'CAPTURE_VALIDATED','file_sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}
                    if failure=='timeout_status':status={'ready':'false','reason':'CAPTURE_TIMEOUT'}
                    if failure=='bad_hash':status['file_sha256']='0'*64
                    if failure=='not_ready':status['ready']='false'
                    (directory/'biglotto-capture-status.json').write_text(json.dumps(status))
                    return types.SimpleNamespace(returncode=1 if failure=='timeout_status' else 0)
                with self.assertRaises(ValueError):bridge.collect(env(),proof,directory,run,TIME)

    def test_timeout_is_bounded_and_never_retries(self):
        with tempfile.TemporaryDirectory() as temp:
            proof=Path(temp)/'proof.json'; proof.write_text(json.dumps(self.proof()))
            calls=[]
            def run(command,**kw):
                calls.append(command)
                if command[0]=='git': return types.SimpleNamespace(stdout=bridge.CAPTURE_COMMIT)
                self.assertLessEqual(kw['timeout'],170)
                raise subprocess.TimeoutExpired(command,kw['timeout'])
            with self.assertRaisesRegex(ValueError,'CAPTURE_TIMEOUT'): bridge.collect(env(),proof,temp,run,TIME)
            self.assertEqual(len(calls),2)


if __name__=='__main__': unittest.main(verbosity=2)
