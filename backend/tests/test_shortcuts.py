import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import shortcuts


class ShortcutTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.object(main, 'DB_PATH', Path(directory) / 'reading.sqlite3'))
        self.client = self.stack.enter_context(TestClient(main.app))

    def read(self):
        response = self.client.get('/api/shortcuts')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def draft(self, **changes):
        return dict(name='My service', description='Useful service', aliases=['service'], icon='Server',
                    color='#a4b9e4', action='open', value='http://10.0.0.149:9000', **changes)

    def create(self, changes=None):
        data = self.draft()
        data.update(changes or {})
        data['revision'] = self.read()['revision']
        return self.client.post('/api/shortcuts', json=data)

    def test_migration_preserves_all_seven_current_shortcuts(self):
        data = self.read()
        expected = json.loads(Path(shortcuts.__file__).with_name('shortcut_defaults.json').read_text(encoding='utf-8'))
        expected = [dict(item, group='', monitor=None) for item in expected]
        self.assertEqual(data, {'items': expected, 'revision': 0})
        self.assertEqual(data['items'][0]['value'], 'https://dsm.dledwith.com')
        self.assertEqual(data['items'][2]['value'], 'https://qbit.dledwith.com')
        self.assertEqual(data['items'][4]['action'], 'copy')

    def test_edits_survive_reinitialization_without_reseeding(self):
        data = self.read(); item = data['items'][2]
        item.pop('id'); item['name'] = 'My downloads'; item['revision'] = data['revision']
        response = self.client.put('/api/shortcuts/qbit', json=item)
        self.assertEqual(response.status_code, 200, response.text)
        shortcuts.initialize_shortcuts()
        self.assertEqual(self.read()['items'][2]['name'], 'My downloads')
        self.assertEqual(self.read()['revision'], 1)

    def test_create_edit_delete_and_alias_normalization(self):
        response = self.create({'name': '  Test  ', 'aliases': [' NAS ', 'nas', '', 'Test'], 'color': '#A4B9E4'})
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json(); item = data['items'][-1]; item_id = item.pop('id')
        self.assertEqual(item['aliases'], ['nas', 'test'])
        self.assertEqual(item['name'], 'Test'); self.assertEqual(item['color'], '#a4b9e4')
        item.update(name='Changed', revision=data['revision'])
        updated = self.client.put(f'/api/shortcuts/{item_id}', json=item)
        self.assertEqual(updated.status_code, 200, updated.text)
        deleted = self.client.delete(f'/api/shortcuts/{item_id}', params={'revision': updated.json()['revision']})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(len(deleted.json()['items']), 7)

    def test_deleting_every_shortcut_stays_empty_after_restart(self):
        data = self.read()
        for item in data['items'][:]:
            response = self.client.delete(f"/api/shortcuts/{item['id']}", params={'revision': data['revision']})
            self.assertEqual(response.status_code, 200)
            data = response.json()
        shortcuts.initialize_shortcuts()
        self.assertEqual(self.read()['items'], [])
        with TestClient(main.app) as other:
            self.assertEqual(other.get('/api/shortcuts').json()['items'], [])

    def test_copy_action_preserves_multiline_text_and_is_never_a_website(self):
        response = self.create({'action': 'copy', 'value': 'echo hello\necho world'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['items'][-1]['value'], 'echo hello\necho world')

    def test_private_and_local_website_urls_are_supported(self):
        for url in ['http://localhost:9000', 'http://10.0.0.149:8888/', 'https://nas.tailnet.ts.net', 'http://[::1]:8080']:
            with self.subTest(url=url): self.assertEqual(self.create({'value': url}).status_code, 201)

    def test_invalid_fields_cannot_change_saved_data(self):
        baseline = self.read()
        for changes in [dict(name='  '), dict(icon='MissingIcon'), dict(color='red'), dict(color='#abc'),
                        dict(action='execute'), dict(value='javascript:alert(1)'), dict(value='file:///etc/passwd'),
                        dict(value='https://user:secret@example.com'), dict(value='https://example.com\\@evil.com'),
                        dict(value='https://example.com:bad'), dict(value='https://'), dict(value='example.com'),
                        dict(value='https://bad<host>'), dict(aliases=['x'*81]), dict(value='')]:
            with self.subTest(changes=changes): self.assertEqual(self.create(changes).status_code, 422)
        self.assertEqual(self.read(), baseline)

    def test_reordering_is_atomic_persistent_and_validates_the_complete_list(self):
        before = self.read(); ids = [item['id'] for item in before['items']]
        for invalid in [ids[:-1], ids+[ids[0]], ['unknown']+ids[1:]]:
            response = self.client.put('/api/shortcuts/order', json={'ids': invalid, 'revision': before['revision']})
            self.assertEqual(response.status_code, 422)
            self.assertEqual(self.read(), before)
        response = self.client.put('/api/shortcuts/order', json={'ids': ids[::-1], 'revision': before['revision']})
        self.assertEqual(response.status_code, 200)
        shortcuts.initialize_shortcuts()
        self.assertEqual([item['id'] for item in self.read()['items']], ids[::-1])

    def test_stale_creates_updates_deletes_and_reorders_do_not_overwrite_newer_work(self):
        before = self.read(); self.assertEqual(self.create().status_code, 201); current = self.read()
        data = self.draft(); data['revision'] = before['revision']
        responses = [self.client.post('/api/shortcuts', json=data), self.client.put('/api/shortcuts/qbit', json=data),
                     self.client.delete('/api/shortcuts/qbit', params={'revision': before['revision']}),
                     self.client.put('/api/shortcuts/order', json={'ids': [item['id'] for item in before['items']], 'revision': before['revision']})]
        for response in responses: self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.read(), current)

    def test_concurrent_device_saves_allow_only_one_matching_revision(self):
        data = self.draft(); data['revision'] = self.read()['revision']
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda _: self.client.post('/api/shortcuts', json=data).status_code, range(2)))
        self.assertEqual(sorted(statuses), [201, 409])
        self.assertEqual(len(self.read()['items']), 8)

    def test_missing_shortcuts_and_missing_revision_fail_without_updates(self):
        data = self.draft(); data['revision'] = 0
        self.assertEqual(self.client.put('/api/shortcuts/missing', json=data).status_code, 404)
        self.assertEqual(self.client.delete('/api/shortcuts/missing', params={'revision': 0}).status_code, 404)
        self.assertEqual(self.client.delete('/api/shortcuts/qbit').status_code, 422)
        self.assertEqual(self.read()['revision'], 0)


    def test_group_and_compose_monitor_link_survive_edits_and_reinitialization(self):
        target = {'kind':'container','watcher':'local','project':'media','service':'plex','container':None}
        created = self.create({'group':'  Media  ', 'monitor':target})
        self.assertEqual(created.status_code,201,created.text)
        saved=created.json()['items'][-1]
        self.assertEqual(saved['group'],'Media');self.assertEqual(saved['monitor'],target)
        shortcuts.initialize_shortcuts()
        self.assertEqual(self.read()['items'][-1],saved)
        canonical=self.create({'group':'media'})
        self.assertEqual(canonical.json()['items'][-1]['group'],'Media')

    def test_migration_of_existing_database_preserves_custom_names_order_and_revision(self):
        with main.database() as db:
            db.execute("UPDATE shortcuts SET name = 'My downloads', position = -1 WHERE id = 'qbit'")
            db.execute("UPDATE shortcut_settings SET revision = 8")
            db.execute("ALTER TABLE shortcuts DROP COLUMN group_name")
            db.execute("ALTER TABLE shortcuts DROP COLUMN monitor")
        shortcuts.initialize_shortcuts()
        data=self.read()
        self.assertEqual(data['revision'],8)
        self.assertEqual(data['items'][0]['name'],'My downloads')
        self.assertTrue(all(item['group']=='' and item['monitor'] is None for item in data['items']))
        self.assertEqual(len(data['items']),7)

    def test_older_client_update_keeps_organization_and_copy_conversion_clears_monitoring(self):
        target={'kind':'project','watcher':'local','project':'media'}
        data=self.create({'group':'Media','monitor':target}).json()
        saved=data['items'][-1]
        old_draft=self.draft();old_draft.update(name='Renamed by older client',revision=data['revision'])
        response=self.client.put('/api/shortcuts/'+saved['id'],json=old_draft)
        self.assertEqual(response.status_code,200,response.text)
        latest=response.json()['items'][-1]
        self.assertEqual(latest['group'],'Media');self.assertEqual(latest['monitor']['project'],'media')
        old_draft.update(action='copy',value='ssh nas',revision=response.json()['revision'])
        copied=self.client.put('/api/shortcuts/'+saved['id'],json=old_draft)
        self.assertEqual(copied.status_code,200,copied.text)
        self.assertIsNone(copied.json()['items'][-1]['monitor'])
        self.assertEqual(copied.json()['items'][-1]['group'],'Media')

    def test_rename_and_remove_group_are_revisioned_and_keep_shortcuts(self):
        self.create({'group':'Media'});self.create({'group':'Media'});self.create({'group':'Tools'})
        before=self.read()
        renamed=self.client.put('/api/shortcuts/groups',json={'name':'Media','new_name':'Entertainment','revision':before['revision']})
        self.assertEqual(renamed.status_code,200,renamed.text)
        self.assertEqual(sum(item['group']=='Entertainment' for item in renamed.json()['items']),2)
        stale=self.client.put('/api/shortcuts/groups',json={'name':'Entertainment','new_name':'Video','revision':before['revision']})
        self.assertEqual(stale.status_code,409)
        collision=self.client.put('/api/shortcuts/groups',json={'name':'Entertainment','new_name':'tools','revision':renamed.json()['revision']})
        self.assertEqual(collision.status_code,409)
        removed=self.client.put('/api/shortcuts/groups',json={'name':'Entertainment','new_name':'','revision':renamed.json()['revision']})
        self.assertEqual(removed.status_code,200,removed.text)
        self.assertEqual(len(removed.json()['items']),len(before['items']))
        self.assertFalse(any(item['group']=='Entertainment' for item in removed.json()['items']))

    def test_invalid_groups_and_monitor_targets_cannot_change_data(self):
        before=self.read()
        for changes in [
            {'group':'x'*49}, {'group':'Media\nTools'},
            {'monitor':{'kind':'project','watcher':'local','project':'media','service':'plex'}},
            {'monitor':{'kind':'container','watcher':'local','service':'plex'}},
            {'monitor':{'kind':'container','watcher':'local'}},
            {'monitor':{'kind':'container','watcher':'local','container':'plex','extra':'unsupported'}},
            {'monitor':{'kind':'project','watcher':''}},
            {'action':'copy','monitor':{'kind':'project','watcher':'local','project':'media'}},
        ]:
            with self.subTest(changes=changes): self.assertEqual(self.create(changes).status_code,422)
        self.assertEqual(self.read(),before)


if __name__ == '__main__': unittest.main()
