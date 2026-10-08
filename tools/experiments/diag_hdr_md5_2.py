
import hashlib, struct, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
raw = open('testdata/samples/paiur01.SC2Map','rb').read()
h = raw[:208]
print('md5(header[0:0xC0])   =', hashlib.md5(h[:0xC0]).hexdigest())
print('stored MD5_MpqHeader  =', h[0xC0:0xD0].hex())
print('match:', hashlib.md5(h[:0xC0]).digest() == h[0xC0:0xD0])
