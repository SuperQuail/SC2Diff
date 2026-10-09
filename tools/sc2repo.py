# -*- coding: utf-8 -*-
"""sc2repo -- a git-shaped version control engine for StarCraft II documents.

Repository layout (git-like on purpose, so that agent habits transfer):

    .sc2diff/
      config.json          SC2 container facts (hash count, block shift, unknown08, hints)
      objects/<aa>/<sha1>  content-addressed component blobs (zlib)
      refs/heads/<branch>  branch tips
      refs/tags/<tag>      tags
      HEAD                 "ref: refs/heads/main" or a detached commit id
      commits/<id>.json    {id, parents[], message, author, time, tree{name: sha}}
      index.json           the staging area (and the stat cache that makes status fast)

The index doubles as a stat cache: every entry records (sha, mtime_ns, size), so an
unchanged component is never re-read.

Diffs are semantic (sc2semantic).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor

sys_path = os.path.dirname(os.path.abspath(__file__))
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

import sc2mpq
import sc2semantic

BOOKKEEPING = set(sc2mpq.BOOKKEEPING)
META = ".sc2diff"
BUNDLE_MAGIC = "SC2BUNDLE1"
INDEX_VERSION = 1
HASH_WORKERS = min(8, (os.cpu_count() or 4))


class RepoError(Exception):
    """Raised for repository-level problems that the CLI reports to the user."""


def _sha(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _sha_file(path: str) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Repo:
    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.meta = os.path.join(self.root, META)
        self._config = None
        self._index = None

    @classmethod
    def init(cls, root: str, source: str = None, verbose: bool = True) -> "Repo":
        repo = cls(root)
        for sub in ("objects", "commits", "refs/heads", "refs/tags"):
            os.makedirs(os.path.join(repo.meta, sub.replace("/", os.sep)), exist_ok=True)
        config = {"repo_version": 1, "created": time.time()}
        if source:
            archive = sc2mpq.MPQArchive(source)
            config.update({
                "source": os.path.abspath(source),
                "source_sha1": _sha_file(source),
                "hash_count": archive.hash_count,
                "block_shift": archive.block_shift,
                "unknown08": archive.bet.unknown08 if archive.bet else 0x10,
                "attr_flags": archive.attributes.flags if archive.attributes else 0x05,
                "names": [],
                "layout_hints": {},
                "listfile": archive.try_read("(listfile)").decode("utf-8", "replace")
                             if archive.try_read("(listfile)") else None,
            })
            repo._write_config(config)
            names = repo._import_archive(archive, parallel=True)
            config["names"] = names
            config["layout_hints"] = {n: archive.block_table[archive.find_block(n)].flags
                                      for n in names if archive.find_block(n) >= 0}
        repo._write_config(config)
        repo._write_head("ref: refs/heads/main")
        repo._index = {}
        repo.index_save()
        if source:
            repo.add(all=True)          # stage the imported document (git: add -A)
        return repo

    def _write_config(self, config):
        with open(os.path.join(self.meta, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(config, fh, ensure_ascii=False, indent=1)

    @property
    def config(self) -> dict:
        if self._config is None:
            with open(os.path.join(self.meta, "config.json"), encoding="utf-8") as fh:
                self._config = json.load(fh)
        return self._config

    def exists(self) -> bool:
        return os.path.isdir(self.meta)

    # ------------------------------------------------------------------ #
    # worktree <-> component names
    # ------------------------------------------------------------------ #
    def workpath(self, name: str) -> str:
        return os.path.join(self.root, name.replace("\\", os.sep))

    def _known_names(self) -> set:
        return set(self.config.get("names") or [])

    def _canonical_name(self, rel: str, known: set) -> str:
        cand = rel.replace("/", "\\")
        if cand in known:
            return cand
        lowered = {n.lower(): n for n in known}
        return lowered.get(cand.lower(), cand)

    def walk_worktree(self) -> list:
        """Every file in the working tree (component name relative), excluding .sc2diff."""
        known = self._known_names()
        found = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            if META in dirnames:
                dirnames.remove(META)
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, self.root).replace("\\", "/")
                found.append((self._canonical_name(rel, known), full))
        found.sort()
        return found

    # ------------------------------------------------------------------ #
    # object store
    # ------------------------------------------------------------------ #
    def _object_path(self, sha: str) -> str:
        return os.path.join(self.meta, "objects", sha[:2], sha[2:])

    def put(self, data: bytes) -> str:
        sha = _sha(data)
        path = self._object_path(sha)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp%d" % os.getpid()
            with open(tmp, "wb") as fh:
                fh.write(zlib.compress(data, 6))
            os.replace(tmp, path)          # atomic: a crash never leaves a partial blob
        return sha

    def get(self, sha: str) -> bytes:
        with open(self._object_path(sha), "rb") as fh:
            return zlib.decompress(fh.read())

    def has_object(self, sha: str) -> bool:
        return os.path.exists(self._object_path(sha))

    # ------------------------------------------------------------------ #
    # index / staging area (also the stat cache)
    # ------------------------------------------------------------------ #
    def index(self) -> dict:
        if self._index is None:
            path = os.path.join(self.meta, "index.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    self._index = json.load(fh).get("entries", {})
            else:
                self._index = {}
        return self._index

    def index_save(self):
        path = os.path.join(self.meta, "index.json")
        tmp = path + ".tmp%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"version": INDEX_VERSION, "entries": self.index()}, fh,
                      ensure_ascii=False)
        os.replace(tmp, path)

    def index_tree(self) -> dict:
        return {name: ent["sha"] for name, ent in self.index().items()}

    def _stat_of(self, full: str):
        st = os.stat(full)
        return st.st_mtime_ns, st.st_size

    def hash_workfile(self, name: str) -> str:
        """Hash a component, reusing the cached digest when the stat is unchanged."""
        full = self.workpath(name)
        mtime_ns, size = self._stat_of(full)
        ent = self.index().get(name)
        if ent and ent.get("mtime_ns") == mtime_ns and ent.get("size") == size:
            return ent["sha"]
        return _sha_file(full)

    def scan(self) -> dict:
        """Working tree as {component name: sha}, using the stat cache where possible.

        Only files whose (mtime_ns, size) moved are actually read and hashed; the rest are
        answered from the index.  This is what keeps status/commit O(stat) rather than
        O(bytes) on a 175 MB worktree.
        """
        tree = {}
        pending = []
        for name, full in self.walk_worktree():
            mtime_ns, size = self._stat_of(full)
            ent = self.index().get(name)
            if ent and ent.get("mtime_ns") == mtime_ns and ent.get("size") == size:
                tree[name] = ent["sha"]
            else:
                pending.append((name, full))
        if pending:
            with ThreadPoolExecutor(max_workers=HASH_WORKERS) as pool:
                for (name, _full), sha in zip(pending, pool.map(lambda p: _sha_file(p[1]), pending)):
                    tree[name] = sha
        return tree

    # ------------------------------------------------------------------ #
    # cache maintenance
    # ------------------------------------------------------------------ #
    def _refresh_index_stats(self, tree: dict, save: bool = True):
        """Record fresh stat data + digests for unchanged files so the next scan is cheap."""
        changed = False
        for name, sha in tree.items():
            ent = self.index().get(name)
            full = self.workpath(name)
            if not os.path.exists(full):
                continue
            mtime_ns, size = self._stat_of(full)
            if ent and ent["sha"] == sha and ent.get("mtime_ns") == mtime_ns and ent.get("size") == size:
                continue
            if ent is None or ent["sha"] != sha:
                continue
            ent["mtime_ns"] = mtime_ns
            ent["size"] = size
            changed = True
        if changed and save:
            self.index_save()

    # ------------------------------------------------------------------ #
    # refs / HEAD
    # ------------------------------------------------------------------ #
    def _head_path(self) -> str:
        return os.path.join(self.meta, "HEAD")

    def _write_head(self, text: str):
        with open(self._head_path(), "w", encoding="utf-8") as fh:
            fh.write(text.strip() + "\n")

    def head_text(self) -> str:
        with open(self._head_path(), encoding="utf-8") as fh:
            return fh.read().strip()

    def head_ref(self) -> str:
        text = self.head_text()
        return text.split(":", 1)[1].strip() if text.startswith("ref:") else ""

    def is_detached(self) -> bool:
        return not self.head_text().startswith("ref:")

    def current_branch(self) -> str:
        ref = self.head_ref()
        return ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ""

    def _ref_path(self, ref: str) -> str:
        return os.path.join(self.meta, ref.replace("/", os.sep))

    def read_ref(self, ref: str):
        path = self._ref_path(ref)
        if not os.path.exists(path):
            return None
        with open(path, encoding="utf-8") as fh:
            value = fh.read().strip()
        return value or None

    def write_ref(self, ref: str, sha: str):
        path = self._ref_path(ref)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(sha + "\n")

    def delete_ref(self, ref: str):
        path = self._ref_path(ref)
        if os.path.exists(path):
            os.remove(path)

    def branches(self) -> list:
        heads = os.path.join(self.meta, "refs", "heads")
        out = []
        for dirpath, _dirnames, filenames in os.walk(heads):
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                out.append(os.path.relpath(full, heads).replace(os.sep, "/"))
        return sorted(out)

    def tags(self) -> list:
        tdir = os.path.join(self.meta, "refs", "tags")
        if not os.path.isdir(tdir):
            return []
        return sorted(os.listdir(tdir))

    def head_commit(self):
        if self.is_detached():
            return self.head_text() or None
        return self.read_ref(self.head_ref())

    # ------------------------------------------------------------------ #
    # commits
    # ------------------------------------------------------------------ #
    def commit_data(self, sha: str) -> dict:
        path = os.path.join(self.meta, "commits", sha + ".json")
        if not os.path.exists(path):
            raise RepoError("unknown revision %r" % sha)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def commit_tree(self, sha: str) -> dict:
        return self.commit_data(sha)["tree"] if sha else {}

    def parents(self, sha: str) -> list:
        c = self.commit_data(sha)
        return c.get("parents", [c["parent"]] if c.get("parent") else [])

    def resolve(self, rev: str) -> str:
        """Resolve HEAD, a branch, a tag, a full/short sha, or HEAD~n / HEAD^n."""
        if not rev:
            return None
        base, _, suffix = rev.partition("~")
        sha = self._resolve_base(base)
        if suffix:
            for _ in range(int(suffix or 1)):
                ps = self.parents(sha)
                if not ps:
                    raise RepoError("revision %r has no parent" % rev)
                sha = ps[0]
        return sha

    def _resolve_base(self, rev: str):
        if rev in ("HEAD", "@"):
            sha = self.head_commit()
            if not sha:
                raise RepoError("HEAD does not point at any commit yet")
            return sha
        for ref in ("refs/heads/" + rev, "refs/tags/" + rev, rev):
            sha = self.read_ref(ref)
            if sha:
                return sha
        if len(rev) >= 4:
            matches = [f[:-5] for f in os.listdir(os.path.join(self.meta, "commits"))
                       if f.endswith(".json") and f.startswith(rev)]
            if len(matches) == 1:
                return matches[0]
            if len(matches) > 1:
                raise RepoError("ambiguous revision %r" % rev)
        raise RepoError("unknown revision %r" % rev)

    def commit(self, message: str, author: str = "sc2diff", allow_empty: bool = False,
               extra_parents: list = None) -> str:
        tree = self.index_tree()
        missing = [n for n, sha in tree.items() if not self.has_object(sha)]
        if missing:
            raise RepoError("object store is missing %d staged component(s), e.g. %s "
                            "(run 'sc2diff add -A')" % (len(missing), missing[0]))
        parents = [p for p in ([self.head_commit()] + list(extra_parents or [])) if p]
        if not allow_empty and parents and self.commit_tree(parents[0]) == tree and len(parents) == 1:
            raise RepoError("nothing to commit (index matches HEAD)")
        payload = {"parents": parents, "message": message, "tree": tree,
                   "author": author, "time": int(time.time())}
        cid = _sha(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8"))[:16]
        payload["id"] = cid
        path = os.path.join(self.meta, "commits", cid + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        if not self.is_detached():
            self.write_ref(self.head_ref(), cid)
        else:
            self._write_head(cid)
        self._refresh_index_stats(tree)
        return cid

    def log(self, rev: str = None, limit: int = None) -> list:
        """Commits newest first, ordered by graph generation.

        Sorting by timestamp alone is not enough: commits made in the same second can come
        out in any order, so a parent may be printed above its child.
        """
        tip = self.resolve(rev) if rev else self.head_commit()
        if not tip:
            return []
        order, gen = [], {tip: 0}
        queue = [tip]
        while queue:
            sha = queue.pop(0)
            order.append(sha)
            for p in self.parents(sha):
                if gen.get(p, -1) < gen[sha] + 1:
                    gen[p] = gen[sha] + 1
                    queue.append(p)
        order.sort(key=lambda s: (gen[s], -self.commit_data(s)["time"]))
        out = [self.commit_data(s) for s in order]
        return out[:limit] if limit else out

    def _ancestors(self, start: str) -> set:
        """Every commit reachable from start (memoised)."""
        cache = getattr(self, "_anc_cache", None)
        if cache is None:
            cache = self._anc_cache = {}
        if start in cache:
            return cache[start]
        seen, stack = set(), [start]
        while stack:
            sha = stack.pop()
            if not sha or sha in seen:
                continue
            if sha in cache:                      # reuse a previously walked subgraph
                seen |= cache[sha]
                continue
            seen.add(sha)
            stack.extend(self.parents(sha))
        cache[start] = seen
        return seen

    def merge_base(self, a: str, b: str):
        """Best common ancestor.

        Not by commit timestamp: commits made within the same second compare equal, and
        picking by time then returns the repository root instead of the parent -- which
        silently turns a fast-forwardable update into a bogus "diverged".  Instead take the
        maximal elements of the common ancestor set (those that are not themselves an
        ancestor of another common commit) and prefer the closest to the tips.
        """
        if not a or not b:
            return None
        common = self._ancestors(a) & self._ancestors(b)
        if not common:
            return None
        best, best_depth = None, -1
        for cand in common:
            if any(cand != other and cand in self._ancestors(other) for other in common):
                continue                          # not maximal: some other candidate descends from it
            depth = len(self._ancestors(cand))
            if depth > best_depth:
                best, best_depth = cand, depth
        return best

    # ------------------------------------------------------------------ #
    # staging
    # ------------------------------------------------------------------ #
    def add(self, paths: list = None, all: bool = False) -> dict:
        """Stage components.  Mirrors git add / git add -A / git rm."""
        index = self.index()
        known = self._known_names()
        staged, removed = [], []
        if all or not paths:
            present = {name: full for name, full in self.walk_worktree()}
            # reuse the stat cache: only files whose stat moved get read
            tree = self.scan()
            for name, sha in tree.items():
                full = present[name]
                if not self.has_object(sha):
                    self.put(_read_all(full))      # scan() only hashes; the blob may not exist yet
                mtime_ns, size = self._stat_of(full)
                index[name] = {"sha": sha, "mtime_ns": mtime_ns, "size": size}
                staged.append(name)
            if all:                       # git add -A also stages deletions
                for name in list(index):
                    if name not in present:
                        del index[name]
                        removed.append(name)
        else:
            for raw in paths:
                if raw in (".", "./", ":\\", ":\\"):
                    return self.add(all=True)
                name = self._canonical_name(raw, known)
                full = self.workpath(name)
                if os.path.isdir(full):
                    for sub, subfull in self.walk_worktree():
                        if sub == name or sub.startswith(name + "\\"):
                            mtime_ns, size = self._stat_of(subfull)
                            index[sub] = {"sha": self.put(_read_all(subfull)),
                                          "mtime_ns": mtime_ns, "size": size}
                            staged.append(sub)
                elif os.path.isfile(full):
                    mtime_ns, size = self._stat_of(full)
                    sha = self.hash_workfile(name)
                    if not self.has_object(sha):
                        self.put(_read_all(full))
                    index[name] = {"sha": sha, "mtime_ns": mtime_ns, "size": size}
                    staged.append(name)
                elif name in index:
                    del index[name]
                    removed.append(name)
                else:
                    raise RepoError("pathspec %r did not match any component" % raw)
        self.index_save()   
        return {"staged": staged, "removed": removed}

    def restore(self, paths: list = None, staged: bool = False, all: bool = False) -> dict:
        """restore --staged (unstage) or restore (discard working-tree changes)."""
        index = self.index()
        head_tree = self.commit_tree(self.head_commit())
        targets = list(paths or [])
        if all or not targets:
            targets = sorted(set(list(index) + list(head_tree)))
        else:
            known = self._known_names()
            targets = [self._canonical_name(p, known) for p in targets]
        if staged:
            for name in targets:
                if name in head_tree:
                    index[name] = _index_entry_from_tree(self, name, head_tree[name], trust_stat=False)
                else:
                    index.pop(name, None)
        else:
            written = []
            for name in targets:
                if name in head_tree:
                    self.write_workfile(name, self.get(head_tree[name]))
                    written.append(name)
            self.index_save()
            return {"restored": written}
        self.index_save()
        return {"restored": targets}

    # ------------------------------------------------------------------ #
    # worktree writes
    # ------------------------------------------------------------------ #
    def write_workfile(self, name: str, data: bytes):
        path = self.workpath(name)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp%d" % os.getpid()
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)

    def read_workfile(self, name: str) -> bytes:
        with open(self.workpath(name), "rb") as fh:
            return fh.read()

    def _import_archive(self, archive, parallel: bool = True) -> list:
        """Unpack every component straight into the working tree."""
        blocks = []
        for i in range(archive.block_count):
            if not archive.block_table[i].exists:
                continue
            name = archive.block_name(i)
            if not name or name in BOOKKEEPING:
                continue
            blocks.append((i, name))
        def worker(item):
            i, name = item
            return name, archive.read_block(i, name)
        if parallel:
            with ThreadPoolExecutor(max_workers=HASH_WORKERS) as pool:
                results = list(pool.map(worker, blocks))
        else:
            results = [worker(b) for b in blocks]
        names = []
        for name, data in results:
            self.write_workfile(name, data or b"")
            names.append(name)
        return names

    # ------------------------------------------------------------------ #
    # status / diff
    # ------------------------------------------------------------------ #
    def status(self, use_cache: bool = True) -> dict:
        """Three-way status like git: staged vs HEAD, unstaged vs index, untracked."""
        head_tree = self.commit_tree(self.head_commit())
        index_tree = self.index_tree()
        work = self.scan() if use_cache else self.scan_uncached()
        staged, unstaged, untracked, deleted = {}, {}, [], []
        for name in sorted(set(head_tree) | set(index_tree)):
            a, b = head_tree.get(name), index_tree.get(name)
            if a != b:
                staged[name] = "added" if a is None else ("deleted" if b is None else "modified")
        for name in sorted(set(index_tree) | set(work)):
            if name not in index_tree:
                untracked.append(name)
            elif name not in work:
                unstaged[name] = "deleted"
            elif work[name] != index_tree[name]:
                unstaged[name] = "modified"
        return {"staged": staged, "unstaged": unstaged, "untracked": untracked,
                "work": work, "index": index_tree, "head": head_tree}

    def scan_uncached(self) -> dict:
        return {name: _sha_file(full) for name, full in self.walk_worktree()}

    def diff_trees(self, old: dict, new: dict) -> dict:
        changes = {}
        for name in sorted(set(old) | set(new)):
            if name in BOOKKEEPING:
                continue
            a, b = old.get(name), new.get(name)
            if a == b:
                continue
            if a is None:
                changes[name] = {"type": "added", "sha": b}
                continue
            if b is None:
                changes[name] = {"type": "deleted", "sha": a}
                continue
            d = sc2semantic.diff_model(sc2semantic.canonical(name, self.get(a)),
                                       sc2semantic.canonical(name, self.get(b)))
            if not sc2semantic.is_empty(d):
                changes[name] = {"type": "modified", "model": d}
        return changes

    def diff_index(self) -> dict:
        """git diff --cached : index vs HEAD."""
        return self.diff_trees(self.commit_tree(self.head_commit()), self.index_tree())

    def diff_working(self, vs: str = "index") -> dict:
        """git diff : working tree vs index (default) or vs HEAD.

        The working side cannot go through the object store: a modified file has been hashed
        by scan() but its bytes are not a blob until it is staged.  Read it from disk.
        """
        work = self.scan()
        base = self.index_tree() if vs == "index" else self.commit_tree(self.head_commit())
        changes = {}
        for name in sorted(set(base) | set(work)):
            if name in BOOKKEEPING:
                continue
            a, b = base.get(name), work.get(name)
            if a == b:
                continue
            if a is None:
                changes[name] = {"type": "added", "sha": b}
                continue
            if b is None:
                changes[name] = {"type": "deleted", "sha": a}
                continue
            try:
                new_data = self.read_workfile(name)
            except OSError:
                changes[name] = {"type": "deleted", "sha": a}
                continue
            d = sc2semantic.diff_model(sc2semantic.canonical(name, self.get(a)),
                                       sc2semantic.canonical(name, new_data))
            if not sc2semantic.is_empty(d):
                changes[name] = {"type": "modified", "model": d}
        return changes

    # ------------------------------------------------------------------ #
    # checkout / branch / tag
    # ------------------------------------------------------------------ #
    def create_branch(self, name: str, rev: str = None, force: bool = False) -> str:
        ref = "refs/heads/" + name
        if self.read_ref(ref) and not force:
            raise RepoError("a branch named %r already exists" % name)
        sha = self.resolve(rev) if rev else self.head_commit()
        if not sha:
            raise RepoError("cannot create a branch before the first commit")
        self.write_ref(ref, sha)
        return sha

    def delete_branch(self, name: str, force: bool = False) -> bool:
        ref = "refs/heads/" + name
        sha = self.read_ref(ref)
        if not sha:
            raise RepoError("branch %r not found" % name)
        if name == self.current_branch():
            raise RepoError("cannot delete the branch you are on")
        if not force:
            head = self.head_commit()
            base = self.merge_base(head, sha)
            if base != sha:
                raise RepoError("branch %r is not fully merged (use -D to force)" % name)
        self.delete_ref(ref)
        return True

    def rename_branch(self, old: str, new: str):
        sha = self.read_ref("refs/heads/" + old)
        if not sha:
            raise RepoError("branch %r not found" % old)
        self.write_ref("refs/heads/" + new, sha)
        self.delete_ref("refs/heads/" + old)
        if self.current_branch() == old:
            self._write_head("ref: refs/heads/" + new)

    def create_tag(self, name: str, rev: str = None, force: bool = False):
        ref = "refs/tags/" + name
        if self.read_ref(ref) and not force:
            raise RepoError("tag %r already exists" % name)
        sha = self.resolve(rev) if rev else self.head_commit()
        if not sha:
            raise RepoError("cannot tag before the first commit")
        self.write_ref(ref, sha)
        return sha

    def delete_tag(self, name: str):
        if not self.read_ref("refs/tags/" + name):
            raise RepoError("tag %r not found" % name)
        self.delete_ref("refs/tags/" + name)

    def _apply_tree(self, tree: dict, force: bool = False) -> dict:
        written, removed = [], []
        current = self.scan()
        for name, sha in tree.items():
            if name not in current or current[name] != sha:
                self.write_workfile(name, self.get(sha))
                written.append(name)
        for name in current:
            if name not in tree:
                path = self.workpath(name)
                if os.path.exists(path):
                    os.remove(path)
                removed.append(name)
        self._index = {name: _index_entry_from_tree(self, name, sha, trust_stat=True)
                       for name, sha in tree.items()}
        self.index_save()
        return {"written": written, "removed": removed}

    def checkout(self, target: str, force: bool = False, create: bool = False) -> dict:
        """git checkout: branch name, tag, or commit (detached)."""
        if self.dirty() and not force:
            raise RepoError("you have local changes; commit them or use -f")
        if create:
            self.create_branch(target)
        branch_ref = "refs/heads/" + target
        tag_ref = "refs/tags/" + target
        if self.read_ref(branch_ref):
            sha = self.read_ref(branch_ref)
            self._write_head("ref: " + branch_ref)
            mode = "branch"
        elif self.read_ref(tag_ref):
            sha = self.read_ref(tag_ref)
            self._write_head(sha)
            mode = "detached"
        else:
            sha = self.resolve(target)
            self._write_head(sha)
            mode = "detached"
        result = self._apply_tree(self.commit_tree(sha), force=force)
        result.update({"sha": sha, "mode": mode, "target": target})
        return result

    # ------------------------------------------------------------------ #
    # SC2 extras: repack the working tree into a container',
    # ------------------------------------------------------------------ #
    def pack(self, dest: str) -> dict:
        """Rebuild a .SC2Map / .SC2Mod from the working tree.

        Original block order, hash-table size, BET unknown08 and per-component layout hints
        are carried in config.json so the rebuilt container is structurally as close to the
        source as the content allows.
        """
        cfg = self.config
        names = cfg.get("names") or []
        hints = cfg.get("layout_hints") or {}
        tree = self.scan()
        ordered = [n for n in names if n in tree]
        ordered += [n for n in sorted(tree) if n not in names]
        entries = [(n, self.get(tree[n])) for n in ordered]
        listfile = None
        if ordered == list(names) and cfg.get("listfile"):
            listfile = cfg["listfile"].encode("utf-8")
        return sc2mpq.build_document(entries, dest,
                                     hash_count=cfg.get("hash_count"),
                                     block_shift=cfg.get("block_shift", 5),
                                     unknown08=cfg.get("unknown08", 0x10),
                                     attr_flags=cfg.get("attr_flags", 0x05),
                                     listfile=listfile, hints=hints)

    # ------------------------------------------------------------------ #
    # patch bundles -- the exchange format (there is no server)
    #
    # A bundle is a plain ZIP holding manifest.json, the commits, and the raw
    # component payloads.  Deliberately a zip: it travels through the channels these
    # projects already use (网盘 / 群文件 / 共享目录) with no new infrastructure, and it
    # is inspectable with any zip tool.
    # ------------------------------------------------------------------ #
    def bundle(self, path: str, refs: list = None, basis: list = None) -> dict:
        """Pack the commits reachable from refs but not from basis into one file."""
        refs = refs or [self.current_branch() or "HEAD"]
        ref_shas, prereq = {}, set()
        for r in refs:
            ref_shas[r] = self.resolve(r)
        for b in (basis or []):
            prereq.add(self.resolve(b))
        # walk history from the tips, stopping at the prerequisites
        commits, seen = [], set()
        stack = [s for s in ref_shas.values() if s]
        while stack:
            sha = stack.pop()
            if not sha or sha in seen or sha in prereq:
                continue
            seen.add(sha)
            c = self.commit_data(sha)
            commits.append(c)
            stack.extend(self.parents(sha))
        # A thin bundle ships only what the receiver cannot already have: every component
        # present in any basis tree is excluded.  Without this the patch is as big as a
        # full copy, which defeats the whole purpose.
        basis_blobs = set()
        for sha in prereq:
            basis_blobs.update(self.commit_tree(sha).values())
        objects = sorted({sha for c in commits for sha in c["tree"].values()
                          if sha not in basis_blobs})
        commits.sort(key=lambda c: c["time"])
        manifest = {"magic": BUNDLE_MAGIC, "version": 1, "created": int(time.time()),
                    "refs": ref_shas, "prerequisites": sorted(prereq),
                    "commits": [c["id"] for c in commits], "objects": objects}
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            for c in commits:
                z.writestr("commits/%s.json" % c["id"], json.dumps(c, ensure_ascii=False))
            for sha in objects:
                z.writestr("objects/%s" % sha, self.get(sha))
        return {"path": os.path.abspath(path), "bytes": os.path.getsize(path),
                "refs": ref_shas, "commits": len(commits), "objects": len(objects),
                "prerequisites": sorted(prereq), "basis": list(basis or [])}

    def apply_bundle(self, path: str, update_refs: bool = True, checkout: bool = True) -> dict:
        """Import a bundle into this repository.  Refuses if prerequisites are missing."""
        # state *before* anything is imported: after the refs move, HEAD would already name
        # the incoming tree while the working tree is still empty, which looks like 65
        # staged additions and would wrongly block the checkout.
        preexisting_dirty = self.dirty()
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            if "manifest.json" not in names:
                raise RepoError("%s is not a sc2diff bundle" % path)
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
            if manifest.get("magic") != BUNDLE_MAGIC:
                raise RepoError("%s has an unrecognised bundle header" % path)
            missing = [p for p in manifest.get("prerequisites", [])
                       if not os.path.exists(os.path.join(self.meta, "commits", p + ".json"))]
            if missing:
                raise RepoError("bundle needs %d commit(s) this repository does not have, "
                                "e.g. %s (send a bundle with --basis omitted)"
                                % (len(missing), missing[0]))
            imported_objects = imported_commits = 0
            for name in manifest.get("objects", []):
                entry = "objects/" + name
                if entry in names:
                    data = z.read(entry)
                    if self.put(data) == name:
                        imported_objects += 1
            for cid in manifest.get("commits", []):
                dst = os.path.join(self.meta, "commits", cid + ".json")
                if os.path.exists(dst):
                    continue
                payload = json.loads(z.read("commits/%s.json" % cid).decode("utf-8"))
                with open(dst, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=1)
                imported_commits += 1
        actions = []
        if update_refs:
            for ref_name, sha in manifest.get("refs", {}).items():
                ref = ref_name if ref_name.startswith("refs/") else "refs/heads/" + ref_name
                local = self.read_ref(ref)
                if local is None:
                    self.write_ref(ref, sha)
                    actions.append(("created", ref_name, sha))
                elif local == sha:
                    actions.append(("up-to-date", ref_name, sha))
                elif self.merge_base(local, sha) == local:
                    self.write_ref(ref, sha)
                    actions.append(("fast-forward", ref_name, sha))
                else:
                    actions.append(("diverged", ref_name, sha))
        checked_out = None
        # fetch puts objects in the store; a patch arriving from a colleague should also
        # give them the files, which is what clone/pull do.  Only when nothing local is at
        # stake, and only after a created/fast-forward ref.
        if update_refs and checkout and any(k in ("created", "fast-forward") for k, _n, _s in actions):
            target_ref = None
            if not self.is_detached():
                target_ref = self.head_ref()
            elif actions:
                target_ref = "refs/heads/" + actions[0][1]
            if target_ref and not self.read_ref(target_ref) and actions:
                target_ref = "refs/heads/" + actions[0][1]
            sha = self.read_ref(target_ref) if target_ref else None
            if sha and not preexisting_dirty:
                if target_ref.startswith("refs/heads/"):
                    self._write_head("ref: " + target_ref)
                result = self._apply_tree(self.commit_tree(sha), force=True)
                checked_out = {"ref": target_ref, "sha": sha,
                               "written": len(result["written"]), "removed": len(result["removed"])}
        return {"path": os.path.abspath(path), "commits": imported_commits,
                "objects": imported_objects, "actions": actions,
                "checked_out": checked_out,
                "prerequisites": manifest.get("prerequisites", [])}

    def dirty(self) -> bool:
        st = self.status()
        return bool(st["staged"] or st["unstaged"])


def _read_all(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _index_entry_from_tree(repo: Repo, name: str, sha: str, trust_stat: bool = False) -> dict:
    """Index entry for a name taken from a tree.

    trust_stat may be True only right after the working file was written from that blob
    (checkout).  Otherwise the stat is left empty so the next scan re-hashes the file.
    """
    full = repo.workpath(name)
    ent = {"sha": sha, "mtime_ns": None, "size": None}
    if trust_stat and os.path.exists(full):
        st = os.stat(full)
        ent["mtime_ns"] = st.st_mtime_ns
        ent["size"] = st.st_size
    return ent
