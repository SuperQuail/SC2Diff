//! Reading and writing StarCraft II documents.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use md5::{Digest, Md5};

use super::tables::{build_attributes, build_bet, build_het, Attributes, BetTable, HetTable,
    BET_ENTRY_COUNT_I, BET_BIT_TOTAL_NAMEHASH2_I, BET_BIT_COUNT_NAMEHASH2_I};
use super::*;
use crate::mpq::compress::{best_compress, decompress_chunk};
use crate::mpq::crypto::{decrypt, encrypt, hash_string, jenkins_name_hash, bits_get};

#[derive(Debug, Clone, Copy)]
pub struct HashEntry {
    pub hash_a: u32,
    pub hash_b: u32,
    pub locale: u16,
    pub platform: u16,
    pub block_index: u32,
}

impl HashEntry {
    pub fn free(&self) -> bool { self.block_index == HASH_ENTRY_FREE }
}

#[derive(Debug, Clone, Copy)]
pub struct BlockEntry {
    pub file_pos: u32,
    pub cmp_size: u32,
    pub file_size: u32,
    pub flags: u32,
}

impl BlockEntry {
    pub fn exists(&self) -> bool { self.flags & FLAG_EXISTS != 0 }
}

pub struct Archive {
    pub path: PathBuf,
    pub raw: Vec<u8>,
    pub header_size: u32,
    pub archive_size: u64,
    pub format_version: u16,
    pub block_shift: u16,
    pub hash_pos: u32,
    pub block_pos: u32,
    pub hash_count: u32,
    pub block_count: u32,
    pub block_size: u32,
    pub bet_pos: u64,
    pub het_pos: u64,
    pub het_size: u64,
    pub bet_size: u64,
    pub hash_table: Vec<HashEntry>,
    pub block_table: Vec<BlockEntry>,
    pub het: Option<HetTable>,
    pub bet: Option<BetTable>,
    pub attributes: Option<Attributes>,
    pub names: Vec<String>,
    pub name_by_block: HashMap<u32, String>,
}

fn u16at(d: &[u8], o: usize) -> u16 { u16::from_le_bytes([d[o], d[o + 1]]) }
fn u32at(d: &[u8], o: usize) -> u32 { u32::from_le_bytes([d[o], d[o + 1], d[o + 2], d[o + 3]]) }
fn u64at(d: &[u8], o: usize) -> u64 {
    let mut b = [0u8; 8];
    b.copy_from_slice(&d[o..o + 8]);
    u64::from_le_bytes(b)
}

impl Archive {
    pub fn open(path: &Path) -> Result<Self> {
        let raw = std::fs::read(path).map_err(|e| MpqError(format!("{}: {e}", path.display())))?;
        if raw.len() < 32 || &raw[0..4] != MAGIC {
            return Err(MpqError(format!("not an MPQ archive: {}", path.display())));
        }
        let header_size = u32at(&raw, 4);
        let archive_size = u32at(&raw, 8) as u64;
        let format_version = u16at(&raw, 12);
        let block_shift = u16at(&raw, 14);
        let hash_pos = u32at(&raw, 16);
        let block_pos = u32at(&raw, 20);
        let hash_count = u32at(&raw, 24);
        let block_count = u32at(&raw, 28);
        let mut a = Archive {
            path: path.to_path_buf(), raw, header_size, archive_size, format_version,
            block_shift, hash_pos, block_pos, hash_count, block_count,
            block_size: 512u32 << block_shift,
            bet_pos: 0,
            het_pos: 0, het_size: 0, bet_size: 0,
            hash_table: Vec::new(), block_table: Vec::new(),
            het: None, bet: None, attributes: None,
            names: Vec::new(), name_by_block: HashMap::new(),
        };
        if header_size >= 68 {
            a.bet_pos = u64at(&a.raw, 52);
            a.het_pos = u64at(&a.raw, 60);
        }
        if header_size >= 208 {
            a.het_size = u64at(&a.raw, 92);
            a.bet_size = u64at(&a.raw, 100);
        }
        a.hash_table = a.parse_hash_table();
        a.block_table = a.parse_block_table();
        a.het = if a.het_pos != 0 { Some(HetTable::parse(&a.ext_table(a.het_pos, a.het_size))?) } else { None };
        a.bet = if a.bet_pos != 0 { Some(BetTable::parse(&a.ext_table(a.bet_pos, a.bet_size))?) } else { None };
        if let Some(ab) = a.try_read("(attributes)") {
            a.attributes = Attributes::parse(&ab, a.block_count as usize).ok();
        }
        if let Some(lf) = a.try_read("(listfile)") {
            a.names = String::from_utf8_lossy(&lf)
                .lines()
                .map(|l| l.trim().to_string())
                .filter(|l| !l.is_empty())
                .collect();
        }
        let mut map = HashMap::new();
        for n in a.names.clone() {
            if let Some(i) = a.find_block(&n) { map.insert(i, n); }
        }
        for s in BOOKKEEPING {
            if let Some(i) = a.find_block(s) { map.entry(i).or_insert_with(|| s.to_string()); }
        }
        a.name_by_block = map;
        Ok(a)
    }

