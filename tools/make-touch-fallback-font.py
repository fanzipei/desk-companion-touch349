"""Build the Touch349 Unicode fallback from SIL-OFL Noto Sans CJK SC.

python tools/make-touch-fallback-font.py path/to/NotoSansCJKsc-Regular.otf
Development dependencies: tools/requirements-fonts.txt. The PC app does not use these.
Binary: TFB1, u32 glyph count, sorted <IIIHH> records (codepoint, two bitmap
offsets, two advances), then row-major MSB-first 1-bpp bitmaps for 12/16 px.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import unicodedata

from fontTools.ttLib import TTFont
import fontTools
from PIL import Image, ImageDraw, ImageFont, __version__ as pillow_version

ROOT=Path(__file__).resolve().parents[1]


def raster(font, codepoint, size):
    char=chr(codepoint)
    width=max(1, min(size*2, math.ceil(font.getlength(char))))
    image=Image.new('L',(width,size))
    # Match CJK top/bottom at each native pixel size; keep the name dot centered.
    baseline=10 if size==12 else 14
    ImageDraw.Draw(image).text((0,baseline),char,font=font,fill=255,anchor='ls',stroke_width=0)
    bitmap=bytearray((width*size+7)//8)
    for bit,value in enumerate(image.tobytes()):
        if value>=96:bitmap[bit//8]|=0x80>>(bit%8)
    return width,bytes(bitmap)


def generate(source, destination):
    with TTFont(source,lazy=True) as font:
        codepoints=sorted(cp for cp in font.getBestCmap() if cp>=128 and
                          unicodedata.category(chr(cp)) not in ('Cc','Cf','Cs','Cn'))
    fonts={size:ImageFont.truetype(str(source),size) for size in (12,16)}
    records=[];pixels=bytearray();start=8+16*len(codepoints)
    for index,cp in enumerate(codepoints):
        glyphs=[raster(fonts[size],cp,size) for size in (12,16)]
        offset12=start+len(pixels);pixels.extend(glyphs[0][1])
        offset16=start+len(pixels);pixels.extend(glyphs[1][1])
        records.append(struct.pack('<IIIHH',cp,offset12,offset16,glyphs[0][0],glyphs[1][0]))
        if index and index%10000==0:print(f'Generated {index}/{len(codepoints)} glyphs',flush=True)
    result=b'TFB1'+struct.pack('<I',len(codepoints))+b''.join(records)+pixels
    destination.write_bytes(result)
    metadata={'font':'Noto Sans CJK SC Regular','license':'SIL OFL 1.1',
              'source':'https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/SimplifiedChinese',
              'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
              'asset_sha256':hashlib.sha256(result).hexdigest(),'glyph_count':len(codepoints),
              'bytes':len(result),'native_sizes':[12,16],'scaled_sizes':[24,32],
              'format':'TFB1','pillow':pillow_version,'fonttools':fontTools.__version__}
    destination.with_suffix('.json').write_text(json.dumps(metadata,indent=2),encoding='utf8')
    print(json.dumps(metadata),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'firmware/main/touch_fallback.bin')
    args=parser.parse_args();generate(args.source,args.output)
