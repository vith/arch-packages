"""Uncredentialed discovery and immutable native source materialization."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import string
import subprocess
import urllib.request

MAXIMUM = 1073741824
FIELDS = {'id','kind','source','url','ref','commit','tag_object','peeled_commit','release_id','asset_id','checksums','git_context'}
HEX = re.compile(r'^(?:[0-9a-f]{40}|[0-9a-f]{64})$')


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)+'\n').encode()


def _format_version_fields(template, values):
    if not isinstance(template,str):
        raise ValueError('invalid source version template')
    for _,field,spec,conversion in string.Formatter().parse(template):
        if field is not None and (field not in values or spec or conversion):
            raise ValueError('unsupported source version template field')
    return template.format_map(values)


def format_source_template(enrolled, template, version):
    """Retain native pkgver while projecting an enrolled artifact directory."""
    artifact=version
    if 'version_projection' in enrolled:
        projection=enrolled['version_projection']
        if not isinstance(projection,dict) or set(projection)!={'pattern','template'}:
            raise ValueError('invalid source version projection')
        pattern=projection['pattern']
        if not isinstance(pattern,str) or not 1<=len(pattern)<=1024:
            raise ValueError('invalid source version projection pattern')
        if not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9.+_]{1,128}',version):
            raise ValueError('invalid source projection version')
        try:
            grammar=re.compile(pattern)
        except re.error as error:
            raise ValueError('invalid source version projection pattern') from error
        if not grammar.groupindex or grammar.groups!=len(grammar.groupindex):
            raise ValueError('source projection requires named captures only')
        matched=grammar.fullmatch(version)
        if matched is None or any(value is None for value in matched.groupdict().values()):
            raise ValueError('source version projection mismatch')
        projection_template=projection['template']
        if not isinstance(projection_template,str) or not 1<=len(projection_template)<=256:
            raise ValueError('invalid source version projection template')
        artifact=_format_version_fields(projection_template,matched.groupdict())
        if not re.fullmatch(r'[A-Za-z0-9.+_-]{1,128}',artifact):
            raise ValueError('invalid projected artifact version')
    return _format_version_fields(template,{'version':version,'artifact_version':artifact})


def public_url(url):
    from urllib.parse import urlsplit
    p = urlsplit(url)
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.fragment:
        raise ValueError('source URL must be public HTTPS without credentials or fragment')
    if p.hostname=='localhost' or p.hostname.endswith('.localhost'):
        raise ValueError('unsupported runtime origin')
    return url


def fetch(url, destination=None):
    public_url(url)
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent':'arch-packages-source'}), timeout=120) as response:
        public_url(response.url)
        data=response.read(MAXIMUM+1)
    if len(data)>MAXIMUM:
        raise ValueError('source exceeds bounded download')
    if destination is not None:
        Path(destination).write_bytes(data)
    return data


def git(*args, cwd=None, check=True):
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','TMPDIR'}}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0', GIT_ALLOW_PROTOCOL='https:file')
    safe=['-c','safe.directory='+str(Path(cwd).resolve())] if cwd is not None else []
    result=subprocess.run(['git',*safe,'-c','core.hooksPath=/dev/null','-c','credential.helper=','-c','fetch.recurseSubmodules=false','-c','protocol.file.allow=always',*map(str,args)],cwd=cwd,env=env,check=check,capture_output=True,text=True)
    return result.stdout.strip() if check else result


def refs(repository):
    rows=[]
    for line in git('for-each-ref','--format=%(refname) %(objectname) %(*objectname)',cwd=repository).splitlines():
        fields=line.split()
        rows.append({'name':fields[0],'object':fields[1],'peeled':fields[2] if len(fields)>2 else None})
    return sorted(rows,key=lambda r:r['name'])


def clone(url, directory, reference):
    public_url(url)
    if Path(directory).exists():
        raise ValueError('source clone destination exists')
    advertised=git('ls-remote','--heads','--tags',url).splitlines()
    lengths={len(row.split()[0]) for row in advertised}
    if lengths not in ({40},{64}):
        raise ValueError('invalid or empty remote object format')
    git('init','--bare','--object-format='+('sha256' if lengths=={64} else 'sha1'),directory)
    git('fetch','--no-recurse-submodules','--',url,reference+':'+reference,cwd=directory)
    return Path(directory)


def _work(watcher):
    path=Path(watcher['work_dir'])
    path.mkdir(parents=True,exist_ok=True)
    return path


def discover_aur(watcher, accepted):
    package=watcher['package']
    if not re.fullmatch(r'[a-z0-9][a-z0-9+_.-]*',package):
        raise ValueError('invalid AUR package')
    repository=_work(watcher)/'aur.git'
    if repository.exists():
        raise ValueError('AUR checkout destination exists')
    git('init','--bare',repository)
    ref=watcher.get('ref','refs/heads/master')
    if not ref.startswith('refs/heads/'):
        raise ValueError('AUR watcher must enroll a branch')
    git('fetch','--no-recurse-submodules','--',watcher.get('url','https://aur.archlinux.org/'+package+'.git'),'+'+ref+':'+ref,cwd=repository)
    commit=git('rev-parse',ref+'^{commit}',cwd=repository)
    old=accepted.get('commit')
    if old:
        git('cat-file','-e',old+'^{commit}',cwd=repository)
    if old==commit:
        return None
    files=aur_files(repository,commit)
    previous_files=aur_files(repository,old) if old else []
    difference=git('diff','--no-ext-diff','--no-textconv',old,commit,cwd=repository) if old else 'Initial AUR import'
    if len(difference.encode())>1048576:
        raise ValueError('AUR diff exceeds bound')
    return {'kind':'aur','commit':commit,'previous':old,'previous_files':previous_files,'files':files,'diff':difference,'fast_forward':not old or git('merge-base','--is-ancestor',old,commit,cwd=repository,check=False).returncode==0}


def frozen_aur(watcher, accepted, current):
    """Reconstruct enrolled AUR commits, not the branch's present tip."""
    package=watcher['package']
    if not re.fullmatch(r'[a-z0-9][a-z0-9+_.-]*',package):
        raise ValueError('invalid AUR package')
    old=accepted.get('commit');commit=current.get('commit')
    if not HEX.fullmatch(commit or '') or old is not None and not HEX.fullmatch(old):
        raise ValueError('invalid frozen AUR commit')
    url=watcher.get('url','https://aur.archlinux.org/'+package+'.git')
    public_url(url)
    repository=_work(watcher)/'aur.git'
    if repository.exists():
        raise ValueError('AUR checkout destination exists')
    git('init','--bare','--object-format='+('sha256' if len(commit)==64 else 'sha1'),repository)
    git('fetch','--no-tags','--no-recurse-submodules','--',url,*sorted({commit,old} if old else {commit}),cwd=repository)
    for identity in (old,commit):
        if identity and git('rev-parse',identity+'^{commit}',cwd=repository)!=identity:
            raise ValueError('frozen AUR object is not a commit')
    return {'kind':'aur','commit':commit,'previous':old,
            'previous_files':aur_files(repository,old) if old else [],
            'files':aur_files(repository,commit),
            'fast_forward':not old or git('merge-base','--is-ancestor',old,commit,cwd=repository,check=False).returncode==0}


