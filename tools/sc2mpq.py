# -*- coding: utf-8 -*-
"""sc2mpq -- container layer for StarCraft II documents (.SC2Map / .SC2Mod).

Everything the diff/repack pipeline needs from the MPQ container:

  * header, formats 1..4  (SC2 5.x writes a 208-byte header with formatVersion=3)
  * classic hash table + block table (encrypted with the standard keys)
  * HET table 'HET\\x1a' -- bit-packed; 12 plaintext bytes then an encrypted body
  * BET table 'BET\\x1a' -- ditto, with a big-endian flag array
  * (attributes) version 100 -- per-block CRC32 / FILETIME / MD5
  * (listfile) / (signature)
  * multi-compression: zlib 0x02, pkware 0x08, bzip2 0x10, lzma 0x12, sparse 0x20

The oracle used throughout is byte-exact round-trip: parse a real archive, regenerate
HET / BET / (attributes) from the parsed model, and compare with what is on disk.  If
that holds for untouched data it holds for changed data too.

Bit arrays are LSB-first: bit k is bit (k % 8) of byte (k // 8); multi-bit values are
little-endian over that bit order (StormLib TMPQBits::GetBits/SetBits).

References: StormLib src (SBaseFileTable.cpp, SFileAttributes.cpp, StormLib.h),
jenkins/lookup3.c (public domain), Zezula's MPQ documentation.
"""
from __future__ import annotations

import bz2
import hashlib
import lzma
import os
import struct
import zlib
from dataclasses import dataclass

MAGIC = b"MPQ\x1a"
HET_SIG = b"HET\x1a"
BET_SIG = b"BET\x1a"

FLAG_IMPLODE = 0x00000100
FLAG_COMPRESS = 0x00000200
FLAG_ENCRYPTED = 0x00010000
FLAG_FIX_KEY = 0x00020000
FLAG_PATCH = 0x00100000
FLAG_SINGLE_UNIT = 0x01000000
FLAG_DELETE_MARKER = 0x02000000
FLAG_SECTOR_CRC = 0x04000000
FLAG_EXISTS = 0x80000000

MASK_ZLIB = 0x02
MASK_BZIP2 = 0x10
MASK_LZMA = 0x12
MASK_SPARSE = 0x20

ATTR_CRC32 = 0x01
ATTR_FILETIME = 0x02
ATTR_MD5 = 0x04

HET_ENTRY_FREE = 0x00
HASH_ENTRY_FREE = 0xFFFFFFFF
MD5_ZERO = b"\x00" * 16


# --------------------------------------------------------------------------- #
# crypto primitives
# --------------------------------------------------------------------------- #
def _crypt_table() -> list[int]:
    seed = 0x00100001
    table = [0] * 0x500
    for i1 in range(0x100):
        i2 = i1
        for _ in range(5):
            seed = (seed * 125 + 3) % 0x2AAAAB
            t1 = (seed & 0xFFFF) << 0x10
            seed = (seed * 125 + 3) % 0x2AAAAB
            t2 = seed & 0xFFFF
            table[i2] = (t1 | t2) & 0xFFFFFFFF
            i2 += 0x100
    return table


CRYPT = _crypt_table()

HASH_TABLE_OFFSET, HASH_NAME_A, HASH_NAME_B, HASH_FILE_KEY = 0, 1, 2, 3


def hash_string(text: str, hash_type: int) -> int:
    """Classic MPQ string hash (hash-table addressing and file keys)."""
    seed1, seed2 = 0x7FED7FED, 0xEEEEEEEE
    for byte in text.upper().encode("latin-1", "replace"):
        seed1 = (CRYPT[(hash_type << 8) + byte] ^ ((seed1 + seed2) & 0xFFFFFFFF)) & 0xFFFFFFFF
        seed2 = (byte + seed1 + seed2 + (seed2 << 5) + 3) & 0xFFFFFFFF
    return seed1


def decrypt(data: bytes, key: int) -> bytes:
    """MPQ block cipher.  Only whole 32-bit words are processed; a trailing 1-3 bytes
    are stored verbatim by the writer and must be preserved, not dropped."""
    seed1, seed2 = key & 0xFFFFFFFF, 0xEEEEEEEE
    out = bytearray()
    for i in range(0, len(data) & ~3, 4):
        dw = struct.unpack_from("<I", data, i)[0]
        seed2 = (seed2 + CRYPT[0x400 + (seed1 & 0xFF)]) & 0xFFFFFFFF
        dw = (dw ^ ((seed1 + seed2) & 0xFFFFFFFF)) & 0xFFFFFFFF
        seed1 = ((((~seed1 & 0xFFFFFFFF) << 0x15) + 0x11111111) | (seed1 >> 0x0B)) & 0xFFFFFFFF
        seed2 = (dw + seed2 + (seed2 << 5) + 3) & 0xFFFFFFFF
        out += struct.pack("<I", dw)
    out += data[len(data) & ~3:]
    return bytes(out)


def encrypt(data: bytes, key: int) -> bytes:
    """Inverse of decrypt.  NOT self-inverse: state advances on the plaintext word."""
    seed1, seed2 = key & 0xFFFFFFFF, 0xEEEEEEEE
    out = bytearray()
    for i in range(0, len(data) & ~3, 4):
        plain = struct.unpack_from("<I", data, i)[0]
        seed2 = (seed2 + CRYPT[0x400 + (seed1 & 0xFF)]) & 0xFFFFFFFF
        cipher = (plain ^ ((seed1 + seed2) & 0xFFFFFFFF)) & 0xFFFFFFFF
        seed1 = ((((~seed1 & 0xFFFFFFFF) << 0x15) + 0x11111111) | (seed1 >> 0x0B)) & 0xFFFFFFFF
        seed2 = (plain + seed2 + (seed2 << 5) + 3) & 0xFFFFFFFF
        out += struct.pack("<I", cipher)
    out += data[len(data) & ~3:]
    return bytes(out)


# --------------------------------------------------------------------------- #
# Jenkins lookup3 -- the 64-bit file name hash used by HET/BET
# --------------------------------------------------------------------------- #
_M32 = 0xFFFFFFFF


def _rot(x: int, k: int) -> int:
    return ((x << k) | (x >> (32 - k))) & _M32


