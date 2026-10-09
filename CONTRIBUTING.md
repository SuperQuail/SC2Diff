# 贡献指南

先读开发文档：

- [docs/DEV_ARCHITECTURE.md](docs/DEV_ARCHITECTURE.md) 代码结构与仓库布局
- [docs/DEV_FORMAT.md](docs/DEV_FORMAT.md) 容器格式实现要点
- [docs/DEV_TESTING.md](docs/DEV_TESTING.md) 测试与验收
- [docs/DEV_RELEASE.md](docs/DEV_RELEASE.md) 分支模型与发版流程

## 提交流程

1. 从最新 `dev` 切出工作分支：`feat/` `fix/` `chore/` `docs/` `refactor/` `test/` `ci/`；
2. 提交信息遵循 Conventional Commits，例如 `feat(semantic): ...`；
3. 向 `dev` 开 PR；
4. CI 全绿后合并。

## 本地自检

    cargo test --release
    python tools/test_selftest.py

改动涉及容器读写时，额外跑：

    python tools/test_rebuild.py          # 有真实样本时
    python tools/editor_accept.py <doc.SC2Map>   # 有编辑器时

## 不要提交游戏资源

`testdata/` 与 `reference/` 已在 `.gitignore` 中。
需要测试数据时用 `tools/fixture.py` 生成合成文档，CI 也是这么做的。

## 修改容器写入相关代码时

[docs/DEV_FORMAT.md](docs/DEV_FORMAT.md) 第 2 节列出的两个头部字段是编辑器接受改动的前提，
改动这些代码后必须跑一次编辑器验收并在 PR 里贴出结论。
