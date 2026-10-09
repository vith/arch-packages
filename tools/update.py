"""Trusted-main update controller. Candidate code runs only in isolated containers."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tarfile

from tools import sources, recipes
from tools.github_api import api, download
from tools.recipe_gate import classify_recipe_update, input_digest, parse_srcinfo, tree_manifest, harness_digest

ROOT=Path(__file__).resolve().parents[1]
SHA=re.compile(r'^[0-9a-f]{40}$')
NAME=re.compile(r'^[a-z0-9][a-z0-9+_.-]*$')
MAX_FILE=4*1024*1024
MAX_TREE=64*1024*1024


def dump(path,value):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    Path(path).write_bytes(sources.canonical(value))


def load(path):
    data=Path(path).read_bytes()
    if len(data)>MAX_TREE:
        raise ValueError('JSON exceeds bound')
    return json.loads(data)


def repository():
    value=os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',value):
        raise ValueError('invalid repository')
    return value


def route(suffix):
    return '/repos/'+repository()+suffix


def main_sha():
    return api(route('/git/ref/heads/main'))['object']['sha']


def pr_identity(number, base=None, head=None):
    if not str(number).isdecimal() or int(number)<1:
        raise ValueError('invalid PR number')
    pr=api(route('/pulls/'+str(number)))
    if pr['state']!='open' or pr['base']['ref']!='main' or pr['base']['repo']['full_name']!=repository():
        raise ValueError('PR is not an open main candidate')
    actual_base=main_sha()
    if pr['base']['sha']!=actual_base or base and base!=actual_base or head and head!=pr['head']['sha']:
        raise ValueError('candidate base/head changed; redispatch required')
    if not SHA.fullmatch(actual_base) or not SHA.fullmatch(pr['head']['sha']):
        raise ValueError('invalid candidate identity')
    return pr,actual_base,pr['head']['sha']


def safe_path(name):
    path=PurePosixPath(name)
    if not name or name.startswith('/') or '\\' in name or any(p in {'..','.'} for p in name.split('/')):
        raise ValueError('unsafe artifact path')
    return path


def extract_tree(archive,destination,allowed=None,source_assets=False):
    destination=Path(destination)
    destination.mkdir(parents=True,exist_ok=False)
    total=0; seen=set();links=[]
    with tarfile.open(archive,'r:*') as stream:
        members=stream.getmembers()
        if len(members)>10000:
            raise ValueError('too many artifact entries')
        # GitHub tarball has exactly one wrapper directory.
        roots={PurePosixPath(m.name).parts[0] for m in members if m.name}
        if len(roots)!=1:
            raise ValueError('unexpected artifact roots')
        root=next(iter(roots))
        for member in members:
            safe_path(member.name.rstrip('/'))
            parts=PurePosixPath(member.name).parts
            if parts[0]!=root:
                raise ValueError('inconsistent archive wrapper')
            relative=PurePosixPath(*parts[1:])
            if not relative.parts:
                continue
            name=str(relative)
            if name in seen:
                raise ValueError('duplicate artifact path')
            seen.add(name)
            if allowed and relative.parts[0] not in allowed:
                raise ValueError('unexpected top-level artifact input')
            target=destination/name
            if member.isdir():
                target.mkdir(parents=True,exist_ok=True)
                continue
            if member.issym():
                import posixpath
                link=member.linkname
                if not link or link.startswith('/') or '\\' in link:
                    raise ValueError('absolute or invalid artifact symlink')
                resolved=posixpath.normpath(str(relative.parent/link))
                if resolved=='..' or resolved.startswith('../') or resolved.startswith('/'):
                    raise ValueError('escaping artifact symlink')
                links.append((target,link))
                continue
            limit=MAX_FILE
            if not member.isfile() or member.mode not in {0o644,0o755,0o664,0o775} or member.size>limit:
                raise ValueError('artifact contains unsafe mode/type/size')
            total+=member.size
            if total>MAX_TREE:
                raise ValueError('artifact exceeds tree bound')
            target.parent.mkdir(parents=True,exist_ok=True)
            with stream.extractfile(member) as handle:
                with target.open('wb') as output:
                    shutil.copyfileobj(handle,output,length=1024*1024)
            target.chmod(0o755 if member.mode&0o111 else 0o644)
    for target,link in links:
        if target.exists() or any(p.is_symlink() for p in target.parents if p!=destination.parent):
            raise ValueError('artifact symlink is a parent of extracted input')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.symlink_to(link)
    for target,_ in links:
        if not target.resolve(strict=False).is_relative_to(destination.resolve()):
            raise ValueError('artifact symlink chain escapes trusted root')

    return destination


def checkout_data(sha,destination,pins=None):
    if not SHA.fullmatch(sha):
        raise ValueError('invalid immutable tree SHA')
    archive=Path(str(destination)+'.tar.gz')
    download('https://api.github.com/repos/'+repository()+'/tarball/'+sha,archive,maximum=MAX_TREE)
    root=extract_tree(archive,destination)
    exact=recipes.materialize(root,sha,repository(),extract_tree)
    if pins is not None:
        pins.update(exact)
    return root


def status(head,context,state,description):
    if not SHA.fullmatch(head) or context not in {'verify','recipe-policy','candidate-build'}:
        raise ValueError('invalid status identity')
    api(route('/statuses/'+head),'POST',{'state':state,'context':context,'description':description[:140]})


def run_candidate_tests(root, image):
    """Execute candidate tests only inside a disposable, credential-free container."""
    environment = {k: v for k, v in os.environ.items() if k in {'PATH', 'HOME', 'DOCKER_HOST', 'TMPDIR'}}
    command = 'pacman -Syu --noconfirm --needed -- python python-yaml git gnupg && cd /verify && python -m unittest discover -s tests -p "test_*.py"'
    container = subprocess.check_output([
        'docker', 'create', '--cap-drop=ALL', '--cap-add=CHOWN',
        '--cap-add=DAC_OVERRIDE', '--cap-add=FOWNER', '--cap-add=SETUID',
        '--cap-add=SETGID', '--security-opt=no-new-privileges',
        image, '/bin/bash', '-c', command,
    ], text=True, env=environment).strip()
    try:
        subprocess.run(['docker', 'cp', str(root) + '/.', container + ':/verify'], check=True, env=environment)
        subprocess.run(['docker', 'start', '-a', container], check=True, env=environment)
    finally:
        subprocess.run(['docker', 'rm', '-f', container], check=True, env=environment)


def remove_verification_tree(root):
    """Restore directory permissions only after trusted source verification ends."""
    root = Path(root)
    for directory, _, _ in os.walk(root, followlinks=False):
        Path(directory).chmod(0o755)
    shutil.rmtree(root)


def policy_at(root):
    obj=load(root/'packages.json')
    if obj.get('schema')!=1 or not obj['packages']:
        raise ValueError('expected nonempty enrolled package policy')
    result={p['pkgbase']:p for p in obj['packages']}
    if len(result)!=len(obj['packages']) or any(not NAME.fullmatch(n) for n in result):
        raise ValueError('invalid package enrollment')
    return result


def affected_packages(base,head,policies):
    # Complete manifests include modes and all policy/harness paths.
    old=tree_manifest(base); new=tree_manifest(head)
    if old==new:
        return [],False
    a={row['path']:row for row in old};b={row['path']:row for row in new}
    changed={n for n in a.keys()|b.keys() if a.get(n)!=b.get(n)}
    affected=set();shared=False
    for path in changed:
        parts=PurePosixPath(path).parts
        if parts[0]=='recipes' and len(parts)>1 and parts[1] in policies:
            affected.add(parts[1])
        elif parts[0] in {'inputs','upstream'} and len(parts)==2 and Path(parts[1]).stem in policies:
            affected.add(Path(parts[1]).stem)
        else:
            shared=True
    if any(path == 'build-image.txt' or path == 'packages.json' or path == '.gitmodules' or path.startswith('tools/') for path in changed):
        affected=set(policies)
    return sorted(affected),shared


def validate_source_policy(lock,policy):
    sources.validate_lock(lock)
    expected={s['id']:s for s in policy['sources']}
    if {s['id'] for s in lock['sources']}!=set(expected):
        raise ValueError('source enrollment set changed')
    pkgver=parse_version(lock['version'])
    for source in lock['sources']:
        enrolled=expected[source['id']]
        if source['kind']!=enrolled['kind']:
            raise ValueError('source kind changed')
        if source['kind']=='git':
            expression=source['source'].split('#',1)
            if len(expression)!=2 or '=' not in expression[1]:
                raise ValueError('Git source must retain enrolled native ref semantics')
            kind,value=expression[1].split('=',1)
            ref=('refs/tags/' if kind=='tag' else 'refs/heads/' if kind=='branch' else '')+value
            if not ref or source['ref']!=ref:
                raise ValueError('frozen ref differs from native source')
        template=enrolled['source_template']
        if template.format(version=pkgver)!=source['source']:
            raise ValueError('source native template mismatch')
        url=enrolled.get('url_template')
        if url and url.format(version=pkgver)!=source['url']:
            raise ValueError('source URL template mismatch')
    return lock


def parse_version(full):
    return full.split(':',1)[-1].rsplit('-',1)[0]


def prepare(number,output):
    pr,base,head=pr_identity(number)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    oldpins={};newpins={}
    old=checkout_data(base,output/'base-tree',oldpins);new=checkout_data(head,output/'head-tree',newpins)
    policies=policy_at(old)
    proposed=policy_at(new)
    if set(proposed)!=set(newpins):
        raise ValueError('candidate package enrollment and recipe pins differ')
    retired=set(policies)-set(proposed)
    names,shared=affected_packages(old,new,policies)
    names=sorted((set(names)|{name for name in policies if oldpins.get(name)!=newpins.get(name)})-retired)
    shared |= oldpins.keys()!=newpins.keys()
    image=(old/'build-image.txt').read_text().strip()
    if not re.fullmatch(r'ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}',image):
        raise ValueError('build image is not pinned official Arch')
    status(head,'verify','pending','Testing exact candidate in an isolated container')
    try:
        run_candidate_tests(new,image)
        pr_identity(number,base,head)
    except Exception:
        status(head,'verify','failure','Exact candidate verification failed')
        raise
    status(head,'verify','success','Exact candidate regression tests passed')
    harness=harness_digest(old)
    packages=[]
    decisions=[{'pkgbase':name,'decision':'manual','reason':'Package enrollment retired'} for name in sorted(retired)]
    for name in names:
        lock=load(new/'inputs'/f'{name}.json')
        validate_source_policy(lock,policies[name])
        decision=classify_recipe_update(old/'recipes'/name,new/'recipes'/name,policies[name])
        if decision['decision']=='invalid':
            raise ValueError(decision['reason'])
        # No candidate-provided evidence grants automatic approval. Independent
        # trusted source receipts are verified below for bot ownership only.
        evidence=independent_transition(old,new,name,policies[name],output/f'verify-{name}')
        if evidence:
            trusted=dict(policies[name]); trusted['_verified_transition']=evidence
            decision=classify_recipe_update(old/'recipes'/name,new/'recipes'/name,trusted)
        if oldpins[name]!=newpins[name] and not recipes.is_ancestor(repository(),oldpins[name],newpins[name]):
            decision={'decision':'manual','reason':'Recipe history is not a fast-forward of the accepted pin'}
        decisions.append({'pkgbase':name,**decision})
        recipe=output/'bundle'/'recipes'/name;recipes.copy_recipe(new/'recipes'/name,recipe)
        digest=input_digest(recipe,lock,policies[name],image,harness)
        packages.append({'pkgbase':name,'recipe_commit':newpins[name],'previous_recipe_commit':oldpins[name],'recipe_dir':f'recipes/{name}','lock':lock,'policy':policies[name],'input_digest':digest,'expected_srcinfo':(recipe/'.SRCINFO').read_text()})
    mechanical=not shared and len(names)==1 and all(d['decision']=='mechanical' for d in decisions)
    bundle={'schema':1,'repository':repository(),'base':base,'head':head,'recipe_pins':newpins,'previous_recipe_pins':oldpins,'run_id':os.environ['GITHUB_RUN_ID'],'run_attempt':os.environ['GITHUB_RUN_ATTEMPT'],'image':image,'harness_sha':harness,'packages':packages}
    dump(output/'bundle'/'bundle.json',bundle)
    record={**bundle,'pr_number':int(number),'mechanical':mechanical,'decisions':decisions}
    dump(output/'candidate.json',record)
    with tarfile.open(output/'bundle.tar','w') as archive:
        archive.add(output/'bundle',arcname='bundle',recursive=True)
    shutil.rmtree(output/'bundle')
    status(head,'candidate-build','pending','Building complete frozen candidate')
    status(head,'recipe-policy','pending','Source policy verified; awaiting exact build/review')
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as handle:
            handle.write(f'base={base}\nhead={head}\nmechanical={str(mechanical).lower()}\n')
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as handle:
            handle.write(f'PR #{number}\nBase `{base}`\nHead `{head}`\n'+json.dumps(decisions,indent=2)+'\n')
    # Only bounded explicit data goes to the build. Neither candidate automation
    # nor base/head full trees leave the control artifact directory.
    for path in output.glob('verify-*'):
        remove_verification_tree(path)
    shutil.rmtree(old);shutil.rmtree(new)
    return record


def verify_provenance(old,new,lock,policy,watcher_id=None):
    if set(old)!=set(new) or any(old[k]!=new[k] for k in old if k not in {'aur','watchers'}):
        raise ValueError('unrelated provenance changed')
    if len(old['watchers'])!=len(new['watchers']):
        raise ValueError('watcher enrollment changed')
    changed=[]
    for previous,current in zip(old['watchers'],new['watchers']):
        if previous==current:
            continue
        if previous['kind'] in {'release','tag'}:
            tag=current.get('accepted_tag')
            match=re.fullmatch(previous['tag_pattern'],tag or '')
            source=next(s for s in lock['sources'] if s['id']==previous['source_id'])
            expected=next(s for s in policy['sources'] if s['id']==previous['source_id'])
            if source['kind']=='git' and source['ref']!='refs/tags/'+tag:
                raise ValueError('watcher tag differs from frozen Git tag')
            if not match:
                raise ValueError('source lacks authentic enrolled tag')
            version=match[1] if len(match.groups())==1 else '.'.join(match.groups())
            if source['source']!=expected['source_template'].format(version=version):
                raise ValueError('source lock and watcher tag disagree')
        changed.append(previous['id'])
        allowed={'accepted_tag','accepted_tag_object','accepted_peeled_commit','release_id','asset_id'}
        if any(previous.get(k)!=current.get(k) for k in previous.keys()|current.keys() if k not in allowed):
            raise ValueError('source watcher policy changed')
        tag=current.get('accepted_tag')
        if not tag or not re.fullmatch(previous['tag_pattern'],tag):
            raise ValueError('unenrolled tag identity')
        rows=sources.git('ls-remote',previous['url'],'refs/tags/'+tag,'refs/tags/'+tag+'^{}').splitlines()
        identities={r.split()[1]:r.split()[0] for r in rows}
        obj=identities.get('refs/tags/'+tag)
        peeled=identities.get('refs/tags/'+tag+'^{}',obj)
        if obj!=current['accepted_tag_object'] or current.get('accepted_peeled_commit')!=peeled:
            raise ValueError('source tag provenance is not authentic')
        if previous['kind']=='release':
            release=json.loads(sources.fetch('https://api.github.com/repos/'+previous['repository']+'/releases/tags/'+tag))
            if release['draft'] or release['prerelease'] or release['id']!=current['release_id']:
                raise ValueError('release provenance mismatch')
            matches=[a for a in release['assets'] if a['name']==previous['asset']]
            source=next(s for s in lock['sources'] if s['id']==previous['source_id'])
            if len(matches)!=1 or matches[0]['id']!=source['asset_id'] or source['release_id']!=release['id'] or source['url']!=matches[0]['browser_download_url']:
                raise ValueError('release asset identity mismatch')
    if old['aur']!=new['aur']:
        if not old['aur'] or not new['aur'] or any(old['aur'].get(k)!=new['aur'].get(k) for k in old['aur'].keys()|new['aur'].keys() if k!='commit'):
            raise ValueError('AUR enrollment changed')
        watchers=[w for w in old['watchers'] if w['kind']=='aur']
        if len(watchers)!=1:
            raise ValueError('ambiguous AUR watcher')
        row=sources.git('ls-remote',watchers[0]['url'],watchers[0].get('ref','refs/heads/master')).splitlines()
        if len(row)!=1 or row[0].split()[0]!=new['aur']['commit']:
            raise ValueError('AUR provenance no longer authentic')
        changed.append(watchers[0]['id'])
    if watcher_id and any(identity!=watcher_id for identity in changed):
        raise ValueError('watcher changed unrelated provenance')
    return changed


def independent_transition(old,new,name,policy,work):
    oldlock=load(old/'inputs'/f'{name}.json');newlock=load(new/'inputs'/f'{name}.json')
    oldpro=load(old/'upstream'/f'{name}.json');newpro=load(new/'upstream'/f'{name}.json')
    verify_provenance(oldpro,newpro,newlock,policy)
    if [s['id'] for s in oldlock['sources']]!=[s['id'] for s in newlock['sources']]:
        raise ValueError('source record order changed')
    for previous,current in zip(oldlock['sources'],newlock['sources']):
        enrolled=next(s for s in policy['sources'] if s['id']==current['id'])
        if previous!=current and not enrolled['mutable'] and current['kind']!='local':
            return None
    changed=[(a,b) for a,b in zip(oldlock['sources'],newlock['sources']) if a!=b]
    if len(oldlock['sources'])!=len(newlock['sources']) or not changed and oldpro==newpro:
        return None
    aur_fast_forward=True
    if oldpro['aur']!=newpro['aur']:
        watcher=next(w for w in oldpro['watchers'] if w['kind']=='aur')
        configured={**watcher,'work_dir':str(work/'aur-verify')}
        transition=sources.discover_aur(configured,oldpro['aur'])
        if transition is None or transition['commit']!=newpro['aur']['commit']:
            return None
        aur_fast_forward=transition.get('fast_forward') is True
        expected=work/'integrated'
        recipes.copy_recipe(old/'recipes'/name,expected)
        integration=apply_aur_transition(expected,transition,policy,work/'verify-three-way')
        if integration['conflicts']:
            return None
        newmetadata=parse_srcinfo((new/'recipes'/name/'.SRCINFO').read_text())
        render_recipe(expected,policy,newmetadata['pkgver'],{s['id']:s['checksums'] for s in newlock['sources']},pkgrel=newmetadata['pkgrel'])
        (expected/'.SRCINFO').write_text((new/'recipes'/name/'.SRCINFO').read_text())
        if tree_manifest(expected)!=tree_manifest(new/'recipes'/name):
            return None
    # Verify every source independently, including unchanged auxiliary bytes.
    authentic=True;fast_forward=aur_fast_forward
    work.mkdir(parents=True,exist_ok=True)
    for source in newlock['sources']:
        if source['kind']=='local':
            file=new/'recipes'/name/source['source']
            for alg,digest in source['checksums'].items():
                if digest!='SKIP' and hashlib.new(alg,file.read_bytes()).hexdigest()!=digest:
                    raise ValueError('local source checksum mismatch')
        elif source['kind']=='git':
            mirror=sources.materialize_sources({'schema':1,'version':newlock['version'],'sources':[source]},work/source['id'])[source['url']]
            previous=next(s for s in oldlock['sources'] if s['id']==source['id'])
            if previous['commit']:
                fast_forward &= sources.git('merge-base','--is-ancestor',previous['commit'],source['commit'],cwd=mirror,check=False).returncode==0
            remote_tags=sources.git('ls-remote','--tags',source['url']).splitlines()
            tags={r.split()[1]:r.split()[0] for r in remote_tags}
            for tag in (previous['git_context']['version_tag'],source['git_context']['version_tag']):
                authentic &= tag is None or tags.get(tag['name'])==tag['object']
            remote=sources.git('ls-remote',source['url'],source['ref']).splitlines()
            if len(remote)!=1 or remote[0].split()[0]!=(source['tag_object'] or source['commit']):
                raise ValueError('source transition no longer authentic')
        else:
            sources.freeze_source(source)
    # Exact provenance ownership is required; candidate fields are not evidence.
    if oldpro.get('bootstrap')!=newpro.get('bootstrap') or oldpro.get('pkgbase')!=newpro.get('pkgbase'):
        return None
    validate_source_policy(newlock,policy)
    # This is a provisional classification only. validate_build requires the
    # trusted native harness to independently reproduce the complete SRCINFO.
    return {'srcinfo':(new/'recipes'/name/'.SRCINFO').read_text(),'checksums':{s['id']:s['checksums'] for s in newlock['sources']},'source_templates_verified':True,'lock_verified':True,'auxiliary_inputs_verified':True,'authentic':authentic,'fast_forward':fast_forward}


def validate_build(record,directory,report=True):
    record=load(record) if not isinstance(record,dict) else record
    pr_identity(record['pr_number'],record['base'],record['head'])
    directory=Path(directory);evidence=load(directory/'native-evidence.json')
    for key in ('schema','repository','base','head','recipe_pins','previous_recipe_pins','run_id','run_attempt','image','harness_sha'):
        if evidence.get(key)!=record[key]:
            raise ValueError('build artifact identity mismatch: '+key)
    expected={p['pkgbase']:p for p in record['packages']}
    built={p['pkgbase']:p for p in evidence['packages']}
    if set(built)!=set(expected) or len(built)!=len(evidence['packages']):
        raise ValueError('incomplete/duplicate candidate outputs')
    filenames=set()
    for name,pkg in built.items():
        candidate=expected[name]
        pin=candidate['recipe_commit']
        if not SHA.fullmatch(pin) or record['recipe_pins'].get(name)!=pin or pkg.get('recipe_commit')!=pin or pkg['input_digest']!=candidate['input_digest'] or pkg['source_lock']!=candidate['lock']:
            raise ValueError('build frozen input mismatch')
        expected_metadata=candidate['expected_srcinfo']
        if pkg['metadata'].get('srcinfo')!=expected_metadata or parse_srcinfo(expected_metadata)['version']!=candidate['lock']['version']:
            raise ValueError('native preparation metadata differs from exact candidate')
        wanted={(o['name'],o['arch']) for o in candidate['policy']['outputs']}
        actual={(f['name'],f['arch']) for f in pkg['files']}
        if wanted!=actual or len(actual)!=len(pkg['files']):
            raise ValueError('missing/extra native package output')
        for file in pkg['files']:
            filename=file['filename']
            if safe_path(filename).parts!=(filename,) or filename in filenames:
                raise ValueError('unsafe/duplicate output name')
            filenames.add(filename)
            path=directory/filename
            if not path.is_file() or path.is_symlink() or file['version']!=candidate['lock']['version'] or hashlib.sha256(path.read_bytes()).hexdigest()!=file['sha256']:
                raise ValueError('native output bytes/version mismatch')
    if report:
        status(record['head'],'candidate-build','success','Every frozen package output verified')
        if record['mechanical']:
            status(record['head'],'recipe-policy','success','Independent mechanical source transition verified')
    return evidence


def review(record):
    record=load(record)
    if record['mechanical']:
        raise ValueError('mechanical candidates do not use human checkpoint')
    pr_identity(record['pr_number'],record['base'],record['head'])
    states={s['context']:s['state'] for s in reversed(api(route('/commits/'+record['head']+'/statuses')))}
    if states.get('candidate-build')!='success':
        raise ValueError('review requires complete successful candidate build')
    status(record['head'],'recipe-policy','success','Owner approved exact head/base through recipe-review')


def finalize(number,base,head,record_path,directory):
    record=load(record_path)
    if (record['pr_number'],record['base'],record['head'])!=(int(number),base,head):
        raise ValueError('finalize candidate binding mismatch')
    validate_build(record,directory)
    pr_identity(number,base,head)
    states={s['context']:s['state'] for s in reversed(api(route('/commits/'+head+'/statuses')))}
    if any(states.get(k)!='success' for k in ('verify','candidate-build','recipe-policy')):
        raise ValueError('required exact-head statuses missing')
    if not record['mechanical']:
        return {'merged':False,'reason':'Human merge required'}
    result=api(route('/pulls/'+str(number)+'/merge'),'PUT',{'sha':head,'merge_method':'merge'})
    if not result.get('merged') or not SHA.fullmatch(result.get('sha','')):
        raise ValueError('expected-head merge failed')
    api(route('/actions/workflows/publish.yml/dispatches'),'POST',{'ref':'main','inputs':{'accepted_sha':result['sha']}})
    return result


def render_recipe(recipe,policy,pkgver,checksums,pkgrel='1'):
    text=(recipe/'PKGBUILD').read_text()
    rules=policy['automatic']
    baseline=parse_srcinfo((recipe/'.SRCINFO').read_text())
    for key,value in [('version',pkgver),('pkgrel',pkgrel)]:
        rule=rules[key]
        enrolled=rule['literal_assignment'].split('=',1)[1]
        quote=enrolled[0] if enrolled[:1] in {"'",'"'} else ''
        pattern=re.compile(r'^'+re.escape(rule['assignment']+'='+quote)+r'([A-Za-z0-9.+_]+)'+re.escape(quote)+r'$',re.M)
        matches=list(pattern.finditer(text))
        if len(matches)!=1 or not re.fullmatch(r'[A-Za-z0-9.+_]+',value):
            raise ValueError('ambiguous enrolled literal assignment')
        match=matches[0]
        text=text[:match.start(1)]+value+text[match.end(1):]
    array=rules.get('checksum_array')
    if array and checksums and rules['checksums']:
        literal=array['literal']
        current_values=[v for _,key,v in baseline['fields'] if key==array['assignment']]
        if len(current_values)!=len(array['values']):
            raise ValueError('enrolled checksum layout changed')
        for initial,current in zip(array['values'],current_values):
            if initial!=current:
                if literal.count(initial)!=1:
                    raise ValueError('ambiguous checksum enrollment')
                literal=literal.replace(initial,current)
        values=list(current_values)
        for rule in rules['checksums']:
            if rule['source_id'] in checksums:
                values[rule['index']]=checksums[rule['source_id']][rule['algorithm']]
        replacement=literal
        for old,new in zip(current_values,values):
            if old!=new:
                if replacement.count(old)!=1:
                    raise ValueError('ambiguous enrolled checksum span')
                replacement=replacement.replace(old,new)
        if text.count(literal)==1:
            text=text.replace(literal,replacement)
        elif text.count(replacement)!=1:
            raise ValueError('enrolled checksum literal missing')
    (recipe/'PKGBUILD').write_text(text)


def native_probe(recipe,lock,policy,work,preserve_pkgrel=False):
    image=(ROOT/'build-image.txt').read_text().strip()
    if not re.fullmatch(r'ghcr.io/archlinux/archlinux@sha256:[0-9a-f]{64}',image):
        raise ValueError('native probe image not pinned')
    # Only this credential-free fresh process executes recipe code.
    probe=work/'probe';probe.mkdir(parents=True)
    recipes.copy_recipe(recipe,probe/'recipe')
    shutil.copytree(ROOT/'tools',probe/'tools')
    dump(probe/'lock.json',lock);dump(probe/'policy.json',policy)
    mirrors=[]
    for source in lock['sources']:
        if source['kind']=='git':
            mirror=work/source['id']/'freeze.git'
            if not mirror.exists():
                mapping=sources.materialize_sources({'schema':1,'version':lock['version'],'sources':[source]},work/('materialize-'+source['id']))
                mirror=mapping[source['url']]
            target=probe/'mirrors'/source['id'];shutil.copytree(mirror,target)
            mirrors.append((source['url'],'file:///work/mirrors/'+source['id']))
    config='[core]\n\thooksPath = /dev/null\n[protocol "file"]\n\tallow = always\n'
    for source in lock['sources']:
        if source['kind']=='git':
            config+='[safe]\n\tdirectory = /work/mirrors/'+source['id']+'\n'
    for url,target in mirrors:
        config+='[url "'+target+'"]\n\tinsteadOf = '+url+'\n'
    (probe/'gitconfig').write_text(config)
    import shlex
    install='import subprocess;from pathlib import Path;from tools.native import dependency_names;subprocess.run(["pacman","-S","--noconfirm","--needed","--",*dependency_names(Path("recipe/.SRCINFO").read_text())],check=True)'
    execute='import json;from pathlib import Path;from tools.sources import probe_recipe;print(json.dumps(probe_recipe(Path("recipe"),json.load(open("lock.json")),json.load(open("policy.json")),preserve_pkgrel='+repr(preserve_pkgrel)+')))'
    script='set -euo pipefail\npacman -Syu --noconfirm --needed base-devel git python util-linux\nuseradd -m -u 1000 builder\ncp -a /probe /work\nchown -R root:root /work\nchmod a-w /work/gitconfig\nif [ -d /work/mirrors ]; then chmod -R a-w /work/mirrors; fi\nchown -R builder:builder /work/recipe\ncd /work\nPYTHONPATH=/work python -c '+shlex.quote(install)+'\nsetpriv --reuid=1000 --regid=1000 --clear-groups --inh-caps=-all --ambient-caps=-all --bounding-set=-all --no-new-privs env -i PATH=/usr/bin:/bin HOME=/home/builder LANG=C.UTF-8 GOTOOLCHAIN=local MAKEPKG_GIT_CONFIG=/work/gitconfig GIT_CONFIG_SYSTEM=/work/gitconfig GIT_CONFIG_GLOBAL=/dev/null PYTHONPATH=/work python -c '+shlex.quote(execute)+' > /work/result.json\ncp /work/result.json /result/result.json\n'
    (probe/'run.sh').write_text(script)
    result=work/'result';result.mkdir()
    subprocess.run(['docker','run','--rm','--cap-drop=ALL','--cap-add=CHOWN','--cap-add=FOWNER','--cap-add=SETUID','--cap-add=SETGID','--cap-add=DAC_OVERRIDE','--cap-add=SETPCAP','--security-opt=no-new-privileges','-v',str(probe.resolve())+':/probe:ro','-v',str(result.resolve())+':/result',image,'bash','/probe/run.sh'],check=True,env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','DOCKER_HOST','TMPDIR'}})
    metadata=load(result/'result.json')
    parse_srcinfo(metadata['srcinfo'])
    return metadata


def write_aur_tree(files,recipe):
    links=[]
    for file in files:
        target=recipe/str(safe_path(file['path']))
        target.parent.mkdir(parents=True,exist_ok=True)
        if file['mode']=='120000':
            link=file['content']
            if not link or link.startswith('/') or '\\' in link:
                raise ValueError('unsafe AUR symlink')
            links.append((target,link))
        elif file['mode'] in {'100644','100755'}:
            target.write_text(file['content']);target.chmod(int(file['mode'],8)&0o777)
        else:
            raise ValueError('unsafe AUR file mode')
    for target,link in links:
        if target.exists() or any(p.is_symlink() for p in target.parents):
            raise ValueError('AUR symlink cannot be an input parent')
        target.symlink_to(link)
    if any(not target.resolve(strict=False).is_relative_to(recipe.resolve()) for target,_ in links):
        raise ValueError('AUR symlink escapes recipe')


def integrate_aur(local,transition,work):
    """Three-way file/mode merge without Git attributes, hooks or drivers."""
    ours={}
    for path in local.rglob('*'):
        if path.is_symlink():
            value={'mode':'120000','content':os.readlink(path)}
        elif path.is_file():
            try:
                content=path.read_bytes().decode('utf-8')
            except UnicodeDecodeError:
                return {'conflicts':[path.relative_to(local).as_posix()],'files':None}
            value={'mode':'100755' if path.stat().st_mode&0o111 else '100644','content':content}
        elif path.is_dir():
            continue
        else:
            raise ValueError('unsupported maintained recipe input')
        ours[path.relative_to(local).as_posix()]=value
    base={f['path']:{k:f[k] for k in ('mode','content')} for f in transition['previous_files']}
    theirs={f['path']:{k:f[k] for k in ('mode','content')} for f in transition['files']}
    work.mkdir(parents=True,exist_ok=False)
    merged={};conflicts=[]
    def choose(a,b,c):
        if a==c or c==b:
            return a,True
        if a==b:
            return c,True
        return None,False
    for index,name in enumerate(sorted(ours.keys()|base.keys()|theirs.keys())):
        a,b,c=ours.get(name),base.get(name),theirs.get(name)
        value,clean=choose(a,b,c)
        if clean:
            if value is not None:merged[name]=value
            continue
        if a is None or b is None or c is None:
            conflicts.append(name);continue
        mode,clean=choose(a['mode'],b['mode'],c['mode'])
        if not clean or mode=='120000' or any(row['mode']=='120000' for row in (a,b,c)):
            conflicts.append(name);continue
        content,clean=choose(a['content'],b['content'],c['content'])
        if not clean:
            paths=[]
            for role,row in [('ours',a),('base',b),('theirs',c)]:
                path=work/(str(index)+'-'+role)
                path.write_bytes(row['content'].encode())
                paths.append(path)
            env={k:v for k,v in os.environ.items() if k in {'PATH','LANG'}}
            env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null')
            result=subprocess.run(['git','-c','core.hooksPath=/dev/null','merge-file','--stdout','--',*map(str,paths)],env=env,capture_output=True)
            if result.returncode!=0:
                if result.returncode==1 or b'Cannot merge binary' in result.stderr:
                    conflicts.append(name);continue
                raise ValueError('deterministic recipe three-way merge failed')
            if len(result.stdout)>MAX_FILE:
                raise ValueError('merged recipe file exceeds bound')
            content=result.stdout.decode('utf-8')
        merged[name]={'mode':mode,'content':content}
    if conflicts:
        return {'conflicts':conflicts,'files':None}
    return {'conflicts':[],'files':[{'path':name,**value} for name,value in sorted(merged.items())]}


def apply_aur_transition(recipe,transition,policy,work):
    if policy['authority']=='aur':
        integration={'conflicts':[],'files':transition['files']}
    else:
        integration=integrate_aur(recipe,transition,work)
    if integration['files'] is not None:
        shutil.rmtree(recipe);recipe.mkdir()
        write_aur_tree(integration['files'],recipe)
    return integration


def align_aur_lock(recipe,lock,policy,work):
    metadata=parse_srcinfo((recipe/'.SRCINFO').read_text())
    version=metadata['pkgver']
    for source in lock['sources']:
        enrolled=next(s for s in policy['sources'] if s['id']==source['id'])
        algorithm=enrolled['checksum_algorithm']
        values=[v for _,key,v in metadata['fields'] if key in {algorithm+'sums',algorithm+'sums_x86_64'}]
        expanded=enrolled['source_template'].format(version=version)
        native=[v for _,key,v in metadata['fields'] if key in {'source','source_x86_64'}]
        if expanded not in native:
            raise ValueError('AUR integration changed unenrolled native source layout')
        old=dict(source)
        source['source']=expanded
        source['url']=enrolled['url_template'].format(version=version) if enrolled.get('url_template') else None
        source['checksums'][algorithm]=values[enrolled['checksum_index']]
        if source['kind']=='git' and source['source']!=old['source']:
            kind,ref=expanded.split('#',1)[1].split('=',1)
            source['ref']=('refs/tags/' if kind=='tag' else 'refs/heads/')+ref
            source['commit']=None;source['tag_object']=None;source['peeled_commit']=None
            source.update(sources.freeze_source({**source,'work_dir':str(work/source['id'])}))
        elif source['kind'] in {'archive','release'}:
            sources.freeze_source(source)
    return metadata


def prune_probe_work(work):
    for child in list(work.iterdir()):
        if child.name in {'probe','result','watched.git','aur.git','three-way'}:
            shutil.rmtree(child)
        elif child.is_dir() and child.name!='recipe':
            for nested in list(child.iterdir()):
                if nested.name.endswith('.git'):
                    remove_verification_tree(nested)


def control_checkout():
    """Materialize only a clean immutable control checkout, never moving tips."""
    if sources.git('status','--porcelain','--untracked-files=all',cwd=ROOT):
        raise ValueError('control checkout has uncommitted changes')
    base=sources.git('rev-parse','HEAD',cwd=ROOT)
    if not SHA.fullmatch(base):
        raise ValueError('invalid control commit')
    pins=recipes.materialize(ROOT,base,repository(),extract_tree)
    return base,pins


def push_ref(cwd,sha,branch,previous):
    """Exact lease, including absence, without hooks or persistent credentials."""
    if not SHA.fullmatch(sha) or previous and not SHA.fullmatch(previous):
        raise ValueError('invalid ref update identity')
    remote='https://github.com/'+repository()+'.git'
    token=os.environ['GITHUB_TOKEN']
    credential=base64.b64encode(('x-access-token:'+token).encode()).decode()
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','TMPDIR'}}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null',GIT_TERMINAL_PROMPT='0',GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',GIT_CONFIG_VALUE_0='AUTHORIZATION: basic '+credential)
    subprocess.run(['git','-c','core.hooksPath=/dev/null','push','--force-with-lease=refs/heads/'+branch+':'+(previous or ''),remote,sha+':refs/heads/'+branch],cwd=cwd,env=env,check=True,capture_output=True)


def ref_head(branch):
    rows=sources.git('ls-remote','https://github.com/'+repository()+'.git','refs/heads/'+branch).splitlines()
    if len(rows)>1:
        raise ValueError('ambiguous recipe history ref')
    return rows[0].split()[0] if rows else None


def matches_commit(sha, tree, parents, message):
    if not sha:
        return False
    candidate = api(route('/git/commits/' + sha))
    return (candidate['tree']['sha'] == tree
            and [parent['sha'] for parent in candidate['parents']] == parents
            and candidate['message'] == message)


def pin_at(control,name):
    commit=api(route('/git/commits/'+control))
    tree=api(route('/git/trees/'+commit['tree']['sha']+'?recursive=1'))
    if tree.get('truncated'):
        raise ValueError('truncated control tree')
    rows=[r for r in tree['tree'] if r['path']=='recipes/'+name]
    if len(rows)!=1 or rows[0]['mode']!='160000' or rows[0]['type']!='commit' or not SHA.fullmatch(rows[0]['sha']):
        raise ValueError('expected exact enrolled recipe gitlink')
    return rows[0]['sha']


def retain_aur_history(name,watcher,transition,previous,work):
    """Import authentic objects, never reconstruct AUR commits through the API."""
    mirror=work/'authentic-aur.git'
    sources.git('init','--bare',mirror)
    sources.git('fetch','--no-tags',watcher['url'],watcher.get('ref','refs/heads/master'),cwd=mirror)
    new=transition['commit'];old=previous['commit']
    if sources.git('rev-parse','FETCH_HEAD',cwd=mirror)!=new:
        raise ValueError('AUR moved before authentic history import')
    if sources.git('merge-base','--is-ancestor',old,new,cwd=mirror,check=False).returncode:
        raise ValueError('AUR history was rewritten')
    branch='aur/'+name;tip=ref_head(branch)
    if tip:
        sources.git('fetch','https://github.com/'+repository()+'.git',tip,cwd=mirror)
        if sources.git('merge-base','--is-ancestor',tip,new,cwd=mirror,check=False).returncode:
            raise ValueError('AUR history ref has divergent/manual work')
    if tip!=new:
        push_ref(mirror,new,branch,tip)
    return new


def advance_maintained_history(name,pin):
    branch='pkg/'+name;tip=ref_head(branch)
    if tip==pin:
        return
    # Accepted main is authoritative, but a branch may contain pending/manual
    # work. Preserve it instead of moving the branch backwards or sideways.
    if tip and not recipes.is_ancestor(repository(),tip,pin):
        return
    sources.git('fetch','https://github.com/'+repository()+'.git',pin,cwd=ROOT)
    push_ref(ROOT,pin,branch,tip)


def bootstrap(output):
    """Read-only initial enrollment freeze/probe; publication is a separate review."""
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    base,pins=control_checkout()
    policies=policy_at(ROOT)
    for name,policy in policies.items():
        work=output/name;work.mkdir()
        recipe=work/'recipe';recipes.copy_recipe(ROOT/'recipes'/name,recipe)
        metadata=parse_srcinfo((recipe/'.SRCINFO').read_text())
        version=metadata['pkgver']
        records=[]
        for enrolled in policy['sources']:
            native=enrolled['source_template'].format(version=version)
            url=enrolled.get('url_template')
            url=url.format(version=version) if url else None
            algorithm=enrolled['checksum_algorithm']
            field=algorithm+'sums'
            values=[v for _,key,v in metadata['fields'] if key==field or key==field+'_x86_64']
            digest=values[enrolled['checksum_index']]
            record={k:None for k in sources.FIELDS}
            record.update(id=enrolled['id'],kind=enrolled['kind'],source=native,url=url,checksums={algorithm:digest})
            if enrolled['kind']=='local':
                actual=hashlib.new(algorithm,(recipe/native).read_bytes()).hexdigest()
                if actual!=digest:
                    raise ValueError('enrolled local checksum mismatch')
            else:
                if enrolled['kind']=='release':
                    provenance=load(ROOT/'upstream'/f'{name}.json')
                    watcher=next(w for w in provenance['watchers'] if w.get('source_id')==enrolled['id'] and w['kind']=='release')
                    release=json.loads(sources.fetch('https://api.github.com/repos/'+watcher['repository']+'/releases/tags/'+watcher['accepted_tag']))
                    assets=[a for a in release['assets'] if a['name']==watcher['asset'] and a['browser_download_url']==url]
                    if release['draft'] or release['prerelease'] or len(assets)!=1:
                        raise ValueError('bootstrap release asset identity mismatch')
                    record['release_id']=release['id'];record['asset_id']=assets[0]['id']
                if enrolled['kind']=='git':
                    kind,ref=native.split('#',1)[1].split('=',1)
                    record['ref']=('refs/tags/' if kind=='tag' else 'refs/heads/')+ref
                record=sources.freeze_source({**record,'work_dir':str(work/enrolled['id'])})
            records.append(record)
        lock={'schema':1,'version':metadata['version'],'sources':records}
        result=native_probe(recipe,lock,policy,work,preserve_pkgrel=True)
        lock['version']=result['version'];lock['sources']=result['sources']
        render_recipe(recipe,policy,result['pkgver'],result['checksums'],pkgrel=result['pkgrel'])
        (recipe/'.SRCINFO').write_text(result['srcinfo'])
        dump(work/'lock.json',lock);dump(work/'probe.json',{**result,'recipe_commit':pins[name]})
        prune_probe_work(work)
    dump(output/'bootstrap.json',{'schema':1,'repository':repository(),'base':base,'recipe_pins':pins,'packages':list(policies)})


def discover(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    base,pins=control_checkout()
    policies=policy_at(ROOT);receipts=[]
    for name,policy in policies.items():
        provenance=load(ROOT/'upstream'/f'{name}.json');accepted=load(ROOT/'inputs'/f'{name}.json')
        for watcher in provenance['watchers']:
            work=output/name/watcher['id'];work.mkdir(parents=True)
            configured={**watcher,'work_dir':str(work)}
            oldsource=next((s for s in accepted['sources'] if s['id']==watcher.get('source_id')),None)
            identity={'tag':watcher.get('accepted_tag'),'release_id':watcher.get('release_id'),'asset_id':watcher.get('asset_id'),'tag_object':watcher.get('accepted_tag_object')}
            if watcher['kind']=='aur':
                identity=provenance.get('aur') or {};transition=sources.discover_aur(configured,identity)
            elif watcher['kind']=='git':
                transition=sources.discover_git(configured,oldsource)
            else:
                transition=sources.discover_release_tag(configured,identity)
            if transition is None:
                related={watcher.get('source_id'),*watcher.get('related_source_ids',[])}
                for source in accepted['sources']:
                    if source['id'] in related and source['kind'] in {'archive','release'}:
                        sources.freeze_source(source)
                receipts.append({'schema':1,'pkgbase':name,'watcher_id':watcher['id'],'base':base,'recipe_commit':pins[name],'unchanged':True})
                shutil.rmtree(work)
                continue
            recipe=work/'recipe';recipes.copy_recipe(ROOT/'recipes'/name,recipe)
            lock=json.loads(json.dumps(accepted));newpro=json.loads(json.dumps(provenance))
            if watcher['kind']=='aur':
                integration=apply_aur_transition(recipe,transition,policy,work/'three-way')
                dump(work/'aur-integration.json',{'conflicts':integration['conflicts']})
                if not integration['conflicts']:
                    align_aur_lock(recipe,lock,policy,work)
                newpro['aur']['commit']=transition['commit']
            else:
                version=transition.get('version',parse_version(accepted['version']))
                ids={watcher['source_id'],*watcher.get('related_source_ids',[])}
                for source in lock['sources']:
                    if source['id'] not in ids:
                        continue
                    enrolled=next(s for s in policy['sources'] if s['id']==source['id'])
                    source['source']=enrolled['source_template'].format(version=version)
                    source['url']=enrolled['url_template'].format(version=version)
                    if source['kind']=='git':
                        source['ref']=transition['ref'] if 'ref' in transition else 'refs/tags/'+transition['tag']
                        source['commit']=transition.get('commit');source['tag_object']=None;source['peeled_commit']=None
                        source.update(sources.freeze_source({**source,'work_dir':str(work/source['id'])}))
                    else:
                        data=sources.fetch(source['url'])
                        for algorithm in source['checksums']:
                            if source['checksums'][algorithm]!='SKIP':
                                source['checksums'][algorithm]=hashlib.new(algorithm,data).hexdigest()
                        if source['kind']=='release':
                            assets=[a for a in transition['assets'] if a['name']==watcher['asset']]
                            if len(assets)!=1 or assets[0]['browser_download_url']!=source['url']:
                                raise ValueError('release asset enrollment mismatch')
                            source['release_id']=transition['release_id'];source['asset_id']=assets[0]['id']
                render_recipe(recipe,policy,version,{s['id']:s['checksums'] for s in lock['sources']})
                updated=next(w for w in newpro['watchers'] if w['id']==watcher['id'])
                if 'tag' in transition:
                    updated['accepted_tag']=transition['tag']
                    tag=sources.git('ls-remote',watcher['url'],'refs/tags/'+transition['tag']).splitlines()
                    if len(tag)!=1:
                        raise ValueError('release tag missing')
                    updated['accepted_tag_object']=tag[0].split()[0]
                    peeled=sources.git('ls-remote',watcher['url'],'refs/tags/'+transition['tag']+'^{}').splitlines()
                    updated['accepted_peeled_commit']=peeled[0].split()[0] if peeled else updated['accepted_tag_object']
                    if transition.get('release_id'):
                        updated['release_id']=transition['release_id']
                        updated['asset_id']=next(s['asset_id'] for s in lock['sources'] if s['id']==watcher['source_id'])
            probe=native_probe(recipe,lock,policy,work)
            lock['version']=probe['version'];lock['sources']=probe['sources']
            render_recipe(recipe,policy,probe['pkgver'],{s['id']:s['checksums'] for s in lock['sources']},pkgrel=probe['pkgrel'])
            (recipe/'.SRCINFO').write_text(probe['srcinfo'])
            receipt={'schema':1,'pkgbase':name,'watcher_id':watcher['id'],'base':base,'recipe_commit':pins[name],'unchanged':False,'transition':transition,'lock':lock,'provenance':newpro,'probe':probe,'tree':tree_manifest(recipe),'aur_conflicts':integration['conflicts'] if watcher['kind']=='aur' else []}
            dump(work/'receipt.json',receipt);receipts.append(receipt)
            prune_probe_work(work)
    dump(output/'receipts.json',{'schema':1,'base':base,'receipts':receipts})




def dispatch(number):
    api(route('/actions/workflows/candidate.yml/dispatches'),'POST',{'ref':'main','inputs':{'pr_number':str(number)}})


def owned_proposal(existing,name,watcher_id):
    oldhead=existing['head']['sha'];oldcommit=api(route('/git/commits/'+oldhead))
    oldbase=oldcommit['parents'][0]['sha'] if len(oldcommit['parents'])==1 else None
    expected=hashlib.sha256(sources.canonical({'base':oldbase,'tree':oldcommit['tree']['sha'],'watcher':watcher_id,'pkgbase':name})).hexdigest()
    receipts=api(route('/commits/'+oldhead+'/statuses'))
    owned=any(s['context']=='arch-updater-receipt' and s['description']==expected and s['creator']['login']=='github-actions[bot]' for s in receipts)
    if not owned or oldcommit['message']!=f'Update {name} via {watcher_id}\n\nArch-Update-Receipt: {expected}\nArch-Update-Base: {oldbase}':
        raise ValueError('user-edited proposal; refusing overwrite')
    return oldcommit,oldbase


def write_proposals(directory):
    directory=Path(directory);batch=load(directory/'receipts.json');base=main_sha()
    if batch.get('schema')!=1 or batch['base']!=base:
        raise ValueError('discovery base changed; rerun sweep')
    checkout,pins=control_checkout()
    policies=policy_at(ROOT)
    if checkout!=base:
        raise ValueError('writer control checkout is not current main')
    for receipt in batch['receipts']:
        name=receipt['pkgbase'];watcher_id=receipt['watcher_id']
        if name not in policies or not NAME.fullmatch(watcher_id) or receipt['base']!=base or receipt.get('recipe_commit')!=pins[name]:
            raise ValueError('unknown watcher receipt')
        advance_maintained_history(name,pins[name])
        provenance=load(ROOT/'upstream'/f'{name}.json')
        watcher=next(w for w in provenance['watchers'] if w['id']==watcher_id)
        branch='updates/'+name+'/'+watcher_id
        prs=api(route('/pulls?state=open&head='+repository().split('/')[0]+':'+branch))
        if len(prs)>1:
            raise ValueError('multiple bot branch PRs')
        existing=prs[0] if prs else None
        previous_commit,previous_base=owned_proposal(existing,name,watcher_id) if existing else (None,None)
        if receipt['unchanged']:
            if existing:
                # Refresh is handled by rediscovering accepted upstream against
                # current main, not by transplanting untrusted existing code.
                states=api(route('/commits/'+existing['head']['sha']+'/status'))
                latest={s['context']:s['state'] for s in states.get('statuses',[])}
                if any(latest.get(context) in {None,'failure','error'} for context in ('recipe-policy','candidate-build')) or existing['base']['sha']!=base:
                    dispatch(existing['number'])
            continue
        policy=policies[name];lock=receipt['lock'];validate_source_policy(lock,policy)
        work=directory/name/watcher_id;recipe=work/'recipe'
        if tree_manifest(recipe)!=receipt['tree'] or (recipe/'.SRCINFO').read_text()!=receipt['probe']['srcinfo'] or parse_srcinfo(receipt['probe']['srcinfo'])['version']!=lock['version']:
            raise ValueError('typed probe/tree receipt mismatch')
        # Re-render accepted code; source code returned by probes is never trusted.
        rendered=work/'trusted-render';recipes.copy_recipe(ROOT/'recipes'/name,rendered)
        if watcher['kind']=='aur':
            configured={**watcher,'work_dir':str(work/'writer-aur')}
            actual=sources.discover_aur(configured,provenance.get('aur') or {})
            if actual!=receipt['transition']:
                raise ValueError('AUR receipt identity changed')
            integration=apply_aur_transition(rendered,actual,policy,work/'writer-three-way')
            if integration['conflicts']!=receipt.get('aur_conflicts'):
                raise ValueError('AUR integration conflicts differ from typed receipt')
            if not integration['conflicts']:
                render_recipe(rendered,policy,receipt['probe']['pkgver'],{s['id']:s['checksums'] for s in lock['sources']},pkgrel=receipt['probe']['pkgrel'])
        else:
            render_recipe(rendered,policy,receipt['probe']['pkgver'],{s['id']:s['checksums'] for s in lock['sources']},pkgrel=receipt['probe']['pkgrel'])
        (rendered/'.SRCINFO').write_text(receipt['probe']['srcinfo'])
        if tree_manifest(rendered)!=receipt['tree']:
            raise ValueError('proposal differs from trusted typed rendering')
        verify_provenance(provenance,receipt['provenance'],lock,policy,watcher_id)
        oldlock=load(ROOT/'inputs'/f'{name}.json')
        allowed_ids={watcher.get('source_id'),*watcher.get('related_source_ids',[])}
        for source in lock['sources']:
            previous=next(s for s in oldlock['sources'] if s['id']==source['id'])
            if source!=previous and not next(s for s in policy['sources'] if s['id']==source['id'])['mutable'] and watcher['kind']!='aur':
                raise ValueError('watcher changed fixed auxiliary source')
            if source!=previous and source['id'] not in allowed_ids and watcher['kind']!='aur':
                raise ValueError('watcher changed unrelated source')
            if source['kind']=='git' and source!=previous:
                sources.materialize_sources({'schema':1,'version':lock['version'],'sources':[source]},work/('verify-'+source['id']))
            elif source['kind'] not in {'local','git'}:
                sources.freeze_source(source)
            elif source['kind']=='local':
                path=rendered/source['source']
                if not path.resolve().is_relative_to(rendered.resolve()):
                    raise ValueError('local source escapes recipe')
                for algorithm,value in source['checksums'].items():
                    if value!='SKIP' and hashlib.new(algorithm,path.read_bytes()).hexdigest()!=value:
                        raise ValueError('typed local source checksum mismatch')
        aur_parent=None
        if watcher['kind']=='aur':
            aur_parent=retain_aur_history(name,watcher,actual,provenance['aur'],work)
        blobs=[]
        for file in rendered.rglob('*'):
            if file.is_symlink():
                if not file.resolve(strict=False).is_relative_to(rendered.resolve()):
                    raise ValueError('rendered symlink escapes recipe')
                data=os.readlink(file).encode();mode='120000'
            elif file.is_file():
                data=file.read_bytes();mode='100755' if file.stat().st_mode&0o111 else '100644'
            elif file.is_dir():
                continue
            else:
                raise ValueError('unsupported rendered file')
            blob=api(route('/git/blobs'),'POST',{'content':base64.b64encode(data).decode(),'encoding':'base64'})
            blobs.append({'path':file.relative_to(rendered).as_posix(),'mode':mode,'type':'blob','sha':blob['sha']})
        recipe_tree=api(route('/git/trees'),'POST',{'tree':blobs})
        parents=[pins[name]]
        if aur_parent and aur_parent not in parents:
            parents.append(aur_parent)
        recipe_sha=None
        recipe_branch='recipe-updates/'+name+'/'+watcher_id
        recipe_tip=ref_head(recipe_branch)
        previous_recipe=pin_at(existing['head']['sha'],name) if existing else None
        reusable = previous_recipe if existing else recipe_tip
        if matches_commit(reusable, recipe_tree['sha'], parents, f'Update {name} via {watcher_id}'):
            recipe_sha = reusable
        if recipe_sha is None:
            recipe_commit=api(route('/git/commits'),'POST',{'message':f'Update {name} via {watcher_id}','tree':recipe_tree['sha'],'parents':parents,'author':{'name':'github-actions[bot]','email':'41898282+github-actions[bot]@users.noreply.github.com'}})
            recipe_sha=recipe_commit['sha']
        blobs=[{'path':'recipes/'+name,'mode':'160000','type':'commit','sha':recipe_sha}]
        for path,value in [('inputs/'+name+'.json',lock),('upstream/'+name+'.json',receipt['provenance'])]:
            blob=api(route('/git/blobs'),'POST',{'content':sources.canonical(value).decode(),'encoding':'utf-8'})
            blobs.append({'path':path,'mode':'100644','type':'blob','sha':blob['sha']})
        basecommit=api(route('/git/commits/'+base))
        tree=api(route('/git/trees'),'POST',{'base_tree':basecommit['tree']['sha'],'tree':blobs})
        receipt_digest=hashlib.sha256(sources.canonical({'base':base,'tree':tree['sha'],'watcher':watcher_id,'pkgbase':name})).hexdigest()
        message=f'Update {name} via {watcher_id}\n\nArch-Update-Receipt: {receipt_digest}\nArch-Update-Base: {base}'
        if existing:
            oldhead=existing['head']['sha'];oldcommit=previous_commit;oldbase=previous_base
            if oldcommit['tree']['sha']==tree['sha'] and oldbase==base:
                latest={s['context']:s['state'] for s in reversed(api(route('/commits/'+oldhead+'/statuses')))}
                if any(latest.get(context) in {None,'failure','error'} for context in ('recipe-policy','candidate-build')):
                    dispatch(existing['number'])
                continue
        if recipe_tip:
            if existing and recipe_tip!=previous_recipe or not existing and recipe_tip!=recipe_sha and not recipes.is_ancestor(repository(),recipe_tip,pins[name]):
                raise ValueError('user-edited recipe proposal ref; refusing overwrite')
        sources.git('fetch','https://github.com/'+repository()+'.git',recipe_sha,cwd=ROOT)
        push_ref(ROOT,recipe_sha,recipe_branch,recipe_tip)
        if main_sha()!=base:
            raise ValueError('main changed before proposal write')
        commit=api(route('/git/commits'),'POST',{'message':message,'tree':tree['sha'],'parents':[base],'author':{'name':'github-actions[bot]','email':'41898282+github-actions[bot]@users.noreply.github.com'}})
        body=f'Frozen source proposal from `{base}`. Receipt `{receipt_digest}`. Native candidate validation is required.\n\nComplete maintained recipe diff: https://github.com/{repository()}/compare/{pins[name]}...{recipe_sha}\n\nExact recipe commit/history: https://github.com/{repository()}/commit/{recipe_sha}\n\nAccepted recipe parent: `{pins[name]}`; proposed pin: `{recipe_sha}`.'
        if watcher['kind']=='aur':
            body+='\n\nAuthentic AUR history: https://github.com/'+repository()+'/commit/'+aur_parent+'\n\nUpstream recipe diff (local maintained code is preserved):\n```diff\n'+receipt['transition']['diff'][:50000]+'\n```'
        if receipt.get('aur_conflicts'):
            body+='\n\nHuman integration required; accepted local tree retained. Conflicting paths: '+', '.join(receipt['aur_conflicts'])
        if existing:
            # GitHub ref PATCH has no compare-and-swap; use real exact lease.
            sources.git('fetch','https://github.com/'+repository()+'.git',commit['sha'],cwd=ROOT)
            push_ref(ROOT,commit['sha'],branch,oldhead)
            number=existing['number']
        else:
            proposal_tip=ref_head(branch)
            if matches_commit(proposal_tip, tree['sha'], [base], message):
                commit = {'sha': proposal_tip}
            elif proposal_tip and not recipes.is_ancestor(repository(),proposal_tip,base):
                raise ValueError('closed proposal branch has unaccepted/manual work')
            sources.git('fetch','https://github.com/'+repository()+'.git',commit['sha'],cwd=ROOT)
            push_ref(ROOT,commit['sha'],branch,proposal_tip)
            result=api(route('/pulls'),'POST',{'head':branch,'base':'main','title':f'Update {name} ({watcher_id})','body':body})
            number=result['number']
        if existing:
            api(route('/pulls/'+str(number)),'PATCH',{'body':body})
            detail=f'Refreshed exact proposal at `{commit["sha"]}`.\n\nComplete maintained recipe diff: https://github.com/{repository()}/compare/{pins[name]}...{recipe_sha}\n\nRecipe history: https://github.com/{repository()}/commit/{recipe_sha}'
            if watcher['kind']=='aur':
                detail+='\n\n```diff\n'+receipt['transition']['diff'][:50000]+'\n```'
            if receipt.get('aur_conflicts'):
                detail+='\n\nHuman integration required; accepted local tree retained. Conflicting paths: '+', '.join(receipt['aur_conflicts'])
            api(route('/issues/'+str(number)+'/comments'),'POST',{'body':detail})
        api(route('/statuses/'+commit['sha']),'POST',{'state':'success','context':'arch-updater-receipt','description':receipt_digest})
        dispatch(number)


def cli():
    parser=argparse.ArgumentParser();sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('extract');p.add_argument('--archive',required=True);p.add_argument('--directory',required=True);p.add_argument('--source-receipts',action='store_true')
    p=sub.add_parser('pack');p.add_argument('--archive',required=True);p.add_argument('--directory',required=True)
    for command in ('discover','write','bootstrap'):
        p=sub.add_parser(command);p.add_argument('--directory',required=True)
    p=sub.add_parser('prepare');p.add_argument('--pr',required=True);p.add_argument('--directory',required=True)
    for command in ('validate-build','review','fail-build'):
        p=sub.add_parser(command);p.add_argument('--record',required=True)
        if command=='validate-build':p.add_argument('--directory',required=True)
    p=sub.add_parser('finalize');p.add_argument('--pr',required=True);p.add_argument('--base',required=True);p.add_argument('--head',required=True);p.add_argument('--record',required=True);p.add_argument('--directory',required=True)
    args=parser.parse_args()
    if args.command=='pack':
        with tarfile.open(args.archive,'w') as archive:
            archive.add(args.directory,arcname='source-data')
    elif args.command=='extract':
        allowed=set(policy_at(ROOT))|{'receipts.json','bootstrap.json'} if args.source_receipts else {'recipes','bundle.json'}
        extract_tree(args.archive,args.directory,allowed,args.source_receipts)
    elif args.command=='bootstrap':bootstrap(args.directory)
    elif args.command=='discover':discover(args.directory)
    elif args.command=='write':write_proposals(args.directory)
    elif args.command=='prepare':prepare(args.pr,args.directory)
    elif args.command=='validate-build':validate_build(args.record,args.directory)
    elif args.command=='fail-build':
        record=load(args.record)
        pr_identity(record['pr_number'],record['base'],record['head'])
        status(record['head'],'candidate-build','failure','Frozen native build or complete output validation failed')
    elif args.command=='review':review(args.record)
    else:finalize(args.pr,args.base,args.head,args.record,args.directory)


if __name__=='__main__':
    cli()
