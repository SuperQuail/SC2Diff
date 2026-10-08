# -*- coding: utf-8 -*-
"""Extract small real samples (SC2Map / SC2Mod MPQ) out of the reference packages."""
import os, zipfile, sys, io

BASE = r"D:\Code\Rust\HSCL\reference\战役与补丁"
DEST = r"D:\Code\Rust\SC2Diff\testdata\samples"
os.makedirs(DEST, exist_ok=True)

def dec(n):
    try: return n.encode('cp437').decode('gbk')
    except Exception: return n

WANT = [
    ("净化者纪元幼儿园（提示必须看）.zip", ["paiur01.SC2Map", "0单位共享buff.SC2Mod"]),
    ("肉遗V2之猫灯的小布丁20260826.zip", ["SCORE-Other.SC2Mod"]),
    ("黄金之遗1.32(CCM).zip", ["paiur01.SC2Map", "HTXL.SC2Mod", "SCORE-OtherC.SC2Mod", "UmojanCT.SC2Mod"]),
    ("AI盟友虚空之遗（灵梦版）1.9.9.zip", []),
]

MANIFEST = []
for pkg, wants in WANT:
    p = os.path.join(BASE, pkg)
    if not os.path.exists(p):
        print("MISSING", pkg); continue
    z = zipfile.ZipFile(p)
    infos = [(i, dec(i.filename)) for i in z.infolist()]
    # record: for every entry that is a *file* whose name ends in .SC2Map/.SC2Mod -> likely MPQ
    for i, nm in infos:
        base = nm.split('/')[-1]
        if base.endswith('.SC2Map') or base.endswith('.SC2Mod') or base.endswith('.SC2Campaign'):
            MANIFEST.append((pkg, nm, i.file_size, i.compress_size))
    for w in wants:
        hit = None
        for i, nm in infos:
            if nm.split('/')[-1] == w:
                hit = (i, nm); break
        if not hit:
            print("  not found:", w, "in", pkg); continue
        i, nm = hit
        out = os.path.join(DEST, w)
        if os.path.exists(out):
            print("  exists:", out, os.path.getsize(out)); continue
        with z.open(i) as src, open(out, 'wb') as dst:
            while True:
                b = src.read(1 << 20)
                if not b: break
                dst.write(b)
        print("  extracted: %-24s %10d -> %s" % (w, os.path.getsize(out), out))

print()
print("=== document-shaped entries found in packages ===")
for pkg, nm, sz, cz in sorted(MANIFEST, key=lambda x: -x[2])[:60]:
    print("  %-46s %-46s %10d %10d" % (pkg[:46], nm[:46], sz, cz))