def _mix(a: int, b: int, c: int):
    a = (a - c) & _M32; a ^= _rot(c, 4);  c = (c + b) & _M32
    b = (b - a) & _M32; b ^= _rot(a, 6);  a = (a + c) & _M32
    c = (c - b) & _M32; c ^= _rot(b, 8);  b = (b + a) & _M32
    a = (a - c) & _M32; a ^= _rot(c, 16); c = (c + b) & _M32
    b = (b - a) & _M32; b ^= _rot(a, 19); a = (a + c) & _M32
    c = (c - b) & _M32; c ^= _rot(b, 4);  b = (b + a) & _M32
    return a, b, c


def _final(a: int, b: int, c: int):
    c ^= b; c = (c - _rot(b, 14)) & _M32
    a ^= c; a = (a - _rot(c, 11)) & _M32
    b ^= a; b = (b - _rot(a, 25)) & _M32
    c ^= b; c = (c - _rot(b, 16)) & _M32
    a ^= c; a = (a - _rot(c, 4)) & _M32
    b ^= a; b = (b - _rot(a, 14)) & _M32
    c ^= b; c = (c - _rot(b, 24)) & _M32
    return a, b, c


def hashlittle2(key: bytes, init_a: int, init_b: int) -> tuple[int, int]:
    """lookup3 hashlittle2, little-endian.  Returns (pc_result, pb_result) i.e. (c, b)."""
    length = len(key)
    a = b = c = (0xDEADBEEF + length + init_a) & _M32
    c = (c + init_b) & _M32
    pos = 0
    remaining = length
    while remaining > 12:
        a = (a + struct.unpack_from("<I", key, pos)[0]) & _M32
        b = (b + struct.unpack_from("<I", key, pos + 4)[0]) & _M32
        c = (c + struct.unpack_from("<I", key, pos + 8)[0]) & _M32
        a, b, c = _mix(a, b, c)
        pos += 12
        remaining -= 12
    tail = key[pos:pos + remaining]
    # little-endian tail assembly, exactly mirroring lookup3's switch fallthrough
    if remaining >= 1:
        a = (a + tail[0]) & _M32
    if remaining >= 2:
        a = (a + (tail[1] << 8)) & _M32
    if remaining >= 3:
        a = (a + (tail[2] << 16)) & _M32
    if remaining >= 4:
        a = (a + (tail[3] << 24)) & _M32
    if remaining >= 5:
        b = (b + tail[4]) & _M32
    if remaining >= 6:
        b = (b + (tail[5] << 8)) & _M32
    if remaining >= 7:
        b = (b + (tail[6] << 16)) & _M32
    if remaining >= 8:
        b = (b + (tail[7] << 24)) & _M32
    if remaining >= 9:
        c = (c + tail[8]) & _M32
    if remaining >= 10:
        c = (c + (tail[9] << 8)) & _M32
    if remaining >= 11:
        c = (c + (tail[10] << 16)) & _M32
    if remaining >= 12:
        c = (c + (tail[11] << 24)) & _M32
    a, b, c = _final(a, b, c)
    return c, b


def jenkins_name_hash(name: str, name_hash_bit_size: int = 64) -> int:
    """SC2's 64-bit file name hash: lowercase, lookup3, then force the top bit.

    Mirrors StormLib HashStringJenkins + the AndMask/OrMask applied by the HET/BET code:
        FileNameHash = (jenkins(name) & AndMask64) | OrMask64
    with OrMask64 = 1 << (nameHashBitSize - 1).
    """
    normalized = name.encode("latin-1", "replace").lower()
    secondary, primary = hashlittle2(normalized, 2, 1)  # StormLib: pc=&secondary(2), pb=&primary(1)
    raw = ((primary << 32) | secondary) & 0xFFFFFFFFFFFFFFFF
    and_mask = (1 << name_hash_bit_size) - 1 if name_hash_bit_size != 64 else 0xFFFFFFFFFFFFFFFF
    or_mask = 1 << (name_hash_bit_size - 1)
    return (raw & and_mask) | or_mask


def necessary_bit_count(max_value: int) -> int:
    n = 0
    while max_value > 0:
        max_value >>= 1
        n += 1
    return n


# --------------------------------------------------------------------------- #
# LSB-first bit arrays
# --------------------------------------------------------------------------- #
def bits_get(buf: bytes, pos: int, n: int) -> int:
    v = 0
    for i in range(n):
        k = pos + i
        if (buf[k >> 3] >> (k & 7)) & 1:
            v |= 1 << i
    return v


def bits_set(buf: bytearray, pos: int, n: int, val: int) -> None:
    for i in range(n):
        k = pos + i
        byte = k >> 3
        if (val >> i) & 1:
            buf[byte] |= 1 << (k & 7)
        else:
            buf[byte] &= ~(1 << (k & 7)) & 0xFF


# --------------------------------------------------------------------------- #
# tables
# --------------------------------------------------------------------------- #
@dataclass
class BlockEntry:
    file_pos: int
    cmp_size: int
    file_size: int
    flags: int

    @property
    def exists(self) -> bool:
        return bool(self.flags & FLAG_EXISTS)

    def pack(self) -> bytes:
        return struct.pack("<IIII", self.file_pos, self.cmp_size, self.file_size, self.flags)


@dataclass
class HashEntry:
    hash_a: int
    hash_b: int
    locale: int
    platform: int
    block_index: int

    @property
    def free(self) -> bool:
        return self.block_index == HASH_ENTRY_FREE

    def pack(self) -> bytes:
        return struct.pack("<IIHHI", self.hash_a, self.hash_b, self.locale, self.platform, self.block_index)


