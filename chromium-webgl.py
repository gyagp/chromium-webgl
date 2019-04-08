import argparse
import datetime
import inspect
import json
import multiprocessing
import os
import platform
import re
import subprocess
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from HTMLParser import HTMLParser


boto_file = '.boto'
cpu_count = multiprocessing.cpu_count()

build_dir = ''
chrome_src_dir = ''
depot_tools_dir = ''
script_dir = ''
host_os = platform.system().lower()
mesa_install_dir = '/workspace/install'
test_chrome = ''

skip = {
    #'linux': ['WebglConformance_conformance2_textures_misc_tex_3d_size_limit'],
    'linux': [],
    'windows': [],
    'darwin': [],
}

def parse_arg():
    global args
    parser = argparse.ArgumentParser(description='Chromium WebGL',
                                     formatter_class=argparse.RawTextHelpFormatter,
                                     epilog='''
examples:
  python %(prog)s --proxy <host>:<port> --build --build-chrome-hash <hash>
  python %(prog)s --test
''')
    parser.add_argument('--proxy', dest='proxy', help='proxy')
    parser.add_argument('--build', dest='build', help='build', action='store_true')
    parser.add_argument('--build-chrome-hash', dest='build_chrome_hash', help='Chrome hash to build', default='latest')
    parser.add_argument('--test', dest='test', help='test', action='store_true')
    parser.add_argument('--test-chrome-rev', dest='test_chrome_rev', help='Chromium revision', default='latest')
    parser.add_argument('--test-mesa-rev', dest='test_mesa_rev', help='mesa revision', default='latest')
    parser.add_argument('--test-filter', dest='test_filter', help='WebGL CTS suite to test against', default='all')  # For smoke test, we may use conformance_attribs
    parser.add_argument('--test-verbose', dest='test_verbose', help='verbose mode of test', action='store_true')
    parser.add_argument('--test-chrome', dest='test_chrome', help='test chrome', default='default')
    parser.add_argument('--daily', dest='daily', help='daily test', action='store_true')
    parser.add_argument('--run', dest='run', help='run', action='store_true')
    parser.add_argument('--dryrun', dest='dryrun', help='dryrun', action='store_true')
    parser.add_argument('--skip-sync', dest='skip_sync', help='skip sync', action='store_true')
    args = parser.parse_args()

def setup():
    global build_dir, chrome_src_dir, depot_tools_dir, script_dir, test_chrome

    root_dir = os.path.dirname(os.path.split(os.path.realpath(__file__))[0]).replace('\\', '/')
    build_dir = root_dir + '/build'
    chrome_src_dir = root_dir + '/chromium/src'
    depot_tools_dir = root_dir + '/depot_tools'
    script_dir = root_dir + '/script'

    if host_os == 'windows':
        splitter = ';'
    elif host_os in ['linux', 'darwin']:
        splitter = ':'
    _setenv('PATH', depot_tools_dir + splitter + os.getenv('PATH'))

    test_chrome = args.test_chrome
    if host_os == 'darwin':
        if test_chrome == 'default':
            test_chrome = 'canary'
    else:
        if test_chrome == 'default':
            test_chrome = 'build'

def build(force=False):
    if not args.build and not force:
        return

    # build mesa
    if args.daily and host_os == 'linux':
        _chdir('/workspace/project/readonly/mesa')
        if not args.skip_sync:
            _exec('python mesa.py --sync')
        _exec('python mesa.py --build')

    if not args.skip_sync:
        _sync_chrome()
    if test_chrome == 'build':
        _build_chrome()

