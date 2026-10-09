//! HET / BET / (attributes) tables.
//!
//! The BET flag array is stored little-endian.  A HET slot holds only the top 8 bits of the
//! name hash, so a hit is a candidate that must be confirmed against the BET name hash;
//! probing continues on a mismatch.

use super::*;
use crate::mpq::crypto::{bits_get, bits_set, decrypt, encrypt, hash_string, jenkins_name_hash};

pub const BET_TABLE_SIZE_I: usize = 0;
pub const BET_ENTRY_COUNT_I: usize = 1;
pub const BET_UNKNOWN08_I: usize = 2;
pub const BET_TABLE_ENTRY_SIZE_I: usize = 3;
pub const BET_BIT_INDEX_FILEPOS_I: usize = 4;
pub const BET_BIT_INDEX_FILESIZE_I: usize = 5;
pub const BET_BIT_INDEX_CMPSIZE_I: usize = 6;
pub const BET_BIT_INDEX_FLAGINDEX_I: usize = 7;
pub const BET_BIT_INDEX_UNKNOWN_I: usize = 8;
pub const BET_BIT_COUNT_FILEPOS_I: usize = 9;
pub const BET_BIT_COUNT_FILESIZE_I: usize = 10;
pub const BET_BIT_COUNT_CMPSIZE_I: usize = 11;
pub const BET_BIT_COUNT_FLAGINDEX_I: usize = 12;
pub const BET_BIT_COUNT_UNKNOWN_I: usize = 13;
pub const BET_BIT_TOTAL_NAMEHASH2_I: usize = 14;
pub const BET_BIT_EXTRA_NAMEHASH2_I: usize = 15;
pub const BET_BIT_COUNT_NAMEHASH2_I: usize = 16;
pub const BET_NAMEHASH_ARRAY_SIZE_I: usize = 17;
pub const BET_FLAG_COUNT_I: usize = 18;

#[derive(Debug, Clone, Default)]
pub struct HetTable {
    pub table_size: u32,
    pub entry_count: u32,
    pub total_count: u32,
    pub name_hash_bit_size: u32,
    pub index_size_total: u32,
    pub index_size_extra: u32,
    pub index_size: u32,
    pub index_table_size: u32,
    pub name_hashes: Vec<u8>,
    pub index_bits: Vec<u8>,
}

impl HetTable {
    pub fn parse(data: &[u8]) -> Result<Self> {
        if data.len() < 12 || &data[0..4] != HET_SIG {
            return Err(MpqError("not a HET table".into()));
        }
        let data_size = u32::from_le_bytes([data[8], data[9], data[10], data[11]]) as usize;
        let body = decrypt(&data[12..12 + data_size], hash_string("(hash table)", HASH_FILE_KEY));
        let u = |o: usize| u32::from_le_bytes([body[o], body[o + 1], body[o + 2], body[o + 3]]);
        let mut t = HetTable {
            table_size: u(0),
            entry_count: u(4),
            total_count: u(8),
            name_hash_bit_size: u(12),
            index_size_total: u(16),
            index_size_extra: u(20),
            index_size: u(24),
            index_table_size: u(28),
            name_hashes: Vec::new(),
            index_bits: Vec::new(),
        };
        let off = 32usize;
        t.name_hashes = body[off..off + t.total_count as usize].to_vec();
        let off2 = off + t.total_count as usize;
        t.index_bits = body[off2..off2 + t.index_table_size as usize].to_vec();
        Ok(t)
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        let mut body = Vec::new();
        for v in [
            self.table_size, self.entry_count, self.total_count, self.name_hash_bit_size,
            self.index_size_total, self.index_size_extra, self.index_size, self.index_table_size,
        ] {
            body.extend_from_slice(&v.to_le_bytes());
        }
        body.extend_from_slice(&self.name_hashes);
        body.extend_from_slice(&self.index_bits);
        let mut out = Vec::new();
        out.extend_from_slice(HET_SIG);
        out.extend_from_slice(&1u32.to_le_bytes());
        out.extend_from_slice(&(body.len() as u32).to_le_bytes());
        out.extend_from_slice(&encrypt(&body, hash_string("(hash table)", HASH_FILE_KEY)));
        out
    }

