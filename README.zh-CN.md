# Kaggle Agent

**人机协作在 Kaggle 取得竞赛研究成果的环境。**

[English](README.md) · 中文

**29 个工具 · 17 个技能 · 2 个可选依赖（仅绘图）。**

---

## 你得到什么

| | |
|---|---|
| **工具** | 29 个 —— 配额、kernel、比赛、账号、RSI 实验树、证据库、绘图、在场状态 |
| **技能** | 17 个 —— 分为身份、调研、实验、协作四层 |
| **子代理** | 不附带任何人格定义。调研流程只在那唯一一级派发四个子代理（它们的成员彼此独立），其余每一级都在主线程里跑 |
| **运行时依赖** | 无。服务器只用 Python 标准库。`numpy` 与 `matplotlib` 仅绘图需要，且绘图技能会先检测、征得同意后才安装。Kaggle CLI 本身在进程内探测一次；缺失时走同一条安装路径，且**不经你同意绝不安装**。 |

---

## 架构

### 一次调用如何到达工具

![一次调用如何到达工具](docs/architecture-launch-path.svg)

宿主启动一个 stdio 进程，在它的 stdout 上讲 JSON-RPC。它不展开 `${PLUGIN_ROOT}`，也不保证工作目录，
所以 manifest 里带了一行内联 bootstrap，在运行时定位插件包 —— 而且两种安装形状必须长得一样：
本地安装和插件市场缓存。

| | |
|---|---|
| `mcp/agent_server.py` | 自定位入口。本地安装是 `<root>/kaggle-agent`；市场安装缓存在内容哈希之下，更深一层。两者都不按目录名匹配，因为哈希目录不是名字。 |
| `mcp/kaggle_server.py` | 29 个工具的协议与分发。每个 Kaggle 工具先解析凭据，再取 CLI 命令 —— 该值按进程缓存，所以探测一次而不是每次调用都探。 |
| `mcp/credentials.py` | 多账号存储。`token_for()` 只读不写，这正是每次调用传 `account=` 安全的原因。 |
| `mcp/experiment_tree.py` | RSI 实验树。 |
| `mcp/deps.py` | 这台机器能做什么，以及唯一可能安装东西的那条路径。 |

另外十个模块覆盖证据、交接、GitHub 同步、日志监控、绘图、在场状态、搜索选择和结构化输入。

### 四个类别

![十七个技能，四个层级](docs/architecture-skill-layers.svg)

类别是你读的一个分组；边是检查器强制的一条依赖。完整的图在
[`skills/relationships.json`](skills/relationships.json)，渲染结果在
[`skills/README.md`](skills/README.md)。

| 类别 | 它回答的问题 | 技能 |
|---|---|---|
| **身份** | 我在以谁的身份操作，哪个配额在付费？ | `kaggle-cli`、`kaggle-account-switch`、`account-rename-visualizer` |
| **调研** | 这是什么比赛，我该构建什么？ | `kaggle-competition-research`、`approach-decision` |
| **实验** | 如何运行、监控、学习？ | `experiment-launch`、`log-monitor`、`log-monitor-visualizer`、`rsi-experiment-tree`、`scientific-plotting`、`ablation-design` |
| **协作** | 工作如何延续到下个会话，何时该问？ | `handoff`、`github-auth`、`presence-mode`、`evidence-sources`、`genui-scenarios`、`technical-report` |

### 可以这样问

- 「调研一下 ARC Prize 2026 这个比赛。」
- 「我还剩多少 GPU 配额？」
- 「这个我自己写，还是 fork 最好的公开 notebook？」
- 「把我的消融实验记到一棵树上。」
- 「盯着这个 run 的日志，出错时告诉我。」
- 「写一份 handoff，让另一个 agent 能接手。」

---

## 安装

**从插件市场** —— 在插件面板里搜索 *Kaggle Agent* 并安装。用 CLI 就是
`mcode plugin add kaggle-agent@official`。

**从仓库** —— 插件面板也可以直接从 GitHub 仓库添加插件，指向
`https://github.com/Timothy-kira/kaggle-agent`。

两条路都只是注册这个包，都不构建它。

### 首次调用之前

| 前置条件 | 为什么 |
|---|---|
| Python 能以 **`python`** 这个名字被调用 | `servers.mcp.json` 用 `python` 启动服务器，这个名字必须能解析。`python --version` 就是检查。 |
| Kaggle CLI | 工具要调用它。如果缺失，`kaggle_sources action="doctor"` 会说出来，并在你同意后提供安装。 |

