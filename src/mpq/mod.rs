//! MPQ container layer for StarCraft II documents.

mod archive;
mod compress;
mod crypto;
mod tables;

pub use archive::{build_document, Archive, BuildInfo, BuildOptions, Document};
pub use compress::{best_compress, decompress_chunk};
pub use crypto::*;
pub use tables::{Attributes, BetTable, HetTable, BET_UNKNOWN08_I};

pub const MAGIC: &[u8; 4] = b"MPQ\x1a";
pub const HET_SIG: &[u8; 4] = b"HET\x1a";
pub const BET_SIG: &[u8; 4] = b"BET\x1a";
pub const HEADER_SIZE: usize = 208;

pub const FLAG_COMPRESS: u32 = 0x0000_0200;
pub const FLAG_ENCRYPTED: u32 = 0x0001_0000;
pub const FLAG_FIX_KEY: u32 = 0x0002_0000;
pub const FLAG_SINGLE_UNIT: u32 = 0x0100_0000;
pub const FLAG_EXISTS: u32 = 0x8000_0000;

pub const MASK_ZLIB: u8 = 0x02;
pub const MASK_BZIP2: u8 = 0x10;

pub const ATTR_CRC32: u32 = 0x01;
pub const ATTR_FILETIME: u32 = 0x02;
pub const ATTR_MD5: u32 = 0x04;

pub const HET_ENTRY_FREE: u8 = 0x00;
pub const HASH_ENTRY_FREE: u32 = 0xFFFF_FFFF;

pub const BOOKKEEPING: [&str; 4] = ["(listfile)", "(attributes)", "(signature)", "(user data)"];

pub fn is_bookkeeping(name: &str) -> bool {
    BOOKKEEPING.contains(&name)
}

#[derive(Debug)]
pub struct MpqError(pub String);

impl std::fmt::Display for MpqError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}
impl std::error::Error for MpqError {}

pub type Result<T> = std::result::Result<T, MpqError>;
