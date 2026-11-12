"""Bounded PNG attachments. No remote URLs or caller-selected filesystem paths."""
import base64
import hashlib
import struct
import zlib

MAX_PHOTO = 10 * 1024 * 1024


def inspect_png(data):
    if not 45 <= len(data) <= MAX_PHOTO or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Expected a PNG of at most 10 MiB')
    offset, width, height, image = 8, 0, 0, False
    while offset < len(data):
        if offset + 12 > len(data):raise ValueError('Truncated PNG')
        size = struct.unpack('>I', data[offset:offset+4])[0]
        kind = data[offset+4:offset+8]
        end = offset + size + 12
        if end > len(data):raise ValueError('Truncated PNG chunk')
        if zlib.crc32(data[offset+4:end-4]) != struct.unpack('>I',data[end-4:end])[0]:
            raise ValueError('Invalid PNG checksum')
        if offset == 8:
            if kind != b'IHDR' or size != 13:raise ValueError('Invalid PNG header')
            width,height = struct.unpack('>II',data[offset+8:offset+16])
            if min(width,height) < 1 or width+height > 10000 or max(width,height)/min(width,height)>20:
                raise ValueError('PNG dimensions exceed Telegram photo limits')
        if kind == b'IDAT':image=True
        if kind == b'IEND':
            if size or end != len(data) or not image:raise ValueError('Invalid PNG ending')
            return {'sha256':hashlib.sha256(data).hexdigest(),'width':width,'height':height,'mime':'image/png'}
        offset=end
    raise ValueError('Missing PNG ending')


def decode_photo(value):
    if not isinstance(value,dict) or set(value)!={'data','sha256'} or not isinstance(value['data'],str):
        raise ValueError('photo requires base64 data and sha256')
    if len(value['data']) > ((MAX_PHOTO+2)//3)*4:raise ValueError('Photo too large')
    try:data=base64.b64decode(value['data'],validate=True)
    except (ValueError,TypeError):raise ValueError('Invalid photo encoding') from None
    meta=inspect_png(data)
    if value['sha256']!=meta['sha256']:raise ValueError('Photo checksum mismatch')
    return data,meta
