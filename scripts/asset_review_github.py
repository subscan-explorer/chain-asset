#!/usr/bin/env python3
"""Use a repository-scoped App token without exposing it to shell or npm.

Normal read: python3 ~/.config/chain-asset/github_api.py rest /repos/subscan-explorer/chain-asset
GraphQL: python3 ~/.config/chain-asset/github_api.py graphql --input request.json
The adjacent github-app.json and mounted PEM provide the existing configuration.
"""
import argparse
import base64
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

REPO = 'subscan-explorer/chain-asset'
PERMISSIONS = {'contents': 'write', 'issues': 'write', 'metadata': 'read', 'pull_requests': 'write'}
MAX_BYTES = 8 * 1024 * 1024


class Failure(Exception):
    """Contain safe diagnostics, never tokens, request headers or response bodies."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep authenticated requests on the exact GitHub API endpoint."""

    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def api(path, method, token, body=None):
    """Call GitHub with a bounded response and no credential-bearing redirects."""
    parsed = urllib.parse.urlsplit(path)
    if (parsed.scheme or parsed.netloc or parsed.fragment or not path.startswith('/')
            or path.startswith('//') or '\\' in path):
        raise Failure('GitHub request must use an API-relative path.')
    request = urllib.request.Request('https://api.github.com' + path,
        data=None if body is None else json.dumps(body).encode(), method=method,
        headers={'Authorization': 'Bearer ' + token,
                 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json',
                 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with OPENER.open(request, timeout=30) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise Failure('GitHub response exceeds the bounded size limit.')
        value = json.loads(raw) if raw else {}
    except urllib.error.HTTPError as error:
        raise Failure(f'GitHub request failed with HTTP {error.code}; response omitted.') from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise Failure('GitHub transport or JSON decoding failed; inspect the operation before retrying.') from None
    if path == '/graphql' and isinstance(value, dict) and value.get('errors'):
        raise Failure('GitHub GraphQL returned errors; no successful operation is established.')
    return value


def validate_target(path, method):
    """Reject out-of-scope paths before loading any credentials."""
    target = urllib.parse.urlsplit(path)
    if (target.scheme or target.netloc or target.fragment or
            any(part in {'.', '..'} for part in urllib.parse.unquote(target.path).split('/'))):
        raise Failure('GitHub request is outside the configured repository.')
    root = '/repos/' + REPO
    if not (target.path == root or target.path.startswith(root + '/') or
            (target.path == '/installation/repositories' and method == 'GET') or
            (target.path == '/graphql' and method == 'POST')):
        raise Failure('GitHub request is outside the configured repository.')


def installation_token(config):
    """Mint only chain-asset permissions; keep the token in this process."""
    if config.get('repositories') != ['chain-asset'] or config.get('permissions') != PERMISSIONS:
        raise Failure('App configuration must name only chain-asset with its exact review permissions.')
    if config.get('owner') != 'subscan-explorer':
        raise Failure('App configuration owner must be subscan-explorer.')
    def encode(value):
        return base64.urlsafe_b64encode(value).rstrip(b'=')
    now = int(time.time())
    data = b'.'.join([encode(b'{"alg":"RS256","typ":"JWT"}'),
        encode(json.dumps({'iat': now - 60, 'exp': now + 540, 'iss': str(config['app_id'])}).encode())])
    try:
        signed = subprocess.run(['openssl', 'dgst', '-sha256', '-sign',
            str(Path(config['private_key_file']).expanduser())], input=data,
            capture_output=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise Failure('App signing could not run; credential output suppressed.') from None
    if signed.returncode:
        raise Failure('App signing failed; credential output suppressed.')
    jwt = (data + b'.' + encode(signed.stdout)).decode()
    value = api('/app/installations/' + str(config['installation_id']) + '/access_tokens',
                'POST', jwt, {'repositories': ['chain-asset'], 'permissions': PERMISSIONS})
    if value.get('permissions') != PERMISSIONS:
        raise Failure('GitHub returned unexpected installation permissions.')
    if ('repositories' in value and
            [repo.get('full_name') for repo in value['repositories']] != [REPO]):
        raise Failure('GitHub returned unexpected repository access.')
    if not value.get('token'):
        raise Failure('GitHub did not return an installation token.')
    return value['token']


def main():
    """Default credentials to adjacent config; accept mutation payloads by file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['rest', 'graphql'])
    parser.add_argument('path', nargs='?')
    parser.add_argument('--method', choices=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], default='GET')
    parser.add_argument('--input', type=Path)
    args = parser.parse_args()
    config = json.loads(Path(__file__).with_name('github-app.json').read_text())
    body = json.loads(args.input.read_text()) if args.input else None
    path, method = ('/graphql', 'POST') if args.mode == 'graphql' else (args.path, args.method)
    if not path or (args.mode == 'graphql' and not isinstance(body, dict)):
        raise Failure('Provide a REST path or a GraphQL JSON input file.')
    validate_target(path, method)
    value = api(path, method, installation_token(config), body)
    json.dump(value, sys.stdout)
    sys.stdout.write('\n')


if __name__ == '__main__':
    try:
        main()
    except (Failure, OSError, KeyError, ValueError) as error:
        if isinstance(error, Failure):
            print(str(error), file=sys.stderr)
        else:
            print('GitHub helper configuration or input is unavailable; details suppressed.', file=sys.stderr)
        sys.exit(1)