@dataclass
class HetTable:
    """Parsed + regenerable HET table."""
    table_size: int = 0
    entry_count: int = 0
    total_count: int = 0
    name_hash_bit_size: int = 64
    index_size_total: int = 0
    index_size_extra: int = 0
    index_size: int = 0
    index_table_size: int = 0
    name_hashes: bytearray = None          # total_count bytes
    index_bits: bytearray = None           # index_table_size bytes

    def serialize_body(self) -> bytes:
        """Body = everything after the 12-byte ext header (the encrypted part)."""
        head = struct.pack("<IIIIIIII", self.table_size, self.entry_count, self.total_count,
                           self.name_hash_bit_size, self.index_size_total, self.index_size_extra,
                           self.index_size, self.index_table_size)
        return head + bytes(self.name_hashes) + bytes(self.index_bits)

    def to_bytes(self) -> bytes:
        body = self.serialize_body()
        enc = encrypt(body, hash_string("(hash table)", HASH_FILE_KEY))
        return HET_SIG + struct.pack("<II", 1, len(body)) + enc

    def get_file_index(self, name: str) -> int:
        """Resolve a name through the HET table alone.

        HET entries only store the top 8 bits of the name hash, so a hit is a *candidate*;
        StormLib and the SC2 engine then confirm it against the BET name hash.  Use
        MPQArchive.lookup_index for the confirmed answer.
        """
        if self.entry_count == 0:
            return HASH_ENTRY_FREE
        fn_hash = jenkins_name_hash(name, self.name_hash_bit_size)
        name_hash1 = (fn_hash >> (self.name_hash_bit_size - 8)) & 0xFF
        start = index = fn_hash % self.total_count
        while self.name_hashes[index] != HET_ENTRY_FREE:
            if self.name_hashes[index] == name_hash1:
                bitpos = self.index_size_total * index
                if (bitpos + self.index_size + 7) // 8 <= len(self.index_bits):
                    bi = bits_get(self.index_bits, bitpos, self.index_size)
                    if bi != (1 << self.index_size) - 1:
                        return bi
            index = (index + 1) % self.total_count
            if index == start:
                break
        return HASH_ENTRY_FREE


@dataclass
class BetTable:
    table_size: int = 0
    entry_count: int = 0
    unknown08: int = 0x10
    table_entry_size: int = 0
    bit_index_file_pos: int = 0
    bit_index_file_size: int = 0
    bit_index_cmp_size: int = 0
    bit_index_flag_index: int = 0
    bit_index_unknown: int = 0
    bit_count_file_pos: int = 0
    bit_count_file_size: int = 0
    bit_count_cmp_size: int = 0
    bit_count_flag_index: int = 0
    bit_count_unknown: int = 0
    bit_total_name_hash2: int = 0
    bit_extra_name_hash2: int = 0
    bit_count_name_hash2: int = 0
    name_hash_array_size: int = 0
    flag_count: int = 0
    flags: list = None            # flag_count uint32
    file_bits: bytearray = None   # bit-packed file table
    name_hash_bits: bytearray = None

    def serialize_body(self) -> bytes:
        head = struct.pack("<IIIIIIIIIIIIIIIIIII",
                           self.table_size, self.entry_count, self.unknown08, self.table_entry_size,
                           self.bit_index_file_pos, self.bit_index_file_size, self.bit_index_cmp_size,
                           self.bit_index_flag_index, self.bit_index_unknown,
                           self.bit_count_file_pos, self.bit_count_file_size, self.bit_count_cmp_size,
                           self.bit_count_flag_index, self.bit_count_unknown,
                           self.bit_total_name_hash2, self.bit_extra_name_hash2, self.bit_count_name_hash2,
                           self.name_hash_array_size, self.flag_count)
        # measured: SC2 stores the flag array little-endian
        flags = b"".join(struct.pack("<I", f) for f in self.flags)
        return head + flags + bytes(self.file_bits) + bytes(self.name_hash_bits)

    def to_bytes(self) -> bytes:
        body = self.serialize_body()
        enc = encrypt(body, hash_string("(block table)", HASH_FILE_KEY))
        return BET_SIG + struct.pack("<II", 1, len(body)) + enc

    def entry(self, i: int) -> tuple[int, int, int, int]:
        """Return (file_pos, file_size, cmp_size, flag_index) for entry i."""
        base = self.table_entry_size * i
        pos = bits_get(self.file_bits, base + self.bit_index_file_pos, self.bit_count_file_pos)
        size = bits_get(self.file_bits, base + self.bit_index_file_size, self.bit_count_file_size)
        cmp_ = bits_get(self.file_bits, base + self.bit_index_cmp_size, self.bit_count_cmp_size)
        flag = bits_get(self.file_bits, base + self.bit_index_flag_index, self.bit_count_flag_index)
        return pos, size, cmp_, flag


@dataclass
class Attributes:
    version: int = 100
    flags: int = ATTR_CRC32 | ATTR_MD5
    crcs: list = None
    filetimes: list = None
    md5s: list = None

    def to_bytes(self) -> bytes:
        out = struct.pack("<II", self.version, self.flags)
        if self.flags & ATTR_CRC32:
            out += struct.pack("<%dI" % len(self.crcs), *self.crcs)
        if self.flags & ATTR_FILETIME:
            out += struct.pack("<%dQ" % len(self.filetimes), *self.filetimes)
        if self.flags & ATTR_MD5:
            out += b"".join(self.md5s)
        return out


def parse_het(data: bytes) -> HetTable:
    if data[:4] != HET_SIG:
        raise ValueError("not a HET table")
    version, data_size = struct.unpack_from("<II", data, 4)
    body = decrypt(data[12:12 + data_size], hash_string("(hash table)", HASH_FILE_KEY))
    fields = struct.unpack_from("<IIIIIIII", body, 0)
    t = HetTable(*fields)
    off = 32
    t.name_hashes = bytearray(body[off:off + t.total_count]); off += t.total_count
    t.index_bits = bytearray(body[off:off + t.index_table_size])
    return t


def parse_bet(data: bytes) -> BetTable:
    if data[:4] != BET_SIG:
        raise ValueError("not a BET table")
    version, data_size = struct.unpack_from("<II", data, 4)
    body = decrypt(data[12:12 + data_size], hash_string("(block table)", HASH_FILE_KEY))
    fields = struct.unpack_from("<IIIIIIIIIIIIIIIIIII", body, 0)
    t = BetTable(*fields)
    off = 76
    t.flags = list(struct.unpack_from("<%dI" % t.flag_count, body, off)); off += 4 * t.flag_count
    file_bytes = (t.table_entry_size * t.entry_count + 7) // 8
    t.file_bits = bytearray(body[off:off + file_bytes]); off += file_bytes
    t.name_hash_bits = bytearray(body[off:off + t.name_hash_array_size])
    return t


