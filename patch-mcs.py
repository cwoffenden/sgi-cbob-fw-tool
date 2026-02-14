#!/usr/bin/env python3

'''Tool to patch SGI Scalable Compositor (CBOB) PROM images with data extracted
from a ``sting`` error log.

CBOBs were found with newer PROM versions than distributed with IRIX, and no
known OS patches exist to to upgrade existing models. This tool takes advantage
of the ``sting`` hardware conformance tool's PROM verification, which when run
with the ``MAX_ERRORS`` flag will generate a usable list of patches. This patch
data can be extracted from the logs, applied to the known good PROM images,
then the patched PROM written out. Using the generated PROM files for a further
``sting`` verification should show no errors, meaning the operation was
successful and the resulting files could be used to perform upgrades.
'''

import argparse
import functools
import re
import struct
import sys
from enum import Enum
from pathlib import Path
from typing import Optional, Pattern, TextIO

import bincopy  # type: ignore[import-untyped]
from bincopy import BinFile

# Regex to catch the start of a PROM readback test, the group will contain
# either CCX or CTX for the type.
#
start_regex: Pattern[str] = re.compile(r'^HRTB\s*Testing\s(CCX|CTX)\sconfiguration\.$')

# Regex for the comparisons. These are spread over multiple lines, e.g.:
#
# **** ERROR 024000       Miscompare of data at Flash address 0x48: exp 0x480100
# **** ERROR 024000       got 0x490002
# HRTB                    Continuing
#
# And with line breaks in different places:
#
# **** ERROR 024000       Miscompare of data at Flash address 0x188294: exp
# **** ERROR 024000       0x180060 got 0x580060
# HRTB                    Continuing
#
# The group contains the payload, which will need reconstituting, ending only
# when a 'continue_regex' is found.
#
data_regex: Pattern[str] = re.compile(r'^\*{4}\sERROR\s024000\s*(.*)$')

# Regex denoting further error data could be expected, see 'data_regex', but the
# current error line being constructed is complete.
#
# The group will contain an optional '.', which oddly appears or doesn't, or
# 'DONE' which marks this as the last (though the errors may be restarted). All
# ERROR lines terminate with a continue and it's not that important whether it's
# the last.
#
continue_regex: Pattern[str] = re.compile(r'^HRTB\s*Continuing(.?| DONE!)$')

# Regex to extract the comparison data from the concatenated ERROR payload; see
# the examples in 'data_regex'. The three groups are the address of the error,
# which could be offset by an address in the VERSION file (though can be
# deduced) then the expected and found data. Note that zero values are simply
# '0' not '0x0' (so the '0x' is optional and 'int()' determines the base).
#
compare_regex: Pattern[str] = re.compile(r'^Miscompare\s.*\saddress\s((?:0x)?[\da-fA-F]{1,8}):\sexp\s((?:0x)?[\da-fA-F]{1,8})\sgot\s((?:0x)?[\da-fA-F]{1,8})$')

# Regex for when further mismatches will be follow and the ERROR data will
# will restart. Only a 'checked_regex' marks the end.
#
again_regex: Pattern[str] = re.compile(r'^HRTB\s*Double\schecking\s(CCX|CTX)\ssuspicious\s.*$')

# Regex for when the test has completed, the two groups will contain the number
# of mismatches (note the incorrect spelling) and the total number of words.
#
stats_regex: Pattern[str] = re.compile(r'^INFO\s*-+\s(\d+)\smi(?:s+)matches\s\/\s(\d+)\swords\s.*$')

# Regex to mark the end of the CCX or CTX test. The first group contains the
# type, the second whether passed or failed.
#
done_regex: Pattern[str] = re.compile(r'^INFO\s*(CCX|CTX)\sconfiguration\sreadback\s(PASSED|FAILED)$')

# Regex for the 'poke' arguments, with the two groups containing addresses in
# hex or decimal, e.g.: "0x100=255".
#
poke_regex: Pattern[str] = re.compile(r'^((?:0x)?[\da-fA-F]{1,8})\s?=\s?((?:0x)?[\da-fA-F]{1,8})$')

def val_as_bytes(val: str) -> bytes:
	'''Takes a hex string and returns the four bytes in big endian order. See
	the examples in :const:`data_regex`, noting that all the values in the
	``sting`` logs are in BE order.
	'''
	try:
		return struct.pack('>I', int(val, 0))
	except ValueError:
		sys.exit(f'Parsing invalid number ({val})')

class Patch:
	'''Container to hold data extracted from ERROR 024000 payloads, further
	parsed to just the address offset and data values. The address offset will
	be later recalculated and used instead of the given address (because the
	addresses don't match the PROM's due to an optional start address and then
	padding), and so a fit is used after parsing and sorting.
	'''
	def __init__(self, line: str):
		compare_match = compare_regex.match(line)
		if compare_match:
			self.offset: int = int(compare_match.group(1), 0)
			self.exp: bytes = val_as_bytes(compare_match.group(2))
			self.got: bytes = val_as_bytes(compare_match.group(3))
		else:
			raise ValueError(f'Invalid miscompare string (got "{line}")')
	def __str__(self) -> str:
		return f'offset 0x{self.offset:X}, expect 0x{struct.unpack(">I", self.exp)[0]:X}, got 0x{struct.unpack(">I", self.got)[0]:X}'

