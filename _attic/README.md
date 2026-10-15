# `_attic/` 归档区

**这里的内容不进 git**（`.gitignore` 里 `_attic/*` + `!_attic/README.md`：清单入库、文件忽略）。
所有条目都**从工作目录移过来了**，不是删除历史——需要时随时可以从首次提交 `c87d6f6` 取回。

清理日期：**2026-10-15**（首次提交当天，AI 辅助开发累积产物）

---

## `backend-scripts/` — 一次性 AI 辅助脚本

| 文件 | 说明 |
| --- | --- |
| `_e2e_time.py` | **唯一保留内容的脚本**：`time` 类型指标端到端手工验证（建指标 → 试算 → 独占模板绑定 → 生成报告 → 校验 docx）。基于 `urllib` 直接打后端，不是 pytest，用前先起后端再 `python _attic/backend-scripts/_e2e_time.py`。 |

同目录下原本还有 36 个 `_*.py`（`_fix_cr*.py` / `_p3a_*.py` / `_add_tests*.py` / `_probe_*.py` 等），
**全部是 0 字节空文件**（AI 中途产物落盘失败的残留），已直接删除。核查过
`tests/`、`app/`、`Dockerfile`、`pytest.ini`、`docker-compose.yml` 对它们零引用。

> 教训：这类脚本不要留在 `backend/` 根层——`pytest` 虽不收集它们，但会污染目录、干扰 code review，
> 且一旦入库就跟着仓库分发。临时脚本统一放 `_attic/` 或直接不进 git。

## `demo/` — 静态 HTML 原型（2026-09-29）

`index.html` / `datasources.html` / `metrics.html` / `reports.html` / `templates.html` / `style.css`。
页面顶部标注 `UI Demo`，是 Vue 应用落地**之前**的手写原型，现已被真实前端完全取代，全仓零引用。
留作设计决策的历史参考（当时的信息架构与配色起点）。

## `docs-artifacts/` — 冒烟测试生成物

`preview_2026-08.docx`、`report_4_2026-08.docx`、`template_运维月报模板.docx`。
由 `docs/smoke_test*.md` 的执行过程**自动产出**，需要时按文档步骤重新生成即可。

> 移到此处还有一个原因：远端 GitHub 仓库是 **public**，这三个 docx 含真实运行数据
> （设备名、带宽等内网信息），不应随代码分发。
>
> **注意**：公开仓库的历史提交里仍然能取到它们（首次提交 `c87d6f6`）。彻底清除需要
> `git filter-repo` 重写历史 + 强推，详见 `DEPLOY.local.md`。

## `root-scratch/` — 根目录临时产物

| 文件 | 说明 |
| --- | --- |
| `rp3-*.yaml`（7 个）、`tpl.yml`、`save-confirm.yml` | playwright-cli 的无障碍树快照，某次 UI 排查的现场 |
| `rp-unit-scale.tar.gz` / `.list` | 单位换算那轮打好的上传包 |
| `rp-remote.sh` | 连服务器跑一句 SQL 的一次性脚本 |
| `smoke-test-report.md` | 早期「后端骨架 + 真实 PostgreSQL + 真实 Zabbix」验证报告（被 `docs/smoke_test_report.md` 取代） |
| `=3.9,` | 误生成垃圾文件（`pip install matplotlib>=3.9,` 少了空格，shell 重定向创建） |
| `m_empty2.json` | 误生成垃圾文件 |

后两个已在 `AGENTS.md` §3.7 记录为「可删」，本次一并清走。

---

## 取回方式

```powershell
git checkout c87d6f6 -- demo/            # 取回单个目录
git checkout c87d6f6 -- backend/_e2e_time.py
```

新克隆的仓库**不会**拿到本目录内容，这是预期行为。