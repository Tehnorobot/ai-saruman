"""Public egress boundary with mocked DNS and local socket fixtures, never Internet."""
from contextlib import contextmanager
import socket
import threading
import unittest
from unittest.mock import Mock,patch

from network import egress


def address(ip,port):
    family=socket.AF_INET6 if ':' in ip else socket.AF_INET
    destination=(ip,port,0,0) if family==socket.AF_INET6 else (ip,port)
    return family,socket.SOCK_STREAM,socket.IPPROTO_TCP,'',destination


class EgressPolicyTests(unittest.TestCase):
    def test_exact_litellm_http_target_keeps_dns_boundary(self):
        parsed=egress.parse_request(self.request('POST http://lnsigo.mipt.ru:4000/v1/chat/completions HTTP/1.1',
            ['Host: lnsigo.mipt.ru:4000','Content-Length: 0']))
        self.assertEqual((parsed['host'],parsed['port']),('lnsigo.mipt.ru',4000))
        resolver=Mock(return_value=[address('93.184.216.34',4000)])
        self.assertTrue(egress.resolve_public('lnsigo.mipt.ru',4000,resolver))
        resolver.return_value=[address('127.0.0.1',4000)]
        with self.assertRaises(egress.Denied):egress.resolve_public('lnsigo.mipt.ru',4000,resolver)
        for host,port in [('lnsigo.mipt.ru.evil.test',4000),('other.example',4000),('127.0.0.1',4000),('lnsigo.mipt.ru',4001)]:
            with self.subTest(host=host,port=port),self.assertRaises(egress.Denied):
                egress.parse_request(self.request(f'GET http://{host}:{port}/v1 HTTP/1.1'))

    def request(self,line='GET http://example.com/resource HTTP/1.1',headers=()):
        return ('\r\n'.join([line,*headers])+'\r\n\r\n').encode()

    def test_only_public_addresses_are_allowed_including_ipv6_transition_boundaries(self):
        for ip in ('8.8.8.8','93.184.216.34','2001:4860:4860::8888'):
            self.assertTrue(egress.public_ip(ip),ip)
        for ip in ('127.0.0.1','0.0.0.0','10.0.0.1','172.16.0.1','192.168.1.1',
                   '169.254.169.254','100.64.0.1','192.0.2.1','198.51.100.1','203.0.113.1',
                   '224.0.0.1','255.255.255.255','::','::1','fe80::1','fc00::1','fec0::1',
                   '::ffff:8.8.8.8','::ffff:127.0.0.1','64:ff9b::7f00:1','2002:7f00:1::',
                   '2001:db8::1','fe80::1%eth0'):
            with self.subTest(ip=ip):self.assertFalse(egress.public_ip(ip))

    def test_dns_mixed_public_private_answer_is_rejected_entirely(self):
        for private in ('127.0.0.1','10.0.0.1','::ffff:127.0.0.1'):
            resolver=lambda host,port,**kwargs:[address('93.184.216.34',port),address(private,port)]
            with self.subTest(private=private),self.assertRaises(egress.Denied):
                egress.resolve_public('public-looking.example',443,resolver)

    def test_connection_uses_validated_numeric_sockaddr_without_second_hostname_lookup(self):
        resolver=Mock(return_value=[address('93.184.216.34',443)])
        connection=Mock()
        with patch.object(egress.socket,'socket',return_value=connection):
            self.assertIs(egress.connect_public('example.com',443,resolver),connection)
        resolver.assert_called_once()
        connection.connect.assert_called_once_with(('93.184.216.34',443))

    def test_local_aliases_and_private_literals_are_rejected_before_resolution(self):
        resolver=Mock(return_value=[address('93.184.216.34',443)])
        for host in ('localhost','LOCALHOST.','host.docker.internal','gateway.docker.internal',
                     'metadata.google.internal','127.0.0.1','::1','::ffff:8.8.8.8'):
            with self.subTest(host=host),self.assertRaises(egress.Denied):
                egress.resolve_public(host,443,resolver)
        resolver.assert_not_called()

    def test_cached_public_resolution_stays_pinned_and_private_rebinding_after_expiry_is_denied(self):
        key=('rebinding-fixture.example',443)
        with egress.DNS_LOCK:egress.DNS_CACHE.pop(key,None)
        resolver=Mock(side_effect=[[address('93.184.216.34',443)],[address('127.0.0.1',443)]])
        connection=Mock()
        try:
            with patch.object(egress.socket,'getaddrinfo',resolver),patch.object(egress.socket,'socket',return_value=connection):
                egress.connect_public(*key)
                egress.connect_public(*key)
                self.assertEqual(resolver.call_count,1)
                self.assertEqual(connection.connect.call_args_list[0].args,(('93.184.216.34',443),))
                self.assertEqual(connection.connect.call_args_list[1].args,(('93.184.216.34',443),))
                with egress.DNS_LOCK:egress.DNS_CACHE[key]=(0,egress.DNS_CACHE[key][1])
                with self.assertRaises(egress.Denied):egress.connect_public(*key)
                self.assertEqual(connection.connect.call_count,2)
        finally:
            with egress.DNS_LOCK:egress.DNS_CACHE.pop(key,None)

    def test_dns_thread_capacity_is_bounded_without_starting_more_lookups(self):
        acquired=[]
        try:
            for _ in range(8):
                self.assertTrue(egress.DNS_SLOTS.acquire(timeout=1));acquired.append(True)
            with patch.object(egress.socket,'getaddrinfo') as resolver:
                with self.assertRaises(egress.Denied):egress.resolve_public('dns-capacity-fixture.example',443)
                resolver.assert_not_called()
        finally:
            for _ in acquired:egress.DNS_SLOTS.release()

    def test_header_deadline_is_absolute_instead_of_reset_by_slow_drip(self):
        connection=Mock()
        connection.recv.return_value=b'GET http://example.com/ HTTP/1.1\r\n'
        with patch.object(egress.time,'monotonic',side_effect=[0,1,16]):
            with self.assertRaises(egress.Denied):egress.read_headers(connection)
        connection.recv.assert_called_once()

    def test_credentials_non_http_urls_and_non_web_ports_are_rejected(self):
        for line in ('GET http://user:password@example.com/path HTTP/1.1',
                     'GET file:///etc/passwd HTTP/1.1','GET /relative HTTP/1.1',
                     'GET https://example.com/path HTTP/1.1','GET http://example.com:8080/ HTTP/1.1',
                     'CONNECT example.com:22 HTTP/1.1','CONNECT user:password@example.com:443 HTTP/1.1',
                     'CONNECT example.com:443/path HTTP/1.1','TRACE http://example.com/ HTTP/1.1'):
            with self.subTest(line=line),self.assertRaises(ValueError):egress.parse_request(self.request(line))

    def test_ambiguous_framing_and_header_injection_are_rejected(self):
        for headers in (('Content-Length: 1','Content-Length: 1'),
                        ('Content-Length: 1','Content-Length: 2'),
                        ('Transfer-Encoding: chunked',),('Content-Length: 0','Transfer-Encoding: chunked'),
                        ('Content-Length: -1',),('Content-Length: +1',),
                        ('Content-Length: '+str(egress.MAX_BODY_BYTES+1),),
                        ('Host: example.com','Host: example.com'),('Host: internal.invalid',),
                        ('X-Test: first',' second folded'),('X-Test: value\x00bad',),
                        ('Connection: Content-Length',),('Upgrade: websocket',),('Expect: 100-continue',)):
            with self.subTest(headers=headers),self.assertRaises(egress.Denied):
                egress.parse_request(self.request(headers=headers))

    def test_header_limits_are_enforced_without_connecting(self):
        with self.assertRaises(egress.Denied):
            egress.parse_request(self.request(headers=('X-Large: '+'a'*egress.MAX_HEADER_BYTES,)))
        with self.assertRaises(egress.Denied):
            egress.parse_request(self.request(headers=tuple('X-%d: ok'%index for index in range(70))))


