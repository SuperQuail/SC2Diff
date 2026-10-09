//! Repository engine: working tree, object store, index, refs, bundles and packing.
//!
//! The working tree holds document components; pack/unpack bridge to the MPQ container.

use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::mpq::{self, is_bookkeeping, BuildOptions, Document};
use crate::semantic;

pub const META: &str = ".sc2diff";
pub const BUNDLE_MAGIC: &str = "SC2BUNDLE1";

#[derive(Debug)]
pub struct RepoError(pub String);

impl std::fmt::Display for RepoError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result { write!(f, "{}", self.0) }
}
impl std::error::Error for RepoError {}

impl From<mpq::MpqError> for RepoError {
    fn from(e: mpq::MpqError) -> Self { RepoError(e.0) }
}

pub type Result<T> = std::result::Result<T, RepoError>;

fn err<T>(m: impl Into<String>) -> Result<T> { Err(RepoError(m.into())) }

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct IndexEntry {
    pub sha: String,
    #[serde(default)]
    pub mtime_ns: u64,
    #[serde(default)]
    pub size: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Commit {
    pub id: String,
    #[serde(default)]
    pub parents: Vec<String>,
    pub message: String,
    #[serde(default = "default_author")]
    pub author: String,
    #[serde(default)]
    pub time: i64,
    pub tree: BTreeMap<String, String>,
}

fn default_author() -> String { "sc2diff".to_string() }

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Config {
    #[serde(default)]
    pub repo_version: u32,
    #[serde(default)]
    pub created: i64,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub hash_count: Option<u32>,
    #[serde(default = "default_shift")]
    pub block_shift: u16,
    #[serde(default = "default_unknown08")]
    pub unknown08: u32,
    #[serde(default = "default_attr_flags")]
    pub attr_flags: u32,
    #[serde(default)]
    pub names: Vec<String>,
    #[serde(default)]
    pub layout_hints: HashMap<String, u32>,
    #[serde(default)]
    pub listfile: Option<String>,
}

fn default_shift() -> u16 { 5 }
fn default_unknown08() -> u32 { 0x10 }
fn default_attr_flags() -> u32 { mpq::ATTR_CRC32 | mpq::ATTR_MD5 }

pub struct Repo {
    pub root: PathBuf,
    pub meta: PathBuf,
    pub config: Config,
    pub index: BTreeMap<String, IndexEntry>,
}

fn now() -> i64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

fn hex_of(data: &[u8]) -> String { semantic::hex(&semantic::sha1(data)) }

impl Repo {
    pub fn open(root: &Path) -> Result<Self> {
        let root = if root.as_os_str().is_empty() { PathBuf::from(".") } else { root.to_path_buf() };
        let meta = root.join(META);
        let cfg_path = meta.join("config.json");
        if !cfg_path.exists() {
            return err(format!("not a sc2diff repository (or any parent): {}", root.display()));
        }
        let config: Config = serde_json::from_str(&std::fs::read_to_string(&cfg_path).map_err(|e| RepoError(e.to_string()))?)
            .map_err(|e| RepoError(format!("config.json: {e}")))?;
        let mut repo = Repo { root, meta, config, index: BTreeMap::new() };
        let idx_path = repo.meta.join("index.json");
        if idx_path.exists() {
            let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&idx_path).unwrap_or_default())
                .unwrap_or(serde_json::Value::Null);
            if let Some(obj) = v.get("entries").and_then(|e| e.as_object()) {
                for (k, val) in obj {
                    if let Ok(e) = serde_json::from_value::<IndexEntry>(val.clone()) {
                        repo.index.insert(k.clone(), e);
                    }
                }
            }
        }
        Ok(repo)
    }

    pub fn init(root: &Path, source: Option<&Path>) -> Result<Self> {
        let root = if root.as_os_str().is_empty() { PathBuf::from(".") } else { root.to_path_buf() };
        let meta = root.join(META);
        for d in ["objects", "commits", "refs/heads", "refs/tags"] {
            std::fs::create_dir_all(meta.join(d)).map_err(|e| RepoError(e.to_string()))?;
        }
        let mut config = Config {
            repo_version: 1, created: now(), block_shift: default_shift(),
            unknown08: default_unknown08(), attr_flags: default_attr_flags(),
            ..Default::default()
        };
        if let Some(src) = source {
            let archive = mpq::Archive::open(src)?;
            config.source = Some(src.display().to_string());
            config.hash_count = Some(archive.hash_count);
            config.block_shift = archive.block_shift;
            config.unknown08 = archive.bet.as_ref().map(|b| b.get(crate::mpq::BET_UNKNOWN08_I)).unwrap_or(0x10);
            config.attr_flags = archive.attributes.as_ref().map(|a| a.flags).unwrap_or(default_attr_flags());
            config.listfile = archive.try_read("(listfile)").map(|l| String::from_utf8_lossy(&l).to_string());
            let doc = Document::open(src)?;
            let mut names = Vec::new();
            for (name, data) in &doc.entries {
                if is_bookkeeping(name) { continue; }
                let p = root.join(name.replace('\\', std::path::MAIN_SEPARATOR_STR));
                if let Some(parent) = p.parent() { std::fs::create_dir_all(parent).map_err(|e| RepoError(e.to_string()))?; }
                std::fs::write(&p, data).map_err(|e| RepoError(e.to_string()))?;
                names.push(name.clone());
                if let Some(i) = archive.find_block(name) {
                    config.layout_hints.insert(name.clone(), archive.block_table[i as usize].flags);
                }
            }
            config.names = names;
        }
        let repo = Repo { root, meta, config, index: BTreeMap::new() };
        repo.save_config()?;
        std::fs::write(repo.meta.join("HEAD"), "ref: refs/heads/main\n").map_err(|e| RepoError(e.to_string()))?;
        let mut repo = repo;
        if source.is_some() { repo.add_all()?; }
        repo.save_index()?;
        Ok(repo)
    }

    pub fn save_config(&self) -> Result<()> {
        let s = serde_json::to_string_pretty(&self.config).map_err(|e| RepoError(e.to_string()))?;
        std::fs::write(self.meta.join("config.json"), s).map_err(|e| RepoError(e.to_string()))
    }

    pub fn save_index(&self) -> Result<()> {
        let entries: BTreeMap<&String, &IndexEntry> = self.index.iter().collect();
        let v = serde_json::json!({ "version": 1, "entries": entries });
        std::fs::write(self.meta.join("index.json"), serde_json::to_string(&v).unwrap())
            .map_err(|e| RepoError(e.to_string()))
    }

    pub fn workpath(&self, name: &str) -> PathBuf {
        self.root.join(name.replace('\\', std::path::MAIN_SEPARATOR_STR))
    }

    fn walk(&self) -> Vec<(String, PathBuf)> {
        let known: BTreeSet<&String> = self.config.names.iter().collect();
        let lower: BTreeMap<String, String> = self.config.names.iter().map(|n| (n.to_lowercase(), n.clone())).collect();
        let mut out = Vec::new();
        let mut stack = vec![self.root.clone()];
        while let Some(dir) = stack.pop() {
            let rd = match std::fs::read_dir(&dir) { Ok(r) => r, Err(_) => continue };
            for entry in rd.flatten() {
                let p = entry.path();
                if p.is_dir() {
                    if p.file_name().map(|n| n == META).unwrap_or(false) { continue; }
                    stack.push(p);
                    continue;
                }
                let rel = p.strip_prefix(&self.root).unwrap_or(&p).to_string_lossy().replace('/', "\\");
                let name = if known.contains(&rel) { rel.clone() }
                           else { lower.get(&rel.to_lowercase()).cloned().unwrap_or(rel) };
                out.push((name, p));
            }
        }
        out.sort();
        out
    }

    // -- object store ---------------------------------------------------- //
    fn object_path(&self, sha: &str) -> PathBuf {
        self.meta.join("objects").join(&sha[..2]).join(&sha[2..])
    }

    pub fn has_object(&self, sha: &str) -> bool { self.object_path(sha).exists() }

    pub fn put(&self, data: &[u8]) -> Result<String> {
        let sha = hex_of(data);
        let p = self.object_path(&sha);
        if !p.exists() {
            if let Some(parent) = p.parent() { std::fs::create_dir_all(parent).map_err(|e| RepoError(e.to_string()))?; }
            let mut enc = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::default());
            enc.write_all(data).map_err(|e| RepoError(e.to_string()))?;
            let blob = enc.finish().map_err(|e| RepoError(e.to_string()))?;
            let tmp = p.with_extension(format!("tmp{}", std::process::id()));
            std::fs::write(&tmp, blob).map_err(|e| RepoError(e.to_string()))?;
            std::fs::rename(&tmp, &p).map_err(|e| RepoError(e.to_string()))?;
        }
        Ok(sha)
    }

    pub fn get(&self, sha: &str) -> Result<Vec<u8>> {
        let blob = std::fs::read(self.object_path(sha))
            .map_err(|_| RepoError(format!("missing object {sha}")))?;
        let mut out = Vec::new();
        flate2::read::ZlibDecoder::new(&blob[..]).read_to_end(&mut out).map_err(|e| RepoError(e.to_string()))?;
        Ok(out)
    }

    // -- refs ------------------------------------------------------------ //
    fn head_text(&self) -> String {
        std::fs::read_to_string(self.meta.join("HEAD")).unwrap_or_default().trim().to_string()
    }

    pub fn is_detached(&self) -> bool { !self.head_text().starts_with("ref:") }

    pub fn head_ref(&self) -> String {
        let t = self.head_text();
        if let Some(r) = t.strip_prefix("ref:") { r.trim().to_string() } else { String::new() }
    }

    pub fn current_branch(&self) -> String {
        self.head_ref().strip_prefix("refs/heads/").unwrap_or("").to_string()
    }

    pub fn read_ref(&self, r: &str) -> Option<String> {
        std::fs::read_to_string(self.meta.join(r.replace('/', std::path::MAIN_SEPARATOR_STR)))
            .ok().map(|s| s.trim().to_string()).filter(|s| !s.is_empty())
    }

    pub fn write_ref(&self, r: &str, sha: &str) -> Result<()> {
        let p = self.meta.join(r.replace('/', std::path::MAIN_SEPARATOR_STR));
        if let Some(parent) = p.parent() { std::fs::create_dir_all(parent).map_err(|e| RepoError(e.to_string()))?; }
        std::fs::write(p, format!("{sha}\n")).map_err(|e| RepoError(e.to_string()))
    }

    pub fn delete_ref(&self, r: &str) {
        let p = self.meta.join(r.replace('/', std::path::MAIN_SEPARATOR_STR));
        if p.exists() { let _ = std::fs::remove_file(p); }
    }

    pub fn head_commit(&self) -> Option<String> {
        if self.is_detached() { let t = self.head_text(); if t.is_empty() { None } else { Some(t) } }
        else { self.read_ref(&self.head_ref()) }
    }

    pub fn branches(&self) -> Vec<String> {
        let mut out = Vec::new();
        let dir = self.meta.join("refs").join("heads");
        let mut stack = vec![dir.clone()];
        while let Some(d) = stack.pop() {
            let rd = match std::fs::read_dir(&d) { Ok(r) => r, Err(_) => continue };
            for e in rd.flatten() {
                let p = e.path();
                if p.is_dir() { stack.push(p); }
                else if let Ok(rel) = p.strip_prefix(&dir) {
                    out.push(rel.to_string_lossy().replace('\\', "/"));
                }
            }
        }
        out.sort();
        out
    }

    pub fn tags(&self) -> Vec<String> {
        let mut out: Vec<String> = std::fs::read_dir(self.meta.join("refs").join("tags"))
            .map(|rd| rd.flatten().filter_map(|e| e.file_name().into_string().ok()).collect())
            .unwrap_or_default();
        out.sort();
        out
    }

    // -- commits --------------------------------------------------------- //
    pub fn commit_data(&self, sha: &str) -> Result<Commit> {
        let p = self.meta.join("commits").join(format!("{sha}.json"));
        let s = std::fs::read_to_string(&p).map_err(|_| RepoError(format!("unknown revision {sha}")))?;
        serde_json::from_str(&s).map_err(|e| RepoError(format!("{sha}: {e}")))
    }

    pub fn parents(&self, sha: &str) -> Vec<String> {
        self.commit_data(sha).map(|c| c.parents).unwrap_or_default()
    }

    pub fn commit_tree(&self, sha: &Option<String>) -> BTreeMap<String, String> {
        match sha { Some(s) => self.commit_data(s).map(|c| c.tree).unwrap_or_default(), None => BTreeMap::new() }
    }

    pub fn resolve(&self, rev: &str) -> Result<String> {
        let (base, suffix) = match rev.split_once('~') {
            Some((b, s)) => (b, s.parse::<usize>().unwrap_or(1)),
            None => (rev, 0),
        };
        let mut sha = self.resolve_base(base)?;
        for _ in 0..suffix {
            let ps = self.parents(&sha);
            if ps.is_empty() { return err(format!("revision {rev} has no parent")); }
            sha = ps[0].clone();
        }
        Ok(sha)
    }

    fn resolve_base(&self, rev: &str) -> Result<String> {
        if rev == "HEAD" || rev == "@" {
            return self.head_commit().ok_or_else(|| RepoError("HEAD does not point at any commit yet".into()));
        }
        for r in [format!("refs/heads/{rev}"), format!("refs/tags/{rev}"), rev.to_string()] {
            if let Some(s) = self.read_ref(&r) { return Ok(s); }
        }
        if rev.len() >= 4 {
            let mut hits: Vec<String> = std::fs::read_dir(self.meta.join("commits"))
                .map(|rd| rd.flatten()
                    .filter_map(|e| e.file_name().into_string().ok())
                    .filter(|n| n.starts_with(rev) && n.ends_with(".json"))
                    .map(|n| n.trim_end_matches(".json").to_string())
                    .collect())
                .unwrap_or_default();
            hits.sort();
            if hits.len() == 1 { return Ok(hits.remove(0)); }
            if hits.len() > 1 { return err(format!("ambiguous revision {rev}")); }
        }
        err(format!("unknown revision {rev}"))
    }

    pub fn commit(&mut self, message: &str, author: &str, allow_empty: bool) -> Result<String> {
        let tree: BTreeMap<String, String> = self.index.iter().map(|(k, v)| (k.clone(), v.sha.clone())).collect();
        let missing: Vec<&String> = tree.values().filter(|s| !self.has_object(s)).collect();
        if !missing.is_empty() {
            return err(format!("object store is missing {} staged component(s); run 'sc2diff add -A'", missing.len()));
        }
        let parent = self.head_commit();
        if !allow_empty && parent.is_some() && self.commit_tree(&parent) == tree {
            return err("nothing to commit (index matches HEAD)");
        }
        let mut c = Commit {
            id: String::new(),
            parents: parent.clone().into_iter().collect(),
            message: message.to_string(),
            author: author.to_string(),
            time: now(),
            tree,
        };
        let payload = serde_json::json!({
            "parents": c.parents, "message": c.message, "author": c.author,
            "time": c.time, "tree": c.tree
        });
        let cid = &hex_of(serde_json::to_string(&payload).unwrap().as_bytes())[..16];
        c.id = cid.to_string();
        std::fs::write(self.meta.join("commits").join(format!("{cid}.json")),
                       serde_json::to_string_pretty(&c).unwrap()).map_err(|e| RepoError(e.to_string()))?;
        if self.is_detached() {
            std::fs::write(self.meta.join("HEAD"), format!("{cid}\n")).map_err(|e| RepoError(e.to_string()))?;
        } else {
            self.write_ref(&self.head_ref(), cid)?;
        }
        Ok(c.id)
    }

    pub fn log(&self, rev: Option<&str>, limit: Option<usize>) -> Result<Vec<Commit>> {
        let tip = match rev { Some(r) => self.resolve(r)?, None => match self.head_commit() { Some(s) => s, None => return Ok(vec![]) } };
        let mut gen: HashMap<String, usize> = HashMap::new();
        let mut order = Vec::new();
        let mut queue = std::collections::VecDeque::new();
        queue.push_back(tip.clone());
        gen.insert(tip, 0);
        while let Some(sha) = queue.pop_front() {
            order.push(sha.clone());
            let g = gen[&sha];
            for p in self.parents(&sha) {
                if gen.get(&p).map(|x| *x < g + 1).unwrap_or(true) {
                    gen.insert(p.clone(), g + 1);
                    queue.push_back(p);
                }
            }
        }
        order.sort_by_key(|s| (gen.get(s).copied().unwrap_or(0), -(self.commit_data(s).map(|c| c.time).unwrap_or(0))));
        let mut out: Vec<Commit> = order.iter().filter_map(|s| self.commit_data(s).ok()).collect();
        if let Some(n) = limit { out.truncate(n); }
        Ok(out)
    }

    fn ancestors(&self, start: &str) -> BTreeSet<String> {
        let mut seen = BTreeSet::new();
        let mut stack = vec![start.to_string()];
        while let Some(s) = stack.pop() {
            if !seen.insert(s.clone()) { continue; }
            stack.extend(self.parents(&s));
        }
        seen
    }

    /// Best common ancestor, ordered by graph position rather than commit time.
    pub fn merge_base(&self, a: &str, b: &str) -> Option<String> {
        let aa = self.ancestors(a);
        let ab = self.ancestors(b);
        let common: Vec<String> = aa.intersection(&ab).cloned().collect();
        if common.is_empty() { return None; }
        let mut best: Option<String> = None;
        let mut best_depth = 0usize;
        for cand in &common {
            let is_ancestor_of_other = common.iter().any(|o| o != cand && self.ancestors(o).contains(cand));
            if is_ancestor_of_other { continue; }
            let depth = self.ancestors(cand).len();
            if depth > best_depth { best = Some(cand.clone()); best_depth = depth; }
        }
        best
    }

    // -- working tree / index -------------------------------------------- //
    fn stat_of(p: &Path) -> (u64, u64) {
        match std::fs::metadata(p) {
            Ok(m) => {
                let mtime = m.modified().ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_nanos() as u64).unwrap_or(0);
                (mtime, m.len())
            }
            Err(_) => (0, 0),
        }
    }

    /// Working tree as {name: sha}, reusing the cached digest when the stat is unchanged.
    pub fn scan(&self) -> Result<BTreeMap<String, String>> {
        let mut tree = BTreeMap::new();
        for (name, path) in self.walk() {
            let (mtime, size) = Self::stat_of(&path);
            if let Some(e) = self.index.get(&name) {
                if e.mtime_ns == mtime && e.size == size {
                    tree.insert(name, e.sha.clone());
                    continue;
                }
            }
            let data = std::fs::read(&path).map_err(|e| RepoError(e.to_string()))?;
            tree.insert(name, hex_of(&data));
        }
        Ok(tree)
    }

    pub fn add_all(&mut self) -> Result<Vec<String>> {
        let mut staged = Vec::new();
        let present: BTreeMap<String, PathBuf> = self.walk().into_iter().collect();
        let tree = self.scan()?;
        for (name, sha) in tree {
            let path = &present[&name];
            if !self.has_object(&sha) {
                let data = std::fs::read(path).map_err(|e| RepoError(e.to_string()))?;
                self.put(&data)?;
            }
            let (mtime_ns, size) = Self::stat_of(path);
            self.index.insert(name.clone(), IndexEntry { sha, mtime_ns, size });
            staged.push(name);
        }
        for gone in self.index.keys().filter(|k| !present.contains_key(*k)).cloned().collect::<Vec<_>>() {
            self.index.remove(&gone);
        }
        self.save_index()?;
        Ok(staged)
    }

    pub fn add_paths(&mut self, paths: &[String]) -> Result<Vec<String>> {
        let mut staged = Vec::new();
        for raw in paths {
            let name = raw.replace('/', "\\");
            let p = self.workpath(&name);
            if p.exists() {
                let data = std::fs::read(&p).map_err(|e| RepoError(e.to_string()))?;
                let sha = self.put(&data)?;
                let (mtime_ns, size) = Self::stat_of(&p);
                self.index.insert(name.clone(), IndexEntry { sha, mtime_ns, size });
                staged.push(name);
            } else {
                self.index.remove(&name);
            }
        }
        self.save_index()?;
        Ok(staged)
    }

    pub fn unstage(&mut self, paths: &[String]) -> Result<()> {
        let head = self.commit_tree(&self.head_commit());
        for raw in paths {
            let name = raw.replace('/', "\\");
            if let Some(sha) = head.get(&name) {
                self.index.insert(name, IndexEntry { sha: sha.clone(), mtime_ns: 0, size: 0 });
            } else {
                self.index.remove(&name);
            }
        }
        self.save_index()
    }

    pub fn index_tree(&self) -> BTreeMap<String, String> {
        self.index.iter().map(|(k, v)| (k.clone(), v.sha.clone())).collect()
    }

    pub fn status(&self) -> Result<(BTreeMap<String, String>, BTreeMap<String, String>, Vec<String>)> {
        let head = self.commit_tree(&self.head_commit());
        let idx = self.index_tree();
        let work = self.scan()?;
        let mut staged = BTreeMap::new();
        for name in head.keys().chain(idx.keys()).collect::<BTreeSet<_>>() {
            let a = head.get(name); let b = idx.get(name);
            if a != b {
                staged.insert(name.clone(),
                    if a.is_none() { "added" } else if b.is_none() { "deleted" } else { "modified" }.to_string());
            }
        }
        let mut unstaged = BTreeMap::new();
        let mut untracked = Vec::new();
        for name in idx.keys().chain(work.keys()).collect::<BTreeSet<_>>() {
            match (idx.get(name), work.get(name)) {
                (None, Some(_)) => untracked.push(name.clone()),
                (Some(_), None) => { unstaged.insert(name.clone(), "deleted".to_string()); }
                (Some(a), Some(b)) if a != b => { unstaged.insert(name.clone(), "modified".to_string()); }
                _ => {}
            }
        }
        Ok((staged, unstaged, untracked))
    }

    pub fn dirty(&self) -> bool {
        self.status().map(|(a, b, _)| !a.is_empty() || !b.is_empty()).unwrap_or(false)
    }

    // -- diff ------------------------------------------------------------- //
    pub fn diff_trees(&self, old: &BTreeMap<String, String>, new: &BTreeMap<String, String>) -> Result<Vec<(String, semantic::Change)>> {
        let mut out = Vec::new();
        for name in old.keys().chain(new.keys()).collect::<BTreeSet<_>>() {
            if is_bookkeeping(name) { continue; }
            if old.get(name) == new.get(name) { continue; }
            let (a, b) = (old.get(name), new.get(name));
            if a.is_none() { out.push((name.clone(), semantic::Change { added: vec![name.clone()], ..Default::default() })); continue; }
            if b.is_none() { out.push((name.clone(), semantic::Change { removed: vec![name.clone()], ..Default::default() })); continue; }
            let da = self.get(a.unwrap())?;
            let db = self.get(b.unwrap())?;
            let ch = semantic::diff(&semantic::canonical(name, &da), &semantic::canonical(name, &db));
            if !ch.is_empty() { out.push((name.clone(), ch)); }
        }
        Ok(out)
    }

    /// Working tree vs index (or vs HEAD).  The working side is read from disk: a modified
    /// file is not a blob until it is staged.
    pub fn diff_working(&self, vs_head: bool) -> Result<Vec<(String, semantic::Change)>> {
        let base = if vs_head { self.commit_tree(&self.head_commit()) } else { self.index_tree() };
        let work = self.scan()?;
        let mut out = Vec::new();
        for name in base.keys().chain(work.keys()).collect::<BTreeSet<_>>() {
            if is_bookkeeping(name) { continue; }
            if base.get(name) == work.get(name) { continue; }
            let b = work.get(name);
            let a = base.get(name);
            if a.is_none() { out.push((name.clone(), semantic::Change { added: vec![name.clone()], ..Default::default() })); continue; }
            if b.is_none() { out.push((name.clone(), semantic::Change { removed: vec![name.clone()], ..Default::default() })); continue; }
            let da = self.get(a.unwrap())?;
            let db = std::fs::read(self.workpath(name)).map_err(|e| RepoError(e.to_string()))?;
            let ch = semantic::diff(&semantic::canonical(name, &da), &semantic::canonical(name, &db));
            if !ch.is_empty() { out.push((name.clone(), ch)); }
        }
        Ok(out)
    }

    // -- checkout / branch / tag ------------------------------------------ //
    fn apply_tree(&mut self, tree: &BTreeMap<String, String>) -> Result<(Vec<String>, Vec<String>)> {
        let mut written = Vec::new();
        let mut removed = Vec::new();
        let current = self.scan()?;
        for (name, sha) in tree {
            if current.get(name) != Some(sha) {
                let data = self.get(sha)?;
                let p = self.workpath(name);
                if let Some(parent) = p.parent() { std::fs::create_dir_all(parent).map_err(|e| RepoError(e.to_string()))?; }
                std::fs::write(&p, &data).map_err(|e| RepoError(e.to_string()))?;
                written.push(name.clone());
            }
        }
        for name in current.keys() {
            if !tree.contains_key(name) {
                let p = self.workpath(name);
                if p.exists() { let _ = std::fs::remove_file(&p); removed.push(name.clone()); }
            }
        }
        self.index.clear();
        for (name, sha) in tree {
            let p = self.workpath(name);
            let (mtime_ns, size) = Self::stat_of(&p);
            self.index.insert(name.clone(), IndexEntry { sha: sha.clone(), mtime_ns, size });
        }
        self.save_index()?;
        Ok((written, removed))
    }

    pub fn checkout(&mut self, target: &str, force: bool, create: bool) -> Result<(String, String, Vec<String>, Vec<String>)> {
        if self.dirty() && !force {
            return err("you have local changes; commit them or use -f");
        }
        if create { self.create_branch(target, None)?; }
        let branch_ref = format!("refs/heads/{target}");
        let tag_ref = format!("refs/tags/{target}");
        let (sha, mode) = if let Some(s) = self.read_ref(&branch_ref) {
            std::fs::write(self.meta.join("HEAD"), format!("ref: {branch_ref}\n")).map_err(|e| RepoError(e.to_string()))?;
            (s, "branch")
        } else if let Some(s) = self.read_ref(&tag_ref) {
            std::fs::write(self.meta.join("HEAD"), format!("{s}\n")).map_err(|e| RepoError(e.to_string()))?;
            (s, "detached")
        } else {
            let s = self.resolve(target)?;
            std::fs::write(self.meta.join("HEAD"), format!("{s}\n")).map_err(|e| RepoError(e.to_string()))?;
            (s, "detached")
        };
        let tree = self.commit_tree(&Some(sha.clone()));
        let (written, removed) = self.apply_tree(&tree)?;
        Ok((sha, mode.to_string(), written, removed))
    }

    pub fn create_branch(&self, name: &str, rev: Option<&str>) -> Result<String> {
        let refname = format!("refs/heads/{name}");
        if self.read_ref(&refname).is_some() { return err(format!("a branch named {name} already exists")); }
        let sha = match rev { Some(r) => self.resolve(r)?, None => self.head_commit().ok_or_else(|| RepoError("cannot create a branch before the first commit".into()))? };
        self.write_ref(&refname, &sha)?;
        Ok(sha)
    }

    pub fn delete_branch(&self, name: &str, force: bool) -> Result<()> {
        let refname = format!("refs/heads/{name}");
        let sha = self.read_ref(&refname).ok_or_else(|| RepoError(format!("branch {name} not found")))?;
        if name == self.current_branch() { return err("cannot delete the branch you are on"); }
        if !force {
            if let Some(head) = self.head_commit() {
                if self.merge_base(&head, &sha) != Some(sha.clone()) {
                    return err(format!("branch {name} is not fully merged (use -D to force)"));
                }
            }
        }
        self.delete_ref(&refname);
        Ok(())
    }

    pub fn create_tag(&self, name: &str, rev: Option<&str>, force: bool) -> Result<String> {
        let refname = format!("refs/tags/{name}");
        if self.read_ref(&refname).is_some() && !force { return err(format!("tag {name} already exists")); }
        let sha = match rev { Some(r) => self.resolve(r)?, None => self.head_commit().ok_or_else(|| RepoError("cannot tag before the first commit".into()))? };
        self.write_ref(&refname, &sha)?;
        Ok(sha)
    }

    pub fn delete_tag(&self, name: &str) -> Result<()> {
        if self.read_ref(&format!("refs/tags/{name}")).is_none() { return err(format!("tag {name} not found")); }
        self.delete_ref(&format!("refs/tags/{name}"));
        Ok(())
    }

    // -- container ------------------------------------------------------- //
    pub fn pack(&self, dest: &Path) -> Result<mpq::BuildInfo> {
        let tree = self.scan()?;
        let mut ordered: Vec<String> = self.config.names.iter().filter(|n| tree.contains_key(*n)).cloned().collect();
        for n in tree.keys() { if !ordered.contains(n) { ordered.push(n.clone()); } }
        let entries: Vec<(String, Vec<u8>)> = ordered.iter()
            .map(|n| Ok((n.clone(), self.get(&tree[n])?)))
            .collect::<Result<Vec<_>>>()?;
        let listfile = if ordered == self.config.names {
            self.config.listfile.as_ref().map(|s| s.clone().into_bytes())
        } else { None };
        let opts = BuildOptions {
            hash_count: self.config.hash_count,
            block_shift: self.config.block_shift,
            unknown08: self.config.unknown08,
            attr_flags: self.config.attr_flags,
            listfile,
            hints: self.config.layout_hints.clone(),
            raw_chunk_size: 0,
        };
        Ok(mpq::build_document(&entries, dest, &opts)?)
    }

    pub fn unpack(doc: &Path, dest: &Path) -> Result<usize> {
        let d = Document::open(doc)?;
        let mut n = 0;
        for (name, data) in &d.entries {
            if is_bookkeeping(name) { continue; }
            let p = dest.join(name.replace('\\', std::path::MAIN_SEPARATOR_STR));
            if let Some(parent) = p.parent() { std::fs::create_dir_all(parent).map_err(|e| RepoError(e.to_string()))?; }
            std::fs::write(&p, data).map_err(|e| RepoError(e.to_string()))?;
            n += 1;
        }
        Ok(n)
    }

    // -- bundles ---------------------------------------------------------- //
    pub fn bundle(&self, dest: &Path, refs: &[String], basis: &[String]) -> Result<serde_json::Value> {
        let refs = if refs.is_empty() {
            vec![self.current_branch().is_empty().then(|| "HEAD".to_string()).unwrap_or(self.current_branch())]
        } else { refs.to_vec() };
        let mut ref_shas: BTreeMap<String, String> = BTreeMap::new();
        for r in &refs { ref_shas.insert(r.clone(), self.resolve(r)?); }
        let mut prereq: BTreeSet<String> = BTreeSet::new();
        for b in basis { prereq.insert(self.resolve(b)?); }
        let mut commits: Vec<Commit> = Vec::new();
        let mut seen: BTreeSet<String> = BTreeSet::new();
        let mut stack: Vec<String> = ref_shas.values().cloned().collect();
        while let Some(sha) = stack.pop() {
            if seen.contains(&sha) || prereq.contains(&sha) { continue; }
            seen.insert(sha.clone());
            let c = self.commit_data(&sha)?;
            stack.extend(c.parents.clone());
            commits.push(c);
        }
        commits.sort_by_key(|c| c.time);
        let mut basis_blobs: BTreeSet<String> = BTreeSet::new();
        for p in &prereq { basis_blobs.extend(self.commit_tree(&Some(p.clone())).values().cloned()); }
        let mut objects: BTreeSet<String> = BTreeSet::new();
        for c in &commits { for sha in c.tree.values() { if !basis_blobs.contains(sha) { objects.insert(sha.clone()); } } }
        let file = std::fs::File::create(dest).map_err(|e| RepoError(e.to_string()))?;
        let mut z = zip::ZipWriter::new(file);
        let zopts: zip::write::FileOptions<()> = zip::write::FileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);
        let manifest = serde_json::json!({
            "magic": BUNDLE_MAGIC, "version": 1, "created": now(),
            "refs": ref_shas, "prerequisites": prereq.iter().cloned().collect::<Vec<_>>(),
            "commits": commits.iter().map(|c| c.id.clone()).collect::<Vec<_>>(),
            "objects": objects.iter().cloned().collect::<Vec<_>>()
        });
        z.start_file("manifest.json", zopts).map_err(|e| RepoError(e.to_string()))?;
        z.write_all(serde_json::to_string_pretty(&manifest).unwrap().as_bytes()).map_err(|e| RepoError(e.to_string()))?;
        for c in &commits {
            z.start_file(format!("commits/{}.json", c.id), zopts).map_err(|e| RepoError(e.to_string()))?;
            z.write_all(serde_json::to_string(c).unwrap().as_bytes()).map_err(|e| RepoError(e.to_string()))?;
        }
        for sha in &objects {
            z.start_file(format!("objects/{sha}"), zopts).map_err(|e| RepoError(e.to_string()))?;
            let data = self.get(sha).map_err(|e| RepoError(e.to_string()))?;
            z.write_all(&data).map_err(|e| RepoError(e.to_string()))?;
        }
        z.finish().map_err(|e| RepoError(e.to_string()))?;
        Ok(serde_json::json!({
            "bytes": std::fs::metadata(dest).map(|m| m.len()).unwrap_or(0),
            "commits": commits.len(), "objects": objects.len(),
            "refs": ref_shas, "prerequisites": prereq.iter().cloned().collect::<Vec<_>>()
        }))
    }

    pub fn apply_bundle(&mut self, path: &Path, checkout: bool) -> Result<serde_json::Value> {
        let was_dirty = self.dirty();
        let file = std::fs::File::open(path).map_err(|e| RepoError(e.to_string()))?;
        let mut z = zip::ZipArchive::new(file).map_err(|e| RepoError(format!("{}: {e}", path.display())))?;
        let manifest: serde_json::Value = {
            let mut mf = z.by_name("manifest.json").map_err(|_| RepoError(format!("{} is not a sc2diff bundle", path.display())))?;
            let mut s = String::new();
            mf.read_to_string(&mut s).map_err(|e| RepoError(e.to_string()))?;
            serde_json::from_str(&s).map_err(|e| RepoError(e.to_string()))?
        };
        if manifest.get("magic").and_then(|m| m.as_str()) != Some(BUNDLE_MAGIC) {
            return err(format!("{} has an unrecognised bundle header", path.display()));
        }
        let missing: Vec<String> = manifest.get("prerequisites").and_then(|p| p.as_array()).map(|a| {
            a.iter().filter_map(|v| v.as_str()).filter(|s| !self.meta.join("commits").join(format!("{s}.json")).exists())
                .map(|s| s.to_string()).collect()
        }).unwrap_or_default();
        if !missing.is_empty() {
            return err(format!("bundle needs {} commit(s) this repository does not have, e.g. {}", missing.len(), missing[0]));
        }
        let object_names: Vec<String> = manifest.get("objects").and_then(|o| o.as_array())
            .map(|a| a.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
        let mut imported_objects = 0;
        for name in &object_names {
            let mut data = Vec::new();
            if let Ok(mut f) = z.by_name(&format!("objects/{name}")) {
                f.read_to_end(&mut data).map_err(|e| RepoError(e.to_string()))?;
                if !self.has_object(name) { self.put(&data)?; imported_objects += 1; }
            }
        }
        let commit_names: Vec<String> = manifest.get("commits").and_then(|o| o.as_array())
            .map(|a| a.iter().filter_map(|v| v.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
        let mut imported_commits = 0;
        for cid in &commit_names {
            let dst = self.meta.join("commits").join(format!("{cid}.json"));
            if dst.exists() { continue; }
            let mut s = String::new();
            z.by_name(&format!("commits/{cid}.json")).map_err(|e| RepoError(e.to_string()))?
                .read_to_string(&mut s).map_err(|e| RepoError(e.to_string()))?;
            std::fs::write(&dst, s).map_err(|e| RepoError(e.to_string()))?;
            imported_commits += 1;
        }
        let mut actions: Vec<serde_json::Value> = Vec::new();
        let mut refs_changed = false;
        if let Some(refs) = manifest.get("refs").and_then(|r| r.as_object()) {
            for (name, v) in refs {
                let sha = v.as_str().unwrap_or("").to_string();
                let full = if name.starts_with("refs/") { name.clone() } else { format!("refs/heads/{name}") };
                match self.read_ref(&full) {
                    None => { self.write_ref(&full, &sha)?; actions.push(serde_json::json!({"action":"created","ref":name,"sha":sha})); refs_changed = true; }
                    Some(local) if local == sha => { actions.push(serde_json::json!({"action":"up-to-date","ref":name,"sha":sha})); }
                    Some(local) if self.merge_base(&local, &sha).as_deref() == Some(&local) => {
                        self.write_ref(&full, &sha)?;
                        actions.push(serde_json::json!({"action":"fast-forward","ref":name,"sha":sha}));
                        refs_changed = true;
                    }
                    Some(_) => { actions.push(serde_json::json!({"action":"diverged","ref":name,"sha":sha})); }
                }
            }
        }
        let mut checked_out = serde_json::Value::Null;
        if checkout && refs_changed && !was_dirty {
            if let Some(head) = self.head_commit() {
                let tree = self.commit_tree(&Some(head.clone()));
                let (w, r) = self.apply_tree(&tree)?;
                checked_out = serde_json::json!({"sha": head, "written": w.len(), "removed": r.len()});
            }
        }
        Ok(serde_json::json!({
            "commits": imported_commits, "objects": imported_objects,
            "actions": actions, "checked_out": checked_out
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture_doc_path() -> PathBuf {
        let dir = std::env::temp_dir().join("sc2diff-repo-test");
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("src.SC2Map");
        let entries = vec![
            ("Objects".to_string(), b"<PlacedObjects Version=\"27\"><ObjectUnit Id=\"1\" UnitType=\"Marine\"/></PlacedObjects>".to_vec()),
            ("Base.SC2Data\\GameData\\UnitData.xml".to_string(), b"<Catalog><CUnit id=\"Marine\"><LifeMax value=\"45\"/></CUnit></Catalog>".to_vec()),
            ("MapScript.galaxy".to_string(), b"// s\n".to_vec()),
        ];
        mpq::build_document(&entries, &p, &BuildOptions::default()).unwrap();
        p
    }

    #[test]
    fn repository_round_trip_and_pack() {
        let dir = std::env::temp_dir().join("sc2diff-repo-test2");
        let _ = std::fs::remove_dir_all(&dir);
        let src = fixture_doc_path();
        let mut r = Repo::init(&dir, Some(&src)).unwrap();
        r.commit("import", "test", false).unwrap();
        assert!(r.status().unwrap().1.is_empty(), "clean after commit");
        r.checkout("main", true, false).unwrap();
        let out = dir.join("out.SC2Map");
        r.pack(&out).unwrap();
        let a = mpq::Archive::open(&out).unwrap();
        assert_eq!(u32::from_le_bytes([a.raw[108], a.raw[109], a.raw[110], a.raw[111]]), 0);
        assert!(a.verify_digests().0);
        let back = Document::open(&out).unwrap();
        let orig = Document::open(&src).unwrap();
        for n in orig.component_names() {
            assert_eq!(back.get(&n).unwrap(), orig.get(&n).unwrap(), "{n} differs after pack");
        }
    }
}
