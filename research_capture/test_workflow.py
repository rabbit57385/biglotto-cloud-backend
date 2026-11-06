"""Narrow YAML, fixed-version, official-flow regression and secret-boundary checks."""
import ast
import json
from pathlib import Path
import subprocess
import sys
import unittest

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
import bridge
try: import yaml
except ImportError:
    sys.path.insert(0,str(HERE.parents[2]/'tools/pyyaml-6.0.3'))
    import yaml

class StrictLoader(yaml.BaseLoader): pass
def mapping(loader,node):
    values={}
    for key,value in node.value:
        k=loader.construct_object(key)
        if k in values: raise ValueError('Duplicate YAML mapping key')
        values[k]=loader.construct_object(value)
    return values
StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,mapping)
def load(text): return yaml.load(text,Loader=StrictLoader)
BASE='6146a95fef42145d39177be0e3168823018f1216'

def official_code(text):
    content=text.split("          python -B - <<'PY'\n",1)[1].rsplit('          PY',1)[0]
    return '\n'.join(line[10:] for line in content.splitlines())

class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text=(bridge.ROOT/bridge.WORKFLOW).read_text(encoding='utf-8')
        cls.workflow=load(cls.text)
        cls.preflight=load((bridge.ROOT/'.github/workflows/capture-code-preflight.yml').read_text(encoding='utf-8'))
        cls.baseline=subprocess.run(['git','-c','safe.directory='+str(bridge.ROOT),'show',BASE+':'+bridge.WORKFLOW],cwd=bridge.ROOT,capture_output=True,text=True,check=True).stdout
        cls.old=load(cls.baseline)
        cls.steps=cls.workflow['jobs']['update-biglotto']['steps']
        cls.ids={s.get('id'):s for s in cls.steps if s.get('id')}

    def test_yaml_both_documents_valid_no_duplicate_mapping(self):
        self.assertIsInstance(self.workflow,dict);self.assertIsInstance(self.preflight,dict)
        self.assertIn('workflow_dispatch',self.preflight['on'])
        self.assertEqual(set(self.preflight['on']),{'workflow_dispatch'})
        for text in (self.text,(bridge.ROOT/'.github/workflows/capture-code-preflight.yml').read_text(encoding='utf-8')):
            self.assertNotIn('\t',text)

    def test_cron_concurrency_dispatch_and_job_timeout_unchanged(self):
        self.assertEqual(self.workflow['on'],self.old['on'])
        self.assertEqual(self.workflow['concurrency'],self.old['concurrency'])
        self.assertEqual(self.workflow['jobs']['update-biglotto']['timeout-minutes'],'15')

    def test_official_python_unchanged_except_caught_optional_evidence_hook(self):
        original=ast.parse(official_code(self.baseline));new=ast.parse(official_code(self.text))
        main=next(n for n in new.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        hooks=[n for n in main.body if isinstance(n,ast.Try) and any(isinstance(k,ast.ImportFrom) and k.module=='research_capture.bridge' for k in n.body)]
        self.assertEqual(len(hooks),1)
        self.assertEqual(len(hooks[0].handlers),1)
        self.assertEqual(hooks[0].handlers[0].type.id,'BaseException')
        main.body.remove(hooks[0])
        self.assertEqual(ast.dump(original),ast.dump(new))

    def test_original_dry_run_and_single_catchup_are_preserved(self):
        old_preview=next(s for s in self.old['jobs']['update-biglotto']['steps'] if s['name']=='Preview missing Big Lotto draws')
        self.assertEqual(next(s for s in self.steps if s['name']==old_preview['name']),old_preview)
        self.assertEqual(official_code(self.text).count("request('/catch-up', token)"),1)
        self.assertEqual(self.ids['update_draw']['if'],self.old['jobs']['update-biglotto']['steps'][-1]['if'])
        self.assertNotIn('CAPTURE_AUTH_TOKEN',self.ids['update_draw']['env'])
        changed=subprocess.run(['git','-c','safe.directory='+str(bridge.ROOT),'diff','--name-only'],cwd=bridge.ROOT,capture_output=True,text=True,check=True).stdout.splitlines()
        self.assertEqual(changed,[bridge.WORKFLOW])

    def test_prepare_before_sync_and_all_sidecar_steps_isolated(self):
        ids=[s.get('id') for s in self.steps]
        self.assertLess(ids.index('capture_dependencies'),ids.index('update_draw'))
        for name in ('capture_switch','capture_plan','capture_checkout','capture_commit','capture_node','capture_dependencies','capture_gate','capture_collect','capture_upload'):
            self.assertEqual(self.ids[name]['continue-on-error'],'true')
        for name in ('capture_plan','capture_checkout','capture_commit','capture_node','capture_dependencies','capture_collect','capture_upload'):
            self.assertIn("steps.capture_switch.outputs.enabled == 'true'",self.ids[name]['if'])
        self.assertIn('inputs.dry_run == false',self.ids['capture_switch']['if'])
        self.assertIn("steps.capture_collect.outputs.ready == 'true'",self.ids['capture_upload']['if'])
        self.assertIn("steps.capture_gate.outputs.capture_eligible == 'true'",self.ids['capture_collect']['if'])

    def test_fixed_private_checkout_and_permissions(self):
        self.assertEqual(self.workflow['permissions'],{'contents':'read'})
        self.assertEqual(self.workflow['jobs']['update-biglotto']['permissions'],{'contents':'read','actions':'read'})
        for checkout in (self.ids['capture_checkout'],next(s for s in self.preflight['jobs']['preflight']['steps'] if s.get('id')=='code')):
            self.assertEqual(checkout['with']['repository'],bridge.SOURCE_REPO)
            self.assertEqual(checkout['with']['persist-credentials'],'false')
            self.assertEqual(checkout['with']['token'],'${{ secrets.RESEARCH_CAPTURE_CODE_READ_TOKEN }}')
            self.assertNotIn('main',checkout['with']['ref']);self.assertNotIn('latest',checkout['with']['ref'])
        self.assertEqual(bridge.source_config()['commit'],bridge.CAPTURE_COMMIT)
        self.assertNotIn('RESEARCH_STATE_REPO_TOKEN',self.text)

    def test_preflight_has_no_db_sync_capture_or_artifact_step(self):
        text=(bridge.ROOT/'.github/workflows/capture-code-preflight.yml').read_text(encoding='utf-8')
        for forbidden in ('DATABASE_URL','BIGLOTTO_CATCHUP_TOKEN','catch-up','upload-artifact','capture-research.ts','schedule:'):
            self.assertNotIn(forbidden,text)
        self.assertEqual(self.preflight['permissions'],{'contents':'read'})
        self.assertIn("github.ref == 'refs/heads/master'",self.preflight['jobs']['preflight']['if'])

    def test_upload_exact_file_not_directory_or_private_checkout(self):
        upload=self.ids['capture_upload']['with']
        self.assertTrue(upload['path'].endswith('/research-capture.json'))
        self.assertNotIn('*',upload['path'])
        self.assertEqual(upload['overwrite'],'false');self.assertEqual(upload['include-hidden-files'],'false')
        self.assertEqual(upload['retention-days'],'7')
        self.assertEqual(set(self.ids['capture_collect']['env']),{'CAPTURE_AUTH_TOKEN'})

if __name__=='__main__': unittest.main(verbosity=2)
