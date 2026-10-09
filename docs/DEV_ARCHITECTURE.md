# 开发文档 · 代码结构

## 目录

    src/                      Rust 产品
      main.rs                 入口
      lib.rs                  模块导出
      cli.rs                  命令行（clap）
      repo.rs                 仓库引擎：对象库、索引、引用、分支、补丁包、pack
      semantic.rs             语义 diff：组件分类、身份键模型、变更描述
      mpq/                    容器层
        mod.rs                公共类型与常量
        crypto.rs             表密码、文件名哈希、Jenkins 哈希、位数组
        compress.rs           zlib / bzip2 解压与压缩
        tables.rs             HET / BET / (attributes) 的解析与生成
        archive.rs            归档读取、写入、Document
    tools/                    Python 参考实现与验收脚本
    docs/                     文档

## 模块职责

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| `mpq` | MPQ 容器的字节级读写 | 组件语义、版本控制 |
| `semantic` | 把组件解析成可比较的模型 | 存储、IO |
| `repo` | 工作树、对象库、引用、提交、补丁包、pack | 命令解析、输出格式 |
| `cli` | 参数、输出、把命令映射到 repo | 具体算法 |

依赖方向单向：`cli -> repo -> semantic -> mpq`。

## 仓库数据布局

    .sc2diff/
      config.json             容器参数（哈希表项数、块大小、BET 字段、布局提示、原 listfile）
      objects/<aa>/<sha1>     组件内容，zlib 压缩
      index.json              暂存区；同时作为 stat 缓存（记录 mtime 与大小）
      commits/<id>.json       提交：parents、message、author、time、tree
      refs/heads/<name>       分支
      refs/tags/<name>        标签
      HEAD                    "ref: refs/heads/main" 或游离提交 id

组件名用反斜杠分隔（与归档内部一致），落到磁盘时换成系统分隔符。
`(listfile)` 与 `(attributes)` 不进工作树与提交，由 `pack` 生成。

## 暂存区兼作缓存

`index.json` 的每一项记录 `(sha, mtime_ns, size)`。
扫描工作树时，文件的 mtime 与大小没变就直接复用记录的 sha，不重新读取内容；
只有变化的文件才重新计算哈希，且多个文件并行计算。
这决定了 `status` / `commit` 的复杂度是 O(文件数) 而不是 O(字节数)。

## 容器参数的作用

`config.json` 保存源归档的容器特征：

| 字段 | 用途 |
| --- | --- |
| `hash_count` | 哈希表槽位数，重打包时保持一致 |
| `block_shift` | 块大小（512 << n） |
| `unknown08` | BET 头里的该字段，保留源归档取值 |
| `attr_flags` | (attributes) 的标志位 |
| `layout_hints` | 每个组件原先是否分扇区存储 |
| `listfile` | 原 (listfile) 内容，组件集合未变时原样复用 |

## 构建与运行

    cargo build --release        # 产物 target/release/sc2diff.exe
    cargo test --release         # 单元测试
    cargo run --release -- --help

## Python 侧的角色

`tools/` 下是与 Rust 实现等价的 Python 参考实现，以及验收脚本。
它的用途是做**交叉验证**：Rust 读写自洽不代表与真实归档一致，
用 Python 实现（以及独立编译的 StormLib）读同一份文件能发现这类问题。
日常开发以 Rust 为准，Python 侧只在需要交叉验证时使用。