def find_log_start(opened: TextIO, is_ccx: bool) -> bool:
	'''Helper to parse a raw ``sting`` log file and cue it at the correct line
	where any mismatch data starts. Returns True if the the file is cued.
	'''
	for line in opened:
		match_start = start_regex.match(line)
		if match_start:
			if (match_start.group(1) == 'CCX') == bool(is_ccx):
				return True
	return False

def create_patch_list(file: Path, is_ccx: bool) -> list[Patch]:
	'''Helper to create a list of 32-bit word patches from a ``sting`` log.
	The found entries are sorted by address offset (due to 'double checking'
	entries that come after the initial batch). Values are returned exactly as
	extracted, noting that further steps on processing offsets is required (to
	fit actual PROM addresses).
	'''
	with file.open('rt', errors='replace') as opened:
		found: list[Patch] = []
		if find_log_start(opened, is_ccx):
			data_builder: str = ''
			for line in opened:
				match_data = data_regex.match(line)
				if match_data:
					data_builder += f' {match_data.group(1).strip()}'
				else:
					if continue_regex.match(line):
						found.append(Patch(data_builder.lstrip()))
					else:
						if again_regex.match(line):
							pass
						else:
							match_stats = stats_regex.match(line)
							if match_stats:
								if len(found) != int(match_stats.group(1)):
									raise ValueError(f'Expected {match_stats.group(1)} but found {len(found)}')
								found.sort(key = lambda entry: entry.offset)
								return found
					# Always reset this on anything other than an ERRROR line
					# (which should then raise if extracting data fails from a
					# reconstituted payload).
					data_builder = ''
			sys.exit('Malformed log file')
		else:
			sys.exit('Log file must contain corresponding CCX/CTX data')
	sys.exit('Error reading log file')

def recalculate_patch_offsets(patches: list[Patch], offset_addr: int=-1) -> None:
	'''Turns the patch addresses as taken from the log into corresponding real
	addresses in the PROM file. Addresses in the logs are grouped in blocks of
	512 bytes, with each storing 264 bytes of data, which needs converting back
	to bytes indices. This is further obfuscated by an address offset (stored
	in the ``VERSION`` file alongside the MCS files) whose value can be deduced
	if not supplied (by subtracting the first patch's block offset, then
	advancing through all blocks for a possible match).
	'''
	if len(patches) > 0:
		# The magic numbers: 511 being 0x1FF, the mask for 512 byte blocks, 264
		# is described in the doc above, the shift being preferable to dividing
		# (by 512) and needing to convert back to an int.
		if offset_addr < 0:
			offset_addr = patches[0].offset & ~511
		for patch in patches:
			patch.offset = (((patch.offset & ~511) - offset_addr) >> 9) * 264 + (patch.offset & 511)
			if patch.offset < 0:
				sys.exit('Cannot create a negative patch address (wrong manual offset applied?)')

def verify_patch_offsets(patches: list[Patch], prom: BinFile, start_addr: int=0) -> bool:
	'''Adds the given start address to each patch offset and returns True if
	*all* the patch entries' expected values match the PROM's. Offsets should
	have already been converted from the 512/264 byte layout, explained in
	:func:`recalculate_patch_offsets()`.
	'''
	last_prom_word: int = prom.maximum_address - 4
	for patch in patches:
		prom_addr: int = start_addr + patch.offset
		if prom_addr > last_prom_word:
			sys.exit(f'Trying to match patch address outside of PROM (0x{prom_addr:X}/0x{last_prom_word:X})')
		for n in range(4):
			if prom[prom_addr + n] != patch.exp[n]:
				return False
	return True

def find_patch_start(patches: list[Patch], prom: BinFile) -> int:
	'''Brute force a start address by advancing through the PROM in 264 byte
	blocks and matching *all* the patches' expected values. If the patch list
	is small then it's possible multiple matches will be found, in which case a
	manual start address will be required. :func:`recalculate_patch_offsets()`
	explains the 264 byte blocks.
	'''
	found_matches: list[int] = []
	for block in range(int((prom.maximum_address - patches[-1].offset) / 264)):
		start_addr: int = block * 264
		if verify_patch_offsets(patches, prom, start_addr):
			found_matches.append(start_addr)
	num_matches: int = len(found_matches)
	if num_matches == 0:
		sys.exit('Unable to match address offsets')
	else:
		if num_matches > 1:
			sys.exit(f'Multiple address offsets possible, manual offset required ({num_matches} found)')
	return found_matches[0]

def find_and_add_patch_start(patches: list[Patch], prom: BinFile) -> None:
	'''Helper to find and add the start address to all patch offsets.
	'''
	start_addr: int = find_patch_start(patches, prom)
	for patch in patches:
		patch.offset += start_addr