@contextmanager
def local_proxy(connector):
    with patch.object(egress,'connect_public',side_effect=connector):
        with egress.Server(('127.0.0.1',0),egress.Handler) as server:
            thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.01),daemon=True)
            thread.start()
            try:yield server
            finally:server.shutdown();thread.join(3)


def receive_all(connection):
    data=bytearray();connection.settimeout(5)
    while True:
        try:block=connection.recv(65536)
        except ConnectionResetError:break
        if not block:return bytes(data)
        data.extend(block)
    return bytes(data)


class EgressSocketTests(unittest.TestCase):
    def test_http_does_not_signal_disconnect_while_server_prepares_response(self):
        import select
        proxy_side,server_side=socket.socketpair();observed=[]
        def upstream():
            with server_side:
                data=bytearray()
                while b'\r\n\r\n' not in data:data.extend(server_side.recv(65536))
                headers,body=bytes(data).split(b'\r\n\r\n',1)
                while len(body)<2:body+=server_side.recv(2-len(body))
                # Simulate an ASGI server observing early client EOF before
                # it has produced its response; old relay sent FIN here.
                ready,_,_=select.select([server_side],[],[],.15)
                observed.append(server_side.recv(1)==b'' if ready else False)
                server_side.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
        thread=threading.Thread(target=upstream,daemon=True);thread.start()
        with local_proxy(lambda host,port:proxy_side) as server:
            with socket.create_connection(server.server_address) as client:
                client.sendall(b'POST http://lnsigo.mipt.ru:4000/v1/chat/completions HTTP/1.1\r\nHost: lnsigo.mipt.ru:4000\r\nContent-Length: 2\r\n\r\n{}')
                response=receive_all(client)
        thread.join(3)
        self.assertEqual(observed,[False])
        self.assertTrue(response.endswith(b'\r\n\r\nOK'))

    def test_connect_relays_opaque_tls_bytes_including_bytes_after_header(self):
        proxy_side,server_side=socket.socketpair()
        payload=b'\x16\x03\x01opaque TLS bytes\x00\xff'
        observed=[]
        def upstream():
            with server_side:
                data=bytearray()
                while len(data)<len(payload):data.extend(server_side.recv(65536))
                observed.append(bytes(data));server_side.sendall(bytes(data))
        thread=threading.Thread(target=upstream,daemon=True);thread.start()
        with local_proxy(lambda host,port:proxy_side) as server:
            with socket.create_connection(server.server_address) as client:
                client.sendall(b'CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n'+payload)
                response=receive_all(client)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(observed,[payload])
        self.assertEqual(response.split(b'\r\n\r\n',1),[b'HTTP/1.1 200 Connection Established',payload])

    def test_http_preserves_body_drops_proxy_headers_and_does_not_forward_pipelined_request(self):
        proxy_side,server_side=socket.socketpair();observed=[]
        def upstream():
            with server_side:
                server_side.settimeout(5)
                data=bytearray()
                while b'\r\n\r\n' not in data:data.extend(server_side.recv(65536))
                header,body=bytes(data).split(b'\r\n\r\n',1)
                while len(body)<4:body+=server_side.recv(4-len(body))
                observed.append((header,body))
                server_side.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
        thread=threading.Thread(target=upstream,daemon=True);thread.start()
        with local_proxy(lambda host,port:proxy_side) as server:
            with socket.create_connection(server.server_address) as client:
                client.sendall(b'POST http://example.com/resource?q=1 HTTP/1.1\r\nHost: example.com\r\n'
                               b'Content-Length: 4\r\nProxy-Authorization: dummy-secret\r\n'
                               b'Proxy-Connection: keep-alive\r\n\r\ndataGET /smuggled HTTP/1.1\r\n\r\n')
                response=receive_all(client)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertTrue(observed[0][0].startswith(b'POST /resource?q=1 HTTP/1.1\r\n'))
        self.assertNotIn(b'Proxy-',observed[0][0])
        self.assertNotIn(b'dummy-secret',observed[0][0])
        self.assertEqual(observed[0][1],b'data')
        self.assertTrue(response.endswith(b'\r\n\r\nOK'))

    def test_bad_framing_never_connects_to_upstream_and_error_does_not_echo_url(self):
        connector=Mock(side_effect=AssertionError('Forbidden upstream connection'))
        with local_proxy(connector) as server:
            with socket.create_connection(server.server_address) as client:
                client.sendall(b'POST http://example.com/private-token HTTP/1.1\r\n'
                               b'Transfer-Encoding: chunked\r\nContent-Length: 0\r\n\r\n')
                response=receive_all(client)
        connector.assert_not_called()
        self.assertTrue(response.startswith(b'HTTP/1.1 403 '))
        self.assertNotIn(b'private-token',response)

    def test_connection_capacity_is_finite_and_excess_connection_is_closed(self):
        with local_proxy(Mock()) as server:
            for _ in range(egress.MAX_CONNECTIONS):self.assertTrue(server.slots.acquire(blocking=False))
            try:
                with socket.create_connection(server.server_address) as client:
                    response=receive_all(client)
                self.assertTrue(response.startswith(b'HTTP/1.1 503 '))
            finally:
                for _ in range(egress.MAX_CONNECTIONS):server.slots.release()


if __name__=='__main__':unittest.main()