**在 macOS 和 Linux 上**，只带 `python3` 的机器没有 `python`，症状是插件装得很顺、然后**一个工具都没有**。
工具列表为空意味着没找到解释器，不是插件没注册成功。一行命令解决：

```bash
mkdir -p ~/.local/bin && ln -sf "$(command -v python3)" ~/.local/bin/python
```

去改已安装的 manifest 没用，下次更新会被覆盖。如果服务器还是起不来，
`python3 -B mcp/agent_server.py` 可以手动启动它并把错误打出来。

**本地副本从插件面板里消失了。** MiniMax Code 3.1.0 会拒绝任何含硬链接的本地插件目录（`.git`
也算），而且不写日志。从本地路径 `git clone` 默认会把 `.git/objects` 做成硬链接，所以往
`~/.minimax/plugins` 里克隆工作副本时要加 `--no-hardlinks`，或者直接从 GitHub URL 克隆。
`python tools/check_plugin.py` 会报出它找到的硬链接。

`bin/` 下的两个入口都是带 shebang、没有执行位的 Python 文件，通过解释器调用 ——
`python3 bin/kaggle-cli.sh`、`python3 bin/run-mcp.sh` —— 所以同一条命令在所有平台都能用，
打包后的文件模式位也不需要在检出后幸存。

### 凭据

仓库里没有提交任何 token、密钥或账号名，而且每次验证运行都会扫描。凭据从你的环境或
`~/.kaggle-agent/accounts.json` 解析；你在对话里粘贴的 token 会被存到你自己的存储里 ——
绝不会写进 skill、manifest、日志、图表或 handoff。旧位置（`~/.kaggle-cli/accounts.json`）
的存储在首次读取时会被复制过来，并原地保留。

---

## RSI for Science

![RSI for Science：实验树](docs/architecture-rsi-tree.svg)

`kaggle_experiment_tree` 是这个包的核心：一个读门控的、经验证的有向无环图（DAG），**同时**也是
回放模拟器。这里的 *RSI* 指科研领域的递归自我改进 —— 闭环才是重点，而它要成为闭环，
前提是记忆能活过会话。

- **门控是真的。** `action="read"` 返回一个 revision；`record` 拒绝过期的或缺失的。
  说「先检查当前状态」的散文会被跳过，revision 号不会。
- **节点在被回退时必须声明失败层**，于是「这没效果」变成一句关于**在哪里**断掉的断言。
- **回放给你自己的历史打分**，在候选探索策略下评估，而 `compare` 永远包含你正在跑的策略 ——
  所以推荐不可能比现状更差。回放是估计，不是测量；它说该试什么，不说什么会赢。
- **父节点选择是非贪心的**（`score + progress + novelty`，带访问冷却），所以一个局部诱人的分支
  饿不死全局搜索。
- **按标准计有效成本。** 一个标准上的增益悄悄吃掉另外两个标准时，这本身是可见的 ——
  因为四个成本标志按标准保留，而不是压平成一个布尔值。
- **溯源是强制的。** 节点需要链接来源，或显式声明 `evidence:"local-only"`。
  没有证据支撑的断言必须大声说出来。
- **`action="audit-report"` 会拿完成的报告对账。** 当 `mayNotClaim` 的句子被抄进来、
  或树声称了一个磁盘上并不存在的产物时，它机械地拒绝。它**只驱动、绝不宣告无罪**：
  树持有的图表证明证据存在，从不证明它支持那句话。

---

## 工具清单

**账号与配额** —— `kaggle_accounts`（添加 / 切换 / 重命名 / 列出）· `kaggle_auth_status` ·
`kaggle_config_view` · `kaggle_quota` · `kaggle_accelerators`

**Kernels** —— `kaggle_kernel_launch`（12 小时封顶）· `kaggle_kernel_verify` · `kaggle_kernel_push` ·
`kaggle_kernels_list` · `kaggle_kernels_status` · `kaggle_kernels_logs` · `kaggle_kernels_output` ·
`kaggle_kernel_pull` · `kaggle_kernel_retire`

**比赛** —— `kaggle_competitions_list` · `kaggle_competitions_forums` ·
`kaggle_competitions_leaderboard`

**科学循环** —— `kaggle_experiment_tree` · `kaggle_sources` · `kaggle_log_monitor`

**协作与判断** —— `handoff_read` · `handoff_write` · `handoff_status` ·
`handoff_sync` · `github_auth` · `kaggle_presence` · `kaggle_search_engine`

