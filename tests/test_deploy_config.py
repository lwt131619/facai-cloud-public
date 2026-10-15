"""The deployment plane must be safe and parseable before it ever runs.

These checks are static (they run everywhere) plus an optional real
``nginx -t`` / ``bash -n`` pass driven by ``deploy/validate_config.sh``.
"""

import importlib.util
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTTP_CONF = ROOT / 'deploy' / 'nginx' / 'facai-cloud.http.conf'
TLS_CONF = ROOT / 'deploy' / 'nginx' / 'facai-cloud.conf'
DEPLOY = ROOT / 'deploy' / 'deploy.sh'
INSTALL_OPENCODE = ROOT / 'deploy' / 'install-opencode.sh'
SERVICE_UNIT = ROOT / 'deploy' / 'systemd' / 'facai-cloud.service'
BACKUP_UNIT = ROOT / 'deploy' / 'facai-backup.service'
DIGEST_UNIT = ROOT / 'deploy' / 'facai-digest.service'
APP_ENV_EXAMPLE = ROOT / 'deploy' / 'facai.env.example'
PROVIDER_DIR = ROOT / 'deploy' / 'providers'
PROVIDER_EXAMPLE = PROVIDER_DIR / 'deepseek.env.example'
# The operator-side one-shot deploy lives one level above the repo and is not
# part of the deployed tree; the tests that need it skip when it is absent.
DEPLOY_NOW = ROOT.parent / 'deploy-now.py'


def code_only(path):
    """Strip ``#`` comment lines so prose cannot satisfy or break a check."""
    lines = path.read_text(encoding='utf-8').splitlines()
    return '\n'.join(line for line in lines if not line.lstrip().startswith('#'))


def find_bash():
    candidates = []
    override = os.environ.get('FACAI_BASH')
    if override:
        candidates.append(override)
    if os.name == 'nt':
        candidates += [
            r'C:\Program Files\Git\bin\bash.exe',
            r'C:\Program Files (x86)\Git\bin\bash.exe',
        ]
    else:
        candidates.append('/bin/bash')
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def test_http_bootstrap_needs_no_certificate():
    conf = code_only(HTTP_CONF)
    assert 'listen 80' in conf
    assert '.well-known/acme-challenge' in conf
    assert 'listen 443' not in conf
    assert 'ssl_certificate' not in conf


def test_tls_config_requires_cert_and_omits_includesubdomains():
    conf = code_only(TLS_CONF)
    assert 'ssl_certificate' in conf
    assert '/etc/letsencrypt/live/' in conf
    assert 'Strict-Transport-Security' in conf
    assert 'includeSubDomains' not in conf


def test_deploy_gates_tls_and_preserves_unknown_files():
    script = DEPLOY.read_text(encoding='utf-8')
    # Two-phase install: HTTP bootstrap first, TLS only once a cert exists.
    assert 'facai-cloud.http.conf' in script
    assert 'facai-cloud.conf' in script
    assert 'has_cert' in script
    assert 'cmd_cert' in script
    # Scoped rsync: delete only inside managed paths, protect data and the rest.
    assert '--delete' in script
    assert "--exclude '/*'" in script
    assert 'P data' in script
    # Recoverability.
    assert 'backup_app_dir' in script
    assert 'cmd_rollback' in script


def test_managed_includes_reference_existing_paths():
    """Every `--include '/path'` must exist in the checkout.

    A typo here (e.g. a renamed top-level script) makes rsync silently skip the
    file, so the VPS would never receive it. This guards that class of bug.
    """
    script = DEPLOY.read_text(encoding='utf-8')
    managed = re.findall(r"--include '/+([^']+)'", script)
    assert managed, 'deploy.sh declares no managed include paths'
    for entry in managed:
        relative = entry.split('/***')[0].strip('/')
        assert (ROOT / relative).exists(), f'deploy.sh manages a missing path: {entry}'


def test_update_reinstalls_service_and_proxy_config():
    """`update` must not leave a changed unit or nginx config behind."""
    script = DEPLOY.read_text(encoding='utf-8')
    assert 'cmd_update()' in script
    body = script.split('cmd_update()', 1)[1].split('cmd_cert()', 1)[0]
    assert 'install_systemd' in body
    assert 'install_nginx' in body


