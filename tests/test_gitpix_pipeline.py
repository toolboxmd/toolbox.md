import copy
import base64
import io
import json
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import gitpix_pipeline as gp
import site_pipeline as sp
import test_site_pipeline as agent_tests
from test_site_pipeline import git, write_json


def fixture_bundle(sha):
    def run(id, name, path, event):
        return {'id': id, 'run_attempt': 1, 'name': name, 'path': path, 'event': event,
                'head_branch': 'main', 'status': 'completed', 'conclusion': 'success', 'head_sha': sha,
                'repository': {'id': gp.REPO_ID, 'full_name': gp.REPO}, 'head_repository': {'id': gp.REPO_ID}}
    receipt = {'schema': 1, 'project': 'gitpix', 'operation': 'activation', 'status': 'activated',
               'sourceSha': sha, 'version': '3.1.2',
               'artifact': {'id': 200, 'name': f'gitpix-3.1.2-{sha}', 'payloadSha256': 'b'*64, 'uploadDigest': 'sha256:'+'c'*64},
               'target': {'host': 'bigbrain', 'service': 'gitpix.service', 'currentLink': '/var/www/gitpix/current'},
               'production': {'status': 'healthy', 'httpStatus': 200}, 'publicEdge': {'status': 'expected', 'httpStatus': 302},
               'release': {'payloadSha256': 'b'*64},
               'trigger': {'workflow': 'Release Candidate', 'path': gp.CANDIDATE, 'event': 'push', 'branch': 'main',
                           'conclusion': 'success', 'sourceSha': sha, 'repository': gp.REPO, 'runId': 10, 'runAttempt': 1}}
    bundle = {'schema': 1, 'activationRun': run(20, 'Activate Release', gp.ACTIVATE, 'workflow_run'),
            'triggerRun': run(10, 'Release Candidate', gp.CANDIDATE, 'push'), 'receipt': receipt,
            'receiptArtifact': {'id': 300, 'name': 'gitpix-activation-receipt-20-1', 'expired': False, 'digest': 'sha256:'+'d'*64},
            'release': {'tag_name': 'v3.1.2', 'draft': False, 'prerelease': False, 'published_at': '2026-09-07T10:00:00Z'},
            'manifestSha256': 'e'*64,
            'manifest': {'schema': 1, 'project': 'gitpix', 'version': '3.1.2', 'source': {'sha': sha},
                         'artifact': {'name': 'gitpix-3.1.2.tar.gz', 'digest': 'sha256', 'sha256': 'b'*64},
                         'provenance': {'workflow': 'Release Candidate', 'runId': '10', 'runAttempt': '1', 'workflowRef': gp.REPO+'/'+gp.CANDIDATE+'@refs/heads/main'}}}

    bundle['receiptArtifact']['workflow_run'] = {'id':20, 'repository_id':gp.REPO_ID, 'head_repository_id':gp.REPO_ID, 'head_branch':'main', 'head_sha':sha}
    bundle['payloadArtifact'] = {'id':200, 'name':receipt['artifact']['name'], 'digest':receipt['artifact']['uploadDigest'], 'expired':False, 'workflow_run':{**bundle['receiptArtifact']['workflow_run'], 'id':10}}
    seal_bundle(bundle)
    return bundle


def seal_bundle(bundle):
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as z:
        z.writestr(bundle['receiptArtifact']['name']+'.json', json.dumps(bundle['receipt']))
    bundle['receiptArchive'] = base64.b64encode(raw.getvalue()).decode()
    bundle['receiptArtifact']['digest'] = 'sha256:'+sp.sha256_bytes(raw.getvalue())
    manifest = json.dumps(bundle['manifest']).encode()
    bundle['manifestRaw'] = base64.b64encode(manifest).decode()
    bundle['manifestSha256'] = sp.sha256_bytes(manifest)
    bundle['release']['assets'] = [{'id':400, 'name':'gitpix-3.1.2.manifest.json', 'digest':'sha256:'+bundle['manifestSha256']}, {'id':401, 'name':'gitpix-3.1.2.tar.gz', 'digest':'sha256:'+'b'*64}]