def parse_attributes(data: bytes, block_count: int) -> Attributes:
    version, flags = struct.unpack_from("<II", data, 0)
    a = Attributes(version=version, flags=flags)
    p = 8
    if flags & ATTR_CRC32:
        a.crcs = list(struct.unpack_from("<%dI" % block_count, data, p)); p += 4 * block_count
    if flags & ATTR_FILETIME:
        a.filetimes = list(struct.unpack_from("<%dQ" % block_count, data, p)); p += 8 * block_count
    if flags & ATTR_MD5:
        a.md5s = [data[p + 16 * i: p + 16 * i + 16] for i in range(block_count)]; p += 16 * block_count
    return a


# --------------------------------------------------------------------------- #
# archive
# --------------------------------------------------------------------------- #
class MPQError(Exception):
    pass


class MPQArchive:
    """Read (and structurally understand) a StarCraft II document."""

    def __init__(self, path: str):
        self.path = path
        self.raw = open(path, "rb").read()
        d = self.raw
        if d[:4] != MAGIC:
            raise MPQError("not an MPQ archive: %s" % path)
        (self.header_size, self.archive_size, self.format_version, self.block_shift,
         self.hash_pos, self.block_pos, self.hash_count, self.block_count) = struct.unpack_from("<IIHHIIII", d, 4)
        self.block_size = 512 << self.block_shift
        self.ar_size64 = struct.unpack_from("<Q", d, 44)[0] if self.header_size >= 68 else 0
        self.bet_pos = struct.unpack_from("<Q", d, 52)[0] if self.header_size >= 68 else 0
        self.het_pos = struct.unpack_from("<Q", d, 60)[0] if self.header_size >= 68 else 0
        self.het_size = struct.unpack_from("<Q", d, 92)[0] if self.header_size >= 208 else 0
        self.bet_size = struct.unpack_from("<Q", d, 100)[0] if self.header_size >= 208 else 0

        self.hash_table = self._parse_hash_table()
        self.block_table = self._parse_block_table()
        self.het = parse_het(self._ext_table(self.het_pos, self.het_size)) if self.het_pos else None
        self.bet = parse_bet(self._ext_table(self.bet_pos, self.bet_size)) if self.bet_pos else None
        self._names = None
        self.attributes = None
        ab = self.try_read("(attributes)")
        if ab is not None:
            self.attributes = parse_attributes(ab, self.block_count)

    # -- structural -------------------------------------------------------- #
    def _ext_table(self, pos: int, size: int) -> bytes:
        if size:
            return self.raw[pos:pos + size]
        data_size = struct.unpack_from("<I", self.raw, pos + 8)[0]
        return self.raw[pos:pos + 12 + data_size]

    def _parse_hash_table(self) -> list:
        blob = self.raw[self.hash_pos:self.hash_pos + self.hash_count * 16]
        cand = decrypt(blob, hash_string("(hash table)", HASH_FILE_KEY))
        entries = [HashEntry(*struct.unpack_from("<IIHHI", cand, i * 16)) for i in range(self.hash_count)]
        ok = all(e.free or e.block_index < self.block_count for e in entries) and any(e.free for e in entries)
        if not ok:
            entries = [HashEntry(*struct.unpack_from("<IIHHI", blob, i * 16)) for i in range(self.hash_count)]
        return entries

    def _parse_block_table(self) -> list:
        blob = self.raw[self.block_pos:self.block_pos + self.block_count * 16]
        cand = decrypt(blob, hash_string("(block table)", HASH_FILE_KEY))
        entries = [BlockEntry(*struct.unpack_from("<IIII", cand, i * 16)) for i in range(self.block_count)]
        ok = all((not e.exists) or (e.file_pos + e.cmp_size <= len(self.raw)) for e in entries)
        if not ok:
            entries = [BlockEntry(*struct.unpack_from("<IIII", blob, i * 16)) for i in range(self.block_count)]
        return entries

    def serialized_hash_table(self) -> bytes:
        return encrypt(b"".join(e.pack() for e in self.hash_table), hash_string("(hash table)", HASH_FILE_KEY))

    def serialized_block_table(self) -> bytes:
        return encrypt(b"".join(e.pack() for e in self.block_table), hash_string("(block table)", HASH_FILE_KEY))

    # -- lookup ------------------------------------------------------------ #
    def find_block(self, name: str) -> int:
        idx = hash_string(name, HASH_TABLE_OFFSET) & (self.hash_count - 1)
        for _ in range(self.hash_count):
            e = self.hash_table[idx]
            if e.free:
                return -1
            if (e.hash_a == hash_string(name, HASH_NAME_A)
                    and e.hash_b == hash_string(name, HASH_NAME_B)
                    and e.block_index < self.block_count):
                return e.block_index
            idx = (idx + 1) & (self.hash_count - 1)
        return -1

    def lookup_index(self, name: str, index_size_total: int = None, index_size: int = None) -> int:
        """Confirmed file -> block lookup, following StormLib's GetFileIndex_Het exactly.

        When a HET table exists that is the path the engine takes.  A HET slot only stores
        the top 8 bits of the name hash, so every candidate whose high byte matches is
        confirmed against the full 64-bit name hash stored in the BET; probing continues
        past a mismatch (StormLib does the same - returning the first candidate would break
        archives that contain high-byte collisions).
        """
        if self.het is None:
            return self.find_block(name)
        het = self.het
        if het.entry_count == 0:
            return HASH_ENTRY_FREE
        want = jenkins_name_hash(name, het.name_hash_bit_size)
        name_hash1 = (want >> (het.name_hash_bit_size - 8)) & 0xFF
        start = index = want % het.total_count
        while het.name_hashes[index] != HET_ENTRY_FREE:
            if het.name_hashes[index] == name_hash1:
                bitpos = het.index_size_total * index
                if (bitpos + het.index_size + 7) // 8 <= len(het.index_bits):
                    cand = bits_get(het.index_bits, bitpos, het.index_size)
                    if cand < self.block_count:
                        if self.bet is None:
                            return cand
                        if cand < self.bet.entry_count:
                            got = bits_get(self.bet.name_hash_bits,
                                           self.bet.bit_total_name_hash2 * cand,
                                           self.bet.bit_count_name_hash2)
                            mask = (1 << self.bet.bit_count_name_hash2) - 1
                            if (want & mask) == got:
                                return cand
            index = (index + 1) % het.total_count
            if index == start:
                break
        return HASH_ENTRY_FREE

    def names(self) -> list:
        if self._names is None:
            listing = self.try_read("(listfile)")
            self._names = [l.strip() for l in listing.decode("utf-8", "replace").splitlines() if l.strip()] if listing else []
        return list(self._names)

    def block_name(self, index: int):
        for n in self.names():
            if self.find_block(n) == index:
                return n
        for s in ("(listfile)", "(attributes)", "(signature)", "(user data)"):
            if self.find_block(s) == index:
                return s
        return None

    # -- reading ----------------------------------------------------------- #
    def _file_key(self, name: str, block: BlockEntry) -> int:
        base = name.replace("/", "\\").split("\\")[-1]
        key = hash_string(base, HASH_FILE_KEY)
        if block.flags & FLAG_FIX_KEY:
            key = (key + block.file_pos) & 0xFFFFFFFF
        return key

    def read_block(self, index: int, name: str = ""):
        b = self.block_table[index]
        if not b.exists:
            return None
        if b.file_size == 0:
            return b""
        data = self.raw[b.file_pos:b.file_pos + b.cmp_size]
        if b.flags & FLAG_ENCRYPTED:
            data = decrypt(data, self._file_key(name, b))
        return self._decompress(data, b)

    def try_read(self, name: str):
        i = self.find_block(name)
        if i < 0:
            return None
        try:
            return self.read_block(i, name)
        except Exception:
            return None

    def read(self, name: str) -> bytes:
        i = self.find_block(name)
        if i < 0:
            raise KeyError(name)
        return self.read_block(i, name)

    def _decompress(self, data: bytes, b: BlockEntry) -> bytes:
        if b.flags & FLAG_IMPLODE:
            return _decompress_chunk(data, b.file_size)
        if b.flags & FLAG_SINGLE_UNIT:
            return data if len(data) == b.file_size else _decompress_chunk(data, b.file_size)
        sectors = (b.file_size + self.block_size - 1) // self.block_size
        offsets = struct.unpack_from("<%dI" % (sectors + 1), data, 0)
        out = bytearray()
        for i in range(sectors):
            expected = min(self.block_size, b.file_size - len(out))
            out += _decompress_chunk(data[offsets[i]:offsets[i + 1]], expected)
        return bytes(out)

    def extract_all(self, dest: str, verbose=False) -> list:
        os.makedirs(dest, exist_ok=True)
        written = []
        for i in range(self.block_count):
            if not self.block_table[i].exists:
                continue
            name = self.block_name(i)
            if not name:
                continue
            try:
                data = self.read_block(i, name)
            except Exception as exc:
                if verbose:
                    print("  ! %s: %s" % (name, exc))
                continue
            target = os.path.join(dest, name.replace("\\", os.sep))
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)
            written.append(name)
        return written

    # -- verification ------------------------------------------------------ #
    def verify(self, deep=True) -> dict:
        report = {"path": self.path, "blocks": self.block_count, "hash_entries": self.hash_count,
                  "format_version": self.format_version, "header_size": self.header_size,
                  "het": self.het is not None, "bet": self.bet is not None,
                  "attributes": self.attributes is not None}
        names = self.names()
        report["names"] = len(names)
        if self.het is not None:
            report["het_roundtrip_exact"] = (self.het.to_bytes()
                                             == self._ext_table(self.het_pos, self.het_size))
        if self.bet is not None:
            report["bet_roundtrip_exact"] = (self.bet.to_bytes()
                                             == self._ext_table(self.bet_pos, self.bet_size))
        if self.header_size >= HEADER_SIZE_V3:
            h = self.raw[:self.header_size]
            report["header_md5_valid"] = hashlib.md5(h[:0xC0]).digest() == h[0xC0:0xD0]
            report["block_table_md5_valid"] = hashlib.md5(
                self.raw[self.block_pos:self.block_pos + self.block_count * 16]).digest() == h[0x70:0x80]
            report["hash_table_md5_valid"] = hashlib.md5(
                self.raw[self.hash_pos:self.hash_pos + self.hash_count * 16]).digest() == h[0x80:0x90]
            report["het_md5_valid"] = (self.het is None or hashlib.md5(
                self.raw[self.het_pos:self.het_pos + self.het_size]).digest() == h[0xB0:0xC0])
            report["bet_md5_valid"] = (self.bet is None or hashlib.md5(
                self.raw[self.bet_pos:self.bet_pos + self.bet_size]).digest() == h[0xA0:0xB0])
        if deep and self.het is not None:
            report["het_vs_classic_mismatch"] = [
                n for n in names + ["(listfile)", "(attributes)"]
                if self.find_block(n) != self.lookup_index(n)]
        if deep and self.bet is not None:
            mism, flagmism = [], []
            for i in range(min(self.block_count, self.bet.entry_count)):
                pos, size, cmp_, flag = self.bet.entry(i)
                b = self.block_table[i]
                if (pos, size, cmp_) != (b.file_pos, b.file_size, b.cmp_size):
                    mism.append(i)
                if flag < len(self.bet.flags) and self.bet.flags[flag] != b.flags:
                    flagmism.append((i, hex(self.bet.flags[flag]), hex(b.flags)))
            report["bet_vs_blocktable_mismatch"] = mism
            report["bet_flag_mismatch"] = flagmism
        if deep and self.attributes is not None:
            ok_md5 = bad_md5 = ok_crc = bad_crc = 0
            exceptions = []
            for i in range(self.block_count):
                if not self.block_table[i].exists:
                    continue
                name = self.block_name(i) or ""
                try:
                    data = self.read_block(i, name)
                except Exception:
                    continue
                if data is None:
                    continue
                if self.attributes.md5s is not None:
                    if hashlib.md5(data).digest() == self.attributes.md5s[i]:
                        ok_md5 += 1
                    else:
                        bad_md5 += 1
                        exceptions.append(name)
                if self.attributes.crcs is not None:
                    if (zlib.crc32(data) & 0xFFFFFFFF) == self.attributes.crcs[i]:
                        ok_crc += 1
                    else:
                        bad_crc += 1
            report["md5_ok"], report["md5_bad"] = ok_md5, bad_md5
            report["crc_ok"], report["crc_bad"] = ok_crc, bad_crc
            report["md5_exceptions"] = exceptions
        return report


