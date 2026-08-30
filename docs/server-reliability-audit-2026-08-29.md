# 服务器导入、连接与配置可靠性审计（2026-08-29）

## 结论

本轮审计已消除当前代码中已发现的“界面看似成功、实际配置没有生效或状态并不可信”的高严重度路径，并把服务器状态收敛为下面这条单向证据链：

1. **已解析**：本地文件可以读取，只代表发现了候选服务器。
2. **已保存**：Profile 与系统凭据存储已完成事务提交，只代表配置被持久化。
3. **正在验证**：应用正在使用保存后的同一套 SSH 参数运行真实资源采集器。
4. **监控就绪**：SSH 认证成功、后端命令成功、返回协议完成解析；只有此状态计入实时 GPU 和显存。
5. **数据已过期**：保留旧快照供参考，但不计入实时容量，并显示最新连接错误。

因此，**“导入成功”仍不能也不应该等同于“服务器可用”**。服务器可能暂时离线，也可能需要首次接受 Host Key；应用允许用户先保存这类配置，但不会把它标成 Connected、Ready 或 Available。只有完整采集器成功后才显示“监控就绪”。

VRAM Radar 当前是只读监控和导航工具，不提交作业、不运行用户代码、也不进行文件同步。因此，本轮能够保证的是“监控就绪 = 当前 SSH 身份可以执行并解析所选 Direct SSH / Slurm 资源采集链路”，**不能把它扩大解释为任意任务必然可执行或任意目录可写**。

## 审计范围

- 导入：项目 TOML、OpenSSH Config、Include、编辑器 Remote-SSH 配置、首次发现、手动导入、启动同步。
- 解析：Host/HostName、User、Port、IdentityFile、精确 `-F` 配置来源、ProxyJump/ProxyCommand 的 OpenSSH 原生解析路径。
- 本地路径：`~`、`$HOME`、`${HOME}`、`$env:...`、`%USERPROFILE%`、OpenSSH `%d`、Windows 与 POSIX 路径。
- 认证：SSH Key、ssh-agent、系统凭据存储中的密码、密钥优先与密码回退、Host Key、代理链。
- 采集：Direct SSH、Slurm、目录树、节点分页、缓存、刷新与错误状态。
- 提交一致性：Profile、运行时状态、系统凭据、自动同步、并发保存、本地失败恢复、远端公钥追加后的恢复提示、第二实例。
- UI 一致性：导入/保存/验证文案、陈旧数据、异步刷新、目录和节点的乱序响应。

没有在本轮连接用户的真实服务器，也没有在 Windows 主机上宣称完成原生 macOS 验证。

## 已发现并修复的问题

### REL-01 — 非默认 OpenSSH 来源导入后可能无法按原配置连接

- **场景与影响**：服务器来自 Cursor、VS Code、Windsurf、XDG 或 Include 文件；候选 Host 能导入，但后续若丢失来源文件，`HostName`、`User`、`IdentityFile`、`ProxyJump` 或 `ProxyCommand` 可能没有生效，导致服务器无法使用。
- **严重程度**：P1 / 高；典型“导入成功、实际不可连接”。
- **根本原因**：导入结果过去没有始终保留拥有该别名的精确 OpenSSH 配置来源。
- **修复**：OpenSSH 候选保存精确配置路径；登录、监控、目录与复制 SSH 共用规范化参数并显式传递 `ssh -F <source> <alias>`。HostName/User/Port 的显式表单覆盖仅在用户选择覆盖时生效。
- **回归测试**：`test_imported_alias_uses_its_exact_openssh_config_file`、`test_alias_overrides_are_canonical_and_target_is_option_terminated`、`test_ssh_copy_uses_the_backend_canonical_command`。

### REL-02 — 自动同步保存时与重启后使用不同服务器集合

