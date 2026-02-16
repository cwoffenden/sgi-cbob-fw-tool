# SGI Scalable Graphics Compositor Firmware Tool

Tool to patch SGI [Scalable Graphics Compositor](//irix7.com/techpubs/007-4602-002.pdf) (CBOB) PROM images with data extracted from a `sting` error log.

CBOBs were found with newer PROM versions than distributed with IRIX, and no known OS patches exist to upgrade existing models. This tool takes advantage of the `sting` hardware diagnostic tool's PROM verification, which when run with the `MAX_ERRORS` flag will generate a list of differences between the hardware and the PROM files. Patch data can be generated from the logs, applied to the known good PROM images, then the patched PROM written out. Using the generated PROM files for a further `sting` verification should show no errors, meaning the operation was successful and the resulting files could be used to perform upgrades.

Besides being the tool written to perform this one-time conversion, this is also the handwritten example referred to in a [vibe-coding challenge](//github.com/numfum/task-fw-patcher) set to see how LLMs handle such tasks. Results and findings of this at the end.

### Using

With a CBOB connected and recognised by IRIX (not just `hinv` but also accessible from `sgcombine`), generate the `sting` log, either redirecting to a file or saving the terminal session:
```
# cd /usr/diags/compositor/bin
# ./sting MAX_ERRORS=20000
```
The section of interest is `PROMread_test`. This patch script then takes one of the PROM images, the log file, and a destination filename:
```
./patch-mcs --ctx ctx1.mcs --log sting-2026-01-24.log --out patched-ctx1.mcs
```
Replacing the `.mcs` files in `/usr/gfx/ucode/STINGRAY/xlx/`, updating the (binary) `VERSION` file with the newer `1.2.3` and `1.0.1` version numbers (each being bumped by `.1`), then running `sting` again should result in zero read test errors.

### Design

Ordinarily, it would be enough to present the tool and the finished results, but, and switching to the first person, I'll explain my approach to solving this with brainpower (and tea), before looking at various LLM efforts.

1. Familiarise myself with the PROM images; these are the starting point. They're in [Intel Hex format](//en.wikipedia.org/wiki/Intel_HEX), not binary as one might expect, and have the oddity of Windows line endings on a Unix machine. The first step is to read then write exactly the same data, maintaining the `I16HEX` file variant and `CRLF` endings, resulting in a bit-correct copy.
2. Understand and parse the `sting` log. I decided early on that a project around text processing would be best suited to Python, so I [interactively wrote](//regex101.com/) a few regexes to extract the mismatch *errors*.

I looked at different Python libraries for dealing with Intel Hex files, initially choosing [`intexlhex`](//github.com/python-intelhex/intelhex) because of its ability to directly write to a file. A simple 10-line test reader/writer was written, before noticing that writing only supports the newer `I32HEX` (verified in the source). `I16HEX` is writable by switching to [`bincopy`](//github.com/eerimoq/bincopy) though with its own caveats, but with a few tweaks it was quickly adapted to make an SGI-compatible writer (see [`write_compatible_mcs`](patch-mcs.py#L235)).

Parsing the log content was straightforward, and I [littered the comments](patch-mcs.py#L33) with its layout and pitfalls (for example, [how zero is represented](patch-mcs.py#L66), and that values are [big-endian](patch-mcs.py#L88)). For each of the `ERROR` entries I created a patch of address, expected value (from the PROM file) and actual value (from the CBOB). So far so good, time to verify that the expected values match the PROM files... boom! They don't, for multiple reasons it turns out. The simplest issue solved was `CTX` errors starting at `0x188294` (approx. `1.5MB`), for a PROM file that's `76kB` bytes long (`CTX` addresses are offset by `0x188000`, a value eventually found in a binary `VERSIONS` file alongside the `.mcs` files). But even knowing this offset the expected values still didn't match. Solving this required cycling home in the dark pondering the issue.

### Offsets

No matter what, the memory addresses drifted. I tried brute forcing a search, and whilst this mostly worked, it wasn't 100% (and would certainly fail with a smaller log file containing fewer errors). So I started manually mapping out the values, with entries of interest surrounded by `[]`:
```
0100 01000000 00000000[BF530300]00000000 D9 <-- 0xF530300 estimated at 0x188200
0110 00000000 00000000 00000000 00000000 DF
0120 00000000 00000000 00000000 0000FC6F 64
0130 05000000 00000000 FF530100 00000000 67
0140 00000000 00000000 00000000 00000000 AF
0150 00000000 00000000 00000000 0000FC6F 34
0160 05000000 00000000 00C80100 00000000 C1
0170 00000000 00000000 00000000 00000000 7F
0180 00000000 00000000 00000000 00000020 4F
0190 01000000 00000000 BD480006[00180060]DB <-- Logged 0x00180060 at 0x188294
01A0 00800100 06001E00 7801E001 80070016 B3
01B0 00380540 15805100 54015805 6004FC23 A7
01C0 00000000 00000000 03A03B00 F700ECA3 CB
01D0 B0CFC03E 02FB00FC 00B003C0 3F00F300 04
01E0 FC03F08F D30E18FF 04FC03B0 0FC00800 0F
01F0 0E000000 00000000 01083F00 F102841B 17

0200 106F40BC 03C10074 00D00040[3F00D100]1B <-- Logged 0x3f00d100 at 0x188304
0210[7403D04E]4004004D 0E340310 0DC00620 70 <-- Logged 0x7403d04e at 0x188400
0220 0C000000 00000000 10A03300 C508241B D3     (crossed 264 boundary)
0230 D02C4032 10C90036 00D00040 3300C500 39
0240 3403D04C 400200CD 343403D0 0C404C80 F9
0250 0E000000 00000000 03A83500 D1206403 58
0260 100D4034 00D14074 13D00140 3300D508 44
0270 7603D00D 4406005D 00740310 0D400220 8B
0280 06000000 00000000 02A83700 D7006C03 41
0290 F00DC036 08DB0078 14B00DC1 3F40D781 A7
02A0 7C03F00E C0D611FF 137C03F0 0DC00CA2 2E
02B0 04000000 00000000 07803940 EF00DC03 6C
02C0 F00FC03F 00FF00FC 80F09FC0 3F00FB01 2B
02D0 FC03F00F C07D02FD 05FC03F0 0EC03300 EF
02E0 06000000 00000000 03083500 D3405C03 56
02F0 700CC035 00D7004C 88F025C0[3600D300]04 <-- Logged 0x3600d300 at 0x1884ec

0300[4D03D00D]C09518DF 025D0330 0DC00F20 E6 <-- Logged 0x4d03d00d at 0x1884f0
0310 04000000 00000000[13A03C00]F100C403 32 <-- Logged 0x13a03c00 at 0x188600 
0320 100F403C 20D10044 03D05540 7C01D300 45
0330 4403D00F 403400DD 00440310 0F406B00 35
0340 02000000 00000000 07A03200 C1005403 BA
0350 500C4035 00C50034 43501040 F204C900 31
0360 3403D01C 4021008D 00540310 0C401F00 AA
0370 0A000000 00000000 06807800 C1018407 28
0380 101E4071 02E101B6 04C01640 7C00E101 7C
0390 B407D89E 4168102C 01840710 1E403B00 12
03A0 02000000 00000000 12103000 C3001C03 17
03B0 500DC031 22C7083C 037088C0 3200CB00 0A
03C0 3C23F00C C031008C 001E0330 0CC04B40 AD
03D0 00000000 00000000 02B83D00 FF10FC031 8
03E0 F00FC03E 0AFF80CC 03F00FC2 3B40EF00 8D
03F0 CC03F02D 403F02FF 08FC23F4 8FC00B60 BC

0400 06000000 00000000 15A03703 DF047C0B 8D
0410 B06DC0B7 01D3007C 53F049C1[3701DF00]94 <-- Logged 0x3701df00 at 0x188704
0420[7C03F09D]C00400DF 004C03F0 0DC85700 B2 <-- Logged 0x7c03f09d at 0x188800
```
Only once this was written out could I spot the pattern of `512` byte blocks and 264 byte boundaries. The magic being, excusing Python's lack of hex literals:
```
patch.offset = (((patch.offset & ~511) - offset_addr) >> 9) * 264 + (patch.offset & 511)
```
This worked, so now I could map the list of patches to their expected values as along as I knew the start offset. Since I'd written code to brute force matches, I refactored it to work out the offset (advancing in `264` byte chunks).

The pieces were all now in place, reader, verifier, patcher, writer. I took the time to add an option to *poke* bytes at select addresses (more [later](#poking)) and called it done. Roughly two days, 191 lines of Python (a language I don't really use) with an additional 126 lines of comments (pitfalls and reasons).

### Poking

Getting familiar with the PROM files, diagnostic tools and logs, I noticed the the numbers don't make sense. `ctx1.mcs` has `19682` 4-byte words, but the log claims to verify only 10% of that:
```
INFO                --- 1507 missmatches / 1980 words checked
```
The tool's `--poke` option was written to overwrite any address with a value, to verify that that a bunch of random changes fail verification (using a CBOB with the known production PROMs matching the `.mcs` files).

To be continued... once I plug everything back in (the SGI was moved in the meantime).
