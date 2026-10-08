//! git-shaped command line front end.
//!
//! Command names, flags and output shapes deliberately mirror git, so an agent that knows
//! git does not have to learn a second dialect.

use std::path::PathBuf;

use clap::{Parser, Subcommand};

use crate::mpq;
use crate::repo::{Repo, RepoError, Result};
use crate::semantic;

#[derive(Parser)]
#[command(name = "sc2diff", version, about = "StarCraft II document version control (git-shaped)")]
pub struct Cli {
    /// run as if started in this directory
    #[arg(short = 'C', long = "dir", global = true)]
    pub dir: Option<PathBuf>,
    #[command(subcommand)]
    pub cmd: Cmd,
}

#[derive(Subcommand)]
pub enum Cmd {
    /// create a repository (optionally importing a packed document)
    Init { source: Option<PathBuf> },
    /// stage components
    Add {
        #[arg(short = 'A', long = "all")] all: bool,
        paths: Vec<String>,
    },
    /// remove components
    Rm {
        #[arg(long)] cached: bool,
        paths: Vec<String>,
    },
    /// show the working tree status
    Status { #[arg(short = 's', long)] short: bool },
    /// semantic diff
    Diff {
        #[arg(long = "cached", alias = "staged")] cached: bool,
        rev1: Option<String>,
        rev2: Option<String>,
    },
    /// record changes to the repository
    Commit {
        #[arg(short = 'm', long)] message: String,
        #[arg(short = 'a', long)] all: bool,
        #[arg(long)] allow_empty: bool,
    },
    /// show the commit log
    Log {
        #[arg(long)] oneline: bool,
        #[arg(short = 'n')] limit: Option<usize>,
        rev: Option<String>,
    },
    /// show one commit and its diff
    Show { rev: Option<String> },
    /// list, create, delete or rename branches
    Branch {
        name: Option<String>,
        start: Option<String>,
        #[arg(short = 'd', long)] delete: Option<String>,
        #[arg(short = 'D')] force_delete: Option<String>,
        #[arg(short = 'm', long, num_args = 2, value_names = ["OLD", "NEW"])] rename: Option<Vec<String>>,
    },
    /// switch branches or restore the working tree
    Checkout {
        target: String,
        #[arg(short = 'b')] create: bool,
        #[arg(short = 'f', long)] force: bool,
    },
    /// switch branches
    Switch {
        name: String,
        #[arg(short = 'c', long)] create: bool,
    },
    /// list, create or delete tags
    Tag {
        name: Option<String>,
        rev: Option<String>,
        #[arg(short = 'd', long)] delete: Option<String>,
    },
    /// unstage or discard changes
    Restore {
        #[arg(long)] staged: bool,
        paths: Vec<String>,
    },
    /// list the staged components
    LsFiles,
    /// export a packed document into a component working tree
    Unpack { doc: PathBuf, dest: Option<PathBuf> },
    /// repack the working tree into a document the editor accepts
    Pack { out: PathBuf },
    /// pack commits into a portable patch bundle
    Bundle {
        out: PathBuf,
        #[arg(long, num_args = 1..)] refs: Vec<String>,
        #[arg(long, num_args = 1..)] basis: Vec<String>,
    },
    /// apply a patch bundle
    Apply {
        file: PathBuf,
        #[arg(long)] no_checkout: bool,
    },
    /// check a document container (digests, HET/BET, the editor-acceptance field)
    Verify { doc: PathBuf },
}

fn repo_of(cli: &Cli) -> Result<Repo> {
    Repo::open(cli.dir.as_deref().unwrap_or(std::path::Path::new(".")))
}

fn print_diff(changes: &[(String, semantic::Change)]) {
    if changes.is_empty() {
        println!("  (no changes)");
        return;
    }
    for (name, c) in changes {
        println!("  {:<58} {}", name, c.summary(name));
    }
    println!();
    for (name, c) in changes.iter().take(8) {
        println!("{}", semantic::render(name, c));
    }
    if changes.len() > 8 {
        println!("... detail limited to 8 components");
    }
    println!("\n{} component(s) changed", changes.len());
}

pub fn run(cli: Cli) -> Result<i32> {
    match cli.cmd {
        Cmd::Init { ref source } => {
            let root = cli.dir.clone().unwrap_or_else(|| PathBuf::from("."));
            let repo = Repo::init(&root, source.as_deref())?;
            println!("Initialized empty sc2diff repository in {}", repo.meta.display());
            if let Some(s) = &repo.config.source {
                println!("  imported {s}");
                println!("  components: {} (staged with add -A)", repo.config.names.len());
                println!("  container : hash_entries={:?} block_shift={} unknown08={}",
                         repo.config.hash_count, repo.config.block_shift, repo.config.unknown08);
            }
            Ok(0)
        }
        Cmd::Add { all, ref paths } => {
            let mut repo = repo_of(&cli)?;
            let staged = if all || paths.is_empty() { repo.add_all()? } else { repo.add_paths(&paths)? };
            for n in &staged { println!("add      {n}"); }
            if staged.is_empty() { println!("nothing to add"); }
            Ok(0)
        }
        Cmd::Rm { cached, ref paths } => {
            let mut repo = repo_of(&cli)?;
            for raw in paths {
                let name = raw.replace('/', "\\");
                if !cached {
                    let p = repo.workpath(&name);
                    if p.exists() { let _ = std::fs::remove_file(p); }
                }
                repo.index.remove(&name);
                println!("rm       {name}");
            }
            repo.save_index()?;
            Ok(0)
        }
        Cmd::Status { short } => {
            let repo = repo_of(&cli)?;
            let (staged, unstaged, untracked) = repo.status()?;
            if short {
                for (n, k) in &staged {
                    println!("{}  {n}", match k.as_str() { "added" => "A", "deleted" => "D", _ => "M" });
                }
                for (n, k) in &unstaged {
                    println!(" {} {n}", if k == "deleted" { "D" } else { "M" });
                }
                for n in &untracked { println!("?? {n}"); }
                return Ok(0);
            }
            if repo.is_detached() {
                println!("HEAD detached at {}", repo.head_commit().unwrap_or_default());
            } else {
                println!("On branch {}", repo.current_branch());
            }
            if repo.head_commit().is_none() { println!("\nNo commits yet"); }
            if !staged.is_empty() {
                println!("\nChanges to be committed:");
                for (n, k) in &staged { println!("        {k}: {n}"); }
            }
            if !unstaged.is_empty() {
                println!("\nChanges not staged for commit:");
                for (n, k) in &unstaged { println!("        {k}: {n}"); }
            }
            if !untracked.is_empty() {
                println!("\nUntracked files:");
                for n in untracked.iter().take(40) { println!("        {n}"); }
                if untracked.len() > 40 { println!("        ... {} more", untracked.len() - 40); }
            }
            if staged.is_empty() && unstaged.is_empty() && untracked.is_empty() {
                println!("nothing to commit, working tree clean");
            }
            Ok(0)
        }
        Cmd::Diff { cached, ref rev1, ref rev2 } => {
            let repo = repo_of(&cli)?;
            if cached {
                let head = repo.commit_tree(&repo.head_commit());
                let idx = repo.index_tree();
                println!("diff index vs HEAD {}", repo.head_commit().unwrap_or_else(|| "(none)".into()));
                print_diff(&repo.diff_trees(&head, &idx)?);
            } else if let (Some(a), Some(b)) = (rev1, rev2) {
                let ta = repo.commit_tree(&Some(repo.resolve(a)?));
                let tb = repo.commit_tree(&Some(repo.resolve(b)?));
                println!("diff {a} -> {b}");
                print_diff(&repo.diff_trees(&ta, &tb)?);
            } else if let Some(a) = rev1 {
                let ta = repo.commit_tree(&Some(repo.resolve(a)?));
                println!("diff {a} -> working tree");
                print_diff(&repo.diff_trees(&ta, &repo.scan()?)?);
            } else {
                println!("diff working tree vs index");
                print_diff(&repo.diff_working(false)?);
            }
            Ok(0)
        }
        Cmd::Commit { ref message, all, allow_empty } => {
            let mut repo = repo_of(&cli)?;
            if all { repo.add_all()?; }
            match repo.commit(message, "sc2diff", allow_empty) {
                Ok(id) => {
                    let branch = if repo.current_branch().is_empty() { "(detached)".to_string() } else { repo.current_branch() };
                    println!("[{branch} {}] {message}", &id[..8]);
                    println!("  {} component(s) in tree", repo.index.len());
                    Ok(0)
                }
                Err(e) => { println!("{e}"); Ok(1) }
            }
        }
        Cmd::Log { oneline, limit, ref rev } => {
            let repo = repo_of(&cli)?;
            for c in repo.log(rev.as_deref(), limit)? {
                if oneline {
                    println!("{} {}", &c.id[..8], c.message.lines().next().unwrap_or(""));
                } else {
                    println!("commit {}", c.id);
                    println!("Author: {}", c.author);
                    println!("Date:   {}", c.time);
                    println!("\n    {}\n", c.message);
                }
            }
            Ok(0)
        }
        Cmd::Show { ref rev } => {
            let repo = repo_of(&cli)?;
            let sha = match rev { Some(r) => repo.resolve(r)?, None => repo.head_commit().ok_or_else(|| RepoError("no commits yet".into()))? };
            let c = repo.commit_data(&sha)?;
            println!("commit {}", c.id);
            println!("Author: {}", c.author);
            println!("Date:   {}", c.time);
            println!("\n    {}\n", c.message);
            let parent = c.parents.first().cloned();
            let ta = repo.commit_tree(&parent);
            println!("diff {} (vs {})", &sha[..8], parent.as_deref().map(|p| &p[..8]).unwrap_or("empty"));
            print_diff(&repo.diff_trees(&ta, &c.tree)?);
            Ok(0)
        }
        Cmd::Branch { ref name, ref start, ref delete, ref force_delete, ref rename } => {
            let repo = repo_of(&cli)?;
            if let Some(old_new) = rename {
                let (old, new) = (&old_new[0], &old_new[1]);
                let sha = repo.read_ref(&format!("refs/heads/{old}")).ok_or_else(|| RepoError(format!("branch {old} not found")))?;
                repo.write_ref(&format!("refs/heads/{new}"), &sha)?;
                repo.delete_ref(&format!("refs/heads/{old}"));
                if repo.current_branch() == *old {
                    std::fs::write(repo.meta.join("HEAD"), format!("ref: refs/heads/{new}\n")).map_err(|e| RepoError(e.to_string()))?;
                }
                println!("Renamed {old} -> {new}");
                return Ok(0);
            }
            if let Some(d) = delete.as_ref().or(force_delete.as_ref()) {
                repo.delete_branch(d, force_delete.is_some())?;
                println!("Deleted branch {d}");
                return Ok(0);
            }
            if let Some(n) = name {
                let sha = repo.create_branch(n, start.as_deref())?;
                println!("Created branch {n} at {sha}");
                return Ok(0);
            }
            let cur = repo.current_branch();
            for b in repo.branches() {
                println!("{} {b}", if b == cur { "*" } else { " " });
            }
            Ok(0)
        }
        Cmd::Checkout { ref target, create, force } => {
            let mut repo = repo_of(&cli)?;
            match repo.checkout(target, force, create) {
                Ok((sha, mode, w, r)) => {
                    if mode == "branch" { println!("Switched to branch '{target}'"); }
                    else { println!("HEAD is now at {} (detached)", &sha[..8.min(sha.len())]); }
                    println!("  {} written, {} removed", w.len(), r.len());
                    Ok(0)
                }
                Err(e) => { println!("fatal: {e}"); Ok(1) }
            }
        }
        Cmd::Switch { ref name, create } => {
            let mut repo = repo_of(&cli)?;
            match repo.checkout(name, false, create) {
                Ok((_, _, w, r)) => {
                    println!("Switched to branch '{name}'");
                    println!("  {} written, {} removed", w.len(), r.len());
                    Ok(0)
                }
                Err(e) => { println!("fatal: {e}"); Ok(1) }
            }
        }
        Cmd::Tag { ref name, ref rev, ref delete } => {
            let repo = repo_of(&cli)?;
            if let Some(d) = delete {
                repo.delete_tag(d)?;
                println!("Deleted tag {d}");
                return Ok(0);
            }
            match name {
                None => { for t in repo.tags() { println!("{t}"); } }
                Some(n) => { let sha = repo.create_tag(n, rev.as_deref(), false)?; println!("Tagged {n} at {sha}"); }
            }
            Ok(0)
        }
        Cmd::Restore { staged, ref paths } => {
            let mut repo = repo_of(&cli)?;
            if staged {
                repo.unstage(paths)?;
                for p in paths { println!("unstaged {p}"); }
            } else {
                let head = repo.commit_tree(&repo.head_commit());
                for raw in paths {
                    let name = raw.replace('/', "\\");
                    if let Some(sha) = head.get(&name) {
                        let data = repo.get(sha)?;
                        let p = repo.workpath(&name);
                        std::fs::write(&p, data).map_err(|e| RepoError(e.to_string()))?;
                        println!("restored {name}");
                    }
                }
            }
            Ok(0)
        }
        Cmd::LsFiles => {
            let repo = repo_of(&cli)?;
            for n in repo.index_tree().keys() { println!("{n}"); }
            Ok(0)
        }
        Cmd::Unpack { ref doc, ref dest } => {
            let dest = dest.clone().unwrap_or_else(|| PathBuf::from("."));
            let n = Repo::unpack(doc, &dest)?;
            println!("unpacked {n} components to {}", dest.display());
            Ok(0)
        }
        Cmd::Pack { ref out } => {
            let repo = repo_of(&cli)?;
            let info = repo.pack(out)?;
            println!("packed {}", out.display());
            println!("  {} bytes, {} blocks, {} components, rawChunkSize=0",
                     info.bytes, info.blocks, info.entries);
            let a = mpq::Archive::open(out)?;
            let (h, b, hs, het, bet) = a.verify_digests();
            println!("  digests: header={h} block={b} hash={hs} het={het} bet={bet}");
            Ok(0)
        }
        Cmd::Bundle { ref out, ref refs, ref basis } => {
            let repo = repo_of(&cli)?;
            let info = repo.bundle(out, refs, basis)?;
            let kb = info.get("bytes").and_then(|b| b.as_u64()).unwrap_or(0) as f64 / 1024.0;
            println!("bundle {}", out.display());
            println!("  {} commit(s), {} object(s), {:.1} KB",
                     info["commits"], info["objects"], kb);
            if let Some(p) = info.get("prerequisites").and_then(|p| p.as_array()) {
                if !p.is_empty() {
                    println!("  requires these commits on the other side:");
                    for x in p { println!("    {}", x.as_str().unwrap_or("")); }
                }
            }
            println!("  -> send this file by any means; no server needed");
            Ok(0)
        }
        Cmd::Apply { ref file, no_checkout } => {
            let mut repo = repo_of(&cli)?;
            match repo.apply_bundle(file, !no_checkout) {
                Ok(info) => {
                    println!("applied {}", file.display());
                    println!("  imported {} commit(s), {} object(s)", info["commits"], info["objects"]);
                    if let Some(actions) = info.get("actions").and_then(|a| a.as_array()) {
                        for a in actions {
                            println!("  {:<13} {} {}", a["action"].as_str().unwrap_or(""),
                                     a["ref"].as_str().unwrap_or(""),
                                     &a["sha"].as_str().unwrap_or("")[..8.min(a["sha"].as_str().unwrap_or("").len())]);
                            if a["action"].as_str() == Some("diverged") {
                                println!("      run: sc2diff merge {}", a["ref"].as_str().unwrap_or(""));
                            }
                        }
                    }
                    if !info["checked_out"].is_null() {
                        println!("  checked out {} ({} files)", info["checked_out"]["sha"], info["checked_out"]["written"]);
                    }
                    Ok(0)
                }
                Err(e) => { println!("fatal: {e}"); Ok(1) }
            }
        }
        Cmd::Verify { ref doc } => {
            let a = mpq::Archive::open(doc)?;
            let (h, b, hs, het, bet) = a.verify_digests();
            let raw_chunk = u32::from_le_bytes([a.raw[108], a.raw[109], a.raw[110], a.raw[111]]);
            println!("archive    {}", doc.display());
            println!("  blocks   {}  hash entries {}", a.block_count, a.hash_count);
            println!("  header digest {h}  block table {b}  hash table {hs}  HET {het}  BET {bet}");
            println!("  dwRawChunkSize {raw_chunk} (must be 0 for the editor to accept edits)");
            println!("  names    {}", a.names.len());
            let ok = h && b && hs && het && bet && raw_chunk == 0;
            println!("  RESULT   {}", if ok { "OK" } else { "FAILED" });
            Ok(if ok { 0 } else { 1 })
        }
    }
}
