"""#33–#42 的隔离边界检查；只使用临时文件和模拟密钥。"""
import json
import os
import sys
import tempfile
import time
import traceback
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(tempfile.gettempdir())
sys.path.insert(0, str(ROOT))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['LLM_CABINET_DISABLE_KEYRING'] = '1'
from app.db import connect
from app.models import Project, FileItem
from app.repository import Repository
from app.llm import config
from app.library_check import backup_library

results = []
def check(name, condition, detail=''):
    result = dict(name=name, passed=bool(condition), detail=str(detail))
    results.append(result)
    print(('PASS ' if condition else 'FAIL ') + name + ': ' + str(detail), flush=True)

def repository(root):
    root.mkdir(parents=True, exist_ok=True)
    repo = Repository(connect(root / 'cabinet.db'))
    repo.set_setting('library_root', str(root))
    return repo

class MemoryKeys:
    def __init__(self, reject=False):
        self.data = {}
        self.reject = reject
    def get_keyring(self):
        return self
    def set_password(self, service, user, key):
        if self.reject:
            raise RuntimeError('simulated credential backend unavailable')
        self.data[(service, user)] = key
    def get_password(self, service, user):
        return self.data.get((service, user))
    def delete_password(self, service, user):
        self.data.pop((service, user), None)

def tags(root):
    cases = [
        ('rename-tree', ['A', 'A/child', 'unrelated'], 'A', 'B', ['B', 'B/child', 'unrelated']),
        ('merge-existing', ['A', 'B'], 'A', 'B', ['B']),
        ('rename-to-own-child', ['A', 'A/child'], 'A', 'A/child', ['A/child', 'A/child/child']),
        ('rename-child-first', ['A/child', 'A'], 'A', 'A/child', ['A/child', 'A/child/child']),
        ('case-sensitive', ['A', 'a/child'], 'A', 'B', ['B', 'a/child']),
        ('literal-underscore', ['A_', 'AX/leaf'], 'A_', 'B', ['B', 'AX/leaf']),
        ('literal-percent', ['A%', 'AX/leaf'], 'A%', 'B', ['B', 'AX/leaf']),
    ]
    for name, initial, old, new, expected in cases:
        repo = repository(root / name)
        try:
            pid = repo.save_project(Project(title=name, tags=initial))
            repo.rename_tag(old, new)
            actual = sorted(repo.get_project(pid).tags)
            check('tag/' + name, actual == sorted(expected), f'expected={sorted(expected)}, actual={actual}')
        finally:
            repo.conn.close()
    repo = repository(root / 'delete-literal')
    try:
        a = repo.save_project(Project(title='target', tags=['A_']))
        b = repo.save_project(Project(title='untouched', tags=['AX/leaf']))
        check('tag/count-literal', repo.count_projects_with_tag('A_') == 1, repo.count_projects_with_tag('A_'))
        repo.remove_tag_everywhere('A_')
        check('tag/delete-preserves-unrelated', repo.get_project(b).tags == ['AX/leaf'], repo.get_project(b).tags)
    finally:
        repo.conn.close()

def keys(root):
    repo = repository(root / 'keys')
    dst = repository(root / 'keys-dst')
    mock = MemoryKeys()
    token = 'verification-only-not-a-real-key-20260907'
    try:
        with patch.object(config, '_kr', return_value=mock):
            cfg = config.load_config(repo)
            cfg.providers['deepseek'].api_key = token
            config.save_config(repo, cfg)
            check('key/sentinel', token not in repo.get_setting('llm_config', ''))
            check('key/roundtrip', config.load_config(repo).providers['deepseek'].api_key == token)
            dst.set_setting('llm_config', repo.get_setting('llm_config', ''))
            check('key/library-isolation', config.load_config(dst).providers['deepseek'].api_key == '')
            check('key/import-scope', config.rekey_imported_llm_config(dst, root / 'keys') == (1, 0))
            check('key/import-read', config.load_config(dst).providers['deepseek'].api_key == token)
            cfg.providers['deepseek'].api_key = ''
            config.save_config(repo, cfg)
            check('key/clear', config.load_config(repo).providers['deepseek'].api_key == '')
        with patch.object(config, '_kr', return_value=MemoryKeys(reject=True)):
            cfg.providers['deepseek'].api_key = token
            config.save_config(repo, cfg)
            check('key/failure-notice', '明文' in config.key_storage_notice(repo))
            check('key/failure-detected-by-ui', not config.keyring_available(), 'backend rejects writes; availability=' + str(config.keyring_available()))
            (root / 'keys' / 'cabinet.v1.bak').write_bytes(token.encode())
            repo.wal_checkpoint()
            archive = backup_library(root / 'keys', root / 'key-backup.zip')
            with zipfile.ZipFile(archive) as z:
                found = any(token.encode() in z.read(n) for n in z.namelist() if not n.endswith('/'))
                check('key/excludes-history', not any(n.endswith(('-wal', '-shm', '.bak')) for n in z.namelist()))
            check('key/fallback-backup-excludes-key', not found, f'fake key present in backup={found}')
            check('key/backup-preserves-source', token in repo.get_setting('llm_config', ''))
            from app.library_check import restore_library
            restored = restore_library(archive, root / 'restored')
            restored_repo = repository(restored)
            try:
                data = json.loads(restored_repo.get_setting('llm_config', ''))
                check('key/restore-empty', data['providers']['deepseek']['api_key'] == '')
                check('key/restore-model', data['providers']['deepseek']['model'] == cfg.providers['deepseek'].model)
                check('key/restore-integrity', restored_repo.conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok')
            finally:
                restored_repo.conn.close()
        with patch.object(config, '_kr', return_value=mock):
            migrated = config.load_config(repo)
            check('key/plaintext-migration', migrated.providers['deepseek'].api_key == token and token not in repo.get_setting('llm_config', ''))
            check('key/recovered-notice', '系统凭据引用' in config.key_storage_notice(repo))
            repo.wal_checkpoint()
            archive = backup_library(root / 'keys', root / 'migrated-backup.zip')
            with zipfile.ZipFile(archive) as z:
                exposed = [n for n in z.namelist() if not n.endswith('/') and token.encode() in z.read(n)]
            check('key/migrated-backup-excludes-key', not exposed, exposed)
        repo.set_setting('llm_config', '{"broken":"' + token)
        archive = backup_library(root / 'keys', root / 'broken-backup.zip')
        with zipfile.ZipFile(archive) as z:
            check('key/malformed-excluded', not any(token.encode() in z.read(n) for n in z.namelist()))
    finally:
        repo.conn.close()
        dst.conn.close()

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='cabinet-security-') as td:
        tags(Path(td))
        keys(Path(td))
    print(f'PASSED: {sum(r["passed"] for r in results)}')
    print(f'FAILED: {sum(not r["passed"] for r in results)}')
    sys.exit(int(any(not r['passed'] for r in results)))