def frozen_tag(watcher, current, work):
    """Verify the exact tag object/peel authenticated by the original context."""
    obj=current.get('accepted_tag_object');commit=current.get('accepted_peeled_commit')
    if not HEX.fullmatch(obj or '') or not HEX.fullmatch(commit or ''):
        raise ValueError('invalid frozen tag identity')
    public_url(watcher['url'])
    repository=Path(work)
    git('init','--bare','--object-format='+('sha256' if len(obj)==64 else 'sha1'),repository)
    git('fetch','--no-tags','--no-recurse-submodules','--',watcher['url'],obj,commit,cwd=repository)
    if git('rev-parse',obj+'^{commit}',cwd=repository)!=commit:
        raise ValueError('frozen tag provenance mismatch')
    if git('cat-file','-t',obj,cwd=repository)=='tag':
        lines=git('cat-file','-p',obj,cwd=repository).split('\n\n',1)[0].splitlines()
        if 'tag '+current['accepted_tag'] not in lines:
            raise ValueError('frozen tag name mismatch')


def aur_files(repository,commit):
    files=[]
    for row in git('ls-tree','-r','-z',commit,cwd=repository).split('\0'):
        if not row:
            continue
        descriptor,path=row.split('\t',1)
        mode,kind,obj=descriptor.split()
        if kind!='blob' or mode not in {'100644','100755','120000'}:
            raise ValueError('AUR tree contains unsupported file type')
        if path.startswith('/') or '..' in Path(path).parts:
            raise ValueError('unsafe AUR tree path')
        data=subprocess.run(['git','--git-dir',str(repository),'cat-file','blob',obj],check=True,capture_output=True).stdout
        if len(data)>1048576:
            raise ValueError('AUR recipe file too large')
        files.append({'path':path,'mode':mode,'content':data.decode('utf-8')})
    return files


