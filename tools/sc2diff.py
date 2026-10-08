# -*- coding: utf-8 -*-
"""sc2diff -- StarCraft II document version control, with git-shaped commands.

    sc2diff [-C <repo>] init [<doc.SC2Map>]
    sc2diff add [-A] [<name>...]
    sc2diff rm [--cached] <name>...
    sc2diff status [-s]
    sc2diff diff [--cached] [<rev>] [<rev2>] [--json]
    sc2diff commit -m <msg> [-a] [--amend] [--allow-empty]
    sc2diff log [--oneline] [-n N] [<rev>]
    sc2diff show [<rev>]
    sc2diff branch [-a] [-d|-D <name>] [-m <old> <new>] [<name> [<start>]]
    sc2diff checkout [-b] [-f] <branch|rev>
    sc2diff switch [-c] <branch>
    sc2diff tag [-d] <name> [<rev>]
    sc2diff restore [--staged] <name>...
    sc2diff ls-files
    sc2diff unpack <doc.SC2Map>
    sc2diff pack <out.SC2Map>

Command names, flags and output shapes follow git deliberately: an agent that knows git
should not have to learn a second dialect.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sc2mpq
import sc2semantic
import sc2repo


# --------------------------------------------------------------------- #
# output helpers
# --------------------------------------------------------------------- #
def _describe(name, c):
    if c["type"] == "added":
        return "new component"
    if c["type"] == "deleted":
        return "deleted component"
    return sc2semantic.describe(name, c["model"])


def _render(name, c):
    if c["type"] == "added":
        print("new component %s" % name)
    elif c["type"] == "deleted":
        print("deleted component %s" % name)
    else:
        print(sc2semantic.render(name, c["model"]))


def _print_diff(changes, header, as_json=False, max_entities=8):
    if as_json:
        print(json.dumps(changes, ensure_ascii=False, indent=1, default=str))
        return 0
    print("diff %s" % header)
    if not changes:
        print("  (no changes)")
        return 0
    for name in sorted(changes):
        print("  %-58s %s" % (name, _describe(name, changes[name])))
    detailed = [n for n in sorted(changes) if changes[n]["type"] == "modified"][:max_entities]
    if detailed:
        print()
        for name in detailed:
            _render(name, changes[name])
        modified = [n for n in changes if changes[n]["type"] == "modified"]
        if len(modified) > len(detailed):
            print("... detail limited to %d components" % max_entities)
    print("\n%d component(s) changed" % len(changes))
    return 0


def _repo(args):
    root = args.C or getattr(args, "dir", None) or "."
    repo = sc2repo.Repo(root)
    if not repo.exists():
        raise sc2repo.RepoError("not a sc2diff repository (or any parent): %s" % os.path.abspath(root))
    return repo


# --------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------- #
def cmd_init(args):
    root = args.C or args.dir
    repo = sc2repo.Repo.init(root, args.source)
    cfg = repo.config
    print("Initialized empty sc2diff repository in %s" % os.path.join(repo.meta, ""))
    if args.source:
        print("  imported %s" % cfg.get("source"))
        print("  components: %d   (staged with add -A)" % len(cfg.get("names") or []))
        print("  container : hash_entries=%s block_shift=%s unknown08=%s"
              % (cfg.get("hash_count"), cfg.get("block_shift"), cfg.get("unknown08")))
    return 0


def cmd_add(args):
    repo = _repo(args)
    r = repo.add(args.paths or None, all=args.all)
    for n in r["staged"]:
        print("add      %s" % n)
    for n in r["removed"]:
        print("remove   %s" % n)
    if not r["staged"] and not r["removed"]:
        print("nothing to add")
    return 0


def cmd_rm(args):
    repo = _repo(args)
    for raw in args.paths:
        name = repo._canonical_name(raw, repo._known_names())
        if args.cached:
            repo.index().pop(name, None)
            print("rm (cached) %s" % name)
        else:
            path = repo.workpath(name)
            if os.path.exists(path):
                os.remove(path)
            repo.index().pop(name, None)
            print("rm          %s" % name)
    repo.index_save()
    return 0


def cmd_status(args):
    repo = _repo(args)
    st = repo.status()
    head = repo.head_commit()
    if args.short:
        for name, kind in sorted(st["staged"].items()):
            print("%s  %s" % ({"added": "A", "modified": "M", "deleted": "D"}[kind], name))
        for name, kind in sorted(st["unstaged"].items()):
            print(" %s %s" % ({"modified": "M", "deleted": "D"}[kind], name))
        for name in st["untracked"]:
            print("?? %s" % name)
        return 0
    if repo.is_detached():
        print("HEAD detached at %s" % (head or "(none)"))
    else:
        print("On branch %s" % repo.current_branch())
    if not head:
        print("\nNo commits yet")
    if st["staged"]:
        print("\nChanges to be committed:")
        print("  (use \"sc2diff restore --staged <name>...\" to unstage)")
        for name, kind in sorted(st["staged"].items()):
            print("        %-9s %s" % (kind + ":", name))
    if st["unstaged"]:
        print("\nChanges not staged for commit:")
        print("  (use \"sc2diff add <name>...\" to update what will be committed)")
        for name, kind in sorted(st["unstaged"].items()):
            print("        %-9s %s" % (kind + ":", name))
    if st["untracked"]:
        print("\nUntracked files:")
        print("  (use \"sc2diff add <name>...\" to include in what will be committed)")
        for name in st["untracked"][:40]:
            print("        %s" % name)
        if len(st["untracked"]) > 40:
            print("        ... %d more" % (len(st["untracked"]) - 40))
    if not (st["staged"] or st["unstaged"] or st["untracked"]):
        print("nothing to commit, working tree clean")
    return 0


def cmd_diff(args):
    repo = _repo(args)
    if args.cached:
        changes = repo.diff_index()
        header = "index vs HEAD %s" % (repo.head_commit() or "(none)")
    elif args.rev1 and args.rev2:
        changes = repo.diff_trees(repo.commit_tree(repo.resolve(args.rev1)),
                                  repo.commit_tree(repo.resolve(args.rev2)))
        header = "%s -> %s" % (args.rev1, args.rev2)
    elif args.rev1:
        changes = repo.diff_trees(repo.commit_tree(repo.resolve(args.rev1)), repo.scan())
        header = "%s -> working tree" % args.rev1
    else:
        changes = repo.diff_working()
        header = "working tree vs index"
    return _print_diff(changes, header, args.json, args.max_entities)


def cmd_commit(args):
    repo = _repo(args)
    if args.all:
        repo.add(all=True)
    if args.amend:
        old = repo.commit_data(repo.head_commit())
        repo._write_head(old["parents"][0] if old["parents"] else "")
    try:
        cid = repo.commit(args.message, allow_empty=args.allow_empty)
    except sc2repo.RepoError as exc:
        print(str(exc))
        return 1
    branch = repo.current_branch() or "(detached)"
    n = len(repo.commit_tree(cid))
    print("[%s %s] %s" % (branch, cid, args.message))
    print("  %d component(s) in tree" % n)
    return 0


def cmd_log(args):
    repo = _repo(args)
    for c in repo.log(args.rev, args.n):
        if args.oneline:
            print("%s %s" % (c["id"][:8], c["message"].splitlines()[0]))
        else:
            ts = datetime.datetime.fromtimestamp(c["time"]).strftime("%Y-%m-%d %H:%M:%S")
            print("commit %s" % c["id"])
            if len(c.get("parents") or []) > 1:
                print("Merge: %s" % " ".join(p[:8] for p in c["parents"]))
            print("Author: %s" % c.get("author"))
            print("Date:   %s" % ts)
            print("\n    %s\n" % c["message"])
    return 0


def cmd_show(args):
    repo = _repo(args)
    sha = repo.resolve(args.rev) if args.rev else repo.head_commit()
    c = repo.commit_data(sha)
    print("commit %s" % sha)
    print("Author: %s" % c.get("author"))
    print("Date:   %s" % datetime.datetime.fromtimestamp(c["time"]).strftime("%Y-%m-%d %H:%M:%S"))
    print("\n    %s\n" % c["message"])
    parents = c.get("parents") or []
    changes = repo.diff_trees(repo.commit_tree(parents[0]) if parents else {}, c["tree"])
    return _print_diff(changes, "%s (vs %s)" % (sha[:8], parents[0][:8] if parents else "empty"),
                       args.json, args.max_entities)


def cmd_branch(args):
    repo = _repo(args)
    if args.delete or args.force_delete:
        name = args.delete or args.force_delete
        repo.delete_branch(name, force=args.force or bool(args.force_delete))
        print("Deleted branch %s" % name)
        return 0
    if args.move:
        old, new = args.move
        repo.rename_branch(old, new)
        print("Renamed %s -> %s" % (old, new))
        return 0
    if args.name:
        sha = repo.create_branch(args.name, args.start, force=args.force)
        print("Created branch %s at %s" % (args.name, sha))
        return 0
    current = repo.current_branch()
    for b in repo.branches():
        print("%s %s" % ("*" if b == current else " ", b))
    if args.all:
        for t in repo.tags():
            print("  tag %s" % t)
    return 0


def cmd_checkout(args):
    repo = _repo(args)
    try:
        r = repo.checkout(args.target, force=args.force, create=args.branch)
    except sc2repo.RepoError as exc:
        print(str(exc))
        return 1
    if r["mode"] == "branch":
        print("Switched to branch %r" % r["target"])
    else:
        print("HEAD is now at %s (detached)" % r["sha"][:8])
    print("  %d written, %d removed" % (len(r["written"]), len(r["removed"])))
    return 0


def cmd_switch(args):
    args.target = args.name
    args.branch = args.create
    args.force = False
    return cmd_checkout(args)


def cmd_tag(args):
    repo = _repo(args)
    if args.delete:
        repo.delete_tag(args.delete)
        print("Deleted tag %s" % args.delete)
        return 0
    if not args.name:
        for t in repo.tags():
            print(t)
        return 0
    sha = repo.create_tag(args.name, args.rev, force=args.force)
    print("Tagged %s at %s" % (args.name, sha))
    return 0


def cmd_restore(args):
    repo = _repo(args)
    r = repo.restore(args.paths, staged=args.staged, all=args.all)
    for n in r["restored"]:
        print("restored %s" % n)
    return 0


def cmd_ls_files(args):
    repo = _repo(args)
    for name in sorted(repo.index_tree()):
        print(name)
    return 0


def cmd_unpack(args):
    doc = sc2mpq.Document.open(args.doc)
    dest = args.C or args.dir
    n = 0
    for name, data in doc.entries:
        if name in sc2mpq.BOOKKEEPING:
            continue
        path = os.path.join(dest, name.replace("\\", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        n += 1
    print("unpacked %d components to %s" % (n, dest))
    return 0


def cmd_bundle(args):
    repo = _repo(args)
    info = repo.bundle(args.out, args.refs or None, args.basis or None)
    print("bundle %s" % args.out)
    print("  %d commit(s), %d object(s), %.1f KB"
          % (info["commits"], info["objects"], info["bytes"] / 1024))
    for name, sha in info["refs"].items():
        print("  ref %-20s %s" % (name, sha))
    if info["prerequisites"]:
        print("  requires these commits to exist on the other side:")
        for p in info["prerequisites"]:
            print("    %s" % p)
    print("  -> send this file by any means (shared folder, chat, netdisk); no server needed")
    return 0


def cmd_apply(args):
    repo = _repo(args)
    try:
        info = repo.apply_bundle(args.file, update_refs=not args.no_refs,
                                 checkout=not args.no_checkout)
    except sc2repo.RepoError as exc:
        print("fatal: %s" % exc, file=sys.stderr)
        return 1
    print("applied %s" % args.file)
    print("  imported %d commit(s), %d object(s)" % (info["commits"], info["objects"]))
    for kind, name, sha in info["actions"]:
        print("  %-13s %-20s %s" % (kind, name, sha[:8]))
        if kind == "diverged":
            print("      run: sc2diff merge %s" % name)
    if info.get("checked_out"):
        co = info["checked_out"]
        print("  checked out %s at %s (%d files written)"
              % (co["ref"].split("/")[-1], co["sha"][:8], co["written"]))
    return 0


def cmd_pack(args):
    repo = _repo(args)
    info = repo.pack(args.out)
    print("packed %s" % args.out)
    print("  " + json.dumps({k: v for k, v in info.items() if k != "path"}, ensure_ascii=False))
    v = sc2mpq.MPQArchive(args.out).verify(deep=False)
    print("  container: HET=%s BET=%s attributes=%s blocks=%s rawChunkSize=%d"
          % (v["het"], v["bet"], v["attributes"], v["blocks"],
             __import__("struct").unpack_from("<I", sc2mpq.MPQArchive(args.out).raw, 108)[0]))
    return 0


# --------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------- #
def build_parser():
    ap = argparse.ArgumentParser(prog="sc2diff", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-C", metavar="<path>", help="run as if started in <path>")
    ap.add_argument("-d", "--dir", metavar="<path>", help="alias of -C")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init"); p.add_argument("source", nargs="?"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("add"); p.add_argument("paths", nargs="*")
    p.add_argument("-A", "--all", action="store_true"); p.set_defaults(fn=cmd_add)
    p = sub.add_parser("rm"); p.add_argument("paths", nargs="+")
    p.add_argument("--cached", action="store_true"); p.set_defaults(fn=cmd_rm)
    p = sub.add_parser("status"); p.add_argument("-s", "--short", action="store_true")
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("diff"); p.add_argument("rev1", nargs="?"); p.add_argument("rev2", nargs="?")
    p.add_argument("--cached", "--staged", dest="cached", action="store_true")
    p.add_argument("--json", action="store_true"); p.add_argument("--max-entities", type=int, default=8)
    p.set_defaults(fn=cmd_diff)
    p = sub.add_parser("commit"); p.add_argument("-m", "--message", required=True)
    p.add_argument("-a", "--all", action="store_true"); p.add_argument("--amend", action="store_true")
    p.add_argument("--allow-empty", action="store_true"); p.set_defaults(fn=cmd_commit)
    p = sub.add_parser("log"); p.add_argument("rev", nargs="?")
    p.add_argument("-n", type=int, default=None); p.add_argument("--oneline", action="store_true")
    p.set_defaults(fn=cmd_log)
    p = sub.add_parser("show"); p.add_argument("rev", nargs="?"); p.add_argument("--json", action="store_true")
    p.add_argument("--max-entities", type=int, default=8); p.set_defaults(fn=cmd_show)
    p = sub.add_parser("branch"); p.add_argument("name", nargs="?"); p.add_argument("start", nargs="?")
    p.add_argument("-a", "--all", action="store_true"); p.add_argument("-d", "--delete")
    p.add_argument("-D", dest="force_delete", metavar="<name>")
    p.add_argument("-m", "--move", nargs=2)
    p.add_argument("-f", "--force", action="store_true"); p.set_defaults(fn=cmd_branch)
    p = sub.add_parser("checkout"); p.add_argument("target")
    p.add_argument("-b", dest="branch", action="store_true"); p.add_argument("-f", "--force", action="store_true")
    p.set_defaults(fn=cmd_checkout)
    p = sub.add_parser("switch"); p.add_argument("name")
    p.add_argument("-c", "--create", action="store_true"); p.set_defaults(fn=cmd_switch)
    p = sub.add_parser("tag"); p.add_argument("name", nargs="?"); p.add_argument("rev", nargs="?")
    p.add_argument("-d", "--delete"); p.add_argument("-f", "--force", action="store_true")
    p.set_defaults(fn=cmd_tag)
    p = sub.add_parser("restore"); p.add_argument("paths", nargs="*")
    p.add_argument("--staged", action="store_true"); p.add_argument("-A", "--all", action="store_true")
    p.add_argument("-f", "--force", action="store_true"); p.set_defaults(fn=cmd_restore)
    p = sub.add_parser("ls-files"); p.set_defaults(fn=cmd_ls_files)
    p = sub.add_parser("unpack"); p.add_argument("doc"); p.set_defaults(fn=cmd_unpack)
    p = sub.add_parser("pack"); p.add_argument("out"); p.set_defaults(fn=cmd_pack)
    p = sub.add_parser("bundle"); p.add_argument("out")
    p.add_argument("--refs", nargs="*", help="refs to include (default: current branch)")
    p.add_argument("--basis", nargs="*", help="revisions the receiver already has (makes a thin patch)")
    p.set_defaults(fn=cmd_bundle)
    p = sub.add_parser("apply"); p.add_argument("file")
    p.add_argument("--no-refs", action="store_true")
    p.add_argument("--no-checkout", action="store_true"); p.set_defaults(fn=cmd_apply)
    p = sub.add_parser("unbundle"); p.add_argument("file")
    p.add_argument("--no-refs", action="store_true")
    p.add_argument("--no-checkout", action="store_true"); p.set_defaults(fn=cmd_apply)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.C and args.dir:
        args.dir = None
    if args.C:
        # git -C requires the directory to exist; init is allowed to create it
        if args.fn is cmd_init:
            os.makedirs(args.C, exist_ok=True)
        os.chdir(args.C)
    try:
        return args.fn(args)
    except sc2repo.RepoError as exc:
        print("fatal: %s" % exc, file=sys.stderr)
        return 128
    except KeyError as exc:
        print("fatal: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