def _decompress_chunk(chunk: bytes, expected: int) -> bytes:
    if len(chunk) == expected:
        return chunk
    if not chunk:
        return b""
    mask = chunk[0]
    payload = chunk[1:]
    if mask == 0:
        return payload
    if mask & MASK_ZLIB:
        return zlib.decompress(payload)
    if mask & MASK_BZIP2:
        return bz2.decompress(payload)
    if mask == MASK_LZMA:
        return lzma.decompress(payload)
    if mask & MASK_SPARSE:
        return payload
    raise MPQError("unsupported compression mask 0x%02X" % mask)


# --------------------------------------------------------------------------- #
# writing
# --------------------------------------------------------------------------- #
HEADER_SIZE_V3 = 208
DEFAULT_BLOCK_SHIFT = 5

BOOKKEEPING = ("(listfile)", "(attributes)", "(signature)", "(user data)")


def _best_compress(data: bytes) -> tuple:
    """Compress with the two masks SC2 itself uses (bzip2 dominates, zlib appears too)
    and keep whichever is smallest; fall back to stored."""
    candidate = bytes([MASK_BZIP2]) + bz2.compress(data, 9)
    other = bytes([MASK_ZLIB]) + zlib.compress(data, 9)
    best = candidate if len(candidate) <= len(other) else other
    if len(best) < len(data):
        return best, True
    return data, False


