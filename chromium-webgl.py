import argparse
import datetime
import inspect
import multiprocessing
import os
import re
import subprocess


boto_file = '.boto'
cpu_count = multiprocessing.cpu_count()
build_dir = ''
chromium_dir = ''
chromium_src_dir = ''
depot_tools_dir = ''
script_dir = ''


def parse_arg():
    global args, args_dict
    parser = argparse.ArgumentParser(description='Chromium WebGL',
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog='''
examples:
  python %(prog)s --proxy <host>:<port> --sync --build
  python %(prog)s --test
''')
    parser.add_argument('--proxy', dest='proxy', help='proxy')
    parser.add_argument('--sync', dest='sync', help='sync', action='store_true')
    parser.add_argument('--build', dest='build', help='build', action='store_true')
    parser.add_argument('--test', dest='test', help='test', action='store_true')
    parser.add_argument('--test-revision', dest='test_revision', help='Chromium revision')
    parser.add_argument('--test-version', dest='test_version', help='WebGL CTS version to test against', default='1.0.3')
    parser.add_argument('--test-filter', dest='test_filter', help='WebGL CTS suite to test against', default='conformance_attribs')
    args = parser.parse_args()


def setup():
    global build_dir, chromium_dir, chromium_src_dir, depot_tools_dir, script_dir

    root_dir = os.path.dirname(os.path.split(os.path.realpath(__file__))[0]).replace('\\', '/')
    build_dir = root_dir + '/build'
    chromium_dir = root_dir + '/chromium'
    chromium_src_dir = chromium_dir + '/src'
    depot_tools_dir = root_dir + '/depot_tools'
    script_dir = root_dir + '/script'

    _setenv('DEPOT_TOOLS_WIN_TOOLCHAIN', 0)

    if args.proxy:
        _setenv('http_proxy', args.proxy)
        _setenv('https_proxy', args.proxy)

        _chdir(script_dir)
        _ensure_nofile(boto_file)
        proxy_parts = args.proxy.split(':')
        f = open(boto_file, 'w')
        content = '[Boto]\nproxy=%s\nproxy_port=%s\nproxy_rdns=True' % (proxy_parts[0], proxy_parts[1])
        f.write(content)
        f.close()
        _setenv('NO_AUTH_BOTO_CONFIG', script_dir + '/' + boto_file)


def sync():
    if not args.sync:
        return

    _chdir(depot_tools_dir)
    _exec('git pull')
    _chdir(chromium_src_dir)
    _exec('git pull')
    _exec('gclient sync -R -j%s' % cpu_count)


def build():
    if not args.build:
        return

    _chdir(chromium_src_dir)

    # build Chromium
    gn_args = 'proprietary_codecs=true ffmpeg_branding=\\\"Chrome\\\" is_official_build=true is_debug=false'
    gn_args += ' symbol_level=0 is_component_build=false use_jumbo_build=true remove_webcore_debug_symbols=true enable_nacl=false'
    cmd = 'gn --args=\"%s\" gen out/Default' % gn_args
    result = _exec(cmd)
    if result[0]:
        _error('Failed to execute gn command')
    result = _exec('ninja -j%s -C out/Default chrome' % cpu_count)
    if result[0]:
        _error('Failed to build Chromium')

    # get revision
    cmd = 'git log --shortstat -1 origin/master'
    result = _exec(cmd, show_cmd=False, return_out=True)
    lines = result[1].split('\n')
    for line in lines:
        match = re.search('Cr-Commit-Position: refs/heads/master@{#(.*)}', line)
        if match:
            rev = int(match.group(1))
            break
    else:
        _error('Failed to find the revision of Chromium')

    # generate telemetry_gpu_integration_test
    cmd = 'python tools/mb/mb.py zip out/Default/ telemetry_gpu_integration_test %s/%s.zip' % (build_dir, rev)
    result = _exec(cmd)
    if result[0]:
        _error('Failed to generate telemetry_gpu_integration_test')


def test():
    if not args.test:
        return

    _chdir(build_dir)
    rev = args.test_revision
    if not os.path.exists('%s' % rev):
        if not os.path.exists('%s.zip' % rev):
            _error('Could not find Chromium revision %s' % rev)
        _ensure_dir(rev)
        _exec('unzip %s.zip -d %s' % (rev, rev))

    _chdir(build_dir + '/' + rev)
    cmd = 'vpython content/test/gpu/run_gpu_integration_test.py webgl_conformance --browser=exact --browser-executable=%s/out/Default/chrome.exe' % (build_dir + '/' + rev)
    cmd += ' --webgl-conformance-version=%s' % args.test_version
    if args.test_filter != 'all':
        cmd += ' --test-filter=%s' % args.test_filter

    result = _exec(cmd)
    if result[0]:
        _error('Failed to test CTS')


def _chdir(dir):
    _info('Enter ' + dir)
    os.chdir(dir)


def _ensure_dir(dir):
    if os.path.exists(dir):
        return

    os.mkdir(dir)


def _ensure_nofile(file):
    if not os.path.exists(file):
        return

    os.remove(file)


def _exec(cmd, return_out=False, show_cmd=True, show_duration=False):
    if show_cmd:
        _cmd(cmd)

    if show_duration:
        start_time = datetime.datetime.now().replace(microsecond=0)

    if return_out:
        tmp_out = ''
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        (out, err) = process.communicate()
        out = tmp_out + out
        ret = process.returncode
        result = [ret, out + err]
    else:
        ret = os.system(cmd)
        result = [ret / 256, '']

    if show_duration:
        end_time = datetime.datetime.now().replace(microsecond=0)
        time_diff = end_time - start_time
        _info(str(time_diff) + ' was spent to execute command: ' + cmd)

    return result


def _setenv(env, value):
    if value:
        os.environ[env] = value


def _cmd(cmd):
    _msg(cmd)


def _error(error):
    _msg(error)
    exit(1)


def _info(info):
    _msg(info)


def _msg(msg):
    m = inspect.stack()[1][3].upper().lstrip('_')
    m = '[' + m + '] ' + msg
    print m


if __name__ == '__main__':
    parse_arg()
    setup()
    sync()
    build()
    test()
