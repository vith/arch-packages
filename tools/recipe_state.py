"""Append-only bounded controller receipts, never stored in recipe roots."""
import base64
import hashlib
import json
import re
from tools import sources

BRANCH='controller-state'
NAMESPACES={'proposal','candidate','candidate-provenance','built','approved','acceptance','built-by-input'}
KEY=re.compile(r'^[a-zA-Z0-9_-]{1,200}$')
CONTROLS=('tools/update.py','tools/recipes.py','tools/recipe_candidates.py',
          'tools/recipe_acceptance.py','tools/recipe_state.py','tools/package_runs.py','tools/build_store.py','tools/attestations.py',
          '.github/workflows/update.yml','.github/workflows/candidate.yml',
          '.github/workflows/candidate-dispatch.yml','.github/workflows/build-package.yml',
          'tools/source_review.py','.github/workflows/verification.yml')


def digest(value):
    return hashlib.sha256(sources.canonical(value)).hexdigest()


def control_digest(root):
    from pathlib import Path
    import stat
    root=Path(root)
    manifest=[]
    for name in CONTROLS:
        path=root/name
        if not path.is_file() or path.is_symlink():
            raise ValueError('trusted controller file missing')
        manifest.append({'path':name,'mode':stat.S_IMODE(path.stat().st_mode),
                         'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return digest(manifest)


def record_path(namespace,key):
    if namespace not in NAMESPACES or not isinstance(key,str) or not KEY.fullmatch(key):
        raise ValueError('invalid controller receipt key')
    return namespace+'/'+key+'.json'


def identity_key(record):
    from tools import update as u
    values=[record['base'],record.get('recipe_base',record['base']),record['head']]
    if any(not u.SHA.fullmatch(v) for v in values):
        raise ValueError('invalid receipt identity')
    return '-'.join(values)


def decode(envelope):
    if set(envelope)!={'schema','digest','value'} or envelope['schema']!=1 or envelope['digest']!=digest(envelope['value']):
        raise ValueError('controller receipt digest mismatch')
    return envelope['value']


def load_optional(namespace,key):
    from tools import update as u
    path=record_path(namespace,key)
    tip=u.ref_head(BRANCH)
    if not tip:return None
    commit=u.api(u.route('/git/commits/'+tip))
    tree=u.api(u.route('/git/trees/'+commit['tree']['sha']+'?recursive=1'))
    if tree.get('truncated'):raise ValueError('truncated controller state')
    rows=[r for r in tree['tree'] if r['path']==path]
    if not rows:return None
    if len(rows)!=1 or rows[0]['type']!='blob' or rows[0]['mode']!='100644':
        raise ValueError('invalid controller receipt object')
    blob=u.api(u.route('/git/blobs/'+rows[0]['sha']))
    if blob.get('encoding')!='base64' or blob.get('size',u.MAX_TREE+1)>u.MAX_TREE:
        raise ValueError('controller receipt exceeds bound')
    raw=base64.b64decode(blob['content'])
    if len(raw)>u.MAX_TREE:raise ValueError('controller receipt exceeds bound')
    return decode(json.loads(raw))


def keys(namespace,prefix):
    from tools import update as u
    if prefix:
        record_path(namespace,prefix)
    elif namespace not in NAMESPACES:
        raise ValueError('invalid controller receipt namespace')
    tip=u.ref_head(BRANCH)
    if not tip:return []
    commit=u.api(u.route('/git/commits/'+tip))
    tree=u.api(u.route('/git/trees/'+commit['tree']['sha']+'?recursive=1'))
    if tree.get('truncated'):raise ValueError('truncated controller state')
    result=[]
    for row in tree['tree']:
        path=row['path']
        if not path.startswith(namespace+'/'+prefix) or not path.endswith('.json'):
            continue
        key=path[len(namespace)+1:-5]
        record_path(namespace,key)
        if row['mode']!='100644' or row['type']!='blob':
            raise ValueError('invalid controller receipt object')
        result.append(key)
    return sorted(result)


def load(namespace,key):
    value=load_optional(namespace,key)
    if value is None:raise ValueError('missing durable '+namespace+' receipt')
    return value


def save(namespace,key,value):
    from tools import update as u
    path=record_path(namespace,key)
    existing=load_optional(namespace,key)
    if existing is not None:
        if existing!=value:raise ValueError('immutable controller receipt differs')
        return digest(value)
    raw=sources.canonical({'schema':1,'digest':digest(value),'value':value})
    if len(raw)>u.MAX_TREE:raise ValueError('controller receipt exceeds bound')
    previous=u.ref_head(BRANCH)
    # A competing writer may have retained this identity since the first read.
    # A later branch advance is rejected by the exact push lease below.
    existing=load_optional(namespace,key)
    if existing is not None:
        if existing!=value:raise ValueError('immutable controller receipt differs')
        return digest(value)
    entries=[{'path':path,'mode':'100644','type':'blob','sha':u.api(u.route('/git/blobs'),'POST',{'content':base64.b64encode(raw).decode(),'encoding':'base64'})['sha']}]
    body={'tree':entries}
    if previous:body['base_tree']=u.api(u.route('/git/commits/'+previous))['tree']['sha']
    tree=u.api(u.route('/git/trees'),'POST',body)['sha']
    commit=u.api(u.route('/git/commits'),'POST',{'tree':tree,'parents':[previous] if previous else [],'message':'Retain '+path})['sha']
    sources.git('fetch','https://github.com/'+u.repository()+'.git',commit,cwd=u.ROOT)
    u.push_ref(u.ROOT,commit,BRANCH,previous)
    return digest(value)


def is_attested(namespace,key,value,head):
    from tools import update as u
    record_path(namespace,key)
    if not u.SHA.fullmatch(head):raise ValueError('invalid attestation head')
    context='arch-receipt/'+namespace+'/'+key
    expected=digest(value)
    page=1
    while True:
        rows=u.api(u.route('/commits/'+head+'/statuses?per_page=100&page='+str(page)))
        if not isinstance(rows,list) or len(rows)>100:
            raise ValueError('invalid receipt attestation status page')
        if any(row.get('context')==context and row.get('state')=='success'
               and row.get('description')==expected
               and row.get('creator',{}).get('login')=='github-actions[bot]' for row in rows):
            return True
        if len(rows)<100:return False
        page+=1


def attest(namespace,key,value,head):
    from tools import update as u
    if is_attested(namespace,key,value,head):return
    context='arch-receipt/'+namespace+'/'+key
    posted=u.api(u.route('/statuses/'+head),'POST',{'state':'success',
          'context':context,'description':digest(value)})
    if (posted.get('creator',{}).get('login')!='github-actions[bot]'
            or posted.get('context')!=context or posted.get('state')!='success'
            or posted.get('description')!=digest(value)):
        raise ValueError('receipt attestation requires trusted Actions bot execution')


def require_attestation(namespace,key,value,head):
    if not is_attested(namespace,key,value,head):
        raise ValueError('missing exact trusted receipt attestation')


def protected_branch(name):
    from tools import update as u
    if not u.NAME.fullmatch(name):raise ValueError('invalid package branch')
    branch=u.api(u.route('/branches/pkg%2F'+name))
    if branch.get('protected') is not True:
        raise ValueError('recipe branch protection is not deployed')
    return branch


def recipe_identity(number,base=None,head=None,merged=False):
    from tools import update as u
    if not str(number).isdecimal() or int(number)<1:raise ValueError('invalid PR number')
    pr=u.api(u.route('/pulls/'+str(number)))
    name=pr['base']['ref'].removeprefix('pkg/')
    branch=pr['head']['ref']
    valid_state=(pr['state']=='closed' and pr.get('merged') and pr.get('merged_at')) if merged else pr['state']=='open'
    if not valid_state or pr['base']['ref']!='pkg/'+name or not u.NAME.fullmatch(name) or not isinstance(branch,str) or not branch or any(pr[side]['repo']['full_name']!=u.repository() for side in ('base','head')):
        raise ValueError('PR is not an open native recipe candidate')
    protected_branch(name)
    control=u.main_sha();checkout=u.trusted_checkout_sha()
    if control!=checkout or base and base!=control or head and head!=pr['head']['sha'] or not merged and u.ref_head('pkg/'+name)!=pr['base']['sha']:
        raise ValueError('candidate control/base/head changed; redispatch required')
    if any(not u.SHA.fullmatch(v) for v in (control,pr['base']['sha'],pr['head']['sha'])):
        raise ValueError('invalid recipe identities')
    return pr,control,pr['head']['sha']


def assert_recipe_identity(record,merged=False,control=None):
    from tools import update as u
    merged=merged or bool(record.get('accepted'))
    if not merged:
        pr,_,_=recipe_identity(record['pr_number'],record['base'],record['head'])
    else:
        pr=u.api(u.route('/pulls/'+str(record['pr_number'])))
        protected_branch(record['pkgbase'])
        expected_control=control or record['base']
        if pr['state']!='closed' or not pr.get('merged') or not pr.get('merged_at') or pr['head']['sha']!=record['head'] or u.main_sha()!=expected_control or u.trusted_checkout_sha()!=expected_control:
            raise ValueError('accepted recipe identity changed')
        if record.get('accepted') and pr.get('merge_commit_sha')!=record['accepted']:
            raise ValueError('accepted recipe merge changed')
    expected_branch=record['head_branch']
    if pr['base']['ref']!='pkg/'+record['pkgbase'] or pr['base']['sha']!=record['recipe_base'] or pr['head']['ref']!=expected_branch or any(pr[side]['repo']['full_name']!=u.repository() for side in ('base','head')):
        raise ValueError('recipe candidate base/head changed')
    tree=u.api(u.route('/git/commits/'+record['head']))['tree']['sha']
    if tree!=record['recipe_tree']:raise ValueError('recipe tree identity mismatch')
    return pr