    fn ext_table(&self, pos: u64, size: u64) -> Vec<u8> {
        let start = pos as usize;
        if size != 0 {
            return self.raw[start..start + size as usize].to_vec();
        }
        let data_size = u32at(&self.raw, start + 8) as usize;
        self.raw[start..start + 12 + data_size].to_vec()
    }

    fn parse_hash_table(&self) -> Vec<HashEntry> {
        let blob = &self.raw[self.hash_pos as usize..(self.hash_pos + self.hash_count * 16) as usize];
        let plain = decrypt(blob, hash_string("(hash table)", HASH_FILE_KEY));
        let mut v = Vec::with_capacity(self.hash_count as usize);
        for i in 0..self.hash_count as usize {
            let o = i * 16;
            v.push(HashEntry {
                hash_a: u32at(&plain, o), hash_b: u32at(&plain, o + 4),
                locale: u16at(&plain, o + 8), platform: u16at(&plain, o + 10),
                block_index: u32at(&plain, o + 12),
            });
        }
        let ok = v.iter().all(|e| e.free() || e.block_index < self.block_count) && v.iter().any(|e| e.free());
        if ok { v } else {
            (0..self.hash_count as usize).map(|i| {
                let o = i * 16;
                HashEntry {
                    hash_a: u32at(blob, o), hash_b: u32at(blob, o + 4),
                    locale: u16at(blob, o + 8), platform: u16at(blob, o + 10),
                    block_index: u32at(blob, o + 12),
                }
            }).collect()
        }
    }

    fn parse_block_table(&self) -> Vec<BlockEntry> {
        let blob = &self.raw[self.block_pos as usize..(self.block_pos + self.block_count * 16) as usize];
        let plain = decrypt(blob, hash_string("(block table)", HASH_FILE_KEY));
        let mut v: Vec<BlockEntry> = (0..self.block_count as usize).map(|i| {
            let o = i * 16;
            BlockEntry { file_pos: u32at(&plain, o), cmp_size: u32at(&plain, o + 4),
                         file_size: u32at(&plain, o + 8), flags: u32at(&plain, o + 12) }
        }).collect();
        let sane = v.iter().all(|b| !b.exists() || (b.file_pos as usize + b.cmp_size as usize) <= self.raw.len());
        if !sane {
            v = (0..self.block_count as usize).map(|i| {
                let o = i * 16;
                BlockEntry { file_pos: u32at(blob, o), cmp_size: u32at(blob, o + 4),
                             file_size: u32at(blob, o + 8), flags: u32at(blob, o + 12) }
            }).collect();
        }
        v
    }

    /// Classic hash-table lookup (linear probing).
    pub fn find_block(&self, name: &str) -> Option<u32> {
        if self.hash_count == 0 { return None; }
        let mut idx = hash_string(name, HASH_TABLE_OFFSET) & (self.hash_count - 1);
        let ha = hash_string(name, HASH_NAME_A);
        let hb = hash_string(name, HASH_NAME_B);
        for _ in 0..self.hash_count {
            let e = self.hash_table[idx as usize];
            if e.free() { return None; }
            if e.hash_a == ha && e.hash_b == hb && e.block_index < self.block_count {
                return Some(e.block_index);
            }
            idx = (idx + 1) & (self.hash_count - 1);
        }
        None
    }