def apply_patches(patches: list[Patch], prom: BinFile) -> None:
	'''Simple helper to apply all the patches. Before calling it is expected
	that the patches have been verified for valid addresses and content.
	'''
	for patch in patches:
		for n in range(4):
			prom[patch.offset + n] = patch.got[n]

def write_compatible_mcs(prom: BinFile, file: Path) -> None:
	'''Writes an MCS file compatible with SGI's.
	
	Note: `bincopy`` doesn't write the first (optional) zero address, so it's
	written manually for consistency (and diffing). The remaining ``BinFile``
	content can be written as-is, with the bytes per line (``16``) and the
	address length (``24``, meaning the ``I16HEX`` format) matching SGI's (also
	noting that ``I32HEX`` is incompatible).
	
	Note: one interesting caveat of the original files is that they have
	``CRLF`` Windows line endings on IRIX, so this is also reproduced here.
	'''
	with file.open(mode='w', newline='\r\n') as opened:
		print(bincopy.pack_ihex(2, 0, 2, b'\0\0'), file=opened)
		ihex_str = prom.as_ihex(number_of_data_bytes=16, address_length_bits=24)
		for line in ihex_str.splitlines():
			if len(line):
				print(line, file=opened)
		return
	sys.exit('Error writing MCS file')

# All the work is in here, reading, patching, writing
if __name__ == '__main__':
	parser = argparse.ArgumentParser(description='Compositor PROM Tool', epilog=f'example: {sys.argv[0]} -t ctx1.mcs -l sting.log -o output.mcs')
	parser.add_argument('-c', '--ccx', type=Path, help='CCX file in MCS format')
	parser.add_argument('-t', '--ctx', type=Path, help='CTX file in MCS format')
	parser.add_argument('-l', '--log', type=Path, help='log output from sting tool to apply')
	parser.add_argument('-o', '--out', type=Path, help='processed output file')
	parser.add_argument('-p', '--poke', type=str, help='PROM byte to set, e.g. 0x100=0x42 (for generating test files)', action='append')
	parser.add_argument('--offset', type=str, help='address offset as hex, e.g. 0x188000 (deduced otherwise)')
	args = parser.parse_args()

	# Input file, one of the two MCS files is required
	promSrc: Path
	if bool(args.ccx) ^ bool(args.ctx):
		if args.ccx:
			promSrc = args.ccx
		else:
			promSrc = args.ctx
	else:
		sys.exit('Input must be either a CCX or CTX file')
	try:
		promSrc = promSrc.resolve(strict=True)
		prom: BinFile = bincopy.BinFile(str(promSrc))
	except OSError:
		sys.exit('Unable to read PROM file')
	except bincopy.UnsupportedFileFormatError:
		sys.exit('Invalid MCS file')
	if prom.minimum_address > 0:
		sys.exit('PROM addresses expected to run from zero to end')
	print(f'Successfully read PROM file ({prom.maximum_address} bytes)')

	# Optional patches to apply to the MCS file
	if args.log:
		try:
			logSrc: Path = args.log.resolve(strict=True)
		except OSError:
			sys.exit('Unable to load log file')
		patches: list[Patch] = create_patch_list(logSrc, args.ccx)
		if len(patches) > 0:
			if args.offset:
				# Either with a manually applied offset
				try:
					offset_addr: int = int(args.offset, 0)
				except ValueError:
					sys.exit(f'Invalid offset address ({args.offset})')
				recalculate_patch_offsets(patches, offset_addr)
			else:
				# Or with the original address stripped out and one determined
				# by matching the expected data
				recalculate_patch_offsets(patches)
				find_and_add_patch_start(patches, prom)
			# Still verify the expected values before applying
			if verify_patch_offsets(patches, prom):
				apply_patches(patches, prom)
				print(f'Applied {len(patches)} word patches')
			else:
				sys.exit('PROM content does not match log file (invalid offset?)')
		else:
			print('No patches in log file')

	# Optional single byte 'pokes' to apply (after the patches)
	if args.poke:
		for poke in args.poke:
			poke_match = poke_regex.match(poke)
			if poke_match:
				try:
					addr: int = int(poke_match.group(1), 0)
					byte: int = int(poke_match.group(2), 0)
				except ValueError:
					sys.exit(f'Invalid poke address/value ({poke})')
				if addr < 0 or addr >= prom.maximum_address:
					sys.exit(f'Poke address out of range (0x{addr:X})')
				if byte < 0 or byte > 255:
					sys.exit(f'Poke value should be a byte (0x{byte:X})')
				prom[addr] = byte
			else:
				sys.exit('Poke format is address=byte_value, e.g. 0x100=0x42')
		print(f'Appled {len(args.poke)} poke values')

	# Optionally write the result (otherwise this verified the inputs)
	if args.out:
		try:
			promDst: Path = args.out.resolve()
		except OSError:
			sys.exit('Unable to open destination file')
		if promSrc == promDst:
			sys.exit('Source and destination files are the same')
		write_compatible_mcs(prom, promDst)
		print('Successfully wrote replacement PROM file')
