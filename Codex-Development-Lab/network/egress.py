"""Bounded public-IP-only HTTP/CONNECT proxy. No TLS interception or request logs."""
from __future__ import annotations

import ipaddress
import queue
import re
import select
import socket
import socketserver
import threading
import time
from urllib.parse import urlsplit

MAX_CONNECTIONS=128
MAX_HEADER_BYTES=32768
MAX_LINE_BYTES=8192
MAX_BODY_BYTES=64*1024*1024
BUFFER_BYTES=65536
HEADER_TIMEOUT=15
CONNECT_TIMEOUT=8
IDLE_TIMEOUT=900
MAX_LIFETIME=7200
DNS_TIMEOUT=8
DNS_SLOTS=threading.BoundedSemaphore(8)
DNS_CACHE={}
DNS_LOCK=threading.Lock()
TOKEN=re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
METHODS={'GET','HEAD','POST','PUT','PATCH','DELETE','OPTIONS','CONNECT'}
EXTRA_HTTP_TARGETS=frozenset({('lnsigo.mipt.ru',4000)})
HOP_HEADERS={'connection','proxy-connection','keep-alive','te','trailer','upgrade',
             'proxy-authorization','proxy-authenticate'}
BLOCKED_NETWORKS=tuple(ipaddress.ip_network(value) for value in (
    '192.0.0.0/24','192.88.99.0/24','64:ff9b::/96','64:ff9b:1::/48'))


class Denied(ValueError):
    pass


def hostname(value):
    if not value or any(ord(char)<=32 or ord(char)>=127 for char in value) or any(char in value for char in '%/@\\?#'):
        raise Denied('Invalid target host')
    value=value.lower()
    if value.endswith('.'):value=value[:-1]
    if value in {'localhost','host.docker.internal','gateway.docker.internal'} or value.endswith(('.localhost','.internal','.local')):
        raise Denied('Local target host')
    try:return str(ipaddress.ip_address(value))
    except ValueError:
        if (len(value)>253 or not re.fullmatch(r'[a-z0-9.-]+',value)
                or any(not label or len(label)>63 or label.startswith('-') or label.endswith('-') for label in value.split('.'))):
            raise Denied('Invalid target host')
        return value


def public_ip(value):
    if '%' in value:return False
    try:address=ipaddress.ip_address(value)
    except ValueError:return False
    if (not address.is_global or address.is_private or address.is_loopback or address.is_link_local
            or address.is_reserved or address.is_multicast or address.is_unspecified):return False
    if isinstance(address,ipaddress.IPv6Address):
        if address.ipv4_mapped is not None or address.sixtofour is not None or address.teredo is not None or address.is_site_local:
            return False
    return not any(address.version==network.version and address in network for network in BLOCKED_NETWORKS)


def _dns(host,port):
    key=(host,port)
    with DNS_LOCK:
        cached=DNS_CACHE.get(key)
        if cached and cached[0]>time.monotonic():return cached[1]
    if not DNS_SLOTS.acquire(blocking=False):raise Denied('DNS capacity reached')
    result=queue.Queue(maxsize=1)
    def lookup():
        try:result.put((True,socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)))
        except Exception:result.put((False,None))
        finally:DNS_SLOTS.release()
    try:threading.Thread(target=lookup,daemon=True).start()
    except Exception:
        DNS_SLOTS.release();raise Denied('DNS unavailable')
    try:ok,addresses=result.get(timeout=DNS_TIMEOUT)
    except queue.Empty:raise Denied('DNS timeout')
    if not ok:raise Denied('DNS unavailable')
    # Only cache validated public resolutions; stale public answers stay IP-pinned.
    validated=_validate_addresses(addresses,port)
    with DNS_LOCK:
        if len(DNS_CACHE)>=256:DNS_CACHE.pop(next(iter(DNS_CACHE)))
        DNS_CACHE[key]=(time.monotonic()+60,validated)
    return validated


def _validate_addresses(addresses,port):
    if not addresses or len(addresses)>64:raise Denied('Invalid DNS answer')
    result=[]
    for family,kind,protocol,_name,address in addresses:
        if (family not in (socket.AF_INET,socket.AF_INET6) or kind!=socket.SOCK_STREAM
                or len(address)!=(2 if family==socket.AF_INET else 4)
                or not public_ip(address[0]) or address[1]!=port
                or ipaddress.ip_address(address[0]).version!=(4 if family==socket.AF_INET else 6)
                or (family==socket.AF_INET6 and (len(address)!=4 or address[3]!=0))):
            raise Denied('Non-public DNS answer')
        item=(family,kind,protocol,address)
        if item not in result:result.append(item)
    return result


