//! MPQ crypto, string hashing and the Jenkins name hash.

// --------------------------------------------------------------------------- //
// crypto
// --------------------------------------------------------------------------- //
static CRYPT: std::sync::OnceLock<[u32; 0x500]> = std::sync::OnceLock::new();

pub fn crypt_table() -> &'static [u32; 0x500] {
    CRYPT.get_or_init(|| {
        let mut table = [0u32; 0x500];
        let mut seed: u32 = 0x0010_0001;
        for i1 in 0..0x100usize {
            let mut i2 = i1;
            for _ in 0..5 {
                seed = seed.wrapping_mul(125).wrapping_add(3) % 0x2AAAAB;
                let t1 = (seed & 0xFFFF) << 0x10;
                seed = seed.wrapping_mul(125).wrapping_add(3) % 0x2AAAAB;
                let t2 = seed & 0xFFFF;
                table[i2] = t1 | t2;
                i2 += 0x100;
            }
        }
        table
    })
}

pub const HASH_TABLE_OFFSET: u32 = 0;
pub const HASH_NAME_A: u32 = 1;
pub const HASH_NAME_B: u32 = 2;
pub const HASH_FILE_KEY: u32 = 3;

pub fn hash_string(text: &str, hash_type: u32) -> u32 {
    let table = crypt_table();
    let mut seed1: u32 = 0x7FED_7FED;
    let mut seed2: u32 = 0xEEEE_EEEE;
    // No mask: the table has 0x500 entries and (hash_type << 8) + byte stays below that.
    let base = (hash_type as usize) << 8;
    for byte in text.to_uppercase().bytes() {
        let idx = base + byte as usize;
        seed1 = table[idx] ^ seed1.wrapping_add(seed2);
        seed2 = (byte as u32)
            .wrapping_add(seed1)
            .wrapping_add(seed2)
            .wrapping_add(seed2 << 5)
            .wrapping_add(3);
    }
    seed1
}

/// MPQ block cipher.  Only whole 32-bit words are processed; trailing bytes are copied
/// through unchanged.
pub fn decrypt(data: &[u8], key: u32) -> Vec<u8> {
    let table = crypt_table();
    let mut seed1 = key;
    let mut seed2: u32 = 0xEEEE_EEEE;
    let n = data.len() & !3;
    let mut out = Vec::with_capacity(data.len());
    for i in (0..n).step_by(4) {
        let mut dw = u32::from_le_bytes([data[i], data[i + 1], data[i + 2], data[i + 3]]);
        seed2 = seed2.wrapping_add(table[0x400 + (seed1 & 0xFF) as usize]);
        dw ^= seed1.wrapping_add(seed2);
        seed1 = ((!seed1).wrapping_shl(0x15)).wrapping_add(0x1111_1111) | (seed1 >> 0x0B);
        seed2 = dw.wrapping_add(seed2).wrapping_add(seed2 << 5).wrapping_add(3);
        out.extend_from_slice(&dw.to_le_bytes());
    }
    out.extend_from_slice(&data[n..]);
    out
}

/// Inverse of `decrypt`.  NOT self-inverse: the keystream advances on the *plaintext* word.
pub fn encrypt(data: &[u8], key: u32) -> Vec<u8> {
    let table = crypt_table();
    let mut seed1 = key;
    let mut seed2: u32 = 0xEEEE_EEEE;
    let n = data.len() & !3;
    let mut out = Vec::with_capacity(data.len());
    for i in (0..n).step_by(4) {
        let plain = u32::from_le_bytes([data[i], data[i + 1], data[i + 2], data[i + 3]]);
        seed2 = seed2.wrapping_add(table[0x400 + (seed1 & 0xFF) as usize]);
        let cipher = plain ^ seed1.wrapping_add(seed2);
        seed1 = ((!seed1).wrapping_shl(0x15)).wrapping_add(0x1111_1111) | (seed1 >> 0x0B);
        seed2 = plain.wrapping_add(seed2).wrapping_add(seed2 << 5).wrapping_add(3);
        out.extend_from_slice(&cipher.to_le_bytes());
    }
    out.extend_from_slice(&data[n..]);
    out
}

// --------------------------------------------------------------------------- //
// Jenkins lookup3 -- the 64-bit file name hash used by HET/BET
// --------------------------------------------------------------------------- //
fn rot(x: u32, k: u32) -> u32 {
    x.rotate_left(k)
}

