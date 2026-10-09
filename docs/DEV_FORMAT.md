# 开发文档 · 容器格式

本文记录当前实现所依赖的格式事实。常数与布局都可以在源码里对应到。

## 1. 头部（208 字节）

| 偏移 | 字段 | 说明 |
| --- | --- | --- |
| 0x00 | magic | `MPQ\x1a` |
| 0x04 | headerSize | **208** |
| 0x08 | archiveSize | 文件总长度 |
| 0x0C | formatVersion | **3** |
| 0x0E | blockSizeShift | 5（16 KiB） |
| 0x10 / 0x14 | hashTablePos / blockTablePos | |
| 0x18 / 0x1C | hashTableEntries / blockTableEntries | |
| 0x2C | archiveSize64 | |
| 0x34 / 0x3C | betTablePos64 / hetTablePos64 | |
| 0x44 / 0x4C | hashTableSize64 / blockTableSize64 | 字节数 |
| 0x5C / 0x64 | hetTableSize64 / betTableSize64 | 字节数 |
| 0x6C | **dwRawChunkSize** | 必须为 **0**，见第 2 节 |
| 0x70 | MD5(块表) | |
| 0x80 | MD5(哈希表) | |
| 0x90 | MD5(hi-block 表) | 无该表时全零 |
| 0xA0 | MD5(BET) | |
| 0xB0 | MD5(HET) | |
| 0xC0 | MD5(头部前 0xC0 字节) | |

头部是「formatVersion=3 + v4 的 208 字节布局」的组合。

## 2. 两个决定编辑器是否接受改动的字段

### dwRawChunkSize（0x6C）必须写 0

该字段非 0 时会启用引擎按块校验文件数据的分支；该校验依据的摘要不来自 `(attributes)`，
因此**任何内容改动都会被判为归档损坏**，即使 `(attributes)` 的 CRC32 与 MD5 都正确。
formatVersion=3 的归档该字段应为 0。

### 摘要块（0x70..0xCF）必须正确

其中 0xC0 是头部的自哈希。缺失或不匹配的归档会被判为伪造头部。
六个值按上表顺序写入，覆盖范围：块表、哈希表、hi-block 表、BET、HET、头部自身。

## 3. 表加密

- 密钥：`hash_string("(hash table)", 3)` 与 `hash_string("(block table)", 3)`。
- 块密码只处理 4 字节的整数倍，**末尾不足 4 字节的部分原样保留**。

## 4. HET

    12 字节明文：签名 'HET\x1a' | version=1 | dataSize
    加密体：8 × u32 头 | nameHashes[totalCount] | indexBits[indexTableSize]

参数关系：

    totalCount     = entryCount * 4 / 3
    indexSize      = 容纳 entryCount 所需位数
    indexTableSize = (totalCount * indexSizeTotal + 7) / 8
    tableSize      = 32 + totalCount + indexTableSize   （等于 dataSize）

查找规则：条目只保存文件名哈希的高 8 位，命中后需要用 BET 里的完整哈希确认，
不匹配则继续向后探测。空槽值为 0x00。

## 5. BET

    12 字节明文：签名 'BET\x1a' | version=1 | dataSize
    加密体：19 × u32 头 | flags[flagCount] | fileBits | nameHash2Bits

19 个 u32 头的顺序按源码中的 `BET_*_I` 常量。
`flags` 数组在磁盘上是**小端**。
文件位表按位打包，每条记录依次是 文件位置 / 解压大小 / 压缩大小 / 标志索引。

## 6. (attributes)

    u32 version = 100
    u32 flags
    crc32[blockCount]                    flags & 0x01
    filetime[blockCount]                 flags & 0x02
    md5[blockCount][16]                  flags & 0x04

数组按块表索引排列，MD5 作用于解压后的内容。
约定：`(listfile)` 与 `(attributes)` 自身的 MD5 全零，`(attributes)` 自身的 CRC32 为 0。

## 7. 压缩与块布局

- 掩码：0x02 zlib，0x10 bzip2。
- 单块（SINGLE_UNIT）块的首字节是掩码；当 `cmpSize == fileSize` 时表示原样存储。
- 分扇区块以 `(扇区数+1)` 个 u32 的偏移表开头，其后是各扇区数据。
- 多兆字节组件通常用单块存储。

## 8. 写入策略

`pack` 时：

1. 组件顺序沿用源归档；
2. 每个组件按 `layout_hints` 决定单块还是分扇区；
3. 逐个尝试 bzip2 与 zlib，取较小者，压不小就原样存储；
4. 重新生成 HET、BET、哈希表、块表与 `(attributes)`；
5. 写头部，含摘要块，`dwRawChunkSize = 0`。

## 9. 组件分类与身份键

| 分类 | 判定 | 比较方式 |
| --- | --- | --- |
| xml | 名字以 .xml / .SC2Components / .SC2Layout / .SC2Style 结尾，或内容以 `<?xml` 开头 | 身份键模型 |
| galaxy | .galaxy | 文本行 |
| version | .version，或内容以 `cdes` 开头 | 构建号与时间戳字段 |
| text | .txt，或可解码为 UTF-8 且无 NUL | 文本行 |
| binary | 其余 | 大小与内容哈希 |

身份键优先级：元素的 `Index` / `Id` / `Type` / `Name` / `Link` 属性；
没有身份属性的同名兄弟元素才退回位置下标。
