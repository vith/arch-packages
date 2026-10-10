"""Exact source-only validation and ordinary protected automatic merge."""
import argparse
import os
from pathlib import Path

from tools import update as u
from tools.recipe_gate import tree_manifest


def unchanged_git_scope(base, head):
    """Reject package changes before checking out or executing candidate code."""
    scopes = []
    workflow = None
    for sha in (base, head):
        tree = u.api(u.route('/git/trees/' + sha + '?recursive=1'))
        if tree.get('truncated') is not False:
            raise ValueError('source-only scope tree is truncated or incomplete')
        scope = []
        for entry in tree['tree']:
            path = entry['path']
            if path in {'packages.json', '.gitmodules', 'recipes', 'inputs', 'upstream', 'acceptance'} or path.startswith(('recipes/', 'inputs/', 'upstream/', 'acceptance/')):
                scope.append({key:entry[key] for key in ('path','mode','type','sha')})
            if sha == head and path == '.github/workflows/verification.yml':
                if entry['mode'] != '100644' or entry['type'] != 'blob':
                    raise ValueError('source-only workflow must be a regular Git blob')
                workflow = entry['sha']
        scopes.append(sorted(scope, key=lambda entry:entry['path']))
    if scopes[0] != scopes[1]:
        raise ValueError('source-only route cannot change package scope')
    if not workflow or not any(entry['path'] == 'packages.json' for entry in scopes[0]):
        raise ValueError('source-only workflow or enrolled package scope missing')


def authority(number, base, head, bootstrap=False):
    pr, actual_base, actual_head = u.pr_identity(number, base, head)
    run_id=os.environ['GITHUB_RUN_ID']; attempt=os.environ['GITHUB_RUN_ATTEMPT']
    run=u.api(u.route('/actions/runs/'+run_id+'/attempts/'+attempt))
    title=f'Source review PR {number} head {head} base {base}'
    workflow_sha = head if bootstrap else base
    workflow_branch = 'source-review-'+head if bootstrap else 'main'
    workflow_ref = 'refs/tags/'+workflow_branch if bootstrap else 'refs/heads/main'
    if (run.get('id')!=int(run_id) or run.get('head_sha')!=workflow_sha or run.get('event')!='workflow_dispatch'
            or run.get('path')!='.github/workflows/verification.yml'
            or run.get('display_title')!=title or run.get('run_attempt')!=int(attempt)
            or run.get('head_branch')!=workflow_branch or pr['head']['repo']['full_name']!=u.repository()
            or os.environ.get('GITHUB_SHA')!=workflow_sha or os.environ.get('GITHUB_REF')!=workflow_ref):
        raise ValueError('source-review immutable workflow identity mismatch')
    if bootstrap:
        tag=u.api(u.route('/git/ref/tags/'+workflow_branch))
        if tag.get('ref')!=workflow_ref or tag.get('object',{}).get('type')!='commit' or tag['object'].get('sha')!=head:
            raise ValueError('bootstrap immutable tag identity mismatch')
    unchanged_git_scope(actual_base,actual_head)
    return {'run_id':run_id,'run_attempt':attempt,'base':actual_base,'head':actual_head}


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
    for filename in ('.gitmodules','packages.json'):
        a=old/filename;b=new/filename
        if a.exists()!=b.exists() or a.exists() and a.read_bytes()!=b.read_bytes():
            raise ValueError('source-only route cannot change recipe enrollment paths')


def validate(number,base,head,directory,bootstrap=False):
    proof=authority(number,base,head,bootstrap)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    oldpins={};newpins={}
    old=u.checkout_data(base,directory/'base',oldpins)
    new=u.checkout_data(head,directory/'head',newpins)
    try:
        unchanged_packages(old,new,oldpins,newpins)
        image=(old/'build-image.txt').read_text().strip()
        if not u.re.fullmatch(r'ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}',image):
            raise ValueError('source-review baseline image is not pinned official Arch')
        authority(number,base,head,bootstrap)
        u.run_candidate_tests(new,image)
        authority(number,base,head,bootstrap)
        evidence={'schema':1,'repository':u.repository(),'base':base,'head':head,
                  'authorization':proof,'packages':[],'package_content_unchanged':True}
        u.dump(directory/'source-review-evidence.json',evidence)
        u.status(head,'verify','success','Exact source tests passed')
        u.status(head,'candidate-build','success','Exact unchanged package inputs verified; zero package workers')
        u.status(head,'recipe-policy','success','Exact source-only package scope verified')
        return evidence
    finally:
        u.remove_verification_tree(old);u.remove_verification_tree(new)


def finalize(number,base,head,bootstrap=False):
    proof=authority(number,base,head,bootstrap)
    jobs=u.api(u.route('/actions/runs/'+proof['run_id']+'/attempts/'+proof['run_attempt']+'/jobs?per_page=100'))
    matches=[job for job in jobs['jobs'] if job.get('name')==f'Validate source PR {number} head {head} base {base}']
    if jobs.get('total_count',len(jobs['jobs']))>100 or len(matches)!=1 or matches[0].get('conclusion')!='success':
        raise ValueError('exact source validation job did not succeed')
    states={s['context']:s['state'] for s in reversed(u.api(u.route('/commits/'+head+'/statuses')))}
    if any(states.get(k)!='success' for k in ('verify','candidate-build','recipe-policy')):
        raise ValueError('required exact-head statuses missing')
    authority(number,base,head,bootstrap)
    result=u.api(u.route('/pulls/'+str(number)+'/merge'),'PUT',{'sha':head,'merge_method':'merge'})
    if not result.get('merged') or not u.SHA.fullmatch(result.get('sha','')):
        raise ValueError('expected-head merge failed')
    # Actions-token merges do not trigger push workflows; resume publication only.
    u.api(u.route('/actions/workflows/publish.yml/dispatches'),'POST',{'ref':'main','inputs':{'accepted_sha':result['sha']}})
    return result


def cli():
    parser=argparse.ArgumentParser()
    parser.add_argument('--pr',required=True,type=int);parser.add_argument('--base',required=True)
    parser.add_argument('--head',required=True);parser.add_argument('--directory')
    parser.add_argument('--bootstrap',action='store_true')
    parser.add_argument('--finalize',action='store_true')
    args=parser.parse_args()
    if args.finalize:
        finalize(args.pr,args.base,args.head,args.bootstrap)
    else:
        if not args.directory:
            parser.error('--directory is required for validation')
        validate(args.pr,args.base,args.head,args.directory,args.bootstrap)


if __name__=='__main__':cli()
