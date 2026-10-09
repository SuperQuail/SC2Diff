# 开发文档 · 分支与发版

## 1. 分支模型

| 分支 | 用途 | 接受来自 |
| --- | --- | --- |
| `dev` | 日常开发 | `feat/*` `fix/*` `chore/*` `docs/*` `refactor/*` `test/*` `ci/*` |
| `main` | 稳定 | 只接受 `dev` |
| `release` | 发版，tag 从这里打 | 只接受 `main` |

流向固定：`feat/* -> dev -> main -> release -> tag`，不跳级。

## 2. PR 要求

- 一律走 PR，`main` 与 `release` 已启用分支保护，直接推送会被拒绝；
- 目标分支必须符合上面的流向，`branch-policy` 工作流会校验；
- PR 标题遵循 Conventional Commits：`<type>(<scope>): <subject>`，
  type 取 feat / fix / chore / refactor / docs / test / build / ci / perf；
- 合并前 `tests (windows)`、`rust (windows)`、`branch flow` 三项检查必须通过。

## 3. 版本

版本号写在仓库根目录的 `VERSION` 文件，tag 为 `v` + 该版本号。
`.github/workflows/release.yml` 会校验两者一致，不一致则发版失败。

形如 `0.1.0a1`、`0.2.0b3`、`1.0.0-rc1` 的版本会被标记为预发布（pre-release）。

## 4. 发版步骤

1. 在 `dev` 上更新 `VERSION`，并新增 `docs/release-notes/v<版本>.md`（作为 Release 正文）；
2. 提交，开 PR 并入 `dev`；
3. 开 `dev -> main` 的 PR 并合并；
4. 开 `main -> release` 的 PR 并合并；
5. 在 `release` 上打 tag 并推送：

    git checkout release && git pull
    git tag v0.1.0a2
    git push origin v0.1.0a2

推送 tag 会触发 `release` 工作流：校验版本 -> 跑测试 -> 构建 Rust CLI -> 打包 -> 创建 Release。

## 5. Release 内容

| 附件 | 内容 |
| --- | --- |
| `sc2diff.exe` | 编译好的 Windows x64 CLI |
| `sc2diff-v<版本>.zip` | 源码、文档与同一个 exe |

## 6. 分支保护设置

`main` 与 `release`：

    required_status_checks          strict, [tests (windows), rust (windows), branch flow]
    required_pull_request_reviews   0 个必需审批
    enforce_admins                  true
    allow_force_pushes / deletions  false
    required_conversation_resolution true

审批数设为 0 是为了单人维护时不被锁死；「必须走 PR」与「必须过 CI」仍然强制。
`dev` 不设保护。
