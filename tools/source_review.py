"""Accepted-main controller for human-approved source-only main changes."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

from tools import update as u
from tools.recipe_gate import tree_manifest


ADMISSION_PREFIX = 'arch-packages/source-bootstrap/v1\n'


def operator_api(path):
    """Read GitHub metadata through the operator's existing gh login only."""
    if os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN'):
        raise ValueError('operator admission command requires GH_TOKEN and GITHUB_TOKEN unset')
    with subprocess.Popen(['gh','api','--hostname','github.com',path.lstrip('/')],stdout=subprocess.PIPE) as process:
        data=process.stdout.read(u.MAX_TREE+1)
        if len(data)>u.MAX_TREE:
            process.kill()
            raise ValueError('operator API JSON exceeds bound')
        if process.wait()!=0:
            raise ValueError('authenticated operator gh api read failed')
    return json.loads(data)


def bootstrap_claim(number, base, head, api=None):
    """Describe exact workflow and zero-package scope using GitHub Git objects only."""
    if api is None:
        api=u.api
        pr,actual_base,actual_head=u.pr_identity(number,base,head)
    else:
        if not str(number).isdecimal() or int(number)<1 or not u.SHA.fullmatch(base) or not u.SHA.fullmatch(head):
            raise ValueError('invalid bootstrap immutable PR identity')
        pr=api(u.route('/pulls/'+str(number)))
        actual_base=api(u.route('/git/ref/heads/main'))['object']['sha']
        actual_head=pr['head']['sha']
        if (pr['state']!='open' or pr['base']['ref']!='main'
                or pr['base']['repo']['full_name']!=u.repository()
                or pr['base']['sha']!=actual_base or actual_base!=base or actual_head!=head):
            raise ValueError('bootstrap candidate base/head changed; redispatch required')
    if not pr.get('draft') or pr['head']['repo']['full_name'] != u.repository():
        raise ValueError('bootstrap requires an exact same-repository draft main PR')
    scopes = []
    workflow = None
    for sha in (actual_base, actual_head):
        tree = api(u.route('/git/trees/' + sha + '?recursive=1'))
        if tree.get('truncated') is not False:
            raise ValueError('bootstrap scope tree is truncated or incomplete')
        scope = []
        for entry in tree['tree']:
            path = entry['path']
            if path in {'packages.json', '.gitmodules', 'recipes', 'inputs', 'upstream', 'acceptance'} or path.startswith(('recipes/', 'inputs/', 'upstream/', 'acceptance/')):
                scope.append({key:entry[key] for key in ('path','mode','type','sha')})
            if sha == actual_head and path == '.github/workflows/verification.yml':
                if entry['mode'] != '100644' or entry['type'] != 'blob':
                    raise ValueError('bootstrap workflow must be a regular Git blob')
                workflow = entry['sha']
        scopes.append(sorted(scope, key=lambda entry:entry['path']))
    if scopes[0] != scopes[1]:
        raise ValueError('bootstrap cannot change package scope')
    if not workflow or not any(entry['path'] == 'packages.json' for entry in scopes[0]):
        raise ValueError('bootstrap workflow or enrolled package scope missing')
    digest = hashlib.sha256(json.dumps(scopes[0],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {'decision':'approve-bootstrap','repository':u.repository(),'base':actual_base,'head':actual_head,'pr_number':int(number),
            'workflow_blob_sha':workflow,'zero_package_scope_digest':digest}


def admission_body(number, base, head, api=None):
    return ADMISSION_PREFIX + json.dumps(bootstrap_claim(number,base,head,api),sort_keys=True,separators=(',',':'))


def authenticated_admission(number, base, head, admission, environment, run):
    if not str(admission).isdecimal() or int(admission) < 1:
        raise ValueError('bootstrap requires authenticated operator admission review ID')
    body = admission_body(number,base,head)
    review = u.api(u.route(f'/pulls/{number}/reviews/{int(admission)}'))
    reviewers = {item['reviewer']['id'] for rule in environment.get('protection_rules',[])
                 if rule.get('type') == 'required_reviewers' for item in rule.get('reviewers',[])
                 if item.get('type') == 'User' and item.get('reviewer',{}).get('type') == 'User'}
    user = review.get('user',{})
    if (review.get('state') not in {'APPROVED','COMMENTED'} or review.get('commit_id') != head
            or user.get('type') != 'User' or user.get('id') not in reviewers
            or review.get('body') != body):
        raise ValueError('bootstrap authenticated operator admission does not match exact workflow and scope')
    try:
        submitted = datetime.fromisoformat(review['submitted_at'].replace('Z','+00:00'))
        started = datetime.fromisoformat(run['run_started_at'].replace('Z','+00:00'))
        prior = submitted < started
    except (KeyError,TypeError,ValueError):
        prior = False
    if not prior:
        raise ValueError('bootstrap operator admission must precede this workflow attempt')


def authority(number, base, head, bootstrap=False, admission=None):
    if not bootstrap and admission is not None:
        raise ValueError('operator admission is only valid for explicit bootstrap')
    if bootstrap and (not str(admission).isdecimal() or int(admission) < 1):
        raise ValueError('bootstrap requires authenticated operator admission review ID')
    pr, actual_base, actual_head = u.pr_identity(number, base, head)
    if not pr.get('draft'):
        raise ValueError('source-only review requires a draft PR; legacy dispatcher cancellation is a separate prerequisite')
    run_id=os.environ['GITHUB_RUN_ID']; attempt=os.environ['GITHUB_RUN_ATTEMPT']
    run=u.api(u.route('/actions/runs/'+run_id+'/attempts/'+attempt))
    title=f'Source review PR {number} head {head} base {base}'
    workflow_sha = head if bootstrap else base
    workflow_branch = 'source-review-'+head if bootstrap else 'main'
    workflow_ref = 'refs/tags/'+workflow_branch if bootstrap else 'refs/heads/main'
    if (run.get('head_sha')!=workflow_sha or run.get('event')!='workflow_dispatch'
            or run.get('path')!='.github/workflows/verification.yml'
            or run.get('display_title')!=title or run.get('run_attempt')!=int(attempt)
            or run.get('head_branch')!=workflow_branch or pr['head']['repo']['full_name']!=u.repository()
            or os.environ.get('GITHUB_SHA')!=workflow_sha or os.environ.get('GITHUB_REF')!=workflow_ref):
        raise ValueError('source-review immutable workflow identity mismatch')
    u.require_review_environment('code-review')
    environment=u.api(u.route('/environments/code-review'))
    if bootstrap:
        tag=u.api(u.route('/git/ref/tags/'+workflow_branch))
        if tag.get('ref')!=workflow_ref or tag.get('object',{}).get('type')!='commit' or tag['object'].get('sha')!=head:
            raise ValueError('bootstrap immutable tag identity mismatch')
        authenticated_admission(number,base,head,admission,environment,run)
    jobs=u.api(u.route('/actions/runs/'+run_id+'/attempts/'+attempt+'/jobs?per_page=100'))['jobs']
    matches=[job for job in jobs if job.get('name')==f'Approve source PR {number} head {head} base {base}']
    if len(matches)!=1 or matches[0].get('conclusion')!='success':
        raise ValueError('source-review exact approval job did not succeed')
    history=u.api(u.route('/actions/runs/'+run_id+'/approvals'))
    if not any(review.get('state')=='approved' and review.get('user',{}).get('type')=='User'
               and any(item.get('id')==environment['id'] and item.get('name')=='code-review'
                       for item in review.get('environments',[])) for review in history):
        raise ValueError('source-review genuine human environment approval missing')
    return {'run_id':run_id,'run_attempt':attempt,'job_id':matches[0]['id'],'base':actual_base,'head':actual_head}


def unchanged_packages(old,new,oldpins,newpins):
    if oldpins!=newpins or u.policy_at(old)!=u.policy_at(new):
        raise ValueError('source-only route cannot change package pins or enrollment')
    for name in oldpins:
        if tree_manifest(old/'recipes'/name)!=tree_manifest(new/'recipes'/name):
            raise ValueError('source-only route cannot change recipe content')
    for directory in ('inputs','upstream','acceptance'):
        a=old/directory;b=new/directory
        if a.exists()!=b.exists() or a.exists() and tree_manifest(a)!=tree_manifest(b):
            raise ValueError('source-only route cannot change package source or acceptance data')
    for filename in ('.gitmodules',):
        a=old/filename;b=new/filename
        if a.exists()!=b.exists() or a.exists() and a.read_bytes()!=b.read_bytes():
            raise ValueError('source-only route cannot change recipe enrollment paths')


def validate(number,base,head,directory,bootstrap=False,admission=None):
    proof=authority(number,base,head,bootstrap,admission)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    oldpins={};newpins={}
    old=u.checkout_data(base,directory/'base',oldpins)
    new=u.checkout_data(head,directory/'head',newpins)
    try:
        unchanged_packages(old,new,oldpins,newpins)
        image=(old/'build-image.txt').read_text().strip()
        if not u.re.fullmatch(r'ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}',image):
            raise ValueError('source-review baseline image is not pinned official Arch')
        authority(number,base,head,bootstrap,admission)
        u.run_candidate_tests(new,image)
        authority(number,base,head,bootstrap,admission)
        evidence={'schema':1,'repository':u.repository(),'base':base,'head':head,
                  'authorization':proof,'packages':[],'package_content_unchanged':True}
        u.dump(directory/'source-review-evidence.json',evidence)
        u.status(head,'verify','success','Human-approved exact source tests passed')
        u.status(head,'candidate-build','success','Exact unchanged package inputs verified; zero package workers')
        u.status(head,'recipe-policy','success','Human-approved source-only change; human merge required')
        return evidence
    finally:
        u.remove_verification_tree(old);u.remove_verification_tree(new)


def cli():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pr',required=True,type=int);parser.add_argument('--base',required=True)
    parser.add_argument('--head',required=True);parser.add_argument('--directory')
    parser.add_argument('--bootstrap',action='store_true')
    parser.add_argument('--admission',type=int)
    parser.add_argument('--admission-body',action='store_true')
    args=parser.parse_args()
    if args.admission_body:
        print(admission_body(args.pr,args.base,args.head,operator_api),end='')
    else:
        if not args.directory:
            parser.error('--directory is required for validation')
        validate(args.pr,args.base,args.head,args.directory,args.bootstrap,args.admission)


if __name__=='__main__':cli()
