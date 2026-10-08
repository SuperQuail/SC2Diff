<!-- 标题请遵循 Conventional Commits，例如 feat(mpq): 支持写入属性表 -->

## 这个 PR 做了什么


## 为什么


## 自检清单

- [ ] 目标分支正确（feat/* -> **dev**；只有 dev -> main；只有 main -> release）
- [ ] CI 全绿
- [ ] 本地跑过 `python tools/test_selftest.py`
- [ ] 如涉及封装/容器改动，跑了 `python tools/test_rebuild.py` 与 `tools/editor_accept.py`，并把结论贴在下面
- [ ] **没有提交任何游戏资源**（testdata/、reference/ 均在 .gitignore 中）
- [ ] 没有把格式化改动与功能改动混在同一个提交里

## 涉容器改动的验收结论（如有）

<!-- 例：rebuild 5/5 PASS；editor_accept 原件 LOADED / 重建件 LOADED -->

## 影响面

- [ ] 破坏性变更（需要写明迁移方式）
- [ ] 仅内部实现，无对外行为变化