def _version_prefix(watcher):
    prefix=watcher.get('version_prefix','')
    if not isinstance(prefix,str) or prefix and not re.fullmatch(r'[A-Za-z]{1,16}',prefix):
        raise ValueError('invalid watcher version prefix')
    return prefix


def release_asset_name(watcher, version, source_id=None):
    """Select an enrolled release asset without sharing primary asset identity."""
    if 'asset_templates' in watcher:
        templates=watcher['asset_templates']
        source_id=watcher.get('source_id') if source_id is None else source_id
        if not isinstance(templates,dict) or not templates or source_id not in templates:
            raise ValueError('release source lacks enrolled asset template')
        template=templates[source_id]
        if not isinstance(template,str) or not 1<=len(template)<=512:
            raise ValueError('invalid release asset template')
        name=_format_version_fields(template,{'version':version})
    else:
        name=watcher.get('asset')
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+-]{0,254}',name):
        raise ValueError('invalid release asset filename')
    return name


def discover_release_tag(watcher, accepted):
    prefix=_version_prefix(watcher)
    repository=watcher['repository']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):
        raise ValueError('invalid release repository')
    pattern=re.compile(watcher.get('tag_pattern',r'^v(\d+)\.(\d+)\.(\d+)$'))
    if accepted.get('tag'):
        previous=git('ls-remote','--tags','https://github.com/'+repository+'.git','refs/tags/'+accepted['tag']).splitlines()
        if len(previous)!=1 or accepted.get('tag_object') and previous[0].split()[0]!=accepted['tag_object']:
            raise ValueError('accepted tag retargeted or missing')
    if watcher.get('kind')=='release':
        releases=json.loads(fetch('https://api.github.com/repos/'+repository+'/releases?per_page=100'))
        choices=[r for r in releases if not r['draft'] and not r['prerelease'] and pattern.fullmatch(r['tag_name'])]
        tag_key='tag_name'
    else:
        rows=git('ls-remote','--tags','--refs','https://github.com/'+repository+'.git').splitlines()
        choices=[{'tag_name':r.split()[1].removeprefix('refs/tags/'),'object':r.split()[0]} for r in rows if pattern.fullmatch(r.split()[1].removeprefix('refs/tags/'))]
        tag_key='tag_name'
    if not choices:
        raise ValueError('no authentic stable release/tag found')
    def order(row):
        groups=pattern.fullmatch(row[tag_key]).groups()
        components = groups[0].split('.') if len(groups)==1 else groups
        if not components or any(not g.isdecimal() for g in components):
            raise ValueError('tag grammar must provide numeric version groups')
        return tuple(map(int,components))
    selected=max(choices,key=order)
    version=prefix+'.'.join(map(str,order(selected)))
    if watcher.get('kind')=='release':
        release_asset_name(watcher,version)
        ids=watcher['asset_templates'] if 'asset_templates' in watcher else (None,)
        names=set()
        for source_id in ids:
            name=release_asset_name(watcher,version,source_id)
            if name in names:
                raise ValueError('release sources share an enrolled asset')
            names.add(name)
            if len([a for a in selected['assets'] if a['name']==name])!=1:
                raise ValueError('enrolled release asset missing or duplicated')
    if selected['tag_name']==accepted.get('tag'):
        if selected.get('id')!=accepted.get('release_id') and watcher.get('kind')=='release':
            raise ValueError('release identity changed')
        if watcher.get('kind')=='release':
            matches=[a for a in selected['assets'] if a['name']==release_asset_name(watcher,version)]
            if len(matches)!=1 or accepted.get('asset_id') and matches[0]['id']!=accepted['asset_id']:
                raise ValueError('accepted release asset replaced or missing')
        # Verify tag object even when version did not advance.
        rows=git('ls-remote','--tags','https://github.com/'+repository+'.git','refs/tags/'+selected['tag_name']).splitlines()
        if len(rows)!=1 or accepted.get('tag_object') and rows[0].split()[0]!=accepted['tag_object']:
            raise ValueError('accepted tag retargeted or missing')
        return None
    return {'kind':watcher['kind'],'tag':selected['tag_name'],'version':version,'release_id':selected.get('id'),'assets':selected.get('assets',[]),'previous':accepted.get('tag')}