    /// First candidate block index for a name (top-8-bit match only).
    pub fn candidate_index(&self, name: &str) -> u32 {
        if self.entry_count == 0 || self.total_count == 0 {
            return HASH_ENTRY_FREE;
        }
        let want = jenkins_name_hash(name, self.name_hash_bit_size);
        let name_hash1 = ((want >> (self.name_hash_bit_size - 8)) & 0xFF) as u8;
        let start = (want % self.total_count as u64) as u32;
        let mut index = start;
        loop {
            let slot = self.name_hashes[index as usize];
            if slot == HET_ENTRY_FREE {
                return HASH_ENTRY_FREE;
            }
            if slot == name_hash1 {
                let bitpos = (self.index_size_total * index) as usize;
                if (bitpos + self.index_size as usize + 7) / 8 <= self.index_bits.len() {
                    return bits_get(&self.index_bits, bitpos, self.index_size) as u32;
                }
            }
            index = (index + 1) % self.total_count;
            if index == start {
                return HASH_ENTRY_FREE;
            }
        }
    }
}

#[derive(Debug, Clone, Default)]
pub struct BetTable {
    pub head: [u32; 19],
    pub flags: Vec<u32>,
    pub file_bits: Vec<u8>,
    pub name_hash_bits: Vec<u8>,
}

impl BetTable {
    pub fn get(&self, i: usize) -> u32 { self.head[i] }

    pub fn parse(data: &[u8]) -> Result<Self> {
        if data.len() < 12 || &data[0..4] != BET_SIG {
            return Err(MpqError("not a BET table".into()));
        }
        let data_size = u32::from_le_bytes([data[8], data[9], data[10], data[11]]) as usize;
        let body = decrypt(&data[12..12 + data_size], hash_string("(block table)", HASH_FILE_KEY));
        let mut head = [0u32; 19];
        for (i, slot) in head.iter_mut().enumerate() {
            let o = i * 4;
            *slot = u32::from_le_bytes([body[o], body[o + 1], body[o + 2], body[o + 3]]);
        }
        let flag_count = head[BET_FLAG_COUNT_I] as usize;
        let mut off = 76usize;
        let mut flags = Vec::with_capacity(flag_count);
        for _ in 0..flag_count {
            flags.push(u32::from_le_bytes([body[off], body[off + 1], body[off + 2], body[off + 3]]));
            off += 4;
        }
        let file_bytes = ((head[BET_TABLE_ENTRY_SIZE_I] as usize) * (head[BET_ENTRY_COUNT_I] as usize) + 7) / 8;
        let file_bits = body[off..off + file_bytes].to_vec();
        off += file_bytes;
        let nh_bytes = head[BET_NAMEHASH_ARRAY_SIZE_I] as usize;
        let name_hash_bits = body[off..off + nh_bytes].to_vec();
        Ok(BetTable { head, flags, file_bits, name_hash_bits })
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        let mut body = Vec::new();
        for v in self.head {
            body.extend_from_slice(&v.to_le_bytes());
        }
        for f in &self.flags {
            body.extend_from_slice(&f.to_le_bytes());
        }
        body.extend_from_slice(&self.file_bits);
        body.extend_from_slice(&self.name_hash_bits);
        let mut out = Vec::new();
        out.extend_from_slice(BET_SIG);
        out.extend_from_slice(&1u32.to_le_bytes());
        out.extend_from_slice(&(body.len() as u32).to_le_bytes());
        out.extend_from_slice(&encrypt(&body, hash_string("(block table)", HASH_FILE_KEY)));
        out
    }

    /// (file_pos, file_size, cmp_size, flag_index) for entry i.
    pub fn entry(&self, i: usize) -> (u64, u64, u64, u64) {
        let base = (self.head[BET_TABLE_ENTRY_SIZE_I] as usize) * i;
        let g = |idx: usize, cnt: usize| bits_get(&self.file_bits, base + idx, cnt as u32);
        (
            g(self.head[BET_BIT_INDEX_FILEPOS_I] as usize, self.head[BET_BIT_COUNT_FILEPOS_I] as usize),
            g(self.head[BET_BIT_INDEX_FILESIZE_I] as usize, self.head[BET_BIT_COUNT_FILESIZE_I] as usize),
            g(self.head[BET_BIT_INDEX_CMPSIZE_I] as usize, self.head[BET_BIT_COUNT_CMPSIZE_I] as usize),
            g(self.head[BET_BIT_INDEX_FLAGINDEX_I] as usize, self.head[BET_BIT_COUNT_FLAGINDEX_I] as usize),
        )
    }
}

#[derive(Debug, Clone, Default)]
pub struct Attributes {
    pub version: u32,
    pub flags: u32,
    pub crcs: Option<Vec<u32>>,
    pub filetimes: Option<Vec<u64>>,
    pub md5s: Option<Vec<[u8; 16]>>,
}

