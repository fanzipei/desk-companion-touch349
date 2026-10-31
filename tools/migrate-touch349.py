"""Seed the new screen before letting the PC adopt its state.

Run with the desktop companion stopped. Only an explicitly selected Touch349
device is accepted. No existing screen notes are evicted to make space.
"""
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bleak import BleakClient
from pc.core import Repository
from pc.display import display_packet
from pc.protocol import Protocol

ROOT=Path(__file__).resolve().parents[1]
(ROOT/'logs').mkdir(exist_ok=True)


async def migrate(address):
    directory=ROOT/'backups/touch349-companion'
    directory.mkdir(parents=True,exist_ok=True)
    repository=Repository(ROOT/'data')
    snapshot=repository.snapshot()
    stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
    (directory/f'pc-state-{stamp}.json').write_text(json.dumps(snapshot,ensure_ascii=False,indent=2),encoding='utf8')
    report={'address':address,'before_notes':len(snapshot['notes'])}
    async with BleakClient(address,timeout=15,winrt={'use_cached_services':False}) as client:
        protocol=Protocol(client,lambda event:None)
        await protocol.start()

        async def request(packet):
            reply=await protocol.request(packet,timeout=90)
            if not reply.get('ok'): raise RuntimeError(f"{packet['op']} failed: {reply.get('error')}")
            return reply

        state=await request({'op':'state'})
        if state.get('hardware')!='waveshare-touch-lcd-3.49-v2':
            raise RuntimeError('Refusing migration to a different board')
        existing={note['id']:note for note in state['notes']}
        required=len(set(existing)|{note['id'] for note in snapshot['notes']})
        if required>20: raise RuntimeError('Insufficient free slots; existing notes will not be evicted')
        for note in snapshot['notes']:
            if note['id'] in existing and existing[note['id']]['text']!=note['text']:
                raise RuntimeError('Conflicting text for an existing note; preserved both backups for review')
        await request({'op':'config','note_limit':max(required,snapshot['config']['note_limit'])})
        await request({'op':'time_sync','unix':int(datetime.now().timestamp())})
        await request(display_packet(snapshot['config']))
        for note in reversed(snapshot['notes']):
            if note['id'] not in existing:
                reply=await request({'op':'add',**note})
                if reply.get('evicted'): raise RuntimeError('Unexpected eviction; stop migration')
        for note in snapshot['notes']:
            await request({'op':'pin','id':note['id'],'pinned':note.get('pinned',False)})
        news=repository.news_packet()
        reply=await protocol.sync_news(news)
        if not reply.get('ok'): raise RuntimeError(f'News migration failed: {reply}')
        await request({'op':'mode','mode':snapshot.get('mode',0)})
        final=await request({'op':'state'})
        actual={n['id']:n for n in final['notes']}
        for note in snapshot['notes']:
            assert actual[note['id']]['text']==note['text']
            assert actual[note['id']]['pinned']==note.get('pinned',False)
        report.update(notes_verified=len(snapshot['notes']),news=final['news_count'])
    # The new device is fully populated before the bridge's apply_state can run.
    repository.configure(device_address=address)
    report['passed']=True
    (ROOT/'logs/touch349-migration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--address',required=True)
    args=parser.parse_args()
    asyncio.run(migrate(args.address))
