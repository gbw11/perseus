"""새 Linux 사본의 표시만 변경하고 실제 Bash prompt expansion까지 검사한다."""
import copy
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from first_install_fixture import prepare_rootfs as guest, guest_identity
from test_xenova_platform import record
import test_xenova_platform as contracts


class GuestPromptTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='xenova-prompt-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        contracts.GuestPreparationTest().root(self.root)
        (self.root/'root').mkdir()
        (self.root/'usr/bin').mkdir(parents=True, exist_ok=True)
        (self.root/'usr/bin/python3').write_text('fixture interpreter, not executed')
        self.original_bashrc = "# distro startup fixture\nPS1='distro default> '\n"
        (self.root/'etc/bash.bashrc').write_text(self.original_bashrc)
        guest.prepare_template(self.root)
        self.record = record()
        self.record['display_name'] = '주행 제어 A'

    def prepare(self, *, seed=False):
        if seed:
            guest.prepare_deployment_seed(self.root, self.record)
        else:
            guest.prepare_instance(self.root, self.record, command=lambda *a, **kw: None)

    def shell_files(self):
        # Only absolute guest read/source paths are redirected into this fixture.
        # Prompt construction, hook handling and Bash expansion are production code.
        profile = self.root/'local-prompt.sh'
        profile.write_text((self.root/'etc/profile.d/xenova-prompt.sh').read_text()
                           .replace('/etc/xenova/', str(self.root/'etc/xenova')+'/')
                           .replace('/sys/hypervisor/uuid', str(self.root/'sys/hypervisor/uuid')))
        bashrc = self.root/'local-bashrc'
        bashrc.write_text((self.root/'etc/bash.bashrc').read_text()
                         .replace('/etc/profile.d/xenova-prompt.sh', str(profile)))
        return profile, bashrc

    def shell(self, *, startup='login', before='', commands=''):
        profile, bashrc = self.shell_files()
        # /etc/profile sources bash.bashrc and then profile.d. User rc files may
        # subsequently reset PS1. A non-login Bash reads bash.bashrc directly.
        sources = f'. {bashrc}\n'
        if startup == 'login':
            sources += f'. {profile}\n'
        script = before + sources + "PS1='late user rc> '\n" + commands + '\nexit\n'
        return subprocess.run(['bash', '--noprofile', '--norc', '-i'], input=script,
                              cwd=self.root/'root', text=True, capture_output=True, timeout=10,
                              env={'PATH':os.environ['PATH'], 'HOME':str(self.root/'root'),
                                   'TERM':'dumb', 'LC_ALL':'C.UTF-8'})

    def test_ordinary_prompt_reuses_display_name_and_preserves_all_identity_fields(self):
        before = copy.deepcopy(self.record)
        self.prepare()
        self.assertEqual(self.record, before)
        self.assertEqual((self.root/'etc/hostname').read_text(), self.record['hostname']+'\n')
        self.assertEqual((self.root/'etc/xenova/instance-id').read_text(), self.record['uuid']+'\n')
        self.assertEqual((self.root/'etc/xenova/prompt-uuid').read_text(), self.record['uuid']+'\n')
        self.assertEqual((self.root/'etc/xenova/display-name').read_text(), '주행 제어 A\n')
        self.assertTrue((self.root/'etc/bash.bashrc').read_text().startswith(self.original_bashrc))
        for startup in ('login', 'non-login'):
            with self.subTest(startup=startup):
                result = self.shell(startup=startup, commands='false\nprintf "STATUS:%s\\n" "$?"\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('[주행 제어 A] ', result.stderr)
                self.assertIn('@vm-'+self.record['uuid'][:8]+':~', result.stderr)
                self.assertIn('STATUS:1\n', result.stdout)

    def test_shell_and_prompt_metacharacters_remain_literal_through_repeated_prompts(self):
        self.record['display_name'] = '주행 $(touch pwned) `touch pwned` ${HOME} \\u\'"[]'
        self.prepare()
        result = self.shell(commands='printf "first\\n"\nprintf "second\\n"\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('['+self.record['display_name']+'] ', result.stderr)
        self.assertFalse((self.root/'root/pwned').exists())

    def test_existing_scalar_and_array_hooks_survive_and_repeated_source_is_idempotent(self):
        self.prepare()
        for previous in ('PROMPT_COMMAND=previous', 'PROMPT_COMMAND=(previous second)'):
            with self.subTest(previous=previous):
                result = self.shell(before='previous() { :; }\nsecond() { :; }\n'+previous+'\n',
                                    commands='printf "HOOK:%s\\n" "${PROMPT_COMMAND[@]}"\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                hooks = [line for line in result.stdout.splitlines() if line.startswith('HOOK:')]
                expected = ['HOOK:previous']+(['HOOK:second'] if '(' in previous else [])+['HOOK:_xenova_prompt']
                self.assertEqual(hooks, expected)

    def test_seed_defers_short_id_until_real_guest_identity_exists(self):
        self.record['uuid'] = '00000000-0000-4000-8000-000000000000'
        self.prepare(seed=True)
        self.assertFalse((self.root/'etc/xenova/prompt-uuid').exists())
        xen_uuid = self.root/'sys/hypervisor/uuid'
        xen_uuid.parent.mkdir(parents=True)
        xen_uuid.write_text('not-a-uuid\n')
        result = self.shell()
        self.assertNotIn('[주행 제어 A]', result.stderr)
        (self.root/'etc/machine-id').write_text('a13f0b2c'+'1'*24+'\n')
        def keys(args, **kwargs):
            if '-A' in args:
                for kind in ('rsa','ecdsa','ed25519'):
                    (self.root/f'etc/ssh/ssh_host_{kind}_key').write_text('owned fixture key')
        guest_identity.ensure(self.root, keys)
        identity_before = (self.root/'etc/xenova/instance-id').read_bytes()
        hostname_before = (self.root/'etc/hostname').read_bytes()
        result = self.shell()
        self.assertIn('[주행 제어 A] ', result.stderr)
        self.assertIn('@vm-a13f0b2c:~', result.stderr)
        self.assertNotIn('@vm-00000000:', result.stderr)
        self.assertEqual((self.root/'etc/xenova/instance-id').read_bytes(), identity_before)
        self.assertEqual((self.root/'etc/hostname').read_bytes(), hostname_before)

    def test_seed_can_read_the_actual_xen_uuid_without_reading_root_only_instance_id(self):
        self.prepare(seed=True)
        path = self.root/'sys/hypervisor/uuid'
        path.parent.mkdir(parents=True)
        path.write_text('a13f0b2c-1111-4222-8333-123456789abc\n')
        result = self.shell()
        self.assertIn('@vm-a13f0b2c:~', result.stderr)

    def test_noninteractive_and_non_bash_shells_keep_their_original_prompt_and_output(self):
        self.prepare()
        profile, _ = self.shell_files()
        for shell in ('bash','sh'):
            with self.subTest(shell=shell):
                result = subprocess.run([shell,'-c',f'PS1=original; . {profile}; printf "%s\\n" "$PS1"'],
                                        text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, 'original\n')
                self.assertEqual(result.stderr, '')

    def test_invalid_name_or_linked_prompt_path_is_rejected_before_identity_writes(self):
        for name in ('bad\nname', 'bad\x1bname', '', None):
            with self.subTest(name=name):
                self.record['display_name'] = name
                with self.assertRaisesRegex(ValueError, 'display name'):
                    self.prepare()
                self.assertFalse((self.root/'etc/hostname').exists())
        self.record['display_name'] = '주행 제어 A'
        (self.root/'etc/profile.d').mkdir()
        (self.root/'etc/profile.d/xenova-prompt.sh').symlink_to('/etc/shadow')
        original = (self.root/'etc/shadow').read_bytes()
        with self.assertRaisesRegex(ValueError, 'linked/non-regular'):
            self.prepare()
        self.assertEqual((self.root/'etc/shadow').read_bytes(), original)
        self.assertFalse((self.root/'etc/hostname').exists())

    def test_zephyr_record_does_not_receive_linux_prompt_files(self):
        self.record['guest_kind'] = 'zephyr'
        with self.assertRaisesRegex(ValueError, 'Linux prompt'):
            guest.configure_prompt(self.root, self.record)
        self.assertFalse((self.root/'etc/profile.d/xenova-prompt.sh').exists())
        self.assertFalse((self.root/'etc/xenova/display-name').exists())


if __name__ == '__main__':
    unittest.main()