impl Attributes {
    pub fn parse(data: &[u8], block_count: usize) -> Result<Self> {
        let version = u32::from_le_bytes([data[0], data[1], data[2], data[3]]);
        let flags = u32::from_le_bytes([data[4], data[5], data[6], data[7]]);
        let mut a = Attributes { version, flags, crcs: None, filetimes: None, md5s: None };
        let mut p = 8usize;
        if flags & ATTR_CRC32 != 0 {
            let mut v = Vec::with_capacity(block_count);
            for i in 0..block_count {
                let o = p + i * 4;
                v.push(u32::from_le_bytes([data[o], data[o + 1], data[o + 2], data[o + 3]]));
            }
            p += block_count * 4;
            a.crcs = Some(v);
        }
        if flags & ATTR_FILETIME != 0 {
            let mut v = Vec::with_capacity(block_count);
            for i in 0..block_count {
                let o = p + i * 8;
                let mut b = [0u8; 8];
                b.copy_from_slice(&data[o..o + 8]);
                v.push(u64::from_le_bytes(b));
            }
            p += block_count * 8;
            a.filetimes = Some(v);
        }
        if flags & ATTR_MD5 != 0 {
            let mut v = Vec::with_capacity(block_count);
            for i in 0..block_count {
                let o = p + i * 16;
                let mut d = [0u8; 16];
                d.copy_from_slice(&data[o..o + 16]);
                v.push(d);
            }
            a.md5s = Some(v);
        }
        Ok(a)
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        let mut out = Vec::new();
        out.extend_from_slice(&self.version.to_le_bytes());
        out.extend_from_slice(&self.flags.to_le_bytes());
        if let Some(c) = &self.crcs {
            for v in c { out.extend_from_slice(&v.to_le_bytes()); }
        }
        if let Some(c) = &self.filetimes {
            for v in c { out.extend_from_slice(&v.to_le_bytes()); }
        }
        if let Some(c) = &self.md5s {
            for v in c { out.extend_from_slice(v); }
        }
        out
    }
}

/// Build a HET table from an ordered name list (cf StormLib CreateHetTable).
pub fn build_het(names: &[String], name_hash_bit_size: u32) -> Result<HetTable> {
    let entry_count = names.len() as u32;
    let mut total_count = (entry_count * 4) / 3;
    if total_count < entry_count { total_count = entry_count; }
    if total_count == 0 { total_count = 1; }
    let index_size_total = necessary_bit_count(entry_count as u64).max(1);
    let index_table_size = ((total_count * index_size_total) + 7) / 8;
    let mut name_hashes = vec![0u8; total_count as usize];
    let mut index_bits = vec![0xFFu8; index_table_size as usize];
    let and_mask: u64 = if name_hash_bit_size == 64 { u64::MAX } else { (1u64 << name_hash_bit_size) - 1 };
    let or_mask: u64 = 1u64 << (name_hash_bit_size - 1);
    for (i, name) in names.iter().enumerate() {
        let fh = (jenkins_name_hash(name, name_hash_bit_size) & and_mask) | or_mask;
        let nh1 = ((fh >> (name_hash_bit_size - 8)) & 0xFF) as u8;
        let mut idx = (fh % total_count as u64) as u32;
        let start = idx;
        while name_hashes[idx as usize] != HET_ENTRY_FREE {
            idx = (idx + 1) % total_count;
            if idx == start {
                return Err(MpqError("HET table full".into()));
            }
        }
        name_hashes[idx as usize] = nh1;
        bits_set(&mut index_bits, (index_size_total * idx) as usize, index_size_total, i as u64);
    }
    Ok(HetTable {
        table_size: 32 + total_count + index_table_size,
        entry_count, total_count, name_hash_bit_size,
        index_size_total, index_size_extra: 0, index_size: index_size_total,
        index_table_size, name_hashes, index_bits,
    })
}

