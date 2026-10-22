import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/windows-desktop-pet/scripts/audit_action_graph.py'
spec = importlib.util.spec_from_file_location('action_graph', SCRIPT)
graph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graph)


class ActionGraphTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.gettempdir()).resolve()
        self.manifest = {'initial_state': 'stand', 'actions': [
            {'asset_id': 'sit', 'start_state': 'stand', 'end_state': 'sit', 'kind': 'transition'},
            {'asset_id': 'rise', 'start_state': 'sit', 'end_state': 'stand', 'kind': 'transition'}],
            'routes': [{'start_state': 'stand', 'end_state': 'stand', 'actions': ['sit', 'rise']}]}

    def test_complete_return_route(self):
        self.assertTrue(graph.audit(self.manifest, self.root)['ok'])

    def test_reject_reverse_order_without_reverse_asset(self):
        self.manifest['routes'][0]['actions'].reverse()
        self.assertFalse(graph.audit(self.manifest, self.root)['ok'])

    def test_reject_implicit_return(self):
        self.manifest['routes'][0]['actions'] = ['sit']
        self.assertFalse(graph.audit(self.manifest, self.root)['ok'])

    def test_reject_duplicate(self):
        self.manifest['actions'].append(copy.deepcopy(self.manifest['actions'][0]))
        self.assertFalse(graph.audit(self.manifest, self.root)['ok'])

    def test_loop_cannot_change_posture(self):
        self.manifest['actions'][0]['kind'] = 'loop'
        self.assertFalse(graph.audit(self.manifest, self.root)['ok'])

    def test_nonportable_paths(self):
        for value in ('../escape', str(self.root), 'C:drive-relative'):
            with self.subTest(value=value):
                self.manifest['actions'][0]['path'] = value
                self.assertFalse(graph.audit(self.manifest, self.root, portable=True)['ok'])

    def test_missing_file(self):
        self.manifest['actions'][0]['path'] = 'file-that-does-not-exist-for-pet-test'
        self.assertFalse(graph.audit(self.manifest, self.root, check_files=True)['ok'])


if __name__ == '__main__':
    unittest.main()