def _compress_single(data: bytes) -> tuple:
    """Compress a single-unit block.  Returns (payload, compressed?)."""
    if not data:
        return b"", False
    return _best_compress(data)


def _compress_chunk(chunk: bytes) -> tuple:
    if not chunk:
        return b"", False
    return _best_compress(chunk)


def build_het(names: list, name_hash_bit_size: int = 64) -> HetTable:
    """Construct a HET table mapping names to block indices (cf StormLib CreateHetTable)."""
    entry_count = len(names)
    total_count = (entry_count * 4) // 3
    if total_count < entry_count:
        total_count = entry_count
    index_size_total = necessary_bit_count(entry_count) or 1
    index_table_size = ((total_count * index_size_total) + 7) // 8
    name_hashes = bytearray(total_count)              # 0 == HET_ENTRY_FREE
    index_bits = bytearray(b"\xFF" * index_table_size)
    and_mask = 0xFFFFFFFFFFFFFFFF if name_hash_bit_size == 64 else (1 << name_hash_bit_size) - 1
    or_mask = 1 << (name_hash_bit_size - 1)
    for i, name in enumerate(names):
        fh = (jenkins_name_hash(name, name_hash_bit_size) & and_mask) | or_mask
        nh1 = (fh >> (name_hash_bit_size - 8)) & 0xFF
        idx = fh % total_count
        start = idx
        while name_hashes[idx] != HET_ENTRY_FREE:
            idx = (idx + 1) % total_count
            if idx == start:
                raise MPQError("HET table full")
        name_hashes[idx] = nh1
        bits_set(index_bits, index_size_total * idx, index_size_total, i)
    return HetTable(table_size=32 + total_count + index_table_size,
                    entry_count=entry_count, total_count=total_count,
                    name_hash_bit_size=name_hash_bit_size,
                    index_size_total=index_size_total, index_size_extra=0,
                    index_size=index_size_total, index_table_size=index_table_size,
                    name_hashes=name_hashes, index_bits=index_bits)


