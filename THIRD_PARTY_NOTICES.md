# Third-party notices

The top-level Apache-2.0 license covers original project code only. These
components and fonts retain their own licenses, notices and source provenance.

## Waveshare Touch349 V2 board port

- Paths: `firmware/main/board.cpp`, `user_config.h`, `firmware/components/i2c_bsp/`, and `lcd_bl_pwm_bsp/`.
- Upstream: <https://github.com/waveshareteam/ESP32-S3-Touch-LCD-3.49-V2>, commit `1c157e6e8e68b89fd4dc400f46bf1724cb64a57e`, based on `ESP-IDF/09_LVGL_V8_Test`.
- License: Apache-2.0, retained at `LICENSES/Waveshare-Apache-2.0.txt`.
- Local changes: desktop reader rendering, stable touch release handling, landscape rotation, motion/flip logic, power controls and integration with the companion protocol.

## U8g2 and WenQuanYi

- Path: `firmware/components/u8g2/`. Source: <https://github.com/olikraus/u8g2>.
- U8g2 code: BSD-2-Clause; full upstream license is at `firmware/components/u8g2/LICENSE`. Upstream source notices are retained.
- Local changes: ESP-IDF CMake integration includes generic rendering sources and omits unused display driver implementations. The font C file retains only the used fonts; their initializers and glyph data are unchanged.
- `u8g2_font_wqy12_t_gb2312b` and `u8g2_font_wqy16_t_gb2312b`: WenQuanYi Bitmap Song, Copyright (C) 2004–2010 WenQuanYi Project Board of Trustees and Qianqian Fang. GPL v2 with font embedding exception; see `LICENSES/GPL-2.0-only.txt` and `LICENSES/WenQuanYi-font-exception.txt`.
- Font provenance and source/conversion tools: <https://github.com/olikraus/u8g2/wiki/fntgrpwqy> and <https://github.com/larryli/u8g2_wqy>.
- `u8g2_font_5x7_tf` retains its upstream public-domain notice.
- Font embedding exceptions have their own scope. Original-code licensing does not relicense the font data or make the complete firmware purely Apache-2.0.

## Noto Unicode fallback

- `firmware/main/touch_fallback.bin`: 12/16 px one-bit glyphs derived from Noto Sans CJK SC Regular. Original font copyright: © 2014–2021 Adobe (<http://www.adobe.com/>).
- License: SIL Open Font License 1.1, retained at `LICENSES/OFL.NotoSansCJK.txt`.
- Source: <https://github.com/notofonts/noto-cjk/tree/main/Sans/OTF/SimplifiedChinese>.
- Local transformation: Unicode cmap extraction and one-bit rasterization into the independently named Touch349 fallback resource, with 24/32 px scaling in the adapter. The source and asset SHA256 values are recorded in `firmware/main/touch_fallback.json`; the reproducible generator is `tools/make-touch-fallback-font.py`.

## Managed and installed dependencies

The following are downloaded or installed through the named package managers;
their distributions retain their own full licenses. They are not copied as private binaries here.

| Component | License |
| --- | --- |
| ESP-IDF | Apache-2.0 and per-component notices |
| LVGL 8.4.0 | MIT |
| Espressif AXS15231B, TCA9554 and CMake utilities | Apache-2.0 |
| Bleak | MIT |
| pystray | LGPL-3.0 |
| Pillow | MIT-CMU and retained Pillow/PIL notices |
| python-qrcode | BSD-3-Clause |
| fontTools (development only) | MIT |

WeChat integration code follows Tencent's public iLink protocol documentation:
<https://github.com/Tencent/openclaw-weixin>. No Tencent credentials, user chats,
calendar application source or external calendar data are included.
