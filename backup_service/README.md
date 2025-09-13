# Document Portal Backup Service

Purpose: Incremental (mtime+size diff) or full-archive backups from mounted EFS directories to S3.

## Modes
- Incremental (default): Upload only new/changed files using a manifest.
- Archive: Package sources into a timestamped tar.gz and upload single object.

## Configuration (YAML First)
All operational settings now come from `backup_config.yaml` (or `--config` / `BACKUP_CONFIG_PATH`).

`backup_config.yaml` excerpt (current canonical paths):
```yaml
backup:
  s3:
    bucket: "document-portal-s3"   # REQUIRED (unless overridden via --bucket)
    prefix: "backups/"
    include_dirs:
      - "/data"
      - "/logs"
    interval_seconds: 900
```

Optional CLI overrides (highest precedence): `--bucket`, `--prefix`, `--dirs`, `--interval`, `--archive`, `--no-incremental`, `--config`.

Environment variables are now minimal:
- BACKUP_CONFIG_PATH (optional path to YAML if not using --config)
- BACKUP_MANIFEST (override manifest filename; default `.backup_manifest.json`)
- LOG_LEVEL (INFO by default)
- AWS_* (only for local testing; prefer IAM role in ECS)
- API_KEYS (JSON bundle – only if reusing existing secret expansion logic)

## Examples
One-shot incremental (YAML only):
python -m backup_service.cli --config backup_config.yaml --once

Override bucket & prefix ad-hoc:
python -m backup_service.cli --config backup_config.yaml --bucket my-bucket --prefix adhoc/ --once

Archive every hour (Scheduler rule using YAML):
python -m backup_service.cli --config /app/backup_config.yaml --archive --interval 3600

## Container Mounting & Paths

You chose simplified absolute paths: `/data` and `/logs`.

Mount strategy options:
1. ECS/Fargate EFS volumes (recommended): map EFS access points to `/data` & `/logs`.
2. Docker local dev bind mounts: `-v $(pwd)/data:/data:ro -v $(pwd)/logs:/logs:ro`.
3. Kubernetes (if used later): use PersistentVolumeClaims mounted at those two paths.

Example Docker run (one-off test):
```
docker run --rm \
  -e AWS_REGION=us-east-2 \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  -v $(pwd)/backup_service/backup_config.yaml:/app/backup_config.yaml:ro \
  -v $(pwd)/data:/data:ro \
  -v $(pwd)/logs:/logs:ro \
  <acct>.dkr.ecr.<region>.amazonaws.com/document-portal-backup:latest \
  python -m backup_service.cli --config /app/backup_config.yaml --once
```

ECS Task Definition fragment (mount points updated):
```json
{
  "containerDefinitions": [
    {
      "name": "backup",
      "image": "<acct>.dkr.ecr.<region>.amazonaws.com/document-portal-backup:latest",
      "essential": true,
      "command": [
        "python","-m","backup_service.cli",
        "--config","/app/backup_config.yaml",
        "--once"
      ],
      "mountPoints": [
        {"sourceVolume": "data", "containerPath": "/data", "readOnly": true},
        {"sourceVolume": "logs", "containerPath": "/logs", "readOnly": true}
      ],
      "logConfiguration": {"logDriver": "awslogs","options": {"awslogs-group": "/ecs/document-portal-backup","awslogs-region": "<region>","awslogs-stream-prefix": "ecs"}}
    }
  ],
  "volumes": [
    {"name": "data", "efsVolumeConfiguration": {"fileSystemId": "fs-xxxx", "authorizationConfig": {"accessPointId": "fsap-data"}, "transitEncryption": "ENABLED"}},
    {"name": "logs", "efsVolumeConfiguration": {"fileSystemId": "fs-xxxx", "authorizationConfig": {"accessPointId": "fsap-logs"}, "transitEncryption": "ENABLED"}}
  ]
}
```

## ECS Task Definition Command Array
["python","-m","backup_service.cli","--config","/app/backup_config.yaml","--once"]

## IAM Policy Snippet
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::my-bucket"},
    {"Effect":"Allow","Action":["s3:PutObject","s3:AbortMultipartUpload","s3:DeleteObject"],"Resource":"arn:aws:s3:::my-bucket/backups/*"}
  ]
}

## EventBridge (Scheduled ECS Task) Integration

### 1. Build & Push Image
Tag and push the backup image (from repo root if you keep monorepo):
```
docker build -f backup_service/Dockerfile -t <acct>.dkr.ecr.<region>.amazonaws.com/document-portal-backup:latest backup_service
docker push <acct>.dkr.ecr.<region>.amazonaws.com/document-portal-backup:latest
```

### 2. Task Definition (Fargate)
Minimal container JSON excerpt (mount same EFS paths as main app):
```json
{
  "family": "document-portal-backup",
  "networkMode": "awsvpc",
  "requiresCompatibilities": ["FARGATE"],
  "cpu": "256",
  "memory": "512",
  "executionRoleArn": "arn:aws:iam::<acct>:role/ecsTaskExecutionRole",
  "taskRoleArn": "arn:aws:iam::<acct>:role/document-portal-backup-task-role",
  "containerDefinitions": [
    {
      "name": "backup",
      "image": "<acct>.dkr.ecr.<region>.amazonaws.com/document-portal-backup:latest",
      "essential": true,
      "command": [
        "python","-m","backup_service.cli",
        "--bucket","my-bucket",
        "--dirs","/app/data,/app/logs",
        "--prefix","backups/",
        "--once"
      ],
      "logConfiguration": {"logDriver": "awslogs","options": {"awslogs-group": "/ecs/document-portal-backup","awslogs-region": "<region>","awslogs-stream-prefix": "ecs"}},
      "mountPoints": [
        {"sourceVolume": "data", "containerPath": "/app/data", "readOnly": true},
        {"sourceVolume": "logs", "containerPath": "/app/logs", "readOnly": true}
      ]
    }
  ],
  "volumes": [
    {"name": "data", "efsVolumeConfiguration": {"fileSystemId": "fs-xxxx", "authorizationConfig": {"accessPointId": "fsap-data"}, "transitEncryption": "ENABLED"}},
    {"name": "logs", "efsVolumeConfiguration": {"fileSystemId": "fs-xxxx", "authorizationConfig": {"accessPointId": "fsap-logs"}, "transitEncryption": "ENABLED"}}
  ]
}
```

