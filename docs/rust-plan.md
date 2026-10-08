# SC2Diff — Rust 实装方案

> 前提：可行性研究与算法验证已经在 Python 侧完成，判据是**逐字节可复现**
> （见 research/findings.md）。Rust 侧是**移植**，不是重新研究。

---

## 1. 为什么最终交付 Rust 版本

| 维度 | Python 原型 | Rust 交付 |
| --- | --- | --- |
| 分发 | 需要解释器/虚拟环境；玩家机器上不可接受 | 单个 exe，无运行时依赖 |
| 44MB 级依赖包 | 解包/重打包要数秒到数十秒 | mmap + 并行压缩，亚秒级 |
| 与现有产品集成 | 无法嵌入 | 可直接作为 crate 挂进现有 Tauri 启动器（MiYin/HSCL 已是 Rust + Tauri） |
| 内存 | bz2 整块解压峰值高 | 可控 |
| 反复试错的正确性 | **更适合**（改一行就跑） | 编译期约束强但迭代慢 |

结论：**Python 做验证、Rust 做交付**，并且保留 Python 实现作为 Rust 的差分测试 oracle。

---

## 2. Workspace 结构

    sc2diff/
      Cargo.toml                 # workspace, resolver = "3", edition = "2024"
      crates/
        sc2mpq/                  # 容器层（核心，无 GUI、无 IO 策略）
          header.rs              # v1..v4 头；SC2 的 208 字节 / formatVersion=3 混合体
          crypt.rs               # MPQ 表密码 + 文件名哈希（含尾部非 4 字节对齐处理）
          jenkins.rs             # lookup3 hashlittle2（HET/BET 的 64 位名字哈希）
          bits.rs                # LSB-first 位数组 GetBits/SetBits
          het.rs                 # 解析 / 生成 / 逐字节重序列化
          bet.rs                 # 解析 / 生成（注意 flags 数组在盘上是小端）
          attributes.rs          # version 100；CRC32 + MD5；簿记项为零的约定
          compress.rs            # 0x02 zlib / 0x10 bzip2 / 0x12 lzma / 0x20 sparse / adpcm
          archive.rs             # 读：hash/block/HET/BET/attributes 一致性校验
          writer.rs              # 布局提示（SINGLE_UNIT vs 分扇区）、按序写盘
        sc2model/                # 组件模型
          document.rs            # 组件名 -> bytes，工作树导入/导出
          names.rs               # 名字规范化（反斜杠、大小写）
        sc2semantic/             # diff 引擎
          classify.rs            # xml / text / galaxy / version / binary
          canon.rs               # 身份键扁平化（Index/Id/Type 属性优先）
          diff.rs                # 有序无关比较 + 变更描述
          render.rs              # 人类可读输出（git 风格）
        sc2repo/                 # 版本控制
          store.rs               # 内容寻址 blob（zstd）
          commit.rs              # 提交对象、父指针、refs
          worktree.rs            # 工作树扫描/检出
          pack.rs                # 工作树 -> .SC2Map/.SC2Mod
        sc2diff-cli/             # clap 命令行
      apps/
        sc2diff-gui/             # 可选：Tauri 2 命令层，挂到现有启动器

**依赖方向**：sc2mpq 不依赖任何上层；sc2semantic 只依赖数据；sc2repo 依赖三者；CLI/GUI 只做转发。

---

## 3. 依赖选型

⚠️ 以下 crate 的可用性与版本**尚未联网核实**（本次会话的库调研子任务失败）。落地前必须先核实。

| 用途 | 计划 | 备注 |
| --- | --- | --- |
| bzip2 解压/压缩 | bzip2 crate（0.5+ 默认后端为 libbz2-rs-sys，纯 Rust 移植） | SC2 主力压缩，必须有**压缩**能力 |
| zlib | flate2（miniz_oxide 后端） | 少量块使用 |
| LZMA | lzma-rs | 掩码 0x12 |
| XML | quick-xml | 只需要流式读取 + 属性访问 |
| CLI | clap（derive） | |
| 哈希 | sha1 / sha2 / crc32fast | blob 名、attributes 校验 |
| 序列化 | serde + serde_json | 提交对象、config |
| blob 压缩 | zstd | 比 zlib 更快更好 |
| 文本 diff | similar | 仅作为 fallback（语义 diff 是主路径） |
| 并行 | rayon | 大归档的多块压缩 |
| 内存映射 | memmap2 | 44MB 归档避免整读 |

