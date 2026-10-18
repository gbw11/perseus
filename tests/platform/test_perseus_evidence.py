"""실패/빈 SSH 출력으로 이전 비교 파일을 덮어쓰거나 데이터 손실을 판정하지 않는다."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_perseus_platform import ROOT, load

evidence = load('perseus_guest_evidence_test', ROOT / 'scripts/collect-perseus-guest-evidence.py')


class EvidenceTest(unittest.TestCase):
    def test_failure_empty_malformed_and_incomplete_output_preserve_prior_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'before.json'
            target.write_text('prior valid evidence')
            for status, content in ((255, b''), (0, b''), (0, b'not-json'), (0, b'{}'), (0, b'null'), (0, b'[]')):
                def command(args, **kw):
                    kw['stdout'].write(content)
                    return subprocess.CompletedProcess(args, status, stderr=b'failure')
                with self.subTest(status=status, content=content), self.assertRaises(ValueError):
                    evidence.collect('root@192.168.77.10', target, command=command)
                self.assertEqual(target.read_text(), 'prior valid evidence')

    def test_success_publishes_only_valid_complete_output_and_keeps_guest_path_as_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'after.json'
            guest_file = '/root/perseus proof; literal.txt'
            result = {'schema': 1, 'machine_id': '1' * 32, 'ssh_public_keys': {'key.pub': '2' * 64},
                      'files': {guest_file: '3' * 64}}
            def command(args, **kw):
                self.assertIn('--', args)
                self.assertIn("'/root/perseus proof; literal.txt'", args[-1])
                kw['stdout'].write(json.dumps(result).encode())
                return subprocess.CompletedProcess(args, 0, stderr=b'')
            self.assertEqual(evidence.collect('root@192.168.77.10', target, [guest_file], command), result)
            self.assertEqual(json.loads(target.read_text()), result)

    def test_timeout_preserves_prior_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'before.json'; target.write_text('keep')
            def command(args, **kw): raise subprocess.TimeoutExpired(args, 45)
            with self.assertRaises(subprocess.TimeoutExpired):
                evidence.collect('root@192.168.77.10', target, command=command)
            self.assertEqual(target.read_text(), 'keep')
