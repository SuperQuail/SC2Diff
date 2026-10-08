# 贡献指南

> 本仓库沿用 SuperQuail 组织既有的分支模型（与 SC2Miyin-Launcher 一致）。
> **任何改动都必须走 Pull Request**，不允许直接推 main / release。

---

## 1. 分支模型

| 分支 | 用途 | 接受谁的合并 |
| --- | --- | --- |
| dev | **日常开发**，所有特性与修复先合到这里 | feat/* fix/* chore/* docs/* refactor/* test/* ci/* |
| main | **稳定分支**，随时可发包 | 只接受 dev |
| release | **发版分支**，tag 从这里打 | 只接受 main |

工作分支命名：

    feat/<简短描述>      新功能
    fix/<简短描述>       缺陷修复
    chore/<简短描述>     构建、依赖、杂项
    docs/<简短描述>      文档
    refactor/<简短描述>  重构（不改行为）
    test/<简短描述>      测试
    ci/<简短描述>        工作流

流向固定：**feat/* -> dev -> main -> release -> tag**。不要跳级合并。

这条流向由 .github/workflows/branch-policy.yml 自动校验，开错目标分支的 PR 会被直接拦下。

---

## 2. 提交流程

1. 从最新 dev 切出工作分支：`git checkout dev && git pull && git checkout -b feat/xxx`
2. 提交（Conventional Commits）：`feat(mpq): ...` / `fix(repo): ...` / `docs: ...`
3. 推送并向 **dev** 开 PR，填好模板里的自检项；
4. CI 必须全绿（见第 4 节）；
5. 评审合并（建议 squash）；
6. 发版：dev -> main 的 PR，再由 main -> release 的 PR，然后在 release 上打 tag。

### 提交信息格式

    <type>(<scope>): <subject>

- type：feat | fix | chore | refactor | docs | test | build | ci | perf
- scope：mpq | semantic | repo | cli | ci | docs
- 主题行不超过 72 字符；一个提交只做一件事。

---

## 3. 不要提交的东西（重要）

本仓库**不包含任何游戏资源**。以下内容已在 .gitignore 中：

- testdata/ -- 社区战役包与解包出来的地图内容（暴雪与社区版权素材）；
- reference/ -- 第三方源码（目前是为交叉验证而克隆的 StormLib）。

需要测试数据时用 `tools/fixture.py` **自己生成**一份结构完整的合成文档，
这也是 CI 的做法（见 tools/test_selftest.py）。

---

## 4. 本地自检（提交前请全部跑通）

    python tools/test_selftest.py                          # 合成样本全链路，无需游戏
    python tools/test_git_parity.py --source testdata/fixture.SC2Map
    python tools/test_bundle.py  --source testdata/fixture.SC2Map

有真实样本时再跑（可选，判据更强）：

    python tools/verify_roundtrip.py    # HET/BET 逐字节重生成
    python tools/test_rebuild.py        # 5/5 真实归档 组件往返保真
    python tools/editor_accept.py <doc.SC2Map>   # 银河编辑器验收（需本机装 SC2）

`tools/editor_accept.py` 需要机器上装有星际争霸 II 编辑器，因此**不进 CI**，
涉及封装格式的改动请在本地跑一次并在 PR 里贴结论。

---

## 5. 硬性约束（改 MPQ 写入前必读）

以下三条都是实测得来，违反会写出编辑器打不开的归档：

1. **头部 0x6C 的 dwRawChunkSize 必须写 0。** 暴雪写入器在自称 formatVersion=3 的头里写了
   16384，那会打开 Blizzard System_Mopaq 的分块 MD5 校验，任何内容改动都被判 e_fileCorrupt。
2. **必须写 v4 摘要块（0x70..0xCF 六个 MD5）**，否则被判 fake header（ERROR_FAKE_MPQ_HEADER）。
3. **验收必须用编辑器本体做 A/B**；StormLib 能读不等于 SC2 接受（两者只差一个头部字段）。

背景与完整实测矩阵见 [research/findings.md](research/findings.md) 第 6-8 节。

---

## 6. 分支保护（已在本仓库启用）

main 与 release 已启用保护，**任何改动都必须经 PR**，且必须通过两项检查：
`tests (windows)` 与 `branch flow`（strict，即分支必须与目标分支同步后才能合并）。
同时：禁止强推、禁止删除分支、必须解决所有评论。

实际生效的设置：

    required_status_checks : strict=true, contexts=[tests (windows), branch flow]
    required_pull_request_reviews : required_approving_review_count=0
    enforce_admins : true
    allow_force_pushes : false
    allow_deletions : false

审批数设为 0 是刻意的：单人维护时不会被锁死，但**走 PR + 过 CI** 这两条依然强制。
团队变大后把它改成 1 即可。

复现/调整：

    gh api -X PUT repos/SuperQuail/SC2Diff/branches/main/protection --input protection.json

其中 protection.json 就是上面那份设置。

**dev 不设保护**，允许直接推送以保持迭代速度；但按约定仍建议走 PR。

实测这两道闸门确实生效：

- 直接 `git push origin <branch>:main` -> 被拒：`GH006: Protected branch update failed` /
  `Changes must be made through a pull request.`
- PR 标题不符合 Conventional Commits -> `branch flow` 检查 2 秒内失败。
