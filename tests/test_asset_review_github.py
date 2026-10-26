"""Verify the App client's credential and repository boundaries."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch
import urllib.error

spec = importlib.util.spec_from_file_location('asset_github',
    Path(__file__).parents[1] / 'scripts' / 'asset_review_github.py')
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


class ClientTests(unittest.TestCase):
    def test_api_targets_are_repository_scoped(self):
        for path in ('/repos/' + client.REPO, '/repos/' + client.REPO + '/issues?per_page=100',
                     '/installation/repositories'):
            client.validate_target(path, 'GET')
        client.validate_target('/graphql', 'POST')
        for path, method in [('https://api.github.com/repos/' + client.REPO, 'GET'),
                ('//attacker.example/repos/' + client.REPO, 'GET'),
                ('/repos/subscan-explorer/subscan', 'GET'),
                ('/repos/' + client.REPO + '/%2e%2e/other', 'GET'),
                ('/installation/repositories', 'POST'), ('/graphql', 'GET'), ('/user', 'GET')]:
            with self.subTest(path=path), self.assertRaises(client.Failure):
                client.validate_target(path, method)

    def test_unscoped_app_configuration_never_reads_the_key(self):
        config = {'owner': 'subscan-explorer', 'repositories': ['chain-asset', 'subscan'],
                  'permissions': client.PERMISSIONS}
        with patch.object(client.subprocess, 'run') as signing, self.assertRaises(client.Failure):
            client.installation_token(config)
        signing.assert_not_called()

    def test_redirect_failure_does_not_expose_authentication(self):
        failure = urllib.error.HTTPError('https://api.github.com', 302, 'Found',
                                        {'Location': 'https://attacker.example'}, None)
        with patch.object(client.OPENER, 'open', side_effect=failure), self.assertRaises(client.Failure) as error:
            client.api('/repos/' + client.REPO, 'GET', 'private-token')
        self.assertNotIn('private-token', str(error.exception))
        self.assertNotIn('attacker.example', str(error.exception))
        self.assertIsNone(client.NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_graphql_errors_do_not_count_as_success(self):
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(json.dumps({'errors': [{'message': 'denied'}]}).encode())
        with patch.object(client.OPENER, 'open', return_value=response), self.assertRaises(client.Failure):
            client.api('/graphql', 'POST', 'private-token', {'query': '{viewer{login}}'})

    def test_response_size_is_bounded(self):
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b' ' * (client.MAX_BYTES + 1))
        with patch.object(client.OPENER, 'open', return_value=response), self.assertRaises(client.Failure):
            client.api('/repos/' + client.REPO, 'GET', 'private-token')


if __name__ == '__main__':
    unittest.main()
