# 开发文档 · 测试与验收

## 1. Rust 单元测试

    cargo test --release

覆盖：

| 测试 | 内容 |
| --- | --- |
| `mpq::archive::round_trips_byte_exactly` | 生成归档 -> 读取 -> 重新打包，所有组件逐字节一致 |
| `mpq::archive::digest_block_and_raw_chunk_size` | 摘要块自校验通过；`dwRawChunkSize` 为 0；HET/BET 重序列化与磁盘字节一致 |
| `semantic::*` | 身份键 diff：单字段改动只报一个实体；顺序变化不算改动；插入实体不影响既有实体 |
| `repo::repository_round_trip_and_pack` | 建仓 -> 提交 -> 检出 -> 打包，组件与源文档一致 |

## 2. 交叉验收（tools/，Python）

Rust 实现自洽不能证明与真实归档一致，下列脚本用独立实现读同一份文件做交叉检查。

| 脚本 | 用途 | 命令 |
| --- | --- | --- |
| `fixture.py` | 生成合成文档，供无样本环境使用 | `python -c "import sys;sys.path.insert(0,'tools');import fixture;fixture.build('testdata/fixture.SC2Map')"` |
| `test_selftest.py` | 全链路自检：容器往返、建仓、语义 diff、补丁包 | `python tools/test_selftest.py` |
| `test_git_parity.py` | CLI 行为验收（28 项断言） | `python tools/test_git_parity.py --source testdata/fixture.SC2Map` |
| `test_bundle.py` | 补丁包交换与瘦补丁 | `python tools/test_bundle.py --source testdata/fixture.SC2Map` |
| `verify_roundtrip.py` | 真实样本上校验 HET/BET 逐字节重生成与 attributes | `python tools/verify_roundtrip.py` |
| `test_rebuild.py` | 真实样本上的解包 -> 重打包 -> 回读保真 | `python tools/test_rebuild.py` |
| `compare_stormlib.py` | 与 StormLib 抽取结果逐字节比对 | 配合 `stormlib_verify.exe` |
| `bench_repo.py` | 仓库操作性能基准 | `python tools/bench_repo.py` |

没有真实样本时 `verify_roundtrip.py` 与 `test_rebuild.py` 输出 SKIP 并以 0 退出。

## 3. 独立实现交叉验证（StormLib）

`tools/stormlib_verify.cpp` 只用 StormLib 的公开 API 打开归档、枚举并抽取全部成员，
用于确认产物对第三方读取器同样有效。构建方式见 `tools/build_verify.bat`。

## 4. 编辑器验收

`tools/editor_accept.py` 启动银河编辑器打开指定文档并给出判定：

    python tools/editor_accept.py <doc.SC2Map>

判定不看截图，依据：

- 模块窗口标题出现文档名 -> LOADED
- 进程消失或 EditorLogs 新增崩溃目录 -> CRASH
- 超时且无标题匹配 -> REJECTED

脚本要点：

- 用无参数方式启动编辑器，再驱动「文件/打开」；
- 把「最近目录」注册表项指向目标文件所在目录，对话框才会列出它；
- 文档依赖的 mod 未安装时会出现「无法加载依赖项数据」提示，这属于内容级提示，
  需要答「继续」而不是当作失败，判据应同时参考原件的行为。

该脚本需要本机安装星际争霸 II 编辑器，因此不进 CI。

## 5. CI

`.github/workflows/ci.yml` 在两个 job 上运行：

| job | 内容 |
| --- | --- |
| `tests (windows)` | `test_selftest.py`、`test_git_parity.py`、`test_bundle.py`，以及样本缺失时会跳过的容器脚本 |
| `rust (windows)` | `cargo build --release`、`cargo test --release`，并用编译出的 CLI 跑一次 建仓 -> 提交 -> 打包 -> verify |

两个 job 使用合成样本，不需要游戏安装，也不依赖任何游戏资源。