def resolve_public(host,port,resolver=None):
    host=hostname(host)
    if port not in (80,443) and (host,port) not in EXTRA_HTTP_TARGETS:raise Denied('Target port is not allowed')
    try:literal=ipaddress.ip_address(host)
    except ValueError:literal=None
    if literal is not None and not public_ip(host):raise Denied('Non-public literal target')
    if resolver is None:return _dns(host,port)
    return _validate_addresses(resolver(host,port,type=socket.SOCK_STREAM),port)


def connect_public(host,port,resolver=None):
    addresses=resolve_public(host,port,resolver)
    for family,kind,protocol,address in addresses[:16]:
        connection=socket.socket(family,kind,protocol)
        try:
            connection.settimeout(CONNECT_TIMEOUT)
            # Use the validated numeric sockaddr, never resolve the hostname again.
            connection.connect(address)
            return connection
        except OSError:connection.close()
    raise Denied('Public target unavailable')


def _authority(value,default_port):
    parsed=urlsplit('//'+value)
    if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
        raise Denied('Invalid target authority')
    host=hostname(parsed.hostname)
    return host,parsed.port or default_port


def parse_request(block):
    if len(block)>MAX_HEADER_BYTES or not block.endswith(b'\r\n\r\n'):raise Denied('Invalid headers')
    lines=block[:-4].split(b'\r\n')
    if not lines or len(lines)>65 or any(len(line)>MAX_LINE_BYTES for line in lines):raise Denied('Invalid headers')
    try:
        method,target,version=lines[0].decode('ascii').split(' ')
    except (ValueError,UnicodeError):raise Denied('Invalid request line')
    if method not in METHODS or version not in ('HTTP/1.0','HTTP/1.1'):raise Denied('Unsupported method or version')
    if not target or any(ord(char)<=32 or ord(char)>=127 for char in target):raise Denied('Invalid target URL')
    headers=[];values={}
    for line in lines[1:]:
        if b':' not in line or line[:1] in (b' ',b'\t'):raise Denied('Invalid header')
        name,value=line.split(b':',1)
        try:name=name.decode('ascii');value=value.decode('latin-1').strip(' \t')
        except UnicodeError:raise Denied('Invalid header')
        if not TOKEN.fullmatch(name) or any(ord(char)<32 or ord(char)==127 for char in value):raise Denied('Invalid header')
        lower=name.lower();headers.append((name,value));values.setdefault(lower,[]).append(value)
    if ('transfer-encoding' in values or 'upgrade' in values or 'expect' in values
            or len(values.get('content-length',[]))>1 or len(values.get('host',[]))>1):
        raise Denied('Unsupported request framing')
    length=values.get('content-length',['0'])[0]
    if not re.fullmatch(r'[0-9]{1,12}',length) or int(length)>MAX_BODY_BYTES:raise Denied('Invalid request length')
    length=int(length)
    tunnel=method=='CONNECT'
    if tunnel:
        host,port=_authority(target,443)
        if port!=443 or length:raise Denied('CONNECT is limited to TLS port 443')
        path=''
    else:
        parsed=urlsplit(target)
        if parsed.scheme!='http' or parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise Denied('Expected an absolute HTTP URL without credentials')
        host=hostname(parsed.hostname);port=parsed.port or 80
        if port!=80 and (host,port) not in EXTRA_HTTP_TARGETS:raise Denied('HTTP target is not allowed')
        path=(parsed.path or '/')+('?' + parsed.query if parsed.query else '')
    if values.get('host') and _authority(values['host'][0],443 if tunnel else 80)!=(host,port):
        raise Denied('Host header does not match target')
    hop=set(HOP_HEADERS)
    for value in values.get('connection',[]):
        for token in value.split(','):
            token=token.strip().lower()
            if not TOKEN.fullmatch(token):raise Denied('Invalid connection header')
            if token in {'host','content-length','transfer-encoding'}:raise Denied('Unsafe connection framing')
            hop.add(token)
    return {'method':method,'host':host,'port':port,'path':path,'length':length,'tunnel':tunnel,
            'headers':[(name,value) for name,value in headers if name.lower() not in hop|{'host','content-length'}]}


def read_headers(connection):
    data=bytearray()
    deadline=time.monotonic()+HEADER_TIMEOUT
    while b'\r\n\r\n' not in data:
        if len(data)>=MAX_HEADER_BYTES:raise Denied('Headers too large')
        remaining=deadline-time.monotonic()
        if remaining<=0:raise Denied('Header timeout')
        connection.settimeout(remaining)
        block=connection.recv(min(4096,MAX_HEADER_BYTES-len(data)))
        if not block:raise Denied('Incomplete headers')
        data.extend(block)
    end=data.index(b'\r\n\r\n')+4
    return bytes(data[:end]),bytes(data[end:])