- **场景与影响**：用户启用自动同步；保存当下看似成功，但同步结果要到重启才应用，或者无效主配置被另一个有效的次要来源掩盖。
- **严重程度**：P1 / 高；配置表面保存但实际未生效或重启后突变。
- **根本原因**：设置保存和启动同步使用了两条不同路径，且多来源成功条件过宽。
- **修复**：保存时立即运行与启动相同的保守同步；用户选定的主来源必须独立有效，次要来源只能补充，不能掩盖主来源失败；同步错误进入主界面通知而不只写日志。
- **回归测试**：`test_auto_sync_save_applies_catalog_immediately_and_matches_restart`、`test_auto_sync_does_not_hide_an_invalid_primary_behind_a_valid_secondary`、`test_startup_auto_sync_failure_is_visible_in_the_dashboard_snapshot`。

### REL-03 — 密码、加密私钥和代理错误被错误归类

- **场景与影响**：密码错误、私钥需要口令但未加载 agent、agent 拒绝密钥、ProxyCommand 本地程序不存在；用户可能只看到泛化的连接失败，甚至被误导去检查远端 GPU 命令。
- **严重程度**：P1 / 高；认证和代理故障会完全阻断使用，错误动作会延长恢复时间。
- **根本原因**：SSH stderr 分类过于粗糙，密码重试提示和本地代理错误与远端命令错误混在一起。
- **修复**：分别输出认证拒绝、密钥不存在、私钥权限/格式、密钥口令或 agent、DNS、超时、端口拒绝、网络不可达、Host Key、ProxyJump、ProxyCommand 和资源命令缺失。只在真正的 `auth_failed` 后读取保存密码；网络、代理和采集错误不会触发第二次密码连接。
- **回归测试**：`test_password_retry_prompt_is_classified_as_authentication_failure`、`test_encrypted_or_agent_refused_keys_have_actionable_auth_errors`、`test_local_proxy_command_missing_is_not_reported_as_remote_collector_success`、`test_configured_identity_is_tried_before_saved_password_fallback`。

### REL-04 — 并发设置可能用旧表单覆盖新 Profile

- **场景与影响**：两个保存动作、SSH Key 引导和普通设置、或旧 WebView 响应交错；磁盘、运行时与 UI 可能指向不同版本。
- **严重程度**：P1 / 高；可造成配置丢失、错误认证引用和不可预测状态。
- **根本原因**：Profile 没有提交版本比较，读取也可能发生在 Profile 与 revision 更新的中间点。
- **修复**：所有 Profile 修改由同一把锁串行化；保存必须携带当前 `profile_revision`；旧版本返回 `profile_changed` 并带回最新 Profile，不覆盖用户新状态；Profile 与 revision 原子读取。
- **回归测试**：`test_profile_save_requires_the_current_revision`、`test_get_profile_waits_for_atomic_profile_and_revision_commit`、Web UI 的保存单飞与冲突恢复断言。

### REL-05 — 本地事务失败或 SSH Key 部分完成可能被当作普通失败

- **场景与影响**：密码已写入系统凭据存储后 Profile 写入失败；或公钥已经追加到服务器，但所选私钥验证或本地 Profile 保存随后失败。继续操作可能使用不确定的本地凭据状态；若此时自动恢复旧 `authorized_keys`，还可能删除管理员或其他进程的并发写入。
- **严重程度**：P1 / 高；本地状态可能分裂，错误的远端“回滚”还可能造成其他 SSH Key 数据丢失。
- **根本原因**：早期错误处理忽略本地二次恢复失败，并把应用不独占的远端 `authorized_keys` 错误建模为可以安全回滚的事务文件。
- **修复**：Profile、运行时与系统凭据继续使用显式本地事务；本地恢复不完整时返回 `recovery_required` 和专用错误码，绝不显示成功。远端公钥安装改为非替换式追加；一旦追加成功，后续验证或 Profile 保存失败时不再自动改写或删除 `authorized_keys`。应用会保留已追加公钥及其匹配的本地生成密钥，返回 `recovery_required`，并提示用户重试或手动精确移除该公钥。
- **回归测试**：覆盖系统凭据本地恢复失败、远端追加后验证失败、远端追加后 Profile 保存失败三条路径；后两者必须断言远端公钥与匹配的本地生成密钥均被保留，并返回可操作的恢复提示。

