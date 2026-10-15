# 云端夜检（VPS）首跑诊断 — NIGHTLY_VPS_DIAGNOSIS

- 日期：2026-10-06
- 诊断对象：`/opt/facai-dev/nightly.sh`（cron 2:30）首跑结果 **921 passed / 1 failed / 25 skipped**
- 本仓库 HEAD：`5a45633`（诊断时 `git status` 干净）
- 诊断方式：**静态诊断**（原因见"方法与局限"）+ 本机全量基线复跑
- 结论速览：**最可能的 1 failed 是 `tests/test_checkpoints_and_cost.py::test_checkpoint_initializes_nested_workspace_without_committing_parent`** —— 它对仓库根执行 `git rev-parse HEAD`（`check=True`），而 tarball 部署树（`make-bundle.py` 明确排除 `.git`）不是 git 检出。25 skipped ≈ 20 条 Playwright/Chromium 测试 + 3~5 条部署树门控 skip。957→947 的收集差最可能是 VPS 部署版本略旧于 HEAD（10 条为后来新增），而非环境差异。

---

## 0. 方法与局限（如实声明）

1. **未 SSH**：SSH 口令属会话敏感信息，按任务纪律本子代理不接触。VPS 侧任何状态（nightly.sh 内容、`/opt/facai-dev` 是否有 `.git`、失败测试名、25 条 skip 的理由清单）**均未直接验证**。
2. **本机无夜检产物**：`find facai-handoff -maxdepth 2 -iname "*nightly*" -o -iname "*vps*" -o -iname "*.log"` 仅命中无关的 `autoclaw-relay-bridge/bridge.log`；`facai-cloud/` 内同样无任何 nightly/VPS 日志。首跑的失败输出（测试名、traceback）**不可得**。
3. **本机 Linux 视角模拟不可行**：宿主为 win32（10.0.26200）。作为替代，本机全量复跑取得基线（见 §1）。
4. 因此本文给的是**按证据排序的候选 + 每条的 VPS 侧一步验证命令**，不是已确认的失败名单。所有候选的验证都只需一次 SSH 窗口。

## 1. 本机基线（本次实跑，命令与输出原样引用）

```
$ python -m pytest --collect-only -q        →  957 tests collected in 0.38s
$ python -m pytest -q                        →  957 passed, 2 warnings in 122.19s
```

要点：**本机 0 skipped**（Chromium/Playwright 已装，20 条浏览器测试真跑真过）。所以 VPS 的 25 skipped 全部来自环境差异（浏览器缺失或部署树门控），本机基线无法复现它们——这正是静态诊断的切入点。

## 2. 候选 #1（主嫌疑，唯一同时满足"恰好 1 条失败"的静态证据）

**`tests/test_checkpoints_and_cost.py::test_checkpoint_initializes_nested_workspace_without_committing_parent`**

### 证据
- `tests/test_checkpoints_and_cost.py:54,94,118`：三个 checkpoint 测试的门控只有
  `@pytest.mark.skipif(not HAS_GIT, reason="git is required for checkpoint tests")`，
  其中 `HAS_GIT = shutil.which("git") is not None`（`tests/test_checkpoints_and_cost.py:15`）——**只检查 git 二进制存在，不检查被测仓库树是否 git 检出**。
- `tests/test_checkpoints_and_cost.py:99-101`：
  ```python
  repo_root = Path(__file__).resolve().parents[1]
  before = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=repo_root,
                          capture_output=True, text=True, check=True).stdout.strip()
  ```
  `:113` 结尾再跑一次同样的命令。仓库根不是 git 仓库（或当前用户无权读它）时 `git rev-parse HEAD` 退出码 128，`check=True` 抛 `CalledProcessError` → **该测试 FAIL**。