def relay(client,upstream,*,tunnel=True,initial=b'',deadline=None):
    for connection in (client,upstream):connection.setblocking(False)
    peer={client:upstream,upstream:client}
    buffers={client:bytearray(),upstream:bytearray(initial)}
    reading={client:tunnel,upstream:True};closed_writes=set()
    started=last=time.monotonic()
    deadline=deadline or started+MAX_LIFETIME
    while any(reading.values()) or any(buffers.values()):
        now=time.monotonic()
        if now>deadline or now-last>IDLE_TIMEOUT:return
        for connection in (client,upstream):
            # HTTP framing already delimits the complete request. Sending FIN
            # here can make ASGI servers treat it as a disconnect and abandon
            # the response, especially while preparing a streaming completion.
            if not tunnel and connection is upstream:continue
            if not reading[peer[connection]] and not buffers[connection] and connection not in closed_writes:
                try:connection.shutdown(socket.SHUT_WR)
                except OSError:pass
                closed_writes.add(connection)
        readers=[connection for connection in peer if reading[connection] and len(buffers[peer[connection]])<BUFFER_BYTES]
        writers=[connection for connection in peer if buffers[connection]]
        ready_read,ready_write,_=select.select(readers,writers,[],1)
        for connection in ready_read:
            try:data=connection.recv(min(16384,BUFFER_BYTES-len(buffers[peer[connection]])))
            except BlockingIOError:continue
            if data:buffers[peer[connection]].extend(data);last=time.monotonic()
            else:reading[connection]=False
        for connection in ready_write:
            try:sent=connection.send(buffers[connection])
            except BlockingIOError:continue
            if not sent:return
            del buffers[connection][:sent];last=time.monotonic()


def error_response(status):
    body=b'Proxy request denied or unavailable.\n'
    return (f'HTTP/1.1 {status} Proxy Error\r\nConnection: close\r\nContent-Length: {len(body)}\r\n\r\n'.encode()+body)


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        upstream=None;response_started=False
        deadline=time.monotonic()+MAX_LIFETIME
        try:
            self.request.settimeout(HEADER_TIMEOUT)
            headers,remaining=read_headers(self.request)
            request=parse_request(headers)
            upstream=connect_public(request['host'],request['port'])
            if request['tunnel']:
                self.request.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                response_started=True
                relay(self.request,upstream,initial=remaining,deadline=deadline)
            else:
                host=request['host'];host='['+host+']' if ':' in host else host
                lines=[request['method']+' '+request['path']+' HTTP/1.1',f"Host: {host}:{request['port']}",
                       'Connection: close',f"Content-Length: {request['length']}"]
                lines.extend(name+': '+value for name,value in request['headers'])
                upstream.sendall(('\r\n'.join(lines)+'\r\n\r\n').encode('latin-1'))
                left=request['length']
                if remaining:
                    part=remaining[:left];upstream.sendall(part);left-=len(part)
                while left:
                    remaining_time=deadline-time.monotonic()
                    if remaining_time<=0:raise Denied('Request lifetime exceeded')
                    self.request.settimeout(min(HEADER_TIMEOUT,remaining_time))
                    part=self.request.recv(min(left,BUFFER_BYTES))
                    if not part:raise Denied('Incomplete request body')
                    upstream.sendall(part);left-=len(part)
                response_started=True
                relay(self.request,upstream,tunnel=False,deadline=deadline)
        except (Denied,ValueError,UnicodeError,OSError):
            if not response_started:
                try:self.request.sendall(error_response(403))
                except OSError:pass
        finally:
            if upstream:upstream.close()


class Server(socketserver.ThreadingMixIn,socketserver.TCPServer):
    allow_reuse_address=True
    daemon_threads=True
    request_queue_size=128
    def __init__(self,*args,**kwargs):
        self.slots=threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args,**kwargs)

    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            try:
                request.settimeout(1);request.sendall(error_response(503))
            except OSError:pass
            finally:self.shutdown_request(request)
            return
        try:super().process_request(request,address)
        except BaseException:
            self.slots.release();self.shutdown_request(request);raise

    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()

    def handle_error(self,request,address):
        # Do not log URLs, headers, credentials, tunnel contents or exception data.
        pass


if __name__=='__main__':
    with Server(('0.0.0.0',3128),Handler) as server:server.serve_forever(poll_interval=.5)