def test_deploy_refuses_public_tls_without_credentials():
    """Enabling TLS exposes the API, so cert/update must fail safe.

    `update` must not reinstall a unit whose EnvironmentFile is missing, and
    `cert` must not enable public HTTPS while FACAI_ADMIN_PASSWORD is unset.
    """
    script = DEPLOY.read_text(encoding='utf-8')
    assert 'require_env_file' in script
    assert 'require_admin_password' in script
    assert 'adopt_legacy_env' in script
    update_body = script.split('cmd_update()', 1)[1].split('cmd_cert()', 1)[0]
    assert 'require_env_file' in update_body
    cert_body = script.split('cmd_cert()', 1)[1].split('cmd_backup()', 1)[0]
    assert 'require_admin_password' in cert_body


def test_service_unit_gives_opencode_a_path_and_writable_home():
    """The adapter resolves `opencode` on PATH and the CLI writes under $HOME.

    The hardened unit makes ``/opt/facai-cloud/data`` the only writable path, so
    HOME must live there; otherwise every run fails with a read-only filesystem.
    """
    unit = code_only(SERVICE_UNIT)
    assert 'Environment=PATH=/usr/local/bin' in unit
    assert 'Environment=HOME=/opt/facai-cloud/data/' in unit
    assert 'XDG_DATA_HOME=/opt/facai-cloud/data/' in unit
    assert 'EnvironmentFile=/etc/facai-cloud/facai.env' in unit
    assert 'ReadWritePaths=/opt/facai-cloud/data' in unit
    # No credential may be baked into the unit.
    for forbidden in ('DEEPSEEK_API_KEY=', 'FACAI_ADMIN_PASSWORD=', 'sk-'):
        assert forbidden not in unit


def test_opencode_installer_pins_and_verifies_version():
    """A pinned, checksum-verified install; never an unpinned download."""
    script = code_only(INSTALL_OPENCODE)
    assert 'OPENCODE_VERSION="${OPENCODE_VERSION:-' in script
    assert 'OPENCODE_SHA256="${OPENCODE_SHA256:-' in script
    assert 'sha256sum' in script
    assert 'SHA-256 mismatch' in script
    assert 'anomalyco/opencode/releases/download/v${OPENCODE_VERSION}' in script
    assert 'install -m 0755' in script
    # It must not touch any model credential.
    assert 'DEEPSEEK_API_KEY' not in script
    assert 'api_key' not in script.lower()


def test_deploy_manages_the_opencode_home_and_action():
    script = DEPLOY.read_text(encoding='utf-8')
    assert 'prepare_opencode_home' in script
    assert 'cmd_opencode' in script
    assert 'install-opencode.sh' in script
    # install and update both prepare the writable OpenCode HOME.
    install_body = script.split('cmd_install()', 1)[1].split('cmd_update()', 1)[0]
    update_body = script.split('cmd_update()', 1)[1].split('cmd_opencode()', 1)[0]
    assert 'prepare_opencode_home' in install_body
    assert 'prepare_opencode_home' in update_body


def test_service_unit_loads_app_and_classified_provider_env():
    """The unit must load the app env and every classified provider file.

    Provider credentials are one file per service under providers/; the leading
    '-' keeps the unit valid before the first provider file exists.
    """
    unit = code_only(SERVICE_UNIT)
    assert 'EnvironmentFile=/etc/facai-cloud/facai.env' in unit
    assert 'EnvironmentFile=-/etc/facai-cloud/providers/*.env' in unit


def test_app_env_example_has_no_provider_credentials():
    """Application login/runtime config must not carry model keys.

    Only the actively-used DeepSeek key may ever be migrated; unused providers
    (Tavily/Bocha/Agnes/Claude/Pollinations) must not appear at all.
    """
    text = APP_ENV_EXAMPLE.read_text(encoding='utf-8')
    assert 'DEEPSEEK_API_KEY' not in text
    for unused in ('TAVILY', 'BOCHA', 'AGNES', 'ANTHROPIC', 'CLAUDE', 'POLLINATIONS'):
        assert unused not in text
    # The template points at the classified provider directory instead.
    assert 'providers/' in text


def test_provider_env_template_declares_only_a_name():
    """A committed provider template must declare the key name, never a value."""
    assert PROVIDER_EXAMPLE.is_file()
    lines = [
        line for line in PROVIDER_EXAMPLE.read_text(encoding='utf-8').splitlines()
        if not line.lstrip().startswith('#')
    ]
    body = '\n'.join(lines)
    assert 'DEEPSEEK_API_KEY=' in body
    assert re.search(r'DEEPSEEK_API_KEY=.+', body) is None, 'template must not contain a value'