def discover_git(watcher, accepted):
    repository=clone(watcher['url'],_work(watcher)/'watched.git',watcher['ref'])
    ref=watcher['ref']
    if not ref.startswith('refs/heads/'):
        raise ValueError('watched ref must be full branch ref')
    commit=git('rev-parse','--verify',ref+'^{commit}',cwd=repository)
    context=version_context(repository,commit)
    old=accepted.get('commit')
    if old and git('cat-file','-e',old+'^{commit}',cwd=repository,check=False).returncode:
        git('fetch','--no-tags','--no-recurse-submodules','--',watcher['url'],old,cwd=repository)
    previous_tag=accepted['git_context']['version_tag']
    advertised=dict(reversed(row.split()) for row in git('ls-remote','--tags',watcher['url']).splitlines())
    authentic=previous_tag is None or advertised.get(previous_tag['name'])==previous_tag['object']
    if not authentic:
        raise ValueError('accepted authentic tag changed or disappeared')
    if old==commit and accepted['git_context']==context:
        return None
    return {'kind':'git','commit':commit,'ref':ref,'git_context':context,'authentic':authentic,'fast_forward':not old or git('merge-base','--is-ancestor',old,commit,cwd=repository,check=False).returncode==0}


def version_context(repository,commit):
    described=git('describe','--tags','--match','v[0-9]*','--abbrev=0',commit,cwd=repository,check=False)
    tag=None
    if described.returncode==0:
        reference='refs/tags/'+described.stdout.strip()
        tag={'name':reference,'object':git('rev-parse',reference,cwd=repository),'commit':git('rev-parse',reference+'^{commit}',cwd=repository)}
    return {'object_format':git('rev-parse','--show-object-format',cwd=repository),'version_tag':tag}


def pinned_refs(source):
    expected={source['ref']:source['tag_object'] or source['commit']}
    tag=source['git_context']['version_tag']
    if tag:
        if tag['name'] in expected and expected[tag['name']]!=tag['object']:
            raise ValueError('conflicting pinned tag')
        expected[tag['name']]=tag['object']
    return expected


def verify_git_source(source,repository):
    actual={row['name']:row['object'] for row in refs(repository)}
    if actual!=pinned_refs(source) or git('rev-parse',source['ref']+'^{commit}',cwd=repository)!=source['commit']:
        raise ValueError('frozen Git commit/ref mismatch')
    if git('rev-parse','--show-object-format',cwd=repository)!=source['git_context']['object_format']:
        raise ValueError('frozen Git object format mismatch')
    if source['ref'].startswith('refs/tags/') and source['peeled_commit']!=source['commit']:
        raise ValueError('frozen Git peeled commit mismatch')
    tag=source['git_context']['version_tag']
    if tag:
        if git('rev-parse',tag['name']+'^{commit}',cwd=repository)!=tag['commit']:
            raise ValueError('frozen version tag mismatch')
        git('merge-base','--is-ancestor',tag['commit'],source['commit'],cwd=repository)


def ancestry_version(source, repository, template):
    """Authenticate the live tag before rendering the frozen ancestry grammar."""
    tag=source['git_context']['version_tag']
    if tag is None:
        raise ValueError('static ancestry version requires a frozen version tag')
    advertised=dict(reversed(row.split()) for row in git('ls-remote','--tags',source['url']).splitlines())
    if advertised.get(tag['name'])!=tag['object']:
        raise ValueError('static ancestry version requires an authentic tag')
    return frozen_ancestry_version(source,repository,template)


def frozen_ancestry_version(source, repository, template):
    """Render only verified immutable objects; authentication belongs to context."""
    verify_git_source(source, repository)
    tag=source['git_context']['version_tag']
    if tag is None:
        raise ValueError('static ancestry version requires a frozen version tag')
    version=tag['name'].removeprefix('refs/tags/v')
    if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+',version):
        raise ValueError('unsupported frozen version tag')
    count=git('rev-list','--count',tag['commit']+'..'+source['commit'],cwd=repository)
    # Recognize only the already enrolled scalar captures and literal separators.
    captures=[r'([0-9]+(?:\.[0-9]+)+)',r'([0-9]+)',r'([0-9a-f]{12})']
    rendered=template.removeprefix('^').removesuffix('$')
    for capture,value in zip(captures,(version,count,source['commit'][:12])):
        if rendered.count(capture)!=1:
            raise ValueError('unsupported static ancestry template')
        rendered=rendered.replace(capture,value,1)
    rendered=rendered.replace(r'\.','.')
    if not re.fullmatch(r'[A-Za-z0-9.+_]+',rendered) or not re.fullmatch(template,rendered):
        raise ValueError('unsupported static ancestry template')
    return rendered