### REL-06 — 同一服务器 ID 可能继承旧端点缓存

- **场景与影响**：用户把已有 ID 改指向另一台主机；旧 GPU 快照可能短暂显示在新端点下，造成错误容量判断。
- **严重程度**：P1 / 高；真实状态与 UI 不一致。
- **根本原因**：旧缓存只按服务器 ID 归属，没有绑定连接身份。
- **修复**：缓存 schema v2 绑定规范化连接指纹；Host、alias、配置/密钥依赖、用户、端口、后端或相关文件变化都会阻止旧快照继承。损坏或未来版本缓存被安全丢弃，不阻塞启动。
- **回归测试**：`test_cache_from_the_same_server_id_but_an_old_endpoint_is_not_loaded`、`test_connection_fingerprint_tracks_relative_ssh_files_under_user_ssh_directory`、`test_semantically_corrupt_cache_payload_is_ignored_without_blocking_startup`。

### REL-07 — 设置和探测后的 UI 状态可能没有反映真实运行时

- **场景与影响**：修改收藏、侧栏等非连接设置时在线状态被重置；手动连接测试成功或失败后主界面仍显示旧状态；缓存失败仍被计入实时容量。
- **严重程度**：P1 / 高；用户无法相信状态标签和容量汇总。
- **根本原因**：Profile 替换、探测与汇总各自维护了部分状态，没有统一到同一 RuntimeState。
- **修复**：不改变连接身份的 Profile 更新保留实时状态；连接身份变化只重置该服务器；手动测试运行与周期刷新完全相同的采集器并写回同一运行时；旧快照标为 stale 且排除实时容量。
- **回归测试**：`test_connection_probe_success_updates_the_same_runtime_state_used_by_the_dashboard`、`test_connection_probe_failure_updates_runtime_before_returning_the_error`、`test_non_connection_profile_changes_preserve_live_runtime_state`、`test_cached_failure_is_stale_and_excluded_from_summary`。

### REL-08 — 乱序刷新可能让旧响应覆盖新状态

- **场景与影响**：慢服务器、多次点击刷新、切换目录或 Slurm 节点页；旧请求晚到后可能覆盖刚保存的 Profile、最新错误或当前页。
- **严重程度**：P1 / 高；UI 展示过期状态，用户可能在错误服务器/目录上继续操作。
- **根本原因**：异步请求只按完成顺序渲染，没有绑定 Profile、数据 revision 或请求 generation。
- **修复**：全局刷新、Slurm 页、目录请求均带 generation/revision；旧结果、旧异常和旧 finally 不得覆盖新状态；目录结果还校验服务器连接指纹与 Profile generation。
- **回归测试**：`test_refresh_ignores_older_status_responses_errors_and_cleanup`、`test_cluster_node_requests_ignore_out_of_order_results`、`test_directory_requests_ignore_stale_results_and_merge_into_latest_tree`、服务端目录 generation 测试。

### REL-09 — 第二实例可能在发现已运行实例前改写 Profile

- **场景与影响**：用户连续点击应用；第二进程虽随后退出，但可能已经执行自动发现/同步，造成首实例内存和磁盘分歧。
- **严重程度**：P1 / 高；用户没有看到第二窗口却发生配置突变。
- **根本原因**：单实例锁获取晚于 Runtime/Profile 构建。
- **修复**：先解析本地路径并获取实例锁，再读取或同步 Profile；锁目录不可写与“已有实例”使用不同错误，退出时幂等释放。
- **回归测试**：`test_second_instance_is_rejected_before_runtime_can_sync_the_profile`、`test_second_instance_signals_primary_and_exits_without_an_error_window`。