- 部署树不是 git 检出是本项目的既定部署形态：
  - `make-bundle.py:9`：`EXCLUDE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "data", "dist", "bridge"}`；
  - 实际打包产物 `facai-cloud/facai-head.tar.gz` 经 `tar -tzf | grep "\.git"` 仅含 `.gitignore`/`.gitattributes`，**无 `.git/` 目录**；
  - 同一套件里 `tests/test_deploy_config.py:296-297` 有正确示范：`if not (ROOT / '.git').exists(): pytest.skip('not a git checkout (deployed tree)')` —— 说明套件作者知道部署树无 `.git`，但 checkpoint 测试漏了同款门控。
- 该缺陷在已部署的旧包里同样存在：`tar -xzOf facai-head.tar.gz tests/test_checkpoints_and_cost.py` 的 99-113 行与当前 HEAD 一致（`repo_root` + `git rev-parse HEAD, check=True`，仅 `HAS_GIT` 门控）。
- 套件内**只有这一个测试**以 `check=True` 方式对仓库根跑 git：全 tests/ 检索 `parents[1]` + git 组合仅此一处；`test_parallel_isolation.py`、`test_ingest.py` 的 git 都作用在 `tmp_path` 自建微仓库上，不受部署树影响。

### 环境依赖机理
本机（git 检出）→ `git rev-parse HEAD` 成功 → 绿。VPS `/opt/facai-dev`（tarball 解包，无 `.git`）→ 退出码 128 → 恰好这 1 条红。其它 921 条不碰仓库根 git，全部照常绿。
变体机理（若 VPS 树实际是 git 检出）：cron 以 root 运行而树属部署用户时，git 的 `dubious ownership` 保护同样让这条命令 128 失败——**同一测试、同一修法**。

### 建议修法（最小，1 行门控 + 复用既有模式）
```python
# tests/test_checkpoints_and_cost.py 顶部（HAS_GIT 旁）
IS_SOURCE_CHECKOUT = (Path(__file__).resolve().parents[1] / '.git').exists()

# 该测试的装饰器追加一条（与 test_deploy_config.py:296 完全同构）
@pytest.mark.skipif(not IS_SOURCE_CHECKOUT, reason="not a git checkout (deployed tree)")
```
加固（可选，覆盖 dubious-ownership 变体）：把 `:100` 与 `:113` 的两次 `git rev-parse` 改 `check=False`，`returncode != 0` 时 `pytest.skip("repo_root git unavailable")`。
测试纪律说明：装饰器级门控无独立逻辑可单测，建议验收标准定为"本机 957 全绿 + VPS 次日夜检 0 failed"双端复跑；若团队要求门控本身带测试，把 `IS_SOURCE_CHECKOUT` 抽到 `tests/_env.py` 并用 monkeypatch 加 2 条单元测试（属下个修复波的实施项）。

### VPS 验证步骤（下次 SSH 窗口）
```bash
cd /opt/facai-dev
ls -d .git 2>/dev/null || echo "NO .git (候选#1前提成立)"
python3 -m pytest tests/test_checkpoints_and_cost.py -q   # 预期：恰好 nested-workspace 这条 FAIL，
                                                          # traceback 含 "fatal: not a git repository"
# 若树是 git 检出但属主不同：git -C /opt/facai-dev rev-parse HEAD 看是否报 dubious ownership
```

## 3. 候选 #2（若候选 #1 在 VPS 被证伪）：浏览器测试在 2:30 负载下的 flaky

- 依据：本机 0 skipped 说明 20 条 Chromium 测试在本机真跑；VPS 若**装了** Chromium（`deploy/install-opencode.sh` 同目录生态下不排除），这 20 条会真跑，而 `_launch()` 里的稳定性手段只有 `reduced_motion` 模拟（`tests/test_console_layout.py:17-23`），夜间与生产进程抢 CPU 时 Playwright 的 auto-wait 可能超时。任务书已声明"已知 flaky 可忽略"，故降级为次候选。
- 20 条的构成（grep `def test_.*console_server|protected_server|playwright_api` 逐文件计数）：`test_console_layout.py` 11、`test_mobile_ui.py` 3、`test_ui_theme.py` 4、`test_billing.py` 1、`test_daily_progress.py` 1。
- 验证：`python3 -m pytest -q -rs 2>&1 | tail -40`——若 25 条 skip 里没有 chromium 字样，说明浏览器在跑，重跑该失败测试确认是否 flaky。