def test(force=False):
    if not args.test and not force:
        return

    if host_os == 'linux':
        mesa_rev_number = args.test_mesa_rev
        if mesa_rev_number == 'system':
            _info('Use system Mesa')
        else:
            if mesa_rev_number == 'latest':
                mesa_dir = _get_latest('mesa')
                mesa_rev_number = re.match('mesa-master-release-(.*)-', mesa_dir).group(1)
            else:
                files = os.listdir(mesa_install_dir)
                for file in files:
                    match = re.match('mesa-master-release-%s' % mesa_rev_number, file)
                    if match:
                        mesa_dir = file
                        break
                else:
                    _error('Could not find mesa build %s' % mesa_rev_number)

            mesa_dir = mesa_install_dir + '/' + mesa_dir
            _setenv('LD_LIBRARY_PATH', mesa_dir + '/lib')
            _setenv('LIBGL_DRIVERS_PATH', mesa_dir + '/lib/dri')
            _info('Use mesa at %s' % mesa_dir)

    common_cmd = 'python content/test/gpu/run_gpu_integration_test.py webgl_conformance --disable-log-uploads'
    if test_chrome == 'build':
        chrome_rev_number = args.test_chrome_rev
        if chrome_rev_number == 'latest':
            chrome_file = _get_latest('chrome')
            chrome_rev_number = chrome_file.replace('.zip', '')
            if not re.match(r'\d{6}', chrome_rev_number):
                _error('Could not find the correct revision')

        _chdir(build_dir)
        if not os.path.exists('%s' % chrome_rev_number):
            if not os.path.exists('%s.zip' % chrome_rev_number):
                _error('Could not find Chromium revision %s' % chrome_rev_number)
            _ensure_dir(chrome_rev_number)
            _exec('unzip %s.zip -d %s' % (chrome_rev_number, chrome_rev_number))

        _chdir(build_dir + '/' + chrome_rev_number)

        if host_os == 'windows':
            chrome = 'out\Default\chrome.exe'
        else:
            chrome = 'out/Default/chrome'

        if args.run:
            param = '--enable-experimental-web-platform-features --disable-gpu-process-for-dx12-vulkan-info-collection --disable-domain-blocking-for-3d-apis --disable-gpu-process-crash-limit --disable-blink-features=WebXR --js-flags=--expose-gc --disable-gpu-watchdog --autoplay-policy=no-user-gesture-required --disable-features=UseSurfaceLayerForVideo --enable-net-benchmarking --metrics-recording-only --no-default-browser-check --no-first-run --ignore-background-tasks --enable-gpu-benchmarking --deny-permission-prompts --autoplay-policy=no-user-gesture-required --disable-background-networking --disable-component-extensions-with-background-pages --disable-default-apps --disable-search-geolocation-disclosure --enable-crash-reporter-for-testing --disable-component-update'
            _exec('%s %s http://wp-27.sh.intel.com/workspace/project/readonly/WebGL/sdk/tests/webgl-conformance-tests.html?version=2.0.1' % (chrome, param))
            return

        common_cmd += ' --browser=exact --browser-executable=%s' % chrome
    else:
        common_cmd += ' --browser=%s' % test_chrome
        _chdir(chrome_src_dir)
        chrome_rev_number = test_chrome
        if host_os == 'darwin':
            if test_chrome == 'canary':
                chrome = '/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary'
            else:
                _error('test_chrome is not supported')
        elif host_os == 'linux':
            if test_chrome == 'canary':
                chrome = '/usr/bin/google-chrome-unstable'
            elif test_chrome == 'stable':
                chrome = '/usr/bin/google-chrome-stable'
            else:
                _error('test_chrome is not supported')
        else:
            _error('test_chrome is not supported')

    if args.test_filter != 'all':
        common_cmd += ' --test-filter=%s' % args.test_filter
    skip_filter = skip[host_os]
    if skip_filter:
        for skip_tmp in skip_filter:
            common_cmd += ' --skip=%s' % skip_tmp
    if args.test_verbose:
        common_cmd += ' --verbose'

    result_dir = '%s/result' % script_dir
    _ensure_dir(result_dir)
    datetime = _get_datetime()

    COMB_INDEX_WEBGL = 0
    COMB_INDEX_D3D = 1
    if host_os in ['linux', 'darwin']:
        combs = [['2.0.1']]
    elif host_os == 'windows':
        combs = [
            ['1.0.3', '9'],
            ['1.0.3', '11'],
            ['2.0.1', '11'],
        ]

    for comb in combs:
        cmd = common_cmd + ' --webgl-conformance-version=%s' % comb[COMB_INDEX_WEBGL]
        log_file = ''
        if host_os == 'linux':
            log_file = '%s/%s-%s-%s-%s.log' % (result_dir, datetime, chrome_rev_number, mesa_rev_number, comb[COMB_INDEX_WEBGL])
        elif host_os == 'windows':
            if comb[COMB_INDEX_D3D] != '11':
                cmd += ' --extra-browser-args=--use-angle=d3d%s' % comb[COMB_INDEX_D3D]
            log_file = '%s/%s-%s-%s-%s.log' % (result_dir, datetime, chrome_rev_number, comb[COMB_INDEX_WEBGL], comb[COMB_INDEX_D3D])
        elif host_os == 'darwin':
            log_file = '%s/%s-%s-%s.log' % (result_dir, datetime, chrome_rev_number, comb[COMB_INDEX_WEBGL])

        cmd += ' --write-full-results-to %s' % log_file
        result = _exec(cmd)
        if result[0]:
            _warning('Failed to run test "%s"' % cmd)

        # send report
        if args.daily and host_os == 'linux':
            json_result = json.load(open(log_file))
            result_type = json_result['num_failures_by_type']
            content = 'FAIL: %s, SKIP: %s, PASS %s\n' % (result_type['FAIL'], result_type['SKIP'], result_type['PASS'])
            test_results = json_result['tests']
            fails = []
            for key in test_results:
                if test_results[key]['actual'] == 'FAIL':
                    fails.append(key)
            if fails:
                content += '[FAIL]\n'
                for fail in fails:
                    content += fail + '\n'

            subject = 'WebGL CTS on Chrome %s and Mesa %s has %s Regression' % (chrome_rev_number, mesa_rev_number, json_result['num_regressions'])
            _send_email('webperf@intel.com', 'yang.gu@intel.com', subject, content)

