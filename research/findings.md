# SC2 地图/依赖 diff 与版本控制 — 技术验证发现

日期：2026-10-08
环境：Windows，SC2 5.0.16 build 97579（网易 CN 分支），编辑器 D:\Game\BLZ\StarCraft II\Support64\SC2Editor_x64.exe
样本：真实社区战役/补丁包 5 个文档（paiur01.SC2Map 1.1MB / SCORE-Other.SC2Mod 1.8MB /
      SCORE-OtherC.SC2Mod 39KB / HTXL.SC2Mod 4.8MB / UmojanCT.SC2Mod 44MB）

本文件只记录**实测**。推断处一律标注「推断」。

---

## 1. 容器（MPQ）结论

### 1.1 SC2 写的是 MPQ format 3，208 字节头

| 字段 | 实测值 |
| --- | --- |
| magic | MPQ 0x1a |
| headerSize | **208**（不是规范的 68） |
| formatVersion | **3** |
| blockSizeShift | 5（16 KiB） |
| archiveSize / archiveSize64 | 一致 |
| HET/BET 指针 | 位于 0x3C / 0x34（v3 布局） |
| hetTableSize64 / betTableSize64 | 位于 0x5C / 0x64（**v4 偏移**） |

即 SC2 用的是「v3 语义 + 208 字节头 + v4 的 size64 字段位置」的混合体。
只按 formatVersion 选布局的读取器会读歪。

### 1.2 哈希表 / 块表用标准密钥加密

密钥 = hash_string("(hash table)", 3) / hash_string("(block table)", 3)。
不解密也能读到「看似合理」的垃圾，必须解密后按不变量校验（空闲槽、块索引越界）。

### 1.3 HET / BET 是权威索引，且必须存在

编辑器二进制内含 StormLib（可见 SFileOpenArchive / SFileCreateArchive /
SFileEnumerateArchive / SFileIsArchiveMPQ 等符号），其查找顺序是：
**有 HET 就走 HET**（SBaseFileTable.cpp: GetFileIndex_Het），HET 未命中才回退经典哈希表。
实测佐证：把 HET/BET 指针清零或使其与块表脱节，编辑器报「无法加载依赖项数据」。

**HET 条目只存名字哈希的高 8 位**，是「候选」；StormLib 会在探测循环里用
BET 里的完整 64 位名字哈希复核，不匹配**继续探测**（不是直接返回失败）。
按「命中即返回」实现会漏文件 —— 实测 HTXL 有 3 个、UmojanCT 有 1 个这样的名字。

### 1.4 两张表的结构（已逐字段验证）

HET：12 字节明文（HET 签名 / version=1 / dataSize）+ 加密体
加密体 = 8 个 u32 头 + nameHashes[totalCount] + indexBits[indexTableSize]

实测 paiur01：tableSize=199 entryCount=67 totalCount=89 nameHashBitSize=64
indexSizeTotal=7 indexSizeExtra=0 indexSize=7 indexTableSize=78
自洽校验：32 + 89 + 78 = 199；((7*89)+7)//8 = 78；totalCount = (67*4)//3 = 89。

BET：12 字节明文 + 加密体 = 19 个 u32 头 + flags[flagCount] + fileBits + nameHash2Bits。
实测 paiur01：tableSize=1115 entryCount=67 unknown08=**7888**（StormLib 写 0x10）
tableEntrySize=67（21+24+20+2+0）flagCount=2（两个块标志 0x81000200 / 0x84000200）
nameHashArraySize=469。自洽校验：76 + 2*4 + ((67*67)+7)//8 + 469 = 1115。

**BET 的 flags 数组在磁盘上是小端**（实测原始字节 0002008100020084 -> LE 得
0x81000200 / 0x84000200）。StormLib 源码里那句 BSWAP 在 SC2 产物上不成立，
照抄会得到 0x00020081 这种废值。

**unknown08 保留原值**（7888 而不是 StormLib 的 0x10），不臆造。

### 1.5 加密只作用于整 4 字节