### REL-10 — 跨平台路径展开不完整，Windows `%d` 替换会误解析反斜杠

- **场景与影响**：从另一平台复制配置，或使用 `~`、`$HOME`、`${HOME}`、`$env:USERPROFILE`、`%USERPROFILE%`、OpenSSH `%d`；文件存在但应用报告找不到。审计回归还发现 `%d` 展开到 Windows 用户目录时可能被正则替换语法当成转义符。
- **严重程度**：P2 / 中；特定配置无法导入或认证。
- **根本原因**：Path.expanduser 和环境变量语法只覆盖当前平台，正则替换字符串没有把 Windows 反斜杠当作字面数据。
- **修复**：统一跨平台本地路径展开；原生路径规范化、异平台绝对路径保守保留；`%d` 使用 callable 替换，避免反斜杠转义。
- **回归测试**：`test_explicit_config_path_expands_cross_platform_home_variables`、`test_openssh_include_expands_home_environment_and_percent_d`、`test_openssh_include_preserves_windows_backslashes`、连接器相对路径测试。

### REL-11 — 大小写不同的服务器 ID 可在同步后形成不可保存 Profile

- **场景与影响**：本地已有 `GPU-A`，导入源生成 `gpu-a`；启动内存中似乎有两个候选，但下次保存被大小写不敏感唯一性校验拒绝。
- **严重程度**：P2 / 中；同步表面完成，后续管理失败。
- **根本原因**：Profile ID 规则已经大小写不敏感，而同步匹配仍按原始字符串比较。
- **修复**：在 alias 合并前按 casefold 匹配并保留本地 ID 拼写；后续所有匹配与冲突处理基于统一身份。
- **回归测试**：`test_resync_matches_existing_server_ids_case_insensitively`。

### REL-12 — SSH 超时只结束父进程，代理子进程和输出可能继续占用资源

- **场景与影响**：ProxyCommand 卡住、远端输出异常大、千服务器同时刷新；残留进程、内存增长或长时间卡顿会让正常服务器也不可用。
- **严重程度**：P2 / 中；资源耗尽时可升级为全局可用性故障。
- **根本原因**：缺少进程组终止、全局捕获预算和协议/输出边界。
- **修复**：建立独立进程组，超时/溢出时终止进程树；限制 stdout、stderr、stdin、并发捕获总量、节点/任务/目录条目和字段长度。
- **回归测试**：`test_remote_stdout_overflow_is_killed_and_reported_as_domain_failure`、`test_near_limit_remote_captures_share_a_global_concurrency_budget`、目录与 Slurm 病理范围测试。

### REL-13 — OpenSSH Port 与表单默认端口可能互相覆盖

- **场景与影响**：OpenSSH Config 使用非 22 端口，但导入表单默认显示 22；若无显式语义，保存后可能强制覆盖原配置。
- **严重程度**：P2 / 中；特定服务器完全无法连接。
- **根本原因**：默认显示值与用户明确覆盖值没有分开建模。
- **修复**：新增 `port_override`；只有用户明确勾选或编辑端口时才传 `-p`，否则由精确 OpenSSH 配置决定。
- **回归测试**：连接 argv、Profile round-trip 和 Web UI 端口覆盖测试。

### REL-14 — “导入/保存成功”文案可能被理解为已经可用

- **场景与影响**：用户完成解析或保存后立即认为服务器在线；后台还未认证或采集已经失败。
- **严重程度**：P2 / 中；不破坏数据，但直接造成错误预期。
- **根本原因**：发现、持久化与运行验证没有在用户语言中明确分级。
- **修复**：导入结果只说“已解析，待检查/保存”；保存后说“配置已保存，正在连接服务器”；连接中显示“正在验证”；只有采集成功显示“监控就绪”；汇总明确为“SSH 已认证 / 资源已读取”。
- **回归测试**：`test_import_messages_distinguish_parsing_from_save_and_connection_validation`、`test_connection_test_never_probes_stale_saved_configuration`、UI 状态词断言。

