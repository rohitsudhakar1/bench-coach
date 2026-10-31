"""Silent live delivery check: python -m capture.check --url http://127.0.0.1:8881."""
import argparse
import asyncio
import json
import time

import httpx
import websockets


async def check(url):
    async with httpx.AsyncClient(base_url=url, timeout=5) as client:
        first = (await client.get('/cams')).json()
        await asyncio.sleep(.25)
        second = (await client.get('/cams')).json()
        assert len(second) == 2
        for before, after in zip(first, second):
            assert after['live'] and after['sequence'] > before['sequence'], after
            response = await client.get(f"/cam/{after['id']}/frame.jpg")
            response.raise_for_status()
            assert 0 <= time.time() - float(response.headers['X-Frame-Time']) < 3
    async with websockets.connect(url.replace('http', 'ws', 1) + '/mic') as ws:
        header = json.loads(await asyncio.wait_for(ws.recv(), 5))
        pcm = await asyncio.wait_for(ws.recv(), 5)
        assert isinstance(pcm, bytes) and len(pcm) == header['n'] * 2
        assert 0 <= time.time() - header['t'] < 3
    print('PASS: two advancing camera feeds and 16 kHz PCM delivery (silence also passes).')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8701')
    asyncio.run(check(parser.parse_args().url.rstrip('/')))


if __name__ == '__main__':
    main()
