# 用户指南

SC2Diff 把 .SC2Map / .SC2Mod 里的组件解包成普通文件，用 git 式的命令管理改动，
需要时再打包回编辑器可用的归档。

---

## 1. 仓库是什么

执行 `init` 后，目录下会得到一个工作树和一个隐藏的仓库目录：

    mymap/
      Objects                                  <- 组件工作树，普通文件
      Base.SC2Data/GameData/UnitData.xml
      MapScript.galaxy
      ...
      .sc2diff/                                <- 仓库数据（对象库、索引、提交、引用、分支）

工作树里的文件就是地图的组件。用任何文本编辑器、脚本或工具改它们都可以。

**两个簿记文件不进入工作树**：`(listfile)` 与 `(attributes)` 是归档内部的清单与校验表，
打包时按当前内容重新生成。

---

## 2. 建仓

    sc2diff -C mymap init Levels/Aiur.SC2Map

解包所有组件并暂存，随后可以直接提交：

    sc2diff -C mymap commit -m "import"

也可以建空仓库，稍后用 `apply` 从补丁包导入：

    sc2diff -C mymap init

---

## 3. 日常改动

    sc2diff -C mymap status          # 哪些组件被改了
    sc2diff -C mymap diff            # 具体改了什么
    sc2diff -C mymap add -A          # 暂存全部改动
    sc2diff -C mymap commit -m "说明"

`commit -a -m ...` 等于先 `add -A` 再提交。

### 状态的三段

    $ sc2diff -C mymap status
    On branch main

    Changes to be committed:          <- 已暂存，下次 commit 会记录
            modified: Objects

    Changes not staged for commit:    <- 改了但没暂存
            modified: MapScript.galaxy

    Untracked files:                  <- 仓库里还没有的新文件
            NewComponent.xml

短格式：

    $ sc2diff -C mymap status -s
    M  Objects        已暂存（新增 A / 修改 M / 删除 D）
     M MapScript.galaxy   未暂存
    ?? NewComponent.xml   未跟踪

---

## 4. diff 是语义的

`diff` 不比较文本行，而是把组件解析成按**身份键**索引的模型再比较：

| 组件 | 身份键 |
| --- | --- |
| Objects | 物件 Id（ObjectUnit / ObjectDoodad） |
| Triggers | 触发元素 Id |
| Base.SC2Data/GameData/*.xml | 目录条目（标签 + id） |
| LocalizedData/*.txt | 键名 |
| *.galaxy | 文本行 |
| 二进制组件 | 只报大小与内容哈希 |

所以即使编辑器把整份 XML 重写过（缩进与元素顺序全变），diff 仍然只报真正变化的部分：

    $ sc2diff -C mymap diff
    diff working tree vs index
      Objects                    xml(PlacedObjects): ~1 entities

    ### Objects
      ~ ObjectUnit[Id=2]
          /@UnitType: Medivac -> Viking

    1 component(s) changed

### 几种比较对象

| 命令 | 比较 |
| --- | --- |
| `diff` | 工作树 与 暂存区 |
| `diff --cached` | 暂存区 与 最近一次提交 |
| `diff <rev>` | 提交 与 工作树 |
| `diff <rev1> <rev2>` | 两个提交 |

---

## 5. 导出给银河编辑器

    sc2diff -C mymap pack out.SC2Map

把当前工作树打包成归档，可以直接用编辑器打开。

打包沿用原归档的容器参数（哈希表大小、块大小、每个组件的分块方式）；
未改动的组件原样搬运，改动的组件重新压缩。

打开之前可以先自查：

    $ sc2diff verify out.SC2Map
    archive    out.SC2Map
      blocks   12  hash entries 16
      header digest true  block table true  hash table true  HET true  BET true
      dwRawChunkSize 0 (must be 0 for the editor to accept edits)
      names    10
      RESULT   OK

`RESULT OK` 表示容器自洽，且满足编辑器接受改动内容的条件。

---

## 6. 分支与回滚

    sc2diff -C mymap branch feature        # 新建分支
    sc2diff -C mymap switch feature        # 切换
    sc2diff -C mymap switch -c hotfix      # 新建并切换
    sc2diff -C mymap checkout main         # 切回去
    sc2diff -C mymap checkout <sha>        # 检出某个提交（detached）

切换分支会还原工作树。有未提交改动时会拒绝，用 `-f` 强制。

丢弃改动：

    sc2diff -C mymap restore Objects           # 用已提交内容覆盖某个组件
    sc2diff -C mymap restore --staged Objects  # 只取消暂存

标签用来标记可发布的节点：

    sc2diff -C mymap tag v1        # 打标签
    sc2diff -C mymap tag           # 列出
    sc2diff -C mymap tag -d v1     # 删除

---

## 7. 与别人交换改动：补丁包

不需要服务器。`bundle` 生成一个文件，`apply` 把它导进另一个仓库。

### 全量

    sc2diff -C mymap bundle full.sc2bundle        # 作者
    sc2diff -C theirs init                        # 接收方
    sc2diff -C theirs apply full.sc2bundle

接收方得到完整历史与工作树。

### 增量

`--basis` 声明对方已经有的节点，只有对方缺的组件进入包里：

    sc2diff -C mymap bundle patch.sc2bundle --basis v1
    sc2diff -C theirs apply patch.sc2bundle

包是普通 zip，走共享目录、网盘、聊天软件都可以。对方缺少前置提交时 `apply` 会明确报错，
不会写出半个仓库。

`apply` 之后：

| 对方的分支状态 | 结果 |
| --- | --- |
| 分支不存在 | 创建并检出 |
| 是导入提交的祖先 | 快进并检出 |
| 已分叉 | 只导入历史，需要人工取舍 |

---

## 8. 当前限制

- **没有合并**：两条分支改了同一处时，只能人工取舍。
- **没有远端仓库**：不提供 `push` / `pull` / `clone`，改用补丁包交换。
- 二进制组件只报「已变化 + 哈希」，没有字段级 diff。
- 仓库按整份组件存储，没有增量压缩。
