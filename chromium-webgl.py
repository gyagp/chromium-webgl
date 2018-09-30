import argparse
import datetime
import inspect
import multiprocessing
import os
import re
import subprocess
import time
import urllib2
from HTMLParser import HTMLParser


boto_file = '.boto'
cpu_count = multiprocessing.cpu_count()
lkgr_count = 100
lkgr_url = 'https://ci.chromium.org/p/chromium/builders/luci.chromium.ci/Win10%20FYI%20Release%20%28Intel%20HD%20630%29?limit=500'

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
  python %(prog)s --proxy <host>:<port> --build --build-revision <revision>
  python %(prog)s --test --test-revision <revision>
''')
    parser.add_argument('--proxy', dest='proxy', help='proxy')
    parser.add_argument('--build', dest='build', help='build', action='store_true')
    parser.add_argument('--build-revision', dest='build_revision', help='Chromium revision to build with')
    parser.add_argument('--test', dest='test', help='test', action='store_true')
    parser.add_argument('--test-revision', dest='test_revision', help='Chromium revision to test against', default='lkgr')
    parser.add_argument('--test-filter', dest='test_filter', help='WebGL CTS suite to test against', default='all')  # For smoke test, we may use conformance_attribs
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


def build():
    if not args.build:
        return

    # get rev_hash
    if args.build_revision:
        rev_hash = args.build_revision
    else:
        try:
            response = urllib2.urlopen(lkgr_url)
            html = response.read()
        except Exception:
            _error('Failed to open %s' % lkgr_url)

        parser = Parser()
        parser.feed(html)

        count = 0
        for i in range(0, len(parser.rev_result)):
            rev_hash = parser.rev_result[i][0]
            result = parser.rev_result[i][1]
            if result == 'Success':
                count = count + 1
            else:
                count = 0
            if count == lkgr_count:
                break

        if count == lkgr_count:
            rev_hash = parser.rev_result[i][0]
            _info('The Last Known Good Revision is %s' % rev_hash)
        else:
            _error('Could not find Last Known Good Revision')

    # sync code
    _chdir(depot_tools_dir)
    _exec('git pull')

    (current_rev_hash, _) = _get_revision()
    if current_rev_hash != rev_hash:
        _chdir(chromium_src_dir)
        _exec('git pull')
        cmd = 'gclient sync -R -j%s --revision=%s' % (cpu_count, rev_hash)
        _exec(cmd)

    # build Chromium
    _chdir(chromium_src_dir)
    gn_args = 'proprietary_codecs=true ffmpeg_branding=\\\"Chrome\\\" is_debug=false'
    gn_args += ' symbol_level=0 is_component_build=false remove_webcore_debug_symbols=true enable_nacl=false'
    cmd = 'gn --args=\"%s\" gen out/Default' % gn_args
    result = _exec(cmd)
    if result[0]:
        _error('Failed to execute gn command')
    result = _exec('ninja -j%s -C out/Default chrome' % cpu_count)
    if result[0]:
        _error('Failed to build Chromium')

    (_, rev_number) = _get_revision()
    # generate telemetry_gpu_integration_test
    cmd = 'python tools/mb/mb.py zip out/Default/ telemetry_gpu_integration_test %s/%s.zip' % (build_dir, rev_number)
    result = _exec(cmd)
    if result[0]:
        _error('Failed to generate telemetry_gpu_integration_test')


def test():
    if not args.test:
        return

    _chdir(build_dir)
    rev_number = args.test_revision
    if rev_number == 'latest':
        files = sorted(os.listdir('.'), reverse=True)
        rev_number = files[0].replace('.zip', '')
        if not re.match('\d{6}', rev_number):
            _error('Could not find the correct revision')

    if not os.path.exists('%s' % rev_number):
        if not os.path.exists('%s.zip' % rev_number):
            _error('Could not find Chromium revision %s' % rev_number)
        _ensure_dir(rev_number)
        _exec('unzip %s.zip -d %s' % (rev_number, rev_number))

    _chdir(build_dir + '/' + rev_number)
    common_cmd = 'python content/test/gpu/run_gpu_integration_test.py webgl_conformance --browser=exact --browser-executable=%s/out/Default/chrome.exe' % (build_dir + '/' + rev_number)
    if args.test_filter != 'all':
        common_cmd += ' --test-filter=%s' % args.test_filter

    result_dir = '%s/result' % script_dir
    _ensure_dir(result_dir)
    cmds = []
    datetime = _get_datetime()

    VERSION_INDEX_WEBGL = 0
    VERSION_INDEX_D3D = 1
    versions = [
        ['1.0.3', '9'],
        ['1.0.3', '11'],
        ['2.0.1', '11'],
    ]

    for version in versions:
        cmd = common_cmd + ' --webgl-conformance-version=%s' % version[VERSION_INDEX_WEBGL]
        if version[VERSION_INDEX_D3D] != '11':
            cmd += ' --extra-browser-args=--use-angle=d3d%s' % version[VERSION_INDEX_D3D]
        cmd += ' --write-full-results-to %s/%s-%s-%s.log' % (result_dir, datetime, version[VERSION_INDEX_WEBGL], version[VERSION_INDEX_D3D])
        cmds.append(cmd)

    for cmd in cmds:
        result = _exec(cmd)
        if result[0]:
            _error('Failed to run test "%s"' % cmd)


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


def _get_datetime(format='%Y%m%d%H%M%S'):
    return time.strftime(format, time.localtime())


def _get_revision():
    _chdir(chromium_src_dir)
    cmd = 'git log --shortstat -1'
    result = _exec(cmd, show_cmd=False, return_out=True)
    lines = result[1].split('\n')
    for line in lines:
        match = re.match('commit (.*)', line)
        if match:
            rev_hash = match.group(1)
        match = re.search('Cr-Commit-Position: refs/heads/master@{#(.*)}', line)
        if match:
            rev_number = int(match.group(1))
            break
    else:
        _error('Failed to find the revision of Chromium')

    return (rev_hash, rev_number)


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


class Parser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.is_tr = False
        self.tag_count = 0
        self.rev_hash = ''
        self.rev_result = []

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.is_tr = True

    def handle_endtag(self, tag):
        if tag == 'tr':
            self.is_tr = False

    def handle_data(self, data):
        if self.is_tr:
            if self.tag_count == 3:
                self.rev_result.append([self.rev_hash, data])
                self.tag_count = 0
            elif self.tag_count > 0:
                self.tag_count = self.tag_count + 1
            match = re.search('([a-z0-9]{40})', data)
            if match:
                self.rev_hash = match.group(1)
                self.tag_count = 1


if __name__ == '__main__':
    parse_arg()
    setup()
    build()
    test()