**代码拦住什么，又把什么交给宿主。** 有三个调用会作用到本机以外或改动本机，每个都要在调用里
表明用户已同意才会执行：`kaggle_kernel_retire` 只有 `confirm` 等于 `ref` 时才删除，
`kaggle_sources action="install"` 只有 `confirm=true` 时才安装，`handoff_sync` 只有
`confirm=true` 时才推送。缺少确认时，它们只报告将要做什么。`kaggle_local_launch` 在声明了实验
节点之后会启动一条本地命令，而这个声明 agent 自己就能做，所以它不是安全边界：本地命令能不能跑，
由宿主的工具审批决定，也应该在那里决定。

---

## 验证

```bash
python tools/check_plugin.py
```

约 1500 条断言，覆盖 manifest、技能图及其渲染索引、树的机制、证据链、绘图引擎后端、
凭据扫描，以及在链路上对 MCP 服务器的真实驱动。它是 skill 里的说法成为断言而非意图的原因：
只写在散文里的关系会烂，写进图里并被检查的关系不会。

skill 的 `assets/`、`references/`、`scripts/` 下的每个文件都来自别人，全部在每次运行时接受
提示注入模式扫描 —— 没有白名单，所以以后新加的 vendored 文件也自动覆盖。一次干净的扫描是
下限而不是结论，报告会这么说，而不是暗示内容已被清除。

七个探针驱动真实运行而不是读代码：传输层、消融循环、绘图循环、声明审计、监控循环、账号作用域，
以及市场布局 —— 最后一个用插件包自己的 bootstrap 对着一个市场形状的目录树启动，并要求服务器
回答 `initialize`。每个探针都被证明过会失败：五种不同的破坏能让布局探针变红，
而传输探针在某个用例完全拿不到回复时会失败。

这些检查里包含这个包真实踩过的坑。`check_publishable` 断言没有 manifest 路径被 `.gitignore`
排除、每条 gitignore 目录规则都带锚点、且没有可发布文件含凭据 —— 因为曾有一个文件在磁盘上、
在 manifest 里声明、在图里链接，而仓库里却缺了它。

---

## 仓库结构

```
.minimax-plugin/plugin.json   manifest：17 个技能，一个 MCP 服务器，无应用
servers.mcp.json              stdio 服务器，内联 bootstrap，与工作目录无关
mcp/                          服务器与它的十五个模块
skills/                       17 个技能，按 manifest 要求平铺
  categories/                 分层关系，以可读页面呈现
  _shared/                    GenUI 基座，fork 一次
  relationships.json          技能如何相连的唯一事实来源
docs/                         架构图，由 tools/draw_architecture.py 生成
tools/                        强制层
```

重新生成：`python tools/draw_architecture.py`。

---

## 出处

八份正文取自 MIT 许可的
[K-Dense-AI/scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)
项目（原名 `K-Dense-AI/claude-scientific-skills`），pin 在提交
`065b734670d7d990627dbc06a05b5a99be33f1f1`；其中七份整份 vendor，各自放在会读它的宿主 skill 下，
并附一份上游许可原文：

| Vendor 的正文 | 读它的 skill |
|---|---|
| `hypothesis-generation`、`scientific-critical-thinking` | `ruler-audit` —— 声明与裁定复核 |
| `seaborn`、`scientific-visualization` | `scientific-plotting` —— 出图阶段 |
| `scientific-writing`、`scientific-slides` | `technical-report` —— 报告与汇报 |
| `scientific-brainstorming` | `kaggle-competition-research` —— 调研之后 |

`experimental-design` 取过又去掉了：它是一份实验室方案书，能迁移到比赛的两点——block 干扰因素、
什么才算真正的独立重复——`ablation-design` 和 `ruler-audit` 已经在承担；带上它的脚本等于为一张
本包自己能列的实验臂矩阵引入 `pyDOE3` 依赖。

正文一律不改，这是有意的：每个宿主 skill 都用自己的文字写明取了什么、拒了什么、为什么。重新
vendor 用 `python tools/fetch_kdense_bodies.py <commit>`。

`ablation-design` 另外改编了实验设计与不确定性材料。取的是方法，没有取那些依赖繁重的脚本。原始
MIT 许可和上游提交都记录在 skill 里。

回放与非贪心选择的设计来自 *Dream-RSI*（arXiv 2609.14858），从线性子链改编为多子节点 DAG；
按标准计成本核算来自 *Effective Feedback Compute*（arXiv 2605.29682）。两者都在 skill 和
`mcp/experiment_tree.py` 中被引用，并且改编不是逐字移植的地方都在注释里点明。

## 许可

MIT。