def _frozen_git_version(source, repository, rules):
    """Derive enrolled Git versions from verified immutable source objects."""
    if rules['derivation']=='frozen-authentic-tag-ancestry':
        return frozen_ancestry_version(source,repository,rules['template'])
    if rules['derivation']!='frozen-git-revision-count':
        raise ValueError('unsupported frozen Git version derivation')
    verify_git_source(source,repository)
    if not source['ref'].startswith('refs/heads/'):
        raise ValueError('revision count version requires a frozen branch')
    if git('rev-parse','--is-shallow-repository',cwd=repository)!='false':
        raise ValueError('revision count version requires full Git history')
    if rules['template']!=r'^r[0-9]+\.[0-9a-f]{7,40}$':
        raise ValueError('unsupported revision count template')
    count=git('rev-list','--count',source['commit'],cwd=repository)
    abbreviation=git('rev-parse','--short=7',source['commit'],cwd=repository)
    version='r'+count+'.'+abbreviation
    if not re.fullmatch(rules['template'],version):
        raise ValueError('unsupported revision count version')
    return version


def git_checksums(source, repository):
    """Match makepkg's tag/commit Git archive checksum without sourcing PKGBUILD."""
    verify_git_source(source, repository)
    if not source['ref'].startswith('refs/tags/'):
        return {algorithm:'SKIP' for algorithm in source['checksums']}
    # makepkg disables export attributes before creating the native archive.
    attributes=Path(repository)/'info'/'attributes'
    if attributes.read_text()!='* -export-subst -export-ignore\n':
        raise ValueError('frozen Git archive attributes are not sanitized')
    env={k:v for k,v in os.environ.items() if k in {'PATH','HOME','LANG','LC_ALL','TMPDIR'}}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL='/dev/null',GIT_TERMINAL_PROMPT='0',GIT_ALLOW_PROTOCOL='https:file')
    hashes={algorithm:hashlib.new(algorithm) for algorithm,value in source['checksums'].items() if value!='SKIP'}
    with subprocess.Popen(['git','-c','safe.directory='+str(Path(repository).resolve()),'-c','core.hooksPath=/dev/null','-c','core.abbrev=no','archive','--format','tar',source['ref']],cwd=repository,env=env,stdout=subprocess.PIPE) as process:
        total=0
        while chunk:=process.stdout.read(1048576):
            total+=len(chunk)
            if total>MAXIMUM:
                process.kill()
                raise ValueError('Git archive exceeds bound')
            for digest in hashes.values():
                digest.update(chunk)
        if process.wait()!=0:
            raise ValueError('static Git archive failed')
    return {algorithm:hashes[algorithm].hexdigest() if algorithm in hashes else 'SKIP' for algorithm in source['checksums']}


def freeze_source(spec):
    record={key:spec.get(key) for key in FIELDS}
    record['checksums']=dict(spec.get('checksums',{}))
    if record['kind']=='local':
        return record
    public_url(record['url'])
    if record['kind']!='git':
        data=fetch(record['url'])
        for algorithm,expected in record['checksums'].items():
            if expected=='SKIP':
                continue
            if hashlib.new(algorithm,data).hexdigest()!=expected:
                raise ValueError('source checksum mismatch')
        return record
    directory=Path(spec['work_dir']); directory.mkdir(parents=True,exist_ok=True)
    repository=clone(record['url'],directory/'freeze.git',record['ref'])
    target=git('rev-parse','--verify',record['ref']+'^{commit}',cwd=repository)
    if record['commit'] and target!=record['commit']:
        raise ValueError('moving source changed during freeze')
    record['commit']=target
    if record['ref'].startswith('refs/tags/'):
        record['tag_object']=git('rev-parse',record['ref'],cwd=repository)
        record['peeled_commit']=target
        if spec.get('tag_object') and spec['tag_object']!=record['tag_object']:
            raise ValueError('tag changed during freeze')
    record['git_context']=version_context(repository,target)
    expected=pinned_refs(record)
    for row in refs(repository):
        if row['name'] not in expected:
            git('update-ref','-d',row['name'],cwd=repository)
    verify_git_source(record,repository)
    (repository/'info'/'attributes').write_text('* -export-subst -export-ignore\n')
    return record


