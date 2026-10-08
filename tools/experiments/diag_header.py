
import sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
a = open('testdata/samples/paiur01.SC2Map','rb').read(208)
b = open('testdata/rebuilt/paiur01.SC2Map','rb').read(208)
print('%-6s %-36s %-36s' % ('off','ORIGINAL','REPACKED'))
for i in range(0, 208, 16):
    ra, rb = a[i:i+16], b[i:i+16]
    mark = '' if ra == rb else '   <-- DIFFERS'
    print('%04X   %-36s %-36s%s' % (i, ra.hex(' '), rb.hex(' '), mark))