## 4. 候选 #3（低概率但值得一次夜间卫生整改）：FACAI_* 环境变量泄漏

- monkeypatch 缺口：`tests/conftest.py:14-38` 的 autouse `hermetic_upstream` **只清 `*_API_KEY` 后缀**并打桩 `facai.api.complete`；其余 `FACAI_*` 变量原样进入每个测试 app。
- 产品代码在 import/app 构建期读大量 `FACAI_*`：如 `facai/api.py:493`（调度器默认开）、`facai/api.py:466`（通道通知默认开）、`facai/auth.py:423`（`FACAI_COOKIE_SECURE` 默认 auto）、`facai/auth.py:171`（`FACAI_ADMIN_PASSWORD`）、`facai/api.py:342`（`FACAI_DAILY_PROGRESS_FILE`）等。
- 机理：若 `nightly.sh` source 了生产 env（`deploy/facai.env.example` 的形态），测试 app 会带上生产的密码/安全 Cookie/进度文件路径等配置，个别断言可能随之红。本机 shell 没有这些变量，所以本机全绿不能排除此项。
- 建议修法（不动测试）：nightly.sh 用净环境跑——
  ```bash
  env -i HOME="$HOME" PATH="/usr/local/bin:/usr/bin:/bin" LC_ALL=C.UTF-8 \
      python3 -m pytest -q -rs 2>&1 | tee /opt/facai-dev/nightly-last.log
  ```
  （顺带把 `-rs` 的 skip 理由和 `--collect-only -q | tail -1` 的收集数落进日志，以后诊断不再盲猜。）
- 验证：`cat /opt/facai-dev/nightly.sh` 看是否 source 生产 env；用净环境重跑对比结果是否一致。

## 5. 25 skipped 的构成推算（机理可信，逐条身份待 VPS `-rs` 确认）

| 来源 | 条数 | 证据 |
|---|---|---|
| Playwright/Chromium 缺失（conftest 夹具 `importorskip` + 用例内 skip） | 20 | `tests/conftest.py:70`；`tests/test_console_layout.py:21` 等同款 skip；§3 的逐文件计数 |
| `test_deploy_config.py::test_deploy_script_is_executable_in_git`（部署树无 `.git`） | 1 | `tests/test_deploy_config.py:296` |
| `load_deploy_now()` 门控（deploy-now.py 在部署树外 + paramiko） | ≥1 | `tests/test_deploy_config.py:369-373` |
| `test_verifier` 的 vendor 清单（仓库外层 `vendor/authority-manifests`） | 0~1 | `tests/test_verifier.py:32,238`（`parents[2]/vendor/...`，注意它指向**仓库同级**目录；VPS 布局多半没有） |
| 其余（VPS 部署版本与 HEAD 的门控差异等） | ~2 | 无法在本机枚举，需 `-rs` 输出 |

20+1+1+1 ≈ 23，与 25 差 ~2 条，属于"VPS 部署版本与 HEAD 略有出入"的正常误差（见 §6）。

## 6. 957 vs 947 收集差：最可能是版本偏移，不是环境

- 本机实测收集 **957**（`pytest --collect-only -q`），VPS 收集 947（921+1+25），差 10 条。
- 证据：仓库当前 `grep -rh "^def test_\|^    def test_" tests/*.py | wc -l` = **848** 个测试函数；而 handoff 里现存部署包 `facai-cloud/facai-head.tar.gz`（10-05 打包）只有 **49 个测试文件 / 550 个测试函数**，且缺 `test_verifier.py`、`test_line_channel.py`、`test_tron_*.py` 等——**VPS 跑的显然比这个包新、但比 HEAD 略旧**。最近两天套件日增 ~10 条以上，10 条之差与"部署略滞后于 HEAD"完全相容。
- 环境性收集差也被排除过一轮：全部 28 处 `@parametrize` 都是字面量列表（无 iterdir/glob 驱动）；无模块级 `importorskip`。
- 验证：`python3 -m pytest --collect-only -q | tail -1`（VPS），与 HEAD 的 957 对比，差集即为版本偏移量；对齐部署版本后此差应归零。

