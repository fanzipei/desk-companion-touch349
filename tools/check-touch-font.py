"""Host regression: compile the actual LVGL adapter and audit glyph pixels."""
from pathlib import Path
from collections import Counter
import json
import os
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
SHIM='''#pragma once
#include <stdint.h>
#include <stdbool.h>
typedef struct {uint16_t adv_w,box_w,box_h;int16_t ofs_x,ofs_y;uint8_t bpp;} lv_font_glyph_dsc_t;
typedef struct lv_font_t lv_font_t;
struct lv_font_t {bool (*get_glyph_dsc)(const lv_font_t*,lv_font_glyph_dsc_t*,uint32_t,uint32_t);
 const uint8_t *(*get_glyph_bitmap)(const lv_font_t*,uint32_t);uint16_t line_height;};
'''


def check():
    if os.name=='nt' and Path('C:/msys64/ucrt64/bin/gcc.exe').is_file():
        os.environ['PATH']='C:/msys64/ucrt64/bin;'+os.environ['PATH']
    compiler=shutil.which('gcc')
    if not compiler:raise SystemExit('Install GCC (MSYS2 UCRT64 on Windows).')
    (ROOT/'logs').mkdir(exist_ok=True)
    source=ROOT/'firmware/components/u8g2/csrc'
    files=[path for path in source.glob('*.c') if (path.name.startswith('u8g2_') or path.name.startswith('u8x8_'))
           and path.name!='u8g2_d_setup.c' and not path.name.startswith('u8x8_d_')]
    samples='?·・‧•∙⋅娅圳辍—–臺灣祎喆𠮷éñ€™®'
    whitespace=' \u00a0\u2007\u2009\u202f'
    controls='\u200b\u200c\u200d\u200e\u200f\u202a\u202e\u2060\u2066\u2069\ufeff\ufe0f'
    sizes=(12,16,24,32)
    points={ord(c) for c in samples+whitespace+controls}|{0x10fffd}
    private_texts=[]
    if (ROOT/'data/state.json').exists():
        state=json.loads((ROOT/'data/state.json').read_text(encoding='utf8'))
        private_texts=[item['text'] for item in state['notes']]+[item.get(key,'') for item in state['news'] for key in ('title','title_zh','summary')]
        points.update(ord(c) for c in ''.join(private_texts) if ord(c)>=32)
    with tempfile.TemporaryDirectory(prefix='touch-font-host-') as directory:
        temp=Path(directory)
        (temp/'lvgl.h').write_text(SHIM,encoding='utf8')
        shutil.copy2(ROOT/'firmware/main/touch_fallback.bin',temp/'touch_fallback.bin')
        section='.section .rdata,"dr"' if os.name=='nt' else '.section .rodata'
        (temp/'asset.S').write_text(section+'''
.global _binary_touch_fallback_bin_start
.global _binary_touch_fallback_bin_end
_binary_touch_fallback_bin_start:
.incbin "touch_fallback.bin"
_binary_touch_fallback_bin_end:
''',encoding='ascii')
        binary=temp/'check.exe'
        command=[compiler,'-O2','-DU8G2_USE_LARGE_FONTS','-DU8X8_WITH_USER_PTR',
                 '-I',str(temp),'-I',str(source),'-I',str(ROOT/'firmware/main'),
                 str(ROOT/'tests/touch_font_host.c'),str(ROOT/'firmware/main/touch_font.c'),
                 str(temp/'asset.S'),*[str(path) for path in files],'-o',str(binary)]
        subprocess.run(command,cwd=temp,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        queries=[(cp,size) for cp in sorted(points) for size in sizes]
        result=subprocess.run([str(binary)],input=''.join(f'{cp} {size}\n' for cp,size in queries),
                              text=True,check=True,stdout=subprocess.PIPE)
        glyphs={}
        for line in result.stdout.splitlines():
            cp,size,known,advance,width,height,*bits=line.split()
            glyphs[int(cp),int(size)]={'known':bool(int(known)),'advance':int(advance),'width':int(width),
                                      'height':int(height),'bits':bytes.fromhex(bits[0]) if bits else b''}
        def pixels(glyph):
            return [bool(glyph['bits'][i//8] & (0x80>>(i%8))) for i in range(glyph['width']*glyph['height'])]
        for char in samples:
            for size in sizes:
                value=glyphs[ord(char),size]
                assert value['known'] and any(value['bits']),(char,size)
                if char!='?':assert value!=glyphs[ord('?'),size],(char,size,'replaced by question mark')
        for char in whitespace:
            for size in sizes:
                value=glyphs[ord(char),size]
                assert value['known'] and value['advance']>0 and not any(value['bits'])
        for char in controls:
            for size in sizes:
                value=glyphs[ord(char),size]
                assert value['known'] and value['advance']==0 and not value['bits']
        for char in samples:
            for small,large in ((12,24),(16,32)):
                before,after=glyphs[ord(char),small],glyphs[ord(char),large]
                assert after['advance']==before['advance']*2
                src,dst=pixels(before),pixels(after)
                assert all(dst[y*after['width']+x]==src[(y//2)*before['width']+x//2]
                           for y in range(after['height']) for x in range(after['width']))
        for size in sizes:
            assert not glyphs[0x10fffd,size]['known']
            assert glyphs[0x10fffd,size]!=glyphs[ord('?'),size]
        missing=Counter(c for c in ''.join(private_texts) if ord(c)>=32 and not glyphs[ord(c),16]['known'])
        assert not missing,dict(missing)
        # Export only synthetic text, never any user's notes or news content.
        from PIL import Image,ImageDraw
        preview=Image.new('RGB',(820,230),'#f4f6f8');draw=ImageDraw.Draw(preview)
        for row,size in enumerate(sizes):
            x=8;y=8+row*54
            for char in samples:
                value=glyphs[ord(char),size];data=pixels(value)
                for gy in range(value['height']):
                    for gx in range(value['width']):
                        if data[gy*value['width']+gx]:draw.point((x+gx,y+gy),fill='#16212e')
                x+=value['advance']+3
        preview.save(ROOT/'logs/touch-font-host-preview.png')
        report={'passed':True,'glyph_size_checks':len(queries),'current_content_missing':dict(missing),
                'sizes':sizes,'spacing_and_invisible_characters':True,'supplementary_cjk':True,
                'scaled_bitmaps_match':True,'unknown_is_box_not_question':True}
        (ROOT/'logs/touch-font-host-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':check()