class GitPixTest(unittest.TestCase):
    def setUp(self):
        # Reuse the mature Agent fixtures without modifying their acceptance path.
        self.agent = agent_tests.SitePipelineTest()
        self.agent.setUp()
        self.root = self.agent.base / 'gitpix'
        self.agent._init_repo(self.root)
        record = {'$schema': gp.RECORD_SCHEMA, 'id': 'gitpix', 'kind': 'app', 'outcome': 'Create images and keep their history.',
                  'factSources': {'version': 'VERSION', 'delivery': {'release': '.version-policy.json', 'runtime': 'next.config.ts', 'activation': 'ops/activation.json'},
                                  'documentation': ['README.md', *gp.PUBLIC_DOCS], 'requirements': ['.toolboxmd/delivery.json'],
                                  'proof': [gp.CANDIDATE, gp.ACTIVATE, 'docs/ops.md']}}
        self.mapping = json.loads((ROOT/'.toolboxmd/projects.json').read_text())['projects'][1]
        for path in ['VERSION', 'API_VERSION', '.version-policy.json', 'next.config.ts', 'README.md', gp.CANDIDATE, gp.ACTIVATE, 'docs/ops.md']:
            dest = self.root/path; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_text('fixture\n')
        (self.root/'VERSION').write_text('3.1.2\n'); (self.root/'src/lib').mkdir(parents=True); (self.root/'src/lib/api-version.ts').write_text('export const API_VERSION = "2.3.0";\n')
        write_json(self.root/'.toolboxmd/project.json', record)
        write_json(self.root/'.toolboxmd/delivery.json', {'website': self.mapping['website']})
        write_json(self.root/'ops/activation.json', {'repositoryId': gp.REPO_ID, 'publicUrl': gp.PUBLIC_APP, 'host': 'bigbrain', 'service': 'gitpix.service'})
        write_json(self.root/'public/openapi.json', {'info': {'version': '2.3.0', 'x-project-version': '3.1.2', 'x-project-website': gp.CANONICAL}})
        (self.root/'public/llms.txt').write_text(f'Project version: 3.1.2 (VERSION). API version: 2.3.0 (API_VERSION).\nCanonical Project website: {gp.CANONICAL}\n')
        self.sha = self.agent._commit_and_tag(self.root, 'v3.1.2')
        self.bundle = fixture_bundle(self.sha)
        self.bundle_path = self.agent.base/'inputs.json'; write_json(self.bundle_path, self.bundle)
        registry = sp.read_json(self.agent.toolbox/'.toolboxmd/projects.json'); registry['projects'].append(self.mapping)
        write_json(self.agent.toolbox/'.toolboxmd/projects.json', registry)
        for path in ['templates/gitpix.html', 'schemas/app-project-record-v1.schema.json', 'schemas/app-website-parity-receipt-v1.schema.json']:
            (self.agent.toolbox/path).write_bytes((ROOT/path).read_bytes())

    def tearDown(self): self.agent.tearDown()

    def build(self):
        return sp.build_site(self.agent.toolbox, self.agent.agentsmd, self.agent.marketplace, self.agent.output, self.root, self.bundle_path)

    def test_complete_candidate_deterministic_and_exact_docs(self):
        receipt = self.build(); first = sp.site_files(self.agent.output)
        self.assertEqual(len(sp.read_json(self.agent.output/'site-manifest.json')['projects']), 2)
        self.assertEqual(self.build(), receipt); self.assertEqual(first, sp.site_files(self.agent.output))
        for path in gp.PUBLIC_DOCS:
            self.assertEqual((self.root/path).read_bytes(), (self.agent.output/'gitpix'/Path(path).name).read_bytes())
        self.assertFalse((self.agent.output/'gitpix/README.md').exists())
        self.assertEqual(receipt['apps'][0]['states']['applicationLiveVerification']['state'], 'pending')
        self.assertEqual(receipt['apps'][0]['states']['health']['state'], 'healthy')

    def test_missing_input_and_invalid_release_preserve_candidate(self):
        self.build(); before = sp.site_files(self.agent.output)
        self.bundle['receipt']['production']['status'] = 'failed'; write_json(self.bundle_path, self.bundle)
        with self.assertRaises(sp.PipelineError): self.build()
        self.assertEqual(before, sp.site_files(self.agent.output))
        with self.assertRaises(sp.PipelineError):
            sp.build_site(self.agent.toolbox, self.agent.agentsmd, self.agent.marketplace, self.agent.output)
        self.assertEqual(before, sp.site_files(self.agent.output))

    def test_provenance_rejections(self):
        mutations = [lambda b: b['activationRun'].update(event='pull_request'),
                     lambda b: b['activationRun']['repository'].update(id=1),
                     lambda b: b['activationRun'].update(path='evil.yml'),
                     lambda b: b['triggerRun'].update(run_attempt=2),
                     lambda b: b['triggerRun'].update(head_sha='f'*40),
                     lambda b: b['release'].update(draft=True),
                     lambda b: b['manifest']['artifact'].update(sha256='f'*64),
                     lambda b: b['receiptArtifact'].update(expired=True),
                     lambda b: b['receipt']['target'].update(host='other'),
                     lambda b: b['manifest']['provenance'].update(runId=999)]
        for mutate in mutations:
            b = copy.deepcopy(self.bundle); mutate(b); seal_bundle(b)
            with self.subTest(mutate=mutate), self.assertRaises(sp.PipelineError): gp.verify_bundle(b)

    def test_receipt_zip_digest_entry_and_provenance(self):
        run = self.bundle['activationRun']
        a = {**self.bundle['receiptArtifact'], 'workflow_run': {'id':20,'repository_id':gp.REPO_ID,'head_repository_id':gp.REPO_ID,'head_branch':'main','head_sha':self.sha}}
        def archive(name='gitpix-activation-receipt-20-1.json', mode=stat.S_IFREG|0o644):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:
                info=zipfile.ZipInfo(name); info.external_attr=mode<<16;z.writestr(info,json.dumps(self.bundle['receipt']))
            raw=out.getvalue(); a['digest']='sha256:'+sp.sha256_bytes(raw);return raw
        raw=archive();self.assertEqual(gp.receipt_zip(raw,a,run),self.bundle['receipt'])
        a['digest']='sha256:'+'0'*64
        with self.assertRaises(sp.PipelineError): gp.receipt_zip(raw,a,run)
        for name,mode in [('../bad.json',stat.S_IFREG),('gitpix-activation-receipt-20-1.json',stat.S_IFLNK)]:
            raw=archive(name,mode)
            with self.assertRaises(sp.PipelineError): gp.receipt_zip(raw,a,run)
        raw=archive();a['workflow_run']['head_sha']='f'*40
        with self.assertRaises(sp.PipelineError): gp.receipt_zip(raw,a,run)

    def test_stale_docs_pointer_and_schema_rejected(self):
        for file, content in [('public/openapi.json',json.dumps({'info':{'version':'2.2.0'}})),
                              ('public/llms.txt','Project version: 3.0.0'),
                              ('.toolboxmd/project.json',json.dumps({'$schema':'https://evil.invalid'}))]:
            original=(self.root/file).read_bytes();(self.root/file).write_text(content)
            # Bypass only Git dirty check to exercise the semantic seam independently.
            with patch.object(gp,'verify_release_checkout'), self.assertRaises(sp.PipelineError):
                gp.resolve(self.mapping,self.root,self.bundle)
            (self.root/file).write_bytes(original)
        record=sp.read_json(self.root/'.toolboxmd/project.json');record['factSources']['proof']=['../secret']
        write_json(self.root/'.toolboxmd/project.json',record)
        with patch.object(gp,'verify_release_checkout'), self.assertRaises(sp.PipelineError): gp.resolve(self.mapping,self.root,self.bundle)

    def test_evidence_never_passes_a_different_release(self):
        resolved=gp.resolve(self.mapping,self.root,self.bundle)
        evidence={'schema':1,'project':'gitpix','identity':resolved['identity'].copy(),'states':{'applicationLiveVerification':{'state':'passed','reference':'exact proof'}}}
        self.assertEqual(gp.app_receipt(resolved,evidence)['states']['applicationLiveVerification']['state'],'passed')
        evidence['states']['documentation']={'state':'blocked','reason':'known source guide mismatch'}
        self.assertEqual(gp.app_receipt(resolved,evidence)['states']['documentation']['state'],'blocked')
        evidence['identity']['payloadSha256']='f'*64
        receipt=gp.app_receipt(resolved,evidence)
        self.assertEqual(receipt['states']['applicationLiveVerification']['state'],'pending')
        self.assertEqual(receipt['states']['health']['state'],'healthy')
        self.assertEqual(receipt['states']['documentation']['state'],'released')

    def test_first_pilot_and_later_publication_gate(self):
        receipt = self.build()
        self.assertFalse(gp.publication_gate(receipt, False))
        self.assertTrue(gp.publication_gate(receipt, True))
        app = receipt['apps'][0]
        app['operationalEvidence']['state'] = 'matched'
        app['states']['applicationLiveVerification']['state'] = 'passed'
        self.assertTrue(gp.publication_gate(receipt, False))
        app['states']['documentation']['state'] = 'blocked'
        self.assertFalse(gp.publication_gate(receipt, True))
        app['states']['documentation']['state'] = 'released'
        app['operationalEvidence']['state'] = 'pending'
        self.assertFalse(gp.publication_gate(receipt, False))

    def test_app_schema_and_runtime_agree(self):
        receipt = self.build()['apps'][0]
        schema = sp.read_json(ROOT/'schemas/app-website-parity-receipt-v1.schema.json')
        gp.validate_app_schema(receipt, schema)
        malformed = copy.deepcopy(receipt); malformed['identity']['version'] = 'invalid'
        with self.assertRaises(sp.PipelineError): gp.validate_app_schema(malformed, schema)
        malformed = copy.deepcopy(receipt); del malformed['states']['health']
        with self.assertRaises(sp.PipelineError): gp.validate_app_schema(malformed, schema)
        record = sp.read_json(self.root/'.toolboxmd/project.json')
        schema = sp.read_json(ROOT/'schemas/app-project-record-v1.schema.json')
        gp.validate_app_schema(record, schema)
        record['factSources']['version'] = '../secret'
        with self.assertRaises(sp.PipelineError): gp.validate_app_schema(record, schema)

    def test_preserved_raw_evidence_cannot_disagree(self):
        for key in ['receipt', 'manifest']:
            bundle = copy.deepcopy(self.bundle); bundle[key]['version'] = '3.1.3'
            with self.assertRaises(sp.PipelineError): gp.verify_bundle(bundle)

    def test_fetch_inputs_selects_exact_attempt_and_revalidates_download(self):
        bundle = self.bundle
        artifact = bundle['receiptArtifact']
        manifest_raw = base64.b64decode(bundle['manifestRaw'])
        release = copy.deepcopy(bundle['release'])
        responses = {
            f'repos/{gp.REPO}/actions/artifacts/200': bundle['payloadArtifact'],
            f'repos/{gp.REPO}/actions/workflows/activate-release.yml/runs?branch=main&event=workflow_run&per_page=100': {'workflow_runs': [bundle['activationRun']]},
            f'repos/{gp.REPO}/actions/runs/20': bundle['activationRun'],
            f'repos/{gp.REPO}/actions/runs/20/attempts/1': bundle['activationRun'],
            f'repos/{gp.REPO}/actions/runs/20/artifacts?per_page=100': {'total_count': 2, 'artifacts': [artifact, {**artifact, 'name': 'gitpix-activation-receipt-20-2'}]},
            f'repos/{gp.REPO}/actions/artifacts/300/zip': base64.b64decode(bundle['receiptArchive']),
            f'repos/{gp.REPO}/actions/runs/10/attempts/1': bundle['triggerRun'],
            f'repos/{gp.REPO}/releases/tags/v3.1.2': release,
        }
        class Result:
            returncode = 0
            stdout = manifest_raw
        with patch.dict(os.environ, {'GH_TOKEN': 'fixture-only'}), patch.object(gp, 'gh', side_effect=lambda endpoint, **kw: responses[endpoint]), patch.object(gp.subprocess, 'run', return_value=Result()):
            fetched = gp.fetch_inputs()
            self.assertEqual(gp.verify_bundle(fetched)['sourceSha'], self.sha)
            self.assertNotIn('fixture-only', json.dumps(fetched))
            release['assets'][0]['digest'] = 'sha256:'+'0'*64
            with self.assertRaises(sp.PipelineError): gp.fetch_inputs()
            release['assets'][0]['digest'] = 'sha256:'+sp.sha256_bytes(manifest_raw)
            responses[f'repos/{gp.REPO}/actions/runs/20/artifacts?per_page=100']['artifacts'].append(artifact)
            with self.assertRaises(sp.PipelineError): gp.fetch_inputs()

    def test_newest_failed_or_running_activation_blocks_older_success(self):
        listing = f'repos/{gp.REPO}/actions/workflows/activate-release.yml/runs?branch=main&event=workflow_run&per_page=100'
        for status, conclusion in [('completed', 'failure'), ('in_progress', None)]:
            newest = {**self.bundle['activationRun'], 'id': 21, 'status': status, 'conclusion': conclusion}
            def response(endpoint, **kwargs):
                if endpoint == listing:
                    return {'workflow_runs': [newest, self.bundle['activationRun']]}
                if endpoint == f'repos/{gp.REPO}/actions/runs/21':
                    return newest
                self.fail('Resolver must reject the latest activation before reading older evidence: '+endpoint)
            with self.subTest(status=status), patch.dict(os.environ, {'GH_TOKEN': 'fixture-only'}), patch.object(gp, 'gh', side_effect=response):
                with self.assertRaisesRegex(sp.PipelineError, 'workflow did not succeed'):
                    gp.fetch_inputs()

    def test_latest_run_refresh_rejects_a_newer_failed_attempt(self):
        listed = {**self.bundle['activationRun'], 'id': 21}
        current = {**listed, 'run_attempt': 2, 'conclusion': 'failure'}
        responses = [ {'workflow_runs': [listed, self.bundle['activationRun']]}, current ]
        with patch.dict(os.environ, {'GH_TOKEN': 'fixture-only'}), patch.object(gp, 'gh', side_effect=responses) as network:
            with self.assertRaisesRegex(sp.PipelineError, 'workflow did not succeed'):
                gp.fetch_inputs()
            self.assertEqual(network.call_args_list[-1].args[0], f'repos/{gp.REPO}/actions/runs/21')

    def test_ambiguous_latest_run_listing_is_rejected(self):
        with patch.dict(os.environ, {'GH_TOKEN': 'fixture-only'}), patch.object(gp, 'gh', return_value={'workflow_runs': [self.bundle['activationRun'], self.bundle['activationRun']]}) as network:
            with self.assertRaisesRegex(sp.PipelineError, 'ambiguous activation run listing'):
                gp.fetch_inputs()
            self.assertEqual(network.call_count, 1)

    def test_workflow_revision_and_payload_revision_are_independently_bound(self):
        bundle = copy.deepcopy(self.bundle)
        workflow_sha = 'f' * 40
        bundle['activationRun']['head_sha'] = workflow_sha
        bundle['receiptArtifact']['workflow_run']['head_sha'] = workflow_sha
        resolved = gp.resolve(self.mapping, self.root, bundle)
        self.assertEqual(resolved['identity']['sourceSha'], self.sha)
        self.assertNotEqual(workflow_sha, self.sha)
        wrong_receipt = copy.deepcopy(bundle)
        wrong_receipt['receiptArtifact']['workflow_run']['head_sha'] = self.sha
        with self.assertRaisesRegex(sp.PipelineError, 'receipt artifact provenance mismatch'):
            gp.verify_bundle(wrong_receipt)
        wrong_payload = copy.deepcopy(bundle)
        wrong_payload['payloadArtifact']['workflow_run']['head_sha'] = workflow_sha
        with self.assertRaisesRegex(sp.PipelineError, 'payload artifact workflow provenance mismatch'):
            gp.verify_bundle(wrong_payload)

    def test_no_credential_no_network(self):
        with patch.dict(os.environ,{},clear=True), patch.object(gp,'gh') as network:
            with self.assertRaises(sp.PipelineError): gp.fetch_inputs()
            network.assert_not_called()

    def test_workflow_separates_unprivileged_tests(self):
        workflow=(ROOT/'.github/workflows/website-parity.yml').read_text()
        tests,production=workflow.split('  website-parity:',1)
        self.assertNotIn('secrets.',tests)
        self.assertIn("github.event_name != 'pull_request' && github.ref == 'refs/heads/main'",production)
        self.assertIn('environment: production',production)
        self.assertIn('GITPIX_RELEASE_READ_TOKEN',production)
        self.assertIn('repos/toolboxmd/toolbox.md/issues/16',production)
        self.assertNotIn('repos/toolboxmd/gitpix/issues',production)
        self.assertIn('--gitpix-inputs .inputs/gitpix-release.json',production)

if __name__=='__main__':unittest.main()