    /// Confirmed lookup: HET candidate first, verified against the BET name hash, and
    /// probing continues on a mismatch (a HET slot holds only the top 8 bits).
    pub fn lookup_index(&self, name: &str) -> u32 {
        let het = match &self.het { Some(h) => h, None => return self.find_block(name).unwrap_or(HASH_ENTRY_FREE) };
        if het.entry_count == 0 { return HASH_ENTRY_FREE; }
        let want = jenkins_name_hash(name, het.name_hash_bit_size);
        let name_hash1 = ((want >> (het.name_hash_bit_size - 8)) & 0xFF) as u8;
        let start = (want % het.total_count as u64) as u32;
        let mut index = start;
        while het.name_hashes[index as usize] != HET_ENTRY_FREE {
            if het.name_hashes[index as usize] == name_hash1 {
                let bitpos = (het.index_size_total * index) as usize;
                if (bitpos + het.index_size as usize + 7) / 8 <= het.index_bits.len() {
                    let cand = bits_get(&het.index_bits, bitpos, het.index_size) as u32;
                    if cand < self.block_count {
                        match &self.bet {
                            None => return cand,
                            Some(bet) => {
                                if (cand as usize) < bet.get(BET_ENTRY_COUNT_I) as usize {
                                    let nh2 = bet.get(BET_BIT_TOTAL_NAMEHASH2_I);
                                    let cnt = bet.get(BET_BIT_COUNT_NAMEHASH2_I);
                                    let got = bits_get(&bet.name_hash_bits, (nh2 * cand) as usize, cnt);
                                    let mask = if cnt >= 64 { u64::MAX } else { (1u64 << cnt) - 1 };
                                    if want & mask == got { return cand; }
                                }
                            }
                        }
                    }
                }
            }
            index = (index + 1) % het.total_count;
            if index == start { break; }
        }
        HASH_ENTRY_FREE
    }

    pub fn block_name(&self, index: u32) -> Option<&str> {
        self.name_by_block.get(&index).map(|s| s.as_str())
    }

    pub fn read_block(&self, index: u32, name: &str) -> Result<Vec<u8>> {
        let b = self.block_table[index as usize];
        if !b.exists() { return Ok(Vec::new()); }
        if b.file_size == 0 { return Ok(Vec::new()); }
        let mut data = self.raw[b.file_pos as usize..(b.file_pos + b.cmp_size) as usize].to_vec();
        if b.flags & FLAG_ENCRYPTED != 0 {
            let base = name.replace('/', "\\");
            let base = base.rsplit('\\').next().unwrap_or(&base);
            let mut key = hash_string(base, HASH_FILE_KEY);
            if b.flags & FLAG_FIX_KEY != 0 { key = key.wrapping_add(b.file_pos); }
            data = decrypt(&data, key);
        }
        if b.flags & FLAG_SINGLE_UNIT != 0 {
            return if data.len() == b.file_size as usize { Ok(data) } else { decompress_chunk(&data, b.file_size as usize) };
        }
        let sectors = ((b.file_size + self.block_size - 1) / self.block_size) as usize;
        let mut out = Vec::with_capacity(b.file_size as usize);
        for i in 0..sectors {
            let o = i * 4;
            let lo = u32at(&data, o) as usize;
            let hi = u32at(&data, o + 4) as usize;
            let expected = std::cmp::min(self.block_size as usize, b.file_size as usize - out.len());
            out.extend_from_slice(&decompress_chunk(&data[lo..hi], expected)?);
        }
        Ok(out)
    }

    pub fn try_read(&self, name: &str) -> Option<Vec<u8>> {
        let i = self.find_block(name)?;
        self.read_block(i, name).ok()
    }

    pub fn read(&self, name: &str) -> Result<Vec<u8>> {
        match self.find_block(name) {
            Some(i) => self.read_block(i, name),
            None => Err(MpqError(format!("no component named {name}"))),
        }
    }

