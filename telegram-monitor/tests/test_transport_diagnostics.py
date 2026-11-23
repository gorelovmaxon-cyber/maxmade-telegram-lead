import io
import json
import os
from pathlib import Path
import ssl
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import monitor
import transport_diagnostics as td


class Response(io.BytesIO):
    status = 200


class TransportDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.patch = patch.object(td, 'STATE', Path(self.directory.name))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.env = patch.dict(os.environ, {'MONITOR_HTTP_BACKEND': 'urllib'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def log(self):
        return json.loads((td.STATE/'delivery-transport.jsonl').read_text().splitlines()[-1])

    def failure(self, phase, error):
        def fail(request, timeout, trace):
            trace.enter(phase)
            raise error
        with patch.object(td, 'open_request', side_effect=fail):
            with self.assertRaises(monitor.UnknownDelivery):
                monitor.Telegram('SECRET_TOKEN').call('sendMessage', {'chat_id': 321, 'text': 'PRIVATE_BODY'}, timeout=3)
        return self.log()

    def test_proxy_refusal_classified_without_exception_url_or_payload(self):
        result = self.failure('proxy_tcp', urllib.error.URLError(ConnectionRefusedError(10061, 'SECRET_TOKEN PRIVATE_BODY chat_id=321')))
        self.assertEqual((result['phase'], result['error_category'], result['errno']), ('proxy_tcp','connection_refused',10061))
        text = json.dumps(result)
        for secret in ('SECRET_TOKEN', 'PRIVATE_BODY', '321', 'api.telegram.org', 'https://'):
            self.assertNotIn(secret, text)

    def test_tls_certificate_failure_is_distinct_from_proxy_failure(self):
        result = self.failure('tls', urllib.error.URLError(ssl.SSLCertVerificationError(1, 'SECRET_TOKEN')))
        self.assertEqual(result['error_category'], 'tls_certificate')
        self.assertEqual(result['phase'], 'tls')

    def test_header_timeout_is_not_reported_as_api_rejection(self):
        result = self.failure('http_headers', TimeoutError('PRIVATE_BODY'))
        self.assertEqual((result['outcome'],result['error_category']), ('network_error','timeout'))
        self.assertNotIn('api_code', result)

    def test_http_429_preserves_retry_after_and_does_not_log_body(self):
        error = urllib.error.HTTPError('https://SECRET_TOKEN',429,'PRIVATE_BODY',{},io.BytesIO(b'{"parameters":{"retry_after":17}}'))
        with patch.object(td,'open_request',side_effect=error):
            with self.assertRaises(monitor.ApiError) as raised:
                monitor.Telegram('SECRET_TOKEN').call('getUpdates',{})
        self.assertEqual((raised.exception.code,raised.exception.retry_after),(429,17))
        self.assertEqual(self.log()['http_status'],429)
        self.assertEqual(self.log()['retry_after_s'],17)
        self.assertNotIn('SECRET_TOKEN',json.dumps(self.log()))

    def test_invalid_json_records_http_status_but_no_response(self):
        with patch.object(td,'open_request',return_value=Response(b'PRIVATE_BODY SECRET_TOKEN')):
            with self.assertRaises(monitor.UnknownDelivery):
                monitor.Telegram('SECRET_TOKEN').call('getMe',{})
        self.assertEqual((self.log()['outcome'],self.log()['http_status']),('invalid_response',200))
        self.assertNotIn('PRIVATE_BODY',json.dumps(self.log()))

    def test_success_and_api_error_keep_existing_result_semantics(self):
        with patch.object(td,'open_request',return_value=Response(b'{"ok":true,"result":{"id":7}}')):
            self.assertEqual(monitor.Telegram('SECRET_TOKEN').call('getMe',{}),{'id':7})
        self.assertEqual(self.log()['outcome'],'ok')
        with patch.object(td,'open_request',return_value=Response(b'{"ok":false,"error_code":403}')):
            with self.assertRaises(monitor.ApiError) as raised:
                monitor.Telegram('SECRET_TOKEN').call('getMe',{})
        self.assertEqual(raised.exception.code,403)
        self.assertEqual(self.log()['outcome'],'api_error')

    def test_tcp_and_connect_tunnel_phases_are_distinct(self):
        trace = td.Trace('getMe',3,'urllib')
        def tcp(*args,**kwargs):
            raise ConnectionRefusedError(10061,'PRIVATE_PROXY_PASSWORD')
        with patch('http.client.socket.create_connection',side_effect=tcp):
            connection=td.Connection('127.0.0.1',trace=trace)
            connection.set_tunnel('api.telegram.org',443)
            with self.assertRaises(ConnectionRefusedError):
                connection.connect()
        self.assertEqual(trace.phase,'proxy_tcp')
        self.assertTrue(trace.record['via_proxy'])
        trace=td.Trace('getMe',3,'urllib')
        connection=td.Connection('127.0.0.1',trace=trace)
        with patch('http.client.HTTPSConnection._tunnel',side_effect=OSError('Tunnel connection failed: 407 PRIVATE_PROXY_PASSWORD')):
            with self.assertRaises(OSError):
                connection._tunnel()
        self.assertEqual(trace.record['proxy_connect_status'],407)
        self.assertEqual(trace.phase,'proxy_connect')

    def test_logging_failure_does_not_fail_successful_api_call(self):
        with patch.object(td,'open_request',return_value=Response(b'{"ok":true,"result":true}')), \
                patch.object(Path,'open',side_effect=PermissionError('SECRET_TOKEN')):
            self.assertTrue(monitor.Telegram('SECRET_TOKEN').call('getMe',{}))

    def test_bounded_rotation(self):
        with patch.object(td,'MAX_BYTES',1):
            for _ in range(5):
                td.Trace('user-supplied-SECRET_TOKEN',2,'unknown').finish('ok')
        self.assertEqual(len(list(td.STATE.glob('delivery-transport*.jsonl'))),3)
        self.assertEqual(self.log()['method'],'other')


if __name__ == '__main__':
    unittest.main()
