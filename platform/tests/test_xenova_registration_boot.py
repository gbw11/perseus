"""부팅 통지의 비동기 예약·설치 순서. 실제 systemd/HTTP 왕복은 process 시험에서 다룬다."""
import configparser
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import xenova_registration_hook as hook

ROOT=Path(__file__).resolve().parents[2]
VM='850e8400-e29b-41d4-a716-446655440001'


class RegistrationBootTest(unittest.TestCase):
    def test_real_defer_subprocess_only_submits_async_job_without_http_or_secret_inheritance(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'run').mkdir();(root/'data').mkdir();(root/'bin').mkdir()
            env=root/'local.env';env.write_text('XENOVA_HOST=127.0.0.1\nXENOVA_PORT=1\n');env.chmod(0o600)
            # systemd command boundary only. The product automatic/defer subprocess is real.
            command=root/'bin/systemd-run'
            command.write_text('#!/usr/bin/python3\nimport json,os,sys\nfrom pathlib import Path\n'
                               'Path(__file__).with_name("submitted.json").write_text(json.dumps({"args":sys.argv[1:],'
                               '"leaked":[k for k in os.environ if k in ("VM_REGISTRATION_SERVICE_TOKEN","XENOVA_REGISTRATION_URL")]}))\n')
            command.chmod(0o755)
            with patch.dict(os.environ,{'PATH':str(root/'bin')+':'+os.environ['PATH'],
                                        'VM_REGISTRATION_SERVICE_TOKEN':'fixture-must-not-inherit',
                                        'XENOVA_REGISTRATION_URL':'https://fixture.invalid'}):
                result=hook.automatic(root/'data',root/'run',{'uuid':VM},wait_ready=True,api_env=env,token_file=root/'token')
            self.assertEqual(result['state'],'attempt-started')
            end=time.monotonic()+3
            submitted=root/'bin/submitted.json'
            while not submitted.exists() and time.monotonic()<end:time.sleep(.02)
            value=json.loads(submitted.read_text());args=value['args']
            self.assertEqual(value['leaked'],[])
            self.assertIn('--no-block',args);self.assertIn('--collect',args)
            self.assertIn('--property=After=xenova-dom0-manager.service',args)
            self.assertIn('--property=Requires=xenova-dom0-manager.service',args)
            self.assertIn('--property=Restart=no',args)
            # 이 실행 인자에는 최초 등록 시점의 원격 호출 deadline이 없다.
            index=args.index('after-api')
            self.assertEqual(args[index+1:],[str(root/'data'),str(root/'run'),str(env),str(root/'token'),VM])
            self.assertFalse((root/'data/registration-notices').exists())

    def test_ready_unit_is_after_autostart_and_boot_does_not_wait_for_api(self):
        files=ROOT/'platform/yocto/meta-xenova/recipes-core'
        def unit(relative):
            parser=configparser.ConfigParser(interpolation=None,strict=False);parser.read(files/relative);return parser
        api=unit('xenova-dom0-manager/files/xenova-dom0-manager.service')
        first=unit('xenova-platform/files/xenova-first-boot.service')
        admission=unit('xenova-platform/files/xenova-host-admission.service')
        self.assertEqual(api['Service']['Type'],'notify');self.assertEqual(api['Service']['NotifyAccess'],'main')
        self.assertIn('xenova-host-admission.service',api['Unit']['After'].split())
        self.assertIn('xenova-vms.service',admission['Unit']['After'].split())
        self.assertNotIn(hook.API_UNIT,dict(first['Unit']).values())
        self.assertNotIn('ExecStartPost',api['Service'])


if __name__=='__main__':unittest.main()