MPQ 块密码只处理 floor(len/4) 个字，尾部 1-3 字节**原样保存**。
把尾部丢掉会截断 HET 表（paiur01 的 HET dataSize=199，199 % 4 = 3）。

### 1.6 (attributes) 是完整性元数据，不是可选装饰

版本 100，flags=0x5（CRC32 | MD5），数组**按块表索引**排列：
crc32[blockCount]（u32）+ md5[blockCount]（16 字节，作用于**解压后**内容）。

实测约定：
- (listfile) 的 MD5 是**全零**，(attributes) 的 MD5 也是全零；
- (attributes) 的 CRC32 是 **0**；
- 其余全部块两者都要正确。

实测 5 个样本：MD5 ok=182/299/64/15/65，bad 恒为 2（就是这两个簿记项）；
CRC ok=183/300/65/16/66，bad 恒为 1。

### 1.7 压缩掩码

真实文档里出现的首字节掩码：0x10(bzip2) 占绝大多数，0x02(zlib) 少量，
以及 0x63/0x3C/0xEF/0x48/0x44/0x64 等「组合掩码」——那些块的 cmp==size，
即**标志位写着 COMPRESS 但数据是原样存储**，读取器靠 cmp==size 判定不压缩。

bzip2 是主力：实测同一份内容用 bz2 level 9 得到的长度与暴雪产物**逐字节相等**
（Triggers 145291、Objects 63577、MapScript.galaxy 23046 完全一致）。

### 1.8 块布局策略

暴雪对**多兆字节组件用 SINGLE_UNIT**（Triggers 2.5MB 压成一整块 145KB），
只有少数块用分扇区（t3TextureMasks 9.4MB、DocumentHeader 18KB，带 SECTOR_CRC）。
按 16KB 分扇区压缩会显著变差（实测 Triggers 264636 vs 145291，归档 +21%）。

SECTOR_CRC 块的扇区偏移表前导表是 **(n+2) 个 dword**（实测 offsets[0] = 4*(n+1)+4）。
本实现不复现 SECTOR_CRC（它是可选的）。

---

## 2. 自研写入器：可复现性证据

归档里的旧 mpqwrite 会把归档写坏（其自身文档也记录了编辑器拒收），因此从零实现了
Python 容器层（tools/sc2mpq.py），判据是**逐字节可复现**：

| 文件 | 组件数 | 组件往返 | HET 重生成逐字节 | BET 重生成逐字节 | HET/经典查找一致 | 体积 |
| --- | --- | --- | --- | --- | --- | --- |
| paiur01.SC2Map | 67 | 全部相同 | 是 | 是 | 是 | 1108308 -> 1100877 |
| SCORE-Other.SC2Mod | 66 | 全部相同 | 是 | 是 | 是 | 1774052 -> 1766909 |
| SCORE-OtherC.SC2Mod | 17 | 全部相同 | 是 | 是 | 是 | 39276 -> 38324 |
| HTXL.SC2Mod | 184 | 全部相同 | 是 | 是 | 是 | 4818030 -> 4567575 |
| UmojanCT.SC2Mod | 301 | 全部相同 | 是 | 是 | 是 | 44036128 -> 43009253 |

「HET 重生成逐字节」= 把解析出来的模型重新序列化，与磁盘上的原始 211/1131 字节**完全相同**。
这是最强的正确性证据：模型没有信息损失。重打包后体积甚至略小于原版。

---

## 3. 组件层：文档其实是「文本为主」

以 paiur01 为例，65 个组件里 23 个是 XML、10 个是 *.version、2 个是文本，其余二进制。

可语义化的关键组件：

