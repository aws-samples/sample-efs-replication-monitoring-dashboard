# Extracted from template.yaml (AWS::Lambda::Function FileValidatorDestFunction / Code.ZipFile).
# template.yaml is the single source of truth — it is what CloudFormation deploys.
# This copy exists for readability and review only; regenerate it if the template changes.

import os, hashlib, json

EFS_MOUNT = '/mnt/efs'

def handler(event, context):
    rel_path = event.get('last_modified_file', '')
    source_checksum = event.get('checksum_sha256')
    source_size = event.get('file_size_bytes', 0)

    dest_path = EFS_MOUNT + rel_path
    exists = os.path.exists(dest_path)

    dest_checksum, dest_size = None, 0
    if exists:
        dest_size = os.path.getsize(dest_path)
        h = hashlib.sha256()
        with open(dest_path, 'rb') as fh:
            for chunk in iter(lambda: fh.read(8192), b''):
                h.update(chunk)
        dest_checksum = h.hexdigest()

    checksum_match = (dest_checksum == source_checksum) if source_checksum and dest_checksum else False

    total_files, total_size = 0, 0
    for root, _, files in os.walk(EFS_MOUNT):
        for f in files:
            total_files += 1
            try:
                total_size += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue

    result = {
        'role': 'destination',
        'file': rel_path,
        'exists': exists,
        'source_checksum': source_checksum,
        'dest_checksum': dest_checksum,
        'checksum_match': checksum_match,
        'dest_file_size': dest_size,
        'source_file_size': source_size,
        'total_files': total_files,
        'total_size_bytes': total_size,
    }
    print(json.dumps(result))
    return result