/// Build a BET table describing a block table (cf StormLib CreateBetHeader).
pub fn build_bet(blocks: &[(u32, u32, u32, u32)], names: &[String],
                 name_hash_bit_size: u32, unknown08: u32) -> BetTable {
    let entry_count = blocks.len() as u32;
    let mut flag_array: Vec<u32> = Vec::new();
    let mut flag_indexes: Vec<u32> = Vec::new();
    for b in blocks {
        let f = b.3;
        match flag_array.iter().position(|x| *x == f) {
            Some(p) => flag_indexes.push(p as u32),
            None => { flag_array.push(f); flag_indexes.push((flag_array.len() - 1) as u32); }
        }
    }
    let max_pos = blocks.iter().map(|b| b.0 as u64).max().unwrap_or(0);
    let max_fsize = blocks.iter().map(|b| b.2 as u64).max().unwrap_or(0);
    let max_csize = blocks.iter().map(|b| b.1 as u64).max().unwrap_or(0);
    let bc_pos = necessary_bit_count(max_pos);
    let bc_fsize = necessary_bit_count(max_fsize);
    let bc_csize = necessary_bit_count(max_csize);
    let bc_flag = necessary_bit_count(flag_indexes.iter().copied().max().unwrap_or(0) as u64 + 1);
    let bi_pos = 0;
    let bi_fsize = bi_pos + bc_pos;
    let bi_csize = bi_fsize + bc_fsize;
    let bi_flag = bi_csize + bc_csize;
    let bi_unknown = bi_flag + bc_flag;
    let table_entry_size = bi_unknown;
    let bit_total_nh2 = name_hash_bit_size - 8;
    let name_hash_array_size = ((bit_total_nh2 * entry_count) + 7) / 8;
    let table_size = 76 + (flag_array.len() as u32) * 4
        + ((table_entry_size * entry_count) + 7) / 8 + name_hash_array_size;
    let mut file_bits = vec![0u8; (((table_entry_size * entry_count) + 7) / 8) as usize];
    let mut hash_bits = vec![0u8; name_hash_array_size as usize];
    let and_mask: u64 = if name_hash_bit_size == 64 { u64::MAX } else { (1u64 << name_hash_bit_size) - 1 };
    let or_mask: u64 = 1u64 << (name_hash_bit_size - 1);
    for i in 0..blocks.len() {
        let base = (table_entry_size * i as u32) as usize;
        bits_set(&mut file_bits, base + bi_pos as usize, bc_pos, blocks[i].0 as u64);
        bits_set(&mut file_bits, base + bi_fsize as usize, bc_fsize, blocks[i].2 as u64);
        bits_set(&mut file_bits, base + bi_csize as usize, bc_csize, blocks[i].1 as u64);
        bits_set(&mut file_bits, base + bi_flag as usize, bc_flag, flag_indexes[i] as u64);
        let fh = (jenkins_name_hash(&names[i], name_hash_bit_size) & and_mask) | or_mask;
        bits_set(&mut hash_bits, (bit_total_nh2 * i as u32) as usize, bit_total_nh2,
                 fh & ((1u64 << bit_total_nh2) - 1));
    }
    let mut head = [0u32; 19];
    head[BET_TABLE_SIZE_I] = table_size;
    head[BET_ENTRY_COUNT_I] = entry_count;
    head[BET_UNKNOWN08_I] = unknown08;
    head[BET_TABLE_ENTRY_SIZE_I] = table_entry_size;
    head[BET_BIT_INDEX_FILEPOS_I] = bi_pos;
    head[BET_BIT_INDEX_FILESIZE_I] = bi_fsize;
    head[BET_BIT_INDEX_CMPSIZE_I] = bi_csize;
    head[BET_BIT_INDEX_FLAGINDEX_I] = bi_flag;
    head[BET_BIT_INDEX_UNKNOWN_I] = bi_unknown;
    head[BET_BIT_COUNT_FILEPOS_I] = bc_pos;
    head[BET_BIT_COUNT_FILESIZE_I] = bc_fsize;
    head[BET_BIT_COUNT_CMPSIZE_I] = bc_csize;
    head[BET_BIT_COUNT_FLAGINDEX_I] = bc_flag;
    head[BET_BIT_COUNT_UNKNOWN_I] = 0;
    head[BET_BIT_TOTAL_NAMEHASH2_I] = bit_total_nh2;
    head[BET_BIT_EXTRA_NAMEHASH2_I] = 0;
    head[BET_BIT_COUNT_NAMEHASH2_I] = bit_total_nh2;
    head[BET_NAMEHASH_ARRAY_SIZE_I] = name_hash_array_size;
    head[BET_FLAG_COUNT_I] = flag_array.len() as u32;
    BetTable { head, flags: flag_array, file_bits, name_hash_bits: hash_bits }
}

/// Per-block CRC32 / MD5.  (listfile) and (attributes) carry an all-zero MD5, and
/// (attributes) carries a zero CRC32.
pub fn build_attributes(blocks: &[(u32, u32, u32, u32)], names: &[String],
                        flags: u32, datas: &[Vec<u8>]) -> Attributes {
    use md5::{Digest, Md5};
    let n = blocks.len();
    let mut a = Attributes { version: 100, flags, crcs: None, filetimes: None, md5s: None };
    if flags & ATTR_CRC32 != 0 { a.crcs = Some(vec![0u32; n]); }
    if flags & ATTR_MD5 != 0 { a.md5s = Some(vec![[0u8; 16]; n]); }
    for i in 0..names.len().min(datas.len()) {
        if names[i] == "(attributes)" { continue; }
        if let Some(c) = a.crcs.as_mut() { c[i] = crc32fast::hash(&datas[i]); }
        if names[i] != "(listfile)" {
            if let Some(m) = a.md5s.as_mut() {
                let d = Md5::digest(&datas[i]);
                m[i].copy_from_slice(&d);
            }
        }
    }
    a
}
