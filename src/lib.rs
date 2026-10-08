//! SC2Diff -- StarCraft II document diff, version control and container rewriting.
//!
//! This is the Rust product.  The Python implementation under tools/ is the validation
//! harness that established the format facts this crate relies on; it stays in the tree
//! as the differential-testing oracle.

pub mod cli;
pub mod mpq;
pub mod repo;
pub mod semantic;