def run():
    if not args.run:
        return

    test(force=True)

def daily():
    if not args.daily:
        return

    build(force=True)
    test(force=True)

def _sync_chrome():
    _chdir(depot_tools_dir)
    _exec('git pull')

    chrome_rev_hash = args.build_chrome_hash

    if chrome_rev_hash != 'latest':
        (chrome_rev_hash_tmp, _) = _get_rev()
        if chrome_rev_hash == chrome_rev_hash_tmp:
            return

    _chdir(chrome_src_dir)
    _exec('git pull')
    cmd = 'gclient sync -R --break_repo_locks --delete_unversioned_trees -j%s' % cpu_count
    if chrome_rev_hash != 'latest':
        cmd += ' --revision=%s' % chrome_rev_hash
    _exec(cmd)

def _build_chrome():
    (_, chrome_rev_number) = _get_rev()
    if os.path.exists('%s/%s.zip' % (build_dir, chrome_rev_number)):
        _info('Chrome has been built')
        return

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

    _chdir(chrome_src_dir + '/build/util')
    _exec('python lastchange.py -o LASTCHANGE')
    _chdir(chrome_src_dir)
    gn_args = 'proprietary_codecs=true ffmpeg_branding=\\\"Chrome\\\" is_debug=false'
    gn_args += ' symbol_level=0 is_component_build=false enable_nacl=false'
    cmd = 'gn --args=\"%s\" gen out/Default' % gn_args
    result = _exec(cmd)
    if result[0]:
        _error('Failed to execute gn command')
    result = _exec('ninja -j%s -C out/Default chrome chromedriver' % cpu_count)
    if result[0]:
        _error('Failed to build Chromium')

    # generate telemetry_gpu_integration_test
    cmd = 'python tools/mb/mb.py zip out/Default/ telemetry_gpu_integration_test %s/%s.zip' % (build_dir, chrome_rev_number)
    result = _exec(cmd)
    if result[0]:
        _error('Failed to generate telemetry_gpu_integration_test')

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

def _exec(cmd, return_out=False, show_cmd=True, show_duration=False, dryrun=False):
    if show_cmd:
        _cmd(cmd)

    if show_duration:
        start_time = datetime.datetime.now().replace(microsecond=0)

    if (args.dryrun or dryrun) and not re.match('git log', cmd):
        result = [0, '']
    else:
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

def _get_rev():
    _chdir(chrome_src_dir)
    cmd = 'git log --shortstat -1'
    result = _exec(cmd, show_cmd=False, return_out=True)
    lines = result[1].split('\n')
    for line in lines:
        match = re.match('commit (.*)', line)
        if match:
            chrome_rev_hash = match.group(1)
        match = re.search('Cr-Commit-Position: refs/heads/master@{#(.*)}', line)
        if match:
            chrome_rev_number = int(match.group(1))
            break
    else:
        _error('Failed to find the revision of Chromium')

    return (chrome_rev_hash, chrome_rev_number)

def _get_latest(type):
    if type == 'mesa':
        rev_dir = mesa_install_dir
        rev_pattern = 'mesa-master-release-(.*)-'
    elif type == 'chrome':
        rev_dir = build_dir
        rev_pattern = '(\d{6}).zip'

    latest_rev = -1
    latest_file = ''
    files = os.listdir(rev_dir)
    for file in files:
        match = re.search(rev_pattern, file)
        if match:
            tmp_rev = int(match.group(1))
            if tmp_rev > latest_rev:
                latest_file = file
                latest_rev = tmp_rev

    return latest_file

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

def _warning(warning):
    _msg(warning)

def _msg(msg):
    m = inspect.stack()[1][3].upper().lstrip('_')
    m = '[' + m + '] ' + msg
    print m

def _send_email(sender, to, subject, content, type='plain'):
    if isinstance(to, list):
        to = ','.join(to)

    to_list = to.split(',')
    msg = MIMEMultipart('alternative')
    msg['From'] = sender
    msg['To'] = to
    msg['Subject'] = subject
    msg.attach(MIMEText(content, type))

    try:
        smtp = smtplib.SMTP('localhost')
        smtp.sendmail(sender, to_list, msg.as_string())
        _info('Email was sent successfully')
    except Exception as e:
        _error('Failed to send mail: %s' % e)
    finally:
        smtp.quit()

class Parser(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self)
        self.is_tr = False
        self.tag_count = 0
        self.chrome_rev_hash = ''
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
                self.rev_result.append([self.chrome_rev_hash, data])
                self.tag_count = 0
            elif self.tag_count > 0:
                self.tag_count = self.tag_count + 1
            match = re.search('([a-z0-9]{40})', data)
            if match:
                self.chrome_rev_hash = match.group(1)
                self.tag_count = 1


if __name__ == '__main__':
    parse_arg()
    setup()
    build()
    test()
    run()
    daily()
