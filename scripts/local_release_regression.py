"""Replay a retained synthetic API release scenario; never seeds approvals.

Only accepts the owned loopback API. Requires a private credential manifest and
prior API-created state. State records must not contain real users or materials.
This verifies retained lineage, denial and idempotency, not a fresh authoring run.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4
import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--api', default='http://127.0.0.1:1343')
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--synthetic-only', action='store_true', required=True)
    args = parser.parse_args()
    url = urlsplit(args.api)
    if (url.scheme != 'http' or url.hostname not in ('localhost', '127.0.0.1', '::1')
            or url.username or url.password or url.query or url.fragment or url.path not in ('', '/')):
        parser.error('A bare loopback HTTP origin is required')
    fixture = json.loads(args.credentials.read_text())
    state = json.loads(args.state.read_text())
    clients = {}
    try:
        for account in fixture['accounts']:
            if account['label'] not in ('teacher', 'reviewer', 'learner'):
                continue
            if not account['email'].endswith('@example.com'):
                parser.error('Only synthetic example.com accounts are accepted')
            client = httpx.Client(base_url=args.api, trust_env=False, follow_redirects=False, timeout=20)
            clients[account['label']] = client
            assert client.post('/api/v1/auth/login', data={
                'username': account['email'], 'password': account['password'], 'org_slug': 'default',
            }).status_code == 200, 'Synthetic login failed'
        prefix = '/api/v1/studytomy/orgs/' + state['org'] + '/courses/' + state['course']
        lesson = prefix + '/lessons/' + state['activity']
        source = clients['teacher'].get(lesson + '/grounded-lesson-sources')
        print('Retained cited-source read:', source.status_code)
        assert source.status_code == 200 and source.json()['status'] == 'current'
        denied = clients['learner'].get(lesson + '/draft-sources')
        print('Unauthorized source read:', denied.status_code)
        assert denied.status_code in (403, 404)
        # A mismatched course/lesson URL must not resolve another course's sources.
        wrong = lesson.replace('/courses/' + state['course'], '/courses/course_' + str(uuid4()))
        denied = clients['teacher'].get(wrong + '/draft-sources')
        print('Wrong-course source route:', denied.status_code)
        assert denied.status_code in (403, 404)
        route = prefix + '/content-review-decisions/' + state['review']['uuid'] + '/release'
        denied = clients['learner'].post(route)
        print('Unauthorized release:', denied.status_code)
        assert denied.status_code in (403, 404)
        receipts = [clients['reviewer'].post(route) for _ in range(2)]
        print('Release replay statuses:', [r.status_code for r in receipts])
        assert all(r.status_code == 201 for r in receipts), 'Release replay blocked; inspect policy/review currency without bypass'
        assert all(r.json() == state['release'] for r in receipts), 'Immutable receipt changed'
        print('PASS: same immutable receipt on both replays; no new release/approval seeded')
    finally:
        for client in clients.values():
            client.close()

if __name__ == '__main__':
    main()