def build_bet(blocks: list, names: list, name_hash_bit_size: int = 64, unknown08: int = 0x10) -> BetTable:
    """Construct a BET table describing the block table (cf StormLib CreateBetHeader)."""
    entry_count = len(blocks)
    flag_array, flag_indexes = [], []
    for b in blocks:
        if b.flags in flag_array:
            flag_indexes.append(flag_array.index(b.flags))
        else:
            flag_array.append(b.flags)
            flag_indexes.append(len(flag_array) - 1)
    max_pos = max((b.file_pos for b in blocks), default=0)
    max_fsize = max((b.file_size for b in blocks), default=0)
    max_csize = max((b.cmp_size for b in blocks), default=0)
    bc_pos = necessary_bit_count(max_pos)
    bc_fsize = necessary_bit_count(max_fsize)
    bc_csize = necessary_bit_count(max_csize)
    bc_flag = necessary_bit_count((max(flag_indexes) + 1) if flag_indexes else 0)
    bi_pos = 0
    bi_fsize = bi_pos + bc_pos
    bi_csize = bi_fsize + bc_fsize
    bi_flag = bi_csize + bc_csize
    bi_unknown = bi_flag + bc_flag
    table_entry_size = bi_unknown
    bit_total_nh2 = name_hash_bit_size - 8
    name_hash_array_size = ((bit_total_nh2 * entry_count) + 7) // 8
    table_size = (76 + len(flag_array) * 4
                  + ((table_entry_size * entry_count) + 7) // 8 + name_hash_array_size)
    file_bits = bytearray(((table_entry_size * entry_count) + 7) // 8)
    hash_bits = bytearray(name_hash_array_size)
    and_mask = 0xFFFFFFFFFFFFFFFF if name_hash_bit_size == 64 else (1 << name_hash_bit_size) - 1
    or_mask = 1 << (name_hash_bit_size - 1)
    for i, b in enumerate(blocks):
        base = table_entry_size * i
        bits_set(file_bits, base + bi_pos, bc_pos, b.file_pos)
        bits_set(file_bits, base + bi_fsize, bc_fsize, b.file_size)
        bits_set(file_bits, base + bi_csize, bc_csize, b.cmp_size)
        bits_set(file_bits, base + bi_flag, bc_flag, flag_indexes[i])
        fh = (jenkins_name_hash(names[i], name_hash_bit_size) & and_mask) | or_mask
        bits_set(hash_bits, bit_total_nh2 * i, bit_total_nh2, fh & ((1 << bit_total_nh2) - 1))
    return BetTable(table_size=table_size, entry_count=entry_count, unknown08=unknown08,
                    table_entry_size=table_entry_size,
                    bit_index_file_pos=bi_pos, bit_index_file_size=bi_fsize,
                    bit_index_cmp_size=bi_csize, bit_index_flag_index=bi_flag, bit_index_unknown=bi_unknown,
                    bit_count_file_pos=bc_pos, bit_count_file_size=bc_fsize,
                    bit_count_cmp_size=bc_csize, bit_count_flag_index=bc_flag, bit_count_unknown=0,
                    bit_total_name_hash2=bit_total_nh2, bit_extra_name_hash2=0,
                    bit_count_name_hash2=bit_total_nh2, name_hash_array_size=name_hash_array_size,
                    flag_count=len(flag_array), flags=flag_array,
                    file_bits=file_bits, name_hash_bits=hash_bits)


def build_hash_table(names: list, count: int) -> list:
    entries = [HashEntry(0, 0, 0xFFFF, 0xFFFF, HASH_ENTRY_FREE) for _ in range(count)]
    for i, name in enumerate(names):
        slot = hash_string(name, HASH_TABLE_OFFSET) & (count - 1)
        start = slot
        while not entries[slot].free:
            slot = (slot + 1) & (count - 1)
            if slot == start:
                raise MPQError("hash table full")
        entries[slot] = HashEntry(hash_string(name, HASH_NAME_A), hash_string(name, HASH_NAME_B), 0, 0, i)
    return entries


def build_attributes(blocks: list, names: list, version: int = 100,
                     flags: int = ATTR_CRC32 | ATTR_MD5, datas: list = None) -> Attributes:
    """Per-block CRC32 / MD5.  Measured on real archives: the (listfile) and (attributes)
    entries carry an all-zero MD5, and (attributes) carries a zero CRC32."""
    n = len(blocks)
    a = Attributes(version=version, flags=flags)
    if flags & ATTR_CRC32:
        a.crcs = [0] * n
    if flags & ATTR_MD5:
        a.md5s = [MD5_ZERO] * n
    for i, (name, data) in enumerate(zip(names, datas or [])):
        if name == "(attributes)":
            continue
        if flags & ATTR_CRC32:
            a.crcs[i] = zlib.crc32(data) & 0xFFFFFFFF
        if flags & ATTR_MD5 and name != "(listfile)":
            a.md5s[i] = hashlib.md5(data).digest()
    return a


def _pack_entries(entries: list, block_size: int, hints: dict = None) -> tuple:
    """Pack entries into block payloads and a block table.

    hints maps component name -> the flags the source archive used for it.  A component
    the source stored sector-by-sector is stored sector-by-sector again (fewer structural
    surprises for the engine); everything else is stored as one single unit, which is what
    the editor itself does for multi-megabyte components such as Triggers.
    MPQ_FILE_SECTOR_CRC is never emitted: it is optional, and the archives the editor
    writes rely on the offsets alone to locate sectors.
    """
    hints = hints or {}
    body = bytearray()
    blocks = []
    for name, data in entries:
        hint = hints.get(name)
        sectorised = (hint is not None) and not (hint & FLAG_SINGLE_UNIT) and len(data) > block_size
        if not data:
            blocks.append(BlockEntry(HEADER_SIZE_V3 + len(body), 0, 0, FLAG_EXISTS | FLAG_SINGLE_UNIT))
            continue
        if not sectorised:
            payload, compressed = _compress_single(data)
            flags = FLAG_EXISTS | FLAG_SINGLE_UNIT | (FLAG_COMPRESS if compressed else 0)
            blocks.append(BlockEntry(HEADER_SIZE_V3 + len(body), len(payload), len(data), flags))
            body += payload
        else:
            sectors = [data[i:i + block_size] for i in range(0, len(data), block_size)]
            pieces, offsets, any_compressed = [], [], False
            running = 4 * (len(sectors) + 1)
            offsets.append(running)
            for chunk in sectors:
                stored, compressed = _compress_chunk(chunk)
                any_compressed = any_compressed or compressed
                pieces.append(stored)
                running += len(stored)
                offsets.append(running)
            payload = struct.pack("<%dI" % len(offsets), *offsets) + b"".join(pieces)
            flags = FLAG_EXISTS | (FLAG_COMPRESS if any_compressed else 0)
            blocks.append(BlockEntry(HEADER_SIZE_V3 + len(body), len(payload), len(data), flags))
            body += payload
    return body, blocks


def build_document(entries: list, dest: str, *, hash_count: int = None, block_shift: int = DEFAULT_BLOCK_SHIFT,
                   unknown08: int = 0x10, attr_flags: int = ATTR_CRC32 | ATTR_MD5,
                   listfile: bytes = None, hints: dict = None, raw_chunk_size: int = 0) -> dict:
    """Write a format-3 StarCraft II document containing the given ordered (name, bytes) entries.

    Layout mirrors a real editor-written archive: 208-byte header, packed block data,
    HET, BET, classic hash table, classic block table.  (listfile) and (attributes) are
    generated here so the caller never has to keep them in sync.
    """
    block_size = 512 << block_shift
    user_entries = [(n, d) for (n, d) in entries if n not in BOOKKEEPING]

    # pass 1: provisional (listfile) so (attributes) can be sized correctly.
    # When the caller hands us the archive's own listfile (unchanged component set) keep
    # it byte-for-byte: that makes a no-op rebuild reproduce (attributes) exactly too.
    if listfile is None:
        listfile = ("\r\n".join(n for n, _ in user_entries) + "\r\n").encode("utf-8")
    working = user_entries + [("(listfile)", listfile)]
    _, blocks0 = _pack_entries(working + [("(attributes)", b"\x00" * (8 + 4 * (len(working) + 1)
                                                                      + 16 * (len(working) + 1)))],
                               block_size, hints)
    attrs = build_attributes(blocks0, [n for n, _ in working] + ["(attributes)"], flags=attr_flags,
                             datas=[d for _, d in working] + [b""])
    entries = working + [("(attributes)", attrs.to_bytes())]

    # pass 2: real layout, then refresh (attributes) for the final block geometry
    body, blocks = _pack_entries(entries, block_size, hints)
    names = [n for n, _ in entries]
    for _ in range(4):
        attrs = build_attributes(blocks, names, flags=attr_flags, datas=[d for _, d in entries])
        blob = attrs.to_bytes()
        if entries[-1][1] == blob:
            break
        entries[-1] = ("(attributes)", blob)
        body, blocks = _pack_entries(entries, block_size, hints)

    if hash_count is None:
        hash_count = 4
        while hash_count < max(4, len(entries)):
            hash_count <<= 1

    het = build_het(names)
    bet = build_bet(blocks, names, unknown08=unknown08)
    het_blob = het.to_bytes()
    bet_blob = bet.to_bytes()

    het_pos = HEADER_SIZE_V3 + len(body)
    bet_pos = het_pos + len(het_blob)
    hash_pos = bet_pos + len(bet_blob)
    hash_entries = build_hash_table(names, hash_count)
    hash_blob = encrypt(b"".join(e.pack() for e in hash_entries), hash_string("(hash table)", HASH_FILE_KEY))
    block_pos = hash_pos + len(hash_blob)
    block_blob = encrypt(b"".join(b.pack() for b in blocks), hash_string("(block table)", HASH_FILE_KEY))

    archive_size = block_pos + len(block_blob)
    header = bytearray(HEADER_SIZE_V3)
    struct.pack_into("<4sIIHHIIII", header, 0, MAGIC, HEADER_SIZE_V3, archive_size, 3, block_shift,
                     hash_pos, block_pos, hash_count, len(blocks))
    struct.pack_into("<Q", header, 44, archive_size)          # archiveSize64
    struct.pack_into("<Q", header, 52, bet_pos)               # betTablePos64
    struct.pack_into("<Q", header, 60, het_pos)               # hetTablePos64
    struct.pack_into("<Q", header, 68, len(hash_blob))        # hashTableSize64
    struct.pack_into("<Q", header, 76, len(block_blob))       # blockTableSize64
    struct.pack_into("<Q", header, 84, 0)                     # hiBlockTableSize64 (absent)
    struct.pack_into("<Q", header, 92, len(het_blob))         # hetTableSize64
    struct.pack_into("<Q", header, 100, len(bet_blob))        # betTableSize64
    # rawChunkSize stays 0.  SC2's own writer stamps 16384 here even though the header
    # claims formatVersion 3, and that is what enables Blizzard's System_Mopaq raw-chunk
    # MD5 verification path: any content edit is then rejected with e_fileCorrupt even when
    # (attributes) CRC32/MD5 are correct.  A genuine v3 archive has no raw-chunk MD5s, so
    # 0 is both correct and the setting the editor accepts.  Measured A/B: identical archive
    # with 16384 -> crash on the edited file; with 0 -> loads.
    struct.pack_into("<I", header, 108, raw_chunk_size)       # rawChunkSize (0 = v3-style, editor-accepted)

    # The v4 digest block (0x70..0xCF).  This is not decoration: StormLib refuses a header
    # whose own hash does not match, with the comment "Apparently, Starcraft II only accepts
    # MPQ headers where the MPQ header hash matches".  Layout measured on real documents:
    #   0x70 block table, 0x80 hash table, 0x90 hi-block table (zero when absent),
    #   0xA0 BET, 0xB0 HET, 0xC0 md5 of the first 0xC0 bytes of the header itself.
    header[0x70:0x80] = hashlib.md5(block_blob).digest()
    header[0x80:0x90] = hashlib.md5(hash_blob).digest()
    header[0x90:0xA0] = b"\x00" * 16
    header[0xA0:0xB0] = hashlib.md5(bet_blob).digest()
    header[0xB0:0xC0] = hashlib.md5(het_blob).digest()
    header[0xC0:0xD0] = hashlib.md5(bytes(header[:0xC0])).digest()

    payload = bytes(header) + bytes(body) + het_blob + bet_blob + hash_blob + block_blob
    parent = os.path.dirname(os.path.abspath(dest))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(dest, "wb") as fh:
        fh.write(payload)
    return {"path": dest, "bytes": len(payload), "entries": len(entries), "blocks": len(blocks),
            "hash_entries": hash_count, "het_bytes": len(het_blob), "bet_bytes": len(bet_blob),
            "block_size": block_size}


class Document:
    """A StarCraft II document as an ordered map of component name -> bytes."""

    def __init__(self, archive: MPQArchive):
        self.archive = archive
        raw_entries = []
        for i in range(archive.block_count):
            b = archive.block_table[i]
            if not b.exists:
                continue
            name = archive.block_name(i)
            if name is None:
                continue
            data = archive.read_block(i, name)
            raw_entries.append((name, data if data is not None else b""))
        specials = [n for n in BOOKKEEPING if any(n == x for x, _ in raw_entries)]
        self.entries = [(n, d) for n, d in raw_entries if n not in BOOKKEEPING]
        self.entries += [(n, next(d for x, d in raw_entries if x == n)) for n in specials]
        self.original_listfile = next((d for n, d in raw_entries if n == "(listfile)"), None)
        self.original_names = [n for n, _ in raw_entries if n not in BOOKKEEPING]
        self.layout_hints = {}
        for i in range(archive.block_count):
            blk = archive.block_table[i]
            if blk.exists:
                nm = archive.block_name(i)
                if nm:
                    self.layout_hints[nm] = blk.flags
        self.het_unknown08 = archive.bet.unknown08 if archive.bet else 0x10
        self.hash_count = archive.hash_count
        self.block_shift = archive.block_shift
        self.attr_flags = archive.attributes.flags if archive.attributes else (ATTR_CRC32 | ATTR_MD5)

    @classmethod
    def open(cls, path: str) -> "Document":
        return cls(MPQArchive(path))

    def get(self, name: str, default=None):
        for n, d in self.entries:
            if n == name:
                return d
        return default

    def set(self, name: str, data: bytes) -> None:
        for i, (n, _) in enumerate(self.entries):
            if n == name:
                self.entries[i] = (n, data)
                return
        insert_at = len(self.entries)
        for i, (n, _) in enumerate(self.entries):
            if n in BOOKKEEPING:
                insert_at = i
                break
        self.entries.insert(insert_at, (name, data))

    def remove(self, name: str) -> bool:
        for i, (n, _) in enumerate(self.entries):
            if n == name:
                del self.entries[i]
                return True
        return False

    def component_names(self) -> list:
        return [n for n, _ in self.entries if n not in BOOKKEEPING]

    def save(self, dest: str) -> dict:
        names_now = [n for n, _ in self.entries if n not in BOOKKEEPING]
        keep_listfile = self.original_listfile if names_now == self.original_names else None
        return build_document(self.entries, dest, hash_count=self.hash_count,
                              block_shift=self.block_shift, unknown08=self.het_unknown08,
                              attr_flags=self.attr_flags, listfile=keep_listfile,
                              hints=self.layout_hints)


