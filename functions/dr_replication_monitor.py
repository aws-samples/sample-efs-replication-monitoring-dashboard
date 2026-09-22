# Extracted from template.yaml (AWS::Lambda::Function ReplicationMonitorFunction / Code.ZipFile).
# template.yaml is the single source of truth — it is what CloudFormation deploys.
# This copy exists for readability and review only; regenerate it if the template changes.

import boto3, json, time, os
from botocore.exceptions import ClientError

NS = 'DR/EFSMonitor'
FS_ID = os.environ['SOURCE_FS_ID']
R1 = os.environ['SOURCE_REGION']
R2 = os.environ['DEST_REGION']
ACCT = os.environ['ACCOUNT_ID']

def handler(event, context):
    efs = boto3.client('efs', region_name=R1)
    try:
        repl = efs.describe_replication_configurations(FileSystemId=FS_ID)['Replications'][0]
    except ClientError as e:
        if e.response['Error']['Code'] != 'ReplicationNotFound':
            raise
        # No replication configuration exists — e.g. it was deleted during a
        # failover, or SOURCE_FS_ID is wrong. Publish ERROR (-3) so the dashboard
        # shows a red signal instead of silently going stale, and log a row that
        # the Replication Configuration widget can display (it filters on source_fs).
        boto3.client('cloudwatch', region_name=R1).put_metric_data(Namespace=NS, MetricData=[
            {'MetricName': 'ReplicationStatus', 'Value': -3, 'Unit': 'None'},
            {'MetricName': 'SyncAgeSeconds', 'Value': -1, 'Unit': 'Seconds'},
        ])
        log = {
            'source_fs': f'{FS_ID} (no replication configuration)', 'source_region': R1,
            'source_dns': '-', 'source_ips': '-', 'source_permission': '-',
            'dest_fs': '-', 'dest_region': '-', 'dest_dns': '-', 'dest_ips': '-',
            'dest_permission': '-', 'replication_status': 'NOT_CONFIGURED',
            'status_message': f'No replication configuration found for {FS_ID}. '
                              'It may have been deleted, or SOURCE_FS_ID may be incorrect.',
            'last_sync': 'N/A', 'source_backups': [], 'dest_backups': [],
        }
        print(json.dumps(log))
        return log

    src_fs = repl['SourceFileSystemId']
    src_r = repl['SourceFileSystemRegion']
    dst_fs = repl['Destinations'][0]['FileSystemId']
    dst_r = repl['Destinations'][0]['Region']
    status = repl['Destinations'][0]['Status']
    status_message = repl['Destinations'][0].get('StatusMessage', '')
    last_sync = repl['Destinations'][0].get('LastReplicatedTimestamp')
    sync_age = (time.time() - last_sync.timestamp()) if last_sync else -1

    efs_s = boto3.client('efs', region_name=src_r)
    efs_d = boto3.client('efs', region_name=dst_r)
    s_info = efs_s.describe_file_systems(FileSystemId=src_fs)['FileSystems'][0]
    d_info = efs_d.describe_file_systems(FileSystemId=dst_fs)['FileSystems'][0]
    s_size = s_info['SizeInBytes']['Value']
    # Destination is ~12 MiB larger due to EFS replication metadata.
    # Subtract to normalize size comparison. Observed in testing — may vary.
    d_size = max(d_info['SizeInBytes']['Value'] - 12582912, 0)

    s_mts = efs_s.describe_mount_targets(FileSystemId=src_fs)['MountTargets']
    d_mts = efs_d.describe_mount_targets(FileSystemId=dst_fs)['MountTargets']

    backups = {}
    for lbl, rgn, fs in [('source', src_r, src_fs), ('dest', dst_r, dst_fs)]:
        bk = boto3.client('backup', region_name=rgn)
        arn = f'arn:aws:elasticfilesystem:{rgn}:{ACCT}:file-system/{fs}'
        try:
            rps = bk.list_recovery_points_by_resource(ResourceArn=arn, MaxResults=3).get('RecoveryPoints', [])
        except Exception:
            rps = []
        # Recovery point ARNs are colon-delimited (…:recovery-point:UUID),
        # so split on ':' to get the short ID for the dashboard widget.
        backups[lbl] = [{'id': r['RecoveryPointArn'].split(':')[-1], 'created': str(r['CreationDate']), 'status': r['Status']} for r in rps]

    cw = boto3.client('cloudwatch', region_name=R1)
    sv = {'ENABLED': 2, 'ENABLING': 1, 'PAUSING': 0,
          'PAUSED': -1, 'DELETING': -2, 'ERROR': -3}.get(status, -3)
    cw.put_metric_data(Namespace=NS, MetricData=[
        {'MetricName': 'ReplicationStatus', 'Value': sv, 'Unit': 'None'},
        {'MetricName': 'SyncAgeSeconds', 'Value': sync_age, 'Unit': 'Seconds'},
        {'MetricName': 'SourceSizeGB', 'Value': s_size/1073741824, 'Unit': 'Gigabytes'},
        {'MetricName': 'DestSizeGB', 'Value': d_size/1073741824, 'Unit': 'Gigabytes'},
        {'MetricName': 'SizeDiffPercent', 'Value': abs(s_size-d_size)/max(s_size,1)*100, 'Unit': 'Percent'},
        {'MetricName': 'SourceBackupCount', 'Value': len(backups['source']), 'Unit': 'Count'},
        {'MetricName': 'DestBackupCount', 'Value': len(backups['dest']), 'Unit': 'Count'},
    ])

    dst_perm = 'Read-Only' if status in ('ENABLED','ENABLING') else 'Read/Write'
    log = {
        'source_fs': f"{s_info.get('Name',src_fs)} ({src_fs})", 'source_region': src_r,
        'source_dns': f'{src_fs}.efs.{src_r}.amazonaws.com',
        'source_ips': ', '.join(m['IpAddress'] for m in s_mts), 'source_permission': 'Read/Write',
        'dest_fs': f"{d_info.get('Name',dst_fs)} ({dst_fs})", 'dest_region': dst_r,
        'dest_dns': f'{dst_fs}.efs.{dst_r}.amazonaws.com',
        'dest_ips': ', '.join(m['IpAddress'] for m in d_mts), 'dest_permission': dst_perm,
        'replication_status': status, 'status_message': status_message or '-',
        'last_sync': str(last_sync) if last_sync else 'N/A',
        'source_backups': backups['source'], 'dest_backups': backups['dest'],
    }
    print(json.dumps(log))
    return log