### 3. IAM Roles
Task Role (attach S3 policy above). Scheduler Role (trust: `scheduler.amazonaws.com`) needs:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect":"Allow","Action":["ecs:RunTask"],"Resource":"arn:aws:ecs:<region>:<acct>:task-definition/document-portal-backup:*"},
    {"Effect":"Allow","Action":["iam:PassRole"],"Resource":["arn:aws:iam::<acct>:role/ecsTaskExecutionRole","arn:aws:iam::<acct>:role/document-portal-backup-task-role"]}
  ]
}
```

### 4. EventBridge Scheduler (Recommended)
Create schedule (hourly example):
```
aws scheduler create-schedule \
  --name document-portal-backup-hourly \
  --schedule-expression "rate(1 hour)" \
  --flexible-time-window Mode=OFF \
  --target "Arn=arn:aws:ecs:<region>:<acct>:cluster/<cluster>,RoleArn=arn:aws:iam::<acct>:role/document-portal-scheduler-role,EcsParameters={LaunchType=FARGATE,PlatformVersion=LATEST,TaskDefinitionArn=arn:aws:ecs:<region>:<acct>:task-definition/document-portal-backup:1,NetworkConfiguration={AwsvpcConfiguration={Subnets=[subnet-1,subnet-2],SecurityGroups=[sg-xxx],AssignPublicIp=DISABLED}}}"
```
Cron alternative (daily 02:15 UTC): `cron(15 2 * * ? *)`

### 5. Legacy CloudWatch Events Rule (Optional)
```
aws events put-rule --name document-portal-backup-daily --schedule-expression "cron(15 2 * * ? *)"
aws events put-targets --rule document-portal-backup-daily --targets '[{"Id":"backup","Arn":"arn:aws:ecs:<region>:<acct>:cluster/<cluster>","EcsParameters":{"TaskDefinitionArn":"document-portal-backup","LaunchType":"FARGATE","NetworkConfiguration":{"awsvpcConfiguration":{"subnets":["subnet-1"],"securityGroups":["sg-xxx"],"assignPublicIp":"DISABLED"}}},"RoleArn":"arn:aws:iam::<acct>:role/document-portal-scheduler-role"}]'
```

### 6. Concurrency / Overlap Protection
Default schedule fires a new task even if previous still running. Mitigation options:
- Keep runtime << interval (design goal).
- Use manifest incremental mode (idempotent uploads).
- Add a lightweight lock: have task create an object `backups/lock` (S3 PutObject with if-none-match) – not implemented here.

### 7. Parameter Changes Without Rebuild
Edit the mounted `backup_config.yaml` (if writable via an EFS-backed config directory) or override via task `command` flags (`--interval 3600`, `--bucket other-bucket`). For purely static images, redeploy with a new config layer baked in.

### 8. Testing the Schedule
Manual dry run:
```
aws ecs run-task \
  --cluster <cluster> \
  --launch-type FARGATE \
  --task-definition document-portal-backup \
  --network-configuration '{"awsvpcConfiguration":{"subnets":["subnet-1"],"securityGroups":["sg-xxx"],"assignPublicIp":"DISABLED"}}'
```

### 9. Observability
Check CloudWatch Logs group `/ecs/document-portal-backup` for JSON lines containing: `backup_completed`, `file_uploaded`, `backup_archive_uploaded`.

### 10. Failure Handling
Scheduler will try each invocation independently. For alerting, add a CloudWatch Metric Filter on `"file_upload_failed"` or set up an EventBridge rule matching ECS Task State Change -> FAILED for this task family.

## Local Development & Testing (uv)

### 1. Sync Environment
Run inside the `backup_service` directory:
```
cd backup_service
uv sync
```
This creates/updates an isolated virtual environment from `pyproject.toml` (no global installs).

### 2. Prepare Sample Data (run from repo root or provide absolute paths)
```
mkdir -p data logs
echo "hello" > data/example.txt
```

### 3. One-Shot Incremental Backup (uses YAML values)
```
uv run -m backup_service.cli --config backup_config.yaml --once
```

### 4. Archive (Tar.gz) Snapshot
```
uv run -m backup_service.cli --config backup_config.yaml --archive --once
```

### 5. Periodic Loop (60s)
```
uv run -m backup_service.cli --config backup_config.yaml --interval 60
```
Interrupt with Ctrl+C.

### 6. Inspect Manifest
After an incremental run open `.backup_manifest.json` to confirm entries (mtime, size, sha256 for changed files).

### 7. Simulate File Change
```
echo "more" >> data/example.txt
uv run -m backup_service.cli --config backup_config.yaml --once
```
Only the modified file should re-upload.

### 8. Clean Environment (optional)
```
uv pip uninstall document-portal-backup -y  # if previously installed editable elsewhere
```

### 9. Success Checklist
- Exit code = 0
- JSON logs show `backup_completed`
- S3 objects under chosen prefix

### 10. Quick Smoke (No Upload) Trick
Temporarily change the bucket in `backup_config.yaml` to an invalid name and run:
```
uv run -m backup_service.cli --config backup_config.yaml --once
```
Expect `file_upload_failed` events and a final `backup_completed`.
