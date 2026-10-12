"""Ejecuta el codigo real del workflow con evento y HTTP sinteticos."""
import json
import os
from pathlib import Path
import tempfile
import textwrap
import unittest
from unittest.mock import patch


class RepositoryPolicyTests(unittest.TestCase):
    def run_policy(self, author_id):
        workflow = Path(__file__).resolve().parents[1] / '.github/workflows/owner-pr.yml'
        code = workflow.read_text(encoding='utf-8').split("          python - <<'PY'\n", 1)[1]
        code = textwrap.dedent(code.split('          PY\n', 1)[0])
        sha = 'a' * 40
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / 'event.json'
            # El login por si solo nunca autoriza a otro ID.
            event.write_text(json.dumps({'pull_request': {
                'number': 7,
                'user': {'id': author_id, 'login': 'JeremiasBadilla'},
                'head': {'sha': sha}}}), encoding='utf-8')
            with patch.dict(os.environ, {
                    'GITHUB_EVENT_PATH': str(event), 'GITHUB_REPOSITORY': 'FourBis/4bis-racing-engineer',
                    'GITHUB_TOKEN': 'SYNTHETIC_TEST_TOKEN'}), patch('urllib.request.urlopen') as http:
                http.return_value.__enter__.return_value.status = 201 if author_id == 66529405 else 200
                if author_id == 66529405:
                    exec(compile(code, str(workflow), 'exec'), {})
                else:
                    with self.assertRaises(SystemExit):
                        exec(compile(code, str(workflow), 'exec'), {})
                request = http.call_args.args[0]
                http.assert_called_once()
                endpoint = 'statuses/' + sha if author_id == 66529405 else 'pulls/7'
                self.assertEqual('https://api.github.com/repos/FourBis/4bis-racing-engineer/' + endpoint,
                                 request.full_url)
                self.assertEqual('POST' if author_id == 66529405 else 'PATCH', request.method)
                return json.loads(request.data)

    def test_owner_pr_posts_success_on_exact_head_sha(self):
        result = self.run_policy(66529405)
        self.assertEqual(('owner-pr', 'success'), (result['context'], result['state']))

    def test_other_author_sharing_owner_sha_is_closed_without_overwriting_its_status(self):
        result = self.run_policy(1)
        self.assertEqual({'state': 'closed'}, result)