**不建议**绑定 StormLib：
- 我们已经在纯实现上拿到 HET/BET **逐字节可复现**，这是比「能读」更强的保证；
- StormLib 会把 `BET.unknown08` 写成 0x10（SC2 写 7888），且其源码里的 flags BSWAP 与
  SC2 产物不符——直接绑定等于继承这些偏差；
- 引入 C 依赖会破坏「单 exe 绿色分发」这个选择 Rust 的主要理由。

（StormLib 仍可作为**交叉验证工具**：用 MPQEditor 打开我们产出的归档，属测试资产，不入交付物。）

---

## 4. 必须逐条照搬的算法细节（Python 已实测，勿发挥）

1. 头部：headerSize=208、formatVersion=3，但 HET/BET 的 size64 字段在 v4 偏移（0x5C/0x64）。
2. 哈希表/块表用 hash_string("(hash table)",3) / ("(block table)",3) 加密；读不出时按不变量回退。
3. 块密码只处理 floor(len/4) 个字，**尾部 1-3 字节原样保留**（丢掉会截断 HET）。
4. HET：12 字节明文 + 加密体；totalCount=(entryCount*4)/3；NameHash1 = 哈希高 8 位；
   空闲槽为 0x00（OrMask 保证 NameHash1 最高位为 1，不会撞空闲值）。
5. HET 查找：命中高 8 位后必须用 BET 的**完整名字哈希**复核，不匹配**继续探测**。
6. BET：19 个 u32 头 + flags[] + fileBits + nameHash2Bits；**flags 数组在盘上是小端**；
   unknown08 保留源归档的值。
7. attributes：version=100、flags=0x5；CRC32 与 MD5 均**按块表索引**；
   (listfile) 与 (attributes) 的 MD5 为全零，(attributes) 的 CRC32 为 0。
8. 压缩：优先 bzip2 level 9；cmp==fileSize 表示原样存储（即使 COMPRESS 标志为真）；
   多兆字节组件用 SINGLE_UNIT（分扇区会显著变差）；不复现 SECTOR_CRC。
9. 布局提示（layout hints）：源归档里分扇区的组件保持分扇区，其余 SINGLE_UNIT。
   ——「只改必须改的」是 VCS 重打包的默认原则。
10. 语义 diff：字段路径优先用身份属性（Index/Id/Type），无身份且同标签才退回位置下标。
11. **`dwRawChunkSize`（头部 0x6C）必须写 0。** 暴雪写入器在自称 formatVersion=3 的头里塞了
    v4 才有的 16384，这会打开 Blizzard `System_Mopaq` 的分块 MD5 校验，使**任何内容改动**都被
    判成 e_fileCorrupt（即使 (attributes) 的 CRC32/MD5 完全正确）。设 0 后编辑器正常打开。
12. 编辑器验收必须用**编辑器本体**做 A/B；StormLib 能读不等于 SC2 接受（本例差别只有一个头部字段）。

---

## 5. 测试策略（把 Python 的判据搬过来）

| 层级 | 判据 | 现状 |
| --- | --- | --- |
| 单元 | lookup3 与 StormLib 已知向量一致 | Python 侧靠 HET 逐字节复现间接验证 |
| 容器 | 解析->重序列化 HET/BET **逐字节相等** | Python 5/5 通过 |
| 归档 | 解包->重打包->再解包，**所有组件逐字节相等** | Python 5/5 通过 |
| 元数据 | MD5/CRC 自校验（簿记项例外恒定） | Python 5/5 通过 |
| 查找 | HET 路径与经典哈希表**结果一致** | Python 5/5 通过 |
| 语义 | 只改一处 -> diff 只报该处（无伪变更） | Python 端到端通过 |
| 差分 | Rust 产出与 Python 产出比对（同输入同布局） | 待实现 —— 这是移植期最有价值的护栏 |
| 端到端 | 归档可被编辑器打开且不崩溃 | tools/editor_accept.py（Windows 专属，需本机装有 SC2） |

