"""Publication gates must reject bad payloads and retain documented boundaries."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from audit_publication import audit
from build_release import source_files


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kps-publication-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for folder in ('docs', 'tests', 'tools', '.github', 'kps_core'):
            (self.repo / folder).mkdir()
        for name in ('LICENSE', 'README.md', 'README.en.md', 'CONTRIBUTING.md',
                     'THIRD-PARTY-NOTICES.md', 'requirements-build.txt', '.gitignore',
                     'docs/RELEASE.zh-CN.md', 'docs/RELEASE.en.md'):
            (self.repo / name).write_text('9.0.0-test\n', encoding='utf-8')
        (self.repo / 'keil_port_tool.py').write_text("TOOL_VERSION = '9.0.0-test'\n", encoding='utf-8')

    def test_working_publication_passes(self):
        self.assertTrue(audit(ROOT)['passed'])

    def test_private_directories_are_not_published(self):
        for folder in ('hardware_tests', 'releases', '.git', '.venv'):
            (self.repo / folder).mkdir()
            (self.repo / folder / 'private.txt').write_text('private', encoding='utf-8')
        (self.repo / 'tests/__pycache__').mkdir()
        (self.repo / 'tests/__pycache__/cache.pyc').write_bytes(b'cache')
        names = [p.relative_to(self.repo).as_posix() for p in source_files(self.repo)]
        self.assertFalse(any('private' in name or '__pycache__' in name for name in names))
        self.assertTrue(audit(self.repo)['passed'])

    def test_secret_and_private_path_report_locations_not_values(self):
        secret = 'ghp' + '_' + 'Z' * 36
        private = 'C:' + '/' + 'Users' + '/FixtureOwner/project'
        (self.repo / 'docs/bad.md').write_text(secret + '\n' + private, encoding='utf-8')
        result = audit(self.repo)
        self.assertFalse(result['passed'])
        kinds = {issue['kind'] for issue in result['issues']}
        self.assertIn('github_token', kinds)
        self.assertIn('private_user_path', kinds)
        self.assertNotIn(secret, str(result))
        self.assertNotIn(private, str(result))

    def test_broken_local_link_fails_but_web_and_anchor_are_allowed(self):
        page = self.repo / 'docs/link.md'
        page.write_text('[web](https://example.invalid) [anchor](#local)', encoding='utf-8')
        self.assertTrue(audit(self.repo)['passed'])
        page.write_text('[missing](missing.md)', encoding='utf-8')
        self.assertIn('missing_local_link', [v['kind'] for v in audit(self.repo)['issues']])

    def test_unexpected_binary_inside_allowed_folder_fails(self):
        (self.repo / 'tests/accidental.exe').write_bytes(b'not a real executable')
        self.assertIn('unexpected_file_type', [v['kind'] for v in audit(self.repo)['issues']])

    def test_document_version_mismatch_fails(self):
        (self.repo / 'README.md').write_text('old version', encoding='utf-8')
        self.assertIn('version_not_documented', [v['kind'] for v in audit(self.repo)['issues']])


if __name__ == '__main__':
    unittest.main(verbosity=2)
