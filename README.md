# SC2Diff

星际争霸 II 地图与依赖（.SC2Map / .SC2Mod）的**语义 diff** 与 **git 式版本控制**，
并能把结果重新打包成银河编辑器可以正常打开的归档。

Rust 实现，单文件 CLI，无运行时依赖。

---

## 安装

下载编译好的 CLI：

    https://github.com/SuperQuail/SC2Diff/releases/latest/download/sc2diff.exe

或从源码构建（需要 Rust 1.85+）：

    cargo build --release      # 产物 target/release/sc2diff.exe

---

## 快速上手

    # 1. 从打包地图建仓，自动解包成组件工作树
    sc2diff -C mymap init Levels/Aiur.SC2Map
    sc2diff -C mymap commit -m "import"

    # 2. 用任何编辑器/脚本改工作树里的组件（它们就是普通文件）
    sc2diff -C mymap status
    sc2diff -C mymap diff

    # 3. 提交，需要时导出给银河编辑器
    sc2diff -C mymap add -A
    sc2diff -C mymap commit -m "调整单位数值"
    sc2diff -C mymap pack out.SC2Map

    # 4. 把改动打成补丁包发给协作者（不需要服务器）
    sc2diff -C mymap bundle patch.sc2bundle --basis v1
    sc2diff -C theirs apply patch.sc2bundle

---

## 命令

| 命令 | 作用 |
| --- | --- |
| `init [<doc>]` | 建仓；给文档则解包并暂存全部组件 |
| `add [-A] [<name>...]` | 暂存组件 |
| `rm [--cached] <name>` | 删除组件（或只从索引移除） |
| `status [-s]` | 三段式状态：已暂存 / 未暂存 / 未跟踪 |
| `diff [--cached] [<rev1>] [<rev2>]` | 语义 diff |
| `commit -m <msg> [-a]` | 提交暂存区 |
| `log [--oneline] [-n N] [<rev>]` | 提交历史 |
| `show [<rev>]` | 某个提交及其改动 |
| `branch [-d/-D/-m] [<name>]` | 分支管理 |
| `checkout [-b] [-f] <branch 或 rev>` | 切换分支 / 检出提交 |
| `switch [-c] <branch>` | 切换分支 |
| `tag [-d] <name> [<rev>]` | 标签 |
| `restore [--staged] <name>...` | 取消暂存 / 丢弃改动 |
| `ls-files` | 列出已暂存组件 |
| `unpack <doc> [<dir>]` | 导出为组件工作树 |
| `pack <out>` | 重新打包成 .SC2Map / .SC2Mod |
| `bundle <out> [--basis <rev>]` | 生成补丁包 |
| `apply <file>` | 应用补丁包 |
| `verify <doc>` | 检查归档完整性与编辑器兼容性 |

`-C <path>` 指定仓库目录；版本号支持 `HEAD`、`HEAD~n`、分支名、标签名、完整或缩写 sha。

详见 [docs/USER_GUIDE.md](docs/USER_GUIDE.md)。

---

## 要点

- **diff 是语义的**：组件被解析成按身份键索引的模型（物件 Id、触发器 Id、目录 标签+id、
  本地化键），比较与元素顺序无关。XML 被整体重写也只报真正变化的部分。
- **打包结果可用**：`pack` 产出的归档经银河编辑器实测可以直接打开。
- **协作靠补丁包**：`bundle` / `apply` 交换改动，不依赖 Git 托管服务；
  带 `--basis` 时只发对方没有的组件。

---

## 文档

| 面向 | 文档 |
| --- | --- |
| 使用者 | [docs/USER_GUIDE.md](docs/USER_GUIDE.md) |
| 开发者 | [docs/DEV_ARCHITECTURE.md](docs/DEV_ARCHITECTURE.md) · [docs/DEV_FORMAT.md](docs/DEV_FORMAT.md) · [docs/DEV_TESTING.md](docs/DEV_TESTING.md) · [docs/DEV_RELEASE.md](docs/DEV_RELEASE.md) |
| 贡献流程 | [CONTRIBUTING.md](CONTRIBUTING.md) |

## 许可

MIT，见 [LICENSE](LICENSE)。