def test_every_provider_template_declares_names_without_values():
    """No classified provider template may carry a real credential value.

    Channel credentials (WhatsApp/WeChat/Telegram) are templates like DeepSeek:
    they declare the variable names and leave the values empty so nothing secret
    can be committed.
    """
    templates = sorted(PROVIDER_DIR.glob('*.env.example'))
    assert templates, 'no provider templates found'
    for template in templates:
        for line in template.read_text(encoding='utf-8').splitlines():
            stripped = line.strip()
            if stripped.startswith('#') or '=' not in stripped:
                continue
            name, _, value = stripped.partition('=')
            assert name.isupper(), f'{template.name}: {name} is not an env name'
            assert value == '', f'{template.name}: {name} must not carry a value'


def test_deploy_owns_the_provider_split_with_backup():
    """install/update must seed classified files, migrate once and back up first."""
    script = DEPLOY.read_text(encoding='utf-8')
    assert 'PROVIDER_ENV_DIR' in script
    assert 'install_provider_env_files' in script
    assert 'classify_deepseek_env' in script
    assert 'backup_env_dir' in script
    for phase in ('cmd_install()', 'cmd_update()'):
        body = script.split(phase, 1)[1].split('\n}', 1)[0]
        assert 'install_provider_env_files' in body
        assert 'classify_deepseek_env' in body
        assert 'backup_env_dir' in body


def test_validate_config_script_passes():
    bash = find_bash()
    if not bash:
        pytest.skip('bash unavailable')
    script = str(ROOT / 'deploy' / 'validate_config.sh').replace('\\', '/')
    result = subprocess.run(
        [bash, script],
        capture_output=True, text=True, timeout=120,
    )
    output = (result.stdout or '') + (result.stderr or '')
    if result.returncode != 0 and 'WSL' in output and 'execvpe' in output:
        pytest.skip('only the WSL bash launcher is available (no distro)')
    assert result.returncode == 0, output


def test_env_backups_are_pruned_like_app_backups():
    """Env archives carry every provider key plus FACAI_ADMIN_PASSWORD.

    ``app-*.tar.gz`` already honors BACKUP_KEEP; without the same retention the
    secrets-bearing ``env-*.tar.gz`` stack grows on the server forever. The
    ``env-latest.tar.gz`` rollback pointer must survive the prune.
    """
    script = DEPLOY.read_text(encoding='utf-8')
    body = script.split('backup_env_dir()', 1)[1].split('\n}', 1)[0]
    assert 'BACKUP_KEEP' in body, 'env archives must honor the app retention count'
    assert 'env-*.tar.gz' in body
    assert 'rm -f' in body
    assert 'env-latest' in body and 'grep -v' in body, 'the latest pointer must not be pruned'


def test_unattended_units_are_hardened_like_the_service():
    """Backup and digest run unattended on data and untrusted upstream input.

    Every other unit runs as the unprivileged service user with a read-only
    filesystem and exactly one writable path; these two were the exception.
    """
    for unit_path in (BACKUP_UNIT, DIGEST_UNIT):
        unit = code_only(unit_path)
        assert 'User=facai' in unit, f'{unit_path.name} must not run as root'
        for directive in (
            'NoNewPrivileges=true',
            'PrivateTmp=true',
            'ProtectSystem=strict',
            'ReadWritePaths=/opt/facai-cloud/data',
        ):
            assert directive in unit, f'{unit_path.name} lacks {directive}'


def test_deploy_script_is_executable_in_git():
    """The server probes ``test -x`` on deploy.sh to pick the update path.

    In the index the mode must be 100755; a 100644 entry makes the probe fail
    and silently downgrades the deploy to a plain tar overlay (no backup, no
    dependencies, no unit refresh).
    """
    if not (ROOT / '.git').exists():
        pytest.skip('not a git checkout (deployed tree)')
    git = shutil.which('git')
    if not git:
        pytest.skip('git unavailable')
    result = subprocess.run(
        [git, '-C', str(ROOT), 'ls-files', '-s', '--', 'deploy/deploy.sh'],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith('100755'), result.stdout


class _FakeStream:
    """Stands in for paramiko's stdout/stderr: read() plus the channel exit code."""

    def __init__(self, text='', code=0):
        self._text = text.encode('utf-8')
        self.code = code
        self.channel = self

    def read(self):
        return self._text

    def recv_exit_status(self):
        return self.code


class _FakeSFTP:
    def put(self, local, remote):
        self.put_args = (local, remote)

    def close(self):
        pass


class _FakeSSHClient:
    """Records the deploy session so the test can assert on the commands sent."""

    def __init__(self, fail_on=None):
        self.commands = []
        self.connected = {}
        self.host_keys_loaded = False
        self.closed = False
        self.fail_on = fail_on

    def load_system_host_keys(self):
        self.host_keys_loaded = True

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, host, **kwargs):
        self.connected = {'host': host, **kwargs}

    def open_sftp(self):
        return _FakeSFTP()

    def exec_command(self, command, timeout=None):
        self.commands.append(command)
        code = 1 if (self.fail_on and self.fail_on in command) else 0
        text = ''
        if command.startswith('systemctl is-active'):
            text = 'active'
        elif 'manifest.webmanifest' in command:
            text = '200'
        elif command.startswith('curl -s '):
            text = '{"status":"ok"}'
        return None, _FakeStream(text, code), _FakeStream('', code)

    def close(self):
        self.closed = True


