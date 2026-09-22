# Extracted from template.yaml (AWS::Lambda::Function FileOrchestratorFunction / Code.ZipFile).
# template.yaml is the single source of truth — it is what CloudFormation deploys.
# This copy exists for readability and review only; regenerate it if the template changes.

import boto3, json, os
from botocore.exceptions import ClientError

NAMESPACE = 'DR/EFSMonitor'
FS_ID = os.environ['SOURCE_FS_ID']
REGION_1 = os.environ['SOURCE_REGION']
REGION_2 = os.environ['DEST_REGION']
MONITOR_FN = os.environ['MONITOR_FN']
VALIDATOR_FN = os.environ['VALIDATOR_FN']

def handler(event, context):
    efs = boto3.client('efs', region_name=REGION_1)
    try:
        repl = efs.describe_replication_configurations(FileSystemId=FS_ID)['Replications'][0]
    except ClientError as e:
        if e.response['Error']['Code'] != 'ReplicationNotFound':
            raise
        # Without a replication configuration there is no direction to detect,
        # so skip the file checks rather than fail. The replication monitor
        # publishes ReplicationStatus = -3, which surfaces this on the dashboard.
        out = {'error': 'no_replication_configuration', 'file_system_id': FS_ID,
               'detail': 'Replication is not configured; skipping file validation.'}
        print(json.dumps(out))
        return out

    src_region = repl['SourceFileSystemRegion']
    dst_region = repl['Destinations'][0]['Region']

    # --- Source file monitor ---
    src_result = {}
    try:
        src_lambda = boto3.client('lambda', region_name=src_region)
        src_resp = src_lambda.invoke(FunctionName=MONITOR_FN, Payload='{}')
        src_result = json.loads(src_resp['Payload'].read())
        if 'errorMessage' in src_result:
            print(json.dumps({'error': 'source_monitor_failed', 'detail': src_result}))
            src_result = {'total_files': 0, 'total_size_bytes': 0, 'last_modified_file_age': -1}
    except Exception as e:
        print(json.dumps({'error': 'source_monitor_exception', 'detail': str(e)}))
        src_result = {'total_files': 0, 'total_size_bytes': 0, 'last_modified_file_age': -1}

    # --- Destination file monitor (fire-and-forget for logging) ---
    # InvocationType='Event' keeps this off the critical path. Its output is only
    # read from CloudWatch Logs by the dashboard, never by this function, and a
    # synchronous call would block while the destination re-reads and checksums
    # the newest file — time that scales with file size.
    dst_lambda = boto3.client('lambda', region_name=dst_region)
    try:
        dst_lambda.invoke(FunctionName=MONITOR_FN, InvocationType='Event', Payload='{}')
    except Exception as e:
        print(json.dumps({'error': 'dest_monitor_exception', 'detail': str(e)}))

    # --- Destination file validator ---
    dst_result = {}
    try:
        dst_resp = dst_lambda.invoke(FunctionName=VALIDATOR_FN, Payload=json.dumps(src_result))
        dst_result = json.loads(dst_resp['Payload'].read())
        if 'errorMessage' in dst_result:
            print(json.dumps({'error': 'dest_validator_failed', 'detail': dst_result}))
            dst_result = {'exists': False, 'checksum_match': False, 'total_files': 0, 'total_size_bytes': 0}
    except Exception as e:
        print(json.dumps({'error': 'dest_validator_exception', 'detail': str(e)}))
        dst_result = {'exists': False, 'checksum_match': False, 'total_files': 0, 'total_size_bytes': 0}

    # --- Publish source metrics ---
    if isinstance(src_result, dict):
        boto3.client('cloudwatch', region_name=src_region).put_metric_data(Namespace=NAMESPACE, MetricData=[
            {'MetricName': 'LastModifiedFileAge', 'Value': src_result.get('last_modified_file_age', -1), 'Unit': 'Seconds'},
            {'MetricName': 'SourceFileCount', 'Value': src_result.get('total_files', 0), 'Unit': 'Count'},
            {'MetricName': 'SourceTotalSizeMB', 'Value': src_result.get('total_size_bytes', 0) / (1024*1024), 'Unit': 'Megabytes'},
        ])

    # --- Publish destination metrics ---
    if isinstance(dst_result, dict):
        boto3.client('cloudwatch', region_name=dst_region).put_metric_data(Namespace=NAMESPACE, MetricData=[
            {'MetricName': 'FileExistsOnDest', 'Value': 1 if dst_result.get('exists') else 0, 'Unit': 'None'},
            {'MetricName': 'ChecksumMatch', 'Value': 1 if dst_result.get('checksum_match') else 0, 'Unit': 'None'},
            {'MetricName': 'DestFileCount', 'Value': dst_result.get('total_files', 0), 'Unit': 'Count'},
            {'MetricName': 'DestTotalSizeMB', 'Value': dst_result.get('total_size_bytes', 0) / (1024*1024), 'Unit': 'Megabytes'},
        ])

    output = {'source': src_result, 'destination': dst_result}
    print(json.dumps(output))
    return output