def validate_lock(lock):
    if set(lock)!= {'schema','version','sources'} or lock['schema']!=1 or not isinstance(lock['version'],str) or not lock['version']:
        raise ValueError('invalid source lock')
    ids=set()
    for source in lock['sources']:
        if set(source)!=FIELDS or source['id'] in ids or source['kind'] not in {'local','archive','release','git'}:
            raise ValueError('invalid/duplicate source record')
        ids.add(source['id'])
        if not isinstance(source['checksums'],dict) or not isinstance(source['source'],str):
            raise ValueError('invalid native source/checksums')
        if source['kind']!='local':
            public_url(source['url'])
        if source['kind']=='git':
            context=source['git_context']
            if not isinstance(context,dict) or set(context)!={'object_format','version_tag'} or context['object_format'] not in {'sha1','sha256'}:
                raise ValueError('invalid Git context')
            if not isinstance(source['ref'],str) or not source['ref'].startswith(('refs/heads/','refs/tags/')):
                raise ValueError('invalid pinned Git ref')
            git('check-ref-format',source['ref'])
            tag=context['version_tag']
            if tag is not None:
                if not isinstance(tag,dict) or set(tag)!={'name','object','commit'} or not tag['name'].startswith('refs/tags/') or not HEX.fullmatch(tag['object']) or not HEX.fullmatch(tag['commit']):
                    raise ValueError('invalid pinned version tag')
                git('check-ref-format',tag['name'])
            if not HEX.fullmatch(source['commit'] or ''):
                raise ValueError('invalid frozen commit')
    return lock


def materialize_sources(lock, destination):
    validate_lock(lock)
    destination=Path(destination); destination.mkdir(parents=True,exist_ok=True)
    mapping={}
    for source in lock['sources']:
        if source['kind']!='git':
            continue
        mirror=destination/(source['id']+'.git')
        git('init','--bare','--object-format='+source['git_context']['object_format'],mirror)
        objects={source['commit'],*pinned_refs(source).values()}
        git('fetch','--no-tags','--no-recurse-submodules','--',source['url'],*sorted(objects),cwd=mirror)
        for reference,object_id in pinned_refs(source).items():
            git('update-ref',reference,object_id,cwd=mirror)
        verify_git_source(source,mirror)
        (mirror/'info'/'attributes').write_text('* -export-subst -export-ignore\n')
        for path in mirror.rglob('*'):
            if path.is_symlink():
                raise ValueError('unexpected mirror symlink')
            path.chmod(0o555 if path.is_dir() else 0o444)
        mirror.chmod(0o555)
        if source['url'] in mapping and mapping[source['url']]!=mirror:
            raise ValueError('conflicting frozen contexts for source URL')
        mapping[source['url']]=mirror
    return mapping


