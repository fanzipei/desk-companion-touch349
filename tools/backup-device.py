"""Verified, retryable full flash read using the ESP-IDF Python environment."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import time
import esptool


def backup(port, output):
    output=Path(output)
    if output.exists(): raise RuntimeError('Backup exists; it will not be overwritten')
    output.parent.mkdir(parents=True,exist_ok=True)
    pending=output.with_suffix('.pending.bin')
    if pending.exists(): raise RuntimeError('A pending backup exists; preserve it before restarting')
    total=16*1024*1024
    device=None
    identity=None
    try:
        with pending.open('wb') as stream:
            for offset in range(0,total,0x20000):
                for attempt in range(5):
                    try:
                        if device is None:
                            device=esptool.detect_chip(port,baud=115200)
                            device=device.run_stub()
                            device.change_baud(460800)
                            current=':'.join(f'{b:02x}' for b in device.read_mac())
                            if identity is not None and identity!=current: raise RuntimeError('Device identity changed')
                            identity=current
                        data=device.read_flash(offset,0x20000)
                        expected=device.flash_md5sum(offset,len(data)).lower()
                        if len(data)!=0x20000 or hashlib.md5(data).hexdigest()!=expected:
                            raise RuntimeError('Flash chunk checksum mismatch')
                        stream.write(data); stream.flush()
                        print(f'BACKUP {offset+len(data)}/{total}',flush=True)
                        break
                    except Exception as exc:
                        print(f'RETRY offset={offset:x} attempt={attempt+1} {exc}',flush=True)
                        if device:
                            device._port.close(); device=None
                        if attempt==4: raise
                        time.sleep(0.5)
            digest=device.flash_md5sum(0,total).lower()
        raw=pending.read_bytes()
        if len(raw)!=total or hashlib.md5(raw).hexdigest()!=digest:
            raise RuntimeError('Full flash checksum mismatch')
        pending.rename(output)
        manifest={'mac':identity,'port':port,'bytes':total,'sha256':hashlib.sha256(raw).hexdigest(),
                  'flash_md5':digest,'verified':True,'created':datetime.now().astimezone().isoformat()}
        output.with_suffix('.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
        print(json.dumps(manifest),flush=True)
    finally:
        if device:
            device.hard_reset(); device._port.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--port',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    backup(args.port,args.output)
