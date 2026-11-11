"""The complete runner policy. Requests cannot supply Docker configuration."""
import json
import re

RUNNER_NETWORK='codex-development-lab_test_internal'


def identifier(value, length=14):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{%d}' % length, value):
        raise ValueError('Invalid runner identifier')
    return value


def validate_request(payload):
    if not isinstance(payload,dict):raise ValueError('Runner request must be an object')
    if ('capture_max_seconds' in payload and
            (type(payload['capture_max_seconds']) is not int or payload['capture_max_seconds'] < 1)):
        raise ValueError('Recording duration must be a positive integer in seconds')
    if payload.get('verification') is True:
        required={'project','job','verification','timeout_seconds','eula'}
        if not required <= payload.keys() or payload.keys() - required - {'capture_max_seconds'}:
            raise ValueError('Verification accepts only project, job, verification, timeout_seconds, eula and capture_max_seconds')
        identifier(payload['project']);identifier(payload['job'])
        if type(payload['timeout_seconds']) is not int or payload['timeout_seconds']<60:
            raise ValueError('Verification timeout must be an integer of at least 60 seconds')
        if type(payload['eula']) is not bool:raise ValueError('Verification consent must be boolean')
        return dict(payload)
    required = {'project', 'job', 'argv', 'record'}
    if not required <= payload.keys() or payload.keys() - required - {'ci', 'eula', 'capture_max_seconds'}:
        raise ValueError('Only project, job, argv, record, ci, eula and capture_max_seconds are accepted')
    identifier(payload['project'])
    identifier(payload['job'])
    argv = payload['argv']
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 256
            or any(not isinstance(arg, str) or '\0' in arg or len(arg) > 65536 for arg in argv)
            or not argv[0] or sum(map(len, argv)) > 131072):
        raise ValueError('Invalid command arguments')
    if (type(payload['record']) is not bool or payload.get('ci', 'true') not in ('true', 'false')
            or type(payload.get('eula', False)) is not bool):
        raise ValueError('Invalid recording/CI setting')
    return dict(payload)


def container_config(image, volume, token, request, owner):
    request = validate_request(request)
    identifier(token, 32)
    identifier(owner, 32)
    verification=request.get('verification') is True
    environment = {
        'HOME': '/tmp/lab', 'CI': 'false' if verification else request.get('ci', 'true'), 'PYTHONPATH': '',
        'GRADLE_USER_HOME': '/cache/gradle',
        'MAVEN_OPTS': '-Dmaven.repo.local=/cache/maven',
        'npm_config_cache': '/cache/npm',
        'JAVA_TOOL_OPTIONS': ('-XX:ActiveProcessorCount=4 -Dhttp.proxyHost=lab-egress -Dhttp.proxyPort=3128 '
                              '-Dhttps.proxyHost=lab-egress -Dhttps.proxyPort=3128 '
                              '-Djna.tmpdir=/cache/native -Dorg.lwjgl.system.SharedLibraryExtractPath=/cache/native '
                              '-Dhttp.nonProxyHosts=localhost|127.*|[::1]'),
        'GRADLE_OPTS': '-Dorg.gradle.workers.max=4 -Dorg.gradle.daemon=true',
        'LP_NUM_THREADS': '4', 'OMP_NUM_THREADS': '4',
        'MINECRAFT_EULA_ACCEPTED': str(request.get('eula', False)).lower(),
        'LAB_VIDEO_MAX_SECONDS': str(request.get('capture_max_seconds', 300)),
        'HTTP_PROXY':'http://lab-egress:3128','HTTPS_PROXY':'http://lab-egress:3128',
        'http_proxy':'http://lab-egress:3128','https_proxy':'http://lab-egress:3128',
        'NO_PROXY':'localhost,127.0.0.1,::1','no_proxy':'localhost,127.0.0.1,::1',
    }
    options=({'verification':True,'timeout_seconds':request['timeout_seconds']} if verification
             else {'argv':request['argv'],'record':request['record']})
    return {
        'Image': image, 'User': '0:0' if verification else '1000:1000', 'WorkingDir': '/workspace',
        'Entrypoint': ['python3', '-I', '/opt/lab/worker/sandbox_entry.py'],
        'Cmd': [json.dumps(options)],
        'Env': [f'{key}={value}' for key, value in environment.items()],
        'Tty': True, 'OpenStdin': False,
        'Labels': {'codex-lab.role': 'project-test', 'codex-lab.project': request['project'],
                   'codex-lab.job': request['job'], 'codex-lab.broker': owner, 'codex-lab.run': token},
        'HostConfig': {
            'Mounts': [{'Type': 'volume', 'Source': volume, 'Target': target,
                        'VolumeOptions': {'NoCopy': True, 'Subpath': f'runs/{token}/{folder}'}}
                       for folder, target in (('workspace', '/workspace'), ('output', '/output'))]
                      + ([{'Type':'volume','Source':volume,'Target':'/game-data',
                           'VolumeOptions':{'NoCopy':True,'Subpath':f'runs/{token}/game-data'}}]
                         if verification else [])
                      +[{'Type':'volume','Source':volume,'Target':'/cache',
                         'VolumeOptions':{'NoCopy':True,'Subpath':f"projects/{request['project']}/cache"}}]
                      +([{'Type':'volume','Source':volume,'Target':'/world-templates',
                          'VolumeOptions':{'NoCopy':True,'Subpath':'world-templates'}}]
                        if verification else []),
            'ReadonlyRootfs': True, 'CapDrop': ['ALL'],
            **({'CapAdd':['SETUID','SETGID']} if verification else {}),
            'SecurityOpt': ['no-new-privileges:true'], 'Init': True,
            'NetworkMode': RUNNER_NETWORK, 'PidsLimit': 768,
            'Memory': 6 * 1024 ** 3, 'NanoCpus': 4 * 10 ** 9,
            'ShmSize': 1024 ** 3, 'Tmpfs': {'/tmp': 'rw,noexec,nosuid,nodev,size=2g,mode=1777'},
            'LogConfig': {'Type': 'json-file', 'Config': {'max-size': '20m', 'max-file': '1'}},
        },
    }