    pub fn verify_digests(&self) -> (bool, bool, bool, bool, bool) {
        if (self.header_size as usize) < HEADER_SIZE || self.raw.len() < HEADER_SIZE {
            return (false, false, false, false, false);
        }
        let h = &self.raw[..self.header_size as usize];
        let md5 = |d: &[u8]| Md5::digest(d).to_vec();
        let header_ok = md5(&h[..0xC0]) == h[0xC0..0xD0];
        let block_ok = md5(&self.raw[self.block_pos as usize..(self.block_pos + self.block_count * 16) as usize]) == h[0x70..0x80];
        let hash_ok = md5(&self.raw[self.hash_pos as usize..(self.hash_pos + self.hash_count * 16) as usize]) == h[0x80..0x90];
        let het_ok = self.het.as_ref().map(|_| md5(&self.raw[self.het_pos as usize..(self.het_pos + self.het_size) as usize]) == h[0xB0..0xC0]).unwrap_or(true);
        let bet_ok = self.bet.as_ref().map(|_| md5(&self.raw[self.bet_pos as usize..(self.bet_pos + self.bet_size) as usize]) == h[0xA0..0xB0]).unwrap_or(true);
        (header_ok, block_ok, hash_ok, het_ok, bet_ok)
    }
}

// --------------------------------------------------------------------------- //
// Document
// --------------------------------------------------------------------------- //
#[derive(Debug, Clone, Default)]
pub struct Document {
    pub entries: Vec<(String, Vec<u8>)>,
}

impl Document {
    pub fn open(path: &Path) -> Result<Self> {
        let a = Archive::open(path)?;
        let mut entries = Vec::new();
        for i in 0..a.block_count {
            if !a.block_table[i as usize].exists() { continue; }
            let name = match a.block_name(i) { Some(n) => n.to_string(), None => continue };
            let data = a.read_block(i, &name).unwrap_or_default();
            entries.push((name, data));
        }
        let mut ordered: Vec<(String, Vec<u8>)> = entries.iter().filter(|e| !is_bookkeeping(&e.0)).cloned().collect();
        for s in BOOKKEEPING {
            if let Some(e) = entries.iter().find(|e| e.0 == s) { ordered.push(e.clone()); }
        }
        Ok(Document { entries: ordered })
    }

    pub fn get(&self, name: &str) -> Option<&[u8]> {
        self.entries.iter().find(|e| e.0 == name).map(|e| e.1.as_slice())
    }

    pub fn component_names(&self) -> Vec<String> {
        self.entries.iter().filter(|e| !is_bookkeeping(&e.0)).map(|e| e.0.clone()).collect()
    }
}

// --------------------------------------------------------------------------- //
// writing
// --------------------------------------------------------------------------- //
#[derive(Debug, Clone)]
pub struct BuildOptions {
    pub hash_count: Option<u32>,
    pub block_shift: u16,
    pub unknown08: u32,
    pub attr_flags: u32,
    pub listfile: Option<Vec<u8>>,
    pub hints: HashMap<String, u32>,
    pub raw_chunk_size: u32,
}

impl Default for BuildOptions {
    fn default() -> Self {
        BuildOptions {
            hash_count: None, block_shift: 5, unknown08: 0x10,
            attr_flags: ATTR_CRC32 | ATTR_MD5, listfile: None,
            hints: HashMap::new(), raw_chunk_size: 0,
        }
    }
}

#[derive(Debug, Clone)]
pub struct BuildInfo {
    pub path: String,
    pub bytes: usize,
    pub entries: usize,
    pub blocks: usize,
    pub hash_entries: u32,
    pub het_bytes: usize,
    pub bet_bytes: usize,
    pub block_size: u32,
}

