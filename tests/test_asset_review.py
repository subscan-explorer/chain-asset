import copy
import importlib.util
import os
import http.client
import io
import json
import tempfile
import urllib.error
import zipfile
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location('asset_review', Path(__file__).parents[1] / 'scripts' / 'asset_review.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class TriggerTests(unittest.TestCase):
    def setUp(self):
        self.issue = {'number': 9, 'title': 'Token', 'body': '### Subscan Link\nhttps://peaq.subscan.io/token/0x123\n### Icon/Logo',
                      'state': 'open', 'user': {'id': 11, 'login': 'owner', 'type': 'User'}, 'labels': []}
        self.event = {'issue': self.issue, 'sender': {'id': 11, 'login': 'owner', 'type': 'User'},
                      'action': 'created', 'comment': {'id': 1, 'body': '/asset-review', 'user': {'id': 11, 'login': 'owner'}}}

    def test_only_owner_can_request_comment_review(self):
        self.assertTrue(review.owner_event(self.event, 'issue_comment', 'reviewer'))
        for attacker in ({'id': 12, 'login': 'other'},
                         {'id': 11, 'login': 'owner', 'type': 'Bot'}):
            with self.subTest(attacker=attacker):
                event = copy.deepcopy(self.event)
                event['sender'] = attacker
                self.assertFalse(review.owner_event(event, 'issue_comment', 'reviewer'))

    def test_current_app_and_configured_historical_reviewer_are_valid(self):
        with patch.dict(os.environ, {'LEGACY_REVIEWER_LOGINS': '["freehere107"]'}):
            self.assertEqual(review.reviewer_logins('subscan-aip-research[bot]'),
                             {'subscan-aip-research[bot]', 'freehere107'})
        for value in ('"freehere107"', '["attacker,other"]', '[null]', 'not-json'):
            with patch.dict(os.environ, {'LEGACY_REVIEWER_LOGINS': value}), self.assertRaises(review.Failure):
                review.reviewer_logins('subscan-aip-research[bot]')

    def test_migration_preserves_fingerprint_and_filters_legacy_feedback(self):
        legacy = copy.deepcopy(self.event['comment'])
        self.issue['user']['login'] = 'freehere107'
        self.event['sender']['login'] = 'freehere107'
        legacy['user']['login'] = 'freehere107'
        legacy['body'] += '\n' + review.MARKER + 'input-sha256=old outcome=needs-info -->'
        self.event['comment'] = legacy
        with patch.dict(os.environ, {'LEGACY_REVIEWER_LOGINS': '[]'}):
            before = review.fingerprint(self.issue, [legacy], 'freehere107')
        with patch.dict(os.environ, {'LEGACY_REVIEWER_LOGINS': '["freehere107"]'}):
            self.assertEqual(before, review.fingerprint(self.issue, [legacy], 'subscan-aip-research[bot]'))
            self.assertFalse(review.owner_event(self.event, 'issue_comment', 'subscan-aip-research[bot]'))
            forged = copy.deepcopy(legacy)
            forged['user']['login'] = 'other'
            self.assertNotEqual(before, review.fingerprint(self.issue, [forged], 'subscan-aip-research[bot]'))

    def test_scheduled_fallback_skips_cooldown_without_waiting(self):
        self.assertTrue(review.owner_event({}, 'schedule', 'reviewer'))
        with tempfile.TemporaryDirectory() as folder:
            event = Path(folder) / 'event.json'
            event.write_text('{}')
            record = {'started_at': '2026-10-26T00:00:00Z', 'fingerprint': 'old', 'status': 'completed'}
            state = {'version': 1, 'repository': review.REPO, 'issues': {'9': record}}
            with patch.dict(os.environ, {'GITHUB_EVENT_PATH': str(event), 'GITHUB_EVENT_NAME': 'schedule',
                    'REVIEWER_LOGIN': 'reviewer', 'LEGACY_REVIEWER_LOGINS': '[]'}), \
                    patch.object(review, 'load_state', return_value=state), \
                    patch.object(review, 'paginate', side_effect=[[self.issue], []]), \
                    patch.object(review.time, 'time', return_value=review.timestamp(record['started_at']) + 1), \
                    patch.object(review, 'automatic_input') as waiting, \
                    patch.object(review, 'output') as output, patch.object(review, 'summary'):
                review.prepare()
            waiting.assert_not_called()
            output.assert_called_once_with('dispatch', 'false')

    def test_shared_operator_account_can_submit_but_agent_markers_cannot_loop(self):
        self.event['sender']['login'] = 'reviewer'
        self.event['comment']['user']['login'] = 'reviewer'
        self.assertTrue(review.owner_event(self.event, 'issue_comment', 'reviewer'))
        self.event['action'] = 'opened'
        self.assertTrue(review.owner_event(self.event, 'issues', 'reviewer'))
        self.event['comment']['body'] += '\n' + review.MARKER + 'outcome=needs-info -->'
        self.assertFalse(review.owner_event(self.event, 'issue_comment', 'reviewer'))
        self.assertEqual(review.fingerprint(self.issue, [], 'reviewer'),
                         review.fingerprint(self.issue, [self.event['comment']], 'reviewer'))

    def test_other_authors_cannot_edit_owner_command_to_trigger(self):
        self.event['comment']['user']['id'] = 12
        self.assertFalse(review.owner_event(self.event, 'issue_comment', 'reviewer'))

    def test_ordinary_replies_and_quoted_commands_do_not_trigger(self):
        for body in ('thanks', 'Please /asset-review', '> /asset-review', '<!-- /asset-review -->'):
            self.event['comment']['body'] = body
            self.assertFalse(review.owner_event(self.event, 'issue_comment', 'reviewer'))

    def test_owner_issue_edit_allowed_but_pr_and_third_party_edit_rejected(self):
        self.event['action'] = 'edited'
        self.assertTrue(review.owner_event(self.event, 'issues', 'reviewer'))
        self.event['sender']['id'] = 12
        self.assertFalse(review.owner_event(self.event, 'issues', 'reviewer'))
        self.event['sender']['id'] = 11
        self.issue['pull_request'] = {}
        # GitHub sends a nonempty pull_request object for PR comments.
        self.issue['pull_request'] = {'url': 'https://api.github.com/pulls/9'}
        self.assertFalse(review.eligible(self.issue))

    def test_template_without_labels_is_eligible_but_closed_and_unrelated_are_not(self):
        self.assertTrue(review.eligible(self.issue))
        self.issue['state'] = 'closed'
        self.assertFalse(review.eligible(self.issue))
        self.issue.update(state='open', body='please buy my token')
        self.assertFalse(review.eligible(self.issue))

    def test_fingerprint_ignores_chatter_others_and_agent_output(self):
        before = review.fingerprint(self.issue, [], 'reviewer')
        chatter = [{'id': 1, 'body': 'thanks', 'user': {'id': 11, 'login': 'owner'}},
                   {'id': 2, 'body': '/asset-review', 'user': {'id': 12, 'login': 'other'}},
                   {'id': 3, 'body': review.MARKER + 'outcome=needs-info -->', 'user': {'id': 13, 'login': 'reviewer'}}]
        self.assertEqual(before, review.fingerprint(self.issue, chatter, 'reviewer'))
        self.assertNotEqual(before, review.fingerprint(self.issue, [self.event['comment']], 'reviewer'))
        self.issue['body'] += '\nCorrected metadata'
        self.assertNotEqual(before, review.fingerprint(self.issue, [], 'reviewer'))

    def test_per_issue_cooldown_deduplication_and_manual_override(self):
        record = {'started_at': '2026-10-10T10:00:00Z', 'fingerprint': 'a', 'status': 'completed'}
        started = review.timestamp(record['started_at'])
        self.assertFalse(review.should_dispatch(record, 'b', started + 1799, 1800))
        self.assertTrue(review.should_dispatch(record, 'b', started + 1800, 1800))
        self.assertFalse(review.should_dispatch(record, 'a', started + 7200, 1800))
        self.assertTrue(review.should_dispatch(record, 'a', started + 1, 1800, True))

    def test_read_only_review_does_not_permanently_suppress_publication(self):
        record = {'started_at': '2026-10-10T10:00:00Z', 'fingerprint': 'a',
                  'status': 'completed', 'dry_run': True}
        started = review.timestamp(record['started_at'])
        self.assertFalse(review.should_dispatch(record, 'a', started + 1, 1800))
        self.assertTrue(review.should_dispatch(record, 'a', started + 1800, 1800))
        self.assertFalse(review.should_dispatch(record, 'a', started + 1800, 1800, dry_run=True))

    def test_cooldown_owner_edit_is_deferred_and_latest_input_is_used(self):
        record = {'started_at': '2026-10-10T10:00:00Z', 'fingerprint': 'old', 'status': 'completed'}
        started = review.timestamp(record['started_at'])
        latest = copy.deepcopy(self.issue)
        latest['body'] += '\nCorrected metadata during cooldown'
        comments = [self.event['comment']]
        with patch.object(review, 'paginate', side_effect=[[], comments]), \
                patch.object(review, 'github', return_value=latest) as github, \
                patch.object(review.time, 'time', side_effect=[started + 1799, started + 1800]), \
                patch.object(review.time, 'monotonic', return_value=100), \
                patch.object(review.time, 'sleep') as sleep, patch.object(review, 'summary'):
            issue, digest = review.automatic_input(self.issue, record, 'reviewer', 1800)
        sleep.assert_called_once_with(1)
        github.assert_called_once_with(f'/repos/{review.REPO}/issues/9')
        self.assertEqual(issue, latest)
        self.assertEqual(digest, review.fingerprint(latest, comments, 'reviewer'))

    def test_completed_unchanged_input_does_not_wait(self):
        record = {'started_at': '2026-10-10T10:00:00Z',
                  'fingerprint': review.fingerprint(self.issue, [], 'reviewer'), 'status': 'completed'}
        with patch.object(review, 'paginate', return_value=[]), \
                patch.object(review.time, 'time', return_value=review.timestamp(record['started_at']) + 1), \
                patch.object(review.time, 'sleep') as sleep, patch.object(review, 'github') as github:
            self.assertIsNone(review.automatic_input(self.issue, record, 'reviewer', 1800))
        sleep.assert_not_called()
        github.assert_not_called()

    def test_issue_closed_during_cooldown_does_not_dispatch(self):
        record = {'started_at': '2026-10-10T10:00:00Z', 'fingerprint': 'old', 'status': 'completed'}
        closed = copy.deepcopy(self.issue)
        closed['state'] = 'closed'
        with patch.object(review, 'paginate', return_value=[]), \
                patch.object(review, 'github', return_value=closed), \
                patch.object(review.time, 'time', return_value=review.timestamp(record['started_at']) + 1799), \
                patch.object(review.time, 'monotonic', return_value=100), \
                patch.object(review.time, 'sleep') as sleep, patch.object(review, 'summary'):
            self.assertIsNone(review.automatic_input(self.issue, record, 'reviewer', 1800))
        sleep.assert_called_once_with(1)


class ProtocolTests(unittest.TestCase):
    def test_latest_state_uses_creation_time_not_artifact_id(self):
        artifacts = [{'id': 99, 'created_at': '2026-10-10T10:00:00Z', 'workflow_run': {'id': 1, 'head_branch': 'main'}},
                     {'id': 88, 'created_at': '2026-10-10T11:00:00Z', 'workflow_run': {'id': 2, 'head_branch': 'main'}}]
        state = {'version': 1, 'repository': review.REPO, 'issues': {'9': {'status': 'completed'}}}
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, 'w') as archive:
            archive.writestr('state.json', json.dumps(state))
        download = MagicMock()
        download.__enter__.return_value = io.BytesIO(blob.getvalue())
        redirect = urllib.error.HTTPError('https://api.github.com', 302, 'Found', {'Location': 'https://example.com/archive'}, None)
        with patch.object(review, 'github', side_effect=[{'artifacts': artifacts},
                {'path': review.WORKFLOW, 'head_repository': {'full_name': review.REPO}}]) as github, patch.object(review.OPENER, 'open', side_effect=[redirect, download]) as opener, patch.dict(os.environ, {'GH_READ_TOKEN': 'read-token'}):
            self.assertEqual(review.load_state(), state)
            self.assertTrue(github.call_args.args[0].endswith('/runs/2'))
            self.assertIn('/artifacts/88/zip', opener.call_args_list[0].args[0].full_url)

    def test_existing_completed_session_is_replayed_without_created_request(self):
        frames = ('id: 1\nevent: message.completed\ndata: {"type":"message.completed","run_id":"r","data":{"role":"assistant","message_id":"m","text":'
                  '"ASSET_REVIEW_RESULT {\\"issue_number\\":9,\\"outcome\\":\\"skipped\\"}"}}\n\n'
                  'id: 2\nevent: run.completed\ndata: {"type":"run.completed","run_id":"r","data":{"final_message_id":"m"}}\n\n')
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'REVIEW_STATE_DIR': folder,
                'HAST_AGENT_URL': 'https://aip.hast.so/api/call?secret=abc', 'REVIEWER_LOGIN': 'reviewer'}):
            plan = {'issue_number': 9, 'dry_run': True, 'connectivity_check': True, 'fingerprint': 'f', 'status': 'pending',
                    'started_at': '2000-01-01T00:00:00Z', 'recovery': True, 'session_id': 's', 'request_id': 'q', 'run_id': '1'}
            review.save_state({'issues': {'9': plan}})
            (Path(folder) / 'plan.json').write_text(json.dumps(plan))
            with patch.object(review, 'hook') as hook, patch.object(review, 'summary'):
                hook.return_value.__enter__.return_value = frames.encode().splitlines(keepends=True)
                review.execute()
                self.assertEqual(hook.call_count, 1)
                self.assertEqual(hook.call_args.args[1], 'sse')
            self.assertEqual(json.loads((Path(folder) / 'state.json').read_text())['issues']['9']['status'], 'completed')

    def test_operator_report_redacts_urls_credentials_and_unbroken_identifiers(self):
        secret = 'github_pat_' + 'x' * 70
        value = 'Blocked: missing permission.\nhttps://aip.hast.so/call?secret=abc\n' + secret + '\n' + 'y' * 40 + '\nAuthorization: Bearer short-secret'
        output = review.redacted_report(value)
        self.assertIn('Blocked: missing permission.', output)
        for sensitive in (secret, 'y' * 40, 'short-secret', 'secret=abc'):
            self.assertNotIn(sensitive, output)
    def test_sse_connection_loss_resumes_same_session_without_new_message(self):
        # The SSE iterator yields lines rather than entire frames.
        def broken_lines():
            yield from b'id: 1\nevent: run.started\ndata: {"type":"run.started","data":{}}\n\n'.splitlines(keepends=True)
            raise http.client.IncompleteRead(b'', 1)

        final = ('id: 2\nevent: message.completed\ndata: '
                 '{"type":"message.completed","run_id":"r","data":{"role":"assistant","message_id":"m","text":'
                 '"ASSET_REVIEW_RESULT {\\"issue_number\\":9,\\"outcome\\":\\"skipped\\"}"}}\n\n'
                 'id: 3\nevent: run.completed\ndata: {"type":"run.completed","run_id":"r","data":{"final_message_id":"m"}}\n\n')
        streams = iter([broken_lines(), final.encode().splitlines(keepends=True)])
        calls = []

        def fake_hook(url, action, payload, timeout=30):
            calls.append((action, payload))
            context = MagicMock()
            context.__enter__.return_value = io.BytesIO(b'{}') if action == 'created' else next(streams)
            return context

        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'REVIEW_STATE_DIR': folder,
                'HAST_AGENT_URL': 'https://aip.hast.so/api/call?secret=abc', 'REVIEWER_LOGIN': 'reviewer'}):
            plan = {'issue_number': 9, 'dry_run': True, 'connectivity_check': True, 'fingerprint': 'f',
                    'status': 'pending', 'started_at': review.now(), 'session_id': 's', 'request_id': 'q', 'run_id': '1'}
            plan['payload'] = review.request_payload(plan)
            review.save_state({'issues': {'9': plan}})
            (Path(folder) / 'plan.json').write_text(json.dumps(plan))
            with patch.object(review, 'hook', side_effect=fake_hook), patch.object(review.time, 'sleep'), patch.object(review, 'summary'):
                review.execute()
            self.assertEqual(json.loads((Path(folder) / 'state.json').read_text())['issues']['9']['status'], 'completed')
        self.assertEqual([action for action, _ in calls], ['created', 'sse', 'sse'])
        self.assertEqual(calls[2][1]['after_event_id'], 1)

    def test_recovery_does_not_reset_review_deadline_and_waits_for_stop_ack(self):
        calls = []
        def fake_hook(url, action, payload, timeout=30):
            calls.append(action)
            context = MagicMock()
            context.__enter__.return_value = (b'id: 1\nevent: run.interrupted\ndata: {"type":"run.interrupted","data":{}}\n\n'.splitlines(keepends=True)
                                             if action == 'sse' else io.BytesIO(b'{}'))
            return context
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'REVIEW_STATE_DIR': folder,
                'HAST_AGENT_URL': 'https://aip.hast.so/api/call?secret=abc', 'REVIEWER_LOGIN': 'reviewer'}):
            plan = {'issue_number': 9, 'dry_run': True, 'fingerprint': 'f', 'status': 'pending',
                    'started_at': '2000-01-01T00:00:00Z', 'session_id': 's', 'request_id': 'q', 'run_id': '1'}
            review.save_state({'issues': {'9': plan}})
            (Path(folder) / 'plan.json').write_text(json.dumps(plan))
            with patch.object(review, 'hook', side_effect=fake_hook), patch.object(review, 'summary'), self.assertRaises(review.Failure):
                review.execute()
            record = json.loads((Path(folder) / 'state.json').read_text())['issues']['9']
            self.assertTrue(record['stop_requested'])
            self.assertEqual(record['status'], 'completed')
        self.assertEqual(calls, ['created', 'prompted', 'sse'])

    def test_inspection_reads_only_existing_session_and_never_prints_message_text(self):
        frames = [b'event: message.completed\n',
                  b'data: {"type":"message.completed","run_id":"r","data":{"role":"user","message_id":"m","text":"401 private-message github_pat_sensitive"}}\n', b'\n',
                  b'event: run.completed\n',
                  b'data: {"type":"run.completed","run_id":"r","data":{"final_message_id":"m"}}\n', b'\n']
        with patch.dict(os.environ, {'REVIEW_SESSION_ID': 'asset-review-9-123', 'HAST_AGENT_URL': 'https://aip.hast.so/api/call?secret=abc'}), patch.object(review, 'hook') as hook, patch.object(review, 'summary') as summary:
            hook.return_value.__enter__.return_value = frames
            review.inspect()
            self.assertEqual(hook.call_count, 1)
            self.assertEqual(hook.call_args.args[1], 'sse')
            output = str(summary.call_args_list)
            self.assertIn('auth-failure', output)
            self.assertNotIn('private-message', output)
            self.assertNotIn('github_pat_sensitive', output)
            self.assertNotIn('secret=abc', output)

    def test_sse_keepalive_multiline_and_durable_id(self):
        lines = [b': keepalive\n', b'\n', b'id: 42\n', b'event: run.completed\n',
                 b'data: {"type":\n', b'data: "run.completed"}\n', b'\n']
        frames = list(review.sse_frames(lines))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]['id'], '42')
        self.assertEqual(frames[0]['data'], '{"type":\n"run.completed"}')
        self.assertNotIn('message.completed', review.TERMINAL)

    def test_sse_final_frame_without_blank_line_is_not_dropped(self):
        lines = [b'id: 7\n', b'event: message.completed\n', b'data: {"type":"message.completed"}\n']
        frames = list(review.sse_frames(lines))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]['id'], '7')
        self.assertEqual(frames[0]['data'], '{"type":"message.completed"}')

    def test_hook_url_validation_without_credential_disclosure(self):
        for value in ('http://aip.hast.so/call?secret=abc', 'https://evil.example/call?secret=abc',
                      'https://aip.hast.so/agents/hook_123?secret=abc', 'https://u:p@hast.so/call'):
            with self.subTest(value=value), self.assertRaises(review.Failure) as error:
                review.validate_hook(value)
            self.assertNotIn(value, str(error.exception))
            self.assertNotIn('secret=abc', str(error.exception))
        self.assertEqual(review.validate_hook('https://aip.hast.so/api/call?secret=abc'),
                         'https://aip.hast.so/api/call?secret=abc')

    def test_only_structured_final_result_is_used(self):
        value = {'data': {'content': 'Report\nASSET_REVIEW_RESULT {"issue_number":9,"outcome":"would-need-info"}'}}
        self.assertEqual(review.extract_result(value)['outcome'], 'would-need-info')
        self.assertIsNone(review.extract_result({'content': 'HTTP 200. Completed.'}))

    def test_identical_concatenated_results_are_tolerated_but_conflicts_fail(self):
        text = 'ASSET_REVIEW_RESULT {"issue_number":9,"outcome":"skipped"}'
        self.assertEqual(review.extract_result(text + text)['outcome'], 'skipped')
        self.assertIsNone(review.extract_result(text + text.replace('skipped', 'draft-pr')))
        self.assertEqual(review.extract_result('ASSET_REVIEW_RESULT\n{\n"issue_number":9,\n"outcome":"skipped"\n}')['issue_number'], 9)

    def test_result_must_belong_to_completed_runs_final_assistant_message(self):
        final = review.FinalResult()
        text = 'ASSET_REVIEW_RESULT {"issue_number":9,"outcome":"would-need-info"}'
        for role in ('user', 'tool'):
            final.accept({'run_id': 'r', 'data': {'role': role, 'message_id': 'fake', 'text': text}}, 'message.completed')
        self.assertIsNone(final.accept({'run_id': 'r', 'data': {'final_message_id': 'fake'}}, 'run.completed'))
        final.accept({'run_id': 'r', 'data': {'role': 'assistant', 'message_id': 'm', 'text': text}}, 'message.completed')
        self.assertIsNone(final.accept({'run_id': 'other', 'data': {'final_message_id': 'm'}}, 'run.completed'))
        self.assertIsNone(final.accept({'run_id': 'r', 'data': {'final_message_id': 'different'}}, 'run.completed'))
        self.assertEqual(final.accept({'run_id': 'r', 'data': {'final_message_id': 'm'}}, 'run.completed')['outcome'], 'would-need-info')

    def test_recovery_payload_is_secret_free_and_manual_override_is_narrow(self):
        plan = {'issue_number': 9, 'session_id': 's', 'request_id': 'q', 'dry_run': False,
                'fingerprint': 'f', 'run_id': 'r', 'force': True}
        payload = review.request_payload(plan)
        self.assertEqual(payload['request_id'], 'q')
        self.assertIn('All provenance, scam, safety and build gates still apply', payload['prompt'])
        self.assertEqual(set(payload), {'session_id', 'request_id', 'prompt', 'context'})

    def test_dry_run_rejects_write_and_blocked_results(self):
        plan = {'issue_number': 9, 'dry_run': True}
        for result in ({'issue_number': 9, 'outcome': 'needs-info'},
                       {'issue_number': 9, 'outcome': 'would-need-info', 'comment_url': 'https://github.com/x'},
                       {'issue_number': 9, 'outcome': 'blocked'}):
            with self.assertRaises(review.Failure):
                review.verify_result(result, plan, 'reviewer')
        self.assertEqual(review.verify_result({'issue_number': 9, 'outcome': 'would-need-info'}, plan, 'reviewer')['outcome'],
                         'would-need-info')

    def test_comment_result_requires_returned_author_and_marker(self):
        result = {'issue_number': 9, 'outcome': 'needs-info',
                  'comment_url': f'https://github.com/{review.REPO}/issues/9#issuecomment-10'}
        plan = {'issue_number': 9, 'dry_run': False, 'fingerprint': 'f'}
        marker = review.MARKER + 'input-sha256=f outcome=needs-info -->'
        for login, body in [('attacker', marker), ('reviewer', review.MARKER),
                            ('reviewer', marker.replace('sha256=f ', 'sha256=old '))]:
            with patch.object(review, 'github', return_value={'user': {'login': login}, 'body': body, 'html_url': result['comment_url']}), self.assertRaises(review.Failure):
                review.verify_result(result, plan, 'reviewer')
        with patch.object(review, 'github', return_value={'user': {'login': 'reviewer'}, 'body': marker, 'html_url': result['comment_url']}):
            self.assertEqual(review.verify_result(result, plan, 'reviewer')['outcome'], 'needs-info')

    def test_skipped_requires_matching_public_outcome_not_model_assertion(self):
        plan = {'issue_number': 9, 'dry_run': False, 'fingerprint': 'f'}
        result = {'issue_number': 9, 'outcome': 'skipped'}
        issue = {'state': 'open', 'labels': [{'name': 'update-request'}]}
        with patch.object(review, 'github', return_value=issue), patch.object(review, 'paginate', return_value=[]), self.assertRaises(review.Failure):
            review.verify_result(result, plan, 'reviewer')
        comments = [{'user': {'login': 'reviewer'}, 'body': review.MARKER + 'input-sha256=f outcome=needs-info -->'}]
        with patch.object(review, 'github', return_value=issue), patch.object(review, 'paginate', return_value=comments):
            self.assertEqual(review.verify_result(result, plan, 'reviewer')['outcome'], 'skipped')
        plan['dry_run'] = True
        plan['connectivity_check'] = True
        self.assertEqual(review.verify_result(result, plan, 'reviewer')['outcome'], 'skipped')

    def test_legacy_outcome_is_only_valid_for_skip_not_a_new_write_receipt(self):
        plan = {'issue_number': 9, 'dry_run': False, 'fingerprint': 'f'}
        issue = {'state': 'open', 'labels': [{'name': 'update-request'}]}
        marker = review.MARKER + 'input-sha256=f outcome=needs-info -->'
        url = f'https://github.com/{review.REPO}/issues/9#issuecomment-10'
        comment = {'user': {'login': 'freehere107'}, 'body': marker, 'html_url': url}
        with patch.dict(os.environ, {'LEGACY_REVIEWER_LOGINS': '["freehere107"]'}), \
                patch.object(review, 'github', return_value=issue), \
                patch.object(review, 'paginate', return_value=[comment]):
            self.assertEqual(review.verify_result({'issue_number': 9, 'outcome': 'skipped'},
                plan, 'subscan-aip-research[bot]')['outcome'], 'skipped')
        with patch.dict(os.environ, {'LEGACY_REVIEWER_LOGINS': '["freehere107"]'}), \
                patch.object(review, 'github', return_value=comment), self.assertRaises(review.Failure):
            review.verify_result({'issue_number': 9, 'outcome': 'needs-info', 'comment_url': url},
                                 plan, 'subscan-aip-research[bot]')

    def test_asset_pr_rejects_non_draft_and_workflow_edits(self):
        result = {'issue_number': 9, 'outcome': 'draft-pr', 'pr_url': f'https://github.com/{review.REPO}/pull/10'}
        pr = {'draft': True, 'user': {'login': 'reviewer'}, 'base': {'ref': 'main'}, 'head': {'ref': 'codex/token-9'}}
        with patch.object(review, 'github', return_value=pr), patch.object(review, 'paginate', return_value=[{'filename': '.github/workflows/pwn.yml'}]), self.assertRaises(review.Failure):
            review.verify_result(result, {'issue_number': 9, 'dry_run': False}, 'reviewer')
        pr['draft'] = False
        with patch.object(review, 'github', return_value=pr), patch.object(review, 'paginate', return_value=[{'filename': 'assets/peaq.json'}]), self.assertRaises(review.Failure):
            review.verify_result(result, {'issue_number': 9, 'dry_run': False}, 'reviewer')

    def test_verified_asset_pr_links_the_issue_and_stays_in_scope(self):
        result = {'issue_number': 9, 'outcome': 'draft-pr', 'pr_url': f'https://github.com/{review.REPO}/pull/10'}
        pr = {'draft': True, 'user': {'login': 'reviewer'}, 'base': {'ref': 'main'},
              'head': {'ref': 'codex/token-9', 'repo': {'full_name': review.REPO}}, 'body': 'Closes #9'}
        files = [{'filename': 'assets/peaq.json'}, {'filename': 'logos/peaq_erc20_PHN.svg'}]
        with patch.object(review, 'github', return_value=pr), patch.object(review, 'paginate', return_value=files):
            self.assertEqual(review.verify_result(result, {'issue_number': 9, 'dry_run': False}, 'reviewer')['outcome'], 'draft-pr')
        pr['body'] = 'Closes #99'
        with patch.object(review, 'github', return_value=pr), patch.object(review, 'paginate', return_value=files), self.assertRaises(review.Failure):
            review.verify_result(result, {'issue_number': 9, 'dry_run': False}, 'reviewer')


if __name__ == '__main__':
    unittest.main()
