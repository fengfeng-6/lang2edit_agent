# 上海交大超算规则

haness 在 Windows 本机运行；项目在上海交大超算运行，小批量测试允许在本机进行。

## 固定环境

- 本机：Windows PowerShell / Windows OpenSSH，不使用 WSL
- SSH config：`C:\Users\feng'feng\.ssh\config`
- SSH host：`hpc_pi`；用户：`yejinquan`
- SSHFS 挂载：`Z:\`（远程仓库的直接视图，不是副本）
- 远程项目：`/lustre/home/acct-wendongwei/yejinquan/it_stu100_home/lang2edit_agent_gallant`
- Python：`/lustre/home/acct-wendongwei/yejinquan/it_stu100_home/.conda/envs/lang2edit/bin/python`（Python 3.11，pydantic 2.x；本机另有 pydantic 1.10 环境，代码须双版本兼容）

不要用 `$HOME`、`~` 或用户名拼接推导项目路径。SSH 自动化使用 `-o BatchMode=yes -o ConnectTimeout=12`。

## 代码同步（git bundle）

远程仓库 `origin` 指向 bundle 文件
`/lustre/home/acct-wendongwei/yejinquan/it_stu100_home/lang2edit_gallant.bundle`，
repo 常驻 detached HEAD。超算无 GitHub 直连假定。同步约定：

```bash
# 本地
git bundle create $TEMP/lang2edit_gallant.bundle <branch>
scp $TEMP/lang2edit_gallant.bundle hpc_pi:/lustre/home/acct-wendongwei/yejinquan/it_stu100_home/lang2edit_gallant.bundle

# 远端
cd /lustre/home/acct-wendongwei/yejinquan/it_stu100_home/lang2edit_agent_gallant
git fetch origin && git checkout origin/<branch>
```

注意 bundle 必须放在 lustre 家目录（pilogin 是多登录节点轮询，/tmp 不共享，
scp 与后续 ssh 会落到不同机器）。远端未跟踪的 smoke 脚本（smoke_gallant.py 等）
是用户的，不要动。

## SSHFS 优先工作流

需要读取或编辑远程项目时，先检查 `Z:\`,注意在沙盒外才可见。若不可用，优先挂载后再继续：

```powershell
& "C:\Program Files\SSHFS-Win\bin\sshfs-win.exe" `
  svc `
  "\sshfs.kr\yejinquan@hpc_pi\lustre\home\acct-wendongwei\yejinquan" `
  Z: 
```

挂载后确认 `Z:\it_stu100_home\lang2edit_agent_gallant\.git` 和目标文件可访问。
挂载失败时诊断 SSHFS/SSH，不得静默创建本地替代仓库。

- 少量源码、配置和文档的读取/补丁编辑优先直接使用 `Z:\`。
- Git、递归搜索、大目录检查和大量 metadata I/O 优先通过 SSH 在远端执行。
- 不通过 SSHFS 递归扫描 `data/`、`outputs/`、模型、checkpoint 或缓存。
- 若当前沙箱没有 `Z:\` 写权限，说明需要把 `Z:\` 加为工作区；不要用未审计的复制/覆盖绕过。

## 硬约束

- 不读取、输出或持久化 SSH 私钥、API key、完整环境变量。
- 不丢弃、覆盖或回滚用户已有修改，不执行破坏性 Git/清理操作。
- 不擅自 push、创建 PR、`scancel`、结束 tmux、停止 Code Server、kill 进程或改变资源申请。
- 不删除或覆盖重要数据、checkpoint、报告原文、人工复核文件或不可重建产物。
- 不在登录节点运行明显消耗 CPU/GPU/内存或大量 Lustre I/O 的任务。

## 登录节点、Job 与资源

登录节点只用于 SSH/Slurm 调度、Git和轻量文本/日志检查。Python、pytest、数据处理、embedding、模型、API 批量请求及重型 I/O 必须进入已有 Slurm Job。

Job ID 是动态状态。需要进入计算节点时先运行：

```bash
squeue -u yejinquan -o '%.18i %.12P %.32j %.2t %.12M %.12l %.5D %R'
```

优先使用明确匹配且 `RUNNING` 的 `session_codeserver_*`。只有无法可靠判断时才询问用户。

- 轻量测试或短小 Python：确认 Job 仍为 `RUNNING` 即可，不必每次完整审计 CPU/GPU/进程。
- 重型测试、数据处理、模型/embedding、或需调整 worker、batch、并发时，必须先检查完整资源：

```bash
scontrol show job <JOB_ID>
srun --jobid=<JOB_ID> --overlap -N1 -n1 --cpus-per-task=1 bash -lc '
  hostname
  nproc
  free -h
  nvidia-smi --query-gpu=index,name,memory.total,memory.used,memory.free,utilization.gpu,utilization.memory --format=csv,noheader
  ps -u yejinquan -o pid,etime,pcpu,pmem,comm,args --sort=-pcpu
'
```

必要时使用 `sstat -j <JOB_ID> --format=JobID,AveCPU,AveRSS,MaxRSS,MaxVMSize`。资源不足或剩余时间不够时先调整/报告，不终止现有进程腾资源。重型任务结果应报告 Job、节点、CPU、内存、GPU/显存、利用率及实际参数。

## Python 与测试

不使用损坏的仓库 `.venv`，也不在 Windows 或登录节点运行项目 Python。典型命令：

```bash
srun --jobid=<JOB_ID> --overlap -N1 -n1 --cpus-per-task=1 bash -lc '
  cd /lustre/home/acct-wendongwei/yejinquan/it_stu100_home/lang2edit_agent_gallant
  PYTHONDONTWRITEBYTECODE=1 /lustre/home/acct-wendongwei/yejinquan/it_stu100_home/.conda/envs/lang2edit/bin/python -m pytest -q -p no:cacheprovider tests
'
```

无可用 Job 时不得退回登录节点或 Windows 测试（本机 `/d/anaconda/python.exe`
仅用于小批量冒烟）。