fn pack_entries(entries: &[(String, Vec<u8>)], block_size: usize,
                hints: &HashMap<String, u32>) -> Result<(Vec<u8>, Vec<(u32, u32, u32, u32)>)> {
    let mut body: Vec<u8> = Vec::new();
    let mut blocks: Vec<(u32, u32, u32, u32)> = Vec::new();
    for (name, data) in entries {
        let hint = hints.get(name).copied();
        let sectorised = hint.map(|h| h & FLAG_SINGLE_UNIT == 0).unwrap_or(false) && data.len() > block_size;
        if data.is_empty() {
            blocks.push(((HEADER_SIZE + body.len()) as u32, 0, 0, FLAG_EXISTS | FLAG_SINGLE_UNIT));
            continue;
        }
        if !sectorised {
            let (payload, compressed) = best_compress(data)?;
            let flags = FLAG_EXISTS | FLAG_SINGLE_UNIT | if compressed { FLAG_COMPRESS } else { 0 };
            blocks.push(((HEADER_SIZE + body.len()) as u32, payload.len() as u32, data.len() as u32, flags));
            body.extend_from_slice(&payload);
        } else {
            let sectors: Vec<&[u8]> = data.chunks(block_size).collect();
            let mut offsets: Vec<u32> = Vec::with_capacity(sectors.len() + 1);
            let mut pieces: Vec<Vec<u8>> = Vec::new();
            let mut running = ((sectors.len() + 1) * 4) as u32;
            offsets.push(running);
            let mut any_compressed = false;
            for chunk in &sectors {
                let (stored, compressed) = best_compress(chunk)?;
                any_compressed |= compressed;
                running += stored.len() as u32;
                offsets.push(running);
                pieces.push(stored);
            }
            let mut payload = Vec::with_capacity(running as usize);
            for o in &offsets { payload.extend_from_slice(&o.to_le_bytes()); }
            for p in &pieces { payload.extend_from_slice(p); }
            let flags = FLAG_EXISTS | if any_compressed { FLAG_COMPRESS } else { 0 };
            blocks.push(((HEADER_SIZE + body.len()) as u32, payload.len() as u32, data.len() as u32, flags));
            body.extend_from_slice(&payload);
        }
    }
    Ok((body, blocks))
}

fn build_hash_table(names: &[String], count: u32) -> Vec<HashEntry> {
    let mut entries = vec![HashEntry { hash_a: 0, hash_b: 0, locale: 0xFFFF, platform: 0xFFFF, block_index: HASH_ENTRY_FREE }; count as usize];
    for (i, name) in names.iter().enumerate() {
        let mut slot = hash_string(name, HASH_TABLE_OFFSET) & (count - 1);
        let start = slot;
        while !entries[slot as usize].free() {
            slot = (slot + 1) & (count - 1);
            if slot == start { break; }
        }
        entries[slot as usize] = HashEntry {
            hash_a: hash_string(name, HASH_NAME_A),
            hash_b: hash_string(name, HASH_NAME_B),
            locale: 0, platform: 0, block_index: i as u32,
        };
    }
    entries
}

