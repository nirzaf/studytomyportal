"""Create an explicitly synthetic authoring scenario through local public APIs.

Requires fresh local_demo_seed accounts and the matching isolated API. No learner
activation, provider use or real-user publication. Approval data are test fixtures.
"""
import argparse, json, os, sys
from pathlib import Path
from datetime import datetime, UTC, timedelta
from uuid import uuid4
from urllib.parse import urlsplit
import httpx
from local_demo_seed import validate_target
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/api'))
from src.db.studytomy.governance import StudyPolicyKey, POLICY_REQUIRED_DECISION_AREAS
from src.services.studytomy.policies import POLICY_REQUIRED_RESPONSIBILITIES
from src.tests.services.rag_helpers import make_pdf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', required=True)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--synthetic-only', action='store_true', required=True)
    args = parser.parse_args()
    target = validate_target(os.environ['LEARNHOUSE_SQL_CONNECTION_STRING'])
    url = urlsplit(args.api)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1','localhost','::1') or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
        parser.error('Only a bare loopback HTTP origin is supported')
    if args.output.exists() or args.output.resolve().is_relative_to(ROOT):
        parser.error('Output must be a new file outside checkout')
    fixture = json.loads(args.credentials.read_text())
    if fixture.get('scenario') != 'native-accounts-v1' or fixture.get('database') != target.database:
        parser.error('Credential manifest must match the explicit disposable seed database')
    clients = {}; sessions = {}
    def call(label, method, path, expected=200, **kw):
        response = getattr(clients[label], method)(path, **kw)
        if response.status_code != expected:
            detail = response.json().get('detail', {})
            code = detail.get('code') if isinstance(detail, dict) else 'request_failed'
            raise RuntimeError(f'{method} {path}: {response.status_code}, {code}')
        return response.json() if response.content else None
    def post(label, path, **kw):
        return call(label,'post',path,headers={'Idempotency-Key':str(uuid4())},**kw)
    try:
        for account in fixture['accounts']:
            if account['email'] != f"demo-{account['label']}@example.com":raise ValueError('Only synthetic seed accounts accepted')
            clients[account['label']] = httpx.Client(base_url=args.api,trust_env=False,follow_redirects=False,timeout=30)
            call(account['label'],'post','/api/v1/auth/login',data={'username':account['email'],'password':account['password'],'org_slug':'default'})
            sessions[account['label']]=call(account['label'],'get','/api/v1/users/session')
        # The seeded manifest supplies the exact organization identifier.
        org_uuid=next(r['org']['org_uuid'] for r in sessions['admin']['roles'] if r['org']['id']==fixture['org_id'])
        org = next(r['org'] for r in sessions['admin']['roles'] if r['org']['id']==fixture['org_id'])
        if org.get('name') != 'Synthetic local demo':raise ValueError('Synthetic seed organization required')
        p='/api/v1/studytomy/orgs/'+org_uuid
        if call('admin','get',p+'/policies') or call('admin','get',p+'/service-owners'):
            raise ValueError('Fresh synthetic organization required; will not overwrite approvals')
        course=call('teacher','post','/api/v1/courses/',params={'org_id':fixture['org_id']},data={'name':'Synthetic cited fractions','description':'Local original PDF only','public':'false','about':'Synthetic regression fixture'})
        other=call('teacher','post','/api/v1/courses/',params={'org_id':fixture['org_id']},data={'name':'Synthetic unrelated course','description':'Scope denial fixture','public':'false','about':'Synthetic only'})
        rights=sessions['reviewer']['roles'][0]['role']['rights'];rights['studytomy']['content_release_review']=True
        role=post('admin',f"/api/v1/roles/org/{fixture['org_id']}",json={'name':'Synthetic scoped release reviewer','rights':rights})
        call('admin','put',f"/api/v1/orgs/{fixture['org_id']}/users/{sessions['reviewer']['user']['id']}/role/{role['role_uuid']}")
        now=datetime.now(UTC); times={'effective_from':now.isoformat(),'effective_to':(now+timedelta(hours=1)).isoformat()}
        keys=[StudyPolicyKey(v) for v in ('D-02','D-08','D-09','D-10','D-12','D-13','D-15','D-16')]
        owners={}
        for responsibility in sorted({r.value for k in keys for r in POLICY_REQUIRED_RESPONSIBILITIES[k]}):
            owners[responsibility]=post('admin',p+'/service-owners',expected=201,json={'user_uuid':sessions['reviewer']['user']['user_uuid'],'responsibility':responsibility,'evidence_reference':'SYNTHETIC/local-authoring-only',**times})
        post('admin',p+'/service-owners/'+owners['education']['owner_uuid']+'/scopes',expected=201,json={'capability':'content_release_review','scope_kind':'course','scope_uuid':course['course_uuid'],'evidence_reference':'SYNTHETIC/exact-course-only'})
        def policy(key, specified):
            k=StudyPolicyKey(key);items={area:{'state':'not_applicable','rationale':'Synthetic text-only test; no actual service decision.','evidence_reference':'SYNTHETIC/'+key} for area in POLICY_REQUIRED_DECISION_AREAS[k]}
            for area,value in specified.items():items[area]={'state':'specified','value':value,'rationale':'Synthetic local test contract only.','evidence_reference':'SYNTHETIC/'+key}
            version=post('admin',p+'/policies',expected=201,json={'policy_key':key,'body':{'decision_items':items},'evidence_reference':'SYNTHETIC/local-authoring-only',**times})
            for responsibility in POLICY_REQUIRED_RESPONSIBILITIES[k]:
                post('reviewer',p+'/policies/'+version['policy_uuid']+'/approvals',json={'responsibility':responsibility.value,'evidence_reference':'SYNTHETIC/local-authoring-only','rationale':'Test fixture approval by separate synthetic reviewer. Not a production decision.'})
        for k in keys:
            if k.value=='D-09':continue
            values={}
            if k.value=='D-13':values={'retention':{'content_evidence_retention_category':'synthetic_content','course_release_retention_category':'synthetic_release'}}
            if k.value=='D-15':values={'supported_formats':{'max_bytes':4096,'media_types':['application/pdf']}}
            policy(k.value,values)
        cp=p+'/courses/'+course['course_uuid'];call('reviewer','put',cp+'/managed-draft',expected=204)
        chapter=post('teacher','/api/v1/chapters/',json={'name':'Synthetic fractions','org_id':fixture['org_id'],'course_id':course['id']})
        activity=post('teacher','/api/v1/activities/',json={'name':'Synthetic PDF lesson','chapter_id':chapter['id'],'activity_type':'TYPE_DYNAMIC','activity_sub_type':'SUBTYPE_DYNAMIC_PAGE','content':{'type':'doc','content':[{'type':'paragraph'}]}})
        lesson=cp+'/lessons/'+activity['activity_uuid'];sentence='Two quarters equal one half.'
        source=post('teacher',lesson+'/draft-sources',expected=201,files={'file':('synthetic.pdf',make_pdf([sentence]),'application/pdf')})
        post('teacher',lesson+'/grounded-lesson-draft',json={'expected_current_version':activity['current_version'],'title':'Synthetic cited fractions','request_id':str(uuid4()),'blocks':[{'text':sentence,'citation':{'source_uuid':source['source_uuid'],'sha256':source['sha256'],'page':1,'quote':sentence}}]})
        assert call('teacher','get',lesson+'/grounded-lesson-sources')['status']=='current'
        assert clients['learner'].get(lesson+'/draft-sources').status_code==403
        wrong=lesson.replace(course['course_uuid'],other['course_uuid'])
        assert clients['teacher'].get(wrong+'/draft-sources').status_code in (403,404)
        assert clients['reviewer'].get(p+'/courses/'+other['course_uuid']+'/content-review-candidate').status_code==403
        print('PASS PDF, cited draft, learner denial, populated wrong-course and reviewer-scope denial')
        policy('D-09',{'release_schedule':{'ungated_text_activities':{course['course_uuid']:[activity['activity_uuid']]}}})
        candidate=call('reviewer','get',cp+'/content-review-candidate');evidence={}
        for purpose,text in [('content_rights','Original synthetic fraction PDF; local test only.'),('content_accessibility','Synthetic searchable text checked in local test; not formal certification.')]:
            evidence[purpose]=post('reviewer',cp+'/evidence-files',expected=201,data={'purpose':purpose,'revision_sha256':candidate['revision_sha256']},files={'file':('evidence.pdf',make_pdf([text]),'application/pdf')})['evidence_uuid']
        review=post('reviewer',cp+'/content-review-decisions',expected=201,json={'revision_sha256':candidate['revision_sha256'],'rights_evidence_uuid':evidence['content_rights'],'accessibility_evidence_uuid':evidence['content_accessibility'],'decision':'approved','accessibility_confirmed':True,'assessment_confirmed':True})
        route=cp+'/content-review-decisions/'+review['uuid']
        call('reviewer','post',route+'/release-preflight')
        release=call('reviewer','post',route+'/release',expected=201)
        assert call('reviewer','post',route+'/release',expected=201)==release
        assert clients['learner'].post(route+'/release').status_code==403
        state={'org':org_uuid,'course':course['course_uuid'],'activity':activity['activity_uuid'],'source':source,'review':review,'release':release}
        with args.output.open('x') as out:json.dump(state,out)
        print('PASS distinct review, immutable release, exact replay, unauthorized release denied')
    finally:
        for client in clients.values():client.close()
if __name__=='__main__':main()