| 组件 | 内容 | 稳定身份 |
| --- | --- | --- |
| Objects | PlacedObjects 放置的单位/装饰物 | Id（ObjectUnit / ObjectDoodad） |
| Triggers | TriggerData 触发器图 | Id（Element / Item） |
| Base.SC2Data/GameData/*.xml | Catalog 目录数据 | 标签+id（CUnit / CAbil ...） |
| t3Terrain.xml / ComponentList / Preload.xml | XML | 通用身份键 |
| LocalizedData/*.txt | key=value | 键 |
| *.galaxy | 源码 | 行 |
| *.version | 44 字节 | 构建号 + Unix 时间戳 |

### 3.1 为什么字节 diff 不可用

编辑器（以及任何 XML 工具）会整体重写组件，**同级元素顺序不承载语义**。
实测：用 ElementTree 读-写一遍 Objects（只删 1 个物品），若按「位置」做字段路径，
会产生约 200 个实体的伪变更；改用**身份键**（Index / Id / Type 属性优先，
位置仅在同标签且无身份时才用）后，diff 精确输出：

    ### Objects
      - ObjectUnit:2288
    ### Base.SC2Data\GameData\UnitData.xml
      ~ CUnit:Purifier
          BehaviorArray[index=2]/@Link: smjhd -> smjhd-changed-by-sc2diff

这就是「有效 diff」的核心：**按身份键 + 有序无关比较**。

---

## 4. 编辑器验收（关键需求）

### 4.1 编辑器行为（实测）

- 命令行**传文档路径会让编辑器立刻退出**（rc=1）-> 必须无参启动，再走「文件/打开」。
- 编辑器是**单实例**，且偶发「启动即退出 rc=0」（重新拉起）-> 不能只信自己 spawn 的 PID，
  要按进程名认领存活的实例。
- 「打开文档」是暴雪自绘对话框：输入路径无效，**必须在树里选中文件**，
  且对话框起始目录 = 注册表 Recent Directory - Open and Save Documents。
- 编辑器会写 EditorLogs 下「<时间戳> <主机> B97579 Error」目录（.dmp + Error.txt）作为崩溃证据。
- 编辑器二进制内含 StormLib（SFileOpenArchive / SFileCreateArchive / ...）。

### 4.2 验收装置

tools/editor_accept.py：无参启动 -> 等可响应（sc2kit doctor）-> 清理模态框 ->
把注册表起始目录指向文档所在目录 -> 复用 sc2kit 的「文件/打开」驱动 -> 按标题栏
是否出现文件名判定 LOADED / REJECTED / CRASH / TIMEOUT，并统计新增崩溃产物。

判定不看截图：进程存活 + 模块窗口标题 + 编辑器自身对话框 + EditorLogs 新增崩溃目录。

---

## 5. 与既有项目的关系（避免重犯）

D:\Code\Python\银河编辑器\archive\sc2editor-kit 是对同一问题的早期探索，
其 design/03-硬约束.md 第 21-24 条记录了编辑器拒收自建归档的实验矩阵。
本工作**没有复用**其 mpqwrite.py（归档里那份会把 MPQ 写坏），但吸收了它的负面结论：
- 重建 v1 头、无 HET/BET 的归档被拒 -> 因此本实现完整生成 v3 头 + HET + BET。
- 等尺寸替换仍报 e_fileCorrupt File: Objects Amount: 65536 Read: 0
  -> 当时未知 (attributes) 的存在；本实现已定位并正确处理该元数据。
- 它已测出「编辑器锁死打开中的文档」「命令 ID 跨实例漂移」等同向结论。
---

## 6. 关键补遗：208 字节头的 v4 摘要块（本轮的突破口）

旧项目把「编辑器拒收自建归档」归因为 HET/BET；真正缺的是**头部摘要块**。

### 6.1 实测：头部 0x70..0xCF 是 6 个 MD5

用原始样本逐个反算，得到确定映射（无一猜测）：

| 偏移 | 内容 | 实测校验 |
| --- | --- | --- |
| 0x68 | hashTableSize64 | 2048 = 128*16 |
| 0x74 | blockTableSize64 | 1072 = 67*16 |
| 0x84 | hiBlockTableSize64 | 0（无 hi-block 表） |
| 0x6C | rawChunkSize | 16384 |
| 0x70 | MD5(块表字节) | 与 md5(blockTable) 逐字节相等 |
| 0x80 | MD5(哈希表字节) | 与 md5(hashTable) 逐字节相等 |
| 0x90 | MD5(hi-block 表) | 全零 |
| 0xA0 | MD5(BET 字节) | 与 md5(bet) 逐字节相等 |
| 0xB0 | MD5(HET 字节) | 与 md5(het) 逐字节相等 |
| 0xC0 | MD5(头部前 0xC0 字节) | 与 md5(header[0:0xC0]) 逐字节相等 |

### 6.2 为什么它是硬性要求

StormLib 源码 SBaseFileTable.cpp 第 702-711 行，格式版本 4 分支的原文注释：

    Verify header MD5. Header MD5 is calculated from the MPQ header since the 'MPQ\x1A'
    signature until the position of header MD5 at offset 0xC0
    Apparently, Starcraft II only accepts MPQ headers where the MPQ header hash matches
    If MD5 doesn't match, we ignore this offset. We also ignore it if there's no MD5 at all

并在不匹配时返回 `ERROR_FAKE_MPQ_HEADER`（10009）。

注意最后一句：**没有 MD5 也会被忽略**。所以「不写摘要块」与「写错摘要块」是同一个后果。
这也解释了旧项目 v1 头重建归档为何连经典表都正确却被拒。

### 6.3 独立验证：StormLib 亲自读我们的产物

用 MSVC 源码编译 StormLib（reference/StormLib，CMake + VS2022），写了一个只用 SFile* API 的
验证程序（tools/stormlib_verify.cpp：SFileOpenArchive -> SFileFindFirstFile -> SFileReadFile），
对两份归档分别枚举并全量抽取，再与我们的读取器逐字节比对：

| 归档 | SFileOpenArchive | 枚举 | 抽取 | 与我们的读取器逐字节一致 |
| --- | --- | --- | --- | --- |
| 暴雪原始 paiur01.SC2Map | OK | 67 | 67 | 是 |
| sc2mpq 从零重建 | OK | 67 | 67 | 是 |

补上摘要块之前，StormLib 对我们重建件的返回是 `SFileOpenArchive FAILED code=10009`
（ERROR_FAKE_MPQ_HEADER）—— 与编辑器的行为同源。补上之后即为 OK。

这是**独立第三方**证据：验证程序只用 StormLib 的公开 API，不共享我们的任何解析代码。
---

## 7. 编辑器验收：受控 A/B 结果（**尚未通过**）

### 7.1 实验设计

在**同一目录、同一文件名模式下、目录内只有这两个文件**做对照，排除目录内容/文件名的干扰：

    testdata/abtest/aa_original.SC2Map     1108308 字节   （暴雪原始地图的逐字节副本）
    testdata/abtest/bb_repacked.SC2Map     1100877 字节   （sc2mpq 从零重建，组件内容完全相同）

驱动方式：tools/editor_accept.py（无参启动编辑器 -> 清模态框 -> 复用 sc2kit 的「文件/打开」
-> 把「无法加载依赖项数据/仍然继续？」答「是」-> 按模块窗口标题判定）。

### 7.2 结果（可重复）

| 归档 | 判定 | 证据 |
| --- | --- | --- |
| aa_original.SC2Map | **LOADED** | 标题变为 `地形 - [D:/.../aa_original.SC2Map] - 《星际争霸II》编辑器`；进度窗口显示 加载游戏数据/创建演算体/加载已放置的对象/加载区域 |
| bb_repacked.SC2Map | **CRASH** | sc2kit 报 `no confirm button in the open dialog`；编辑器进程消失；EditorLogs 新增崩溃目录（.dmp + Error.txt） |

重复 4 次（rebuilt 目录 / 与原件同目录 / 与原件同目录改名 / 受控 abtest），**每次都是原件通过、重建件崩溃**。
崩溃现场的 EditorInfo.txt 记录 `Documents: * 无标题地图`，即崩溃发生在**文档尚未登记**之前，
当事窗口是「打开文档」对话框。

这不是「我们的读取器自说自话」：StormLib 侧完全接受（见第 6.3 节，抽取 67/67 逐字节一致），
而 SC2 编辑器不接受。**「合法 MPQ」不等于「SC2 接受的 MPQ」**，这是本项目最重要的未解问题。

### 7.3 已经排除的原因

- 头部摘要块缺失/错误 —— 已补齐并逐项校验通过（第 6 节）。补之前 StormLib 直接报 10009。
- 组件内容 —— 与原件逐字节相同（67/67）。
- HET/BET 缺失或与块表脱节 —— 两张表都能逐字节重生成，且与经典表查找一致。
- (attributes) —— CRC32/MD5 全部按块表索引正确，簿记项约定与原件一致。
- 目录内容/文件名 —— 受控实验已排除。
- 编辑器自动化本身 —— 同一套代码对原件稳定通过。

### 7.4 仍然不同的地方（下一步应当逐项二分）

1. **压缩流**：我们用 bz2 level 9 / zlib level 9；暴雪对多数块用 bz2 且长度与我们相等，
   但对少数块不同（如 t3TextureMasks 我们更小）。且暴雪对「压不动」的块**保留 COMPRESS 标志、
   靠 cmp==size 表示原样存储**，我们则清除该标志。
2. **块位置与大小**：逻辑内容相同，但压缩后长度/位置与原件不同（我们的归档更小）。
3. **HET 槽位布局**：参数相同（totalCount=89、indexSize=7、NameHashBitSize=64），
   但插入顺序不同 -> nameHashes/indexBits 的排布与原件不同。
4. **BET 的 flags 数组顺序**：按块索引首次出现顺序收集，需与原件逐项比对。
5. **分扇区块**：原件对 t3TextureMasks / DocumentHeader 使用 SECTOR_CRC（前导表是 n+2 个 dword），
   我们保留了分扇区但**去掉了 SECTOR_CRC**。这一条最可疑，值得优先试。
6. **文件整体尺寸**：1100877 vs 1108308。

### 7.5 建议的下一步实验（按性价比排序）

1. **在原件上做「零结构改动」的最小写入**：只改某个 stored 块的载荷字节（尺寸、位置、四张表一字不动），
   看编辑器是否接受。这能一次性判定「SC2 是否校验 (attributes)」，也给出 VCS 的最小可行写入路径。
2. **保留原件的压缩载荷与块布局**，只重建表与摘要（即「重排表但不重压」）。
   若通过 -> 问题在压缩层；若崩溃 -> 问题在表生成层。
3. **复现 SECTOR_CRC 布局**（n+2 前导表 + CRC 表），针对那两个块。
4. **HET 槽位保序**：按原件的 nameHashes 反推插入顺序，尝试生成**与原件逐字节相同**的 HET。
5. 用 **x64dbg/调试器** 挂上崩溃 dump，定位编辑器读的是哪个结构（Crash 目录里有 1MB minidump，
   本次未展开分析）。

### 7.6 对可行性的影响

- **读取 + diff + 版本控制**：不受影响，已完全可用（第 2、3 节）。
- **重新封装**：容器层已经做对（StormLib 认可、逐字节可复现），但**还差 SC2 编辑器这一关**。
  在解决之前，交付物只能是「解包/差异/版本控制 + 由编辑器自身保存的归档」。
- 第 7.4/7.5 的二分实验每次约 10 分钟（编辑器启动 + 加载），实现成本很低，值得继续。
### 7.7 追加变体实验（已做）

**变体 cc_singleunit.SC2Map**：全部 67 个组件一律写成 SINGLE_UNIT（不使用任何分扇区块），
从而彻底移除 SECTOR_CRC 这一类差异。

    bytes 1004146（比原件小），组件与原件的逐字节一致 67/67，
    header_md5 / het / bet / attributes 全部校验通过，
    StormLib: SFileOpenArchive OK，enumerated=67 extracted=67 failed=0

编辑器结果：**仍然 CRASH**（2026-10-08 11.08.50 新增崩溃目录）。

结论：**分扇区/SECTOR_CRC 不是原因**。剩余差异集中在
（a）压缩流与块大小、(b) HET 槽位排布、(c) BET flags 数组顺序。
下一步按 7.5 的第 2 项做：保留原件的压缩载荷与块布局，只重建表与摘要。
---

## 8. 已解决：编辑器拒收的真正原因是 dwRawChunkSize

第 7 节记录的「任何改动都被拒收」已经定位并修复。

### 8.1 定位过程（每次都是受控 A/B，同目录同文件名模式）

| 变体 | 改了什么 | 结果 |
| --- | --- | --- |
| 原件的逐字节副本 | 无 | **LOADED** |
| 只改 1 个字节内容，attributes 不更新 | 文件数据 | CRASH |
| 只改 1 个字节内容，attributes 的 CRC32+MD5 同步更新 | 文件数据 | CRASH |
| 改另一个 44 字节 sidecar，attributes 同步更新 | 文件数据 | CRASH（报错文件随之改变） |
| 把该文件的 attributes 条目**清零** | 文件数据 | CRASH |
| 翻转**表间填充区**的一个字节（无任何校验覆盖） | 非数据区 | **LOADED** |
| 内容改动 + attributes 修正 + **dwRawChunkSize 置 0** | 文件数据 | **LOADED** |

两条关键推理：
1. 填充区改动可以通过 -> 编辑器**不是**在做整归档校验；它只校验**文件数据**。
2. 报错文件随被改文件移动，且「更新 attributes」「清零 attributes」都无效
   -> 编辑器用的期望摘要**不是** (attributes) 里的 CRC32/MD5。

### 8.2 崩溃链（编辑器自带日志，逐字）

    BlizzardError.Summary  e_fileCorrupt  File: DocumentInfo.version  Amount: 44  Read: 0
    [11] System_Mopaq::mopaq_read - fail to repair offset 0 amount 44
    [9]  System_Mopaq::SectorReadHandler::ReadData - fail to repair non-streamed file
    [7]  System_Mopaq::SectorReadHandler::ReadAndDecompressData - PerformRead error
         entry: 4012 44 44 2164261376 0
    [6]  System_Mopaq::MD5VerifyData::ValidateRead - InitializeMD5Buffer fail
    [5]  System_Mopaq::MD5VerifyData::ValidateBlock - ValidateBlock fail 0 44 3 0
    [3]  ... 0 44 2 0
    [1]  ... 0 44 1 0
    [0/2/4] Blizzard::Mopaq::MpqErrorHandler::HandleMD5BlockError

即：Blizzard 自己的 MPQ 实现（System_Mopaq）有一条**按 dwRawChunkSize 分块的 MD5 校验**
路径。暴雪写入器把 v4 才有的 dwRawChunkSize=16384 塞进一个自称 formatVersion=3 的头里，
于是这条校验被打开；它对照的摘要不是 (attributes) 里的内容 MD5，
所以任何内容改动都会被判为损坏。

旁证：StormLib `SFileCreateArchive.cpp:93` 只在 v4 才写 rawChunkSize：
`CreateInfo.dwRawChunkSize = (dwMpqVersion >= MPQ_FORMAT_VERSION_4) ? 0x4000 : 0;`
也就是说 **dwRawChunkSize=0 才是 formatVersion=3 的正确取值**，暴雪这台机器上的产物是自相矛盾的字段组合。

### 8.3 修复

写入器在头部偏移 0x6C 写 **dwRawChunkSize = 0**（并对头部前 0xC0 字节重算自哈希）。
一行改动，见 tools/sc2mpq.py 的 build_document（参数 raw_chunk_size，默认 0）。

### 8.4 修复后的验收（全部实测）

| 对象 | StormLib | 银河编辑器 |
| --- | --- | --- |
| sc2mpq 从零重建的 paiur01.SC2Map | SFileOpenArchive OK，抽取 67/67 | **LOADED** |
| VCS 端到端产物（改内容->提交->pack） | — | 见下节 |

重建件仍然：全部 67 个组件与原件**逐字节相同**，HET/BET **逐字节重生成**，
attributes 全部校验通过，体积 1108308 -> 1100877。

### 8.5 经验教训

- 「合法 MPQ」（StormLib 能读）不等于「SC2 接受」；本例里两者差别只有一个头部字段。
- 只看自家读取器的往返结果会得出错误结论：必须用**编辑器本体**做验收。
- 崩溃日志里的 `System_Mopaq` 错误链是可读的，比盲目试错高效得多——
  旧项目当年正是止步于盲试。