pub fn build_document(entries: &[(String, Vec<u8>)], dest: &Path, opts: &BuildOptions) -> Result<BuildInfo> {
    let block_size = 512usize << opts.block_shift;
    let mut user: Vec<(String, Vec<u8>)> = entries.iter()
        .filter(|e| !is_bookkeeping(&e.0)).cloned().collect();

    let listfile: Vec<u8> = match &opts.listfile {
        Some(lf) => lf.clone(),
        None => user.iter().map(|e| e.0.clone()).collect::<Vec<_>>().join("\r\n").into_bytes()
                    .into_iter().chain("\r\n".bytes()).collect(),
    };
    let mut working = user.clone();
    working.push(("(listfile)".to_string(), listfile));

    // pass 1: provisional attributes so the block is sized like the real one
    let provisional = (working.len() + 1) + 1;
    let _ = provisional;
    let mut attrs = build_attributes(&vec![(0u32, 0u32, 0u32, 0u32); working.len() + 1],
        &working.iter().map(|e| e.0.clone()).chain(std::iter::once("(attributes)".to_string())).collect::<Vec<_>>(),
        opts.attr_flags, &working.iter().map(|e| e.1.clone()).chain(std::iter::once(Vec::new())).collect::<Vec<_>>());
    let mut all = working.clone();
    all.push(("(attributes)".to_string(), attrs.to_bytes()));

    let (mut body, mut blocks) = pack_entries(&all, block_size, &opts.hints)?;
    let names: Vec<String> = all.iter().map(|e| e.0.clone()).collect();
    for _ in 0..4 {
        attrs = build_attributes(&blocks, &names, opts.attr_flags,
            &all.iter().map(|e| e.1.clone()).collect::<Vec<_>>());
        let blob = attrs.to_bytes();
        if all.last().map(|e| e.1.clone()).unwrap_or_default() == blob { break; }
        let last = all.len() - 1;
        all[last].1 = blob;
        let packed = pack_entries(&all, block_size, &opts.hints)?;
        body = packed.0;
        blocks = packed.1;
    }
    user.clear();

    let hash_count = opts.hash_count.unwrap_or_else(|| {
        let mut c = 4u32;
        while c < std::cmp::max(4, all.len() as u32) { c <<= 1; }
        c
    });

    let het = build_het(&names, 64)?;
    let bet = build_bet(&blocks, &names, 64, opts.unknown08);
    let het_blob = het.to_bytes();
    let bet_blob = bet.to_bytes();

    let het_pos = HEADER_SIZE + body.len();
    let bet_pos = het_pos + het_blob.len();
    let hash_pos = bet_pos + bet_blob.len();
    let hash_entries = build_hash_table(&names, hash_count);
    let mut hash_plain = Vec::with_capacity(hash_entries.len() * 16);
    for e in &hash_entries {
        hash_plain.extend_from_slice(&e.hash_a.to_le_bytes());
        hash_plain.extend_from_slice(&e.hash_b.to_le_bytes());
        hash_plain.extend_from_slice(&e.locale.to_le_bytes());
        hash_plain.extend_from_slice(&e.platform.to_le_bytes());
        hash_plain.extend_from_slice(&e.block_index.to_le_bytes());
    }
    let hash_blob = encrypt(&hash_plain, hash_string("(hash table)", HASH_FILE_KEY));
    let block_pos = hash_pos + hash_blob.len();
    let mut block_plain = Vec::with_capacity(blocks.len() * 16);
    for b in &blocks {
        block_plain.extend_from_slice(&b.0.to_le_bytes());
        block_plain.extend_from_slice(&b.1.to_le_bytes());
        block_plain.extend_from_slice(&b.2.to_le_bytes());
        block_plain.extend_from_slice(&b.3.to_le_bytes());
    }
    let block_blob = encrypt(&block_plain, hash_string("(block table)", HASH_FILE_KEY));

    let archive_size = block_pos + block_blob.len();
    let mut header = vec![0u8; HEADER_SIZE];
    header[0..4].copy_from_slice(MAGIC);
    header[4..8].copy_from_slice(&(HEADER_SIZE as u32).to_le_bytes());
    header[8..12].copy_from_slice(&(archive_size as u32).to_le_bytes());
    header[12..14].copy_from_slice(&3u16.to_le_bytes());
    header[14..16].copy_from_slice(&opts.block_shift.to_le_bytes());
    header[16..20].copy_from_slice(&(hash_pos as u32).to_le_bytes());
    header[20..24].copy_from_slice(&(block_pos as u32).to_le_bytes());
    header[24..28].copy_from_slice(&hash_count.to_le_bytes());
    header[28..32].copy_from_slice(&(blocks.len() as u32).to_le_bytes());
    header[44..52].copy_from_slice(&(archive_size as u64).to_le_bytes());
    header[52..60].copy_from_slice(&(bet_pos as u64).to_le_bytes());
    header[60..68].copy_from_slice(&(het_pos as u64).to_le_bytes());
    header[68..76].copy_from_slice(&(hash_blob.len() as u64).to_le_bytes());
    header[76..84].copy_from_slice(&(block_blob.len() as u64).to_le_bytes());
    header[84..92].copy_from_slice(&0u64.to_le_bytes());
    header[92..100].copy_from_slice(&(het_blob.len() as u64).to_le_bytes());
    header[100..108].copy_from_slice(&(bet_blob.len() as u64).to_le_bytes());
    // dwRawChunkSize: 0.  SC2's writer puts the v4-only 16384 here on a header that
    // claims formatVersion 3, which turns on System_Mopaq raw-chunk MD5 verification
    // and makes the editor reject every content edit as e_fileCorrupt.
    header[108..112].copy_from_slice(&opts.raw_chunk_size.to_le_bytes());
    header[0x70..0x80].copy_from_slice(&Md5::digest(&block_blob));
    header[0x80..0x90].copy_from_slice(&Md5::digest(&hash_blob));
    header[0xA0..0xB0].copy_from_slice(&Md5::digest(&bet_blob));
    header[0xB0..0xC0].copy_from_slice(&Md5::digest(&het_blob));
    let self_hash = Md5::digest(&header[..0xC0]);
    header[0xC0..0xD0].copy_from_slice(&self_hash);

    let mut out = Vec::with_capacity(archive_size);
    out.extend_from_slice(&header);
    out.extend_from_slice(&body);
    out.extend_from_slice(&het_blob);
    out.extend_from_slice(&bet_blob);
    out.extend_from_slice(&hash_blob);
    out.extend_from_slice(&block_blob);
    if let Some(parent) = dest.parent() {
        if !parent.as_os_str().is_empty() { std::fs::create_dir_all(parent).ok(); }
    }
    std::fs::write(dest, &out).map_err(|e| MpqError(format!("{}: {e}", dest.display())))?;
    Ok(BuildInfo {
        path: dest.display().to_string(),
        bytes: out.len(),
        entries: all.len(),
        blocks: blocks.len(),
        hash_entries: hash_count,
        het_bytes: het_blob.len(),
        bet_bytes: bet_blob.len(),
        block_size: block_size as u32,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(path: &Path) {
        let entries = vec![
            ("Objects".to_string(), b"<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<PlacedObjects Version=\"27\"><ObjectUnit Id=\"1\" UnitType=\"Marine\"/></PlacedObjects>\n".to_vec()),
            ("Base.SC2Data\\GameData\\UnitData.xml".to_string(), b"<?xml version=\"1.0\"?>\n<Catalog><CUnit id=\"Marine\"><LifeMax value=\"45\"/></CUnit></Catalog>\n".to_vec()),
            ("MapScript.galaxy".to_string(), b"// script\nvoid InitMap() {}\n".to_vec()),
            ("t3HeightMap".to_string(), (0..4096u32).map(|i| (i * 7) as u8).collect()),
        ];
        build_document(&entries, path, &BuildOptions::default()).unwrap();
    }

    #[test]
    fn round_trips_byte_exactly() {
        let dir = std::env::temp_dir().join("sc2diff-rs-test");
        std::fs::create_dir_all(&dir).unwrap();
        let src = dir.join("a.SC2Map");
        let dst = dir.join("b.SC2Map");
        fixture(&src);
        let doc = Document::open(&src).unwrap();
        build_document(&doc.entries, &dst, &BuildOptions::default()).unwrap();
        let back = Document::open(&dst).unwrap();
        assert_eq!(doc.entries.len(), back.entries.len());
        for (n, d) in &doc.entries {
            assert_eq!(back.get(n).unwrap(), d.as_slice(), "component {n} differs");
        }
    }

    #[test]
    fn digest_block_and_raw_chunk_size() {
        let dir = std::env::temp_dir().join("sc2diff-rs-test2");
        std::fs::create_dir_all(&dir).unwrap();
        let src = dir.join("c.SC2Map");
        fixture(&src);
        let a = Archive::open(&src).unwrap();
        let (header, block, hash, het, bet) = a.verify_digests();
        assert!(header && block && hash && het && bet, "digest block must validate");
        assert_eq!(u32at(&a.raw, 108), 0, "dwRawChunkSize must be 0");
        assert_eq!(a.het.as_ref().unwrap().to_bytes(), a.ext_table(a.het_pos, a.het_size));
        assert_eq!(a.bet.as_ref().unwrap().to_bytes(), a.ext_table(a.bet_pos, a.bet_size));
    }
}