### REL-15 — 首次启动可能在用户复核前持久化并连接自动发现的 Host

- **场景与影响**：空 Profile 所在电脑已有 OpenSSH/TOML 配置；启动阶段直接把候选写入 Profile，前端因此跳过三步引导并立即连接。OpenSSH 无法声明 Direct SSH 或 Slurm，未经确认的默认类型可能错误。
- **严重程度**：P1 / 高；绕过用户复核，并可能以错误后端连接。
- **根本原因**：后台 Runtime 构建同时拥有“发现候选”和“提交 Profile”两项责任。
- **修复**：普通 GUI 首次启动永远保持空 Profile；自动发现只在引导中生成未保存、未验证的预览，用户确认并保存后才连接。已保存的自动同步仍在启动时执行；显式命令行 `--servers-config` 保留为有意导入路径。
- **回归测试**：`test_default_first_run_keeps_discovery_unsaved_until_user_review`、`test_explicit_import_merges_catalog_semantics_with_exact_openssh_source`、空 Profile Web UI 引导测试。

### REL-16 — OpenSSH 配置可静默覆盖用户在界面选择的密码认证

- **场景与影响**：导入的 Host 写有 `PasswordAuthentication no` 或 `KbdInteractiveAuthentication no`；用户在界面正确保存密码，但 SSH 仍按配置禁用密码路径。
- **严重程度**：P1 / 高；界面承诺的密码登录无法使用。
- **根本原因**：密码模式只设置认证优先级，没有显式覆盖这两个认证开关。
- **修复**：受控密码模式显式传递 `PasswordAuthentication=yes` 与 `KbdInteractiveAuthentication=yes`，同时保留 `PubkeyAuthentication=no`、单次提示、私有 askpass broker 和密码不进入 argv/环境的边界。
- **回归测试**：密码 SSH argv 的完整认证选项断言与打包 askpass 验证。

### REL-17 — 加密私钥或 ssh-agent 暂时不可用时没有使用已保存密码

- **场景与影响**：服务器同时配置了 SSH Key 与可用密码；私钥尚未解锁或 agent 拒绝签名，连接直接停止，尽管密码可以恢复服务。
- **严重程度**：P1 / 高；存在有效备用身份仍无法使用服务器。
- **根本原因**：密码回退只接受通用 `auth_failed`，没有包含两个明确的本地 Key 不可用状态。
- **修复**：仅在服务器确有 `auth_ref` 时，`identity_passphrase_required` 与 `ssh_agent_refused` 也进入一次密码回退；没有保存密码时保留原始精确错误。
- **回归测试**：`test_saved_password_falls_back_when_private_key_needs_passphrase_or_agent_refuses`、`test_key_specific_auth_error_is_preserved_without_saved_password`。

### REL-18 — SSH Key 后置失败曾尝试自动撤销远端公钥

- **场景与影响**：应用追加公钥后验证或保存失败；与此同时管理员、自动化程序或另一个会话也修改了 `authorized_keys`。自动恢复旧文件或按快照删除内容存在覆盖并发修改的窗口；若同时删除本地生成私钥，服务器上还会遗留一个用户无法再使用的公钥。
- **严重程度**：P1 / 高；可能造成其他 SSH Key 数据丢失，并让失败后的真实认证状态难以恢复。
- **根本原因**：远端 `authorized_keys` 并非应用独占资源，预先校验 checksum 再替换也无法消除“检查通过到写入之间”的竞争窗口，因此不能把它当作普通可回滚文件。
- **修复**：远端安装只做非替换式追加，绝不通过替换整个 `authorized_keys` 完成安装。追加成功后的任何后置失败都不自动改写或删除该文件；应用保留新公钥与匹配的本地生成密钥，明确返回 `recovery_required`，并给出重试或手动精确移除该公钥的步骤。
- **回归测试**：覆盖远端安装期间的并发追加，断言其他写入不会丢失；覆盖验证失败和 Profile 保存失败，断言不会调用远端删除/替换路径，且保留匹配本地密钥和恢复说明。

