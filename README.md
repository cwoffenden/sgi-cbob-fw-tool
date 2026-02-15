# SGI Scalable Graphics Compositor Firmware Tool

Tool to patch SGI (Scalable Graphics Compositor)[//irix7.com/techpubs/007-4602-002.pdf] (CBOB) PROM images with data extracted from a `sting` error log.

CBOBs were found with newer PROM versions than distributed with IRIX, and no known OS patches exist to to upgrade existing models. This tool takes advantage of the `sting` hardware diagnostic tool's PROM verification, which when run with the `MAX_ERRORS` flag will generate list of differences between the hardware and the PROM files. Patch data can be generated from the logs, applied to the known good PROM images, then the patched PROM written out. Using the generated PROM files for a further `sting` verification should show no errors, meaning the operation was successful and the resulting files could be used to perform upgrades.

Besides being the tool written to perform this one-time conversion, this is also the handwritten example referred to in a (vibe-coding challenge)[//github.com/numfum/task-fw-patcher] set to see how LLMs handle such tasks. Results and findings of this at the end.

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

Ordinarily it would be enough to present the tool and the finished results but, and switching to the first person, I'll now explain my approach to solving this:

1. Familiarise myself the PROM images, these are the starting point. They're in (Intel Hex format)[//en.wikipedia.org/wiki/Intel_HEX], not a binary as one might expect, and have the oddity of Windows line endings on a Unix machine. The first step is to read then write exactly the same data, maintaining the `I16HEX` file variant and `CRLF` endings, resulting in a bit-correct copy.
2. Understand and parse the `sting` log. I decided early on the text processing would be best suited to Python, so I (interactively wrote)[//regex101.com/] a few regexes to extract the mismatch *errors*.

I looked at different Python libraries for dealing with Intel Hex files, and initially chose `(intexlhex)[//github.com/python-intelhex/intelhex]` because of its ability to write directly to a file. A simple 20-line test reader/writer was written, before noticing that writing only supports the newer `I32HEX` (verified in the source). An alternative, `(bincopy)[//github.com/eerimoq/bincopy]`, writes `I16HEX` with its own caveats, but an SGI compatible writer was quickly written and verified (see (write_compatible_mcs)[patch-mcs.py#L235].