def load_deploy_now():
    """Import the operator-side deploy script (outside the deployed tree)."""
    if not DEPLOY_NOW.is_file():
        pytest.skip('deploy-now.py is not part of the deployed tree')
    pytest.importorskip('paramiko')
    spec = importlib.util.spec_from_file_location('facai_deploy_now', DEPLOY_NOW)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deploy_now_prompts_and_hardens_the_staged_tree(monkeypatch):
    """No password argument means an interactive prompt, not a usage error.

    The staged tree is unpacked with a fixed umask and without root's default
    ``-p``, so the bundle's permissive member modes cannot make the staged code
    world-writable, and the operator's known_hosts is consulted before auth.
    """
    module = load_deploy_now()
    ssh = _FakeSSHClient()
    monkeypatch.setattr(module.paramiko, 'SSHClient', lambda: ssh)
    prompts = []
    monkeypatch.setattr(
        module.getpass, 'getpass',
        lambda prompt='': prompts.append(prompt) or 'prompted-pass',
    )
    monkeypatch.setattr(module.sys, 'argv', ['deploy-now.py'])

    assert module.main() == 0
    assert prompts, 'the script must prompt when no password argument is given'
    assert ssh.connected['password'] == 'prompted-pass'
    assert ssh.host_keys_loaded, 'known_hosts must be consulted before connecting'
    extractions = [c for c in ssh.commands if 'tar -xzf' in c]
    assert extractions, 'no extraction command was sent'
    for command in extractions:
        assert 'umask 022' in command
        assert '--no-same-permissions' in command
    assert ssh.closed


def test_deploy_now_still_accepts_a_password_argument(monkeypatch):
    """argv compatibility is kept, so existing runbooks keep working."""
    module = load_deploy_now()
    ssh = _FakeSSHClient()
    monkeypatch.setattr(module.paramiko, 'SSHClient', lambda: ssh)
    monkeypatch.setattr(module.sys, 'argv', ['deploy-now.py', 'argv-pass'])

    assert module.main() == 0
    assert ssh.connected['password'] == 'argv-pass'


def test_deploy_now_refuses_an_empty_password(monkeypatch):
    """An empty or interrupted prompt is misuse, never a connect attempt."""
    module = load_deploy_now()
    ssh = _FakeSSHClient()
    monkeypatch.setattr(module.paramiko, 'SSHClient', lambda: ssh)
    monkeypatch.setattr(module.getpass, 'getpass', lambda prompt='': '')
    monkeypatch.setattr(module.sys, 'argv', ['deploy-now.py'])

    assert module.main() == 2
    assert not ssh.connected


def test_deploy_now_cleans_up_staging_even_after_a_failed_run(monkeypatch):
    """The staging tree is a root-owned copy of the repo; it must not outlive the run."""
    module = load_deploy_now()
    ssh = _FakeSSHClient(fail_on='systemctl restart')
    monkeypatch.setattr(module.paramiko, 'SSHClient', lambda: ssh)
    monkeypatch.setattr(module.sys, 'argv', ['deploy-now.py', 'argv-pass'])

    assert module.main() == 1
    assert f'rm -rf {module.STAGING_DIR}' in ssh.commands
    assert any(c.startswith('rm -f ') and module.REMOTE_BUNDLE in c for c in ssh.commands)
    assert ssh.closed


def test_deploy_now_overlay_fallback_is_also_permission_hardened(monkeypatch):
    """Without an executable deploy.sh the fallback writes into the live app dir.

    That overlay is the more dangerous extraction, so it carries the same umask
    and --no-same-permissions restriction as the staging extract.
    """
    module = load_deploy_now()
    ssh = _FakeSSHClient(fail_on='test -x')
    monkeypatch.setattr(module.paramiko, 'SSHClient', lambda: ssh)
    monkeypatch.setattr(module.sys, 'argv', ['deploy-now.py', 'argv-pass'])

    assert module.main() == 0
    overlay = [c for c in ssh.commands if 'tar -xf -' in c]
    assert overlay, 'the fallback overlay never ran'
    for command in overlay:
        assert 'umask 022' in command
        assert '--no-same-permissions' in command