## 7. 已排除项（同样重要的阴性结论，均有本次检索证据）

| 嫌疑 | 结论 | 证据 |
|---|---|---|
| 生产 facai-cloud 占 8765 与测试端口冲突 | **排除**：全 tests/facai/deploy 无任何固定端口绑定；仅两处字符串判断 | `grep -rn "8765" facai/ tests/ deploy/` → `facai/secretbox.py:131`、`tests/test_user_models.py:195` 均为端点字符串校验；两处 live uvicorn 用 `_free_port()`（`tests/conftest.py:41-46`、`tests/test_channel_conversion_claim.py:295-297`） |
| opencode CLI 缺失导致失败 | **排除**：测试全部注入 `OpenCodeAdapter(binary='opencode', runner=FakeRunner(...))`；真实子进程路径用 `sys.executable -u -c` 而非 CLI | `tests/test_checkpoints_and_cost.py:56`、`tests/test_opencode_execution.py:202-221`、`tests/test_opencode_serial.py:94`；`facai/opencode.py:120-122` 的 `which("opencode")` 只影响 `availability()`，无用例对真实 PATH 断言 installed=True |
| 真实网络调用 | **排除**：autouse 夹具打桩网关 + 清 API key；渠道/n8n 全走 `httpx.MockTransport`；连通性探测全 mock | `tests/conftest.py:14-38`；`tests/test_channels.py:225`、`tests/test_n8n.py:121`、`tests/test_gateway_attribution.py:12`、`tests/test_model_connectivity.py`（文件头"mocked upstream gateway"） |
| 时区差异（VPS 多为 UTC） | **排除**：全部时间断言用 UTC-aware | `tests/test_scheduler.py:139` 等 17 处 `datetime.now(timezone.utc)` |
| 微信凭据 0600 断言在 POSIX 误报 | **排除**：POSIX 分支断言与代码显式 chmod 匹配 | `tests/test_wechat.py:39-40`（`os.name != "nt"` 才断言）+ `facai/secretbox.py:60`（`os.chmod(path, 0o600)`） |
| git 缺失（cron 瘦 PATH） | **基本排除**：git 常在 `/usr/bin`，cron PATH 覆盖；且缺失时 10 个用例自动 skip 而非 fail | `tests/test_checkpoints_and_cost.py:54`、`tests/test_ingest.py:212`、`tests/test_parallel_isolation.py:87` 等门控 |

## 8. 下次 SSH 窗口的验证清单（主会话执行，按序）

1. `cat /opt/facai-dev/nightly.sh`（确认 env 来源、pytest 参数、日志落点）。
2. `ls -d /opt/facai-dev/.git` → 预期"不存在"，坐实候选 #1 前提。
3. `python3 -m pytest tests/test_checkpoints_and_cost.py -q` → 预期恰好 1 条 FAIL、`fatal: not a git repository`。**此步直接确认/证伪主嫌疑。**
4. `python3 -m pytest -q -rs 2>&1 | tail -60` → 拿到 25 条 skip 的真实理由与那 1 条失败的身份，与 §5 表对账。
5. `python3 -m pytest --collect-only -q | tail -1` → 与本机 957 对比，量化版本偏移（§6）。
6. 落地候选 #1 的门控（1 行 skipif）→ 本机 `python -m pytest -q` 须仍 957 全绿（不碰既有绿）→ 次日夜检预期 **0 failed**；skip 数预计 +1（该测试在部署树转 skip）。

## 9. 本次波次的交付边界（如实说明）

- 本任务**无 pytest 目标**：产出是诊断报告（本文件），不改产品代码、不加测试逻辑。候选 #1 的 1 行 skipif 修复属"最小修复建议"，留待主会话确认 VPS 第 3 步验证结果后由修复波实施（实施时按纪律补双端验证或门控单元测试）。
- 未做：SSH、VPS 任何命令、对首跑失败测试名的直接确认——均在 §0 声明。
