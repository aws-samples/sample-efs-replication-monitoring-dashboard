# Extracted from template.yaml (AWS::Lambda::Function FileMonitorSourceFunction / Code.ZipFile).
# template.yaml is the single source of truth — it is what CloudFormation deploys.
# This copy exists for readability and review only; regenerate it if the template changes.

import os, hashlib, json, time

EFS_MOUNT = '/mnt/efs'

def handler(event, context):
    latest_file, latest_mtime = None, 0
    total_files, total_size = 0, 0

    for root, _, files in os.walk(EFS_MOUNT):
        for f in files:
            path = os.path.join(root, f)
            try:
                stat = os.stat(path)
                total_files += 1
                total_size += stat.st_size
                if stat.st_mtime > latest_mtime:
                    latest_mtime, latest_file = stat.st_mtime, path
            except OSError:
                continue

    checksum, file_size = None, 0
    if latest_file:
        file_size = os.path.getsize(latest_file)
        h = hashlib.sha256()
        with open(latest_file, 'rb') as fh:
            for chunk in iter(lambda: fh.read(8192), b''):
                h.update(chunk)
        checksum = h.hexdigest()

    rel_path = latest_file.replace(EFS_MOUNT, '') if latest_file else 'N/A'
    age = time.time() - latest_mtime if latest_mtime else -1

    result = {
        'role': 'source',
        'last_modified_file': rel_path,
        'last_modified_time': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(latest_mtime)) if latest_mtime else 'N/A',
        'file_size_bytes': file_size,
        'checksum_sha256': checksum,
        'total_files': total_files,
        'total_size_bytes': total_size,
        'last_modified_file_age': age,
    }
    print(json.dumps(result))
    return result