fn mix(mut a: u32, mut b: u32, mut c: u32) -> (u32, u32, u32) {
    a = a.wrapping_sub(c); a ^= rot(c, 4);  c = c.wrapping_add(b);
    b = b.wrapping_sub(a); b ^= rot(a, 6);  a = a.wrapping_add(c);
    c = c.wrapping_sub(b); c ^= rot(b, 8);  b = b.wrapping_add(a);
    a = a.wrapping_sub(c); a ^= rot(c, 16); c = c.wrapping_add(b);
    b = b.wrapping_sub(a); b ^= rot(a, 19); a = a.wrapping_add(c);
    c = c.wrapping_sub(b); c ^= rot(b, 4);  b = b.wrapping_add(a);
    (a, b, c)
}

fn final_mix(mut a: u32, mut b: u32, mut c: u32) -> (u32, u32, u32) {
    c ^= b; c = c.wrapping_sub(rot(b, 14));
    a ^= c; a = a.wrapping_sub(rot(c, 11));
    b ^= a; b = b.wrapping_sub(rot(a, 25));
    c ^= b; c = c.wrapping_sub(rot(b, 16));
    a ^= c; a = a.wrapping_sub(rot(c, 4));
    b ^= a; b = b.wrapping_sub(rot(a, 14));
    c ^= b; c = c.wrapping_sub(rot(b, 24));
    (a, b, c)
}

/// lookup3 hashlittle2 (little-endian).  Returns (pc_result, pb_result) = (c, b).
pub fn hashlittle2(key: &[u8], init_a: u32, init_b: u32) -> (u32, u32) {
    let length = key.len() as u32;
    let mut a = 0xDEAD_BEEFu32.wrapping_add(length).wrapping_add(init_a);
    let mut b = a;
    let mut c = a.wrapping_add(init_b);
    let mut pos = 0usize;
    let mut remaining = key.len();
    while remaining > 12 {
        a = a.wrapping_add(u32::from_le_bytes([key[pos], key[pos + 1], key[pos + 2], key[pos + 3]]));
        b = b.wrapping_add(u32::from_le_bytes([key[pos + 4], key[pos + 5], key[pos + 6], key[pos + 7]]));
        c = c.wrapping_add(u32::from_le_bytes([key[pos + 8], key[pos + 9], key[pos + 10], key[pos + 11]]));
        let (na, nb, nc) = mix(a, b, c);
        a = na; b = nb; c = nc;
        pos += 12;
        remaining -= 12;
    }
    let tail = &key[pos..];
    fn add(t: &[u8], i: usize) -> u32 { if i < t.len() { (t[i] as u32) << (8 * i) } else { 0 } }
    a = a.wrapping_add(add(tail, 0)).wrapping_add(add(tail, 1)).wrapping_add(add(tail, 2)).wrapping_add(add(tail, 3));
    b = b.wrapping_add(add(tail, 4)).wrapping_add(add(tail, 5)).wrapping_add(add(tail, 6)).wrapping_add(add(tail, 7));
    c = c.wrapping_add(add(tail, 8)).wrapping_add(add(tail, 9)).wrapping_add(add(tail, 10)).wrapping_add(add(tail, 11));
    let (_, fb, fc) = final_mix(a, b, c);
    (fc, fb)
}

/// SC2's 64-bit name hash: lowercase, lookup3, then force the top bit (OrMask).
pub fn jenkins_name_hash(name: &str, name_hash_bit_size: u32) -> u64 {
    let normalized: Vec<u8> = name.to_lowercase().bytes().collect();
    let (secondary, primary) = hashlittle2(&normalized, 2, 1);
    let raw = ((primary as u64) << 32) | secondary as u64;
    let and_mask: u64 = if name_hash_bit_size == 64 { u64::MAX } else { (1u64 << name_hash_bit_size) - 1 };
    let or_mask: u64 = 1u64 << (name_hash_bit_size - 1);
    (raw & and_mask) | or_mask
}

pub fn necessary_bit_count(mut max_value: u64) -> u32 {
    let mut n = 0;
    while max_value > 0 {
        max_value >>= 1;
        n += 1;
    }
    n
}

// --------------------------------------------------------------------------- //
// LSB-first bit arrays
// --------------------------------------------------------------------------- //
pub fn bits_get(buf: &[u8], pos: usize, n: u32) -> u64 {
    let mut v: u64 = 0;
    for i in 0..n as usize {
        let k = pos + i;
        if k / 8 < buf.len() && (buf[k / 8] >> (k % 8)) & 1 == 1 {
            v |= 1u64 << i;
        }
    }
    v
}

pub fn bits_set(buf: &mut [u8], pos: usize, n: u32, val: u64) {
    for i in 0..n as usize {
        let k = pos + i;
        if k / 8 >= buf.len() {
            break;
        }
        let bit = (val >> i) & 1;
        if bit == 1 {
            buf[k / 8] |= 1 << (k % 8);
        } else {
            buf[k / 8] &= !(1 << (k % 8));
        }
    }
}