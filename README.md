# SC2Diff — 星际争霸 II 地图/依赖的 diff 与版本控制

[![ci](https://github.com/SuperQuail/SC2Diff/actions/workflows/ci.yml/badge.svg)](https://github.com/SuperQuail/SC2Diff/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

对 .SC2Map / .SC2Mod（本质是 MPQ 归档）做**语义 diff** 与 **git 式版本控制**，
并把结果**重新封装**回可被银河编辑器正常打开的归档。

状态：**本地闭环已完成并通过验收**；协作（合并 / 远端）见 [docs/collaboration-plan.md](docs/collaboration-plan.md)。

> **分工**：`src/` 是 Rust 产品（编译出单文件 `sc2diff.exe`），`tools/` 是 Python 技术验证与判据
> （差分测试的 oracle）。Python 侧逐字节验证出格式事实，Rust 侧落地实现。
> 构建：`cargo build --release`，测试：`cargo test --release`。

> 分支约定：**feat/* -> dev -> main -> release -> tag**，任何改动都走 PR。详见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 1. 命令（与 git 同名同形，降低 agent 使用成本）

    sc2diff [-C <repo>] <command> [<args>]

| 命令 | 对应 git | 说明 |
| --- | --- | --- |
| init [<doc.SC2Map>] | git init | 建仓；给文档则解包并 add -A |
| add [-A] [<name>...] | git add | 暂存组件；-A 含删除 |
| rm [--cached] <name> | git rm | 删除并从暂存区移除 |
| status [-s] | git status | 三段式：已暂存 / 未暂存 / 未跟踪；-s 短格式 |
| diff [--cached] [<rev>] [<rev2>] | git diff | **语义 diff**；--cached 比较暂存区与 HEAD |
| commit -m <msg> [-a] [--amend] | git commit | 提交暂存区；-a 先暂存全部改动 |
| log [--oneline] [-n N] [<rev>] | git log | |
| show [<rev>] | git show | 提交信息 + 语义 diff |
| branch [-a] [-d/-D] [-m] [<name>] | git branch | 列表 / 新建 / 删除 / 重命名 |
| checkout [-b] [-f] <branch 或 rev> | git checkout | 分支或 detached |
| switch [-c] <branch> | git switch | |
| tag [-d] <name> [<rev>] | git tag | |
| restore [--staged] <name>... | git restore | 取消暂存 / 丢弃改动 |
| ls-files | git ls-files | |
| bundle <out> [--basis <rev>] | git bundle | **补丁包**：一个 zip，装提交与对象；带 --basis 只发改动 |
| apply <file> | — | 应用补丁包（自动检出 / fast-forward） |
| unpack <doc> | — | 导出为组件工作树 |
| pack <out.SC2Map> | — | 重新封装成可被编辑器打开的归档 |

版本解析支持 HEAD、HEAD~n、分支、标签、完整或缩写 sha。

### 例：一条完整的改动链路

    sc2diff -C myrepo init campaign.SC2Map
    sc2diff -C myrepo commit -m "import"
    # 用任何工具改工作树里的组件（都是普通文件）
    sc2diff -C myrepo status
    sc2diff -C myrepo diff
    sc2diff -C myrepo add -A
    sc2diff -C myrepo commit -m "adjust unit"
    sc2diff -C myrepo branch feature && sc2diff -C myrepo switch feature
    sc2diff -C myrepo pack out.SC2Map      # 交给编辑器打开

---

## 2. 它比 git 多什么

| 能力 | 说明 |
| --- | --- |
| **语义 diff** | XML 解析为「身份键 -> 字段」模型（Objects 按 Id、Triggers 按 Id、GameData 按 标签+id、本地化 txt 按键），有序无关比较。实测：删一个物件 + 改一个字段，重写整份 XML 后只报 2 行；文本 diff 是约 200 个实体。 |
| **容器是一等公民** | pack / unpack 直接对应 .SC2Map 与 .SC2Mod；仓库记住容器布局事实（哈希表项数、块大小、BET unknown08、原 listfile、每个组件的分块方式）。 |
| **只改该改的** | 源归档里分扇区的组件仍分扇区；无改动不重排。 |
| **产物经编辑器验收** | 重新封装出的地图能被银河编辑器正常打开（A/B 实测，含原件对照）。 |

---

## 3. 性能（实测，175 MB 工作树 / 299 个组件）

| 操作 | 优化前 | 优化后 | 提升 |
| --- | --- | --- | --- |
| scan（工作树哈希） | 172 ms | 20 ms | **8.6x** |
| status | 172 ms | 19 ms | **9.0x** |
| diff（干净工作树） | 171 ms | 28 ms | **6.1x** |
| init（解包 + 首提交） | 10.1 s | 5.7 s | **1.8x** |

做法：
1. **暂存区兼作 stat 缓存** —— 只有 (mtime_ns, size) 变化的文件才重新读取哈希，其余直接命中；
2. **多线程哈希**（hashlib 释放 GIL）；
3. **解包并行**（按 MPQ 块并行解压）；
4. **blob 原子写**（os.replace），崩溃不会留下半截对象。

复跑：python tools/bench_repo.py

---

## 4. 代码结构

    tools/
      sc2mpq.py           容器层：头 / 加密 / HET / BET / (attributes) / 压缩 / 读写
      sc2semantic.py      语义 diff：分类、身份键规范化、渲染
      sc2repo.py          仓库引擎：对象库、暂存区、引用、分支、检出、pack
      sc2diff.py          git 形状的命令行
      editor_accept.py    银河编辑器验收装置（无头判定 打开 / 拒收 / 崩溃）
      stormlib_verify.cpp 独立第三方验证（自编译 StormLib）
      verify_roundtrip.py 容器往返判据
      test_rebuild.py     解包 -> 重打包 -> 再解包 保真度
      test_git_parity.py  CLI 端到端（28 项断言）
      bench_repo.py       性能基准
    research/findings.md        全部实测结论（含根因与失败矩阵）
    docs/rust-plan.md           Rust 实装方案
    docs/collaboration-plan.md  协作功能规划

---

## 5. 怎么验证

    python tools\verify_roundtrip.py      # HET/BET 逐字节重生成 + attributes 自校验
    python tools\test_rebuild.py          # 5/5 真实归档 组件级往返保真
    python tools\test_git_parity.py       # CLI 端到端 28 项断言
    python tools\bench_repo.py            # 性能基准
    python tools\editor_accept.py <doc.SC2Map>   # 编辑器验收（会短暂启动编辑器）

---

## 6. 已验证事实摘要

| 项 | 结果 |
| --- | --- |
| SC2 归档格式 | MPQ formatVersion=3 + **208 字节头（v4 布局）** + HET/BET + 头部摘要块 |
| 头部摘要块 | 0x70 块表 / 0x80 哈希表 / 0xA0 BET / 0xB0 HET / 0xC0 头部自身；缺失或错误即被判 fake header |
| **dwRawChunkSize** | 必须写 **0**；暴雪写入器在自称 v3 的头里写了 v4 的 16384，会打开分块 MD5 校验，导致任何内容改动都被判损坏 |
| HET/BET 重生成 | **逐字节相等**（5/5 样本） |
| 组件往返 | **逐字节相同**（67 / 66 / 17 / 184 / 301 个组件） |
| 独立验证 | 自编译 StormLib：原件与自建件均 SFileOpenArchive OK，各抽取 67/67，与我们的读取器逐字节一致 |
| 编辑器验收 | 原件 LOADED · 从零重建件 LOADED · CLI/VCS 产物 LOADED |
| 重打包体积 | 1108308 -> 1100877 等（不超过原版） |

详细证据见 [research/findings.md](research/findings.md)。