编辑器验收必须**与原件做 A/B**：本机上这些社区地图依赖的 mod 未安装，
编辑器会对**原件同样**弹「无法加载依赖项数据 / 仍然继续？」。
判据是「原件与重打包件行为一致（同样的提示，都能载入）」，而不是「没有提示」。

---

## 6. 性能预算

| 操作 | 规模 | 目标 |
| --- | --- | --- |
| 打开 + 列组件 | 44MB 依赖 | < 100ms（只读表 + HET/BET） |
| 全量导出 | 44MB | < 3s（rayon 并行 bzip2 解压） |
| 全量重打包 | 44MB | < 10s（bz2 level 9 是主要成本） |
| status/diff | 65 组件 | < 200ms（blob 命中后只解析变化的组件） |
| 仓库体积 | 每次提交 | 只存变化组件的 blob，配合 zstd |

优化点：blob 命中用 sha1 直接短路；diff 只对变化的组件解析 XML；
大归档走 mmap；压缩并行到核数。

---

## 7. 产品形态（使用体验优先）

**命令行（开发者/CI）**

    sc2diff init <doc.SC2Map> -d repo
    sc2diff status | diff [rev] [rev2] | commit -m msg | log | checkout rev
    sc2diff pack out.SC2Map | unpack doc.SC2Map dir

**GUI（玩家/地图作者）** —— 复用现有 Tauri 启动器
- 拖入 .SC2Map/.SC2Mod 即建仓；左侧时间线（提交），右侧**语义 diff**
  （单位/触发/目录字段三栏，而不是文本天书）
- 「在编辑器中测试」一键：pack -> 唤起编辑器
- 与现有战役管理打通：仓库挂在战役库同一 data 目录下

**「有效 diff」的具体表现**

    Objects                               xml(PlacedObjects): -1 entities
    Base.SC2Data\GameData\UnitData.xml   xml(Catalog): ~1 entities

    ### Objects
      - ObjectUnit:2288
    ### Base.SC2Data\GameData\UnitData.xml
      ~ CUnit:Purifier
          BehaviorArray[index=2]/@Link: smjhd -> smjhd-changed-by-sc2diff

即：XML 整体被重写（顺序、缩进全变）也只报真正的语义变化。

---

## 8. 风险与未决

| 风险 | 影响 | 处置 |
| --- | --- | --- |
| 编辑器对自建容器的接受度 | 阻塞交付 | 已完成 A/B 验收脚本；判据与原件对照 |
| 依赖 mod 未安装导致误判 | 验收噪声 | 用原件做对照臂（已实现） |
| 二进制组件（DocumentHeader/MapInfo/t3*）无法语义化 | diff 退化为「二进制已变」 | 先给 hash + 大小 + 变化块定位；后续可解析 |
| crate 可用性/许可证未核实 | 选型返工 | 落地前先跑一次依赖调研 |
| 编辑器外部改动的可见性 | 工作流摩擦 | 实测需 关闭->打开；GUI 里做成一键 |
| 大仓库膨胀 | 磁盘 | blob 去重 + zstd + 可选 GC |

---

## 9. 移植顺序（建议）

1. sc2mpq 只读（header/crypt/hash+block/HET/BET/attributes/解压）+ 逐字节重序列化测试。
2. sc2mpq 写入 + 布局提示 + 归档往返测试。
3. sc2model + 工作树导入导出 + pack/unpack CLI。
4. sc2semantic（先 Objects/Triggers/Catalog 三个家族，其余走通用扁平化）。
5. sc2repo + CLI 完整命令。
6. 与 Python 的差分测试（同输入产出比对），再用编辑器做端到端验收。
7. Tauri GUI。