### REL-19 — 文档宣称“打开终端”，但后端桥接曾不存在

- **场景与影响**：UI 代码只有在 `api.open_terminal` 存在时显示按钮，而后端没有该方法，所以用户永远看不到已宣传的能力。
- **严重程度**：P2 / 中；功能链路未闭环。
- **根本原因**：前端条件分支、README 与桌面 API 的所有权分裂。
- **修复**：Windows 使用新控制台中的固定 PowerShell 解码器，动态 SSH argv 只以 Base64 数据封装；macOS 使用 Terminal/AppleScript，并在 8 秒内检查 `osascript` 的真实退出状态。两端始终复用规范化 `ssh_login_argv`，不使用 `shell=True`；启动失败提示复制 SSH 命令兜底。
- **回归测试**：`test_windows_open_terminal_passes_canonical_ssh_argv_without_shell`、`test_macos_open_terminal_uses_posix_quoted_canonical_command`、`test_open_terminal_failure_is_actionable`、Web UI 事件闭环测试。

### REL-20 — 用户删除的废弃 SSH Host 会在同步或重启时复活

- **场景与影响**：SSH Config 仍保留已经废弃的 Host；用户在应用中主动移除后，保存时或下次启动又被自动导入，服务器列表无法整理。
- **严重程度**：P1 / 高；用户明确操作没有持久生效，且废弃端点持续产生连接错误与干扰。
- **根本原因**：删除只移除了前端编辑器；Profile 没有记录“此 Alias 已由用户明确忽略”，所有同步路径都把它当成新 Host。
- **修复**：Profile v1 新增受限、大小写不敏感唯一的 `ignored_ssh_aliases`。只有点击移除产生的明确意图才会写入；保存同步、启动同步和导入预览共用过滤规则。重命名、导入替换时缺少旧行不会误记；活动/手动重加同一 Alias 会恢复。配置中新增的其他 Host 照常导入；全部 Host 均被忽略仍是成功同步而非配置错误。
- **回归测试**：`test_deleted_imported_host_stays_ignored_across_save_restart_and_new_hosts`、`test_missing_old_row_without_explicit_remove_does_not_create_a_tombstone`、`test_server_rename_does_not_create_an_ignored_alias`、`test_manual_readd_of_ignored_alias_clears_the_tombstone`、`test_removed_import_candidates_persist_as_alias_tombstones`。

### REL-21 — 复制 SSH 只显示 Alias，或为追求“完整”而改变真实连接语义

- **场景与影响**：导入项只保存 Alias 与配置来源，复制结果看起来只有名称；若直接猜测本机用户名和 22 端口，又可能覆盖系统级 `ssh_config` 中真实生效的 User/Port，导致复制命令连接失败或连错账号。
- **严重程度**：P1 / 高；用户用于排障或终端登录的命令可能不可理解，错误固化默认值时还会改变连接目标。
- **根本原因**：有效 HostName/User/Port 属于 OpenSSH 配置求值结果，但 `ssh -G` 会评估 `Match exec`，不能作为无副作用的复制实现；用户配置未声明的字段也不能安全猜测。
- **修复**：新增有界纯 Python 静态解析器，只读取选定配置及其静态 Include，遵守 first-value-wins，不调用 OpenSSH、shell、subprocess、DNS 或网络。只有 HostName/User/Port 三者都能证明时，复制命令才显式加入它们；始终保留 `-F ... -- alias`，让 ProxyJump、ProxyCommand、IdentityFile、HostKeyAlias 与动态 Match 仍由原配置拥有。条件、动态、缺失或系统默认不确定时复制原 Alias 命令并明确提示，不猜测。
- **回归测试**：`test_copied_alias_expands_exact_endpoint_and_preserves_config_semantics`、`test_copy_does_not_freeze_user_or_port_omitted_from_the_selected_config`、`test_copied_alias_does_not_guess_through_conditional_match`、`test_copy_details_does_not_claim_unmatched_host_is_complete`、`test_copied_imported_ssh_command_exposes_static_address_user_and_port`、`test_dynamic_ssh_config_copies_safe_alias_command_with_an_explicit_warning`。

