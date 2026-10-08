// Synthetic pywebview API for tools/check_english_web_ui.py (no real hosts or data).
(() => {
  const params = new URLSearchParams(location.search);
  const lang = params.get('lang') || 'en';
  const now = new Date().toISOString();
  const ago = m => new Date(Date.now() - m * 60000).toISOString();
  const conn = {state: 'online', data_origin: 'live', data_revision: 7, last_success_at: now, retry_at: null, error: null};
  const gpu = (i, type, used, total, util, temp) => ({gpu_index: i, gpu_type: type, memory_used_gib: used,
    memory_free_gib: +(total - used).toFixed(1), memory_total_gib: total, utilization_percent: util, temperature_c: temp});
  const servers = [
    {server_id: 'a100-cluster', display_name: 'A100 Cluster', backend: 'slurm_ssh', view_kind: 'scheduler',
     host: 'login.example.invalid', total_gpus: 16, free_vram_gib: 400, total_vram_gib: 1280,
     account: {username: 'researcher', home_directory: '~'}, connection: conn,
     nodes: [
       {node: 'a100-node-01', partition: 'gpu-large', gpu_type: 'A100-80G × 8', memory_per_gpu_gib: 80, total_gpus: 8, free_gpus: 3,
        allocated_gpus: 5, total_vram_gib: 640, free_vram_gib: 240, tasks: ['48211'], state: 'mix'},
       {node: 'a100-node-02', partition: 'gpu-large', gpu_type: 'A100-80G × 8', memory_per_gpu_gib: 80, total_gpus: 8, free_gpus: 2,
        allocated_gpus: 6, total_vram_gib: 640, free_vram_gib: 160, tasks: ['48230'], state: 'mix'}],
     tasks: {current_user: 'researcher', history_supported: true, history_window_hours: 24,
       counts: {RUNNING: 1, PENDING: 1, COMPLETED: 1},
       active: [
         {job_id: '48211', name: 'finetune-7b', user: 'researcher', state: 'RUNNING', nodes: 'a100-node-01', elapsed: '03:12:44', submitted_at: ago(200), time_limit: '12:00:00', gpu_count: 4},
         {job_id: '48236', name: 'eval-suite', user: 'researcher', state: 'PENDING', reason: 'Resources', elapsed: '00:00', submitted_at: ago(15), time_limit: '02:00:00', gpu_count: 2}],
       recent: [{job_id: '48190', name: 'tokenize', user: 'researcher', state: 'COMPLETED', nodes: 'a100-node-02', elapsed: '00:41:10', ended_at: ago(50), gpu_count: 1}]}},
    {server_id: 'rtx4090-ws', display_name: '4090 Workstation', backend: 'direct_ssh', view_kind: 'live-memory',
     host: 'ws.example.invalid', total_gpus: 2, free_vram_gib: 30.5, total_vram_gib: 48,
     account: {username: 'researcher', home_directory: '~'}, connection: conn,
     gpus: [gpu(0, 'NVIDIA GeForce RTX 4090', 15.2, 24, 78, 64), gpu(1, 'NVIDIA GeForce RTX 4090', 2.3, 24, 0, 38)],
     processes: {supported: true, current_user: 'researcher', metadata_limited: false, active: [
       {pid: '23817', user: 'researcher', owner_scope: 'mine', name: 'train.py', command_preview: 'python train.py --config sd-lora.yaml',
        command_truncated: false, allocations: [{gpu_index: 0, memory_used_gib: 14.8}], memory_used_gib: 14.8, cpu_percent: 96.0,
        elapsed_seconds: 5400, started_at: ago(90)}]},
     cpu: {supported: true, logical_cores: 32, load_average: [6.2, 5.8, 5.1], usage_percent: 21, sampled_at: now},
     memory: {supported: true, total_gib: 128, used_gib: 41, sampled_at: now}},
    {server_id: 'h100-node', display_name: 'H100 Server', backend: 'direct_ssh', view_kind: 'live-memory',
     host: 'h100.example.invalid', total_gpus: 4, free_vram_gib: 248.6, total_vram_gib: 320,
     account: {username: 'researcher', home_directory: '~'}, connection: conn,
     gpus: [gpu(0, 'NVIDIA H100 80GB HBM3', 71.4, 80, 97, 71), gpu(1, 'NVIDIA H100 80GB HBM3', 0.5, 80, 0, 35),
            gpu(2, 'NVIDIA H100 80GB HBM3', 0.5, 80, 0, 34), gpu(3, 'NVIDIA H100 80GB HBM3', 0.5, 80, 0, 36)],
     processes: {supported: true, current_user: 'researcher', metadata_limited: false, active: []}},
    {server_id: 'lab-a6000', display_name: 'Lab A6000', backend: 'direct_ssh', view_kind: 'live-memory',
     host: 'a6000.example.invalid', total_gpus: 0, free_vram_gib: 0, total_vram_gib: 0,
     account: {username: 'researcher', home_directory: '~'},
     connection: {state: 'stale', data_origin: 'cache', data_revision: 7, last_success_at: ago(12),
       retry_at: new Date(Date.now() + 240000).toISOString(), error: {message: 'SSH connection timed out', code: 'ssh_timeout', retryable: true}},
     gpus: [], processes: {supported: true, current_user: 'researcher', active: []}}];
  const variant = params.get('v') || 'full';
  if (variant === 'clean') { servers.pop(); servers[0].tasks.active.pop(); servers[0].tasks.recent = []; servers[0].tasks.counts = {RUNNING: 1}; }
  if (variant === 'offline') {
    servers.forEach(server => { server.connection = servers[3].connection; });
    servers[1].connection = {state: 'offline', data_origin: 'none', data_revision: 7, last_success_at: null, retry_at: null,
      error: {message: 'SSH authentication failed', code: 'auth_failed', retryable: false}};
  }
  if (variant === 'connecting') {
    servers.slice(1).forEach(server => { server.connection = {state: 'connecting', data_origin: 'none', data_revision: 0,
      last_success_at: null, retry_at: null, error: null}; server.gpus = []; });
    servers.push({server_id: 'local-gpu', display_name: 'Local GPU', backend: 'local', view_kind: 'live-memory', host: '',
      total_gpus: 0, free_vram_gib: 0, total_vram_gib: 0, account: {}, gpus: [], processes: {supported: false, active: []},
      connection: {state: 'connecting', data_origin: 'none', data_revision: 0, last_success_at: null, retry_at: null, error: null}});
  }
  if (variant === 'single') {
    // One GPU per server: exercises singular wording ("1 GPU").
    servers.splice(1, 3, {...servers[2], gpus: servers[2].gpus.slice(0, 1), total_gpus: 1, free_vram_gib: 79.5, total_vram_gib: 80});
    servers[0].nodes = servers[0].nodes.slice(0, 1).map(node => ({...node, total_gpus: 1, free_gpus: 1, allocated_gpus: 0}));
    servers[0].total_gpus = 1;
    servers[0].free_gpus = 1;
  }
  const events = variant === 'full' ? [
    {sequence: 3, kind: 'task_completed', label: 'finetune-7b', created_at: ago(5)},
    {sequence: 2, kind: 'favorite_gpu_available', title: 'Favorite resource available', message: 'H100 Server: favorited GPU 1 is free', created_at: ago(20)},
    {sequence: 1, kind: 'update_available', latest_version: '9.9.9', created_at: ago(60)}] : [];
  const profile = {schema_version: 1, profile_revision: 2, id: 'readme-demo', display_name: 'Demo', refresh_seconds: 60,
    server_config_path: '', auto_sync_servers: false, ignored_ssh_aliases: [], navigator_side: 'right', close_behavior: 'hide',
    ui_language: lang, task_completion_watches: [],
    servers: servers.map(s => ({id: s.server_id, display_name: s.display_name, backend: s.backend, ssh_alias: s.server_id, host: s.host,
      port: 22, port_override: false, username: 'researcher', identity_file: '', ssh_config_file: '', enabled: true,
      show_other_user_commands: false, connect_timeout_seconds: 10}))};
  const snapshot = {profile: {refresh_seconds: 60}, fetched_at: now, notices: [],
    monitoring: {revision: 7, in_flight: false, paused: false, data_updated_at: now},
    summary: {revision: 7, free_vram_gib: 679.1, total_vram_gib: 1648, online_servers: servers.filter(s => s.connection.state === 'online').length, total_servers: servers.length, total_gpus: variant === 'single' ? 1 : 22, data_updated_at: now},
    notifications: {unread_count: events.length, latest_sequence: events.length, read_sequence: 0, events},
    servers};
  const known = {
    get_profile: () => profile, get_snapshot: () => snapshot, get_status: () => snapshot,
    check_for_updates: () => ({ok: true, update_available: variant === 'full', current_version: '1.0.0', latest_version: variant === 'full' ? '9.9.9' : '1.0.0'}),
    request_background_refresh: () => ({ok: true, accepted: false}),
    get_update_progress: () => ({state: 'idle', phase: 'idle'}),
    get_codex_usage: () => ({enabled: false, state: 'disabled', windows: []}),
    get_usage_providers: () => ({enabled: false, selected: ['codex'], providers: {}}),
    get_cluster_nodes: () => ({ok: true, nodes: servers[0].nodes || [], total: (servers[0].nodes || []).length, revision: 7}),
    inspect_account_directory: () => ({ok: true, path: '~', truncated: false, entries: [
      {name: 'projects', type: 'directory', modified_at: now, child_count: 2},
      {name: 'notes.txt', type: 'file', size_bytes: 1200, modified_at: now}]}),
  };
  const api = new Proxy({}, {get: (_, name) => async (...args) => (known[name] ? known[name](...args) : {ok: true})});
  window.pywebview = {api};
  window.addEventListener('DOMContentLoaded', () => setTimeout(() => window.dispatchEvent(new Event('pywebviewready')), 50));
  const expand = params.get('expand');
  if (expand && expand !== '0') {
    const selector = expand === '1' ? 'details' : expand;
    setTimeout(() => document.querySelectorAll(selector).forEach(node => { node.open = true; }), 2500);
  }
})();
