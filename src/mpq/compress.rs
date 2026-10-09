//! Component compression.
//!
//! 0x02 is zlib, 0x10 is bzip2.  A block flagged COMPRESS whose cmp_size equals its
//! file_size is stored verbatim.

use std::io::{Read, Write};

use super::{MpqError, Result, MASK_BZIP2, MASK_ZLIB};

pub fn decompress_chunk(chunk: &[u8], expected: usize) -> Result<Vec<u8>> {
    if chunk.len() == expected {
        return Ok(chunk.to_vec());
    }
    if chunk.is_empty() {
        return Ok(Vec::new());
    }
    let mask = chunk[0];
    let payload = &chunk[1..];
    if mask == 0 {
        return Ok(payload.to_vec());
    }
    if mask & MASK_ZLIB != 0 {
        let mut out = Vec::with_capacity(expected);
        flate2::read::ZlibDecoder::new(payload)
            .read_to_end(&mut out)
            .map_err(|e| MpqError(format!("zlib: {e}")))?;
        return Ok(out);
    }
    if mask & MASK_BZIP2 != 0 {
        let mut out = Vec::with_capacity(expected);
        bzip2::read::BzDecoder::new(payload)
            .read_to_end(&mut out)
            .map_err(|e| MpqError(format!("bzip2: {e}")))?;
        return Ok(out);
    }
    Err(MpqError(format!("unsupported compression mask 0x{mask:02X}")))
}

/// Compress with whichever of bzip2 / zlib is smaller.  Returns (payload, compressed).
/// A false result means the caller should store the bytes verbatim.
pub fn best_compress(data: &[u8]) -> Result<(Vec<u8>, bool)> {
    if data.is_empty() {
        return Ok((Vec::new(), false));
    }
    let mut bz = Vec::new();
    {
        let mut w = bzip2::write::BzEncoder::new(&mut bz, bzip2::Compression::best());
        w.write_all(data).map_err(|e| MpqError(format!("bzip2 encode: {e}")))?;
        w.finish().map_err(|e| MpqError(format!("bzip2 finish: {e}")))?;
    }
    let mut zl = Vec::new();
    {
        let mut w = flate2::write::ZlibEncoder::new(&mut zl, flate2::Compression::best());
        w.write_all(data).map_err(|e| MpqError(format!("zlib encode: {e}")))?;
        w.finish().map_err(|e| MpqError(format!("zlib finish: {e}")))?;
    }
    let (mask, payload) = if bz.len() <= zl.len() { (MASK_BZIP2, bz) } else { (MASK_ZLIB, zl) };
    let mut packed = Vec::with_capacity(payload.len() + 1);
    packed.push(mask);
    packed.extend_from_slice(&payload);
    if packed.len() < data.len() {
        Ok((packed, true))
    } else {
        Ok((data.to_vec(), false))
    }
}