## 当前仍需保留的边界与后续验证

### 原生 macOS 验证尚未完成

- **性质**：发布验证缺口，不是本轮已复现的代码故障。
- **风险**：Finder 启动时的 HOME/Keychain/ssh-agent 环境、`/usr/bin/ssh`、Cocoa WebView、arm64 与 x86_64 包可能出现 Windows 测试无法覆盖的差异。
- **必须完成的证据**：分别在 Apple Silicon 和 Intel runner 运行完整单元测试、JavaScript/Python 检查、`Build-VramRadar-macOS.sh`、`validate_macos_bundle.py`；使用一次无身份信息的临时 OpenSSH fixture 验证 Include、ProxyJump、agent/key、错误分类和剪贴板。不应使用用户真实服务器作为普通发布测试。

### 交互式 MFA、硬件密钥确认和未加载的加密私钥不是无提示登录路径

- **性质**：认证能力边界。
- **行为**：密码登录只允许一个受控的本地 askpass 流程；私钥带口令时应先加载 ssh-agent。需要 OTP、多轮 keyboard-interactive、FIDO 触摸或企业 SSO 的服务器不能被静默标为就绪，应停在认证错误并给出行动提示。
- **建议测试**：在后续声明支持某类 MFA 前，增加隔离的 OpenSSH 集成 fixture；不要让通用 askpass 猜测多轮提示。

### 网络和服务状态不能被永久保证

- **性质**：分布式系统事实。
- **行为**：一次验证成功只证明该时刻的 SSH、权限、命令和响应协议成功。服务器关机、路由变化、密钥撤销、Slurm 权限变化都会把运行时降级为 stale/offline/auth_required/misconfigured，并从实时容量中排除。

### “任务执行”和“文件同步”不在当前产品范围

- **性质**：产品边界，不是未接通的按钮。
- **行为**：应用显示当前账号的任务、复制 SSH、打开终端和只读浏览目录；不提交/取消作业、不执行用户代码、不上传或同步文件。若未来加入这些能力，必须单独设计幂等请求、权限预检、命令预览、审计记录、取消与恢复，不能把监控探测成功当成任务执行授权。

## 验证证据

- Windows 自动化测试：共运行 302 项，结果 OK；其中 2 项原生 POSIX 权限断言在 Windows 跳过。
- JavaScript：`node --check src/vram_radar/web/app.js` 通过。
- Python：`python -m compileall -q src tests tools` 通过。
- 补丁：`git diff --check` 通过。
- Windows PyInstaller：`Build-VramRadar.ps1 -SkipSync` 通过。
- 打包密码助手：`tools/validate_packaged_askpass.py` 通过，密码没有进入 argv、子进程环境或输出。
- Windows 通知区域：`tools/validate_packaged_tray.py` 通过。
- 空 Profile：打包 EXE 使用独立 `--home --profile audit --no-auto-import --show-paths` 和 `--gui-smoke` 均以 0 退出。
- 打包 web assets 与源码 SHA-256 一致。
- 当前 Windows EXE SHA-256：`459ACBFE2E9FFEA0FDF5996E8D9B011E72C52EFA7C279288DBE6860B4909AD0F`。

上述证据覆盖代码、事务、模拟 SSH 进程、Windows 打包和空 Profile。原生 macOS 与真实外部服务器仍严格标为未在本轮验证。