def probe_recipe(recipe_dir, lock, policy, preserve_pkgrel=False):
    """Execute only inside the enrolled Arch container as an unprivileged user."""
    if not Path('/etc/arch-release').is_file() or os.geteuid()==0:
        raise RuntimeError('probe_recipe requires isolated unprivileged Arch runtime')
    from tools.recipe_gate import parse_srcinfo
    original_pkgrel=parse_srcinfo((Path(recipe_dir)/'.SRCINFO').read_text())['pkgrel']
    validate_lock(lock)
    configuration=os.environ.get('GIT_CONFIG_SYSTEM')
    if any(source['kind']=='git' for source in lock['sources']):
        if (os.environ.get('MAKEPKG_GIT_CONFIG')!=configuration
                or os.environ.get('GIT_CONFIG_NOSYSTEM','0')!='0'
                or os.environ.get('GIT_CONFIG_GLOBAL')!='/dev/null'):
            raise ValueError('native Git and makepkg require the same frozen system configuration')
        if not configuration or not Path(configuration).is_file():
            raise ValueError('native probe requires root-owned frozen mirror configuration')
        info=Path(configuration).stat()
        if info.st_uid!=0 or info.st_mode&0o222 and not os.statvfs(configuration).f_flag&os.ST_RDONLY:
            raise ValueError('native frozen configuration is writable by recipe')
        entries=git('config','--file',configuration,'--get-regexp',r'^url\..*\.insteadof$',check=False)
        mapping={}
        for row in entries.stdout.splitlines():
            key,url=row.split(None,1)
            target=key.removeprefix('url.').removesuffix('.insteadof')
            if not target.startswith('file:///'):
                raise ValueError('native Git source mapping is not a frozen local mirror')
            mirror=Path(target.removeprefix('file://'))
            readonly=not mirror.stat().st_mode&0o222 or os.statvfs(mirror).f_flag&os.ST_RDONLY
            if mirror.stat().st_uid!=0 or not readonly:
                raise ValueError('native Git mirror is not root-owned readonly')
            mapping[url]=mirror
        for source in lock['sources']:
            if source['kind']=='git':
                mirror=mapping.get(source['url'])
                if mirror is None:
                    raise ValueError('native mirror context differs from frozen lock')
                verify_git_source(source,mirror)
    vcs_rules=[r for r in policy['automatic']['checksums'] if next(s for s in lock['sources'] if s['id']==r['source_id'])['kind']=='git']
    if vcs_rules:
        import shlex
        generated=subprocess.run(['makepkg','--geninteg'],cwd=recipe_dir,check=True,capture_output=True,text=True).stdout
        if len(generated.encode())>1048576:
            raise ValueError('native checksum output exceeds bound')
        text=(Path(recipe_dir)/'PKGBUILD').read_text()
        for rule in vcs_rules:
            rows=re.findall(r'^'+re.escape(rule['algorithm']+'sums')+r'=\(([^)]*)\)',generated,re.M|re.S)
            if len(rows)!=1:
                raise ValueError('ambiguous native VCS checksum output')
            values=shlex.split(rows[0])
            value=values[rule['index']]
            expected_length=hashlib.new(rule['algorithm']).digest_size*2
            if not re.fullmatch('[0-9a-f]{'+str(expected_length)+'}',value):
                raise ValueError('invalid native generated VCS checksum')
            source=next(s for s in lock['sources'] if s['id']==rule['source_id'])
            old=source['checksums'][rule['algorithm']]
            if old!=value:
                if text.count(old)!=1:
                    raise ValueError('ambiguous enrolled VCS checksum literal')
                text=text.replace(old,value)
                source['checksums'][rule['algorithm']]=value
        (Path(recipe_dir)/'PKGBUILD').write_text(text)
    subprocess.run(['makepkg','--nobuild','--noconfirm'],cwd=recipe_dir,check=True,stdout=subprocess.PIPE)
    text=subprocess.run(['makepkg','--printsrcinfo'],cwd=recipe_dir,check=True,capture_output=True,text=True).stdout
    if len(text.encode())>1048576:
        raise ValueError('native metadata exceeds bound')
    metadata=parse_srcinfo(text)
    if preserve_pkgrel and metadata['pkgrel']!=original_pkgrel:
        from tools.update import render_recipe
        render_recipe(Path(recipe_dir),policy,metadata['pkgver'],{source['id']:source['checksums'] for source in lock['sources']},pkgrel=original_pkgrel)
        text=subprocess.run(['makepkg','--printsrcinfo'],cwd=recipe_dir,check=True,capture_output=True,text=True).stdout
        if len(text.encode())>1048576:
            raise ValueError('native metadata exceeds bound')
        metadata=parse_srcinfo(text)
        if metadata['pkgrel']!=original_pkgrel:
            raise ValueError('native enrollment did not preserve pkgrel')
    runtime_identity=None
    if policy['pkgbase']=='oh-my-pi-vith-git':
        match=re.fullmatch(r'([0-9]+(?:\.[0-9]+){2})\.vith\.r([0-9]+)\.g([0-9a-f]{12})',metadata['pkgver'])
        if not match:
            raise ValueError('unexpected native fork identity')
        source=next(s for s in lock['sources'] if s['id']=='omp-git')
        if source['commit'][:12]!=match[3]:
            raise ValueError('native fork SHA differs from frozen commit')
        runtime_identity=f'{match[1]}+vith-fork.{match[2]}.{match[3]}'
    return {'schema':1,'version':metadata['version'],'pkgver':metadata['pkgver'],'pkgrel':metadata['pkgrel'],'srcinfo':text,'checksums':{s['id']:s['checksums'] for s in lock['sources']},'sources':lock['sources'],'runtime_identity':runtime_identity}
